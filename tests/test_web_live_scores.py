"""BPI computed on the fly for sessions the engine has not scored: same numbers as the engine, nothing written, clearly marked."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from vg.engine.scoring import bandar_power
from vg.web import app as webapp
from vg.web import home, live_scores
from vg.web import stock as stockmod

from .test_web_home import (
    SD,
    _raw,
    _raw_row,
    client,  # noqa: F401 — the shared page-test fixture
)
from .test_web_ios_ui import _page_raw

D = Decimal


class _Cur:
    """A cursor that only answers SELECTs and remembers every statement, so a test can prove nothing was written."""

    def __init__(self, rows):
        self.rows, self.log = rows, []

    def execute(self, sql, params=None):
        self.log.append(sql)

    def fetchall(self):
        return self.rows


LADDERS = {
    "AAAA": ([("AK", 40.0), ("YP", 20.0), ("ZP", 10.0)], [("CC", 15.0), ("ZP", 12.0), ("XL", 10.0)]),
    "BBBB": ([("AK", 5.0)], []),                                                          # no sell ladder: not computable
}


def test_ladders_are_read_with_one_select_for_the_whole_market_and_split_by_side():
    cur = _Cur([("AAAA", "B", "AK", D(40)), ("AAAA", "S", "CC", D(15)), ("BBBB", "B", "AK", D(5))])
    out = live_scores.load_ladders(cur, SD)
    assert out == {"AAAA": ([("AK", 40.0)], [("CC", 15.0)]), "BBBB": ([("AK", 5.0)], [])}
    assert len(cur.log) == 1 and cur.log[0].lstrip().upper().startswith("SELECT") and "broker_levels" in cur.log[0]


def test_filling_gives_exactly_the_engines_numbers_and_marks_the_row_live():
    rows = [{"ticker": "AAAA", "value": 100.0, "bpi3": None}, {"ticker": "BBBB", "value": 100.0, "bpi3": None},
            {"ticker": "CCCC", "value": 100.0, "bpi3": None}, {"ticker": "DDDD", "value": None, "bpi3": None}]
    ladders = {**LADDERS, "DDDD": LADDERS["AAAA"]}
    assert live_scores.fill_rows(rows, ladders) == 1
    want = bandar_power(*LADDERS["AAAA"], 100.0)
    a = rows[0]
    assert (a["bpi3"], a["bpi5"], a["bpi_overlap3"], a["bpi_live"]) == (want.bpi3, want.bpi5, want.overlap3, True) and a["bpi_overlap3"] == 1
    assert all(r["bpi3"] is None and "bpi_live" not in r for r in rows[1:])               # no ladder / no value: stays N/A


def test_a_stored_score_is_never_overwritten_by_a_live_one():
    rows = [{"ticker": "AAAA", "value": 100.0, "bpi3": 0.5, "bpi_overlap3": 0}]
    assert live_scores.fill_rows(rows, LADDERS) == 0 and rows[0]["bpi3"] == 0.5 and "bpi_live" not in rows[0]


class _ScoreCur:
    """Just enough of a database for score_ticker: it answers by the table named in the statement."""

    def __init__(self):
        self.last, self.log = None, []

    def execute(self, sql, params=None):
        self.last = sql
        self.log.append(sql)

    def fetchone(self):
        return (100.0,) if "price_bars" in self.last else None

    def fetchall(self):
        if "broker_levels" in self.last:
            return [("B", "AK", 40.0), ("B", "YP", 20.0), ("S", "CC", 15.0), ("S", "ZP", 12.0)]
        return []


def test_a_live_row_has_the_shape_of_a_stored_row_and_the_statements_are_all_selects():
    cur = _ScoreCur()
    row = live_scores.score_values(date(2026, 10, 1), "AAAA", cur)
    assert row[0] == date(2026, 10, 1) and len(row) == 12 and row[1] == pytest.approx(0.60 - 0.27)     # (40+20)/100 - (15+12)/100
    assert cur.log and all(s.lstrip().upper().startswith("SELECT") for s in cur.log)
    assert live_scores.live_rows(_ScoreCur(), "AAAA", [date(2026, 10, 1), date(2026, 10, 2)])[0][0] == date(2026, 10, 2)   # newest first


def test_a_day_with_nothing_to_compute_gives_no_row():
    class Empty(_ScoreCur):
        def fetchall(self):
            return []

    assert live_scores.score_values(date(2026, 10, 1), "AAAA", Empty()) is None


# ---- the screener -----------------------------------------------------------------------------------------------------------------

def _live_raw(stored=0, live=2, last=date(2026, 9, 3)):
    rows = [_raw_row("AAAA", "AKUM-ASING", bpi=0.33, overlap=1), _raw_row("BBBB", "MARKUP", bpi=-0.1, overlap=2), _raw_row("CCCC", "MARKUP")]
    rows[0]["bpi_live"] = rows[1]["bpi_live"] = True
    return {**_raw(rows, last_scored=last), "stored_n": stored, "live_n": live}


def test_the_card_and_notice_say_the_scores_are_live_not_missing_and_not_stored():
    v = home.build_view(_live_raw())
    card = v["cards"]["eod"]
    assert (card["state"], card["word"], card["t"]) == ("computed", "Computed", "2026-11-05") and "last stored: 2026-09-03" in card["sub"]
    assert "computed on the fly" in v["notice"] and "nothing is written" in v["notice"] and "wash flag only appears once the engine has run" in v["notice"]
    assert next(r for r in v["rows"] if r["ticker"] == "AAAA")["bpi_live"] is True
    assert next(r for r in v["rows"] if r["ticker"] == "CCCC")["bpi_live"] is False and next(r for r in v["rows"] if r["ticker"] == "CCCC")["bpi"]["t"] == "N/A"


def test_the_live_wash_flag_is_never_drawn_from_a_live_number():
    v = home.build_view(_live_raw())
    assert v["cards"]["wash"]["n"] == 0 and not any(r["wash"] for r in v["rows"])          # the wash flag is a stored tag only


def test_when_some_rows_are_stored_the_notice_stays_quiet():
    v = home.build_view(_live_raw(stored=3, live=2))
    assert v["notice"] is None and v["cards"]["eod"]["state"] == "scored"


def test_the_screener_marks_live_numbers_and_leaves_stored_ones_unmarked(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(home, "load", lambda sd: _live_raw())
    t = client.get("/").text
    body = re.search(r"<tbody.*?</tbody>", t, re.DOTALL).group(0)
    assert body.count('<span class="computed"') == 2 and "+33.0 pts" in body and "-10.0 pts" in body
    assert "computed on the fly from today" in t and ">Computed<" in t
    monkeypatch.setattr(home, "load", lambda sd: _raw())
    webapp._HOME_CACHE.clear()                                                            # the page keeps a built view for 5 minutes
    assert '<span class="computed"' not in re.search(r"<tbody.*?</tbody>", client.get("/").text, re.DOTALL).group(0)


def test_loading_the_home_screen_only_reads(monkeypatch):
    """`load` is the only database part of the screener: it must fill live rows with SELECTs alone."""
    seen = []

    class Cur:
        description = ()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            seen.append(sql)
            self.sql = sql
            if "FROM price_bars pb" in sql:
                self.description = [(c,) for c in ("ticker", "close", "prev", "value", "quadrant", *home.SCORE_COLS, "tags")]

        def fetchall(self):
            if "information_schema" in self.sql:
                return [(c,) for c in home.SCORE_COLS]
            if "FROM price_bars pb" in self.sql:
                return [("AAAA", 1000.0, 990.0, 100.0, "AKUMULASI", None, None, None, None, None, "AKUM-ASING")]
            if "broker_levels" in self.sql:
                return [("AAAA", "B", "AK", 40.0), ("AAAA", "S", "CC", 15.0)]
            return []

        def fetchone(self):
            return (date(2026, 9, 3),)

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return Cur()

    import vg.db

    monkeypatch.setattr(vg.db, "connect", lambda: Conn())
    raw = home.load(SD)
    assert raw["stored_n"] == 0 and raw["live_n"] == 1 and raw["last_scored"] == date(2026, 9, 3)
    assert raw["rows"][0]["bpi3"] == pytest.approx(0.40 - 0.15) and raw["rows"][0]["bpi_live"] is True
    assert seen and all(s.lstrip().upper().startswith("SELECT") for s in seen)


# ---- the stock page ---------------------------------------------------------------------------------------------------------------

def test_the_stock_page_labels_a_live_session_and_does_not_call_it_stale():
    raw = _page_raw()
    raw["scoring_live"] = {raw["scoring"][0][0]}
    v = stockmod.build_view(raw, {})
    assert v["scoring"]["current"]["live"] is True and v["scoring"]["history"][0]["live"] is True


def test_the_stock_page_shows_the_live_marker_in_the_note_and_the_history(client, monkeypatch):  # noqa: F811
    raw = _page_raw()
    raw["scoring_live"] = {raw["scoring"][0][0]}
    monkeypatch.setattr(stockmod, "load", lambda t: raw)
    t = client.get("/stock/BBCA").text
    assert t.count('<span class="computed"') == 2 and "the same numbers the engine would store; nothing is written" in t
    raw["scoring_live"] = set()
    assert '<span class="computed"' not in client.get("/stock/BBCA").text
