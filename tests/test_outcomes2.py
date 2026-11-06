"""vg.outcomes2: the pure pieces of outcome v2, on the real cases that motivated it (audit 2026-11-07)."""

import pytest

from vg import outcomes2 as o2


def test_an_ordinary_day_has_no_factor():
    assert o2.day_factor(3010.0, 3010.0) == 1.0
    assert o2.day_factor(3010.0, 3000.0) == 1.0          # 0.3%: a rounding difference, not a corporate action


def test_the_ex_rights_day_uses_the_theoretical_price_idx_set():
    # FORU, 22-Sep-2026: cum-rights close 2,560, IDX reference price 131 (theoretical ex-rights price).
    f = o2.day_factor(131.0, 2560.0)
    assert f == pytest.approx(131 / 2560)
    # Bought at 2,560 the day before, 236 two days later: on the theoretical basis that is a GAIN, not the -90.8% raw move.
    assert o2.adjusted_return(2560.0, 236.0, [f, 1.0]) == pytest.approx(236 / 131 - 1)
    assert o2.adjusted_return(2560.0, 236.0, [f, 1.0]) > 0


def test_a_real_split_and_a_placement_that_idx_did_not_price():
    # PBSA 1:2 split, 14-Oct-2026: reference price 845 -> 422.
    assert o2.day_factor(422.0, 845.0) == pytest.approx(422 / 845)
    # BUVA, 22-Oct-2026: listed shares x1.141 but the reference price stayed 322 -> no factor (v1 applied x1.141).
    assert o2.day_factor(322.0, 322.0) == 1.0


def test_cash_dividend_factor_multiplies_in():
    assert o2.day_factor(1000.0, 1000.0, 0.97) == pytest.approx(0.97)
    assert o2.adjusted_return(1000.0, 970.0, [0.97]) == pytest.approx(0.0)       # the drop by the dividend is not a loss


def test_entry_needs_a_real_trade_on_the_entry_day():
    assert o2.entry_vwap(1_000_000.0, 1000.0, 990.0, 1010.0) == pytest.approx(1000.0)
    assert o2.entry_vwap(0.0, 0.0, 990.0, 1010.0) is None                         # nobody traded: no entry, not a guess
    assert o2.entry_vwap(1_500_000.0, 1000.0, 990.0, 1010.0) is None              # VWAP 1,500 outside 990-1,010: bad bar


def test_missing_prices_give_none():
    assert o2.adjusted_return(None, 100.0, []) is None
    assert o2.adjusted_return(100.0, None, []) is None


def test_the_sql_uses_only_idx_reference_prices_and_one_formula_for_tags_and_benchmark():
    # Tier-3 fallback bars carry a prev adjusted for LATER corporate actions (FORU 14-Sep: 190.36); they must never be a factor.
    assert "source_tier = 0" in o2._GRID
    # Tag rows and the benchmark are both built from this one _RETURNS query (VWAP of t+1 for every horizon, close-entry variant).
    assert o2._RETURNS.count("e.vwap") == 4 and "rc5" in o2._RETURNS
