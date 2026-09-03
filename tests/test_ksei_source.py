"""Offline tests for the KSEI ownership parser (no network, no DB)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from vg.sources.ksei import (
    _MAX_LOOKBACK_DAYS,
    KseiOwnershipSource,
    _candidate_dates,
    _parse_ksei_date,
)

FIXT = Path(__file__).parent / "fixtures" / "ksei_balancepos_20260831.zip"


@pytest.fixture
def zip_bytes() -> bytes:
    return FIXT.read_bytes()


def test_parse_ksei_date():
    assert _parse_ksei_date("31-AUG-2026") == date(2026, 8, 31)
    assert _parse_ksei_date("27-FEB-2026") == date(2026, 2, 27)
    assert _parse_ksei_date("garbage") is None


def test_candidate_dates_last_weekday_then_back():
    # Aug 2026: 31st is a Monday -> first candidate is the 31st
    cands = _candidate_dates(date(2026, 8, 15))
    assert cands[0] == date(2026, 8, 31)
    assert cands[1] == date(2026, 8, 30)
    assert len(cands) == _MAX_LOOKBACK_DAYS + 1

    # May 2026: 31st Sun, 30th Sat -> first candidate is Fri the 29th
    assert _candidate_dates(date(2026, 5, 1))[0] == date(2026, 5, 29)


def test_parse_filters_to_equity(zip_bytes):
    snap_date, rows = KseiOwnershipSource._parse(zip_bytes)
    assert snap_date == date(2026, 8, 31)
    tickers = {r.ticker for r in rows}
    assert tickers == {"AADI", "AALI", "BBRI"}          # ADCP02B bond excluded


def test_parse_values(zip_bytes):
    _, rows = KseiOwnershipSource._parse(zip_bytes)
    bbri = next(r for r in rows if r.ticker == "BBRI")

    assert bbri.sec_num == Decimal(151559001604)
    assert bbri.price == Decimal(3250)
    assert bbri.local["ID"] == Decimal(15918400350)      # local individual
    assert bbri.local["TOTAL"] == Decimal(31173026233)
    assert bbri.foreign["TOTAL"] == Decimal(39766741039)
    assert bbri.foreign["MF"] == Decimal(20743718258)

    # scripless_pct sanity: (31.17B + 39.77B) / 151.56B ≈ 0.468
    scripless = float((bbri.local["TOTAL"] + bbri.foreign["TOTAL"]) / bbri.sec_num)
    assert 0.46 < scripless < 0.47


def test_fetch_uses_injected_download(monkeypatch, zip_bytes):
    src = KseiOwnershipSource()
    monkeypatch.setattr(src, "_try_download", lambda d: zip_bytes if d == date(2026, 8, 31) else None)

    res = src.fetch(date(2026, 8, 1))
    assert res.status == "ok"
    assert res.month_end == date(2026, 8, 31)
    assert res.raw_ext == "zip"
    assert len(res.rows) == 3


def test_fetch_no_data(monkeypatch):
    src = KseiOwnershipSource()
    monkeypatch.setattr(src, "_try_download", lambda d: None)
    res = src.fetch(date(2019, 1, 1))
    assert res.status == "no_data"
    assert res.month_end is None


def test_collect_row_mapping(zip_bytes):
    from vg.collect import _OWN_COLS, _own_row

    _, rows = KseiOwnershipSource._parse(zip_bytes)
    bbri = next(r for r in rows if r.ticker == "BBRI")
    row = _own_row(date(2026, 8, 31), bbri, "ksei_balancepos", 0)

    assert len(row) == len(_OWN_COLS)
    m = dict(zip(_OWN_COLS, row))
    assert m["local_individual"] == Decimal(15918400350)
    assert m["local_institution"] == Decimal(31173026233) - Decimal(15918400350)
    assert m["foreign_total"] == Decimal(39766741039)
    assert m["holders_count"] is None
    assert m["source_tier"] == 0
    # detail is a psycopg Jsonb wrapper around a plain dict
    detail = m["detail"].obj
    assert detail["sec_num"] == 151559001604
    assert detail["local"]["ID"] == 15918400350
    assert 0.46 < detail["scripless_pct"] < 0.47
