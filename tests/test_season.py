"""Offline tests for the seasonality pure functions (no DB)."""

from __future__ import annotations

from datetime import date

from vg.season import _stats, _to_monthly, day_of_week, month_of_year


def test_stats():
    assert _stats([]) == (0, None, None)
    n, up, mean = _stats([0.01, -0.02, 0.03])
    assert n == 3
    assert abs(up - 2 / 3) < 1e-9
    assert abs(mean - 0.02 / 3) < 1e-9


def test_day_of_week_buckets():
    # 2026-01-05 Mon, 01-06 Tue, 01-12 Mon
    daily = [(date(2026, 1, 5), 0.01), (date(2026, 1, 6), -0.01), (date(2026, 1, 12), 0.02)]
    out = {r["label"]: r for r in day_of_week(daily)}
    assert out["Mon"]["n"] == 2
    assert out["Tue"]["n"] == 1
    assert abs(out["Mon"]["pct_up"] - 1.0) < 1e-9
    assert out["Wed"]["n"] == 0 and out["Wed"]["mean_ret"] is None


def test_to_monthly_drops_partial_ends():
    daily = [(date(2026, m, day), 0.05) for m in (1, 2, 3) for day in (10, 20)]
    mm = _to_monthly(daily)
    assert len(mm) == 1                       # Jan & Mar (partial) dropped
    assert mm[0][0].month == 2
    assert abs(mm[0][1] - (1.05 * 1.05 - 1)) < 1e-9


def test_month_of_year_shape():
    out = month_of_year([(date(2024, 7, 31), 0.03), (date(2025, 7, 31), -0.01)])
    assert len(out) == 12
    jul = next(r for r in out if r["label"] == "Jul")
    assert jul["n"] == 2
    assert abs(jul["pct_up"] - 0.5) < 1e-9
