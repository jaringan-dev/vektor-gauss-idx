"""vg.schemas: what a row must look like before it may reach Postgres, and that a validated row is the very
tuple collect.py builds today (no DB, no network)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("pydantic")

from pydantic import ValidationError

from vg import collect, schemas
from vg.sources import base as src

D = Decimal
DAY = date(2026, 11, 4)


class _Cap:
    """Records what `upsert_many` hands to executemany."""

    def __init__(self):
        self.batches = []

    def executemany(self, sql, rows):
        self.batches.append((sql, list(rows)))


def _only_rows(cap):
    assert len(cap.batches) == 1
    return cap.batches[0][1]


# ---- parity with the tuples collect.py builds today ------------------------------------------------------------

def test_price_bar_matches_collect_price_row():
    bar = src.PriceBar(
        ticker="BBCA", name="Bank Central Asia", prev=D(9000), open=D(9050), first_trade=D(9050), high=D(9100),
        low=D(8950), close=D(9075), change=D(75), volume=D(1000), value=D(9000000), frequency=D(40),
        foreign_buy_vol=D(10), foreign_sell_vol=D(5), nonreg_volume=D(1), nonreg_value=D(2), nonreg_frequency=D(3),
        listed_shares=D(100), tradable_shares=D(50), weight_for_index=D(7), index_individual=D(1),
        bid=D(9070), bid_volume=D(5), offer=D(9080), offer_volume=D(6), remarks="x", delisting_date=None)
    row = schemas.PriceBar.from_source(bar, session_date=DAY, source="idx_stock_summary", source_tier=0)
    assert row.as_row() == collect._price_row(DAY, bar, "idx_stock_summary", 0)
    assert schemas.PriceBar.COLUMNS == collect._PRICE_COLS
    assert schemas.PriceBar.update_columns() == collect._PRICE_UPDATE


def test_price_bar_open_outside_range_is_nulled_exactly_like_sane_open():
    bar = src.PriceBar(ticker="BBCA", name=None, open=D(9500), high=D(9100), low=D(8950), close=D(9075))
    row = schemas.PriceBar.from_source(bar, session_date=DAY, source="s", source_tier=0)
    assert row.open is None
    assert row.as_row() == collect._price_row(DAY, bar, "s", 0)


def test_price_bar_untraded_row_keeps_open():
    bar = src.PriceBar(ticker="BBCA", name=None, open=D(100), high=D(0), low=D(0), close=D(100))
    assert schemas.PriceBar.from_source(bar, session_date=DAY, source="s", source_tier=0).open == D(100)


def test_price_bar_history_bar_uses_its_own_date():
    bar = src.PriceBar(ticker="BBCA", name=None, bar_date=date(2026, 11, 3), close=D(1))
    assert schemas.PriceBar.from_source(bar, source="s", source_tier=3).session_date == date(2026, 11, 3)
    with pytest.raises(ValidationError):  # neither a session_date nor a bar_date
        schemas.PriceBar.from_source(src.PriceBar(ticker="BBCA", name=None), source="s", source_tier=3)


def test_broker_flow_matches_persist_broker():
    r = src.BrokerFlowRow(
        broker_code="YP", broker_name="Mirae", buy_val=D(1000), buy_vol=D(10), buy_freq=D(3), sell_val=D(400),
        sell_vol=D(4), sell_freq=D(2), net_val=D(600), net_vol=D(6), buy_avg=D(100), sell_avg=D(100),
        side_flag="D", board="ALL")
    res = src.BrokerSummaryFetchResult(
        status="ok", session_date=DAY, ticker="BBCA", source="vektorgauss", source_tier=3, rows=[r])
    cap = _Cap()
    collect._persist_broker(cap, res, "sha1")
    row = schemas.BrokerFlowRow.from_source(
        r, session_date=DAY, ticker="BBCA", source="vektorgauss", source_tier=3, raw_sha="sha1",
        parser_version=collect._BROKER_PARSER_VERSION)
    assert [row.as_row()] == _only_rows(cap)
    assert schemas.BrokerFlowRow.COLUMNS == collect._BROKER_COLS
    assert schemas.BrokerFlowRow.update_columns() == collect._BROKER_UPDATE


def test_broker_inventory_matches_persist_broker_inventory():
    r = src.BrokerInventoryRow(
        bar_date=DAY, broker_code="YP", broker_name="Mirae", net_val=D(-5), net_vol=D(-1), cum_net_val=D(-90),
        buy_avg=D(10), sell_avg=D(11))
    res = src.BrokerInventoryResult(status="ok", ticker="BBCA", source="vektorgauss", source_tier=3, rows=[r])
    cap = _Cap()
    collect._persist_broker_inventory(cap, res)
    row = schemas.BrokerInventoryRow.from_source(r, ticker="BBCA", source="vektorgauss", source_tier=3)
    assert [row.as_row()] == _only_rows(cap)
    assert schemas.BrokerInventoryRow.COLUMNS == collect._INV_COLS
    assert schemas.BrokerInventoryRow.update_columns() == collect._INV_UPDATE


def test_done_by_price_matches_persist_done_by_price():
    r = src.DoneByPriceRow(
        board="RG", price_num=9075, qty=D(10), value=D(90750), trades=3, buy_aggressor_qty=D(6),
        sell_aggressor_qty=D(4), foreign_buy_qty=D(1), foreign_sell_qty=D(2))
    res = src.DoneByPriceResult(
        status="ok", ticker="BBCA", session_date=DAY, source="vektorgauss", source_tier=3, rows=[r])
    cap = _Cap()
    collect._persist_done_by_price(cap, res)
    row = schemas.DoneByPriceRow.from_source(r, session_date=DAY, ticker="BBCA", source="vektorgauss", source_tier=3)
    assert [row.as_row()] == _only_rows(cap)
    assert schemas.DoneByPriceRow.COLUMNS == collect._DONE_COLS
    assert schemas.DoneByPriceRow.update_columns() == collect._DONE_UPDATE


# ---- what is rejected ------------------------------------------------------------------------------------------

def _flow(**over):
    base = {"session_date": DAY, "ticker": "BBCA", "broker_code": "YP", "source": "vektorgauss", "source_tier": 3}
    base.update(over)
    return schemas.BrokerFlowRow(**base)


def test_ticker_and_broker_code_are_normalised_then_checked():
    row = _flow(ticker=" bbca ", broker_code="yp")
    assert (row.ticker, row.broker_code) == ("BBCA", "YP")
    assert _flow(ticker="GOTOM").ticker == "GOTOM"          # 5-char codes exist in price_bars
    for bad in ("", "B", "BB CA", "bbca!", "TOOLONGCODE"):
        with pytest.raises(ValidationError):
            _flow(ticker=bad)
    for bad in ("Y", "YPX", "Y1", ""):
        with pytest.raises(ValidationError):
            _flow(broker_code=bad)


def test_enum_columns_are_closed():
    assert _flow(side_flag="F", board="RG").board == "RG"
    with pytest.raises(ValidationError):
        _flow(side_flag="X")
    with pytest.raises(ValidationError):
        _flow(board="XX")
    with pytest.raises(ValidationError):
        schemas.DoneByPriceRow(session_date=DAY, ticker="BBCA", board="ALL", price_num=1, source="s", source_tier=3)


def test_negative_volume_value_and_price_are_rejected_but_net_may_be_negative():
    for field in ("buy_val", "sell_val", "buy_vol", "sell_vol", "buy_avg", "sell_avg", "buy_freq", "sell_freq"):
        with pytest.raises(ValidationError):
            _flow(**{field: D(-1)})
    row = _flow(net_val=D(-5_000_000), net_vol=D(-100))
    assert row.net_val == D(-5_000_000)
    inv = schemas.BrokerInventoryRow(
        session_date=DAY, ticker="BBCA", broker_code="YP", cum_net_val=D(-1), source="s", source_tier=3)
    assert inv.cum_net_val == D(-1)


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), float("nan")])
def test_nan_and_infinity_never_reach_a_numeric_column(bad):
    with pytest.raises(ValidationError):
        _flow(net_val=bad)
    with pytest.raises(ValidationError):
        _flow(buy_val=bad)


def test_tier_range_and_source_are_required():
    with pytest.raises(ValidationError):
        _flow(source_tier=4)
    with pytest.raises(ValidationError):
        _flow(source="  ")


def test_done_by_price_price_must_be_positive_and_trades_non_negative():
    ok = {"session_date": DAY, "ticker": "BBCA", "board": "RG", "source": "s", "source_tier": 3}
    with pytest.raises(ValidationError):
        schemas.DoneByPriceRow(price_num=0, **ok)
    with pytest.raises(ValidationError):
        schemas.DoneByPriceRow(price_num=5, trades=-1, **ok)


def test_unknown_columns_are_refused():
    with pytest.raises(ValidationError):
        _flow(nope=1)


def test_numbers_arrive_as_decimal_not_float():
    row = _flow(net_val="12.50")
    assert row.net_val == D("12.50") and isinstance(row.net_val, Decimal)
