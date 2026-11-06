"""Complete broker tables (`vg collect-broker-full`): for each stock and day, EVERY broker that traded it, into `broker_flows_full`.

Why a table of its own. `broker_flows` keeps the 20 biggest NET BUYERS of each stock-day (that is what the feed returns by default): no net
seller, and a broker's days outside the top 20 missing. The BROKER-* signals under forward test are computed from it, so it must not change
under them. The feed's `all_data=true` returns every broker for the same 1 call (BBCA, 6 Nov 2026: 71 brokers, bought 817B = sold 817B), and this
module stores that in a separate table that only the broker stalker and inventory pages read. Nothing computed from `broker_flows` reads it.

A table is stored only when it is COMPLETE: bought and sold value agree within 1% (every trade has two sides). Anything else is reported as
`no_data` and left out, so a partial table can never pass for a full one. Raw responses are not archived (they are ~4x the size of the
default ones; the parsed rows are what the pages need).

Quota: one call per stock-day, the same universe as `collect-broker-summary --full` (watchlist, holdings, every liquid name and the radar
extras: about 165 calls for a day, measured 6 Nov 2026).
"""

from __future__ import annotations

from datetime import date

from .collect import (
    CollectOutcome,
    _feed_loop,
    _log,
    _log_error,
    _with_feed_notes,
    plan_broker_full,
)
from .config import CONFIG
from .db import connect, upsert_many
from .sources.vektorgauss import VgFullBrokerSource

PHASE = "collect_broker_full"
TABLE = "broker_flows_full"
TOLERANCE = 0.01                      # bought vs sold value, relative
_COLS = ("session_date", "ticker", "broker_code", "side_flag", "buy_val", "sell_val", "buy_vol", "sell_vol")


def complete(rows) -> tuple[bool, float, float]:
    """(is the table complete, total bought, total sold). Complete = something was traded and bought and sold agree within TOLERANCE."""
    buy = float(sum((r.buy_val or 0) for r in rows))
    sell = float(sum((r.sell_val or 0) for r in rows))
    return (buy > 0 and abs(buy - sell) <= TOLERANCE * buy), buy, sell


def collect_one(session_date: date, ticker: str, *, use_date: bool) -> CollectOutcome:
    """One stock, one day: fetch, check, store. Never raises."""
    try:
        res = VgFullBrokerSource().fetch(session_date, ticker, use_date=use_date)
    except Exception as exc:  # noqa: BLE001
        _log_error(session_date, exc, PHASE)
        return CollectOutcome(session_date, "error", 0, f"{ticker}: {exc}")
    if res.status != "ok":
        return CollectOutcome(session_date, res.status, 0, f"{ticker}: {res.detail}" if res.detail else ticker)
    ok, buy, sell = complete(res.rows)
    if not ok:
        return CollectOutcome(session_date, "no_data", 0, f"{ticker}: incomplete table (bought {buy:,.0f} vs sold {sell:,.0f}) - not stored")
    rows = [(session_date, ticker, r.broker_code, r.side_flag, r.buy_val, r.sell_val, r.buy_vol, r.sell_vol) for r in res.rows]
    try:
        with connect() as conn, conn.cursor() as cur:
            n = upsert_many(cur, TABLE, _COLS, rows, conflict=("ticker", "session_date", "broker_code"),
                            update=("side_flag", "buy_val", "sell_val", "buy_vol", "sell_vol"))
    except Exception as exc:  # noqa: BLE001
        _log_error(session_date, exc, PHASE)
        return CollectOutcome(session_date, "error", 0, f"{ticker}: {exc}")
    return CollectOutcome(session_date, "ok", n, ticker)


def _collect_day(session_date: date | None, delay: float | None, budget: int | None) -> tuple[list[CollectOutcome], int]:
    plan = plan_broker_full(session_date=session_date, have_table=TABLE)
    latest = plan["latest"]
    if latest is None:
        return [CollectOutcome(session_date or date.today(), "error", 0, plan.get("reason") or "no trading_calendar rows - run collect-price first")], 0  # noqa: DTZ011
    todo = plan["todo"] if budget is None else plan["todo"][: max(0, int(budget))]
    dated = latest != plan["newest"]
    pause = CONFIG.feed_delay_seconds if delay is None else delay
    run = _feed_loop(todo, lambda t: collect_one(latest, t, use_date=dated), pause)
    calls = run.ok + run.no_data + run.errors
    left = len(plan["todo"]) - len(todo)
    detail = (f"{len(plan['universe'])} names: stored {run.ok}, not stored {run.no_data} (no data or incomplete), errors {run.errors}, "
              f"already had {len(plan['have'])}" + (f", {left} left for the budget" if left else ""))
    detail = _with_feed_notes(run, detail)
    status = "error" if run.aborted else "ok"
    try:
        with connect() as conn, conn.cursor() as cur:
            _log(cur, latest, status, run.rows, detail, PHASE)
    except Exception:  # noqa: BLE001, S110 - a failed log line must not hide the run
        pass
    return [*run.failures, CollectOutcome(latest, status, run.rows, detail)], calls


def collect_day(*, session_date: date | None = None, delay: float | None = None, budget: int | None = None) -> list[CollectOutcome]:
    """The full pass for one day: the newest trading day by default, or `session_date` (history, dated request). Idempotent: names already
    stored are skipped (0 calls). `budget` caps the calls of this day. Returns the failures plus one summary line, which is also logged."""
    return _collect_day(session_date, delay, budget)[0]


def recent_sessions(n: int) -> list[date]:
    """The newest `n` trading days, newest first."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT session_date FROM trading_calendar ORDER BY session_date DESC LIMIT %s", (int(n),))
        return [r[0] for r in cur.fetchall()]


def collect_recent(sessions: int = 1, *, budget: int | None = None, delay: float | None = None) -> list[CollectOutcome]:
    """The newest `sessions` trading days, NEWEST FIRST, inside an overall call budget (None = no cap). Stops at the first day that fails
    outright, or when the budget is used up; a rerun carries on, because stored names are skipped."""
    left = budget
    out: list[CollectOutcome] = []
    for d in recent_sessions(sessions):
        if left is not None and left <= 0:
            break
        res, calls = _collect_day(d, delay, left)
        out.extend(res)
        if left is not None:
            left -= calls
        if res[-1].status == "error":
            break
    return out
