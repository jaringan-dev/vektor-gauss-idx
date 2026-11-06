"""The watchlist is the user's own: empty until they add stocks on /today, never filled from configuration."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from vg.web import app as webapp
from vg.web import watchlist as wl

from .test_web_ios_ui import client  # noqa: F401 — the shared page-test fixture

ROOT = Path(__file__).resolve().parent.parent


# ---- reading what the user typed -------------------------------------------------------------------------------------------------

def test_codes_are_split_on_commas_spaces_semicolons_and_new_lines_upper_cased_and_deduplicated():
    assert wl.parse("bbca, tlkm;BBRI\nantm  bbca") == (["BBCA", "TLKM", "BBRI", "ANTM"], [])
    assert wl.parse("BBCA,,  ,") == (["BBCA"], [])
    assert wl.parse(None) == ([], []) and wl.parse("") == ([], [])


def test_things_that_are_not_stock_codes_are_set_aside_not_guessed():
    valid, invalid = wl.parse("BBCA BBCA.JK BB TOOLONG <script> 12345 bbc1")
    assert valid == ["BBCA", "BBC1"] and invalid == ["BBCA.JK", "BB", "TOOLONG", "<SCRIPT>", "12345"]


def test_one_submission_cannot_flood_the_list():
    many = " ".join(f"A{i:03d}" for i in range(200))
    valid, _ = wl.parse(many)
    assert len(valid) == wl.MAX_PER_REQUEST
    assert len(wl.parse("X" * 5000)[1]) <= 10


def test_the_address_to_return_to_can_only_be_one_of_our_pages():
    assert wl.safe_next("/today") == "/today" and wl.safe_next("/") == "/"
    for bad in (None, "", "https://evil.example/today", "//evil.example", "/stock/BBCA", "/today?x=1", "javascript:alert(1)"):
        assert wl.safe_next(bad) == "/today"


# ---- what the page says after a change --------------------------------------------------------------------------------------------

def test_the_redirect_carries_the_outcome_and_the_messages_read_it_back():
    res = {"added": ["BBCA", "TLKM"], "exists": ["BBRI"], "unknown": ["ZZZZ"], "invalid": ["BB"], "full": False, "empty": False}
    url = wl.redirect_url("/today", result=res)
    assert url.startswith("/today?") and url.endswith("#watchlist")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q == {"wl_added": "BBCA,TLKM", "wl_exists": "BBRI", "wl_unknown": "ZZZZ", "wl_invalid": "BB"}
    texts = [m["text"] for m in wl.messages(q)]
    assert "Added to your watchlist: BBCA, TLKM." in texts and "Already on your watchlist: BBRI." in texts
    assert "Not a listed stock, so not added: ZZZZ." in texts and any("not a stock code" in t for t in texts)
    assert wl.redirect_url("/today") == "/today#watchlist"


def test_removal_and_failure_messages():
    assert wl.redirect_url("/today", removed=["BBCA"]).startswith("/today?wl_removed=BBCA")
    assert wl.redirect_url("/today", removed=[]).startswith("/today?wl_notfound=1")
    assert wl.redirect_url("/today", error=True).startswith("/today?wl_err=1")
    assert wl.messages({"wl_removed": "BBCA"}) == [{"kind": "ok", "text": "Removed from your watchlist: BBCA."}]
    assert wl.messages({"wl_notfound": "1"})[0]["kind"] == "warn" and wl.messages({"wl_err": "1"})[0]["kind"] == "bad"
    assert "full" in wl.messages({"wl_full": "1"})[0]["text"] and "Type a stock code" in wl.messages({"wl_empty": "1"})[0]["text"]
    assert wl.messages({}) == []


def test_the_page_never_echoes_anything_but_well_formed_stock_codes():
    texts = " ".join(m["text"] for m in wl.messages({"wl_added": "<script>alert(1)</script>,BBCA", "wl_exists": "\"><img src=x>",
                                                      "wl_invalid": "<b>evil</b>", "wl_unknown": "javascript:alert(1)"}))
    assert "BBCA" in texts and "<" not in texts and "script" not in texts.lower() and "alert" not in texts and "evil" not in texts


# ---- the database part (a stand-in that answers by what the statement says) -----------------------------------------------------------

class FakeDB:
    def __init__(self, known=("BBCA", "TLKM", "BBRI", "ANTM"), rows=()):
        self.known, self.rows, self.log = set(known), list(rows), []

    def connect(self):
        db = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                db.log.append(sql)
                self.sql, self.params = sql, params

            def executemany(self, sql, seq):
                db.log.append(sql)
                for (t,) in seq:
                    if t not in db.rows:
                        db.rows.append(t)

            def fetchall(self):
                if "FROM tickers" in self.sql:
                    return [(t,) for t in self.params[0] if t in db.known]
                if "DELETE FROM user_watchlist" in self.sql:
                    gone = [t for t in self.params[0] if t in db.rows]
                    db.rows = [t for t in db.rows if t not in gone]
                    return [(t,) for t in gone]
                return [(t,) for t in db.rows]

        class Conn(Cur):
            def cursor(self):
                return Cur()

        return Conn()


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(wl, "connect", fake.connect)
    return fake


def test_adding_reports_what_happened_to_every_code_and_stores_only_the_new_listed_ones(db):
    db.rows = ["BBRI"]
    res = wl.add("bbca tlkm BBRI ZZZZ BB")
    assert res == {"added": ["BBCA", "TLKM"], "exists": ["BBRI"], "unknown": ["ZZZZ"], "invalid": ["BB"], "full": False, "empty": False}
    assert db.rows == ["BBRI", "BBCA", "TLKM"] and wl.load() == db.rows


def test_adding_nothing_changes_nothing_and_says_so(db):
    assert wl.add("   ")["empty"] is True and wl.add("")["empty"] is True and db.rows == [] and db.log == []
    res = wl.add("ZZ")                                           # something typed, but not a code: not "empty"
    assert res["empty"] is False and res["invalid"] == ["ZZ"] and db.rows == []


def test_a_full_list_refuses_more_without_losing_what_it_has(db, monkeypatch):
    monkeypatch.setattr(wl, "MAX_ITEMS", 3)
    db.rows = ["BBRI", "ANTM"]
    res = wl.add("BBCA TLKM")
    assert res["added"] == ["BBCA"] and res["full"] is True and len(db.rows) == 3


def test_removing_takes_one_stock_and_ignores_the_rest(db):
    db.rows = ["BBCA", "TLKM", "BBRI"]
    assert wl.remove("tlkm bbca") == ["TLKM"] and db.rows == ["BBCA", "BBRI"]      # only the first code of the request
    assert wl.remove("TLKM") == [] and wl.remove("") == [] and wl.remove("<x>") == []


def test_the_database_part_only_reads_inserts_and_deletes_the_watchlist_table(db):
    db.rows = ["BBRI"]
    wl.add("BBCA"), wl.remove("BBRI"), wl.load()
    assert all(re.match(r"\s*(SELECT|INSERT INTO user_watchlist|DELETE FROM user_watchlist)", q) for q in db.log)
    assert not any(w in q.upper() for q in db.log for w in ("DROP", "TRUNCATE", "UPDATE"))


def test_the_table_exists_in_the_schema_and_starts_empty():
    ddl = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    table = re.search(r"CREATE TABLE IF NOT EXISTS user_watchlist \((.*?)\n\);", ddl, re.DOTALL).group(1)
    assert "ticker    TEXT PRIMARY KEY" in table
    assert not re.search(r"INSERT INTO user_watchlist", ddl)                      # nothing is seeded


# ---- the pages and the routes -----------------------------------------------------------------------------------------------------

@pytest.fixture()
def page(client, monkeypatch):  # noqa: F811
    state = {"list": [], "calls": []}
    monkeypatch.setattr(wl, "load", lambda: list(state["list"]))
    monkeypatch.setattr(wl, "add", lambda raw: state["calls"].append(("add", raw)) or {"added": ["BBCA"], "exists": [], "unknown": [], "invalid": [], "full": False, "empty": False})
    monkeypatch.setattr(wl, "remove", lambda raw: state["calls"].append(("remove", raw)) or ["BBCA"])
    client.state = state
    return client


def test_an_empty_watchlist_is_shown_empty_with_a_form_to_fill_it(page):
    t = page.get("/today").text
    assert 'id="watchlist"' in t and "Your watchlist is empty." in t and "Nothing is filled in for you" in t
    assert 'action="/watchlist/add"' in t and 'name="tickers"' in t
    assert '<ul class="wl-chips"' not in t and 'action="/watchlist/remove"' not in t


def test_the_stocks_on_the_list_are_shown_with_a_remove_button_each(page):
    page.state["list"] = ["BBCA", "TLKM"]
    t = page.get("/today").text
    assert t.count('action="/watchlist/remove"') == 2 and "Your watchlist is empty." not in t
    assert 'aria-label="Remove BBCA from your watchlist"' in t and 'name="ticker" value="TLKM"' in t and 'href="/stock/BBCA"' in t


def test_the_configured_watchlist_is_not_shown_as_the_users_own(page, monkeypatch):
    monkeypatch.setattr(webapp.CONFIG, "watchlist", ("BBRI", "BBCA", "ADRO", "ANTM", "TLKM"), raising=False)
    t = page.get("/today").text
    assert "Your watchlist is empty." in t and 'aria-label="Remove' not in t


def test_the_helper_the_other_pages_use_never_breaks_a_page(monkeypatch):
    def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(wl, "load", boom)
    assert webapp._watchlist() == []                                     # no client fixture here, so this is the real helper
    monkeypatch.setattr(wl, "load", lambda: ["BBCA"])
    assert webapp._watchlist() == ["BBCA"]


def test_when_the_list_cannot_be_read_the_page_still_works_and_says_why(page, monkeypatch):
    def boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(wl, "load", boom)
    r = page.get("/today")
    assert r.status_code == 200 and "Could not read your watchlist" in r.text and "connection refused" in r.text
    assert "Your watchlist is empty." not in r.text and 'action="/watchlist/add"' in r.text


def test_the_outcome_of_a_change_is_shown_in_the_panel(page):
    t = page.get("/today?wl_added=BBCA&wl_unknown=ZZZZ").text
    assert "Added to your watchlist: BBCA." in t and "Not a listed stock, so not added: ZZZZ." in t and 'role="status"' in t
    assert "<script>alert" not in page.get("/today?wl_added=%3Cscript%3Ealert(1)%3C/script%3E").text


def test_adding_posts_the_typed_text_and_goes_back_with_the_outcome(page):
    r = page.post("/watchlist/add", data={"tickers": "bbca,tlkm", "next": "/today"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/today?wl_added=BBCA#watchlist"
    assert page.state["calls"] == [("add", "bbca,tlkm")]


def test_removing_posts_one_code_and_goes_back(page):
    r = page.post("/watchlist/remove", data={"ticker": "BBCA", "next": "/today"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/today?wl_removed=BBCA#watchlist"
    assert page.state["calls"] == [("remove", "BBCA")]


def test_the_return_address_cannot_be_pointed_elsewhere(page):
    r = page.post("/watchlist/add", data={"tickers": "BBCA", "next": "https://evil.example/"}, follow_redirects=False)
    assert r.headers["location"].startswith("/today?")
    assert page.post("/watchlist/add", data={"tickers": "BBCA"}, follow_redirects=False).headers["location"].startswith("/today?")


def test_a_post_from_another_site_is_refused_and_changes_nothing(page):
    for path, data in (("/watchlist/add", {"tickers": "BBCA"}), ("/watchlist/remove", {"ticker": "BBCA"})):
        r = page.post(path, data=data, headers={"Origin": "https://evil.example"}, follow_redirects=False)
        assert r.status_code == 403 and "Refused" in r.text
    assert page.state["calls"] == []
    ok = page.post("/watchlist/add", data={"tickers": "BBCA"}, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert ok.status_code == 303 and page.state["calls"] == [("add", "BBCA")]


def test_a_database_failure_on_a_change_comes_back_as_a_message_not_a_crash(page, monkeypatch):
    def boom(raw):
        raise RuntimeError("down")

    monkeypatch.setattr(wl, "add", boom)
    monkeypatch.setattr(wl, "remove", boom)
    for path, data in (("/watchlist/add", {"tickers": "BBCA"}), ("/watchlist/remove", {"ticker": "BBCA"})):
        r = page.post(path, data=data, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/today?wl_err=1")
    assert "the database did not answer" in page.get("/today?wl_err=1").text


def test_a_get_to_the_change_addresses_does_nothing(page):
    assert page.get("/watchlist/add").status_code == 405 and page.get("/watchlist/remove").status_code == 405
    assert page.state["calls"] == []


def test_a_null_origin_is_refused_and_a_forwarding_proxys_public_name_is_accepted(page):
    r = page.post("/watchlist/add", data={"tickers": "BBCA"}, headers={"Origin": "null"}, follow_redirects=False)     # what a sandboxed page sends
    assert r.status_code == 403 and page.state["calls"] == []
    ok = page.post("/watchlist/add", data={"tickers": "BBCA"}, follow_redirects=False,
                   headers={"Origin": "https://dash.example.ts.net", "X-Forwarded-Host": "dash.example.ts.net"})
    assert ok.status_code == 303 and page.state["calls"] == [("add", "BBCA")]


def test_the_referrer_policy_leaks_nothing_but_lets_the_browser_label_the_dashboards_own_posts():
    """`no-referrer` made browsers send `Origin: null` for every form post, so the dashboard's own buttons were refused (found in a real browser)."""
    from vg.web import auth

    assert (b"referrer-policy", b"same-origin") in auth._SECURITY_HEADERS
    assert not any(v == b"no-referrer" for _k, v in auth._SECURITY_HEADERS)
