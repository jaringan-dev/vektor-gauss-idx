"""The four analysis pages: balance position, seasonality, broker stalker, broker inventory."""
from __future__ import annotations

import itertools
import re
from datetime import date, timedelta

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from vg.web import app as webapp
from vg.web import balance, charts, inventory, seasonal, stalker

from .test_web_ios_ui import client  # noqa: F401 — the shared page-test fixture

# ---- seasonality -------------------------------------------------------------------------------------------------------------

DOW = [{"label": d, "n": 100, "pct_up": p, "pct_down": min(1 - p, .40), "mean_ret": m} for d, p, m in
       (("Mon", .495, -.001), ("Tue", .552, .001), ("Wed", .525, 0.0), ("Thu", .528, .0005), ("Fri", .546, .0002))]
MON = [{"label": m, "n": n, "pct_up": p, "pct_down": None if p is None else 1 - p, "mean_ret": r} for m, n, p, r in
       (("Jan", 12, .45, -.001), ("Feb", 3, .6, .02), ("Mar", 0, None, None))]


def test_seasonal_inputs_are_cleaned():
    assert seasonal.clean_ticker(" bbca ") == "BBCA" and seasonal.clean_ticker("ihsg") == "COMPOSITE"
    for bad in ("", None, "TOOLONG", "B B", "BB;X", "<b>"):
        assert seasonal.clean_ticker(bad) == "COMPOSITE"
    assert seasonal.clean_years("10") == 10 and seasonal.clean_years("7") == 12 and seasonal.clean_years("x") == 12 and seasonal.clean_years(None) == 12


def test_seasonal_cells_print_the_numbers_mark_thin_months_and_scale_the_heat():
    cells = seasonal._cells(MON)
    assert cells[0]["flat"] == "0.0%" and seasonal._cells(DOW)[0]["flat"] == "10.5%" and seasonal._cells(DOW)[0]["down"] == "40.0%"       # unchanged days are their own row
    assert cells[0]["up"] == "45.0%" and cells[0]["down"] == "55.0%" and cells[0]["exp"] == "-0.10%" and not cells[0]["thin"]
    assert cells[1]["thin"] and cells[1]["heat"] == 1.0                              # the largest average in the table
    assert cells[0]["heat"] == 0.05
    assert cells[2]["empty"] and cells[2]["up"] == "—" and cells[2]["exp"] == "—" and cells[2]["heat"] == 0.0
    assert [c["cls"] for c in seasonal._cells(DOW)] == ["neg", "pos", "", "pos", "pos"]


def test_seasonal_view_says_when_less_history_exists_than_asked():
    res = {"day_of_week": DOW, "month": MON, "n_days": 1650, "span": ("2020-01-02", "2026-11-06")}
    v = seasonal.build_view(res, "BBCA", 12)
    assert v["has_data"] and v["short"] and v["covered"] == 6.8
    assert not seasonal.build_view(res, "BBCA", 5)["short"]
    assert not seasonal.build_view({"day_of_week": [], "month": [], "n_days": 0, "span": None}, "ZZZZ", 5)["has_data"]


@pytest.fixture()
def page(client, monkeypatch):  # noqa: F811
    webapp._ANALYSIS_CACHE.clear()
    res = {"day_of_week": DOW, "month": MON, "n_days": 1650, "span": ("2020-01-02", "2026-11-06")}
    monkeypatch.setattr(seasonal, "load", lambda t, y: res)
    return client


def test_seasonality_page_shows_both_tables_and_the_short_history_notice(page):
    r = page.get("/seasonality?ticker=BBCA&years=12")
    assert r.status_code == 200
    t = r.text
    assert "Weekday seasonality of BBCA" in t and "Month seasonality of BBCA" in t and "Less history than asked" in t and ">thin<" in t
    assert 'aria-current="page"' in t and 'href="/seasonality"' in t
    assert page.get("/seasonality?ticker=<script>&years=99").status_code == 200       # junk falls back, never errors or echoes
    assert 'value="<script>"' not in page.get("/seasonality?ticker=<script>").text and 'value="COMPOSITE"' in page.get("/seasonality?ticker=<script>").text


def test_seasonality_with_no_history_and_a_failing_database_are_shown(page, monkeypatch):
    monkeypatch.setattr(seasonal, "load", lambda t, y: {"day_of_week": [], "month": [], "n_days": 0, "span": None})
    webapp._ANALYSIS_CACHE.clear()
    assert "No price history for ZZZZ" in page.get("/seasonality?ticker=ZZZZ").text

    def boom(t, y):
        raise RuntimeError("db down")

    monkeypatch.setattr(seasonal, "load", boom)
    webapp._ANALYSIS_CACHE.clear()
    assert "db down" in page.get("/seasonality?ticker=BBCA").text


# ---- balance position --------------------------------------------------------------------------------------------------------

def months(n=5):
    out = []
    for i in range(n):
        d = date(2026, min(6 + i, 12), 28)
        out.append((d, {"local": {"ID": 100e9 + i * 1e9, "MF": 20e9, "IB": 5e9, "CP": 3e9}, "foreign": {"MF": 40e9, "IB": 10e9, "CP": 2e9},
                        "scripless_pct": 0.425501}))
    return out


def test_balance_inputs_and_view():
    assert balance.clean_ticker("enrg") == "ENRG" and balance.clean_ticker("zz") == "BBCA" and balance.clean_ticker("COMPOSITE") == "BBCA"
    assert balance.clean_months("24") == 24 and balance.clean_months("5") == 36 and balance.clean_months(None) == 36
    v = balance.build_view(months(), "BBCA", 36)
    assert v["own_chart"] and "<svg" in v["own_chart"]["chart"] and v["own_chart"]["scripless"] == "42.6%"
    assert balance.build_view([], "BBCA", 36)["own_chart"] is None


def test_balance_page(client, monkeypatch):  # noqa: F811
    webapp._ANALYSIS_CACHE.clear()
    monkeypatch.setattr(balance, "load", lambda t, m: months())
    r = client.get("/balance?ticker=BBCA&months=24")
    assert r.status_code == 200 and "BBCA: ownership by month" in r.text and "held scripless" in r.text and "42.6%" in r.text
    monkeypatch.setattr(balance, "load", lambda t, m: [])
    webapp._ANALYSIS_CACHE.clear()
    assert "No KSEI ownership detail for ZZZZ" in client.get("/balance?ticker=ZZZZ").text


# ---- broker stalker ----------------------------------------------------------------------------------------------------------

BROKERS = [{"code": "AK", "foreign": True, "name": "UBS"}, {"code": "YU", "foreign": True, "name": ""}, {"code": "PD", "foreign": False, "name": "INDO PREMIER"}]
ROWS = [("BBCA", 790e9, 401e9, 159e6, 80e6), ("BBRI", 252e9, 395e9, 90e6, 140e6), ("ZERO", 1e9, 1e9, 1e6, 1e6), ("NOVOL", 5e9, 0, 0, 0)]


def test_stalker_inputs():
    assert stalker.clean_broker(" yu ") == "YU" and stalker.clean_broker("YUU") is None and stalker.clean_broker("") is None and stalker.clean_broker("1A") is None
    assert stalker.clean_window("10") == 10 and stalker.clean_window("7") == 5 and stalker.clean_window(None) == 5


def test_stalker_shapes_net_and_average_prices():
    rows = {r["ticker"]: r for r in stalker.shape(ROWS)}
    assert rows["BBCA"]["net"] == pytest.approx(389e9) and rows["BBCA"]["avg_buy"] == pytest.approx(790e9 / 159e6)
    assert rows["BBRI"]["net"] < 0 and rows["NOVOL"]["avg_buy"] is None and rows["NOVOL"]["avg_sell"] is None


def stalk_raw(**kw):
    return {"days": [date(2026, 11, 2), date(2026, 11, 6)], "rows": ROWS, "n_pairs": 10, "n_complete": 10, **kw}


def test_stalker_view_splits_buys_and_sells_and_skips_flat_stocks():
    v = stalker.build_view(stalk_raw(), BROKERS, "YU", 5, False)
    assert [r["ticker"] for r in v["buys"]] == ["BBCA", "NOVOL"] and [r["ticker"] for r in v["sells"]] == ["BBRI"]
    assert v["n_stocks"] == 4 and "ZERO" not in [r["ticker"] for r in v["buys"] + v["sells"]]
    assert v["buys"][0]["net"].startswith("+389") and v["sells"][0]["net"].startswith("-143")
    assert v["first"] == date(2026, 11, 2) and v["short"] and not v["incomplete"] and not v["partial"]


def test_stalker_caps_the_tables_and_narrows_the_picker():
    many = [(f"S{i:03d}", (i + 1) * 1e9, 0, 1e6, 0) for i in range(30)]
    v = stalker.build_view(stalk_raw(rows=many), BROKERS, "AK", 5, True)
    assert len(v["buys"]) == 20 and v["n_buys"] == 30 and v["buys"][0]["ticker"] == "S029"
    assert [b["code"] for b in v["brokers"]] == ["AK", "YU"]                           # foreign houses only
    assert v["brokers"][0]["label"] == "AK · UBS" and v["brokers"][1]["label"] == "YU"


def test_stalker_an_unknown_broker_or_none_shows_nothing():
    assert stalker.build_view(stalk_raw(), BROKERS, "QQ", 5, False)["broker"] is None
    v = stalker.build_view({"days": [], "rows": []}, BROKERS, None, 5, False)
    assert v["buys"] == [] and v["sells"] == [] and v["total_net"] is None


def test_stalker_says_when_no_stock_day_has_a_complete_table_and_when_only_some_do():
    inc = stalker.build_view(stalk_raw(rows=[], n_pairs=963, n_complete=0), BROKERS, "YU", 5, False)
    assert inc["incomplete"] and not inc["partial"]
    thin = stalker.build_view(stalk_raw(n_pairs=880, n_complete=82), BROKERS, "YU", 5, False)         # the real 5 Nov figure: a few small stocks only
    assert thin["incomplete"] and not thin["partial"]
    part = stalker.build_view(stalk_raw(n_pairs=100, n_complete=70), BROKERS, "YU", 5, False)
    assert part["partial"] and not part["incomplete"]


class Cur:
    """A fake cursor that records every statement and answers from a script."""

    def __init__(self, answers):
        self.answers, self.sqls = list(answers), []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sqls.append((sql, params))
        self.cur_answer = self.answers.pop(0)

    def fetchall(self):
        return self.cur_answer

    def fetchone(self):
        return self.cur_answer[0] if isinstance(self.cur_answer, list) else self.cur_answer


class Conn:
    def __init__(self, cur):
        self._cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self._cur


def test_stalker_load_reads_nothing_per_broker_when_no_table_is_complete(monkeypatch):
    cur = Cur([[(date(2026, 11, 6),), (date(2026, 11, 5),)], (963, 82)])
    monkeypatch.setattr("vg.db.connect", lambda: Conn(cur))
    out = stalker.load("YU", 5)
    assert out["n_pairs"] == 963 and out["n_complete"] == 82 and out["rows"] == [] and len(cur.sqls) == 2
    assert all(s.lstrip().upper().startswith(("SELECT", "WITH")) for s, _p in cur.sqls)


def test_stalker_load_joins_only_complete_stock_days(monkeypatch):
    cur = Cur([[(date(2026, 11, 6),)], (963, 800), [("BBCA", 1, 2, 3, 4)]])
    monkeypatch.setattr("vg.db.connect", lambda: Conn(cur))
    out = stalker.load("YU", 1)
    assert out["rows"] == [("BBCA", 1, 2, 3, 4)] and out["n_complete"] == 800
    sql = cur.sqls[-1][0]
    assert "HAVING" in sql and "JOIN ok" in sql and "0.01" in sql


def test_stalker_page(client, monkeypatch):  # noqa: F811
    webapp._ANALYSIS_CACHE.clear()
    monkeypatch.setattr(stalker, "load_brokers", lambda: BROKERS)
    monkeypatch.setattr(stalker, "load", lambda b, w: stalk_raw())
    assert "Pick a broker" in client.get("/stalker").text
    t = client.get("/stalker?broker=YU&window=5").text
    assert "Stocks YU bought, net" in t and "Stocks YU sold, net" in t and "BBCA" in t and "BBRI" in t
    assert "Foreign brokers only" in t
    monkeypatch.setattr(stalker, "load", lambda b, w: stalk_raw(rows=[], n_pairs=963, n_complete=0))
    webapp._ANALYSIS_CACHE.clear()
    assert "stored broker tables are incomplete" in client.get("/stalker?broker=YU").text


# ---- broker inventory --------------------------------------------------------------------------------------------------------

D = [date(2026, 11, 2) + timedelta(days=i) for i in range(8) if (date(2026, 11, 2) + timedelta(days=i)).weekday() < 5]       # 6 weekdays


def flow(day, code, flag, bval, sval, bvol=0, svol=0):
    return (day, code, flag, bval, sval, bvol, svol)


ROWS_INV = [
    flow(D[0], "AA", "F", 100e9, 0, 20e6, 0), flow(D[1], "AA", "F", 50e9, 20e9, 10e6, 4e6), flow(D[2], "AA", "F", 0, 10e9, 0, 2e6),
    flow(D[0], "BB", "D", 0, 60e9, 0, 12e6), flow(D[3], "BB", "D", 10e9, 5e9, 2e6, 1e6),
    flow(D[1], "CC", "D", 30e9, 0, 6e6, 0),
]


def test_inventory_inputs():
    assert inventory.clean_ticker("enrg") == "ENRG" and inventory.clean_ticker("x") == "BBCA"
    assert inventory.clean_range("1y") == "1Y" and inventory.clean_range("zz") == "3M" and inventory.clean_range(None) == "3M"
    assert inventory.clean_unit("lot") == "lot" and inventory.clean_unit("x") == "value"
    assert inventory.clean_investor("foreign") == "foreign" and inventory.clean_investor("x") == "all"


def test_inventory_running_net_is_cumulative_from_zero_and_a_quiet_day_adds_nothing():
    s = inventory.running(D, ROWS_INV, unit="value", investor="all")
    assert s["AA"]["vals"] == [100e9, 130e9, 120e9, 120e9, 120e9, 120e9][: len(D)]
    assert s["BB"]["vals"][0] == -60e9 and s["BB"]["vals"][3] == -55e9 and s["BB"]["side"] == "D"


def test_inventory_lot_unit_and_investor_filter():
    lots = inventory.running(D, ROWS_INV, unit="lot", investor="all")
    assert lots["AA"]["vals"][0] == 20e6 / 100
    only_f = inventory.running(D, ROWS_INV, unit="value", investor="foreign")
    assert set(only_f) == {"AA"}
    assert set(inventory.running(D, ROWS_INV, unit="value", investor="domestic")) == {"BB", "CC"}


def test_inventory_picks_the_biggest_buyers_and_sellers_in_order():
    s = inventory.running(D, ROWS_INV, unit="value", investor="all")
    assert inventory.pick(s) == (["AA", "CC"], ["BB"])
    many = {f"B{i}": {"side": "F", "vals": [float(i + 1)]} for i in range(9)} | {f"S{i}": {"side": "D", "vals": [-float(i + 1)]} for i in range(9)}
    buyers, sellers = inventory.pick(many)
    assert buyers == ["B8", "B7", "B6", "B5", "B4"] and sellers == ["S8", "S7", "S6", "S5", "S4"]


def inv_raw(**kw):
    return {"days": D, "rows": ROWS_INV, "closes": {d: 1000.0 + i * 10 for i, d in enumerate(D)}, "n_all": len(D), **kw}


def test_inventory_view_draws_solid_buyers_dashed_sellers_and_lists_them():
    v = inventory.build_view(inv_raw(), "BBCA", "3M", "value", "all")
    assert v["has_data"] and v["chart"] and v["chart_m"] and "stroke-dasharray=\"5 3\"" in v["chart"]
    assert [r["code"] for r in v["table"]] == ["AA", "CC", "BB"] and v["table"][2]["dash"] and not v["table"][0]["dash"]
    assert v["table"][0]["net"] == "+120.0B" and v["table"][2]["net"] == "-55.0B"
    assert v["table"][0]["side"] == "Foreign" and v["table"][2]["side"] == "Domestic"
    assert "Net buyers: AA +120B" in v["chart"] and "Net sellers: BB -55B" in v["chart"]


def test_inventory_chart_needs_five_days_and_something_to_draw():
    line = {"code": "AA", "kind": "buy", "color": "#fff", "vals": [1.0, 2.0, 3.0]}
    assert charts.inventory_chart(D[:3], [line], [1.0, 2.0, 3.0]) is None
    assert charts.inventory_chart(D, [], [1.0] * len(D)) is None
    svg = charts.inventory_chart(D, [{**line, "vals": [1.0] * len(D)}], [None] * len(D))
    assert svg and "<polyline" in svg and ">price<" not in svg and "closing price" not in svg          # no closes -> no price panel


def test_inventory_end_labels_do_not_overlap():
    lines = [{"code": f"B{i}", "kind": "buy", "color": "#fff", "vals": [0.0, 0.0, 0.0, 0.0, 100.0]} for i in range(5)]
    svg = charts.inventory_chart(D[:5], lines, [1.0] * 5)
    ys = sorted(float(y) for y in re.findall(r'<text x="[\d.]+" y="([\d.]+)" fill="#fff" font-weight="700"', svg))
    assert len(ys) == 5 and all(b - a >= 10.9 for a, b in itertools.pairwise(ys))


def test_inventory_view_without_complete_days():
    assert inventory.build_view({"days": [], "rows": [], "closes": {}, "n_all": 363}, "BBCA", "3M", "value", "all")["incomplete"]
    none = inventory.build_view({"days": [], "rows": [], "closes": {}, "n_all": 0}, "ZZZZ", "3M", "value", "all")
    assert not none["incomplete"] and not none["has_data"]


def test_inventory_load_uses_only_complete_days(monkeypatch):
    cur = Cur([[(D[0],), (D[1],)], [(D[0],), (D[1],)], [ROWS_INV[0]], [(D[0], 1000.0)]])
    monkeypatch.setattr("vg.db.connect", lambda: Conn(cur))
    out = inventory.load("BBCA", "3M")
    assert out["days"] == [D[0], D[1]] and out["n_all"] == 2 and out["closes"] == {D[0]: 1000.0}
    day_sql = cur.sqls[1][0]
    assert "HAVING" in day_sql and "0.01" in day_sql and cur.sqls[0][1] == ("BBCA", 63)
    assert all(s.lstrip().upper().startswith("SELECT") for s, _p in cur.sqls)


def test_inventory_load_shows_nothing_when_only_a_few_quiet_days_are_complete(monkeypatch):
    period = [(D[i],) for i in range(5)]
    cur = Cur([period, [(D[0],)]])                                          # 1 of 5 days complete: well under 80%
    monkeypatch.setattr("vg.db.connect", lambda: Conn(cur))
    out = inventory.load("BRNA", "1Y")
    assert out["days"] == [] and out["n_all"] == 5 and out["rows"] == [] and len(cur.sqls) == 2
    assert inventory.build_view(out, "BRNA", "1Y", "value", "all")["incomplete"]
    cur2 = Cur([period, [(D[i],) for i in range(4)], [], []])               # 4 of 5 = 80%: enough
    monkeypatch.setattr("vg.db.connect", lambda: Conn(cur2))
    assert len(inventory.load("BRNA", "1Y")["days"]) == 4


def test_inventory_page(client, monkeypatch):  # noqa: F811
    webapp._ANALYSIS_CACHE.clear()
    monkeypatch.setattr(inventory, "load", lambda t, r: inv_raw())
    t = client.get("/inventory?ticker=BBCA&range=3M&unit=value&investor=all").text
    assert "Biggest net buyers and sellers of BBCA" in t and "Net buyer" in t and "Net seller" in t
    webapp._ANALYSIS_CACHE.clear()
    monkeypatch.setattr(inventory, "load", lambda t, r: {"days": [], "rows": [], "closes": {}, "n_all": 363})
    assert "stored broker tables are incomplete" in client.get("/inventory?ticker=BBCA").text
    webapp._ANALYSIS_CACHE.clear()
    monkeypatch.setattr(inventory, "load", lambda t, r: {"days": [], "rows": [], "closes": {}, "n_all": 0})
    assert "No broker data for ZZZZ" in client.get("/inventory?ticker=ZZZZ").text


def test_the_sidebar_lists_the_four_pages_under_analysis(client):  # noqa: F811
    t = client.get("/seasonality").text
    for href in ("/balance", "/seasonality", "/stalker", "/inventory"):
        assert f'href="{href}"' in t
    assert "Analysis" in t
