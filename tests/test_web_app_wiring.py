"""Wiring of vg.web.app (needs the Postgres driver that the app imports; DB itself is stubbed)."""

import re
from datetime import date, timedelta

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from vg.web import app as webapp  # noqa: E402
from vg.web import freshness  # noqa: E402

_NOT_UI = {"/healthz", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _ui_routes():
    return {r.path for r in webapp.app.routes
            if "GET" in (getattr(r, "methods", None) or ()) and r.path not in _NOT_UI}


_DETAIL_ROUTES = {"/coverage/missing", "/stock/{ticker}", "/search", "/pattern/{tag}"}       # reached by clicking inside a screen, not from the sidebar


# Top-bar addresses that serve the same page as a sidebar screen (aliases, not new screens): alias -> the sidebar address.
_ALIASES = {"/eval": "/track-record", "/system": "/health"}


def test_every_html_route_has_a_sidebar_link_and_vice_versa():
    """A new screen that forgets to register in _NAV (or a link to a deleted route) fails here."""
    nav_hrefs = {it["href"] for g in webapp._NAV for it in g["items"]}
    assert _ui_routes() - _DETAIL_ROUTES - set(_ALIASES) == nav_hrefs
    assert _DETAIL_ROUTES | set(_ALIASES) <= _ui_routes()    # and the exemption lists have no ghosts
    assert set(_ALIASES.values()) <= nav_hrefs                # an alias always points at a real sidebar screen


def test_every_nav_key_is_an_active_page_used_by_a_route():
    keys = {it["key"] for g in webapp._NAV for it in g["items"]}
    assert keys == {"home", "today", "tracker", "risk", "research", "screener", "ranking", "rotation", "health", "coverage", "guide"}   # the active_page values the routes pass


@pytest.fixture()
def client(monkeypatch):
    """App with the database stubbed out: empty data, a healthy freshness reading."""
    eod = date(2026, 10, 30)
    days = [eod - timedelta(days=i) for i in range(30) if (eod - timedelta(days=i)).weekday() < 5]
    healthy = freshness.FreshInputs(
        today=date(2026, 10, 31), eod_date=eod,
        price_counts={d: 962 for d in days}, broker_counts={d: 190 for d in days},
        ksei_month_end=date(2026, 9, 30))
    monkeypatch.setattr(freshness, "load_inputs", lambda today=None: healthy)
    monkeypatch.setattr(webapp, "_eval_version", lambda: ("ts", 1))
    monkeypatch.setattr(webapp.engine, "eval_tags", lambda since=None: [])
    monkeypatch.setattr(webapp.engine, "tag_monthly_edge", lambda since=None: {})
    monkeypatch.setattr(webapp.engine, "latest_signal_date", lambda: None)
    monkeypatch.setattr(webapp.engine, "rank_components", lambda *a, **k: (None, 0, []))
    webapp._STRIP_CACHE.clear()
    webapp._EVAL_CACHE.clear()
    return TestClient(webapp.app)


@pytest.mark.parametrize("path,active", [("/", "home"), ("/today", "today"), ("/track-record", "tracker"), ("/screener", "screener"), ("/ranking", "ranking")])
def test_pages_render_inside_the_shell_with_a_healthy_strip(client, path, active):
    r = client.get(path)
    assert r.status_code == 200
    current = re.findall(r'<a [^>]*aria-current="page"[^>]*>', r.text)   # real tags, not the CSS selector
    assert len(current) == 1
    expected = {"home": 'href="/"', "today": 'href="/today"', "tracker": 'href="/track-record"', "screener": 'href="/screener"', "ranking": 'href="/ranking"'}[active]
    assert expected in current[0]
    assert "Data freshness" in r.text and "962 stocks" in r.text and "30 Oct 2026" in r.text
    assert 'class="lv-bad"' not in r.text and 'class="lv-warn"' not in r.text


def test_a_price_gap_turns_the_strip_red_on_every_page(client, monkeypatch):
    eod = date(2026, 10, 30)
    days = [eod - timedelta(days=i) for i in range(30) if (eod - timedelta(days=i)).weekday() < 5]
    counts = {d: 962 for d in days}
    counts[eod] = 5                                           # the 14/15/25 Sep situation
    broken = freshness.FreshInputs(today=date(2026, 10, 31), eod_date=eod, price_counts=counts,
                                   broker_counts={d: 190 for d in days}, ksei_month_end=date(2026, 9, 30))
    monkeypatch.setattr(freshness, "load_inputs", lambda today=None: broken)
    webapp._STRIP_CACHE.clear()
    for path in ("/", "/today", "/track-record", "/screener", "/ranking"):
        r = client.get(path)
        assert r.status_code == 200
        assert 'class="lv-bad"' in r.text and "gap" in r.text


def test_a_failing_freshness_query_does_not_break_the_page(client, monkeypatch):
    def boom(today=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(freshness, "load_inputs", boom)
    webapp._STRIP_CACHE.clear()
    r = client.get("/screener")
    assert r.status_code == 200
    assert "Data freshness could not be checked" in r.text and "db down" in r.text


# ---- F1: coverage pages (DB stubbed with the real incident's numbers) --------------------------

_ALL = [f"T{i:03d}" for i in range(962)]
_GAP = {date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 25)}


def _weekdays(n, end=date(2026, 10, 30)):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


@pytest.fixture()
def cov_client(client, monkeypatch):
    all_days = _weekdays(250)
    price = {d: (5 if d in _GAP else 962) for d in all_days}

    def load_counts(n_days=60):
        days = all_days[-n_days:]
        keep = set(days)
        return days, {"price": {d: v for d, v in price.items() if d in keep},
                      "index": {d: 57 for d in days}, "broker": {}, "signal": {}}

    def load_tickers(source, d):
        return set(_ALL[:5]) if price.get(d, 0) < 100 else set(_ALL)

    monkeypatch.setattr(webapp.cov, "load_counts", load_counts)
    monkeypatch.setattr(webapp.cov, "load_tickers", load_tickers)
    webapp._COVERAGE_CACHE.clear()
    return client


def test_coverage_page_shows_exactly_the_incident_days_as_links(cov_client):
    r = cov_client.get("/coverage")
    assert r.status_code == 200
    links = set(re.findall(r'/coverage/missing\?date=(\d{4}-\d{2}-\d{2})&amp;source=price', r.text))
    assert links == {d.isoformat() for d in _GAP}
    assert "Last 60 trading days" in r.text


@pytest.mark.parametrize("asked,used", [(9999, 250), (1, 10), (0, 10), (-5, 10), (60, 60)])
def test_coverage_window_is_clamped(cov_client, asked, used):
    assert f"Last {used} trading days" in cov_client.get(f"/coverage?days={asked}").text


def test_missing_page_for_the_incident_day(cov_client):
    r = cov_client.get("/coverage/missing?date=2026-09-15&source=price")
    assert r.status_code == 200
    assert "Missing 957 of 962" in r.text
    assert "16 Sep 2026" in r.text                              # nearest HEALTHY day (14th is a gap too)
    assert "vg collect-price --date 2026-09-15" in r.text
    assert "vg compute --date 2026-09-15" in r.text
    assert 'class="chip">T004<' not in r.text and 'class="chip">T005<' in r.text   # first 5 present, rest missing


@pytest.mark.parametrize("path,status,fragment", [
    ("/coverage/missing?date=2026-09-15&source=nope", 400, "Unknown source"),
    ("/coverage/missing?date=banana&source=price", 400, "Invalid date"),
    ("/coverage/missing?source=price", 400, "Invalid date"),
    ("/coverage/missing?date=2026-09-12&source=price", 404, "is not one of the last 250 trading days"),       # a Saturday
    ("/coverage/missing?date=2019-01-02&source=price", 404, "is not one of the last 250 trading days"),       # outside the window
])
def test_missing_page_rejects_bad_input_with_a_real_status_code(cov_client, path, status, fragment):
    r = cov_client.get(path)
    assert r.status_code == status
    assert fragment in r.text and 'href="/coverage"' in r.text


def test_coverage_pages_are_highlighted_under_the_data_group(cov_client):
    for path in ("/coverage", "/coverage/missing?date=2026-09-15&source=price",
                 "/coverage/missing?date=banana&source=price"):
        current = re.findall(r'<a [^>]*aria-current="page"[^>]*>', cov_client.get(path).text)
        assert len(current) == 1 and 'href="/coverage"' in current[0]


def test_a_database_failure_on_the_coverage_pages_is_an_error_panel_not_a_crash(cov_client, monkeypatch):
    def boom(n_days=60):
        raise RuntimeError("db down")

    monkeypatch.setattr(webapp.cov, "load_counts", boom)
    webapp._COVERAGE_CACHE.clear()
    for path in ("/coverage", "/coverage/missing?date=2026-09-15&source=price"):
        r = cov_client.get(path)
        assert r.status_code == 200 and "Cannot read the data" in r.text and "db down" in r.text


def test_every_recommended_repair_command_is_a_real_cli_command():
    """The page must never suggest a command that does not exist (e.g. a flag nobody built)."""
    import shlex

    from vg.__main__ import build_parser

    parser = build_parser()
    for source in webapp.cov.SOURCE_KEYS:
        for cmd in webapp.cov.repair_commands(source, date(2026, 9, 15)):
            argv = shlex.split(cmd.split("#")[0])
            assert argv[0] == "vg"
            parser.parse_args(argv[1:])         # SystemExit here == a command the CLI rejects
