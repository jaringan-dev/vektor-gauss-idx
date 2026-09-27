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
