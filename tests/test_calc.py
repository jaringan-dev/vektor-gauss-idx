"""Position-sizing calculator tests (pure)."""

from __future__ import annotations

import math

import pytest

from vg import calc


def test_average_entry():
    assert calc.average_entry([3000, 3100, 3200]) == pytest.approx(3100.0)
    with pytest.raises(ValueError):
        calc.average_entry([0, -5])


def test_atr_stop():
    assert calc.atr_stop(3390.0, 105.0, 2.0) == 3180.0


def test_plan_target_r_and_rr():
    p = calc.plan(1000.0, 900.0, target_r=2.5, fee_pct=0.0)
    assert p.avg_entry == 1000.0
    assert p.stop_loss == 900.0
    assert p.target == 1250.0                 # 1000 + 2.5*(1000-900)
    assert p.rr == pytest.approx(2.5)
    assert p.risk_per_share == 100.0          # no fee


def test_plan_fee_drag_raises_risk():
    p = calc.plan(1000.0, 900.0, target=1200.0, fee_pct=0.003)
    # (1000-900) + 0.003*(1000+900) = 100 + 5.7
    assert p.risk_per_share == pytest.approx(105.7)


def test_plan_sizing():
    # balance 100M, risk 1% -> risk budget 1,000,000. risk/share (no fee) = 100.
    # raw shares = 10,000 -> 100 lots.
    p = calc.plan(1000.0, 900.0, target_r=2.0, balance=100_000_000, risk_pct=0.01, fee_pct=0.0)
    assert p.lots == 100
    assert p.shares == 10_000
    assert p.position_idr == 10_000_000
    assert p.risk_pct_of_balance == pytest.approx(0.01, abs=1e-6)


def test_plan_sizing_rounds_down_to_lot():
    p = calc.plan(1234.0, 1150.0, target_r=2.0, balance=5_000_000, risk_pct=0.01, fee_pct=0.003)
    assert p.lots == math.floor((5_000_000 * 0.01 / p.risk_per_share) / 100)
    assert p.shares == p.lots * 100


def test_plan_rejects_sl_above_entry():
    with pytest.raises(ValueError):
        calc.plan(1000.0, 1100.0, target_r=2.0)


def test_plan_no_balance_no_sizing():
    p = calc.plan(1000.0, 950.0, target_r=2.0, balance=0.0)
    assert p.lots is None and p.position_idr is None
    assert p.rr is not None
