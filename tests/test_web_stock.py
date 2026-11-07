"""Stock detail page: pure view-building, the plain-language summary, and the routes (DB stubbed)."""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from vg.web import app as webapp  # noqa: E402
from vg.web import freshness, narrative  # noqa: E402
from vg.web import stock as stockmod  # noqa: E402

LATEST = date(2026, 11, 3)


def _raw(atr=120, tags=("BROKER2-ASING-AKUM",)):
    D = Decimal
    days = [LATEST - timedelta(days=i) for i in range(12)]
    bars = [(d, D(4840), D(4980), D(5000), D(4800), D(1000000), D(250_000_000_000)) for d in days]
    comp = (LATEST, D(-24_400_000_000), D(486_600_000_000), "AKUMULASI", 2, None if atr is None else D(atr), D("0.62"),
            D(154_700_000_000), D(-15_000_000_000), D(-1_000_000_000), "up", D("-0.028"))
    comps = [comp] + [(d, D(1), D(2), "AKUMULASI", 5, D(100), None, None, None, None, "up", D(0)) for d in days[1:]]
    return {
        "ticker": "BBCA", "meta": ("Bank Central Asia", "Financials"), "bars": bars, "comps": comps,
        "tags": [(LATEST, t, None, None) for t in tags] + [(LATEST - timedelta(days=3), "AKUM-ASING", D("0.01"), None)],
        "levels": [("B", 1, "YP", "Mirae", D(90_000_000_000)), ("S", 1, "CC", "Mandiri", D(80_000_000_000))],
        "levels_day": LATEST, "ownership": [(date(2026, 9, 30), D("0.31"), D("0.12"), D("0.49"))],
        "corp": [(date(2026, 8, 1), "cash_dividend", None, "Rp 125")], "latest": LATEST,
    }


TRACK = {"BROKER2-ASING-AKUM": {"code": "failing", "label": "FAIL — no edge", "n": 1105, "small_n": False,
                                "edge_h5": {"t": "+0.15%", "c": "pos"}, "edge_after_cost": {"t": "-1.55%", "c": "neg"}}}


def test_view_formats_the_page_numbers():
    v = stockmod.build_view(_raw(), TRACK)
    assert v["close"] == "4,840" and v["chg"]["t"] == "-2.8%" and v["chg"]["c"] == "neg"
    assert v["comp"]["nff_d0"] == "-24.4B" and v["comp"]["nff_w1"] == "+486.6B" and v["comp"]["cons"] == "2/10"
    assert v["comp"]["quad_word"] == "Accumulation" and v["comp"]["quad_key"] == "q_accumulation"
    assert v["comp"]["bounds"] == "-6.0% and -0.4% of traded value"
    assert v["levels_day"] == "2026-11-03" and v["buyers"][0]["code"] == "YP" and v["sellers"][0]["code"] == "CC"


def test_summary_describes_and_never_advises():
    v = stockmod.build_view(_raw(), TRACK)
    text = " ".join(v["summary"])
    assert "BBCA closed at 4,840, down 2.8%" in text
    assert "net sellers of Rp24.4 billion" in text and "net buyers of Rp486.6 billion" in text
    assert "classed as Accumulation" in text
    assert "1 pattern apply to it today; none of them has passed" in text
    assert text.endswith("It is not a recommendation.")
    for phrase in narrative.FORBIDDEN:
        assert phrase not in text.lower()


def test_passed_patterns_are_counted_in_the_summary():
    track = {"BROKER2-ASING-AKUM": dict(TRACK["BROKER2-ASING-AKUM"], code="ready", label="PASS")}
    assert "1 of the patterns being tracked have passed" in " ".join(stockmod.build_view(_raw(), track)["summary"])


def test_tags_today_join_the_forward_test_verdict_and_unknown_tags_survive():
    v = stockmod.build_view(_raw(tags=("BROKER2-ASING-AKUM", "BRAND-NEW-TAG")), TRACK)
    by = {t["code"]: t for t in v["tags_today"]}
    assert by["BROKER2-ASING-AKUM"]["track"]["code"] == "failing"
    assert by["BRAND-NEW-TAG"]["track"] is None and by["BRAND-NEW-TAG"]["name"] == "BRAND-NEW-TAG"
    assert [h["date"] for h in v["history"]] == ["2026-11-03", "2026-10-31"]


def test_risk_plan_and_lot_size_come_from_atr_and_the_chosen_capital():
    v = stockmod.build_view(_raw(atr=120), TRACK, balance=100_000_000, risk_pct=0.01)
    r = v["risk"]
    assert r["stop"] == "4,600" and r["risk_pct"] == "5.0%" and r["tick"] == 10
    assert [t["price"] for t in r["targets"]] == ["5,075", "5,325", "5,550"]
    assert r["lots"]["lots"] == 37 and r["lots"]["shares"] == "3,700"
    assert stockmod.build_view(_raw(atr=120), TRACK)["risk"]["lots"] is None      # no capital typed yet


def test_missing_atr_means_no_risk_plan_instead_of_a_made_up_one():
    v = stockmod.build_view(_raw(atr=None), TRACK, balance=1e8)
    assert v["risk"] is None and v["has_atr"] is False


def test_narrative_handles_sparse_input():
    out = narrative.stock_summary({"ticker": "ZZZZ"})
    assert out == ["This describes data. It is not a recommendation."]
    flat = narrative.stock_summary({"ticker": "ZZZZ", "close": 100, "chg": 0.0, "nff_d0": 0, "quadrant": "NETRAL"})
    assert "unchanged" in flat[0] and "flat (zero net flow)" in flat[1]


# ---- routes ------------------------------------------------------------------------------------

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


def test_stock_page_renders_the_explanations_the_plan_calls_for(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: _raw() if t == "BBCA" else None)
    r = client.get("/stock/bbca?capital=100&risk_pct=1")
    assert r.status_code == 200
    for needle in ("What happened to BBCA?", "Bank Central Asia", "classed as Accumulation", "Risk plan",
                   "mechanical, not a forecast", "4,600", "37 lots", "certain bounds", "Pattern history",
                   'href="/pattern/BROKER2-ASING-AKUM"', "Not investment advice"):
        assert needle in r.text, needle
    assert "buy now" not in r.text.lower()
    assert 'id="gl-data"' in r.text                          # popover data shipped with the page


def test_unknown_or_malformed_tickers_are_404_not_500(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: None)
    for path in ("/stock/ZZZZ", "/stock/not-a-ticker", "/stock/AB"):
        r = client.get(path)
        assert r.status_code == 404 and "No data for" in r.text


def test_stock_page_database_failure_is_an_error_panel(client, monkeypatch):
    def boom(t):
        raise RuntimeError("db down")

    monkeypatch.setattr(stockmod, "load", boom)
    r = client.get("/stock/BBCA")
    assert r.status_code == 200 and "Cannot read the data" in r.text and "db down" in r.text


def test_risk_pct_is_clamped(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: _raw())
    assert 'value="5.0"' in client.get("/stock/BBCA?capital=100&risk_pct=99").text
    assert 'value="0.1"' in client.get("/stock/BBCA?capital=100&risk_pct=0").text


def test_guide_lists_every_term_and_every_pattern_with_anchors(client):
    from vg.web import glossary

    r = client.get("/guide")
    assert r.status_code == 200
    for key in glossary.TERMS:
        assert f'id="{key}"' in r.text, key
    for tag in glossary.TAGS:
        assert f'id="tag-{tag}"' in r.text, tag
    assert "What this tool cannot do" in r.text and "Not investment advice" in r.text


def test_search_redirects_a_known_ticker_and_lists_terms_otherwise(client, monkeypatch):
    monkeypatch.setattr(webapp, "_ticker_exists", lambda t: t == "BBCA")

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass
        def fetchall(self): return [("BBRI", "Bank Rakyat Indonesia")]

    class _Conn(_Cur):
        def cursor(self): return _Cur()

    monkeypatch.setattr(webapp, "connect", lambda: _Conn())
    r = client.get("/search?q=bbca", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/stock/BBCA"
    r = client.get("/search?q=edge")
    assert r.status_code == 200 and 'href="/guide#edge"' in r.text
    assert "Type a stock code" in client.get("/search").text


def test_every_page_has_the_search_box_and_the_disclaimer(client):
    for path in ("/", "/screener", "/ranking", "/coverage", "/guide"):
        r = client.get(path)
        assert r.status_code == 200
        assert 'action="/search"' in r.text, path
        assert re.search(r"[Nn]ot investment advice", r.text), path


# ---- stop & target test page -------------------------------------------------------------------

def _snapshot():
    from vg import risk_eval
    from vg.risk_eval import Bar, Sig

    flat = [Bar(100.0, 101.0, 99.0, 100.0)] * 10
    sigs = [(Sig(date(2026, 10, 1), "AAAA", tag, True), 100.0, 5.0, flat)
            for tag in (risk_eval.BASELINE, "BROKER2-ASING-AKUM") for _ in range(3)]
    return risk_eval.snapshot_payload(risk_eval.evaluate(sigs), (date(2026, 6, 3), date(2026, 10, 20)), min_n=1)


def test_snapshot_payload_is_json_safe_and_self_describing():
    import json

    p = _snapshot()
    json.dumps(p)
    assert p["targets"] == ["1", "2", "3"] and p["baseline"] == "ALL STOCKS" and p["window"] == ["2026-06-03", "2026-10-20"]
    assert p["scopes"]["all"][0]["tag"] == "ALL STOCKS" and set(p["scopes"]["all"][0]["r"]) == {"1", "2", "3"}


def test_risk_test_page_states_the_result_and_the_limits(client, monkeypatch):
    import datetime as dt

    monkeypatch.setattr(webapp, "_risk_snapshot", lambda: (_snapshot(), dt.datetime(2026, 11, 3, 9, 0)))
    r = client.get("/risk-test")
    assert r.status_code == 200
    for needle in ("Does the stop-and-target rule make the patterns pay?", "-1.70%", "simply", "Results by pattern",
                   "not a forward test", "All stocks (same rule)", 'href="/pattern/BROKER2-ASING-AKUM"',
                   'href="/risk-test?scope=liquid"'):
        assert needle in r.text, needle
    assert client.get("/risk-test?scope=liquid").status_code == 200
    assert client.get("/risk-test?scope=bogus").status_code == 200          # unknown scope falls back to all


def test_risk_test_page_without_a_saved_snapshot_says_how_to_create_it(client, monkeypatch):
    monkeypatch.setattr(webapp, "_risk_snapshot", lambda: (None, None))
    r = client.get("/risk-test")
    assert r.status_code == 200 and "vg risk-eval --save" in r.text


def test_stock_page_shows_the_evidence_next_to_the_levels(client, monkeypatch):
    import datetime as dt

    monkeypatch.setattr(stockmod, "load", lambda t: _raw())
    monkeypatch.setattr(webapp, "_risk_snapshot", lambda: (_snapshot(), dt.datetime(2026, 11, 3, 9, 0)))
    r = client.get("/stock/BBCA")
    assert "Has a rule like this paid off?" in r.text and "See the full test" in r.text
    assert "On " in r.text and "signals (3)" in r.text                      # the stock's own flagged pattern is listed
    monkeypatch.setattr(webapp, "_risk_snapshot", lambda: (None, None))
    assert "Has a rule like this paid off?" not in client.get("/stock/BBCA").text


def _with_charts(raw, n=30):
    D = Decimal
    days = [LATEST - timedelta(days=i) for i in range(n)][::-1]
    raw["chart_bars"] = [(d, D(4800 + i), D(4850 + i), D(4780 + i), D(4820 + i), D(1_000_000), D(1)) for i, d in enumerate(days)]
    raw["chart_flow"] = [(d, D(1_000_000_000 * (-1) ** i), D(-15_000_000_000), D(-1_000_000_000), D(250_000_000_000))
                         for i, d in enumerate(days)]
    return raw


def test_view_builds_the_three_charts_and_draws_the_risk_levels_on_the_price_chart():
    v = stockmod.build_view(_with_charts(_raw(atr=120)), TRACK)
    assert v["charts"]["price"].startswith("<svg") and "Stop 4,600" in v["charts"]["price"]
    assert v["charts"]["flow"].startswith("<svg") and v["charts"]["band"].startswith("<svg")


def test_view_without_chart_data_has_no_charts_instead_of_empty_frames():
    v = stockmod.build_view(_raw(), TRACK)
    assert set(v["charts"]) == {"price", "price_m", "bpi", "bpi_m", "flow", "flow_m", "band", "band_m", "groups", "groups_m"} \
        and not any(v["charts"].values())


def test_stock_page_embeds_the_charts_with_their_explanations(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: _with_charts(_raw()))
    r = client.get("/stock/BBCA")
    for needle in ("Price, last 90 sessions", "hollow = closed above the open", "per day", "certain range</a>",
                   'aria-labelledby="price-t price-d"', "<polyline"):
        assert needle in r.text, needle


# ---- pattern detail page ------------------------------------------------------------------------

def _pattern_raw():
    D = Decimal
    cal = [LATEST - timedelta(days=i) for i in range(30)][::-1]
    return {"tag": "BROKER2-ASING-AKUM",
            "monthly": [(date(2026, 10, 1), 400, D("0.0021"), 0.34, D("0.31")), (date(2026, 9, 1), 12, D("-0.0300"), 0.2, D("0.3"))],
            "last_day": LATEST, "n_latest": 77,
            "latest": [("BBCA", D(4840), D(4980), D(250_000_000_000)), ("TLKM", D(3000), D(3000), D(90_000_000_000))],
            "calendar": cal, "counts": {cal[-1]: 77, cal[-2]: 10}}


def _fwd_row(code="probation"):
    status = type("S", (), {"code": code, "label": code.upper()})()
    cell = lambda t, c="": {"t": t, "c": c}  # noqa: E731
    return {"tag": "BROKER2-ASING-AKUM", "n": 1105, "span_days": 59, "edge_h1": cell("-0.07%", "neg"), "edge_h5": cell("+0.15%", "pos"),
            "edge_h10": cell("-0.24%", "neg"), "hit": "34%", "univ_hit": "31%", "n_liquid": 887, "edge_h5_liquid": cell("+0.79%", "pos"),
            "edge_after_cost": cell("-1.55%", "neg"), "status": status, "small_n": False}


def test_pattern_view_formats_months_and_latest_signals():
    from vg.web import pattern as patternmod

    v = patternmod.build_view(_pattern_raw(), _fwd_row(), None, None)
    assert v["name"] == "Foreign brokers certainly net buying into weakness" and v["family"] == "broker"
    assert v["monthly"][0] == {"month": "Oct 2026", "n": 400, "small": False, "edge": {"t": "+0.21%", "c": "pos"}, "hit": "34%", "uhit": "31%"}
    assert v["monthly"][1]["small"] is True
    assert v["latest"][0]["ticker"] == "BBCA" and v["latest"][0]["chg"]["t"] == "-2.8%" and v["latest"][0]["value"] == "250.0B"
    assert v["n_latest"] == 77 and v["chart"].startswith("<svg")
    assert {s["tag"] for s in v["siblings"]} >= {"BROKER2-ASING-DIST", "BROKER-ASING-AKUM"} and v["tag"] not in {s["tag"] for s in v["siblings"]}


def test_pattern_page_states_the_verdict_the_rule_and_the_evidence(client, monkeypatch):
    import datetime as dt

    from vg.web import pattern as patternmod

    monkeypatch.setattr(patternmod, "load", lambda t: _pattern_raw())
    monkeypatch.setattr(webapp, "_cached_view_rows", lambda since, fresh=False: ([_fwd_row()], 0.0, True))
    monkeypatch.setattr(webapp.CONFIG, "forward_since", date(2026, 9, 4), raising=False)
    monkeypatch.setattr(webapp, "_risk_snapshot", lambda: (_snapshot(), dt.datetime(2026, 11, 3, 9, 0)))
    r = client.get("/pattern/broker2-asing-akum")
    assert r.status_code == 200
    for needle in ("What does this pattern say?", "The exact rule", "Lower bound of foreign broker net buying", "Track record",
                   "Forward test since 2026-09-04", "PROBATION", "Month by month", "small n", "Flagged on the latest session",
                   "77 stocks flagged", 'href="/stock/BBCA"', "How often it fires", "Related patterns", "Not investment advice"):
        assert needle in r.text, needle
    assert "BROKER2-ASING-AKUM" in r.text and "It is not a trade instruction" in r.text
    assert r.text.count(">PROBATION<") == 2 and "context only" in r.text     # the full history never gets a verdict


def test_unknown_pattern_is_404(client):
    r = client.get("/pattern/NOT-A-PATTERN")
    assert r.status_code == 404 and "Unknown pattern" in r.text


def test_pattern_chips_everywhere_link_to_the_pattern_page(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: _raw())
    assert 'href="/pattern/BROKER2-ASING-AKUM"' in client.get("/stock/BBCA").text
    assert 'href="/pattern/AKUM-ASING"' in client.get("/guide").text
    assert "Open the pattern page" in client.get("/guide").text


# ---- phones ---------------------------------------------------------------------------------------

def test_stock_page_ships_both_chart_variants_and_css_swaps_them(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: _with_charts(_raw()))
    t = client.get("/stock/BBCA").text
    assert t.count('class="chart-wide"') == 3 and t.count('class="chart-narrow"') == 3
    assert 'aria-labelledby="price-t price-d"' in t and 'aria-labelledby="price-m-t price-m-d"' in t
    assert ".chart-wide { display:none; }" in t and "grid kpis" in t


def test_screener_and_ranking_lists_become_cards_with_a_label_on_every_value(client, monkeypatch):
    row = {"ticker": "BBCA", "in_wl": False, "held": False, "quad": "AKUMULASI", "price": "4,840", "chg": {"t": "-2.8%", "c": "neg"},
           "nff_d0": "-24B", "nff_d0_c": "neg", "nff_w1": "+486B", "nff_w1_c": "pos", "bkr_net": "+1B", "bkr_net_c": "pos", "cr3": "62%",
           "cons": "2/10", "ksei": "↓", "tags": "AKUM-ASING", "tag_list": ["AKUM-ASING"]}
    monkeypatch.setattr(webapp, "_screener_view", lambda: (date(2026, 11, 3), [row]))
    t = client.get("/screener").text
    assert 'class="cards"' in t and 'role="cell"' in t
    for label in ("Quadrant", "Price", "Change", "Foreign today", "Foreign 1 week", "Foreign brokers", "Top-3 share",
                  "Days in a row", "KSEI trend", "Why it appears"):
        assert f'data-label="{label}"' in t, label
    panel = {"title": "Biggest foreign buying", "note": "n", "total": 5,
             "rows": [{"rank": 1, "ticker": "BBCA", "quad": "AKUMULASI", "val": "+1B", "val_c": "pos", "price": "4,840",
                       "chg": {"t": "-2.8%", "c": "neg"}, "tags": "AKUM-ASING", "tag_list": ["AKUM-ASING"]}]}
    monkeypatch.setattr(webapp, "_ranking_view", lambda h: (date(2026, 11, 3), "5d", [panel]))
    r = client.get("/ranking").text
    assert 'class="cards"' in r and 'class="rk">#1<' in r and 'data-label="Net flow"' in r


def test_wide_analysis_tables_get_a_sticky_first_column_and_a_swipe_hint(client, monkeypatch):
    import datetime as dt

    monkeypatch.setattr(webapp, "_risk_snapshot", lambda: (_snapshot(), dt.datetime(2026, 11, 3, 9, 0)))
    t = client.get("/risk-test").text
    assert 'class="sticky"' in t and "Swipe the table sideways" in t
    assert "table.sticky th:first-child" in t


def test_risk_plan_defaults_come_from_env_the_same_settings_the_digest_uses(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: _raw(atr=120))
    cfg = webapp.CONFIG
    monkeypatch.setattr(cfg, "atr_sl_mult", 3.0, raising=False)
    monkeypatch.setattr(cfg, "target_r", 2.5, raising=False)
    monkeypatch.setattr(cfg, "fee_pct", 0.005, raising=False)
    monkeypatch.setattr(cfg, "account_balance", 50_000_000.0, raising=False)
    monkeypatch.setattr(cfg, "risk_pct", 0.02, raising=False)
    t = client.get("/stock/BBCA").text
    assert "(3 × ATR)" in t and ">2.5R<" not in t and "2.5R" in t and "0.5% round-trip fee" in t
    assert 'name="capital"' in t and 'value="50.0"' in t and 'value="2.0"' in t
    assert "lots" in t                                                      # a lot count appears without typing anything


def test_snapshot_note_appears_when_the_tested_stop_differs_from_the_configured_one(client, monkeypatch):
    import datetime as dt

    monkeypatch.setattr(stockmod, "load", lambda t: _raw())
    monkeypatch.setattr(webapp, "_risk_snapshot", lambda: (_snapshot(), dt.datetime(2026, 11, 3, 9, 0)))
    monkeypatch.setattr(webapp.CONFIG, "atr_sl_mult", 3.0, raising=False)
    assert "The test used a 2 × ATR stop; your setting differs." in client.get("/stock/BBCA").text
    monkeypatch.setattr(webapp.CONFIG, "atr_sl_mult", 2.0, raising=False)
    assert "your setting differs" not in client.get("/stock/BBCA").text


# ---- "Is the edge real?" (K10-K13) -----------------------------------------------------------------

def _research_payload(with_forward=False):
    import random

    from vg import research as rs

    rng = random.Random(4)
    days = [date(2023, 1, 2) + timedelta(days=i) for i in range(900) if (date(2023, 1, 2) + timedelta(days=i)).weekday() < 5][:600]

    def tag(mean, cost):
        n = [6.0] * len(days)
        raw = [6 * (mean + rng.gauss(0, 0.007)) for _ in days]
        return {"n": n, "raw": raw, "neut": list(raw), "cost": [6 * cost] * len(days)}

    res = rs.analyze(days, {"BROKER2-ASING-AKUM": tag(0.03, 0.005), "AKUM-ASING": tag(0.0, 0.005)}, {}, reps=200)
    out = {"window": ["2023-01-02", "2025-04-30"], "forward_since": "2026-09-04",
           "params": {"block": 5, "reps": 200, "embargo": 10, "fee": 0.003, "qty_idr": 100_000_000, "kappa": 0.5, "alpha": 0.05},
           "results": {"all": {"full": res}, "liquid": {"full": res}}}
    if with_forward:
        out["results"]["all"]["forward"] = res
    return out


def test_research_page_reports_survivors_and_labels_everything_else(client, monkeypatch):
    import datetime as dt

    monkeypatch.setattr(webapp, "_research_snapshot", lambda: (_research_payload(), dt.datetime(2026, 11, 3, 23, 0)))
    r = client.get("/research")
    assert r.status_code == 200
    for needle in ("Do any of the patterns have an edge that survives scrutiny?", "1 of 2</b> patterns survive all of it",
                   "Candidate: survives costs, factors and time", "No evidence of an edge", "Matched edge", "After costs",
                   'href="/pattern/BROKER2-ASING-AKUM"', "not a forward test", "Not removed: beta and sector"):
        assert needle in r.text, needle
    assert 'href="/research?scope=liquid&amp;sample=full"' in r.text
    assert "Forward window" not in r.text                                    # no forward results saved -> no dead link
    monkeypatch.setattr(webapp, "_research_snapshot", lambda: (_research_payload(True), dt.datetime(2026, 11, 3, 23, 0)))
    assert "Forward window (since 2026-09-04)" in client.get("/research").text
    assert client.get("/research?scope=liquid&sample=forward").status_code == 200
    assert client.get("/research?scope=bogus&sample=bogus").status_code == 200


def test_research_page_without_a_snapshot_says_how_to_create_it(client, monkeypatch):
    monkeypatch.setattr(webapp, "_research_snapshot", lambda: (None, None))
    r = client.get("/research")
    assert r.status_code == 200 and "vg research --save" in r.text


def test_pattern_page_shows_the_research_evidence_when_available(client, monkeypatch):
    import datetime as dt

    from vg.web import pattern as patternmod

    monkeypatch.setattr(patternmod, "load", lambda t: _pattern_raw())
    monkeypatch.setattr(webapp, "_cached_view_rows", lambda since, fresh=False: ([_fwd_row()], 0.0, True))
    monkeypatch.setattr(webapp, "_risk_snapshot", lambda: (None, None))
    monkeypatch.setattr(webapp, "_research_snapshot", lambda: (_research_payload(), dt.datetime(2026, 11, 3, 23, 0)))
    t = client.get("/pattern/BROKER2-ASING-AKUM").text
    for needle in ("Edge [interval]", "Candidate: survives costs, factors and time", "After modelled costs", "q-value", "by half-year", "Market up (60-day trend)"):
        assert needle in t, needle
    monkeypatch.setattr(webapp, "_research_snapshot", lambda: (None, None))
    assert "Edge [interval]" not in client.get("/pattern/BROKER2-ASING-AKUM").text


def test_research_glossary_terms_exist_for_every_term_the_page_uses():
    from vg.web import glossary

    for key in ("ci95", "eff_blocks", "neutral_edge", "net_edge", "fdr_q", "folds_pos", "regime"):
        assert key in glossary.TERMS and glossary.TERMS[key].group == "research"


# ---- /health ---------------------------------------------------------------------------------------

def test_health_page_shows_checks_progress_and_when_you_hear_from_the_system(client, monkeypatch):
    from vg import status as st

    view = {"level": "warn", "headline": "1 warning", "now": "04 Nov 2026 08:00", "last_report": "04 Nov 2026 07:59", "budget": 15000,
            "checks": [{"level": "ok", "name": "EOD", "text": "3 Nov 2026", "mark": "\u2713"},
                       {"level": "warn", "name": "Backup", "text": "backup-2026-10-20 (15 days old)", "mark": "\u26a0"}],
            "progress": st.deep_progress({"total_days": 805, "pending_days": 705, "pending_calls": 150_000}),
            "last_deep": {"status": "ok", "calls": 15000, "error": None, "started_at": "2026-11-04T00:12:00+07:00"},
            "recent": [{"phase": "compute", "status": "ok", "rows": 361, "started_at": "2026-11-03T19:25:00+07:00"},
                       {"phase": "collect_broker", "status": "error", "rows": 0, "started_at": "2026-11-03T19:26:00+07:00"}]}
    monkeypatch.setattr(webapp, "_health_view", lambda: view)
    webapp._HEALTH_CACHE.clear()
    r = client.get("/health")
    assert r.status_code == 200
    for needle in ("Is the system running?", "1 warning", "backup-2026-10-20 (15 days old)", 'role="progressbar"', 'aria-valuenow="12"',
                   "705 days and about 150,000 calls left", "Latest run of each job", "When you hear from the system",
                   "healthchecks.io", "no message means something is wrong"):
        assert needle in r.text, needle
    assert 'href="/health"' in r.text


def test_health_page_database_failure_is_an_error_panel_not_a_crash(client, monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(webapp, "_health_view", boom)
    webapp._HEALTH_CACHE.clear()
    r = client.get("/health")
    assert r.status_code == 200 and "Cannot read the data" in r.text and "db down" in r.text


# ---- Today (new home) ------------------------------------------------------------------------------

def _today_raw():
    D = Decimal
    row = lambda tk, c, p, v, d0, w1, q, cons, lo, hi, n: (tk, D(c), D(p), D(v), D(d0), D(w1), q, cons, lo, hi, n)  # noqa: E731
    return {"session_date": LATEST, "ihsg": (D("7500"), D("7600")), "breadth": (300, 550, 100, D("12000000000000")),
            "foreign": (D("-900000000000"), D("-850000000000")),
            "watch": [row("BBCA", 4840, 4980, 250_000_000_000, -24_400_000_000, 486_600_000_000, "AKUMULASI", 2, None, None, 3)],
            "inflow": [row("ASII", 4750, 4860, 90_000_000_000, 46_000_000_000, 160_000_000_000, "MARKUP", 7, None, None, 1)],
            "outflow": [row("TLKM", 3000, 3100, 120_000_000_000, -30_000_000_000, -200_000_000_000, "MARKDOWN", 1, None, None, 2)],
            "certain": {"BROKER2-ASING-AKUM": [row("BBRI", 2660, 2820, 857_000_000_000, 0, 0, "AKUMULASI", 3, D(60_000_000_000), D(90_000_000_000), 2)],
                        "BROKER2-ASING-AKUM:n": 39, "BROKER2-ASING-DIST": [], "BROKER2-ASING-DIST:n": 0}}


def test_today_view_describes_the_market_and_formats_the_lists():
    from vg.web import today

    v = today.build_view(_today_raw(), {"n": 26, "passed": 0, "failing": 5, "probation": 21})
    assert v["market"][0] == "The Jakarta Composite closed at 7,500, down 1.32%."
    assert "300 stocks rose, 550 fell and 100 were unchanged" in v["market"][1] and "Rp12.0 trillion" in v["market"][1]
    assert "net sellers of Rp900.0 billion" in v["market"][2] and "in liquid stocks alone" in v["market"][2]
    w = v["watch"][0]
    assert w["ticker"] == "BBCA" and w["chg"]["t"] == "-2.8%" and w["w1"] == "+486.6B" and w["quad_word"] == "Accumulation" and w["n_tags"] == 3
    assert v["certain"]["buy_n"] == 39 and v["certain"]["buy"][0]["bounds"] == "+7.0% to +10.5%"
    assert v["inflow"][0]["quad_word"] == "Markup" and v["outflow"][0]["d0_c"] == "neg"


def test_today_page_answers_the_three_questions_and_never_recommends(client, monkeypatch):
    from vg.web import today

    monkeypatch.setattr(webapp.engine, "latest_signal_date", lambda: LATEST)
    monkeypatch.setattr(today, "load", lambda sd, watch: _today_raw())
    monkeypatch.setattr(webapp, "_track_summary", lambda: {"n": 26, "passed": 0, "failing": 5, "probation": 21})
    monkeypatch.setattr(webapp.CONFIG, "watchlist", ("BBCA",), raising=False)
    monkeypatch.setattr(webapp.CONFIG, "holdings", (), raising=False)
    r = client.get("/today")
    assert r.status_code == 200
    for needle in ("What happened on 2026-11-03, and what is worth a look?", "The Jakarta Composite closed at 7,500", "Your stocks",
                   "Foreign money flowing in", "Foreign money flowing out", "the data is certain about the direction",
                   "none of the 26 tracked patterns has passed the forward test yet", "not what to buy",
                   'href="/stock/BBCA"', 'href="/stock/ASII"', 'href="/stock/TLKM"', 'href="/track-record"', "Not investment advice"):
        assert needle in r.text, needle
    assert "buy now" not in r.text.lower() and "you should" not in r.text.lower()


def test_today_page_handles_no_signals_and_database_failure(client, monkeypatch):
    monkeypatch.setattr(webapp.engine, "latest_signal_date", lambda: None)
    assert "No signals yet" in client.get("/today").text

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(webapp.engine, "latest_signal_date", boom)
    r = client.get("/today")
    assert r.status_code == 200 and "Cannot read the data" in r.text and "db down" in r.text


def test_track_record_moved_to_its_own_address(client):
    r = client.get("/track-record")
    assert r.status_code == 200 and 'href="/track-record"' in r.text


# ---- cache for Today and pattern pages -------------------------------------------------------------------

def test_today_view_is_cached_for_the_same_signal_date_and_fresh_rebuilds_it(client, monkeypatch):
    from vg.web import today

    calls = []
    monkeypatch.setattr(webapp.engine, "latest_signal_date", lambda: LATEST)
    monkeypatch.setattr(today, "load", lambda sd, watch: (calls.append(sd), _today_raw())[1])
    monkeypatch.setattr(webapp, "_track_summary", lambda: None)
    webapp._TODAY_CACHE.clear()
    first = client.get("/today")
    assert first.status_code == 200 and len(calls) == 1 and "Built at" in first.text and "kept for up to 10 minutes" not in first.text
    second = client.get("/today")
    assert len(calls) == 1 and "kept for up to 10 minutes" in second.text              # served from the cache and says so
    client.get("/today?fresh=1")
    assert len(calls) == 2
    monkeypatch.setattr(webapp.engine, "latest_signal_date", lambda: LATEST + timedelta(days=1))
    client.get("/today")
    assert len(calls) == 3                                                           # a new signal date never reuses yesterday's view


def test_pattern_data_is_cached_per_pattern(client, monkeypatch):
    from vg.web import pattern as patternmod

    calls = []
    monkeypatch.setattr(patternmod, "load", lambda t: (calls.append(t), _pattern_raw())[1])
    monkeypatch.setattr(webapp.engine, "latest_signal_date", lambda: LATEST)
    monkeypatch.setattr(webapp, "_cached_view_rows", lambda since, fresh=False: ([], 0.0, True))
    monkeypatch.setattr(webapp, "_risk_snapshot", lambda: (None, None))
    monkeypatch.setattr(webapp, "_research_snapshot", lambda: (None, None))
    webapp._PATTERN_CACHE.clear()
    client.get("/pattern/BROKER2-ASING-AKUM")
    client.get("/pattern/BROKER2-ASING-AKUM")
    assert calls == ["BROKER2-ASING-AKUM"]
    client.get("/pattern/AKUM-ASING")
    client.get("/pattern/BROKER2-ASING-AKUM?fresh=1")
    assert calls == ["BROKER2-ASING-AKUM", "AKUM-ASING", "BROKER2-ASING-AKUM"]


def test_every_page_carries_the_theme_button(client):
    for path in ("/guide", "/health"):
        assert 'id="theme-btn"' in client.get(path).text


# ---- net flow by broker group ------------------------------------------------------------------------------------

def _with_brokers(raw, copy_last=False, n=12):
    """Per-broker rows for the chart days: foreign AK, retail YP, other ZZ. Every day's table is different (scaled by
    the day number) unless `copy_last`, which makes the last day an exact copy of the day before it."""
    raw = _with_charts(raw, n=n)
    flows, levels = [], []
    for i, r in enumerate(raw["chart_flow"]):
        d = r[0]
        k = i if (copy_last and i == len(raw["chart_flow"]) - 1) else i + 1
        D = Decimal
        flows.append((d, "AK", D(100 * k), D(40 * k)))
        levels += [(d, "B", "AK", D(100 * k)), (d, "S", "AK", D(40 * k)), (d, "B", "YP", D(30 * k)),
                   (d, "S", "YP", D(90 * k)), (d, "B", "ZZ", D(20 * k)), (d, "S", "ZZ", D(20 * k))]
    raw["chart_brokers"] = {"flows": flows, "levels": levels}
    return raw


def test_group_series_splits_into_three_groups_that_add_to_zero_as_a_share_of_value():
    g = stockmod.group_series(_with_brokers(_raw()))
    assert g and len(g["days"]) == 12 and g["hidden"] == 0
    i = 0                                                                      # day 1: value = 250,000,000,000 in _with_charts
    value = 250_000_000_000
    assert g["foreign"][0][i] == pytest.approx(60 / value) and g["retail"][0][i] == pytest.approx(-60 / value)
    assert g["other"][0][i] == pytest.approx(0.0)
    assert g["churn"][i] == pytest.approx((40 + 30 + 20) / value)


def test_group_series_leaves_out_a_day_that_is_an_exact_copy_of_the_previous_one():
    g = stockmod.group_series(_with_brokers(_raw(), copy_last=True))
    assert g["hidden"] == 1 and g["foreign"][0][-1] is None and g["churn"][-1] is None
    assert g["foreign"][0][-2] is not None


def test_group_series_is_none_without_broker_rows_or_ladders():
    assert stockmod.group_series(_with_charts(_raw())) is None
    raw = _with_brokers(_raw())
    raw["chart_brokers"]["levels"] = []
    assert stockmod.group_series(raw) is None


def test_view_and_page_show_the_group_chart_with_its_caveats(client, monkeypatch):
    v = stockmod.build_view(_with_brokers(_raw()), TRACK)
    assert v["charts"]["groups"].startswith("<svg") and v["charts"]["groups_m"].startswith("<svg") and v["group_hidden"] == 0
    monkeypatch.setattr(stockmod, "load", lambda t: _with_brokers(_raw(), copy_last=True))
    r = client.get("/stock/BBCA")
    for needle in ("Who is buying and selling?", "Net flow by group</a>", "round-trip</a>", "an estimate", "not proof of a market maker", "share one scale",
                   "1 day left out", 'aria-labelledby="groups-t groups-d"', 'aria-labelledby="groups-m-t groups-m-d"'):
        assert needle in r.text, needle


# ---- 'On this page' + folded secondary sections ------------------------------------------------------------

def test_stock_page_sections_have_anchors_for_the_on_this_page_bar(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: _raw())
    t = client.get("/stock/BBCA").text
    assert '<nav id="toc" class="toc" aria-label="On this page" hidden></nav>' in t       # hidden until the script fills it
    for sid in ("patterns", "risk", "recent", "historical-edge", "ownership", "filings", "brokers"):
        assert re.search(rf'class="panel[^"]*" id="{sid}"', t), sid
        assert f'"{sid}":' in t, sid                                                          # every anchor has a short label in the bar
    assert t.index('id="toc"') < t.index('id="patterns"')                                    # the bar comes before the sections


def test_secondary_sections_start_folded_with_their_heading_visible(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: _raw())
    t = client.get("/stock/BBCA").text
    for sid, heading in (("brokers", "Biggest brokers"), ("ownership", "Ownership (KSEI, monthly)"),
                         ("filings", "Ownership filings")):
        assert re.search(rf'<details class="panel fold" id="{sid}">\s*<summary><h2>{re.escape(heading)}', t), sid
    assert 'class="panel fold" id="patterns"' not in t and '<details class="panel fold" id="risk"' not in t   # the core stays open
