"""K10–K13 research maths (vg.research): bootstrap, FDR, folds/regimes, cost model, verdict. Pure, synthetic data."""

import random
from datetime import date, timedelta

import pytest

from vg import research as rs


def _days(n, start=date(2023, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---- K10 ------------------------------------------------------------------------------------

def test_bootstrap_is_deterministic_and_centred_on_the_point_estimate():
    rng = random.Random(3)
    s, n = [2.0 + rng.gauss(0, 1.0) for _ in range(100)], [1.0] * 100
    a = rs.block_bootstrap(s, n, reps=200, seed=1)
    assert a == rs.block_bootstrap(s, n, reps=200, seed=1) and a != rs.block_bootstrap(s, n, reps=200, seed=2)
    assert sum(a) / len(a) == pytest.approx(sum(s) / 100, abs=0.15)


def test_interval_contains_the_truth_for_iid_days_and_p_value_flags_a_real_effect():
    rng = random.Random(5)
    n = [3.0] * 400
    s = [3 * (0.02 + rng.gauss(0, 0.01)) for _ in n]                    # true mean edge +2%
    r = rs.interval(s, n, reps=400)
    assert r["lo"] < 0.02 < r["hi"] and r["lo"] > 0 and r["p"] < 0.01
    flat = rs.interval([3 * rng.gauss(0, 0.01) for _ in n], n, reps=400)
    assert flat["lo"] < 0 < flat["hi"] and flat["p"] > 0.05


def test_clustered_days_widen_the_interval_versus_independent_days():
    """Same number of signals and the same average edge: when whole days share a common shock the interval must be wider."""
    rng = random.Random(9)
    T = 300
    indep = [sum(rng.gauss(0, 0.02) for _ in range(10)) + 10 * 0.005 for _ in range(T)]
    shocks = [rng.gauss(0, 0.02) for _ in range(T)]
    clustered = [10 * (0.005 + sh) for sh in shocks]                 # every signal that day moves together
    counts = [10.0] * T
    wi = rs.interval(indep, counts, reps=300)
    wc = rs.interval(clustered, counts, reps=300)
    assert (wc["hi"] - wc["lo"]) > 2 * (wi["hi"] - wi["lo"])


def test_empty_or_signal_free_input_is_handled():
    assert rs.block_bootstrap([], []) == [] and rs.block_bootstrap([0.0] * 5, [0.0] * 5) == []
    assert rs.interval([0.0] * 5, [0.0] * 5)["point"] is None


def test_block_bootstrap_handles_wraparound_blocks():
    out = rs.block_bootstrap([1.0, 1.0, 1.0], [1.0, 1.0, 1.0], block=5, reps=50, seed=3)
    assert out and all(x == pytest.approx(1.0) for x in out)


def test_benjamini_hochberg_matches_a_hand_computation_and_keeps_none():
    q = rs.bh_qvalues([0.01, 0.04, 0.03, None, 0.20])
    # sorted p: .01 .03 .04 .20 (m=4) -> raw .04 .06 .0533 .20 -> monotone from the top: .04 .0533 .0533 .20
    assert q[0] == pytest.approx(0.04) and q[2] == pytest.approx(0.0533, abs=1e-3) and q[1] == pytest.approx(0.0533, abs=1e-3)
    assert q[3] is None and q[4] == pytest.approx(0.20)
    assert all(x is None or x >= p for x, p in zip(q, [0.01, 0.04, 0.03, None, 0.20]))


def test_effective_blocks_counts_signal_days_not_signals():
    assert rs.effective_blocks([100.0, 0, 0, 50.0, 0, 0, 0, 0, 0, 1.0]) == pytest.approx(3 / 5)


# ---- K12 ------------------------------------------------------------------------------------

def test_fold_labels_and_purge_drop_the_end_of_every_fold():
    days = [date(2023, 6, 1) + timedelta(days=i) for i in range(0, 120)]
    labels = rs.fold_labels(days)
    assert labels[0] == "2023H1" and labels[-1] == "2023H2"
    keep = rs.purge_mask(labels, embargo=10)
    last_h1 = max(i for i, l in enumerate(labels) if l == "2023H1")
    assert keep[last_h1 - 10] and not keep[last_h1 - 9] and not keep[last_h1] and keep[last_h1 + 1]
    assert not keep[-1] and not keep[-10] and keep[-11]


def test_fold_edges_use_only_kept_days_and_hide_thin_folds():
    days = _days(300)
    n = [5.0] * 300
    s = [0.01 * 5] * 300
    folds = rs.fold_edges(days, s, n, min_n=30)
    assert all(f["edge"] == pytest.approx(0.01) for f in folds if f["edge"] is not None)
    assert sum(f["n"] for f in folds) < sum(n)                       # purged days were left out
    thin = rs.fold_edges(days, s, [0.0] * 295 + [1.0] * 5, min_n=30)
    assert all(f["edge"] is None for f in thin)


def test_regimes_follow_the_index_trend():
    days = _days(200)
    close = {d: 100.0 * (1.004 ** i) for i, d in enumerate(days[:100])}
    close.update({d: close[days[99]] * (0.996 ** (i - 99)) for i, d in enumerate(days) if i >= 100})
    lab = rs.regime_labels(days, close)
    assert lab[30] == "flat" and lab[90] == "up" and lab[190] == "down"          # first 60 days have no history -> flat
    e = rs.regime_edges(lab, [0.02] * 200, [1.0] * 200)
    assert e["up"]["edge"] == pytest.approx(0.02) and e["up"]["n"] > 0 and e["down"]["n"] > 0


# ---- K13 ------------------------------------------------------------------------------------

@pytest.mark.parametrize("price,tick", [(150, 1), (300, 2), (1500, 5), (4000, 10), (8000, 25)])
def test_tick_sizes(price, tick):
    assert rs.tick_size(price) == tick


def test_cost_rises_when_the_stock_is_thinner_or_more_volatile_and_is_capped():
    liquid = rs.round_trip_cost(4840, 1_000e9, 120)
    thin = rs.round_trip_cost(4840, 2e9, 120)
    wild = rs.round_trip_cost(4840, 2e9, 600)
    assert 0.003 < liquid < thin < wild <= rs.COST_CAP
    assert rs.round_trip_cost(50, 1e6, 5) == rs.COST_CAP                   # a hopeless name hits the cap
    assert rs.round_trip_cost(4840, 1e9, None) > rs.FEE                     # missing ATR falls back to a 3% volatility


def test_liquid_large_caps_are_cheaper_than_the_flat_assumption_and_illiquid_ones_dearer():
    assert rs.round_trip_cost(4840, 1_000e9, 120) < 0.017 < rs.round_trip_cost(300, 1e9, 15)


# ---- verdict ----------------------------------------------------------------------------------

def _res(**over):
    base = {"n": 5000, "days": 300, "edge_raw": {"lo": -0.01}, "edge_neut": {"lo": -0.01}, "edge_net": {"lo": -0.01},
            "q_net": 0.5, "folds_pos": 0.4}
    base.update(over)
    return base


def test_strength_ladder():
    assert rs.strength(_res(n=50)) == "Too little data" and rs.strength(_res(days=10)) == "Too little data"
    assert rs.strength(_res()) == "No evidence of an edge"
    assert rs.strength(_res(edge_raw={"lo": 0.002})) == "Mostly a size or liquidity effect"
    assert rs.strength(_res(edge_raw={"lo": 0.002}, edge_neut={"lo": 0.001})) == "Real but too small to pay the costs"
    assert rs.strength(_res(edge_neut={"lo": 0.001}, edge_net={"lo": 0.001}, q_net=0.03, folds_pos=0.8)).startswith("Candidate")
    assert rs.strength(_res(edge_neut={"lo": 0.001}, edge_net={"lo": 0.001}, q_net=0.03, folds_pos=0.5)) == "Real but too small to pay the costs"
    assert rs.strength(_res(edge_neut={"lo": 0.001}, edge_net={"lo": 0.001}, q_net=0.5, folds_pos=0.9)) == "Real but too small to pay the costs"


def _tag(rng, mean_edge, cost, days=700, per_day=6, noise=0.02):
    n = [float(per_day)] * days
    raw = [per_day * (mean_edge + rng.gauss(0, noise / 3)) for _ in range(days)]
    return {"n": n, "raw": raw, "neut": list(raw), "cost": [per_day * cost] * days}


def test_analyze_separates_a_real_edge_from_noise_and_applies_fdr():
    rng = random.Random(11)
    days = _days(700)
    per = {"REAL": _tag(rng, 0.03, 0.005), "NOISE": _tag(rng, 0.0, 0.005), "COSTLY": _tag(rng, 0.004, 0.017)}
    out = rs.analyze(days, per, {}, reps=300)
    assert out["REAL"]["strength"].startswith("Candidate") and out["REAL"]["q_net"] < 0.05 and out["REAL"]["folds_pos"] == 1.0
    assert out["NOISE"]["strength"] == "No evidence of an edge"
    assert out["COSTLY"]["strength"] in ("Real but too small to pay the costs", "No evidence of an edge") and out["COSTLY"]["edge_net"]["point"] < 0
    assert out["REAL"]["mean_cost"] == pytest.approx(0.005) and out["REAL"]["eff_blocks"] == pytest.approx(700 / 5)


def test_analyze_skips_patterns_with_no_signals_and_arrays_respect_scope_and_window():
    days = _days(30)
    rows = [("A", days[2], True, 2, 0.02, 0.01, 0.006), ("A", days[3], False, 3, 0.03, 0.02, 0.01), ("A", days[20], True, 1, 0.0, 0.0, 0.003)]
    d_all, per = rs.arrays(days, rows, scope="all")
    assert per["A"]["n"][2] == 2 and per["A"]["n"][3] == 3 and sum(per["A"]["n"]) == 6
    d_liq, per_l = rs.arrays(days, rows, scope="liquid")
    assert per_l["A"]["n"][3] == 0 and sum(per_l["A"]["n"]) == 3
    d_since, per_s = rs.arrays(days, rows, scope="all", since=days[10])
    assert len(d_since) == 20 and sum(per_s["A"]["n"]) == 1
    assert rs.analyze(days, {"EMPTY": {"n": [0.0] * 30, "raw": [0.0] * 30, "neut": [0.0] * 30, "cost": [0.0] * 30}}, {}, reps=10) == {}


def test_text_report_lists_every_pattern():
    rng = random.Random(1)
    days = _days(400)
    res = {"window": ["a", "b"], "results": {"all": {"full": rs.analyze(days, {"X": _tag(rng, 0.0, 0.01, days=400)}, {}, reps=100)}}}
    text = rs.format_text(res)
    assert "X " in text and "No evidence of an edge" in text and "neutral" in text
