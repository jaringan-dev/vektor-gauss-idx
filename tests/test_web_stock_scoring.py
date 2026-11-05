"""The broker-scoring panel on /stock/{ticker}: read-only loading that survives missing columns, the three page
states (absent / empty / ok), graceful N/A, and that the page keeps answering 200 (DB stubbed)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from vg.engine.scoring import SCORING_COLUMNS
from vg.web import app as webapp
from vg.web import freshness
from vg.web import stock as stockmod

from .test_web_stock import LATEST, TRACK, _raw

D = Decimal


def _srow(day=LATEST, **over):
    """One signal_components scoring row, in the order of SCORING_COLUMNS, preceded by the session date."""
    base = {"bpi3": D("0.125"), "bpi5": D("0.140"), "bpi_overlap3": 2, "aggr_haka_share": None, "aggr_ratio": None,
            "aggr_coverage": None, "retail_net_lo": D("40000000000"), "retail_net_hi": D("40000000000"),
            "finst_net_lo": D("-30000000000"), "finst_net_hi": D("-20000000000"),
            "retail_div_state": "retail_buying_institutions_selling"}
    base.update(over)
    return (day, *(base[c] for c in SCORING_COLUMNS))


EMPTY_ROW = (LATEST, *([None] * len(SCORING_COLUMNS)))


# ---- the loader: read-only, and never names a column that may not exist -----------------------------------------------

class _Cur:
    def __init__(self, n_columns, rows=()):
        self.n, self.rows, self.executed = n_columns, list(rows), []
        self._last = ""

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._last = sql

    def fetchone(self):
        assert "information_schema" in self._last
        return (self.n,)

    def fetchall(self):
        return self.rows


@pytest.mark.parametrize("have", [0, 7, 10])
def test_missing_columns_mean_none_and_no_statement_ever_names_them(have):
    cur = _Cur(have)
    assert stockmod._load_scoring(cur, "BBCA") is None
    assert len(cur.executed) == 1 and "information_schema" in cur.executed[0][0]
    assert not any("bpi3" in sql for sql, _ in cur.executed)          # the catalogue query takes them as a parameter only


def test_with_all_columns_present_it_selects_the_last_ten_sessions_read_only():
    cur = _Cur(len(SCORING_COLUMNS), rows=[_srow()])
    assert stockmod._load_scoring(cur, "BBCA") == [_srow()]
    sql, params = cur.executed[1]
    assert sql.lstrip().upper().startswith("SELECT") and "ORDER BY session_date DESC LIMIT 10" in sql
    assert params == ("BBCA",)
    for col in SCORING_COLUMNS:
        assert col in sql
    assert all(s.lstrip().upper().startswith("SELECT") for s, _ in cur.executed)


# ---- the view -----------------------------------------------------------------------------------------------------------

def test_view_states():
    assert stockmod.scoring_view(None, LATEST, []) == {"state": "absent"}
    assert stockmod.scoring_view([], LATEST, []) == {"state": "empty"}
    assert stockmod.scoring_view([EMPTY_ROW], LATEST, []) == {"state": "empty"}     # columns exist, nothing scored


def test_view_formats_a_scored_session():
    v = stockmod.scoring_view([_srow()], LATEST, [])
    c = v["current"]
    assert v["state"] == "ok" and not v["stale"] and c["date"] == "2026-11-03"
    assert c["bpi3"] == {"t": "+12.5 pts", "c": "pos"} and c["bpi5"]["t"] == "+14.0 pts"
    assert c["overlap"] == "2 of 3" and c["wash"] is True               # 2 shared houses and 12.5 pts >= the 10-pt threshold
    assert c["retail"] == "+40.0B" and c["inst"] == "-30.0B to -20.0B"
    assert c["div"] == "Retail buying, foreign institutions selling"


def test_aggressor_is_na_when_order_flow_was_never_collected():
    c = stockmod.scoring_view([_srow()], LATEST, [])["current"]
    assert (c["haka"], c["ratio"], c["coverage"]) == ("N/A", "N/A", "N/A") and c["has_aggr"] is False


def test_aggressor_is_shown_when_it_exists():
    c = stockmod.scoring_view(
        [_srow(aggr_haka_share=D("0.6154"), aggr_ratio=D("1.6"), aggr_coverage=D("0.8667"))], LATEST, [])["current"]
    assert (c["haka"], c["ratio"], c["coverage"]) == ("62%", "1.60", "87%") and c["has_aggr"] is True


def test_every_measure_degrades_to_na_on_its_own():
    row = _srow(bpi3=None, bpi5=None, bpi_overlap3=None, retail_net_lo=None, retail_net_hi=None, finst_net_lo=None,
                finst_net_hi=None, retail_div_state=None, aggr_haka_share=D("0.5"))
    c = stockmod.scoring_view([row], LATEST, [])["current"]
    assert c["bpi3"]["t"] == c["bpi5"]["t"] == c["overlap"] == c["retail"] == c["inst"] == c["div"] == "N/A"
    assert c["wash"] is False and c["haka"] == "50%"


def test_wash_flag_needs_both_conditions():
    for over, wash in (({"bpi_overlap3": 1}, False), ({"bpi3": D("0.099")}, False), ({"bpi3": D("0.10")}, True),
                       ({"bpi3": D("-0.30"), "bpi_overlap3": 3}, False)):
        assert stockmod.scoring_view([_srow(**over)], LATEST, [])["current"]["wash"] is wash, over


def test_stale_when_the_newest_scored_session_is_not_the_latest_one():
    older = LATEST - timedelta(days=2)
    v = stockmod.scoring_view([EMPTY_ROW, _srow(older)], LATEST, [])
    assert v["stale"] is True and v["current"]["date"] == older.isoformat() and len(v["history"]) == 1


def test_wash_today_follows_the_tag_not_a_recomputation():
    assert stockmod.scoring_view([_srow()], LATEST, ["AKUM-NETRAL-WASH"])["wash_today"] is True
    assert stockmod.scoring_view([_srow()], LATEST, ["AKUM-ASING"])["wash_today"] is False


def test_a_raw_dict_without_scoring_still_builds_the_page_view():
    assert stockmod.build_view(_raw(), TRACK)["scoring"] == {"state": "absent"}      # old callers / old tests keep working


# ---- the page --------------------------------------------------------------------------------------------------------------

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


def _page(client, monkeypatch, scoring, tags=("BROKER2-ASING-AKUM",)):
    raw = _raw(tags=tags)
    if scoring != "missing":
        raw["scoring"] = scoring
    monkeypatch.setattr(stockmod, "load", lambda t: raw if t == "BBCA" else None)
    return client.get("/stock/BBCA")


def test_page_before_the_migration_explains_and_still_answers_200(client, monkeypatch):
    for scoring in (None, "missing"):                      # columns absent / an old-style raw dict
        r = _page(client, monkeypatch, scoring)
        assert r.status_code == 200 and "Cannot read the data" not in r.text
        assert "Broker scoring" in r.text and "not in the database yet" in r.text
        assert "What happened to BBCA?" in r.text and "Risk plan" in r.text          # the rest of the page is intact


def test_page_for_a_stock_without_scored_sessions(client, monkeypatch):
    r = _page(client, monkeypatch, [EMPTY_ROW])
    assert r.status_code == 200 and "No broker scoring for this stock yet" in r.text


def test_page_with_scores_shows_the_measures_and_na_for_the_missing_aggressor(client, monkeypatch):
    r = _page(client, monkeypatch, [_srow(), _srow(LATEST - timedelta(days=1), bpi3=D("-0.05"), bpi_overlap3=0)])
    assert r.status_code == 200
    for needle in ("Broker scoring", "experimental, not yet forward-tested", "+12.5 pts", "2 of 3", "same houses on both sides",
                   "Retail buying, foreign institutions selling", "+40.0B", "N/A", "no order-flow data for this day",
                   "Last 2 scored sessions", 'aria-label="Broker scoring by session"', 'data-term="bpi"',
                   'data-term="overlap3"', 'data-term="aggressor"', 'data-term="retail_div"'):
        assert needle in r.text, needle
    assert ">None<" not in r.text and "nan%" not in r.text.lower() and "nan pts" not in r.text.lower()


def test_page_with_order_flow_shows_the_aggressor(client, monkeypatch):
    r = _page(client, monkeypatch, [_srow(aggr_haka_share=D("0.6154"), aggr_ratio=D("1.6"), aggr_coverage=D("0.8667"))])
    assert r.status_code == 200 and "62%" in r.text and "ratio 1.60" in r.text and "87% of volume flagged" in r.text
    assert "N/A under buyer aggression" not in r.text


def test_wash_tag_today_gets_an_explanation_and_a_chip(client, monkeypatch):
    r = _page(client, monkeypatch, [_srow()], tags=("AKUM-NETRAL-WASH",))
    assert r.status_code == 200 and "Flagged today:" in r.text and 'href="/pattern/AKUM-NETRAL-WASH"' in r.text
    assert "not read as one party accumulating" in r.text
    quiet = _page(client, monkeypatch, [_srow()], tags=("AKUM-ASING",))
    assert "Flagged today:" not in quiet.text


def test_a_stale_scored_session_is_labelled(client, monkeypatch):
    r = _page(client, monkeypatch, [EMPTY_ROW, _srow(LATEST - timedelta(days=2))])
    assert "not</b> this stock's latest session" in r.text


def test_the_panel_describes_and_never_advises(client, monkeypatch):
    from vg.web.narrative import FORBIDDEN

    text = _page(client, monkeypatch, [_srow()], tags=("AKUM-NETRAL-WASH",)).text.lower()
    for phrase in FORBIDDEN:
        assert phrase not in text, phrase


def test_unknown_stock_is_still_a_404(client, monkeypatch):
    monkeypatch.setattr(stockmod, "load", lambda t: None)
    assert client.get("/stock/ZZZZ").status_code == 404
