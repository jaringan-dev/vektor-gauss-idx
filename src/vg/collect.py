"""Collection orchestrator. Fetch -> store raw artifact -> upsert DB -> log.

Designed so one bad day never aborts a backfill: fetch/persist errors are
caught and written to run_log with status 'error', then the loop continues.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from . import artifacts
from .config import CONFIG
from .db import connect, upsert_many
from .sources.base import (
    KSEI_CATEGORIES,
    OwnershipFetchResult,
    OwnershipSnapshot,
    PriceBar,
    PriceFetchResult,
)
from .sources.idx import IdxStockSummarySource
from .sources.ksei import KseiOwnershipSource

_PRICE_COLS = (
    "session_date", "ticker", "prev", "open", "first_trade", "high", "low", "close",
    "change", "volume", "value", "frequency", "foreign_buy_vol", "foreign_sell_vol",
    "nonreg_volume", "nonreg_value", "nonreg_frequency", "listed_shares",
    "tradable_shares", "weight_for_index", "index_individual", "bid", "bid_volume",
    "offer", "offer_volume", "remarks", "delisting_date", "source", "source_tier",
)
_PRICE_UPDATE = tuple(c for c in _PRICE_COLS if c not in ("session_date", "ticker"))


@dataclass
class CollectOutcome:
    session_date: date
    status: str
    rows: int
    detail: str = ""


def _price_row(sd: date, bar: PriceBar, source: str, tier: int) -> tuple:
    return (
        sd, bar.ticker, bar.prev, bar.open, bar.first_trade, bar.high, bar.low, bar.close,
        bar.change, bar.volume, bar.value, bar.frequency, bar.foreign_buy_vol,
        bar.foreign_sell_vol, bar.nonreg_volume, bar.nonreg_value, bar.nonreg_frequency,
        bar.listed_shares, bar.tradable_shares, bar.weight_for_index, bar.index_individual,
        bar.bid, bar.bid_volume, bar.offer, bar.offer_volume, bar.remarks,
        bar.delisting_date, source, tier,
    )


def _persist(cur: psycopg.Cursor, res: PriceFetchResult) -> int:
    sd = res.session_date

    upsert_many(
        cur, "tickers", ("ticker", "name", "first_seen", "last_seen"),
        [(b.ticker, b.name, sd, sd) for b in res.bars],
        conflict=("ticker",),
    )
    cur.executemany(
        """
        UPDATE tickers SET
            name       = COALESCE(%s, name),
            first_seen = LEAST(first_seen, %s),
            last_seen  = GREATEST(last_seen, %s),
            updated_at = now()
        WHERE ticker = %s
        """,
        [(b.name, sd, sd, b.ticker) for b in res.bars],
    )

    n = upsert_many(
        cur, "price_bars", _PRICE_COLS,
        [_price_row(sd, b, res.source, res.source_tier) for b in res.bars],
        conflict=("session_date", "ticker"), update=_PRICE_UPDATE,
    )
    cur.execute(
        "UPDATE price_bars SET fetched_at = now() WHERE session_date = %s AND source = %s",
        (sd, res.source),
    )

    cur.execute(
        "INSERT INTO trading_calendar (session_date, confirmed_by) VALUES (%s, %s) "
        "ON CONFLICT (session_date) DO NOTHING",
        (sd, res.source),
    )
    return n


def _log(cur: psycopg.Cursor, sd: date, status: str, rows: int, err: str | None) -> None:
    cur.execute(
        "INSERT INTO run_log (phase, batch_date, status, rows_affected, error, finished_at) "
        "VALUES ('collect_price', %s, %s, %s, %s, now())",
        (sd, status, rows, err),
    )


def _log_error(sd: date, exc: BaseException) -> None:
    try:
        with connect() as conn, conn.cursor() as cur:
            _log(cur, sd, "error", 0, f"{type(exc).__name__}: {exc}")
    except Exception:
        pass  # last resort — don't mask the original error


def _collect(res: PriceFetchResult) -> CollectOutcome:
    # Save raw to disk first (no DB dependency).
    art = artifacts.store(res.source, res.session_date, res.raw_bytes) if res.raw_bytes else None
    try:
        with connect() as conn, conn.cursor() as cur:
            if art:
                artifacts.record(cur, res.source, res.session_date, *art)
            rows = _persist(cur, res) if res.status == "ok" else 0
            _log(cur, res.session_date, res.status, rows, res.detail or None)
        return CollectOutcome(res.session_date, res.status, rows, res.detail)
    except Exception as exc:  # persist failed and rolled back
        _log_error(res.session_date, exc)
        return CollectOutcome(res.session_date, "error", 0, f"{type(exc).__name__}: {exc}")


def collect_price_day(session_date: date) -> CollectOutcome:
    try:
        res = IdxStockSummarySource().fetch(session_date)
    except Exception as exc:
        _log_error(session_date, exc)
        return CollectOutcome(session_date, "error", 0, f"{type(exc).__name__}: {exc}")
    return _collect(res)


def collect_price_latest() -> CollectOutcome:
    try:
        res = IdxStockSummarySource().fetch(None)
    except Exception as exc:
        _log_error(date.today(), exc)
        return CollectOutcome(date.today(), "error", 0, f"{type(exc).__name__}: {exc}")
    return _collect(res)


def collect_price_range(start: date, end: date) -> list[CollectOutcome]:
    """Inclusive. Skips weekends locally; IDX gates public holidays server-side
    (those return no_data / date_mismatch and are logged, not raised)."""
    out: list[CollectOutcome] = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            out.append(collect_price_day(d))
            time.sleep(CONFIG.request_delay_seconds)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# KSEI monthly ownership (Tier 0)
# ---------------------------------------------------------------------------

_OWN_COLS = (
    "month_end", "ticker", "local_individual", "local_institution", "foreign_total",
    "detail", "holders_count", "source", "source_tier",
)
_OWN_UPDATE = tuple(c for c in _OWN_COLS if c not in ("month_end", "ticker"))


def _as_int(v: Decimal | None) -> int | None:
    return int(v) if v is not None else None


def _own_detail(s: OwnershipSnapshot) -> dict:
    local = {c: _as_int(s.local.get(c)) for c in KSEI_CATEGORIES}
    foreign = {c: _as_int(s.foreign.get(c)) for c in KSEI_CATEGORIES}
    lt, ft = s.local.get("TOTAL"), s.foreign.get("TOTAL")
    local["TOTAL"], foreign["TOTAL"] = _as_int(lt), _as_int(ft)
    scripless_pct = None
    if lt is not None and ft is not None and s.sec_num:
        scripless_pct = round(float((lt + ft) / s.sec_num), 6)
    return {
        "price": _as_int(s.price),
        "sec_num": _as_int(s.sec_num),
        "local": local,
        "foreign": foreign,
        "scripless_pct": scripless_pct,
    }


def _own_row(month_end: date, s: OwnershipSnapshot, source: str, tier: int) -> tuple:
    lt = s.local.get("TOTAL")
    lid = s.local.get("ID")
    local_institution = (lt - lid) if (lt is not None and lid is not None) else None
    return (
        month_end, s.ticker, lid, local_institution, s.foreign.get("TOTAL"),
        Jsonb(_own_detail(s)), None, source, tier,
    )


def _persist_ownership(cur: psycopg.Cursor, res: OwnershipFetchResult) -> int:
    me = res.month_end
    upsert_many(
        cur, "tickers", ("ticker", "name", "first_seen", "last_seen"),
        [(r.ticker, None, me, me) for r in res.rows],
        conflict=("ticker",),
    )
    n = upsert_many(
        cur, "ownership_monthly", _OWN_COLS,
        [_own_row(me, r, res.source, res.source_tier) for r in res.rows],
        conflict=("month_end", "ticker"), update=_OWN_UPDATE,
    )
    cur.execute(
        "UPDATE ownership_monthly SET fetched_at = now() WHERE month_end = %s AND source = %s",
        (me, res.source),
    )
    return n


def _collect_ownership(res: OwnershipFetchResult) -> CollectOutcome:
    label = res.month_end or date.today()
    art = (
        artifacts.store(res.source, res.month_end, res.raw_bytes, ext=res.raw_ext)
        if res.raw_bytes else None
    )
    try:
        with connect() as conn, conn.cursor() as cur:
            if art:
                artifacts.record(cur, res.source, res.month_end, *art)
            rows = _persist_ownership(cur, res) if res.status == "ok" else 0
            cur.execute(
                "INSERT INTO run_log (phase, batch_date, status, rows_affected, error, finished_at) "
                "VALUES ('collect_ownership', %s, %s, %s, %s, now())",
                (label, res.status, rows, res.detail or None),
            )
        return CollectOutcome(label, res.status, rows, res.detail)
    except Exception as exc:
        _log_error(label, exc)
        return CollectOutcome(label, "error", 0, f"{type(exc).__name__}: {exc}")


def collect_ownership_month(month: date) -> CollectOutcome:
    try:
        res = KseiOwnershipSource().fetch(month)
    except Exception as exc:
        _log_error(month, exc)
        return CollectOutcome(month, "error", 0, f"{type(exc).__name__}: {exc}")
    return _collect_ownership(res)


def collect_ownership_range(start_month: date, end_month: date) -> list[CollectOutcome]:
    """`start_month`/`end_month` — any day in the first/last month (inclusive)."""
    out: list[CollectOutcome] = []
    y, m = start_month.year, start_month.month
    while (y, m) <= (end_month.year, end_month.month):
        out.append(collect_ownership_month(date(y, m, 1)))
        time.sleep(CONFIG.request_delay_seconds)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def collect_ownership_latest() -> list[CollectOutcome]:
    """This month + last month (this month's file may not be published yet)."""
    today = date.today()
    prev = (today.replace(day=1) - timedelta(days=1))
    return [collect_ownership_month(prev), collect_ownership_month(today)]
