"""The two broker charts on the stock page, redesigned to be readable: plain bars from a zero line (solid = certain, faint = could be
more, grey = cannot say), day counts above the bars, a one-sentence summary and a key. The tests pin the encoding and the wording."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from vg.web import app as webapp
from vg.web import charts, freshness
from vg.web import stock as stockmod

from .test_web_stock import TRACK, _raw, _with_brokers, _with_charts


def _days(n, start=date(2026, 7, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _zero_y(svg):
    """Height of the zero line (the first muted horizontal line)."""
    return float(re.search(r'y1="([\d.]+)" y2="[\d.]+" stroke="var\(--muted\)"', svg).group(1))


def _groups(svg):
    """[(title, [(opacity, y, height)])] for every <g> day mark in the SVG."""
    out = []
    for g in re.findall(r"<g><title>(.*?)</title>(.*?)</g>", svg, re.DOTALL):
        rects = [(float(op), float(y), float(h)) for y, h, op in re.findall(r'<rect x="[\d.]+" y="([\d.]+)" width="[\d.]+" height="([\d.]+)" fill="[^"]+" opacity="([\d.]+)"', g[1])]
        out.append((g[0], rects))
    return out


# ---- encoding ----------------------------------------------------------------------------------------------

def test_day_counts_split_days_into_buying_selling_and_unclear_and_skip_missing_days():
    lo = [0.02, -0.05, -0.01, None, 0.0, -0.09]
    hi = [0.05, -0.01, 0.03, None, 0.04, -0.03]
    assert charts.day_counts(lo, hi) == {"buy": 1, "sell": 2, "unclear": 2}                 # a range that only touches zero is not certain


def test_a_certain_buying_day_is_a_solid_green_bar_from_the_zero_line_plus_a_faint_extension():
    svg = charts.bounds_band(_days(5), [0.02, 0.06, 0.06, 0.06, 0.06], [0.05, 0.09, 0.09, 0.09, 0.09])
    zero = _zero_y(svg)
    title, rects = _groups(svg)[0]
    assert "net buying is certain" in title and "at least +2.0%" in title and "between +2.0% and +5.0% of traded value" in title
    (op1, y1, h1), (op2, y2, h2) = rects
    assert {op1, op2} == {0.9, 0.3}
    solid = (y1, h1) if op1 == 0.9 else (y2, h2)
    faint = (y2, h2) if op1 == 0.9 else (y1, h1)
    assert solid[0] + solid[1] == pytest.approx(zero, abs=0.1)                                  # the solid part sits ON the zero line, growing upward
    assert faint[0] + faint[1] == pytest.approx(solid[0], abs=0.1)                              # the faint part continues where the solid one ends


def test_a_certain_selling_day_is_a_solid_red_bar_growing_down_from_the_zero_line():
    svg = charts.bounds_band(_days(5), [-0.05] * 5, [-0.02] * 5)
    zero = _zero_y(svg)
    title, rects = _groups(svg)[0]
    assert "net selling is certain" in title and "at least 2.0% sold" in title
    solid = next(r for r in rects if r[0] == 0.9)
    faint = next(r for r in rects if r[0] == 0.3)
    assert solid[1] == pytest.approx(zero, abs=0.1) and faint[1] == pytest.approx(solid[1] + solid[2], abs=0.1)     # solid starts at zero, faint continues below it
    assert 'fill="var(--neg)"' in svg and 'fill="var(--pos)"' not in svg


def test_a_day_that_crosses_zero_is_one_faint_grey_bar_and_says_it_cannot_say():
    svg = charts.bounds_band(_days(5), [-0.03] * 5, [0.04] * 5)
    title, rects = _groups(svg)[0]
    assert "unclear" in title and "could be either way" in title and len(rects) == 1 and rects[0][0] == 0.4
    assert 'fill="var(--muted)"' in svg and 'fill="var(--pos)"' not in svg and 'fill="var(--neg)"' not in svg


def test_a_cut_off_faint_part_gets_a_cap_but_the_solid_part_is_never_distorted():
    lo = [0.01] * 11 + [0.02]
    hi = [0.03] * 11 + [0.80]
    svg = charts.bounds_band(_days(12), lo, hi)
    assert "1 faint bar cut off at the edge (hover for the full range)" in svg and 'stroke="var(--fg)" stroke-width="1.5"' in svg
    title, rects = _groups(svg)[-1]
    assert "between +2.0% and +80.0% of traded value" in title
    assert any(r[0] == 0.9 for r in rects)                                                      # the certain amount is still drawn, at its true size


def test_every_panel_of_the_group_chart_carries_its_own_counts_and_the_same_scale():
    ds = _days(30)
    panels = [("Foreign houses", [0.01] * 30, [0.03] * 30), ("Domestic retail brokers", [-0.03] * 30, [-0.01] * 30),
              ("All other brokers", [-0.02] * 30, [0.02] * 30)]
    svg = charts.group_panels(ds, panels, ("Same brokers on both sides", [0.2] * 30))
    for label, words in (("Foreign houses", "buying certain on 30 days · selling certain on 0 days"),
                         ("Domestic retail brokers", "buying certain on 0 days · selling certain on 30 days"),
                         ("All other brokers", "unclear on 30 days")):
        assert label in svg and words in svg, (label, words)
    scales = set(re.findall(r">([+-]\d+)%</text>", svg))
    assert len(scales) == 2                                                                      # one + and one - value, printed on every panel: the panels share one scale
    narrow = charts.group_panels(ds, panels, width=charts.W_MOBILE, uid="groups-m")
    assert "certain buying 30d" in narrow and "buying certain on" not in narrow                  # shorter wording on a phone


# ---- summaries ---------------------------------------------------------------------------------------------

def test_range_summary_counts_days_and_adds_the_ranges_up():
    s = stockmod.range_summary([(2e9, 5e9), (1e9, 2e9), (-3e9, 1e9), (None, None)])
    assert s["n"] == 3 and s["buy"] == 2 and s["sell"] == 0 and s["unclear"] == 1
    assert s["cum_lo"] == "+0" and s["cum_hi"] == "+8.0B"                                                  # 2 + 1 - 3 = 0: the low end is not above zero, so not certain
    assert s["cls"] == "" and "cannot say" in s["verdict"]


def test_range_summary_is_certain_only_when_the_whole_cumulative_range_is_on_one_side():
    buy = stockmod.range_summary([(1e9, 2e9), (3e9, 4e9)])
    assert buy["cls"] == "pos" and "net buying over the whole period is certain" in buy["verdict"] and buy["cum_lo"] == "+4.0B" and buy["cum_hi"] == "+6.0B"
    sell = stockmod.range_summary([(-5e9, -2e9), (-4e9, -1e9)])
    assert sell["cls"] == "neg" and "net selling over the whole period is certain" in sell["verdict"] and sell["cum_lo"] == "-9.0B"
    assert stockmod.range_summary([]) is None and stockmod.range_summary([(None, 1.0)]) is None


def test_group_summary_gives_one_row_per_group_in_a_fixed_order_in_rupiah():
    g = stockmod.group_series(_with_brokers(_raw()))
    rows = stockmod.group_summary(g)
    assert [r["label"] for r in rows] == ["Foreign houses", "Domestic retail brokers", "All other brokers"]
    foreign, retail, other = rows
    assert (foreign["buy"], foreign["sell"], foreign["unclear"]) == (12, 0, 0) and foreign["cls"] == "pos"
    assert (retail["buy"], retail["sell"], retail["unclear"]) == (0, 12, 0) and retail["cls"] == "neg"
    assert (other["buy"], other["sell"], other["unclear"]) == (0, 0, 12) and other["cls"] == ""
    assert stockmod.group_summary(None) == []


# ---- the page ----------------------------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    eod = date(2026, 10, 30)
    days = [eod - timedelta(days=i) for i in range(30) if (eod - timedelta(days=i)).weekday() < 5]
    monkeypatch.setattr(freshness, "load_inputs", lambda today=None: freshness.FreshInputs(
        today=date(2026, 10, 31), eod_date=eod, price_counts={d: 962 for d in days},
        broker_counts={d: 190 for d in days}, ksei_month_end=date(2026, 9, 30)))
    monkeypatch.setattr(webapp, "_eval_version", lambda: ("ts", 1))
    monkeypatch.setattr(webapp, "_track_by_tag", lambda: TRACK)
    webapp._STRIP_CACHE.clear()
    webapp._EVAL_CACHE.clear()
    return TestClient(webapp.app)


def test_band_section_explains_itself_in_plain_words_with_a_key_and_a_one_sentence_summary(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: _with_charts(_raw()))
    t = client.get("/stock/BBCA").text
    section = re.search(r'<h2>Are foreign brokers buying or selling\? Day by day</h2>.*?</div>\s*</div>', t, re.DOTALL).group(0)
    section = re.sub(r"\s+", " ", section)                                                  # the template wraps lines inside sentences
    for needle in ("certain range</a>", "the solid part of a bar is the amount that is", 'aria-label="How to read the bars"', "Green, solid: buying is certain",
                   "Red, solid: selling is certain", "Grey: the data cannot say", "In short", "Over the 30 days with broker data",
                   "net selling was certain on <b>30</b> days", "so net selling over the whole period is certain"):
        assert needle in section or needle.lower() in section.lower(), needle


def test_group_section_keeps_its_caveats_and_adds_a_per_group_table_and_the_same_key(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: _with_brokers(_raw()))
    t = client.get("/stock/BBCA").text
    assert t.count('aria-label="How to read the bars"') == 2                                      # one key per chart
    for needle in ("Who is buying and selling?", "Net flow by group</a>", "round-trip</a>", "an estimate", "not proof of a market maker", "share one scale",
                   'aria-label="Buying and selling by group over the period"', "Days buying was certain", "Foreign houses", "net buying is certain",
                   "net selling is certain", "cannot say", "The same bars as above"):
        assert needle in t, needle
    table = re.search(r'aria-label="Buying and selling by group over the period".*?</table>', t, re.DOTALL).group(0)
    assert "None" not in table and table.count("<tr><td") == 3


def test_no_broker_data_means_no_summary_and_no_empty_frame(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: _raw())
    t = client.get("/stock/BBCA").text
    assert "Are foreign brokers buying or selling" not in t and 'aria-label="How to read the bars"' not in t and "In short" not in t
