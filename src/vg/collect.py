"""Collection orchestrator. Fetch -> store raw artifact -> upsert DB -> log.

Designed so one bad day never aborts a backfill: fetch/persist errors are
caught and written to run_log with status 'error', then the loop continues.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from . import artifacts
from .config import CONFIG
from .db import connect, upsert_many
from .sources.base import (
    KSEI_CATEGORIES,
    CaFetchResult,
    CorpAction,
    DisclosureFetchResult,
    DisclosureFiling,
    IndexBar,
    IndexFetchResult,
    OwnershipFetchResult,
    OwnershipSnapshot,
    PriceBar,
    PriceFetchResult,
)
from .sources.disclosure import IdxDisclosureSource
from .sources.disclosure_pdf import ParsedDisclosure, parse_disclosure_pdf
from .sources.vektorgauss import (
    VgBrokerInventorySource,
    VgBrokerSummarySource,
    VgDoneDetailsSource,
    VgHistoryPriceSource,
)
from .sources.base import (
    BrokerInventoryResult,
    BrokerSummaryFetchResult,
    DoneByPriceResult,
    PriceBar,
)
from .sources.idx import IdxIndexSummarySource, IdxStockSummarySource
from .sources.ksei import KseiCaScheduleSource, KseiOwnershipSource
from .sources.trading_halt import IdxTradingHaltSource, TradingHaltEvent, TradingHaltFetchResult

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


def _log(
    cur: psycopg.Cursor, sd: date, status: str, rows: int, err: str | None,
    phase: str = "collect_price",
) -> None:
    cur.execute(
        "INSERT INTO run_log (phase, batch_date, status, rows_affected, error, finished_at) "
        "VALUES (%s, %s, %s, %s, %s, now())",
        (phase, sd, status, rows, err),
    )


def _log_error(sd: date, exc: BaseException, phase: str = "collect_price") -> None:
    try:
        with connect() as conn, conn.cursor() as cur:
            _log(cur, sd, "error", 0, f"{type(exc).__name__}: {exc}", phase)
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


# ---------------------------------------------------------------------------
# IDX Index Summary (Tier 0)  ->  index_bars
# ---------------------------------------------------------------------------

_IDX_COLS = (
    "session_date", "index_code", "prev", "high", "low", "close", "change",
    "num_stock", "volume", "value", "frequency", "market_capital", "source", "source_tier",
)
_IDX_UPDATE = tuple(c for c in _IDX_COLS if c not in ("session_date", "index_code"))


def _index_row(sd: date, b: IndexBar, source: str, tier: int) -> tuple:
    return (
        sd, b.index_code, b.prev, b.high, b.low, b.close, b.change, b.num_stock,
        b.volume, b.value, b.frequency, b.market_capital, source, tier,
    )


def _collect_index(res: IndexFetchResult) -> CollectOutcome:
    art = artifacts.store(res.source, res.session_date, res.raw_bytes) if res.raw_bytes else None
    try:
        with connect() as conn, conn.cursor() as cur:
            if art:
                artifacts.record(cur, res.source, res.session_date, *art)
            rows = 0
            if res.status == "ok":
                rows = upsert_many(
                    cur, "index_bars", _IDX_COLS,
                    [_index_row(res.session_date, b, res.source, res.source_tier) for b in res.bars],
                    conflict=("session_date", "index_code"), update=_IDX_UPDATE,
                )
                cur.execute(
                    "INSERT INTO trading_calendar (session_date, confirmed_by) VALUES (%s, %s) "
                    "ON CONFLICT (session_date) DO NOTHING",
                    (res.session_date, res.source),
                )
            cur.execute(
                "INSERT INTO run_log (phase, batch_date, status, rows_affected, error, finished_at) "
                "VALUES ('collect_index', %s, %s, %s, %s, now())",
                (res.session_date, res.status, rows, res.detail or None),
            )
        return CollectOutcome(res.session_date, res.status, rows, res.detail)
    except Exception as exc:
        _log_error(res.session_date, exc)
        return CollectOutcome(res.session_date, "error", 0, f"{type(exc).__name__}: {exc}")


def collect_index_day(session_date: date) -> CollectOutcome:
    try:
        res = IdxIndexSummarySource().fetch(session_date)
    except Exception as exc:
        _log_error(session_date, exc)
        return CollectOutcome(session_date, "error", 0, f"{type(exc).__name__}: {exc}")
    return _collect_index(res)


def collect_index_latest() -> CollectOutcome:
    try:
        res = IdxIndexSummarySource().fetch(None)
    except Exception as exc:
        _log_error(date.today(), exc)
        return CollectOutcome(date.today(), "error", 0, f"{type(exc).__name__}: {exc}")
    return _collect_index(res)


def collect_index_range(start: date, end: date) -> list[CollectOutcome]:
    out: list[CollectOutcome] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(collect_index_day(d))
            time.sleep(CONFIG.request_delay_seconds)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Corporate actions  ->  corp_actions
#   (a) KSEI schedule_ca_keseluruhan  (dividends, rights, stock dividends, mergers)
#   (b) derived_from_price_bars       (splits / bonus / rights — share-count jumps)
# ---------------------------------------------------------------------------

_CA_COLS = (
    "ticker", "ex_date", "type", "ratio", "cum_date", "distribution_date",
    "exercise_ratio", "proceed_ratio", "issuer", "notes", "source", "source_tier",
)
_CA_UPDATE = tuple(c for c in _CA_COLS if c not in ("ticker", "ex_date", "type", "source"))


def _ca_row(a: CorpAction, source: str, tier: int) -> tuple:
    return (
        a.ticker, a.ex_date, a.ca_type, None, a.cum_date, a.distribution_date,
        a.exercise_ratio, a.proceed_ratio, a.issuer, a.notes, source, tier,
    )


def _collect_corpactions(res: CaFetchResult) -> CollectOutcome:
    label = date(res.year, 12, 31)
    art = (
        artifacts.store(res.source, label, res.raw_bytes, ext=res.raw_ext)
        if res.raw_bytes else None
    )
    try:
        with connect() as conn, conn.cursor() as cur:
            if art:
                artifacts.record(cur, res.source, label, *art)
            rows = 0
            if res.status == "ok":
                upsert_many(
                    cur, "tickers", ("ticker", "name", "first_seen", "last_seen"),
                    [(a.ticker, None, a.ex_date, a.ex_date) for a in res.rows],
                    conflict=("ticker",),
                )
                rows = upsert_many(
                    cur, "corp_actions", _CA_COLS,
                    [_ca_row(a, res.source, res.source_tier) for a in res.rows],
                    conflict=("ticker", "ex_date", "type", "source"), update=_CA_UPDATE,
                )
            cur.execute(
                "INSERT INTO run_log (phase, batch_date, status, rows_affected, error, finished_at) "
                "VALUES ('collect_corpactions', %s, %s, %s, %s, now())",
                (label, res.status, rows, res.detail or None),
            )
        return CollectOutcome(label, res.status, rows, res.detail)
    except Exception as exc:
        _log_error(label, exc)
        return CollectOutcome(label, "error", 0, f"{type(exc).__name__}: {exc}")


def collect_corpactions_year(year: int) -> CollectOutcome:
    try:
        res = KseiCaScheduleSource().fetch(year)
    except Exception as exc:
        _log_error(date(year, 12, 31), exc)
        return CollectOutcome(date(year, 12, 31), "error", 0, f"{type(exc).__name__}: {exc}")
    return _collect_corpactions(res)


def collect_corpactions_range(start_year: int, end_year: int) -> list[CollectOutcome]:
    out: list[CollectOutcome] = []
    for y in range(start_year, end_year + 1):
        out.append(collect_corpactions_year(y))
        time.sleep(CONFIG.request_delay_seconds)
    return out


# KSEI's schedule omits stock splits. Derive them from a jump in listed_shares
# between consecutive price_bars sessions (Tier-0 data, so source_tier=0).
_SPLIT_MIN_RATIO_DELTA = 0.10   # ignore < 10% changes (buybacks, ESOP, small placements)

_DETECT_SQL = """
WITH seq AS (
  SELECT ticker, session_date, listed_shares, close, prev,
         LAG(listed_shares) OVER w AS prev_listed,
         LAG(close)         OVER w AS prev_close
  FROM price_bars
  WHERE listed_shares IS NOT NULL AND listed_shares > 0
  WINDOW w AS (PARTITION BY ticker ORDER BY session_date)
)
SELECT ticker, session_date, prev_listed, listed_shares, prev_close, close, prev
FROM seq
WHERE prev_listed IS NOT NULL
  AND abs(listed_shares / prev_listed - 1) >= %s
ORDER BY ticker, session_date
"""


def detect_share_count_changes(min_ratio_delta: float = _SPLIT_MIN_RATIO_DELTA) -> int:
    """Scan price_bars for listed_shares jumps -> corp_actions rows
    (type='share_count_change', source='derived_from_price_bars'). Idempotent.
    Returns the number of events written/updated."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(_DETECT_SQL, (min_ratio_delta,))
        events = cur.fetchall()

        rows: list[tuple] = []
        for ticker, sd, prev_listed, listed, prev_close, close, prev in events:
            share_ratio = float(listed) / float(prev_listed)
            # same-session reference-price gap: today's `prev` vs yesterday's close
            price_ratio = (
                float(prev) / float(prev_close) if (prev and prev_close) else None
            )
            # a pure split conserves market cap: price_ratio * share_ratio ~ 1
            looks_like_split = (
                price_ratio is not None and abs(price_ratio * share_ratio - 1) < 0.15
            )
            kind = "split" if looks_like_split else "dilution_or_placement"
            pr = f"{price_ratio:.4f}" if price_ratio is not None else "n/a"
            note = (
                f"{kind}: listed_shares {prev_listed} -> {listed} (x{share_ratio:.4f}); "
                f"ref price {prev_close} -> {prev} (x{pr})"
            )
            rows.append((
                ticker, sd, "share_count_change", round(share_ratio, 6),
                None, None, None, None, None, note,
                "derived_from_price_bars", 0,
            ))

        n = upsert_many(
            cur, "corp_actions", _CA_COLS, rows,
            conflict=("ticker", "ex_date", "type", "source"), update=_CA_UPDATE,
        )
    return n


# ---------------------------------------------------------------------------
# Ownership-disclosure filings (Tier 0)  ->  disclosure_filings
#   >5% substantial + director/commissioner changes, POJK 4/2024 (KSEI AKSes),
#   published on the IDX announcement feed. Phase A: metadata + archive the PDFs.
# ---------------------------------------------------------------------------

_DISC_COLS = (
    "no_pengumuman", "ticker", "filed_at", "filed_date", "title", "subject",
    "party", "jenis", "pdf_url", "pdf_artifact_path", "raw", "source", "source_tier",
)
_DISC_UPDATE = tuple(c for c in _DISC_COLS if c != "no_pengumuman")


def _disc_row(f: DisclosureFiling, pdf_path: str | None, source: str, tier: int) -> tuple:
    return (
        f.no_pengumuman, f.ticker, f.filed_at, f.filed_date, f.title, f.subject,
        f.party, f.jenis,
        (f.attachments[0]["full_save_path"] if f.attachments else None),
        pdf_path, Jsonb(f.raw), source, tier,
    )


def _persist_disclosure(
    cur: psycopg.Cursor, res: DisclosureFetchResult, pdf_paths: dict[str, str]
) -> int:
    upsert_many(
        cur, "tickers", ("ticker", "name", "first_seen", "last_seen"),
        [(f.ticker, None, f.filed_date, f.filed_date) for f in res.rows],
        conflict=("ticker",),
    )
    n = upsert_many(
        cur, "disclosure_filings", _DISC_COLS,
        [_disc_row(f, pdf_paths.get(f.no_pengumuman), res.source, res.source_tier)
         for f in res.rows],
        conflict=("no_pengumuman",), update=_DISC_UPDATE,
    )
    cur.execute(
        "UPDATE disclosure_filings SET fetched_at = now() WHERE filed_date = %s AND source = %s",
        (res.session_date, res.source),
    )
    return n


def _existing_pdf_paths(cur: psycopg.Cursor, nos: list[str]) -> dict[str, str]:
    if not nos:
        return {}
    cur.execute(
        "SELECT no_pengumuman, pdf_artifact_path FROM disclosure_filings "
        "WHERE no_pengumuman = ANY(%s) AND pdf_artifact_path IS NOT NULL",
        (nos,),
    )
    return {no: p for no, p in cur.fetchall() if p and Path(p).exists()}


def _collect_disclosure(res: DisclosureFetchResult) -> CollectOutcome:
    sd = res.session_date
    if res.status not in ("ok", "no_data"):
        _log_error(sd, RuntimeError(res.detail or res.status), "collect_disclosure")
        return CollectOutcome(sd, res.status, 0, res.detail)

    # already-archived PDFs (self-healing re-runs skip the re-fetch)
    with connect() as conn, conn.cursor() as cur:
        have = _existing_pdf_paths(cur, [f.no_pengumuman for f in res.rows])

    pdf_paths: dict[str, str] = dict(have)
    pdf_arts: list[tuple] = []          # (source, logical_date, path, sha, nbytes)
    notes: list[str] = []
    src = IdxDisclosureSource()
    for f in res.rows:
        if f.no_pengumuman in have or not f.attachments:
            continue
        url = f.attachments[0]["full_save_path"]
        stem = Path(f.attachments[0].get("original_filename") or f.no_pengumuman).stem \
            or f.no_pengumuman.replace("/", "-")
        try:
            pdf = src.fetch_pdf(url)
            art = artifacts.store("idx_disclosure_pdf", f.filed_date, pdf, ext="pdf", stem=stem)
            pdf_paths[f.no_pengumuman] = str(art[0])
            pdf_arts.append(("idx_disclosure_pdf", f.filed_date, *art))
        except Exception as exc:  # noqa: BLE001 — one bad PDF never aborts the day
            notes.append(f"{f.no_pengumuman}: {type(exc).__name__}")
        time.sleep(CONFIG.request_delay_seconds)

    list_art = (
        artifacts.store(res.source, sd, res.raw_bytes, ext=res.raw_ext)
        if res.raw_bytes else None
    )
    try:
        with connect() as conn, conn.cursor() as cur:
            if list_art:
                artifacts.record(cur, res.source, sd, *list_art)
            for source, ld, path, sha, nbytes in pdf_arts:
                artifacts.record(cur, source, ld, path, sha, nbytes)
            rows = _persist_disclosure(cur, res, pdf_paths) if res.status == "ok" else 0
            detail = "; ".join(x for x in [res.detail, *notes] if x)
            _log(cur, sd, res.status, rows, detail or None, "collect_disclosure")
        return CollectOutcome(sd, res.status, rows, detail)
    except Exception as exc:
        _log_error(sd, exc, "collect_disclosure")
        return CollectOutcome(sd, "error", 0, f"{type(exc).__name__}: {exc}")


def collect_disclosure_day(session_date: date) -> CollectOutcome:
    try:
        res = IdxDisclosureSource().fetch(session_date)
    except Exception as exc:
        _log_error(session_date, exc, "collect_disclosure")
        return CollectOutcome(session_date, "error", 0, f"{type(exc).__name__}: {exc}")
    return _collect_disclosure(res)


def collect_disclosure_range(start: date, end: date) -> list[CollectOutcome]:
    """Inclusive. Skips weekends locally; the feed simply returns no_data for days
    with no filings (KSEI occasionally posts on a Saturday — a --date catches those)."""
    out: list[CollectOutcome] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(collect_disclosure_day(d))
            time.sleep(CONFIG.request_delay_seconds)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Disclosure PDF parse (Phase B, Tier 0) -> disclosure_holdings
#   Reads the PDFs Phase A already archived on disk; adds per-transaction
#   direction (buy/sell) + the header ownership-change numbers. No network.
# ---------------------------------------------------------------------------

_DH_COLS = (
    "no_pengumuman", "row_seq", "ticker", "filed_date",
    "reporter_name", "is_insider", "position", "is_controller",
    "shares_before", "shares_after", "voting_rights_before", "voting_rights_after",
    "transaction_type", "direction", "ownership_status", "direct_investor",
    "shares_qty", "share_class", "price", "transaction_date", "purpose",
    "source", "source_tier",
)
_DH_UPDATE = tuple(c for c in _DH_COLS if c not in ("no_pengumuman", "row_seq"))


def _dh_rows(
    no_pengumuman: str, ticker: str, filed_date: date, parsed: ParsedDisclosure,
    source: str, tier: int,
) -> list[tuple]:
    h = parsed.header
    header_part = (
        h.reporter_name, h.is_insider, h.position, h.is_controller,
        h.shares_before, h.shares_after, h.voting_rights_before, h.voting_rights_after,
    )
    if not parsed.transactions:
        # Header (shares before/after) still regexed independently of the
        # per-row table — enough on its own for the INSIDER-*/SUBSTANTIAL-*
        # direction tags even when the transaction breakdown didn't parse.
        return [(no_pengumuman, 0, ticker, filed_date, *header_part,
                 None, None, None, None, None, None, None, None, None, source, tier)]
    return [
        (no_pengumuman, i, ticker, filed_date, *header_part,
         t.transaction_type, t.direction, t.ownership_status, t.direct_investor,
         t.shares_qty, t.share_class, t.price, t.transaction_date, t.purpose, source, tier)
        for i, t in enumerate(parsed.transactions)
    ]


def _unparsed_filings(
    cur: psycopg.Cursor, start: date | None, end: date | None
) -> list[tuple[str, str, date, str]]:
    """(no_pengumuman, ticker, filed_date, pdf_artifact_path) for archived
    filings that don't have disclosure_holdings rows yet."""
    where = ["f.pdf_artifact_path IS NOT NULL", "h.no_pengumuman IS NULL"]
    params: list = []
    if start:
        where.append("f.filed_date >= %s")
        params.append(start)
    if end:
        where.append("f.filed_date <= %s")
        params.append(end)
    cur.execute(
        f"""
        SELECT f.no_pengumuman, f.ticker, f.filed_date, f.pdf_artifact_path
        FROM disclosure_filings f
        LEFT JOIN disclosure_holdings h ON h.no_pengumuman = f.no_pengumuman
        WHERE {' AND '.join(where)}
        ORDER BY f.filed_date
        """,
        params,
    )
    return cur.fetchall()


def parse_disclosure_pdfs(start: date | None = None, end: date | None = None) -> CollectOutcome:
    """Phase B: parse archived disclosure PDFs (Phase A's metadata + PDF,
    already collected) into disclosure_holdings. Idempotent by construction —
    only filings without an existing holdings row are processed, so re-runs
    only pick up what's new (delete a filing's holdings rows to force a
    re-parse after a parser fix). start/end filter by filed_date; omit both
    to catch up on every unparsed filing."""
    today = date.today()
    with connect() as conn, conn.cursor() as cur:
        pending = _unparsed_filings(cur, start, end)
    if not pending:
        return CollectOutcome(today, "no_data", 0, "nothing to parse")

    rows: list[tuple] = []
    errors: list[str] = []
    for no_peng, ticker, filed_date, pdf_path in pending:
        try:
            parsed = parse_disclosure_pdf(Path(pdf_path).read_bytes())
            rows.extend(_dh_rows(no_peng, ticker, filed_date, parsed, "idx_disclosure_pdf", 0))
        except Exception as exc:  # noqa: BLE001 — one bad PDF never aborts the batch
            errors.append(f"{no_peng}: {type(exc).__name__}: {exc}")

    try:
        with connect() as conn, conn.cursor() as cur:
            n = upsert_many(
                cur, "disclosure_holdings", _DH_COLS, rows,
                conflict=("no_pengumuman", "row_seq"), update=_DH_UPDATE,
            )
            detail = f"{len(pending)} filings, {n} rows" + (
                f"; {len(errors)} errors: {'; '.join(errors[:5])}" if errors else ""
            )
            _log(cur, today, "ok" if not errors else "partial", n, detail, "parse_disclosure")
        return CollectOutcome(today, "ok" if not errors else "partial", n, detail)
    except Exception as exc:
        _log_error(today, exc, "parse_disclosure")
        return CollectOutcome(today, "error", 0, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Trading-halt announcements (Tier 0) -> trading_halts
#   Metadata only, no PDF — see sources/trading_halt.py for why this can't
#   reuse IdxDisclosureSource despite it being keyword-parameterized.
# ---------------------------------------------------------------------------

_HALT_COLS = (
    "no_pengumuman", "ticker", "filed_at", "filed_date", "title", "is_halt", "source", "source_tier",
)
_HALT_UPDATE = tuple(c for c in _HALT_COLS if c != "no_pengumuman")


def _halt_row(e: TradingHaltEvent, source: str, tier: int) -> tuple:
    return (e.no_pengumuman, e.ticker, e.filed_at, e.filed_date, e.title, e.is_halt, source, tier)


def _collect_trading_halts(res: TradingHaltFetchResult) -> CollectOutcome:
    sd = res.session_date
    if res.status not in ("ok", "no_data"):
        _log_error(sd, RuntimeError(res.detail or res.status), "collect_trading_halts")
        return CollectOutcome(sd, res.status, 0, res.detail)
    try:
        with connect() as conn, conn.cursor() as cur:
            n = 0
            skipped = 0
            if res.status == "ok":
                # IDX occasionally posts dummy/test announcements (e.g. ticker
                # "TESTSBKD", seen live 2026-09-05) through the same feed —
                # only accept tickers already known from real price data.
                cur.execute(
                    "SELECT ticker FROM tickers WHERE ticker = ANY(%s)",
                    ([e.ticker for e in res.rows],),
                )
                known = {t for (t,) in cur.fetchall()}
                rows = [e for e in res.rows if e.ticker in known]
                skipped = len(res.rows) - len(rows)
                if rows:
                    n = upsert_many(
                        cur, "trading_halts", _HALT_COLS,
                        [_halt_row(e, res.source, res.source_tier) for e in rows],
                        conflict=("no_pengumuman",), update=_HALT_UPDATE,
                    )
            detail = res.detail
            if skipped:
                detail = f"{detail}; {skipped} skipped (unknown/test ticker)".strip("; ")
            _log(cur, sd, res.status, n, detail or None, "collect_trading_halts")
        return CollectOutcome(sd, res.status, n, detail)
    except Exception as exc:
        _log_error(sd, exc, "collect_trading_halts")
        return CollectOutcome(sd, "error", 0, f"{type(exc).__name__}: {exc}")


def collect_trading_halts_day(session_date: date) -> CollectOutcome:
    try:
        res = IdxTradingHaltSource().fetch(session_date)
    except Exception as exc:
        _log_error(session_date, exc, "collect_trading_halts")
        return CollectOutcome(session_date, "error", 0, f"{type(exc).__name__}: {exc}")
    return _collect_trading_halts(res)


def collect_trading_halts_range(start: date, end: date) -> list[CollectOutcome]:
    """Inclusive. Skips weekends locally; the feed returns no_data for days
    with no halt announcements."""
    out: list[CollectOutcome] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(collect_trading_halts_day(d))
            time.sleep(CONFIG.request_delay_seconds)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Broker summary (Phase 1.5, vektorgauss, Tier 3). FORWARD-ONLY: the feed
# ignores any date and always returns the latest published trading day, so rows
# are stamped with the session_date the caller passes (the latest confirmed
# trading day) and there is no backfill. Per-ticker; the default universe is the
# watchlist + holdings, not the full ~950-name market (quota + relevance).
# ---------------------------------------------------------------------------

_BROKER_COLS = (
    "session_date", "ticker", "broker_code", "side_flag", "buy_val", "sell_val",
    "buy_vol", "sell_vol", "buy_avg", "sell_avg", "buy_freq", "sell_freq",
    "net_val", "net_vol", "board", "source", "source_tier",
)
_BROKER_UPDATE = tuple(
    c for c in _BROKER_COLS
    if c not in ("session_date", "ticker", "broker_code", "board")
)


def _persist_broker(cur: psycopg.Cursor, res: BrokerSummaryFetchResult) -> int:
    rows = [
        (res.session_date, res.ticker, r.broker_code, r.side_flag, r.buy_val,
         r.sell_val, r.buy_vol, r.sell_vol, r.buy_avg, r.sell_avg, r.buy_freq,
         r.sell_freq, r.net_val, r.net_vol, r.board, res.source, res.source_tier)
        for r in res.rows
    ]
    return upsert_many(
        cur, "broker_flows", _BROKER_COLS, rows,
        conflict=("session_date", "ticker", "broker_code", "board"),
        update=_BROKER_UPDATE,
    )


def _collect_broker(res: BrokerSummaryFetchResult) -> CollectOutcome:
    art = (
        artifacts.store(res.source, res.session_date, res.raw_bytes, stem=res.ticker)
        if res.raw_bytes else None
    )
    try:
        with connect() as conn, conn.cursor() as cur:
            if art:
                artifacts.record(cur, res.source, res.session_date, *art)
            rows = _persist_broker(cur, res) if res.status == "ok" else 0
            _log(cur, res.session_date, res.status, rows,
                 (res.detail or f"{res.ticker}"), "collect_broker")
        detail = res.ticker if res.status == "ok" or not res.detail else f"{res.ticker}: {res.detail}"
        return CollectOutcome(res.session_date, res.status, rows, detail)
    except Exception as exc:
        _log_error(res.session_date, exc, "collect_broker")
        return CollectOutcome(res.session_date, "error", 0, f"{res.ticker}: {exc}")


def _harvest_universe() -> list[str]:
    """The default 'panen' set: watchlist + holdings + VG_HARVEST_TICKERS, deduped."""
    return list(dict.fromkeys((*CONFIG.watchlist, *CONFIG.holdings, *CONFIG.harvest_tickers)))


def _have_for_date(cur: psycopg.Cursor, table: str, tickers: list[str], d: date) -> set[str]:
    """Tickers that already have >=1 row in `table` on session_date d — used to
    skip API calls for data we already hold (quota saver). Idempotent re-runs cost 0."""
    if not tickers:
        return set()
    cur.execute(
        f"SELECT DISTINCT ticker FROM {table} WHERE session_date = %s AND ticker = ANY(%s)",
        (d, tickers),
    )
    return {r[0] for r in cur.fetchall()}


def collect_broker_summary_ticker(session_date: date, ticker: str) -> CollectOutcome:
    try:
        res = VgBrokerSummarySource().fetch(session_date, ticker)
    except Exception as exc:
        _log_error(session_date, exc, "collect_broker")
        return CollectOutcome(session_date, "error", 0, f"{ticker}: {exc}")
    return _collect_broker(res)


def collect_broker_summary_latest(
    tickers: list[str] | None = None, *, skip_existing: bool = True
) -> list[CollectOutcome]:
    """Latest trading day, one call per ticker. `tickers` defaults to the harvest
    universe (watchlist + holdings + VG_HARVEST_TICKERS). With skip_existing (default),
    tickers that already have broker_flows for that day are skipped — no wasted call."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(session_date) FROM trading_calendar")
        latest = cur.fetchone()[0]
        if latest is None:
            return [CollectOutcome(date.today(), "error", 0,
                                   "no trading_calendar rows — run collect-price first")]
        tickers = tickers or _harvest_universe()
        have = _have_for_date(cur, "broker_flows", tickers, latest) if skip_existing else set()
    todo = [t for t in tickers if t not in have]
    out: list[CollectOutcome] = []
    for i, t in enumerate(todo):
        out.append(collect_broker_summary_ticker(latest, t))
        if i < len(todo) - 1:
            time.sleep(CONFIG.request_delay_seconds)
    if have:
        out.append(CollectOutcome(latest, "ok", 0, f"skipped {len(have)} already-have"))
    return out


# Cap on how many radar-flagged names to pull broker summary for in one EOD pass,
# so a busy radar day can't blow the free daily quota. Watchlist + holdings are
# always collected; this bounds only the extra radar candidates.
_BROKER_RADAR_CAP = 60


def collect_broker_summary_radar() -> list[CollectOutcome]:
    """Two-pass EOD helper: broker summary for watchlist + holdings + the names
    flagged by today's radar tags (CONFIG.radar_tags), ranked by weekly NFF and
    capped. Run AFTER the first `compute` (which produces the radar tags), then
    run `compute` again so the broker signals + the RADAR broker line land."""
    base = list(dict.fromkeys((*CONFIG.watchlist, *CONFIG.holdings)))
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(session_date) FROM trading_calendar")
        latest = cur.fetchone()[0]
        if latest is None:
            return [CollectOutcome(date.today(), "error", 0,
                                   "no trading_calendar rows — run collect-price first")]
        extra: list[str] = []
        if CONFIG.radar_tags:
            cur.execute(
                """
                SELECT sl.ticker
                FROM signal_log sl
                JOIN signal_components sc
                  ON sc.session_date = sl.session_date AND sc.ticker = sl.ticker
                WHERE sl.session_date = %s AND sl.tag = ANY(%s)
                GROUP BY sl.ticker, sc.nff_w1
                ORDER BY sc.nff_w1 DESC NULLS LAST
                LIMIT %s
                """,
                (latest, list(CONFIG.radar_tags), _BROKER_RADAR_CAP),
            )
            extra = [r[0] for r in cur.fetchall()]
    tickers = list(dict.fromkeys([*base, *extra]))
    return collect_broker_summary_latest(tickers=tickers)


# ---------------------------------------------------------------------------
# Price history fallback (vektorgauss_history, Tier 3). Cloudflare-free per-ticker
# OHLCV + foreign flow (~120 td/call). GAP-FILLS price_bars: a DO UPDATE guarded
# by source_tier means it fills missing days and refreshes its own Tier-3 rows but
# NEVER overwrites Tier-0 (IDX-direct) data. Also confirms those days in
# trading_calendar so `compute` can run even when the IDX price feed is 403-blocked.
# ---------------------------------------------------------------------------

def _persist_price_history(
    cur: psycopg.Cursor, ticker: str, bars: list[PriceBar], source: str, tier: int
) -> int:
    rows = [_price_row(b.bar_date, b, source, tier) for b in bars if b.bar_date]
    if not rows:
        return 0
    cols = ", ".join(_PRICE_COLS)
    ph = "(" + ", ".join(["%s"] * len(_PRICE_COLS)) + ")"
    setc = ", ".join(f"{c} = EXCLUDED.{c}" for c in _PRICE_UPDATE)
    cur.executemany(
        f"INSERT INTO price_bars ({cols}) VALUES {ph} "
        f"ON CONFLICT (session_date, ticker) DO UPDATE SET {setc} "
        f"WHERE price_bars.source_tier >= EXCLUDED.source_tier",  # never downgrade Tier-0
        rows,
    )
    dates = sorted({b.bar_date for b in bars if b.bar_date})
    if dates:
        cur.execute(
            "INSERT INTO tickers (ticker, first_seen, last_seen) VALUES (%s, %s, %s) "
            "ON CONFLICT (ticker) DO UPDATE SET "
            "first_seen = LEAST(tickers.first_seen, EXCLUDED.first_seen), "
            "last_seen = GREATEST(tickers.last_seen, EXCLUDED.last_seen), updated_at = now()",
            (ticker, dates[0], dates[-1]),
        )
        cur.executemany(
            "INSERT INTO trading_calendar (session_date, confirmed_by) VALUES (%s, %s) "
            "ON CONFLICT (session_date) DO NOTHING",
            [(d, source) for d in dates],
        )
    return len(rows)


def collect_price_history_ticker(ticker: str) -> CollectOutcome:
    try:
        res = VgHistoryPriceSource().fetch(ticker)
    except Exception as exc:
        _log_error(date.today(), exc, "collect_price_history")
        return CollectOutcome(date.today(), "error", 0, f"{ticker}: {exc}")
    latest = max((b.bar_date for b in res.bars if b.bar_date), default=date.today())
    art = (
        artifacts.store(res.source, latest, res.raw_bytes, stem=f"{ticker}-hist")
        if res.raw_bytes else None
    )
    try:
        with connect() as conn, conn.cursor() as cur:
            if art:
                artifacts.record(cur, res.source, latest, *art)
            rows = (
                _persist_price_history(cur, ticker, res.bars, res.source, res.source_tier)
                if res.status == "ok" else 0
            )
            _log(cur, latest, res.status, rows, res.detail or ticker, "collect_price_history")
        detail = ticker if res.status == "ok" else f"{ticker}: {res.detail}"
        return CollectOutcome(latest, res.status, rows, detail)
    except Exception as exc:
        _log_error(latest, exc, "collect_price_history")
        return CollectOutcome(latest, "error", 0, f"{ticker}: {exc}")


def collect_price_history_universe(
    tickers: list[str] | None = None, *, skip_existing: bool = True
) -> list[CollectOutcome]:
    """Gap-fill OHLCV + foreign flow for the harvest universe from the VG feed
    (~120 td/call). With skip_existing, a ticker that already has the latest
    trading day in price_bars (from ANY source, e.g. IDX Tier-0) is skipped — the
    history call would only duplicate it."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(session_date) FROM trading_calendar")
        latest = cur.fetchone()[0]
        tickers = tickers or _harvest_universe()
        have = (
            _have_for_date(cur, "price_bars", tickers, latest)
            if (skip_existing and latest) else set()
        )
    todo = [t for t in tickers if t not in have]
    out: list[CollectOutcome] = []
    for i, t in enumerate(todo):
        out.append(collect_price_history_ticker(t))
        if i < len(todo) - 1:
            time.sleep(CONFIG.request_delay_seconds)
    if have:
        out.append(CollectOutcome(latest, "ok", 0, f"skipped {len(have)} already-have"))
    return out


# ---------------------------------------------------------------------------
# Broker inventory (vektorgauss_inventory, Tier 3). Historical per-broker daily
# net + running cum_net_val (~80 td, top brokers/ticker) -> broker_inventory.
# Powers the Transaction / Inventory / Compare-Inventory charts. Per-ticker.
# ---------------------------------------------------------------------------

_INV_COLS = (
    "session_date", "ticker", "broker_code", "broker_name", "net_val", "net_vol",
    "cum_net_val", "buy_avg", "sell_avg", "source", "source_tier",
)
_INV_UPDATE = tuple(
    c for c in _INV_COLS if c not in ("session_date", "ticker", "broker_code")
)


def _persist_broker_inventory(cur: psycopg.Cursor, res: BrokerInventoryResult) -> int:
    rows = [
        (r.bar_date, res.ticker, r.broker_code, r.broker_name, r.net_val, r.net_vol,
         r.cum_net_val, r.buy_avg, r.sell_avg, res.source, res.source_tier)
        for r in res.rows
    ]
    return upsert_many(
        cur, "broker_inventory", _INV_COLS, rows,
        conflict=("session_date", "ticker", "broker_code"), update=_INV_UPDATE,
    )


def collect_broker_inventory_ticker(ticker: str) -> CollectOutcome:
    try:
        res = VgBrokerInventorySource().fetch(ticker)
    except Exception as exc:
        _log_error(date.today(), exc, "collect_broker_inventory")
        return CollectOutcome(date.today(), "error", 0, f"{ticker}: {exc}")
    latest = max((r.bar_date for r in res.rows), default=date.today())
    art = (
        artifacts.store(res.source, latest, res.raw_bytes, stem=ticker)
        if res.raw_bytes else None
    )
    try:
        with connect() as conn, conn.cursor() as cur:
            if art:
                artifacts.record(cur, res.source, latest, *art)
            rows = _persist_broker_inventory(cur, res) if res.status == "ok" else 0
            _log(cur, latest, res.status, rows, res.detail or ticker, "collect_broker_inventory")
        detail = ticker if res.status == "ok" else f"{ticker}: {res.detail}"
        return CollectOutcome(latest, res.status, rows, detail)
    except Exception as exc:
        _log_error(latest, exc, "collect_broker_inventory")
        return CollectOutcome(latest, "error", 0, f"{ticker}: {exc}")


def collect_broker_inventory_universe(
    tickers: list[str] | None = None, *, skip_existing: bool = True
) -> list[CollectOutcome]:
    """~80 td per-broker inventory for the harvest universe. With skip_existing, a
    ticker that already has broker_inventory for the latest trading day is skipped
    (the ~80 td window barely moves day-to-day — re-pull only when stale)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(session_date) FROM trading_calendar")
        latest = cur.fetchone()[0]
        tickers = tickers or _harvest_universe()
        have = (
            _have_for_date(cur, "broker_inventory", tickers, latest)
            if (skip_existing and latest) else set()
        )
    todo = [t for t in tickers if t not in have]
    out: list[CollectOutcome] = []
    for i, t in enumerate(todo):
        out.append(collect_broker_inventory_ticker(t))
        if i < len(todo) - 1:
            time.sleep(CONFIG.request_delay_seconds)
    if have:
        out.append(CollectOutcome(latest, "ok", 0, f"skipped {len(have)} already-have"))
    return out


# ---------------------------------------------------------------------------
# Done-by-price (vektorgauss_done, Tier 3). Done-details is paginated & expensive
# (~130 pages for a liquid stock/day), so we fetch ONCE, aggregate LOCALLY into
# price-level buckets, and never re-fetch (freshness guard). On-demand, not EOD.
# ---------------------------------------------------------------------------

_DONE_COLS = (
    "session_date", "ticker", "board", "price_num", "qty", "value", "trades",
    "buy_aggressor_qty", "sell_aggressor_qty", "foreign_buy_qty", "foreign_sell_qty",
    "source", "source_tier",
)
_DONE_UPDATE = tuple(
    c for c in _DONE_COLS if c not in ("session_date", "ticker", "board", "price_num")
)


def _persist_done_by_price(cur: psycopg.Cursor, res: DoneByPriceResult) -> int:
    sd = res.session_date
    rows = [
        (sd, res.ticker, r.board, r.price_num, r.qty, r.value, r.trades,
         r.buy_aggressor_qty, r.sell_aggressor_qty, r.foreign_buy_qty, r.foreign_sell_qty,
         res.source, res.source_tier)
        for r in res.rows
    ]
    return upsert_many(
        cur, "done_by_price", _DONE_COLS, rows,
        conflict=("session_date", "ticker", "board", "price_num"), update=_DONE_UPDATE,
    )


def collect_done_details_ticker(
    ticker: str, session_date: date | None = None, *, max_pages: int = 200, force: bool = False
) -> CollectOutcome:
    """Fetch + aggregate one ticker's done-details for one day into done_by_price.
    Skips (0 API calls) if that day is already stored, unless force=True."""
    ticker = ticker.upper()
    # Freshness guard — resolve the target day and skip if we already have it.
    with connect() as conn, conn.cursor() as cur:
        target = session_date
        if target is None:
            cur.execute("SELECT max(session_date) FROM trading_calendar")
            target = cur.fetchone()[0]
        if target is not None and not force:
            cur.execute(
                "SELECT 1 FROM done_by_price WHERE ticker = %s AND session_date = %s LIMIT 1",
                (ticker, target),
            )
            if cur.fetchone():
                return CollectOutcome(target, "ok", 0, f"{ticker}: already have (skipped)")
    try:
        res = VgDoneDetailsSource().fetch(ticker, target, max_pages=max_pages)
    except Exception as exc:
        _log_error(target or date.today(), exc, "collect_done_details")
        return CollectOutcome(target or date.today(), "error", 0, f"{ticker}: {exc}")
    sd = res.session_date or target or date.today()
    res.session_date = sd
    try:
        with connect() as conn, conn.cursor() as cur:
            rows = _persist_done_by_price(cur, res) if res.status == "ok" else 0
            _log(cur, sd, res.status, rows, res.detail or ticker, "collect_done_details")
        detail = f"{ticker} ({res.pages_fetched}/{res.total_pages} pg)"
        if res.detail:
            detail += f" — {res.detail}"
        return CollectOutcome(sd, res.status, rows, detail)
    except Exception as exc:
        _log_error(sd, exc, "collect_done_details")
        return CollectOutcome(sd, "error", 0, f"{ticker}: {exc}")
