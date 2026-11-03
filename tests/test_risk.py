"""Mechanical risk plan arithmetic (vg.risk): tick sizes, ATR stop, R targets, lot sizing."""

import pytest

from vg import risk


@pytest.mark.parametrize("price,tick", [(50, 1), (199, 1), (200, 2), (499, 2), (500, 5), (1999, 5), (2000, 10),
                                         (4999, 10), (5000, 25), (9800, 25)])
def test_idx_tick_size_bands(price, tick):
    assert risk.tick_size(price) == tick


def test_rounding_respects_the_tick_of_the_price_band():
    assert risk.round_down(4837) == 4830
    assert risk.round_down(5012) == 5000
    assert risk.round_nearest(5013) == 5025
    assert risk.round_nearest(4836) == 4840


def test_plan_for_a_normal_stock():
    plan = risk.build_plan(4840, 120)
    assert plan.stop == 4600 and plan.risk_per_share == 240
    assert round(plan.risk_pct, 4) == round(240 / 4840, 4)
    assert [(t.r, t.price) for t in plan.targets] == [(1.0, 5075), (2.0, 5325), (3.0, 5550)]   # prices >= 5,000 use a 25 tick
    assert plan.warnings == ()


def test_stop_is_rounded_down_so_the_risk_is_never_understated():
    plan = risk.build_plan(1000, 33)               # raw stop 934 -> tick 5 -> 930
    assert plan.stop == 930 and plan.stop <= 1000 - 2 * 33


def test_wide_stop_is_flagged():
    plan = risk.build_plan(1000, 100)               # stop 800 = 20% below
    assert plan.stop == 800 and plan.warnings and "very volatile" in plan.warnings[0]


@pytest.mark.parametrize("ref,atr", [(None, 10), (1000, None), (0, 10), (1000, 0), (1000, -5), (100, 80)])
def test_no_plan_when_inputs_are_missing_or_the_stop_would_be_at_or_below_zero(ref, atr):
    assert risk.build_plan(ref, atr) is None


def test_lot_size_caps_the_loss_at_the_chosen_share_of_capital():
    plan = risk.build_plan(4840, 120)
    lots = risk.lot_size(100_000_000, 0.01, plan.ref, plan.stop)
    assert lots.lots == 37 and lots.shares == 3700          # per-share loss = 240 + 0.3% x (4840 + 4600)
    assert lots.max_loss <= 100_000_000 * 0.01                  # fees included
    assert not lots.capped_by_balance


def test_lot_size_is_capped_by_what_the_capital_can_buy():
    lots = risk.lot_size(10_000_000, 0.05, 4840, 4800)          # tiny stop distance -> risk allows a huge size
    assert lots.capped_by_balance and lots.position_value <= 10_000_000


@pytest.mark.parametrize("balance,rf,ref,stop", [(0, 0.01, 100, 90), (1e6, 0, 100, 90), (1e6, 0.01, 100, 100),
                                                  (1e6, 0.01, 100, 120), (1e6, 0.01, 0, 0)])
def test_lot_size_rejects_impossible_inputs(balance, rf, ref, stop):
    assert risk.lot_size(balance, rf, ref, stop) is None
