"""Offline tests for the IDX Stock Summary parser (no network, no DB)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from vg.sources.idx import IdxStockSummarySource

FIXT = Path(__file__).parent / "fixtures" / "stock_summary_20260901.json"


@pytest.fixture
def raw() -> bytes:
    return FIXT.read_bytes()


def _patched(monkeypatch, body: bytes) -> IdxStockSummarySource:
    src = IdxStockSummarySource()
    monkeypatch.setattr(src, "_get", lambda params: body)
    return src


def test_parses_ok(monkeypatch, raw):
    src = _patched(monkeypatch, raw)
    res = src.fetch(date(2026, 9, 1))

    assert res.status == "ok"
    assert res.session_date == date(2026, 9, 1)
    assert res.source == "idx_stock_summary"
    assert res.source_tier == 0
    assert len(res.bars) == 3

    aadi = next(b for b in res.bars if b.ticker == "AADI")
    assert aadi.name == "Adaro Andalan Indonesia Tbk."
    assert aadi.close == Decimal("10875.0")
    assert aadi.value == Decimal("144092477500.0")
    assert aadi.foreign_buy_vol == Decimal("5325100.0")
    assert aadi.foreign_sell_vol == Decimal("2802200.0")
    assert aadi.nonreg_value == Decimal("1544453125.0")
    assert aadi.tradable_shares == Decimal("7786891760.0")
    assert aadi.remarks == "--MO1SD0F70000A121------------"
    assert aadi.delisting_date is None


def test_empty_data_is_no_data(monkeypatch):
    src = _patched(monkeypatch, b'{"draw":0,"recordsTotal":0,"data":[]}')
    res = src.fetch(date(2026, 9, 6))  # a Sunday
    assert res.status == "no_data"
    assert res.bars == []


def test_date_mismatch(monkeypatch, raw):
    src = _patched(monkeypatch, raw)  # fixture is 2026-09-01
    res = src.fetch(date(2026, 9, 2))
    assert res.status == "date_mismatch"
    assert "2026-09-01" in res.detail


def test_latest_uses_returned_date(monkeypatch, raw):
    src = _patched(monkeypatch, raw)
    res = src.fetch(None)
    assert res.status == "ok"
    assert res.session_date == date(2026, 9, 1)


def test_all_price_columns_map_to_a_field():
    from dataclasses import fields

    from vg.collect import _PRICE_COLS
    from vg.sources.base import PriceBar

    attrs = {f.name for f in fields(PriceBar)}
    for col in _PRICE_COLS:
        if col in ("session_date", "source", "source_tier"):
            continue
        assert col in attrs, f"price_bars column {col!r} has no PriceBar attribute"


def test_fixture_is_valid_json(raw):
    payload = json.loads(raw)
    assert payload["recordsTotal"] == len(payload["data"])
