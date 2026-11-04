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
    assert svg.count("<rect") == 4
    assert "var(--pos)" in svg and "var(--neg)" in svg and "var(--muted)" in svg
    assert "between +6.0% and +9.0% of traded value" in svg


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
    assert "1 day beyond the scale" in svg
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
    assert "guaranteed to contain" in svg                                      # the description tells a screen-reader user what a bar means


def test_group_panels_leaves_gaps_for_missing_days_and_escapes_text():
    ds = _days(8)
    lo = [0.01, None, 0.01, 0.01, 0.01, None, 0.01, 0.01]
    hi = [0.02, None, 0.02, 0.02, 0.02, None, 0.02, 0.02]
    svg = charts.group_panels(ds, [("A <b>&", lo, hi)])
    assert svg.count("<rect") == 6 and "A &lt;b&gt;&amp;" in svg
    _parse(svg)


def test_group_panels_clip_outliers_and_say_so_on_their_own_line():
    ds = _days(20)
    lo = [0.01] * 20
    hi = [0.02] * 20
    lo[3], hi[3] = 0.1, 0.9                                                    # one huge crossing day
    svg = charts.group_panels(ds, [("A", lo, hi)])
    assert "beyond the scale" in svg and "+90.0%" in svg                       # real range kept in the tooltip
    _parse(svg)
