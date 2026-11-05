"""Home screener (`/`) and the top navigation bar. Pure functions are tested directly; the routes run with the database stubbed."""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from vg.web import app as webapp
from vg.web import freshness, glossary, home

SD = date(2026, 11, 5)


def _raw_row(ticker, tags="", bpi=None, overlap=None, ratio=None, cov=None, value=10_000_000_000, close=1000.0, prev=990.0):
    return {"ticker": ticker, "close": close, "prev": prev, "value": value, "quadrant": "AKUMULASI", "bpi3": bpi,
            "bpi_overlap3": overlap, "aggr_ratio": ratio, "aggr_coverage": cov, "tags": tags}


def _raw(rows=None, columns_present=True, last_scored=None):
    rows = rows if rows is not None else [
        _raw_row("BBCA", "AKUM-ASING", bpi=0.30, overlap=0, ratio=1.42, cov=0.9),
        _raw_row("RAJA", "AKUM-NETRAL-WASH, KSEI-INST-AKUM", bpi=0.22, overlap=2),
        _raw_row("PRDA", "MARKDOWN", bpi=-0.18, overlap=1),
        _raw_row("ZZZZ", "DISTRIBUSI-ASING"),                                     # tagged but never scored
    ]
    return {"session_date": SD, "rows": rows, "columns_present": columns_present, "last_scored": last_scored}


# ---- definitions -------------------------------------------------------------------------------------------

def test_accumulation_tags_come_from_the_glossary_and_exclude_the_wash_flag():
    assert home.WASH_TAG in glossary.TAGS                                         # the badge names a documented pattern
    assert home.WASH_TAG not in home.ACCUMULATION_TAGS
    assert {"AKUM-ASING", "BROKER2-ASING-AKUM", "KSEI-INST-AKUM", "INSIDER-AKUM"} <= home.ACCUMULATION_TAGS
    assert not {"MARKUP", "DISTRIBUSI-ASING", "TIKET-KECIL-MARKUP", "FOREIGN-INST"} & home.ACCUMULATION_TAGS
    assert home.ACCUMULATION_TAGS <= set(glossary.TAGS)


# ---- view --------------------------------------------------------------------------------------------------

def test_cards_count_accumulation_and_wash_separately_and_average_the_watchlist_bpi():
    v = home.build_view(_raw(), watchlist=("BBCA", "PRDA", "NOPE"))
    assert v["cards"]["acc"] == {"n": 2, "of": 4}                                 # BBCA (AKUM-ASING) + RAJA (KSEI-INST-AKUM)
    assert v["cards"]["wash"] == {"n": 1}
    avg = v["cards"]["avg"]
    assert avg["t"] == "+6.0 pts" and avg["c"] == "pos"                           # (0.30 - 0.18) / 2
    assert avg["sub"] == "2 of 3 watchlist stocks have a BPI."


def test_average_bpi_is_na_without_a_watchlist_or_without_scores_never_zero():
    empty = home.build_view(_raw(), watchlist=())["cards"]["avg"]
    assert empty["t"] == "N/A" and empty["c"] == "" and "empty" in empty["sub"]
    unscored = home.build_view(_raw(), watchlist=("ZZZZ",))["cards"]["avg"]
    assert unscored["t"] == "N/A" and "None of your 1" in unscored["sub"]


def test_bpi_cell_is_signed_and_coloured_and_na_when_null():
    rows = {r["ticker"]: r for r in home.build_view(_raw())["rows"]}
    assert rows["BBCA"]["bpi"] == {"t": "+30.0 pts", "c": "pos"}
    assert rows["PRDA"]["bpi"] == {"t": "-18.0 pts", "c": "neg"}
    assert rows["ZZZZ"]["bpi"] == {"t": "N/A", "c": ""} and rows["ZZZZ"]["bpi_bar"] == 0
    assert rows["BBCA"]["bpi_bar"] == 60 and rows["RAJA"]["bpi_bar"] == 44        # |bpi| / 0.5, rounded


def test_aggressor_ratio_is_na_when_null_and_a_number_otherwise():
    rows = {r["ticker"]: r for r in home.build_view(_raw())["rows"]}
    assert rows["BBCA"]["aggr"] == "1.42" and "90%" in rows["BBCA"]["aggr_note"]
    assert rows["RAJA"]["aggr"] == "N/A" and rows["RAJA"]["aggr_note"] == ""


def test_the_wash_flag_is_a_separate_badge_not_one_of_the_chips():
    rows = {r["ticker"]: r for r in home.build_view(_raw())["rows"]}
    assert rows["RAJA"]["wash"] is True and rows["RAJA"]["tag_list"] == ["KSEI-INST-AKUM"]
    assert rows["BBCA"]["wash"] is False


def test_notice_states_whether_scores_are_missing_and_where_they_stop():
    assert home.build_view(_raw())["notice"] is None
    unscored = [_raw_row("AAAA", "MARKUP"), _raw_row("BBBB", "AKUM-ASING")]
    n = home.build_view(_raw(unscored, last_scored=date(2026, 9, 3)))["notice"]
    assert "2026-11-05" in n and "2026-09-03" in n
    assert "no session has a score yet" in home.build_view(_raw(unscored))["notice"]
    absent = home.build_view(_raw(unscored, columns_present=False))["notice"]
    assert "vg init-db" in absent


def test_an_empty_session_still_builds():
    v = home.build_view(_raw([]))
    assert v["rows"] == [] and v["cards"]["acc"] == {"n": 0, "of": 0} and v["cards"]["avg"]["t"] == "N/A"


# ---- filter / sort / paging --------------------------------------------------------------------------------

def test_clean_replaces_unknown_values_with_defaults():
    p = home.clean({"q": "x" * 99, "view": "nope", "liquid": "yes", "wl": "1", "sort": "evil", "dir": "up", "page": "-4"})
    assert len(p["q"]) == 40 and p["view"] == "" and p["liquid"] == 0 and p["wl"] == 1
    assert p["sort"] == "bpi" and p["dir"] == "desc" and p["page"] == 1
    assert home.clean({"page": "abc"})["page"] == 1
    assert home.clean({"sort": "ticker"})["dir"] == "asc"                         # the column's natural direction


def test_default_order_is_bpi_high_to_low_with_unscored_stocks_last():
    rows = home.build_view(_raw())["rows"]
    assert [r["ticker"] for r in home.apply(rows, home.clean({}))] == ["BBCA", "RAJA", "PRDA", "ZZZZ"]
    asc = home.apply(rows, home.clean({"sort": "bpi", "dir": "asc"}))
    assert [r["ticker"] for r in asc] == ["PRDA", "RAJA", "BBCA", "ZZZZ"]         # N/A stays last in both directions


def test_filters_views_search_watchlist_and_liquidity():
    raw = _raw()
    raw["rows"][2]["value"] = 1_000_000_000                                       # PRDA is thin
    v = home.build_view(raw, watchlist=("PRDA",), holdings=("BBCA",))
    tickers = lambda **p: [r["ticker"] for r in home.apply(v["rows"], home.clean(p))]
    assert tickers(view="acc") == ["BBCA", "RAJA"]
    assert tickers(view="wash") == ["RAJA"]
    assert tickers(liquid="1") == ["BBCA", "RAJA", "ZZZZ"]
    assert tickers(wl="1") == ["BBCA", "PRDA"]                                    # watchlist or held
    assert tickers(q="accumulation") != [] and tickers(q="raja") == ["RAJA"]
    assert tickers(q="nothing-like-this") == []


def test_query_strings_keep_state_and_drop_defaults():
    p = home.clean({})
    assert home.query(p) == "?"
    assert home.query(p, view="wash") == "?view=wash"
    assert home.query(home.clean({"view": "acc", "page": "3"}), liquid=1) == "?view=acc&liquid=1"     # a filter change returns to page 1
    link = home.sort_link(p, "bpi")                                                # active column: next click reverses
    assert link["active"] and link["arrow"] == "▼" and "dir=asc" in link["href"]
    other = home.sort_link(p, "ticker")
    assert not other["active"] and "sort=ticker" in other["href"]


def test_chips_toggle_and_views_are_a_single_choice_group():
    chips = {c["label"]: c for c in home.chips(home.clean({"view": "wash"}))}
    assert chips["Wash alert"]["on"] and chips["Wash alert"]["href"] == "?"
    assert not chips["Accumulation"]["on"] and chips["Accumulation"]["href"] == "?view=acc"


# ---- load(): scoring columns not in the database yet --------------------------------------------------------

class _Cur:
    def __init__(self, have, log):
        self.have, self.log, self.description, self._rows = have, log, [], []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append(sql)
        if "information_schema" in sql:
            self._rows = [(c,) for c in self.have]
        elif "FROM price_bars" in sql:
            cols = ["ticker", "close", "prev", "value", "quadrant", *home.SCORE_COLS, "tags"]
            self.description = [(c,) for c in cols]
            self._rows = [("AAAA", Decimal(100), Decimal(99), Decimal(5_000_000_000), None,
                           *[(Decimal("0.2") if c in self.have else None) for c in home.SCORE_COLS], "MARKUP")]
        else:
            self._rows = [(date(2026, 9, 3),)]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0]


class _Conn(_Cur):
    def cursor(self):
        return self


def _patch_db(monkeypatch, have):
    import vg.db

    log = []
    monkeypatch.setattr(vg.db, "connect", lambda: _Conn(have, log))
    return log


def test_load_selects_null_for_columns_that_do_not_exist_yet(monkeypatch):
    log = _patch_db(monkeypatch, have=[])
    raw = home.load(SD)
    sql = next(s for s in log if "FROM price_bars" in s)
    assert "sc.bpi3" not in sql and "NULL AS bpi3" in sql and "NULL AS aggr_ratio" in sql
    assert raw["columns_present"] is False and raw["rows"][0]["bpi3"] is None
    assert home.build_view(raw)["rows"][0]["bpi"]["t"] == "N/A"                   # renders, as N/A


def test_load_reads_the_columns_when_they_exist_and_looks_up_the_last_scored_session_only_if_needed(monkeypatch):
    log = _patch_db(monkeypatch, have=list(home.SCORE_COLS))
    raw = home.load(SD)
    sql = next(s for s in log if "FROM price_bars" in s)
    assert "sc.bpi3" in sql and "OR sc.bpi3 IS NOT NULL" in sql and "NULL AS" not in sql
    assert raw["columns_present"] is True and raw["rows"][0]["bpi3"] == pytest.approx(0.2) and raw["last_scored"] is None
    assert not any("max(session_date)" in s for s in log)                          # this session has scores: no extra query
    assert isinstance(raw["rows"][0]["close"], float)                              # Decimal -> float


def test_load_only_ever_runs_select_statements(monkeypatch):
    log = _patch_db(monkeypatch, have=[])
    home.load(SD)
    log2 = _patch_db(monkeypatch, have=list(home.SCORE_COLS))
    home.load(SD)
    assert log and log2 and all(s.lstrip().upper().startswith("SELECT") for s in (*log, *log2))


# ---- routes ------------------------------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    eod = date(2026, 10, 30)
    days = [eod - timedelta(days=i) for i in range(30) if (eod - timedelta(days=i)).weekday() < 5]
    monkeypatch.setattr(freshness, "load_inputs", lambda today=None: freshness.FreshInputs(
        today=date(2026, 10, 31), eod_date=eod, price_counts={d: 962 for d in days},
        broker_counts={d: 190 for d in days}, ksei_month_end=date(2026, 9, 30)))
    monkeypatch.setattr(webapp, "_eval_version", lambda: ("ts", 1))
    monkeypatch.setattr(webapp.engine, "eval_tags", lambda since=None: [])
    monkeypatch.setattr(webapp.engine, "tag_monthly_edge", lambda since=None: {})
    monkeypatch.setattr(webapp.engine, "latest_signal_date", lambda: SD)
    monkeypatch.setattr(home, "load", lambda sd: _raw())
    monkeypatch.setattr(webapp.CONFIG, "watchlist", ("BBCA", "PRDA"), raising=False)
    monkeypatch.setattr(webapp.CONFIG, "holdings", (), raising=False)

    def unavailable(*a, **k):
        raise RuntimeError("stubbed: no database in this test")

    monkeypatch.setattr(webapp.sysstatus, "gather", unavailable)                  # /health, /system -> error panel inside the shell
    monkeypatch.setattr(webapp.cov, "load_counts", unavailable)                   # /coverage
    monkeypatch.setattr(webapp, "_risk_snapshot", lambda: (None, None))
    monkeypatch.setattr(webapp, "_research_snapshot", lambda: (None, None))
    for cache in (webapp._STRIP_CACHE, webapp._EVAL_CACHE, webapp._HOME_CACHE, webapp._HEALTH_CACHE, webapp._COVERAGE_CACHE):
        cache.clear()
    return TestClient(webapp.app)


def test_home_is_the_screener_with_three_header_cards_and_the_scored_table(client):
    r = client.get("/")
    assert r.status_code == 200
    t = r.text
    for needle in ("Total accumulation", "Wash trading alert", "Average BPI, watchlist", "+6.0 pts", "session 2026-11-05",
                   'aria-label="Stock screener"', "+30.0 pts", "-18.0 pts", 'href="/stock/BBCA"', "Not investment advice"):
        assert needle in t, needle
    cards = re.search(r'<section class="grid kpis".*?</section>', t, re.DOTALL).group(0)
    assert re.search(r'<div class="k">Total accumulation</div>\s*<div class="v">2</div>', cards)
    assert re.search(r'<div class="k">Wash trading alert</div>\s*<div class="v">1</div>', cards)


def test_home_colours_bpi_badges_the_wash_stock_and_shows_na(client):
    t = client.get("/").text
    assert re.search(r'class="bpi pos">\+30\.0 pts', t) and re.search(r'class="bpi neg">-18\.0 pts', t)
    assert t.count('class="badge wash"') == 1 and 'href="/pattern/AKUM-NETRAL-WASH"' in t
    assert re.search(r'<tr role="row" class="is-wash">\s*<td[^>]*>.*?RAJA', t, re.DOTALL)
    assert re.search(r'data-label="Aggressor ratio" class="na">N/A<', t)            # null -> N/A
    assert 'data-label="Aggressor ratio" title="90% of the volume was classified">1.42<' in t
    assert "None" not in re.search(r"<tbody.*?</tbody>", t, re.DOTALL).group(0)


def test_home_filters_and_sorting_work_through_the_address(client):
    wash = client.get("/?view=wash").text
    body = re.search(r"<tbody.*?</tbody>", wash, re.DOTALL).group(0)
    assert "RAJA" in body and "BBCA" not in body and "(filtered from 4)" in wash and "Clear filters" in wash
    asc = re.search(r"<tbody.*?</tbody>", client.get("/?sort=ticker&dir=asc").text, re.DOTALL).group(0)
    assert asc.index("BBCA") < asc.index("PRDA") < asc.index("RAJA")
    assert client.get("/?view=bogus&sort=evil&page=zzz").status_code == 200       # junk in the address never breaks the page


def test_home_says_so_when_the_session_is_not_scored(client, monkeypatch):
    monkeypatch.setattr(home, "load", lambda sd: _raw([_raw_row("AAAA", "MARKUP")], last_scored=date(2026, 9, 3)))
    t = client.get("/").text
    assert "newest scored session is 2026-09-03" in t and 'class="answer notice"' in t
    assert "N/A" in t


def test_home_handles_no_signals_and_database_failure(client, monkeypatch):
    monkeypatch.setattr(webapp.engine, "latest_signal_date", lambda: None)
    assert "No signals yet" in client.get("/").text

    def boom(sd):
        raise RuntimeError("db down")

    monkeypatch.setattr(webapp.engine, "latest_signal_date", lambda: SD)
    monkeypatch.setattr(home, "load", boom)
    r = client.get("/")
    assert r.status_code == 200 and "Cannot read the data" in r.text and "db down" in r.text and "Not investment advice" in r.text


def test_home_view_is_cached_per_signal_date_and_fresh_rebuilds_it(client, monkeypatch):
    calls = []
    monkeypatch.setattr(home, "load", lambda sd: (calls.append(sd), _raw())[1])
    client.get("/")
    second = client.get("/?sort=price")                                           # other sort, same data: no reload
    assert len(calls) == 1 and "kept for up to 5 minutes" in second.text
    client.get("/?fresh=1")
    assert len(calls) == 2
    monkeypatch.setattr(webapp.engine, "latest_signal_date", lambda: SD + timedelta(days=1))
    client.get("/")
    assert len(calls) == 3


# ---- top navigation bar -----------------------------------------------------------------------------------

def _top_links(html):
    nav = re.search(r'<nav aria-label="Primary sections">(.*?)</nav>', html, re.DOTALL).group(1)
    return re.findall(r'<a href="([^"]+)"( aria-current="true")?>([^<]+)</a>', nav)


def test_top_bar_lists_the_four_sections_in_order(client):
    links = _top_links(client.get("/").text)
    assert [(h, label) for h, _c, label in links] == [("/", "Screener"), ("/stock/BBCA", "Stock Detail"),
                                                     ("/eval", "Signal Evaluator"), ("/system", "System Monitor")]


@pytest.mark.parametrize("path,section", [("/", "/"), ("/today", "/"), ("/screener", "/"), ("/ranking", "/"),
                                          ("/stock/BBCA", "/stock/BBCA"), ("/track-record", "/eval"), ("/eval", "/eval"),
                                          ("/risk-test", "/eval"), ("/research", "/eval"),
                                          ("/health", "/system"), ("/system", "/system"), ("/coverage", "/system")])
def test_top_bar_highlights_exactly_the_current_section(client, monkeypatch, path, section):
    from vg.web import stock as stockmod

    monkeypatch.setattr(stockmod, "load", lambda t: None)                         # /stock/BBCA -> 404 page; still inside the shell
    r = client.get(path)
    assert r.status_code in (200, 404)
    current = [h for h, c, _l in _top_links(r.text) if c]
    assert current == [section], (path, current)


def test_top_bar_does_not_add_a_second_aria_current_page(client):
    assert len(re.findall(r'<a [^>]*aria-current="page"[^>]*>', client.get("/").text)) == 1     # still only the sidebar's


@pytest.mark.parametrize("alias,canonical", [("/eval", "/track-record"), ("/system", "/health")])
def test_aliases_serve_the_same_screen_as_their_canonical_address(client, alias, canonical):
    a, c = client.get(alias), client.get(canonical)
    assert a.status_code == c.status_code == 200
    title = lambda h: re.search(r"<h1>(.*?)</h1>", h, re.DOTALL).group(1).strip()
    assert title(a.text) == title(c.text)
    assert 'aria-current="page"' in a.text and f'href="{canonical}"' in a.text      # the sidebar still marks the canonical screen


def test_the_pages_without_a_top_bar_context_still_render():
    from pathlib import Path

    import jinja2

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(Path(webapp.__file__).parent / "templates")),
                             autoescape=jinja2.select_autoescape(["html"]))
    glossary.install(env)
    out = env.get_template("screener.html").render(active_page="screener", error=None, session_date=None, rows=[])
    assert "<header class=\"topnav\">" not in out and "<body>" in out
