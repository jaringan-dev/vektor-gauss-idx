"""Backtest of the mechanical stop/target rule (vg.risk_eval): replay rules and aggregation."""

from datetime import date

import pytest

from vg import risk, risk_eval
from vg.risk_eval import Bar, Sig


def B(o, h, l, c):
    return Bar(o, h, l, c)


# ---- simulate: the replay rules -------------------------------------------------------------

def test_target_hit_exits_at_the_target():
    assert risk_eval.simulate(100, 90, 120, [B(100, 105, 99, 104), B(104, 125, 103, 120)]) == ("target", 120)


def test_stop_hit_exits_at_the_stop():
    assert risk_eval.simulate(100, 90, 120, [B(100, 101, 89, 92)]) == ("stop", 90)


def test_when_both_levels_are_inside_one_bar_the_stop_is_assumed_first():
    assert risk_eval.simulate(100, 90, 120, [B(100, 130, 85, 100)]) == ("stop", 90)


def test_a_gap_down_through_the_stop_exits_at_the_worse_open():
    assert risk_eval.simulate(100, 90, 120, [B(80, 85, 78, 82)]) == ("stop", 80)


def test_a_gap_up_through_the_target_exits_at_the_open():
    assert risk_eval.simulate(100, 90, 120, [B(125, 130, 124, 128)]) == ("target", 125)


def test_no_hit_exits_at_the_last_close():
    assert risk_eval.simulate(100, 90, 120, [B(100, 105, 95, 101), B(101, 106, 96, 103)]) == ("time", 103)


def test_missing_open_skips_only_the_gap_check():
    assert risk_eval.simulate(100, 90, 120, [B(None, 101, 89, 92)]) == ("stop", 90)


# ---- evaluate: aggregation --------------------------------------------------------------------

D = date(2026, 10, 1)


def _flat(n, price=100.0):
    return [B(price, price + 1, price - 1, price)] * n


def _sig(tag="T", liquid=True):
    return Sig(D, "AAAA", tag, liquid)


def test_a_flat_stock_never_hits_anything_and_loses_the_cost():
    # ATR 5 -> stop 90; a flat series stays far from 90 and from the 1R target 110
    out = risk_eval.evaluate([(_sig(), 100.0, 5.0, _flat(10))])
    g = out[("T", "all")]
    s = g.by_r[1.0].row()
    assert g.n == 1 and s["time_pct"] == 1.0 and s["stop_pct"] == 0 and s["target_pct"] == 0
    assert s["net"] == pytest.approx(-risk_eval.ROUND_TRIP_COST) and s["win"] == 0
    assert g.hold_net_sum == pytest.approx(-risk_eval.ROUND_TRIP_COST)


def test_a_winner_hits_each_target_and_the_net_includes_costs():
    bars = _flat(2) + [B(100, 140, 99, 138)] + _flat(7, 138)
    g = risk_eval.evaluate([(_sig(), 100.0, 5.0, bars)])[("T", "all")]
    plan = risk.build_plan(100.0, 5.0, r_multiples=risk_eval.TARGET_R)
    for t in plan.targets:
        s = g.by_r[t.r].row()
        assert s["target_pct"] == 1.0
        assert s["net"] == pytest.approx(t.price / 100 - 1 - risk_eval.ROUND_TRIP_COST)
        assert s["exp_r"] == pytest.approx(t.r, rel=0.01)


def test_liquid_signals_also_feed_the_liquid_scope_and_illiquid_do_not():
    out = risk_eval.evaluate([(_sig("A", True), 100.0, 5.0, _flat(10)), (_sig("A", False), 100.0, 5.0, _flat(10))])
    assert out[("A", "all")].n == 2 and out[("A", "liquid")].n == 1


def test_unmatured_and_unplannable_signals_are_skipped_not_guessed():
    out = risk_eval.evaluate([
        (_sig("short"), 100.0, 5.0, _flat(9)),            # fewer than HORIZON bars after the signal
        (_sig("noatr"), 100.0, 0.0, _flat(10)),           # no ATR
        (_sig("zero"), 100.0, 60.0, _flat(10)),           # stop would be at or below zero
    ])
    assert out == {}


def test_table_puts_the_baseline_first_then_larger_groups():
    sigs = [(_sig("small"), 100.0, 5.0, _flat(10))] + [(_sig("big"), 100.0, 5.0, _flat(10))] * 3 \
        + [(_sig(risk_eval.BASELINE), 100.0, 5.0, _flat(10))]
    rows = risk_eval.table(risk_eval.evaluate(sigs), "all")
    assert [r["tag"] for r in rows] == [risk_eval.BASELINE, "big", "small"]
    assert [r["tag"] for r in risk_eval.table(risk_eval.evaluate(sigs), "all", min_n=2)] == ["big"]


def test_cost_assumption_matches_the_forward_test_gate():
    from vg.web import status

    assert risk_eval.ROUND_TRIP_COST == status.ROUND_TRIP_COST


def test_text_table_renders_every_row():
    rows = risk_eval.table(risk_eval.evaluate([(_sig(), 100.0, 5.0, _flat(10))]), "all")
    text = risk_eval.format_text(rows, (D, D))
    assert "1R" in text and "2R" in text and "3R" in text and "T " in text and "cost 1.7%" in text
