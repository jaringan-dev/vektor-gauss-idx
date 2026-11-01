"""Adjustment factors (adjust.py) — pure rules, no DB."""

from datetime import date

import pytest

from vg import adjust

LAST = date(2026, 10, 30)


def _row(tk, ex, typ, ratio=None, exr=None, prc=None, notes=None, src="ksei_ca_schedule", prev=1000.0):
    from datetime import timedelta
    return (tk, ex, typ, ratio, exr, prc, notes, src, prev, ex - timedelta(days=1))


def test_cash_dividend_factor_is_one_minus_yield():
    f, why = adjust.dividend_factor("50", "1", 1000.0)
    assert why == "" and f == pytest.approx(0.95)


def test_dividend_with_non_unit_exercise_ratio():
    f, _ = adjust.dividend_factor("100", "2", 1000.0)          # 100 per 2 shares = 50/share
    assert f == pytest.approx(0.95)


@pytest.mark.parametrize("proceed,prev,reason", [
    ("5043837268346872", 1000.0, "dps_implausible"),          # the garbage values found in corp_actions
    ("0", 1000.0, "dps_nonpositive"),
    ("abc", 1000.0, "dps_unparseable"),
    ("50", None, "no_price"),
])
def test_bad_dividends_are_skipped_with_a_reason_not_adjusted(proceed, prev, reason):
    f, why = adjust.dividend_factor(proceed, "1", prev)
    assert f is None and why == reason


def test_decimal_comma_and_dot():
    assert adjust.dividend_factor("0,1504", "1", 1000.0)[0] == pytest.approx(1 - 0.0001504)


def test_bonus_and_split_factors():
    assert adjust.bonus_factor("6", "1") == pytest.approx(6 / 7)
    assert adjust.bonus_factor("100", "30") == pytest.approx(100 / 130)
    assert adjust.bonus_factor("x", "1") is None
    assert adjust.split_factor(5.0) == pytest.approx(0.2)
    assert adjust.split_factor(0) is None


def test_build_events_kinds_and_skips():
    rows = [
        _row("AAA", date(2026, 5, 4), "cash_dividend", exr="1", prc="50"),
        _row("BBB", date(2026, 4, 20), "stock_dividend", exr="6", prc="1"),
        _row("CCC", date(2026, 10, 12), "rights", exr="7", prc="5", notes="Data CA atas Right Issue CCC dengan rasio"),
        _row("DDD", date(2026, 11, 4), "rights", exr="6", prc="4", notes="Rasio pembagian saham bonus adalah 6 lama : 4 baru"),
        _row("EEE", date(2026, 3, 2), "share_count_change", ratio=5.0, notes="split: listed_shares 1 -> 5", src="derived"),
        _row("FFF", date(2026, 3, 3), "share_count_change", ratio=1.3, notes="dilution_or_placement: x", src="derived"),
        _row("HHH", date(2026, 3, 4), "share_count_change", ratio=1.1, notes="split: looks like a split but is a placement", src="derived"),
        _row("GGG", date(2026, 12, 1), "cash_dividend", exr="1", prc="50"),                 # in the future
    ]
    ev, skipped = adjust.build_events(rows, LAST)
    kinds = {(e.ticker, e.kind) for e in ev}
    assert kinds == {("AAA", "dividend"), ("BBB", "bonus"), ("EEE", "split")}
    assert skipped == {"rights_price_unknown": 1, "future_ex_date": 2, "dilution_not_adjusted": 1,
                       "split_ambiguous_placement": 1}


def test_reverse_split_is_adjusted_and_ksei_events_use_the_previous_trading_day():
    rows = [_row("RRR", date(2026, 2, 2), "share_count_change", ratio=0.2, notes="split: reverse", src="derived"),
            _row("AAA", date(2026, 5, 4), "cash_dividend", exr="1", prc="50")]
    ev = {e.ticker: e for e in adjust.build_events(rows, LAST)[0]}
    assert ev["RRR"].factor == pytest.approx(5.0) and ev["RRR"].ex_date == date(2026, 2, 2)       # own data: date as-is
    assert ev["AAA"].ex_date == date(2026, 5, 3)                                                    # KSEI date is 1 bar late


def test_bonus_in_rights_row_is_adjusted_once_it_has_happened():
    rows = [_row("PPGL", date(2026, 10, 1), "rights", exr="6", prc="4", notes="saham bonus adalah setiap 6 lama 4 baru")]
    ev, _ = adjust.build_events(rows, LAST)
    assert [(e.kind, round(e.factor, 4)) for e in ev] == [("bonus", 0.6)]


def test_derived_split_is_dropped_when_ksei_reports_the_same_bonus():
    rows = [
        _row("SPMA", date(2026, 7, 10), "stock_dividend", exr="100", prc="30"),
        _row("SPMA", date(2026, 7, 12), "share_count_change", ratio=1.3, notes="split: x", src="derived"),
        _row("OTH", date(2026, 7, 12), "share_count_change", ratio=2.0, notes="split: x", src="derived"),
    ]
    ev, skipped = adjust.build_events(rows, LAST)
    assert {(e.ticker, e.kind) for e in ev} == {("SPMA", "bonus"), ("OTH", "split")}
    assert skipped["split_duplicates_bonus"] == 1                  # not double-adjusted
