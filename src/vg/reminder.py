"""One-off Telegram reminder: what to verify after the first EOD that runs the broker-scoring release.

`vg remind-verify` is called at the very end of `scripts/eod.sh` (after `vg digest` and the status report, always
with `|| true`, so it can never break either). It sends the checklist ONCE:

  * only when the EOD it follows was healthy enough for the checklist to make sense: a `compute` finished OK in the
    last few hours AND the scoring columns exist in `signal_components` (so "Init-DB has finished" is true);
  * and only if it has not been sent before (a `run_log` row, phase `verify_reminder`, status `ok`, is the marker -
    no new table, no new column). A failed send writes no marker, so the next EOD tries again.

`--force` sends right now regardless of readiness and without writing the marker (the automatic one still fires
later); `--dry-run` prints the message and touches neither Telegram nor the database.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from datetime import date

from .config import CONFIG
from .db import connect
from .engine.scoring import SCORING_COLUMNS  # noqa: F401 - re-exported: the list the message and readiness check use
from .telegram import TelegramSender

PHASE = "verify_reminder"

# The signal_components columns added by the broker-scoring release (db/schema.sql). Tests pin this list to both
# the schema and the engine's column list, so the "N kolom baru" in the message cannot drift from the truth.

_RECENT_COMPUTE_HOURS = 12     # an EOD (feed wait + harvest + two computes) finishes well inside this


def _minutes(m: float) -> str:
    return f"{int(m)}" if float(m).is_integer() else f"{m:g}"


def build_message(targets: Sequence[str], budget_minutes: float) -> str:
    """The checklist as Telegram-HTML. The watchlist and the budget come from the live configuration."""
    names = ", ".join(html.escape(t) for t in targets) if targets else "belum diatur di VG_WATCHLIST"
    if budget_minutes > 0:
        duration = f"pastikan muat dalam anggaran {_minutes(budget_minutes)} menit."
    else:
        duration = "anggaran tidak dibatasi (<code>VG_DONE_BUDGET_MINUTES=0</code>), jadi periksa lamanya sendiri."
    return (
        "🔔 <b>PENGINGAT VERIFIKASI EOD VEKTOR GAUSS</b>\n"
        "\n"
        "Proses EOD &amp; Init-DB telah selesai. Silakan periksa:\n"
        "1. <code>eod.log</code> &amp; <code>run_log</code> — pastikan status OK "
        "(bebas error <code>schema:</code>).\n"
        f"2. Tabel <code>signal_components</code> — cek keterisian {len(SCORING_COLUMNS)} kolom baru "
        "&amp; tag <code>AKUM-NETRAL-WASH</code>.\n"
        f"3. Tabel <code>done_by_price</code> — verifikasi data order flow {len(targets)} saham watchlist "
        f"({names}).\n"
        f"4. Durasi eksekusi order flow — {duration}"
    )


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"</?(?:b|code)>", "", text))


def already_sent(cur) -> bool:
    cur.execute("SELECT 1 FROM run_log WHERE phase = %s AND status = 'ok' LIMIT 1", (PHASE,))
    return cur.fetchone() is not None


def readiness(cur) -> tuple[bool, str]:
    """(ready, why not). Ready = a compute finished OK recently and every scoring column exists."""
    cur.execute(
        "SELECT 1 FROM run_log WHERE phase = 'compute' AND status = 'ok' "
        f"AND finished_at > now() - interval '{_RECENT_COMPUTE_HOURS} hours' LIMIT 1")
    if cur.fetchone() is None:
        return False, f"no successful compute in the last {_RECENT_COMPUTE_HOURS} h (EOD blocked or not a trading day)"
    cur.execute(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'signal_components' AND column_name = ANY(%s)", (list(SCORING_COLUMNS),))
    have = cur.fetchone()[0]
    if have != len(SCORING_COLUMNS):
        return False, f"signal_components has {have}/{len(SCORING_COLUMNS)} scoring columns (init-db has not run)"
    return True, ""


def mark_sent(cur, day: date) -> None:
    cur.execute(
        "INSERT INTO run_log (phase, batch_date, status, rows_affected, finished_at) "
        "VALUES (%s, %s, 'ok', 1, now())", (PHASE, day))


def run(*, force: bool = False, dry_run: bool = False, sender: TelegramSender | None = None) -> int:
    """Exit code: 0 = sent or deliberately not sent (already sent / not ready / dry run); 2 = could not send."""
    from .collect import done_targets

    text = build_message(done_targets(), CONFIG.done_budget_minutes)
    if dry_run:
        print(_plain(text))
        return 0

    sender = sender or TelegramSender()
    if not sender.configured:
        print("Telegram is not configured (VG_TELEGRAM_BOT_TOKEN / VG_TELEGRAM_CHAT_ID): reminder not sent")
        return 2

    if not force:
        try:
            with connect() as conn, conn.cursor() as cur:
                if already_sent(cur):
                    print("verification reminder already sent once (use --force to send it again)")
                    return 0
                ready, why = readiness(cur)
        except Exception as exc:  # noqa: BLE001 - cannot tell whether it was sent: better silent than a duplicate
            print(f"reminder skipped, database check failed: {type(exc).__name__}: {exc}")
            return 2
        if not ready:
            print(f"verification reminder not sent yet: {why}")
            return 0

    try:
        sender.send(text)
    except Exception as exc:  # noqa: BLE001 - surface it; the marker is NOT written so the next EOD retries
        print(f"TELEGRAM SEND FAILED: {type(exc).__name__}: {exc}")
        return 2

    if not force:
        try:
            with connect() as conn, conn.cursor() as cur:
                mark_sent(cur, date.today())  # noqa: DTZ011 - local date, same convention as run_log elsewhere
        except Exception as exc:  # noqa: BLE001 - sent, but could not be recorded: it may repeat once, say so
            print(f"reminder sent, but the marker could not be stored ({type(exc).__name__}: {exc}); "
                  "it may be sent again")
            return 2
    print("verification reminder sent")
    return 0
