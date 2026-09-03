"""Offline tests for the KSEI corporate-action schedule parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from vg.sources.ksei import KseiCaScheduleSource, _normalize_ca_type

FIXT = Path(__file__).parent / "fixtures" / "ksei_ca_schedule_2026.xls"


@pytest.fixture
def html_bytes() -> bytes:
    return FIXT.read_bytes()


def test_normalize_ca_type():
    assert _normalize_ca_type("CASH DIVIDEND") == "cash_dividend"
    assert _normalize_ca_type("RIGHT DISTRIBUTION") == "rights"
    assert _normalize_ca_type("STOCK DIVIDEND") == "stock_dividend"
    assert _normalize_ca_type("REVERSE STOCK SPLIT") == "reverse_split"
    assert _normalize_ca_type("STOCK SPLIT") == "stock_split"
    assert _normalize_ca_type("INTEREST") == "interest"


def test_parse_filters_and_maps(html_bytes):
    rows = KseiCaScheduleSource._parse(html_bytes)
    by_ticker = {r.ticker: r for r in rows}

    # INTEREST (bond) row dropped; 3 equity CA kept
    assert set(by_ticker) == {"BBCA", "AMRT", "TOWR"}

    bbca = by_ticker["BBCA"]
    assert bbca.ca_type == "cash_dividend"
    assert bbca.ex_date == date(2026, 3, 12)          # recording date
    assert bbca.cum_date == date(2026, 3, 10)
    assert bbca.distribution_date == date(2026, 3, 17)
    assert bbca.issuer == "BANK CENTRAL ASIA Tbk, PT"

    amrt = by_ticker["AMRT"]
    assert amrt.ca_type == "rights"
    assert amrt.exercise_ratio == "1000 : 150"
    assert "HMETD" in amrt.notes


def test_fetch_injected(monkeypatch, html_bytes):
    src = KseiCaScheduleSource()
    monkeypatch.setattr(src, "_download", lambda year: html_bytes)
    res = src.fetch(2026)
    assert res.status == "ok"
    assert res.year == 2026
    assert len(res.rows) == 3


def test_fetch_404(monkeypatch):
    src = KseiCaScheduleSource()
    monkeypatch.setattr(src, "_download", lambda year: None)
    res = src.fetch(1999)
    assert res.status == "no_data"


def test_ca_columns_align(html_bytes):
    from vg.collect import _CA_COLS, _ca_row

    rows = KseiCaScheduleSource._parse(html_bytes)
    row = _ca_row(rows[0], "ksei_ca_schedule", 0)
    assert len(row) == len(_CA_COLS)
    m = dict(zip(_CA_COLS, row))
    assert m["type"] == "cash_dividend"
    assert m["source"] == "ksei_ca_schedule"
    assert m["ratio"] is None
