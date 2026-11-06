"""Server-side SVG charts (vg.web.charts): well-formed, honest about missing data, accessible, escaped."""

import xml.etree.ElementTree as ET
from datetime import date, timedelta

import pytest

from vg.web import charts


def _days(n, start=date(2026, 7, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bars(n=40):
    bars = []
    for i, d in enumerate(_days(n)):
        c = 100 + i * 0.5
        bars.append({"d": d, "o": c - 0.3, "h": c + 1, "l": c - 1, "c": c, "v": 1_000_000 + i * 1000})
    return bars


def _parse(svg: str):
    return ET.fromstring(svg)


def test_candles_is_well_formed_and_accessible():
    svg = charts.candles(_bars(), levels=[{"price": 95.0, "label": "Stop", "kind": "stop"},
                                          {"price": 125.0, "label": "1R", "kind": "target"}],
                         tag_counts={_days(40)[3]: 2})
    root = _parse(svg)
    assert root.tag.endswith("svg") and root.get("role") == "img"
    ids = {e.get("id") for e in root.iter() if e.get("id")}
    assert {"price-t", "price-d"} <= ids and root.get("aria-labelledby") == "price-t price-d"
    assert svg.count("<line") > 40 and "Stop 95" in svg and "1R 125" in svg      # candles + the risk levels
    assert "2 patterns flagged" in svg and ">Patterns<" in svg


def test_candle_up_and_down_differ_by_fill_not_only_colour():
    up = [{"d": d, "o": 100.0, "h": 103.0, "l": 99.0, "c": 102.0, "v": 1} for d in _days(6)]
    dn = [{"d": d, "o": 102.0, "h": 103.0, "l": 99.0, "c": 100.0, "v": 1} for d in _days(6)]
    assert 'fill="none" stroke="var(--pos)"' in charts.candles(up)
    assert 'fill="var(--neg)" stroke="var(--neg)"' in charts.candles(dn)


def test_stop_always_inside_the_scale_but_far_targets_are_dropped():
    svg = charts.candles(_bars(), levels=[{"price": 60.0, "label": "Stop", "kind": "stop"},
                                          {"price": 400.0, "label": "3R", "kind": "target"}])
    assert "Stop 60" in svg and "3R 400" not in svg


def test_too_little_data_means_no_chart_instead_of_an_empty_frame():
    assert charts.candles(_bars(3)) is None
    assert charts.flow_bars(_days(3), [1.0, 2.0, 3.0]) is None
    assert charts.bounds_band(_days(2), [0.1, 0.1], [0.2, 0.2]) is None


def test_flow_bars_have_a_zero_line_cumulative_line_and_tooltips():
    vals = [1e9, -2e9, 3e9, None, 4e9, -1e9, 2e9]
    svg = charts.flow_bars(_days(7), vals)
    _parse(svg)
    assert "<polyline" in svg and "Cumulative net foreign flow: +7B" in svg
    assert "+3B" in svg or "3B" in svg
    assert svg.count("<rect") == 6                                  # the missing day is skipped, never drawn as zero


def test_bounds_band_colours_by_certainty_and_skips_missing_days():
    ds = _days(5)
    svg = charts.bounds_band(ds, [0.06, None, -0.09, -0.03, 0.01], [0.09, None, -0.06, 0.04, 0.02])
    _parse(svg)
    assert svg.count("<g>") == 4                                              # one group per day WITH data; the missing day is skipped
    assert "var(--pos)" in svg and "var(--neg)" in svg and "var(--muted)" in svg
    assert "between +6.0% and +9.0% of traded value" in svg
    assert "net buying is certain" in svg and "net selling is certain" in svg and "unclear" in svg       # the hover text says what the bar means
    assert "buying certain on 2 days · selling certain on 1 day · unclear on 1 day" in svg                # counts above the bars, singular handled


def test_text_is_escaped():
    svg = charts.candles(_bars(), levels=[{"price": 100.0, "label": "<b>Stop</b>", "kind": "stop"}])
    assert "<b>Stop</b>" not in svg and "&lt;b&gt;Stop" in svg
    _parse(svg)


def test_bounds_band_clips_outlier_days_but_keeps_their_real_values():
    ds = _days(12)
    lo = [0.01] * 11 + [0.50]
    hi = [0.03] * 11 + [0.56]
    svg = charts.bounds_band(ds, lo, hi)
    _parse(svg)
    assert "1 faint bar cut off at the edge" in svg
    assert "between +50.0% and +56.0% of traded value" in svg        # tooltip keeps the true range
    assert "+56%" not in svg                                           # the axis is not stretched to the outlier


def test_narrow_variant_fits_a_phone_and_keeps_ids_unique():
    wide = charts.candles(_bars())
    narrow = charts.candles(_bars(), width=charts.W_MOBILE, uid="price-m")
    assert 'viewBox="0 0 720 ' in wide and f'viewBox="0 0 {charts.W_MOBILE} ' in narrow
    assert 'id="price-t"' in wide and 'id="price-m-t"' in narrow and 'id="price-t"' not in narrow
    _parse(narrow)
    xs = [float(x) for x in __import__("re").findall(r'x2="([\d.]+)"', narrow)]
    assert max(xs) <= charts.W_MOBILE                                 # nothing is drawn beyond the narrow canvas


# ---- group_panels (net flow by broker group) ---------------------------------------------------------------------

def _panels(n, lo=0.01, hi=0.03):
    return [("Foreign houses", [lo] * n, [hi] * n), ("Domestic retail brokers", [-hi] * n, [-lo] * n), ("All other brokers", [-0.02] * n, [0.02] * n)]


def test_group_panels_needs_at_least_three_days_with_data():
    assert charts.group_panels(_days(2), _panels(2)) is None
    ds = _days(10)
    empty = [("A", [None] * 10, [None] * 10)]
    assert charts.group_panels(ds, empty) is None


def test_group_panels_draws_every_panel_with_labels_titles_and_a_round_trip_floor():
    ds = _days(30)
    svg = charts.group_panels(ds, _panels(30), ("Round-trip floor", [0.2] * 30))
    root = _parse(svg)                                                          # well-formed XML
    assert root.tag.endswith("svg") and root.get("role") == "img"
    for label in ("Foreign houses", "Domestic retail brokers", "All other brokers", "Round-trip floor"):
        assert label in svg
    assert "between +1.0% and +3.0% of traded value" in svg and "at least 20.0% of traded value" in svg
    assert 'aria-labelledby="groups-t groups-d"' in svg
    assert "A solid bar is the amount that is certain" in svg and "same scale" in svg                 # the description tells a screen-reader user what a bar means
    assert "buying certain on 30 days" in svg and "selling certain on 30 days" in svg and "unclear on 30 days" in svg   # each panel carries its own day counts


def test_group_panels_leaves_gaps_for_missing_days_and_escapes_text():
    ds = _days(8)
    lo = [0.01, None, 0.01, 0.01, 0.01, None, 0.01, 0.01]
    hi = [0.02, None, 0.02, 0.02, 0.02, None, 0.02, 0.02]
    svg = charts.group_panels(ds, [("A <b>&", lo, hi)])
    assert svg.count("<g>") == 6 and "A &lt;b&gt;&amp;" in svg
    _parse(svg)


def test_group_panels_clip_outliers_and_say_so_on_their_own_line():
    ds = _days(20)
    lo = [0.01] * 20
    hi = [0.02] * 20
    lo[3], hi[3] = 0.1, 0.9                                                    # one huge crossing day
    svg = charts.group_panels(ds, [("A", lo, hi)])
    assert "1 faint bar cut off at the edge" in svg and "+90.0%" in svg        # real range kept in the tooltip
    _parse(svg)


# ---- sparkline: monthly trend of the 5-day edge (checklist F5) -------------------------------------------------------

def _months(edges, n=100, start=date(2026, 1, 1)):
    """[(month_start, n, edge)] for consecutive calendar months; `n` may be an int or a per-month list."""
    out = []
    for i, e in enumerate(edges):
        y, m = divmod(start.month - 1 + i, 12)
        out.append((date(start.year + y, m + 1, 1), n[i] if isinstance(n, list) else n, e))
    return out


def _spark(points, **kw):
    kw.setdefault("y_max", 0.01)
    kw.setdefault("small_n", 30)
    kw.setdefault("uid", "t")
    return charts.sparkline(points, **kw)


def test_sparkline_is_well_formed_accessible_and_lists_every_month():
    svg = _spark(_months([0.004, -0.002, 0.006, 0.001]))
    root = _parse(svg)
    assert root.get("role") == "img" and root.get("aria-labelledby") == "t-t t-d"
    assert 'width="120"' in svg and 'width="100%"' not in svg                 # pixel width: a % collapses in a table cell
    assert svg.count("<circle") == 4
    for word in ("Jan 2026 +0.40%", "Feb 2026 -0.20%", "Apr 2026 +0.10%"):
        assert word in svg                                                   # the numbers are in the text, not only the picture


def test_sparkline_needs_three_usable_months_not_two():
    assert _spark(_months([0.004, 0.002])) is None
    assert _spark(_months([0.004, None, 0.002])) is None                     # a month with no edge is not a point
    assert _spark(_months([0.004, 0.002, 0.001])) is not None
    assert _spark([]) is None and _spark(_months([0.1, 0.1, 0.1]), y_max=0) is None


def test_a_thin_month_is_a_hollow_dot_and_is_never_joined_by_a_line():
    svg = _spark(_months([0.004, 0.003, 0.009, 0.002], n=[100, 100, 5, 100]))
    root = _parse(svg)
    circles = [e for e in root.iter() if e.tag.endswith("circle")]
    assert [c.get("fill") for c in circles] == ["var(--fg)", "var(--fg)", "var(--panel)", "var(--fg)"]
    segs = [e for e in root.iter() if e.tag.endswith("line") and e.get("stroke") == "var(--fg)"]
    assert len(segs) == 1                                                    # only Jan-Feb; the 5-signal month breaks the line on both sides
    assert "small sample" in svg


def test_a_missing_calendar_month_is_a_gap_not_an_interpolation():
    pts = _months([0.004, 0.003, 0.002, 0.001])
    del pts[1]                                                               # no Feb
    segs = [e for e in _parse(_spark(pts)).iter() if e.tag.endswith("line") and e.get("stroke") == "var(--fg)"]
    assert len(segs) == 1                                                    # Mar-Apr only; Jan and Mar are not joined across the hole


def test_the_vertical_scale_is_shared_and_outliers_are_clipped_not_stretched():
    big, small = _months([0.05, -0.05, 0.05]), _months([0.001, -0.001, 0.001])
    ys = lambda svg: [float(e.get("cy")) for e in _parse(svg).iter() if e.tag.endswith("circle")]
    a, b = ys(_spark(big, y_max=0.01)), ys(_spark(small, y_max=0.01))
    assert min(a) >= 5 - 1e-6 and max(a) <= 25 + 1e-6                       # clipped to the plot area (height 30, pad 5)
    assert max(b) - min(b) < 4 < max(a) - min(a)                             # same scale: the tiny edge really looks tiny


def test_spark_scale_ignores_thin_months_and_has_a_floor():
    series = [_months([0.003, -0.004, 0.002]), _months([0.5, 0.001, 0.001], n=[3, 100, 100])]
    # well-sampled |edges| = .001 .001 .002 .003 .004 -> 90th percentile .004; the 3-signal 50% month is not counted at all
    assert charts.spark_scale(series, small_n=30) == pytest.approx(0.004)
    assert charts.spark_scale([_months([0.0001, 0.0, 0.0001])], small_n=30) == 0.002
    assert charts.spark_scale([], small_n=30) == 0.002


def test_one_rare_outlier_month_does_not_flatten_every_other_line():
    quiet = [_months([0.005, -0.004, 0.006, -0.005]) for _ in range(5)]          # 20 ordinary months around +-0.5%
    loud = [_months([0.095, 0.002, 0.001, 0.001])]                               # one well-sampled +9.5% month
    scale = charts.spark_scale(quiet + loud, small_n=30)
    assert scale < 0.01                                                          # the maximum would have been 0.095
    svg = _spark(_months([0.095, 0.002, 0.001]), y_max=scale)                    # the outlier is pinned to the top edge, not off the box
    assert min(float(e.get("cy")) for e in _parse(svg).iter() if e.tag.endswith("circle")) == pytest.approx(5.0)
    assert "+9.50%" in svg                                                       # and its real value is still in the text


def test_sparkline_only_looks_at_the_last_twelve_months():
    pts = _months([0.9] + [0.001] * 12)                                      # a huge month 13 months before the newest
    assert charts.spark_scale([pts], small_n=30) == pytest.approx(0.002)     # dropped from the scale
    assert _spark(pts).count("<circle") == 12                                # and from the drawing


def test_bounds_band_draws_a_moderately_bigger_day_whole_instead_of_cutting_it():
    # The BBCA case: typical days around 40%, the biggest at 56% (1.3x). Cutting it would also cut its CERTAIN (solid) part.
    ds = _days(20)
    lo = [0.30] * 19 + [0.52]
    hi = [0.36] * 19 + [0.56]
    svg = charts.bounds_band(ds, lo, hi)
    _parse(svg)
    assert "cut off at the edge" not in svg and 'stroke-width="1.5"' not in svg      # nothing cut, no cap
    assert "+59%" in svg                                                              # the axis covers the biggest day (+5% headroom)


def test_scale_amp_switches_to_the_typical_day_only_for_a_truly_extreme_one():
    typical = [(0.0, 0.10)] * 19
    assert charts._scale_amp([*typical, (0.0, 0.20)]) == pytest.approx(0.21)       # 20% vs an 11.5% typical half-range: < 2x, drawn whole
    assert charts._scale_amp([*typical, (0.0, 0.80)]) == pytest.approx(0.115)      # 80%: 7x, the typical day sets the axis
