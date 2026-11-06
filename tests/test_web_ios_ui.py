"""The iOS-style shell and the new widgets: collapsible sidebar, Bandar Power Meter, HAKA/HAKI split, overlap matrix, historical edge,
expectancy by horizon, EOD run time and the run_log viewer. Everything runs without a database."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from vg.engine.scoring import SCORING_COLUMNS
from vg.web import app as webapp
from vg.web import charts, edge, eodlog, runlog
from vg.web import stock as stockmod

from .test_web_home import client  # noqa: F401 — the shared page-test fixture
from .test_web_stock import LATEST, _raw

D = Decimal


def _lum(hexcolor: str) -> float:
    c = [int(hexcolor[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ---- the shell --------------------------------------------------------------------------------------------------------------------

def test_the_sidebar_collapses_to_icons_and_remembers_it_without_breaking_when_storage_is_blocked(client):  # noqa: F811
    t = client.get("/").text
    assert 'id="side-toggle"' in t and 'aria-controls="sidebar"' in t and 'aria-expanded="true"' in t and 'aria-label="Collapse the sidebar"' in t
    head = t[:t.index("<style>")]
    assert 'getItem("vg-side") === "collapsed"' in head and "try {" in head              # applied before the first paint, in a try/catch
    script = t[t.index('var btn = document.getElementById("side-toggle")'):]
    assert 'localStorage.setItem("vg-side", "collapsed")' in script and 'localStorage.removeItem("vg-side")' in script
    assert re.search(r"try \{ if \(collapsed\(\)\) localStorage\.setItem.*?\} catch \(e\) \{\}", script)
    css = t[t.index("<style>"):t.index("</style>")]
    wide = re.search(r"@media \(min-width:860px\) \{\s*html\[data-side=\"collapsed\"\] \.layout \{ grid-template-columns:68px", css)
    assert wide, "the collapsed layout must only apply on wide screens (a phone uses the Menu button)"
    assert ".side-toggle { display:none; }" in css                                      # no toggle on a phone


def test_every_sidebar_item_has_an_icon_a_tooltip_and_a_text_label_that_stays_readable_to_screen_readers(client):  # noqa: F811
    t = client.get("/").text
    side = re.search(r'<aside id="sidebar".*?</aside>', t, re.DOTALL).group(0)
    links = re.findall(r'<li><a href="[^"]+" title="([^"]+)"[^>]*><span class="ico" aria-hidden="true">(.+?)</span><span class="lbl">(.+?)</span></a></li>', side)
    n = sum(len(g["items"]) for g in webapp._NAV)
    assert len(links) == n >= 10
    assert all(title == label and icon for title, icon, label in links)
    assert len(re.findall(r'aria-current="page"', side)) == 1
    css = t[t.index("<style>"):t.index("</style>")]
    assert 'html[data-side="collapsed"] .sidebar .lbl' in css and "clip:rect(0 0 0 0)" in css   # hidden visually, still in the accessibility tree
    assert all(it.get("icon") for g in webapp._NAV for it in g["items"])


def test_the_active_item_is_a_filled_pill_with_enough_contrast():
    assert _contrast("#ffffff", "#1d56d6") >= 4.5                                       # --accent-fg on --accent, both themes
    assert _contrast("#ffffff", "#15803d") >= 4.5 and _contrast("#ffffff", "#b91c1c") >= 4.5   # the HAKA / HAKI bar labels


def test_surfaces_are_frosted_with_an_opaque_fallback(client):  # noqa: F811
    css = client.get("/").text
    assert css.count("backdrop-filter:saturate(180%) blur(18px)") >= 4
    assert "@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px)))" in css
    assert "--r:16px" in css and "border-radius:var(--r)" in css                         # 16px: the fintech template's card radius


# ---- Bandar Power Meter ------------------------------------------------------------------------------------------------------------

def test_the_gauge_is_a_semicircle_with_three_bands_a_needle_and_the_true_number():
    svg = charts.bpi_gauge(30.0)
    assert svg.count("<path") == 3 and 'role="img"' in svg and "+30.0" in svg and "-100" in svg and "+100" in svg
    assert 'fill="var(--neg)"' in svg and 'fill="var(--pos)"' in svg and 'fill="var(--muted)"' in svg
    assert "Bandar Power Index, top 3, is +30.0 points on a scale from -100 to +100" in svg
    cx, cy = 120.0, 112.0
    for value, side in ((-100, "left"), (0, "up"), (100, "right")):
        x, y = charts._polar(cx, cy, 92, value)
        assert {"left": x < cx - 90, "up": abs(x - cx) < 0.01 and y < cy - 90, "right": x > cx + 90}[side], value
    assert charts._polar(cx, cy, 92, 500) == charts._polar(cx, cy, 92, 100)             # out of range is pinned to the end
    assert "+250.0" in charts.bpi_gauge(250.0)                                          # ... but the printed number stays true
    assert charts.bpi_gauge(None) is None


# ---- overlap matrix ----------------------------------------------------------------------------------------------------------------

def _lv(buy, sell):
    return ([("B", i + 1, c, f"{c} name", D(100 - i)) for i, c in enumerate(buy)]
            + [("S", i + 1, c, f"{c} name", D(90 - i)) for i, c in enumerate(sell)])


def test_the_matrix_puts_buyers_next_to_sellers_and_flags_houses_on_both_sides():
    m = stockmod.overlap_matrix(_lv(["AK", "YP", "ZP", "ZZ"], ["ZP", "AK", "CC"]), date(2026, 11, 3))
    assert [(r["buy"]["code"], r["sell"]["code"]) for r in m["rows"]] == [("AK", "ZP"), ("YP", "AK"), ("ZP", "CC")]   # only the top 3 of each side
    assert m["shared"] == ["AK", "ZP"] and m["shared_n"] == 2 and m["alert"] is True and m["day"] == "2026-11-03"
    assert [r["buy"]["shared"] for r in m["rows"]] == [True, False, True]


def test_one_shared_house_is_noted_but_is_not_an_alert_and_a_missing_side_stays_empty():
    m = stockmod.overlap_matrix(_lv(["AK", "YP", "ZP"], ["AK", "CC", "XX"]), None)
    assert m["shared_n"] == 1 and m["alert"] is False and m["day"] is None
    short = stockmod.overlap_matrix(_lv(["AK"], []), date(2026, 11, 3))
    assert short["rows"][1]["buy"] is None and short["rows"][0]["sell"] is None and short["alert"] is False
    assert stockmod.overlap_matrix([], date(2026, 11, 3)) is None and stockmod.overlap_matrix(None, None) is None


# ---- historical edge ---------------------------------------------------------------------------------------------------------------

class _Cur:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, None, None

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows


def test_edge_load_reads_four_horizons_for_the_asked_tags_only():
    cur = _Cur([("AKUM-ASING", 120, 0.55, 0.004, 110, 0.5, 0.0, 100, 0.6, 0.012, 80, 0.7, 0.03)])
    out = edge.load_cur(cur, ["AKUM-ASING"])
    assert out["AKUM-ASING"][1] == (120, 0.55, 0.004) and out["AKUM-ASING"][10] == (80, 0.7, 0.03)
    assert "tag = ANY(%s)" in cur.sql and cur.params == (["AKUM-ASING"],) and "ret2_h1" in cur.sql and "ret2_h10" in cur.sql   # outcome v2
    cur_all = _Cur([])
    edge.load_cur(cur_all, None)
    assert "WHERE" not in cur_all.sql and cur_all.params is None


def test_a_horizon_shows_win_rate_and_expectancy_marks_thin_samples_and_never_prints_none():
    row = edge.row_view("AKUM-ASING", {1: (200, 0.60, 0.012), 3: (10, 0.40, -0.003), 5: (0, None, None)})
    c1, c3, c5, c10 = row["cells"]
    assert (c1["win"], c1["exp"], c1["win_c"], c1["exp_c"], c1["thin"]) == ("60%", "+1.20%", "pos", "pos", False)
    assert (c3["win"], c3["exp"], c3["win_c"], c3["exp_c"], c3["thin"]) == ("40%", "-0.30%", "neg", "neg", True)
    assert c5["win"] == "N/A" and c10["exp"] == "N/A" and row["n"] == 200
    assert "None" not in repr([c["win"] for c in row["cells"]] + [c["exp"] for c in row["cells"]])


def test_the_comparison_needs_both_patterns_and_shows_the_gap_per_horizon():
    data = {"AKUM-ASING": {h: (100, 0.55, 0.010) for h in edge.HORIZONS}, "AKUM-NETRAL-WASH": {h: (100, 0.50, 0.004) for h in edge.HORIZONS}}
    cmp_ = edge.compare(data)
    assert [r["h"] for r in cmp_["rows"]] == [1, 3, 5, 10] and cmp_["rows"][0]["diff"] == "+0.60%" and cmp_["rows"][0]["diff_c"] == "pos"
    assert edge.compare({"AKUM-ASING": data["AKUM-ASING"]}) is None
    data["AKUM-NETRAL-WASH"][3] = (0, None, None)
    row3 = edge.compare(data)["rows"][1]
    assert row3["diff"] == "N/A" and row3["thin"] is True


def test_view_orders_the_asked_tags_first_then_the_rest():
    data = {t: {1: (50, 0.5, 0.0)} for t in ("C-TAG", "A-TAG", "B-TAG")}
    assert [r["tag"] for r in edge.view(data, order=["B-TAG", "MISSING"])] == ["B-TAG", "A-TAG", "C-TAG"]


# ---- the stock page ----------------------------------------------------------------------------------------------------------------

def _scoring_row(**over):
    vals = {c: None for c in SCORING_COLUMNS}
    vals.update(bpi3=D("0.30"), bpi5=D("0.238"), bpi_overlap3=2, aggr_haka_share=D("0.59"), aggr_ratio=D("1.44"), aggr_coverage=D("0.91"))
    vals.update(over)
    return (LATEST, *[vals[c] for c in SCORING_COLUMNS])


def _page_raw(**over):
    raw = _raw()
    raw["scoring"] = [_scoring_row(**over)]
    raw["levels"] = _lv(["AK", "YP", "ZP"], ["ZP", "AK", "CC"])
    raw["edge"] = {"BROKER2-ASING-AKUM": {1: (400, 0.52, 0.004), 3: (390, 0.53, 0.007), 5: (380, 0.54, 0.011), 10: (12, 0.58, 0.02)}}
    return raw


def test_the_scoring_panel_gets_a_gauge_a_haka_haki_split_and_an_overlap_matrix(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(stockmod, "load", lambda t: _page_raw())
    t = client.get("/stock/BBCA").text
    assert 'aria-labelledby="w-gauge"' in t and "Bandar Power Meter" in t and "+30.0" in t
    assert 'aria-label="HAKA 59 percent, HAKI 41 percent of the volume with a known aggressor"' in t and "HAKA 59%" in t and "HAKI 41%" in t
    assert '<table class="matrix alert"' in t and t.count("on both sides") >= 2 and "2 of the 3 biggest buyers are also among the 3 biggest sellers" in t
    assert 'aria-label="Top 3 buyers next to top 3 sellers"' in t


def test_the_widgets_show_na_and_never_break_when_scores_or_order_flow_are_missing(client, monkeypatch):  # noqa: F811
    raw = _page_raw(bpi3=None, bpi5=None, bpi_overlap3=None, aggr_haka_share=None, aggr_ratio=None, aggr_coverage=None, retail_div_state="x")
    raw["levels"] = []
    monkeypatch.setattr(stockmod, "load", lambda t: raw)
    r = client.get("/stock/BBCA")
    assert r.status_code == 200
    w = re.search(r'<div class="widgets".*?</div>\s*</section>\s*</div>', r.text, re.DOTALL) or re.search(r'<div class="widgets".*', r.text, re.DOTALL)
    assert w.group(0).count("N/A") >= 3 and "None" not in w.group(0)[:3000]
    assert "No order-flow data for this day" in r.text and "No broker ladder for this stock" in r.text


def test_the_historical_edge_card_lists_win_rate_and_expectancy_per_horizon(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(stockmod, "load", lambda t: _page_raw())
    t = client.get("/stock/BBCA").text
    card = re.search(r'<div class="panel" id="historical-edge">.*?</table>', t, re.DOTALL).group(0)
    for needle in ("Signal Historical Edge", "history, not a forecast", "H+1", "H+3", "H+5", "H+10", "52%", "+0.40%", "+2.00%", "400"):
        assert needle in card, needle
    assert card.count('class="badge warn"') == 1                                        # only the 12-signal H+10 cell is thin


def test_the_historical_edge_card_says_so_when_there_is_nothing_to_show(client, monkeypatch):  # noqa: F811
    raw = _page_raw()
    raw["edge"] = {}
    monkeypatch.setattr(stockmod, "load", lambda t: raw)
    assert "None of today's patterns has a stored outcome yet" in client.get("/stock/BBCA").text
    raw2 = _raw(tags=())
    raw2["scoring"], raw2["edge"] = [_scoring_row()], {}
    raw2["tags"] = []
    monkeypatch.setattr(stockmod, "load", lambda t: raw2)
    assert "no history to show" in client.get("/stock/BBCA").text


def test_the_last_ten_sessions_table_turns_into_cards_on_a_phone(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(stockmod, "load", lambda t: _page_raw())
    t = client.get("/stock/BBCA").text
    tbl = re.search(r'<table class="cards" aria-label="Last 10 sessions">.*?</table>', t, re.DOTALL).group(0)
    assert tbl.count('data-label="Close"') == 10 and 'class="key"' in tbl


# ---- /eval --------------------------------------------------------------------------------------------------------------------------

def _edge_data():
    return {"AKUM-ASING": {h: (200, 0.56, 0.009) for h in edge.HORIZONS}, "AKUM-NETRAL-WASH": {h: (150, 0.49, -0.002) for h in edge.HORIZONS}}


def test_the_evaluator_shows_expectancy_by_horizon_and_the_pure_vs_wash_matrix(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(edge, "load", lambda tags=None: _edge_data())
    t = client.get("/eval").text
    assert "Expectancy by horizon" in t and 'aria-label="Expectancy by horizon and pattern"' in t
    cmp_ = re.search(r'<div class="panel" id="pure-vs-wash">.*?</table>', t, re.DOTALL).group(0)
    assert "AKUM-ASING" in cmp_ and "AKUM-NETRAL-WASH" in cmp_ and "+1.10%" in cmp_ and cmp_.count("H+") == 4
    assert client.get("/track-record").status_code == 200


def test_a_failing_edge_query_never_takes_the_evaluator_down(client, monkeypatch):  # noqa: F811
    def boom(tags=None):
        raise RuntimeError("signal_log is busy")

    monkeypatch.setattr(edge, "load", boom)
    r = client.get("/eval")
    assert r.status_code == 200 and "Not available right now: RuntimeError: signal_log is busy" in r.text and "Track record" in r.text


def test_the_comparison_panel_says_when_one_pattern_has_no_outcomes(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(edge, "load", lambda tags=None: {"AKUM-ASING": _edge_data()["AKUM-ASING"]})
    assert "One of the two patterns has no stored outcome yet" in client.get("/eval").text


# ---- /system: EOD run time and the run log -----------------------------------------------------------------------------------------

EOD_LOG = """noise before the first run
===================== EOD 2026-11-04T19:15:01+07:00 =====================
-- window 2026-10-30 .. 2026-11-04 --
----- done 2026-11-04T19:26:38+07:00 -----
===================== EOD 2026-11-05T19:15:02+07:00 =====================
-- order-flow harvest took 412s (budget 15 min) --
----- done 2026-11-05T19:25:27+07:00 -----
===================== EOD 2026-11-06T19:15:00+07:00 =====================
-- order-flow harvest took 1020s (budget 15 min) --
"""


def test_the_eod_log_is_parsed_into_runs_with_totals_harvest_and_unfinished_runs():
    runs = eodlog.parse(EOD_LOG)
    assert [r["start"].day for r in runs] == [6, 5, 4]                                  # newest first
    assert runs[1]["seconds"] == 625 and runs[1]["harvest_s"] == 412 and runs[0]["seconds"] is None and runs[2]["harvest_s"] is None
    assert len(eodlog.parse(EOD_LOG, limit=2)) == 2 and eodlog.parse("nothing here") == []


def test_the_eod_view_compares_the_harvest_with_its_budget_and_turns_red_past_it():
    v = eodlog.view(eodlog.parse(EOD_LOG))
    assert v["total"] == "unfinished" and v["finished"] is False and v["harvest"] == "17 min 00 s" and v["harvest_pct"] == 113
    assert v["over"] is True and v["harvest_bar"] == 100 and v["budget"] == "15 min"
    ok = eodlog.view(eodlog.parse(EOD_LOG)[1:])
    assert ok["total"] == "10 min 25 s" and ok["harvest_pct"] == 46 and ok["over"] is False
    old = eodlog.view(eodlog.parse(EOD_LOG)[2:])
    assert old["harvest"] == "N/A" and old["harvest_pct"] is None and old["harvest_bar"] is None
    assert eodlog.view([]) is None


def test_the_log_is_read_from_the_tail_and_a_missing_file_is_not_an_error(tmp_path):
    f = tmp_path / "eod.log"
    f.write_text("x" * (eodlog.TAIL_BYTES + 10) + "\n" + EOD_LOG, encoding="utf-8")
    v = eodlog.load(f)
    assert v["rows"][0]["day"] == "Fri 06 Nov" and len(v["rows"]) == 3
    assert eodlog.load(tmp_path / "missing.log") is None


def _run(phase, status="ok", rows=10, err=None, minute=0, secs=45):
    t0 = datetime(2026, 11, 5, 19, minute, tzinfo=UTC)
    return (phase, date(2026, 11, 5), status, rows, err, t0, t0 + timedelta(seconds=secs))


def test_the_run_log_view_marks_problems_cuts_long_errors_and_filters():
    rows = [_run("collect_price"), _run("collect_broker", "error", 0, "x" * 300, 5, 130), _run("quality_gate", "no_data", None, None, 9)]
    v = runlog.view(rows)
    assert (v["total"], v["problems"], v["shown"], v["filter"]) == (3, 1, 3, "all")
    bad = next(r for r in v["rows"] if r["bad"])
    assert bad["took"] == "2 min 10 s" and bad["error"].endswith("…") and len(bad["error"]) == runlog.ERR_CHARS + 1 and len(bad["error_full"]) == 300
    ok = next(r for r in v["rows"] if r["phase"] == "quality_gate")
    assert ok["rows"] == "—" and ok["error"] == "" and ok["bad"] is False
    only = runlog.view(rows, "problems")
    assert only["shown"] == 1 and only["rows"][0]["phase"] == "collect_broker"
    assert runlog.view(rows, "evil")["filter"] == "all"                                 # junk in the address never breaks it


@pytest.fixture()
def health(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(webapp, "_health_view", lambda: {"level": "ok", "headline": "All systems normal", "checks": [], "progress": None, "last_deep": None,
                                                         "recent": [], "now": "05 Nov 2026 20:00", "last_report": None, "budget": 12000})
    webapp._HEALTH_CACHE.clear()
    monkeypatch.setattr(eodlog, "load", lambda path=None, limit=7: eodlog.view(eodlog.parse(EOD_LOG)[:2]))
    monkeypatch.setattr(runlog, "load", lambda limit=80: [_run("collect_price"), _run("collect_broker", "error", 0, "feed returned 502", 5)])
    return client


def test_the_system_page_shows_the_eod_time_the_harvest_budget_and_the_run_log(health):
    t = health.get("/system").text
    assert "Last EOD run" in t and "Order-flow harvest vs budget" in t and "17 min 00 s" in t and "over budget" in t
    assert 'role="progressbar"' in t and 'aria-valuenow="100"' in t and "Problem runs (last 2)" in t
    assert 'aria-label="Run log"' in t and "feed returned 502" in t and "Problems only (1)" in t and 'aria-label="Recent EOD run times"' in t
    only = health.get("/health?runs=problems").text
    assert "feed returned 502" in only and ">collect_price<" not in only.split('id="run-log"')[1]


def test_the_system_page_still_renders_when_the_log_or_the_table_is_unavailable(health, monkeypatch):
    monkeypatch.setattr(eodlog, "load", lambda path=None, limit=7: None)

    def boom(limit=80):
        raise RuntimeError("relation run_log does not exist")

    monkeypatch.setattr(runlog, "load", boom)
    r = health.get("/system")
    assert r.status_code == 200 and "eod.log" in r.text and "relation run_log does not exist" in r.text and "N/A" in r.text
