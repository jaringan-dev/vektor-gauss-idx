"""Sector rotation screen: the maths, the chart, the loader's SQL, and the page with the database stubbed."""

from __future__ import annotations

import itertools
import math
import re
from datetime import date, timedelta

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from vg.web import app as webapp
from vg.web import charts, freshness, glossary, rotation

LATEST = date(2026, 11, 5)


def _days(n, end=LATEST):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _line(days, f):
    return [(d, f(i)) for i, d in enumerate(days)]


# ---- maths -------------------------------------------------------------------------------------------------

def test_a_flat_relative_line_sits_exactly_at_the_centre():
    days = _days(60)
    bench = _line(days, lambda i: 100.0 + i)
    series = _line(days, lambda i: 2 * (100.0 + i))                      # always exactly twice the benchmark
    pts = rotation.rrg_points(series, bench, l_ratio=20, l_mom=10, tail=8)
    assert len(pts) == 8 and all(abs(x - 100) < 1e-9 and abs(y - 100) < 1e-9 for _d, x, y in pts)


def test_an_accelerating_outperformer_has_ratio_and_momentum_above_100():
    days = _days(80)
    bench = _line(days, lambda i: 100.0)
    series = _line(days, lambda i: 100.0 * math.exp(0.0006 * i * i))     # the growth RATE keeps rising, so the ratio rises too
    d, x, y = rotation.rrg_points(series, bench, l_ratio=20, l_mom=10, tail=8)[-1]
    assert d == days[-1] and x > 100 and y > 100 and rotation.quadrant(x, y) == "Outperform"


def test_a_fading_leader_is_weakening_and_a_recovering_laggard_is_improving():
    days = _days(71)
    bench = _line(days, lambda i: 100.0)
    rise_then_flat = _line(days, lambda i: 100.0 * 1.02 ** min(i, 60))   # strong for 60 days, flat for the last 10: above its average, momentum gone
    x, y = rotation.rrg_points(rise_then_flat, bench, l_ratio=20, l_mom=10, tail=3)[-1][1:]
    assert x > 100 and y < 100 and rotation.quadrant(x, y) == "Weakening"
    days = _days(65)
    fall_then_rise = _line(days, lambda i: 100.0 * (0.98 ** min(i, 60)) * (1.02 ** max(0, i - 60)))     # falling for 60 days, rising for the last 4
    x2, y2 = rotation.rrg_points(fall_then_rise, bench, l_ratio=20, l_mom=10, tail=3)[-1][1:]
    assert x2 < 100 and y2 > 100 and rotation.quadrant(x2, y2) == "Improving"


def test_a_constant_growth_rate_has_flat_ratio_so_momentum_is_100():
    days = _days(80)
    series, bench = _line(days, lambda i: 100.0 * 1.01 ** i), _line(days, lambda i: 100.0)
    _d, x, y = rotation.rrg_points(series, bench, l_ratio=20, l_mom=10, tail=8)[-1]
    assert x > 100 and y == pytest.approx(100.0, abs=1e-6)               # strength is steady, not rising: that is what momentum measures


@pytest.mark.parametrize("x,y,q", [(101, 101, "Outperform"), (101, 99, "Weakening"), (99, 99, "Underperform"), (99, 101, "Improving"),
                                   (100, 100, "Outperform")])
def test_quadrant_is_the_side_of_100_on_each_axis(x, y, q):
    assert rotation.quadrant(x, y) == q


def test_short_history_gives_no_tail_instead_of_a_stretched_one():
    days = _days(25)
    bench, series = _line(days, lambda i: 100.0), _line(days, lambda i: 100.0 + i)
    assert rotation.rrg_points(series, bench, l_ratio=20, l_mom=10, tail=8) is None            # needs 20 + 10 - 2 + 8 = 36
    assert rotation.rrg_points(_line(_days(36), lambda i: 100.0 + i), _line(_days(36), lambda i: 100.0), l_ratio=20, l_mom=10, tail=8) is not None


def test_only_dates_both_series_have_are_used():
    days = _days(70)
    bench = _line(days, lambda i: 100.0)
    gappy = [(d, 100.0 + i) for i, d in enumerate(days) if i % 7 != 3]                          # a stock that skipped some days
    pts = rotation.rrg_points(gappy, bench, l_ratio=20, l_mom=10, tail=5)
    assert pts is not None and all(d in dict(gappy) for d, _x, _y in pts)


def test_weekly_resampling_keeps_the_last_session_of_each_iso_week():
    days = _days(15)
    weekly = rotation.to_weekly(_line(days, float))
    assert len(weekly) == 4 and [d.weekday() for d, _c in weekly[:-1]] == [4, 4, 4] and weekly[-1][0] == LATEST    # Fridays; the newest week is unfinished
    assert weekly[0][1] == float(days.index(weekly[0][0]))


def test_clean_only_accepts_known_values():
    p = rotation.clean({"sector": "Energi", "freq": "weekly", "tail": "99"})
    assert p == {"sector": "Energi", "freq": "weekly", "tail": rotation.TAIL_MAX}
    q = rotation.clean({"sector": "<script>", "freq": "hourly", "tail": "x"})
    assert q == {"sector": "", "freq": "daily", "tail": rotation.TAIL_DEFAULT}
    assert rotation.clean({"tail": "1"})["tail"] == rotation.TAIL_MIN


def test_every_sector_has_an_index_and_the_names_match_the_data_set():
    assert len(rotation.SECTOR_INDEX) == 11 and len(set(rotation.SECTOR_INDEX.values())) == 11
    assert all(v.startswith("IDX") for v in rotation.SECTOR_INDEX.values())


# ---- view --------------------------------------------------------------------------------------------------

def _raw_sectors(n=80):
    days = _days(n)
    bench = _line(days, lambda i: 7000.0)
    series = {"Energi": _line(days, lambda i: 1000.0 * math.exp(0.0006 * i * i)), "Keuangan": _line(days, lambda i: 1000.0 * math.exp(-0.0006 * i * i)),
              "Teknologi": _line(days, lambda i: 1000.0 + (i % 5))}
    return {"latest": LATEST, "bench": bench, "series": series, "avg_value": {}}


def test_sector_view_orders_rows_by_quadrant_and_uses_short_chart_labels():
    v = rotation.build_view(_raw_sectors(), rotation.clean({}))
    assert v["n_shown"] == 3 and v["unit"] == "sessions" and v["windows"] == "20 and 10" and v["bench_name"] == "the Jakarta Composite"
    assert v["rows"][0]["label"] == "Energi" and v["rows"][0]["quad"] == "Outperform"
    assert [i["short"] for i in v["items"]] == ["ENERGY", "FINANCE", "TECHNO"]
    assert v["counts"]["Outperform"] >= 1 and sum(v["counts"].values()) == 3 and v["rows"][-1]["quad"] in ("Underperform", "Weakening")
    assert v["chart"].startswith("<svg") and v["chart_m"].startswith("<svg")
    assert v["last_bar"] == LATEST.isoformat() and v["dropped"] == []


def test_weekly_view_uses_the_weekly_windows():
    v = rotation.build_view(_raw_sectors(120), rotation.clean({"freq": "weekly"}))
    assert v["unit"] == "weeks" and v["windows"] == "10 and 5" and v["n_shown"] == 3


def test_stock_view_keeps_only_liquid_stocks_the_most_liquid_first_and_capped():
    days = _days(80)
    series = {f"T{i:03d}": _line(days, lambda k, i=i: 100.0 + k * 0.1 * (i % 3)) for i in range(40)}
    avg = {t: (10_000_000_000 + i if i < 35 else 1_000_000_000) for i, t in enumerate(series)}       # the last five are thin
    raw = {"latest": LATEST, "bench": _line(days, lambda k: 5000.0), "series": series, "avg_value": avg}
    v = rotation.build_view(raw, rotation.clean({"sector": "Energi"}))
    assert v["n_shown"] == rotation.MAX_STOCKS and v["bench_name"] == "the IDXENERGY index"
    shown = {i["label"] for i in v["items"]}
    assert not shown & {f"T{i:03d}" for i in range(35, 40)}                                          # thin stocks never drawn
    assert "T034" in shown and "T009" not in shown                                                    # biggest value first, cut at 25


def test_items_without_enough_history_are_listed_not_drawn():
    raw = _raw_sectors()
    raw["series"]["Kesehatan"] = raw["series"]["Energi"][-10:]
    v = rotation.build_view(raw, rotation.clean({}))
    assert v["dropped"] == ["Kesehatan"] and v["n_shown"] == 3


def test_empty_data_builds_without_a_chart():
    v = rotation.build_view({"latest": LATEST, "bench": [], "series": {}, "avg_value": {}}, rotation.clean({"sector": "Energi"}))
    assert v["chart"] is None and v["rows"] == [] and v["n_shown"] == 0


def test_chips_link_to_each_sector_and_each_bar_size_and_keep_the_other_choice():
    c = rotation.chips(rotation.clean({"sector": "Energi", "freq": "weekly"}))
    assert [x["label"] for x in c["sectors"]][:2] == ["All sectors", "Energi"] and len(c["sectors"]) == 12
    on = [x["label"] for x in c["sectors"] if x["on"]]
    assert on == ["Energi"] and [x["label"] for x in c["freqs"] if x["on"]] == ["Weekly"]
    assert "freq=weekly" in c["sectors"][3]["href"] and "sector=Keuangan" in next(x for x in c["sectors"] if x["label"] == "Keuangan")["href"]
    assert rotation.chips(rotation.clean({}))["sectors"][0]["href"] == "?"                            # defaults leave the address clean


# ---- chart -------------------------------------------------------------------------------------------------

def _items():
    return [{"label": "Energi", "short": "ENERGY", "quad": "Outperform", "pts": [(100 + i * 0.4, 100 + i * 0.2) for i in range(8)]},
            {"label": "Keuangan", "short": "FINANCE", "quad": "Underperform", "pts": [(99 - i * 0.3, 99.5 - i * 0.1) for i in range(8)]}]


def test_rotation_chart_is_an_accessible_svg_with_quadrant_words_and_names():
    svg = charts.rotation_chart(_items(), 8)
    assert svg.startswith("<svg") and 'role="img"' in svg and "<title" in svg and "<desc" in svg
    for word in ("Improving", "Outperform", "Underperform", "Weakening", "ENERGY", "FINANCE", "RS-Ratio", "RS-Momentum"):
        assert word in svg
    assert "1 in Outperform, 1 in Underperform" in svg or "1 in Outperform" in svg
    assert "Energi: RS-Ratio" in svg and "None" not in svg and not re.search(r"\bnan\b", svg.lower())
    assert charts.rotation_chart([], 8) is None


def test_phone_variant_drops_the_hover_text_and_keeps_the_names():
    svg = charts.rotation_chart(_items(), 8, width=charts.W_MOBILE, height=340, uid="rot-m", tips=False)
    assert "ENERGY" in svg and "RS-Ratio: " not in svg.split("<desc")[0] and "Energi: RS-Ratio" not in svg and 'viewBox="0 0 380 340"' in svg


def test_an_extreme_item_is_drawn_at_the_edge_with_a_dashed_ring_and_does_not_flatten_the_rest():
    items = [{"label": f"S{i}", "short": f"S{i}", "quad": "Outperform", "pts": [(100.5 + i * 0.1, 100.2)] * 4} for i in range(30)]
    items.append({"label": "WILD", "short": "WILD", "quad": "Outperform", "pts": [(100.0, 100.0), (180.0, 140.0)]})
    svg = charts.rotation_chart(items, 4)
    assert "1 beyond the scale (dashed ring)" in svg and "(beyond the scale)" in svg and 'stroke-dasharray="2 2"' in svg
    assert 'font-weight="600">Outperform' in svg


def test_labels_are_nudged_apart_when_heads_coincide():
    items = [{"label": f"A{i}", "short": f"AAA{i}", "quad": "Improving", "pts": [(99.0, 100.5)] * 3} for i in range(4)]
    svg = charts.rotation_chart(items, 3)
    ys = sorted(float(m) for m in re.findall(r'<text x="[\d.]+" y="([\d.]+)" font-size="10"', svg))
    assert len(ys) == 4 and all(b - a >= 9 for a, b in itertools.pairwise(ys))


# ---- loader SQL --------------------------------------------------------------------------------------------

class _Cur:
    def __init__(self, log, mode):
        self.log, self.mode, self._rows = log, mode, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        days = _days(5)
        if "max(session_date)" in sql:
            self._rows = [(LATEST,)]
        elif "FROM index_bars" in sql and "ANY" in sql:
            self._rows = [(c, d, 100.0 + i) for c in ("IDXENERGY", "IDXFINANCE", "COMPOSITE", "NOTMAPPED") for i, d in enumerate(days)]
        elif "FROM index_bars" in sql:
            self._rows = [(d, 100.0 + i) for i, d in enumerate(days)]
        else:
            self._rows = [("BUMI", d, 50.0 + i, 5e9 + i) for i, d in enumerate(days)] + [("ADRO", days[0], 10.0, None)]

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return self._rows


class _Conn(_Cur):
    def cursor(self):
        return self


def _patch_db(monkeypatch, mode=""):
    import vg.db

    log = []
    monkeypatch.setattr(vg.db, "connect", lambda: _Conn(log, mode))
    return log


def test_load_sector_overview_maps_index_codes_back_to_sector_names(monkeypatch):
    log = _patch_db(monkeypatch)
    raw = rotation.load("")
    assert set(raw["series"]) == {"Energi", "Keuangan"} and len(raw["bench"]) == 5 and raw["latest"] == LATEST     # NOTMAPPED never leaks in
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _p in log)


def test_load_one_sector_reads_split_adjusted_closes_and_averages_traded_value(monkeypatch):
    log = _patch_db(monkeypatch)
    raw = rotation.load("Energi")
    stock_sql = next(sql for sql, _p in log if "FROM price_bars" in sql)
    assert "split_factor" in stock_sql and "t.sector = %s" in stock_sql                                           # adjusted prices, sector filter bound as a parameter
    assert set(raw["series"]) == {"BUMI", "ADRO"} and raw["avg_value"]["BUMI"] == pytest.approx(5e9 + 2)
    assert raw["avg_value"]["ADRO"] == 0.0                                                                         # a NULL traded value counts as 0, not an error
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _p in log)


def test_load_without_index_data_returns_an_empty_result(monkeypatch):
    import vg.db

    class _None(_Conn):
        def execute(self, sql, params=None):
            self._rows = [(None,)]

    monkeypatch.setattr(vg.db, "connect", lambda: _None([], ""))
    assert rotation.load("")["latest"] is None


# ---- the page ----------------------------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    eod = date(2026, 10, 30)
    days = [eod - timedelta(days=i) for i in range(30) if (eod - timedelta(days=i)).weekday() < 5]
    monkeypatch.setattr(freshness, "load_inputs", lambda today=None: freshness.FreshInputs(
        today=date(2026, 10, 31), eod_date=eod, price_counts={d: 962 for d in days},
        broker_counts={d: 190 for d in days}, ksei_month_end=date(2026, 9, 30)))
    monkeypatch.setattr(rotation, "load", lambda sector: _raw_sectors(120) if not sector else {**_raw_sectors(120), "avg_value": {"Energi": 9e9, "Keuangan": 9e9}})
    webapp._STRIP_CACHE.clear()
    webapp._ROTATION_CACHE.clear()
    return TestClient(webapp.app)


def test_rotation_page_shows_the_chart_the_controls_and_the_table(client):
    r = client.get("/rotation")
    assert r.status_code == 200
    t = r.text
    for needle in ("Which sectors are gaining or losing strength against the market?", "Sectors against the Jakarta Composite", 'aria-label="Sector"',
                   'aria-label="Bar size"', 'aria-label="Relative rotation values"', "Relative rotation chart", "ENERGY", "Method: relative strength",
                   "not its proprietary formula", "Not investment advice"):
        assert needle in t, needle
    assert re.search(r'<a class="tk" href="\?sector=Energi"><b|<b><a class="tk" href="\?sector=Energi">', t)
    assert 'class="chart-wide"' in t and "None" not in re.search(r'aria-label="Relative rotation values".*?</table>', t, re.DOTALL).group(0)


def test_rotation_page_for_one_sector_links_stocks(client, monkeypatch):
    t = client.get("/rotation?sector=Energi&freq=weekly&tail=5").text
    assert "Energi: stocks against the IDXENERGY index" in t and "its tail shows the last 5 weeks" in t and "The newest week may be unfinished." in t
    assert 'href="/stock/' in t


def test_rotation_page_survives_junk_parameters_and_a_dead_database(client, monkeypatch):
    assert client.get("/rotation?sector=%3Cscript%3E&freq=zz&tail=abc").status_code == 200

    def boom(sector):
        raise RuntimeError("db down")

    monkeypatch.setattr(rotation, "load", boom)
    webapp._ROTATION_CACHE.clear()
    r = client.get("/rotation")
    assert r.status_code == 200 and "Cannot read the data" in r.text and "db down" in r.text and "Not investment advice" in r.text


def test_rotation_page_with_no_index_data_says_so(client, monkeypatch):
    monkeypatch.setattr(rotation, "load", lambda sector: {"latest": None, "bench": [], "series": {}, "avg_value": {}})
    webapp._ROTATION_CACHE.clear()
    assert "No index data yet" in client.get("/rotation").text


def test_rotation_is_in_the_sidebar_and_highlighted_in_the_top_bar(client):
    t = client.get("/rotation").text
    assert len(re.findall(r'<a [^>]*aria-current="page"[^>]*>', t)) == 1 and 'href="/rotation"' in t
    top = re.search(r'<nav aria-label="Primary sections">(.*?)</nav>', t, re.DOTALL).group(1)
    assert re.search(r'<a href="/"[^>]* aria-current="true">Screener', top)


def test_rotation_data_is_cached_per_sector_and_fresh_reloads(client, monkeypatch):
    calls = []
    monkeypatch.setattr(rotation, "load", lambda sector: (calls.append(sector), _raw_sectors(120))[1])
    client.get("/rotation")
    client.get("/rotation?freq=weekly")                                                                 # same data, other bar size: no reload
    assert calls == [""]
    client.get("/rotation?sector=Energi")
    client.get("/rotation?fresh=1")
    assert calls == ["", "Energi", ""]


def test_the_glossary_explains_the_rotation_terms_without_trading_advice():
    t = glossary.TERMS["rotation"]
    assert t.caveat and "forecast" in t.caveat and "proprietary" in t.caveat and t.how
