"""Daily BPI under the candles (charts.bpi_bars, bpi_window.daily_bpi): pure, no database."""

import re
from datetime import date, timedelta

from vg.web import bpi_window, charts

DAYS = [date(2026, 10, 1) + timedelta(days=i) for i in range(12)]


def _candle_xs(svg: str) -> list[float]:
    """Centre x of every candle body (the candles chart draws one <rect> per day inside a <g> with a <title> of the date)."""
    return [round(float(x) + float(w) / 2, 1) for x, w in re.findall(r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', svg)]


def test_each_bar_sits_under_its_candle():
    bars = [{"d": d, "o": 100.0, "h": 105.0, "l": 95.0, "c": 102.0, "v": 1000.0} for d in DAYS]
    pts = [12.0, -15.0, 3.0, None, 25.0, -2.0, 11.0, 30.0, -20.0, 5.0, 14.0, 9.0]
    svg = charts.bpi_bars(DAYS, pts)
    xs, _step = charts._x_positions(len(DAYS))
    centres = _candle_xs(svg)
    assert len(centres) == 11                                         # the None day has no bar
    want = [round(x, 1) for x, p in zip(xs, pts) if p is not None]
    assert all(abs(a - b) <= 0.15 for a, b in zip(centres, want))     # same x as the candle of that day
    assert charts.candles(bars) is not None and len(charts._x_positions(len(bars))[0]) == len(DAYS)


def test_colours_follow_the_gauge_bands_and_the_description_counts():
    pts = [12.0, -15.0, 3.0, 25.0, -2.0]
    svg = charts.bpi_bars(DAYS[:5], pts)
    assert svg.count('fill="var(--pos)"') == 2 and svg.count('fill="var(--neg)"') == 1 and svg.count('fill="var(--muted)" opacity=".8"') == 2
    assert "above +10 points on 2 days, below -10 on 1" in svg


def test_too_few_values_or_misaligned_input_gives_no_chart():
    assert charts.bpi_bars(DAYS[:5], [1.0, None, None, None, 2.0]) is None
    assert charts.bpi_bars(DAYS[:5], [1.0] * 4) is None


def test_daily_bpi_uses_the_engine_formula_and_skips_days_without_a_ladder():
    d0, d1 = DAYS[0], DAYS[1]
    levels = [(d0, "B", "YP", 40.0), (d0, "B", "AA", 30.0), (d0, "B", "BB", 20.0), (d0, "B", "CC", 10.0),
              *[(d0, "S", f"S{i}", 10.0) for i in range(10)],
              (d1, "B", "YP", 50.0)]                                   # no sell ladder on d1
    out = bpi_window.daily_bpi(levels, [(d0, 100.0), (d1, 50.0)])
    assert set(out) == {d0} and abs(out[d0] - (0.9 - 0.3)) < 1e-9
