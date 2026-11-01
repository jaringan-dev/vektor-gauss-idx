"""K5: repair rules for adjusted fallback prices and the ingest-time `open` guard."""

import pytest

from vg import repair
from vg.collect import _sane_open
from vg.sources.base import PriceBar


def test_adjusted_close_is_rescaled_to_the_raw_close_implied_by_next_prev():
    # PBSA-style: the fallback feed says 372.06 (adjusted); the next IDX day's prev - the RAW previous close - says 375.
    fix = repair.plan_row_fix(close=372.06, open_=370.0, high=380.0, low=368.0, next_prev=375.0)
    assert fix.close == 375.0
    f = 375.0 / 372.06
    assert fix.high == round(380.0 * f) and fix.low == round(368.0 * f) and fix.open == round(370.0 * f)
    assert fix.note.startswith("repaired")


def test_row_already_raw_is_left_alone():
    assert repair.plan_row_fix(close=216.0, open_=215.0, high=220.0, low=214.0, next_prev=216.0) is None
    assert repair.plan_row_fix(close=216.0, open_=215.0, high=220.0, low=214.0, next_prev=216.5) is None   # within 0.5%


def test_non_integer_close_without_reference_is_flagged_not_guessed():
    fix = repair.plan_row_fix(close=215.95, open_=None, high=220.0, low=210.0, next_prev=None)
    assert fix.note.startswith("flagged") and fix.close == 215.95
    assert repair.plan_row_fix(close=216.0, open_=None, high=220.0, low=210.0, next_prev=None) is None


def _bar(**kw):
    base = dict(ticker="AAA", name=None, prev=100, open=100, first_trade=100, high=105, low=98, close=102,
                change=2, volume=10, value=1000, frequency=1)
    base.update(kw)
    return PriceBar(**base)


@pytest.mark.parametrize("o,expected", [(100, 100), (98, 98), (105, 105), (106, None), (97, None)])
def test_open_outside_the_day_range_becomes_null(o, expected):
    assert _sane_open(_bar(open=o)) == expected


def test_untraded_rows_keep_their_open():
    assert _sane_open(_bar(open=50, low=0, high=0, volume=0)) == 50
    assert _sane_open(_bar(open=None)) is None


def test_inferred_factor_needs_agreeing_evidence():
    assert repair.infer_factor([1.237, 1.2371]) == pytest.approx(1.23705)
    assert repair.infer_factor([1.237, 1.30]) is None          # the vendor factor changed between the days: do not guess
    assert repair.infer_factor([]) is None


def test_rescale_rounds_to_integer_prices():
    fix = repair.rescale(237.95, 240.0, 245.0, 230.0, 17.7, "repaired: x")
    assert fix.close == round(237.95 * 17.7) and fix.high == round(245.0 * 17.7) and fix.low == round(230.0 * 17.7)
