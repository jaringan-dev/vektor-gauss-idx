"""Offline tests for the IDX Index Summary parser."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from vg.sources.idx import IdxIndexSummarySource

FIXT = Path(__file__).parent / "fixtures" / "index_summary_20260901.json"


@pytest.fixture
def raw() -> bytes:
    return FIXT.read_bytes()


def _patched(monkeypatch, body: bytes) -> IdxIndexSummarySource:
    src = IdxIndexSummarySource()
    monkeypatch.setattr(src, "_get", lambda params: body)
    return src


def test_parses_ok(monkeypatch, raw):
    res = _patched(monkeypatch, raw).fetch(date(2026, 9, 1))
    assert res.status == "ok"
    assert res.session_date == date(2026, 9, 1)
    assert {b.index_code for b in res.bars} == {"COMPOSITE", "LQ45", "IDX30"}

    ihsg = next(b for b in res.bars if b.index_code == "COMPOSITE")
    assert ihsg.close == Decimal("6599.943")
    assert ihsg.prev == Decimal("6525.478")
    assert ihsg.num_stock == Decimal("918.0")
    assert ihsg.market_capital == Decimal("1.15315150321011E+16")


def test_date_mismatch(monkeypatch, raw):
    res = _patched(monkeypatch, raw).fetch(date(2026, 9, 2))
    assert res.status == "date_mismatch"


def test_columns_align():
    from dataclasses import fields

    from vg.collect import _IDX_COLS
    from vg.sources.base import IndexBar

    attrs = {f.name for f in fields(IndexBar)}
    for col in _IDX_COLS:
        if col in ("session_date", "source", "source_tier"):
            continue
        assert col in attrs, f"index_bars column {col!r} has no IndexBar attribute"
