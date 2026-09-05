"""Paper-trading simulation for the long-side probation tags.

HANDOFF.md's "Paper-trade the surviving candidates (FLOAT-ASING-PADAT,
TIKET-BESAR-AKUM), keep a manual spreadsheet" — automated instead of manual.

Mechanical only, same rules as `calc.plan()`/`digest.py`'s trade line: entry =
close on the day the tag fires, stop-loss = close - ATR_SL_MULT*ATR14, target
= entry + TARGET_R * (entry - stop_loss). Walks forward trading days (via
`price_bars`) until the stop or target is hit, or MAX_HOLD_DAYS elapses with
neither (timeout). If both would trigger the same day, the stop is assumed to
hit first — conservative, avoids a look-ahead bias that would flatter the win
rate.

This is a different lens than `vg eval`'s fixed-horizon (H1/H5/H10) average
return: it answers "would the actual SL/target trade plan have worked",
trade-by-trade, not "what's the average return N days later". Complements,
doesn't replace, `vg eval` — same probation/weight-0 discipline
(PRINSIP-DATA / RENCANA-FASE-1): informational until the forward-test gate.

`TIKET-KECIL-MARKUP` is deliberately excluded — it's an avoid/warning filter,
not a buy candidate, so there's no entry/SL/target to simulate. Its
performance is already covered by `vg eval`'s ordinary edge stats.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import psycopg

from .config import CONFIG
from .db import connect

LONG_TAGS = ("FLOAT-ASING-PADAT", "TIKET-BESAR-AKUM")
MAX_HOLD_DAYS = 20   # trading days — matches vg eval's longest horizon (H10), doubled for runway


@dataclass(slots=True)
class PaperSummary:
    opened: int = 0
    resolved_win: int = 0
    resolved_loss: int = 0
    resolved_timeout: int = 0
    still_open: int = 0


def _open_new_trades(cur: psycopg.Cursor) -> int:
    """One paper_trades row per (ticker, tag, signal_date) in signal_log for
    the candidate tags that doesn't have one yet. Entry/SL/target formula
    matches calc.atr_stop()/calc.plan() — kept in sync manually, both are the
    same three-line formula."""
    cur.execute(
        """
        INSERT INTO paper_trades (ticker, tag, signal_date, entry, atr14, stop_loss, target)
        SELECT s.ticker, s.tag, s.session_date, sc.close, sc.atr14,
               sc.close - %(mult)s * sc.atr14,
               sc.close + %(target_r)s * %(mult)s * sc.atr14
        FROM signal_log s
        JOIN signal_components sc
          ON sc.session_date = s.session_date AND sc.ticker = s.ticker
        WHERE s.tag = ANY(%(tags)s) AND sc.atr14 IS NOT NULL AND sc.close IS NOT NULL
        ON CONFLICT (ticker, tag, signal_date) DO NOTHING
        """,
        {"tags": list(LONG_TAGS), "mult": CONFIG.atr_sl_mult, "target_r": CONFIG.target_r},
    )
    return cur.rowcount


@dataclass(slots=True)
class Resolution:
    status: str                    # 'win' | 'loss' | 'timeout' | 'open'
    resolved_date: date | None = None
    exit_price: float | None = None
    r_multiple: float | None = None
    days_held: int | None = None


def resolve_one(
    entry: float, stop_loss: float, target: float, window: list[tuple[date, float | None, float | None, float | None]],
) -> Resolution:
    """Pure simulation core — no DB. `window` is the ticker's bars strictly
    after signal_date, in order, each (date, high, low, close), already
    truncated to at most MAX_HOLD_DAYS. Stop is checked before target on a
    day where both would trigger (conservative — avoids a look-ahead bias
    that would flatter the win rate)."""
    for k, (d, hi, lo, _cl) in enumerate(window, start=1):
        if lo is not None and float(lo) <= float(stop_loss):
            return _resolved("loss", d, float(stop_loss), entry, stop_loss, k)
        if hi is not None and float(hi) >= float(target):
            return _resolved("win", d, float(target), entry, stop_loss, k)

    if len(window) >= MAX_HOLD_DAYS:
        last_d, _hi, _lo, last_close = window[-1]
        exit_price = float(last_close) if last_close is not None else None
        return _resolved("timeout", last_d, exit_price, entry, stop_loss, len(window))

    return Resolution("open")


def _resolved(
    status: str, d: date, exit_price: float | None, entry: float, stop_loss: float, days_held: int,
) -> Resolution:
    r_mult = None
    if exit_price is not None:
        risk = float(entry) - float(stop_loss)
        r_mult = (exit_price - float(entry)) / risk if risk else None
    return Resolution(status, d, exit_price, r_mult, days_held)


def _resolve_open_trades(cur: psycopg.Cursor) -> tuple[int, int, int, int]:
    """Walk each open trade forward against its ticker's price_bars via
    resolve_one(). Returns (win, loss, timeout, still_open) counts. Re-scans
    the full window from signal_date each call rather than tracking a resume
    point — MAX_HOLD_DAYS is small (20 bars), cheap enough that the extra
    bookkeeping isn't worth it."""
    cur.execute(
        "SELECT id, ticker, signal_date, entry, stop_loss, target "
        "FROM paper_trades WHERE status = 'open' ORDER BY ticker, signal_date"
    )
    open_trades = cur.fetchall()
    if not open_trades:
        return (0, 0, 0, 0)

    tickers = sorted({t[1] for t in open_trades})
    cur.execute(
        "SELECT ticker, session_date, high, low, close FROM price_bars "
        "WHERE ticker = ANY(%s) ORDER BY ticker, session_date",
        (tickers,),
    )
    bars: dict[str, list[tuple]] = {}
    for tkr, d, hi, lo, cl in cur.fetchall():
        bars.setdefault(tkr, []).append((d, hi, lo, cl))

    win = loss = timeout = still_open = 0
    updates: list[tuple] = []
    for id_, ticker, sig_date, entry, sl, tgt in open_trades:
        series = bars.get(ticker, [])
        try:
            start_idx = next(i for i, row in enumerate(series) if row[0] == sig_date)
        except StopIteration:
            still_open += 1   # signal day itself missing from price_bars — shouldn't happen
            continue
        window = series[start_idx + 1: start_idx + 1 + MAX_HOLD_DAYS]
        res = resolve_one(float(entry), float(sl), float(tgt), window)

        if res.status == "open":
            still_open += 1
            continue
        updates.append((res.status, res.resolved_date, res.exit_price, res.r_multiple, res.days_held, id_))
        if res.status == "win":
            win += 1
        elif res.status == "loss":
            loss += 1
        else:
            timeout += 1

    if updates:
        cur.executemany(
            "UPDATE paper_trades SET status=%s, resolved_date=%s, exit_price=%s, "
            "r_multiple=%s, days_held=%s, updated_at=now() WHERE id=%s",
            updates,
        )
    return (win, loss, timeout, still_open)


def update_paper_trades() -> PaperSummary:
    """Open any newly-tagged signals, then advance every open trade. Safe to
    run daily (idempotent open; resolved trades are never re-touched)."""
    with connect() as conn, conn.cursor() as cur:
        opened = _open_new_trades(cur)
        win, loss, timeout, still_open = _resolve_open_trades(cur)
    return PaperSummary(opened, win, loss, timeout, still_open)


def report(since: date | None = None) -> list[dict]:
    """Per-tag stats: n by status, win-rate among resolved (win+loss, i.e.
    excluding timeout), and avg realized R-multiple. `since` filters by
    signal_date (e.g. VG_FORWARD_SINCE, to isolate the forward window)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT tag,
                   count(*) FILTER (WHERE status = 'open')    AS n_open,
                   count(*) FILTER (WHERE status = 'win')     AS n_win,
                   count(*) FILTER (WHERE status = 'loss')    AS n_loss,
                   count(*) FILTER (WHERE status = 'timeout') AS n_timeout,
                   avg(r_multiple) FILTER (WHERE status IN ('win', 'loss'))  AS avg_r_resolved,
                   avg(r_multiple) FILTER (WHERE status = 'timeout')        AS avg_r_timeout
            FROM paper_trades
            WHERE signal_date >= %s
            GROUP BY tag ORDER BY tag
            """,
            (since or date.min,),
        )
        cols = ("tag", "n_open", "n_win", "n_loss", "n_timeout", "avg_r_resolved", "avg_r_timeout")
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        resolved = r["n_win"] + r["n_loss"]
        r["win_rate"] = r["n_win"] / resolved if resolved else None
    return rows
