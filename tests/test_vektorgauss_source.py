"""Offline tests for the vektorgauss broker-summary source."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from vg.sources import vektorgauss
from vg.sources.vektorgauss import VgBrokerSummarySource

# Trimmed from a real 2026-09-27 response: one foreign house (YU=CGS, in the seed
# FOREIGN_BROKERS set) and one domestic (NI=BNI). nval = bval - sval.
_SAMPLE = (
    b'{"stock_code":"BBCA","brokers":['
    b'{"broker_code":"YU","broker_name":"CGS INTERNATIONAL SEKURITAS INDONESIA",'
    b'"bval":245992925000,"bvol":39450200,"bfrq":3068,"sfrq":199,'
    b'"sval":7515232500,"svol":1205400,"nval":238477692500,"nvol":38244800},'
    b'{"broker_code":"NI","broker_name":"BNI SEKURITAS",'
    b'"bval":16204435000,"bvol":2598600,"bfrq":359,"sfrq":148,'
    b'"sval":1830130000,"svol":293100,"nval":14374305000,"nvol":2305500}]}'
)


def test_parse_maps_fields_and_derives_avg():
    rows = VgBrokerSummarySource._parse(_SAMPLE)
    assert len(rows) == 2
    yu = next(r for r in rows if r.broker_code == "YU")
    assert yu.buy_val == Decimal("245992925000")
    assert yu.buy_freq == Decimal("3068")
    assert yu.net_val == Decimal("238477692500")
    # buy_avg = buy_val / buy_vol (IDR per share)
    assert yu.buy_avg == Decimal("245992925000") / Decimal("39450200")


def test_origin_flag_from_static_map():
    rows = {r.broker_code: r for r in VgBrokerSummarySource._parse(_SAMPLE)}
    assert "YU" in vektorgauss.FOREIGN_BROKERS
    assert rows["YU"].side_flag == "F"      # seeded foreign house
    assert rows["NI"].side_flag == "D"      # not in the map -> domestic


def test_all_rows_are_all_board():
    for r in VgBrokerSummarySource._parse(_SAMPLE):
        assert r.board == "ALL"             # feed does not split RG/NG/TN


def test_fetch_injected_ok():
    src = VgBrokerSummarySource()
    src._download = lambda ticker: _SAMPLE
    res = src.fetch(date(2026, 9, 27), "bbca")
    assert res.status == "ok"
    assert res.ticker == "BBCA"             # normalized upper
    assert len(res.rows) == 2
    assert res.source_tier == 3


def test_fetch_empty_is_no_data():
    src = VgBrokerSummarySource()
    src._download = lambda ticker: b'{"stock_code":"XXXX","brokers":[]}'
    res = src.fetch(date(2026, 9, 27), "XXXX")
    assert res.status == "no_data"
    assert res.rows == []


def test_fetch_latest_day_path_calls_download_without_a_date():
    """Back-compat guard: the default path must call _download(ticker) with NO extra argument
    (db37921 once broke this by always passing on_date=, which erred every one-arg override)."""
    src = VgBrokerSummarySource()
    seen = []
    src._download = lambda ticker, **kw: seen.append(kw) or _SAMPLE
    assert src.fetch(date(2026, 9, 27), "BBCA").status == "ok"
    assert seen == [{}]


def test_fetch_use_date_pins_the_requested_day():
    src = VgBrokerSummarySource()
    seen = []
    src._download = lambda ticker, on_date=None: seen.append(on_date) or _SAMPLE
    res = src.fetch(date(2026, 7, 15), "BBCA", use_date=True)
    assert res.status == "ok"
    assert seen == [date(2026, 7, 15)]


def test_download_sends_start_equals_end_only_for_a_dated_pull(monkeypatch):
    captured = {}

    class _Resp:
        content = b"{}"

        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["params"] = params
        return _Resp()

    monkeypatch.setattr(vektorgauss.httpx, "get", fake_get)
    monkeypatch.setattr(vektorgauss.CONFIG, "feed_base_url", "http://feed.test")
    monkeypatch.setattr(vektorgauss.CONFIG, "feed_api_key", "k")
    src = VgBrokerSummarySource()

    src._download("bbca", on_date=date(2026, 7, 15))
    assert captured["params"] == {"start_date": "2026-07-15", "end_date": "2026-07-15"}

    src._download("bbca")
    assert captured["params"] is None       # latest-day request carries no date parameters


# --- broker inventory (broker-accumulation) ---------------------------------
_INV = (
    b'{"stock_code":"BBCA","start_date":"2026-06-03","end_date":"2026-06-04",'
    b'"series":[{"broker_code":"AK","broker_name":"UBS","points":['
    b'{"date":"2026-06-03","nval":-99362772500,"nvol":-17556000,"cum_nval":-99362772500,"bavg":5646.05,"savg":5650.26},'
    b'{"date":"2026-06-04","nval":10000000,"nvol":1600,"cum_nval":-99352772500,"bavg":5660.0,"savg":5655.0}]}]}'
)


def test_inventory_parse_cumulative_and_fields():
    from vg.sources.vektorgauss import VgBrokerInventorySource
    rows = VgBrokerInventorySource._parse(_INV)
    assert len(rows) == 2
    r0, r1 = rows
    assert r0.broker_code == "AK"
    assert str(r0.bar_date) == "2026-06-03"
    assert r0.net_val == Decimal("-99362772500")
    assert r0.cum_net_val == Decimal("-99362772500")
    # running inventory carries forward
    assert r1.cum_net_val == Decimal("-99352772500")


def test_inventory_fetch_injected():
    from datetime import date
    from vg.sources.vektorgauss import VgBrokerInventorySource
    src = VgBrokerInventorySource()
    src._download = lambda t: _INV
    res = src.fetch("bbca")
    assert res.status == "ok" and res.ticker == "BBCA" and res.source_tier == 3
    assert len(res.rows) == 2


# --- done-details local aggregation ----------------------------------------
def test_done_ticks_aggregate_by_price_with_aggressor_and_foreign():
    from vg.sources.vektorgauss import _agg_done_ticks
    ticks = [
        {"market_board": "RG", "price_num": 6225, "qty_num": 10, "value_raw": 6_225_000,
         "action": "BUY", "buyer_type": "F", "seller_type": "D"},
        {"market_board": "RG", "price_num": 6225, "qty_num": 5, "value_raw": 3_112_500,
         "action": "SELL", "buyer_type": "D", "seller_type": "F"},
        {"market_board": "NG", "price_num": 6200, "qty_num": 100, "value_raw": 62_000_000,
         "action": "BUY", "buyer_type": "D", "seller_type": "D"},
    ]
    agg: dict = {}
    _agg_done_ticks(ticks, agg)
    rg = agg[("RG", 6225)]
    assert rg["qty"] == 15 and rg["trades"] == 2
    assert rg["ba"] == 10 and rg["sa"] == 5          # aggressor split
    assert rg["fb"] == 10 and rg["fs"] == 5          # foreign buy/sell split
    assert agg[("NG", 6200)]["qty"] == 100
