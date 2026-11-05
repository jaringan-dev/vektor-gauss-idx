"""collect.py now validates every row through vg.schemas before it reaches Postgres. These tests pin the
behaviour that matters for the running EOD: bad rows are reported (not silently written or lost), a broken parser
aborts instead of half-writing a day, and the price-history Tier-0 guard survives (no DB, no network)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("pydantic")

from vg import collect
from vg.sources import base as src

D = Decimal
DAY = date(2026, 11, 4)


class _Cur:
    def __init__(self):
        self.many = []       # (sql, rows)
        self.executed = []   # (sql, params)

    def executemany(self, sql, rows):
        self.many.append((sql, list(rows)))

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def batch(self, table):
        return [rows for sql, rows in self.many if f"INTO {table} " in sql]


@pytest.fixture
def cur(monkeypatch):
    c = _Cur()

    class _Conn:
        def cursor(self):
            return c

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(collect, "connect", lambda: _Conn())
    return c


def _flows(n_good, bad=()):
    rows = [src.BrokerFlowRow(broker_code=f"{chr(65 + i // 26)}{chr(65 + i % 26)}", buy_val=D(10), side_flag="D")
            for i in range(n_good)]
    rows += [src.BrokerFlowRow(broker_code=b, buy_val=D(10)) for b in bad]
    return rows


def _broker_res(rows):
    return src.BrokerSummaryFetchResult(
        status="ok", session_date=DAY, ticker="BBCA", source="vektorgauss", source_tier=3, rows=rows)


# ---- broker_flows (the EOD's biggest write) -------------------------------------------------------------------

def test_one_bad_broker_row_is_reported_in_outcome_and_run_log_and_the_rest_is_written(cur):
    out = collect._collect_broker(_broker_res(_flows(20, bad=["X"])))     # "X" is not a 2-letter code
    assert out.status == "ok" and out.rows == 20
    assert "schema: broker_flows: 20 upserted, 1 rejected" in out.detail and "broker_code" in out.detail
    (log_params,) = [p for sql, p in cur.executed if "INSERT INTO run_log" in sql]
    assert "1 rejected" in log_params[4]                                  # run_log.error carries it too
    (written,) = cur.batch("broker_flows")
    assert len(written) == 20 and all(r[2] != "X" for r in written)


def test_clean_batch_leaves_outcome_detail_exactly_as_before(cur):
    out = collect._collect_broker(_broker_res(_flows(5)))
    assert out.status == "ok" and out.rows == 5 and out.detail == "BBCA"
    (log_params,) = [p for sql, p in cur.executed if "INSERT INTO run_log" in sql]
    assert log_params[4] == "BBCA"


def test_a_broken_parser_aborts_the_ticker_and_writes_nothing(cur):
    out = collect._collect_broker(_broker_res(_flows(1, bad=["X", "Y", "Z", "1"])))
    assert out.status == "error" and "failed validation" in out.detail and "nothing written" in out.detail
    assert cur.batch("broker_flows") == []


# ---- price_bars (IDX Stock Summary) ----------------------------------------------------------------------------

def _bar(ticker, **kw):
    return src.PriceBar(ticker=ticker, name=ticker, close=D(100), high=D(101), low=D(99), open=D(100), **kw)


def test_idx_no_first_trade_sentinel_is_accepted_and_stored_as_received(cur):
    res = src.PriceFetchResult(
        status="ok", session_date=DAY, source="idx_stock_summary", source_tier=0,
        bars=[_bar("RDTX", first_trade=D(-99999999)), _bar("BBCA")])
    n = collect._persist(cur, res)
    assert n == 2
    (rows,) = cur.batch("price_bars")
    assert D(-99999999) in rows[0]


def test_price_notes_report_a_rejected_bar(cur):
    res = src.PriceFetchResult(
        status="ok", session_date=DAY, source="idx_stock_summary", source_tier=0,
        bars=[_bar(f"AB{chr(65 + i // 26)}{chr(65 + i % 26)}") for i in range(30)] + [_bar("bad!")])
    notes: list[str] = []
    assert collect._persist(cur, res, notes) == 30
    assert len(notes) == 1 and "price_bars" in notes[0] and "1 rejected" in notes[0]


# ---- price history: the Tier-0 guard must survive --------------------------------------------------------------

def test_history_keeps_the_tier_guard_and_confirms_days_only_from_valid_rows(cur):
    good = [src.PriceBar(ticker="BBCA", name=None, bar_date=date(2026, 11, d), close=D(1)) for d in (2, 3)]
    bad = src.PriceBar(ticker="BBCA", name=None, bar_date=date(2026, 11, 4), close=D(-1))     # negative close
    notes: list[str] = []
    n = collect._persist_price_history(cur, "BBCA", [*good * 10, bad], "vektorgauss_history", 3, notes)
    assert n == 2                                                          # 20 submitted -> 2 distinct days
    sql = next(s for s, _ in cur.many if "INTO price_bars" in s)
    assert "WHERE price_bars.source_tier >= EXCLUDED.source_tier" in sql
    (cal_rows,) = [r for s, r in cur.many if "INTO trading_calendar" in s]
    assert [d for d, _src in cal_rows] == [date(2026, 11, 2), date(2026, 11, 3)]   # the rejected bar's day is absent
    assert "1 rejected" in notes[0]


def test_history_with_all_rows_invalid_aborts(cur):
    bars = [src.PriceBar(ticker="BBCA", name=None, bar_date=date(2026, 11, d), close=D(-1)) for d in (2, 3, 4)]
    with pytest.raises(Exception, match="nothing written"):
        collect._persist_price_history(cur, "BBCA", bars, "vektorgauss_history", 3)
    assert cur.many == []


# ---- broker_inventory / done_by_price --------------------------------------------------------------------------

def test_inventory_and_done_report_rejections(cur):
    inv_rows = [src.BrokerInventoryRow(bar_date=DAY, broker_code=f"A{chr(65 + i)}", net_val=D(-1)) for i in range(20)]
    inv_rows.append(src.BrokerInventoryRow(bar_date=DAY, broker_code="??"))
    inv = src.BrokerInventoryResult(status="ok", ticker="BBCA", source="vektorgauss", source_tier=3, rows=inv_rows)
    notes: list[str] = []
    assert collect._persist_broker_inventory(cur, inv, notes) == 20 and "1 rejected" in notes[0]

    done_rows = [src.DoneByPriceRow(board="RG", price_num=p, qty=D(1)) for p in range(100, 120)]
    done_rows.append(src.DoneByPriceRow(board="RG", price_num=0, qty=D(1)))
    done = src.DoneByPriceResult(
        status="ok", ticker="BBCA", session_date=DAY, source="vektorgauss", source_tier=3, rows=done_rows)
    notes = []
    assert collect._persist_done_by_price(cur, done, notes) == 20 and "1 rejected" in notes[0]


def test_persist_functions_still_work_without_notes(cur):
    assert collect._persist_broker(cur, _broker_res(_flows(3))) == 3
