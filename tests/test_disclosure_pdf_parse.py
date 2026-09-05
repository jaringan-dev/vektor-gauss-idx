"""Offline tests for Phase B disclosure-PDF parsing, against real archived
filings (tests/fixtures/disclosure_*.pdf — no DB, no network)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from vg.sources.disclosure_pdf import parse_disclosure_pdf

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def test_isat_multi_row_director_purchase():
    """LK/03082026/0015/1 — director VIKRAM SINHA, 7 open-market purchases."""
    parsed = parse_disclosure_pdf(_load("disclosure_isat_20260803.pdf"))
    h = parsed.header

    assert h.reporter_name == "VIKRAM SINHA"
    assert h.is_insider is True
    assert h.position == "Direksi"
    assert h.citizenship == "WNA"
    assert h.shares_before == 11_170_700.0
    assert h.voting_rights_before == pytest.approx(0.000346)
    assert h.shares_after == 14_256_900.0
    assert h.voting_rights_after == pytest.approx(0.000442)
    assert h.is_controller is False

    assert not parsed.notes
    assert len(parsed.transactions) == 7

    expected_qty = [100_000.0, 696_400.0, 775_000.0, 32_100.0, 408_600.0, 688_300.0, 385_800.0]
    expected_price = [1915.0, 1945.0, 1950.0, 1910.0, 1935.0, 1940.0, 1930.0]
    assert [t.shares_qty for t in parsed.transactions] == expected_qty
    assert [t.price for t in parsed.transactions] == expected_price

    t0 = parsed.transactions[0]
    assert t0.transaction_type == "Pembelian"
    assert t0.direction == "buy"
    assert t0.repurchase_agreement is False
    assert t0.ownership_status == "Langsung"
    assert t0.direct_investor is None
    assert t0.share_class == "Saham Biasa"
    assert t0.transaction_date == date(2026, 7, 30)
    assert t0.purpose == "Investasi"

    # every row shares the same transaction date and purpose in this filing
    assert all(t.transaction_date == date(2026, 7, 30) for t in parsed.transactions)
    assert all(t.purpose == "Investasi" for t in parsed.transactions)
    assert all(t.direction == "buy" for t in parsed.transactions)
    # total bought should roughly match the before/after share delta
    total_qty = sum(t.shares_qty for t in parsed.transactions)
    assert total_qty == pytest.approx(h.shares_after - h.shares_before)


def test_bjtm_single_row_lti_program():
    """LK/15072026/0006/1 — director UMI RODIYAH, single LTI-program purchase.
    Regression fixture for the field-reordering case: 'Hak Suara Setelah
    Transaksi' landed on a separate page/line block from the other header
    fields in this filing's PDF layout."""
    parsed = parse_disclosure_pdf(_load("disclosure_bjtm_20260715.pdf"))
    h = parsed.header

    assert h.reporter_name == "UMI RODIYAH"
    assert h.is_insider is True
    assert h.position == "Direksi"
    assert h.citizenship == "WNI"
    assert h.shares_before == 2_170_300.0
    assert h.voting_rights_before == pytest.approx(0.000145)
    assert h.shares_after == 2_466_900.0
    # the field that got reordered across pages in this real filing:
    assert h.voting_rights_after == pytest.approx(0.000164)
    assert h.is_controller is False

    assert len(parsed.transactions) == 1
    t = parsed.transactions[0]
    assert t.transaction_type == "Pembelian"
    assert t.direction == "buy"
    assert t.shares_qty == 296_600.0
    assert t.share_class == "Saham Biasa"
    assert t.price == 510.0
    assert t.transaction_date == date(2026, 7, 14)
    assert t.purpose == "Program LTI"   # 2-word purpose, wraps across lines
