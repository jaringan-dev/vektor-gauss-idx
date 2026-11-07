"""Daily Telegram reminder: what was checked automatically after tonight's EOD, and what is still open in CHECKPOINT.md.

`vg remind-checkpoint` runs at the very end of `scripts/eod.sh` (always `|| true`). It sends at most ONE message per session date:

  * Automatic checks for the newest session, stated as facts (numbers, not "please check"): broker coverage, the iceberg-proxy size
    counts from the trade recorder, the frozen signal snapshot (B13), the v2 tags. Each line says OK / CHECK so a bad night stands out.
  * The open items of the newest day in CHECKPOINT.md: unticked steps ("- [ ]") and the "Menunggu keputusan user" bullets, so a
    decision that is waiting is not forgotten when the work session ended suddenly.

The marker is a `run_log` row (phase `checkpoint_reminder`, batch_date = the session) written only after a successful send, so a
failed send retries next time. `--force` sends again for the same session; `--dry-run` prints and touches neither Telegram nor the
database writes (the checks are still read).

`open_items` and `build_message` are pure; `facts` is SELECT only.
"""

from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path

from .db import connect
from .telegram import TelegramSender

PHASE = "checkpoint_reminder"
CHECKPOINT = Path(__file__).resolve().parents[2] / "CHECKPOINT.md"
MAX_ITEMS = 12                 # Telegram stays readable; the file has the rest
LOTS_SINCE = date(2026, 11, 9)  # first session the recorder counts big fills (trade_lots_daily, b2ffac1)
MIN_BROKER_TICKERS = 250      # the sticky universe is ~370 names a day (d5abaa1); far below that = the broker pass failed


def open_items(text: str) -> tuple[str | None, list[str], list[str]]:
    """(day heading, unticked steps, waiting decisions) of the NEWEST day section ("## <day>") of CHECKPOINT.md."""
    parts = re.split(r"^## ", text, flags=re.MULTILINE)
    if len(parts) < 2:
        return None, [], []
    day = parts[1].splitlines()[0].strip()
    section = parts[1]
    steps = [m.strip() for m in re.findall(r"^- \[ \] (.+)$", section, flags=re.MULTILINE)]
    waiting: list[str] = []
    m = re.search(r"^### Menunggu keputusan user\s*\n(.*?)(?=^### |\Z)", section, flags=re.MULTILINE | re.DOTALL)
    if m:
        waiting = [b.strip() for b in re.findall(r"^- (.+)$", m.group(1), flags=re.MULTILINE)]
    return day, steps, waiting


def _md_to_html(s: str) -> str:
    """CHECKPOINT.md uses `code` and **bold**; Telegram HTML wants <code> and <b>. Everything else is escaped."""
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)


def facts(cur, session: date) -> list[tuple[bool, str]]:
    """(ok, line) for the automatic checks of `session`. SELECT only."""
    out: list[tuple[bool, str]] = []
    cur.execute("SELECT count(DISTINCT ticker) FROM broker_flows WHERE session_date = %s", (session,))
    n = cur.fetchone()[0] or 0
    out.append((n >= MIN_BROKER_TICKERS, f"Broker data: {n} stocks (sticky universe expects ~370)"))
    from .engine import V2_TAGS, V2_TAGS_SINCE

    cur.execute("SELECT to_regclass('trade_lots_daily') IS NOT NULL")
    if session < LOTS_SINCE:
        out.append((True, f"Iceberg-proxy size counts: start {LOTS_SINCE.isoformat()} (the recorder's first day)"))
    elif cur.fetchone()[0]:
        cur.execute("SELECT count(*), count(DISTINCT ticker), coalesce(max(n), 0) FROM trade_lots_daily WHERE session_date = %s", (session,))
        rows, tickers, top = cur.fetchone()
        out.append((rows > 0, f"Iceberg-proxy size counts: {rows:,} rows, {tickers} stocks, most repeats of one size at one price {top}"))
    else:
        out.append((False, "Table trade_lots_daily does not exist (run vg init-db)"))
    cur.execute("SELECT count(*) FROM signal_snapshot WHERE session_date = %s", (session,))
    snap = cur.fetchone()[0] or 0
    out.append((snap > 0, f"Frozen signals (signal_snapshot): {snap:,}"))
    if session < V2_TAGS_SINCE:
        out.append((True, f"v2 tags: their forward clock starts {V2_TAGS_SINCE.isoformat()}"))
    else:
        cur.execute("SELECT count(*) FROM signal_log WHERE session_date = %s AND tag = ANY(%s)", (session, sorted(V2_TAGS)))
        v2 = cur.fetchone()[0] or 0
        out.append((v2 > 0, f"v2 tags fired: {v2:,}"))
    return out


def build_message(session: date, checks: list[tuple[bool, str]], day: str | None, steps: list[str], waiting: list[str]) -> str:
    lines = [f"📌 <b>CHECKPOINT VEKTOR GAUSS — sesi {session.isoformat()}</b>", "", "<b>Cek otomatis setelah EOD</b>"]
    lines += [f"{'✅' if ok else '⚠️'} {html.escape(t, quote=False)}" for ok, t in checks] or ["(tidak ada)"]
    items = [("<b>Langkah belum selesai</b>", steps), ("<b>Menunggu keputusan Anda</b>", waiting)]
    shown = 0
    for title, xs in items:
        if not xs:
            continue
        lines += ["", title]
        for x in xs:
            if shown >= MAX_ITEMS:
                break
            lines.append(f"• {_md_to_html(x)}")
            shown += 1
    rest = len(steps) + len(waiting) - shown
    if rest > 0:
        lines.append(f"… dan {rest} lagi")
    if day:
        lines += ["", f"Detail: <code>CHECKPOINT.md</code> (bagian {html.escape(day, quote=False)})"]
    return "\n".join(lines)


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"</?(?:b|code)>", "", text))


def run(*, force: bool = False, dry_run: bool = False, sender: TelegramSender | None = None, path: Path = CHECKPOINT) -> int:
    """0 = sent, or deliberately not sent (already sent for this session / dry run); 2 = could not send."""
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT max(session_date) FROM trading_calendar")
            session = cur.fetchone()[0]
            if session is None:
                print("no trading_calendar rows: nothing to report")
                return 0
            if not force and not dry_run:
                cur.execute("SELECT 1 FROM run_log WHERE phase = %s AND batch_date = %s AND status = 'ok' LIMIT 1", (PHASE, session))
                if cur.fetchone():
                    print(f"checkpoint reminder already sent for {session} (use --force to send again)")
                    return 0
            checks = facts(cur, session)
    except Exception as exc:  # noqa: BLE001 - say why; never break the EOD that calls this
        print(f"checkpoint reminder skipped, database check failed: {type(exc).__name__}: {exc}")
        return 2
    try:
        day, steps, waiting = open_items(path.read_text(encoding="utf-8"))
    except OSError:
        day, steps, waiting = None, [], []
    text = build_message(session, checks, day, steps, waiting)
    if dry_run:
        print(_plain(text))
        return 0
    sender = sender or TelegramSender()
    if not sender.configured:
        print("Telegram is not configured (VG_TELEGRAM_BOT_TOKEN / VG_TELEGRAM_CHAT_ID): checkpoint reminder not sent")
        return 2
    try:
        sender.send(text, silent=all(ok for ok, _ in checks))      # a sound only when an automatic check failed
    except Exception as exc:  # noqa: BLE001 - no marker: the next run retries
        print(f"TELEGRAM SEND FAILED: {type(exc).__name__}: {exc}")
        return 2
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO run_log (phase, batch_date, status, rows_affected, finished_at) VALUES (%s, %s, 'ok', 1, now())",
                        (PHASE, session))
    except Exception as exc:  # noqa: BLE001
        print(f"checkpoint reminder sent, but the marker could not be stored ({type(exc).__name__}: {exc}); it may repeat once")
        return 2
    print(f"checkpoint reminder sent for {session}")
    return 0
