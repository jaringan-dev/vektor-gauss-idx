"""Template-level regression tests (no DB, no FastAPI): the honesty/accessibility fixes
from CHECKLIST-UI-UX.md — E1 (no stale coverage claims), C4 (quadrant has text), E2 ("small n")."""

import re
from datetime import date
from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")

from vg.web import glossary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "src" / "vg" / "web" / "templates"

ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES)),
    autoescape=jinja2.select_autoescape(["html"]),
)
glossary.install(ENV)


def _cell(t="+1,00%", c="pos"):
    return {"t": t, "c": c}


def _panel(total, rows):
    return {"title": "Top Akum Asing", "note": "arus asing", "total": total, "rows": rows}


def _rank_row(quad="AKUMULASI", emoji="🟢"):
    return {"rank": 1, "ticker": "BBCA", "quad": quad, "quad_emoji": emoji, "val": "+185,6B",
            "val_c": "pos", "price": "6,075", "chg": _cell(), "tags": "AKUM-ASING", "tag_list": ["AKUM-ASING"]}


# ---- A1/A2: shell (sidebar, skip-link, hamburger) and the freshness strip ------------------

NAV = [
    {"title": "Patterns", "items": [{"key": "tracker", "label": "Track record", "href": "/"}]},
    {"title": "Saham", "items": [
        {"key": "screener", "label": "Saham hari ini", "href": "/screener"},
        {"key": "ranking", "label": "Top / Ranking", "href": "/ranking"}]},
]


def _strip(*items, error=None):
    return {"items": list(items), "level": "ok", "checked_at": "21:40", "error": error}


def _it(key, label, text, level, symbol, word):
    return {"key": key, "label": label, "text": text, "level": level, "symbol": symbol, "word": word}


OK_STRIP = _strip(_it("eod", "EOD", "30 Oct 2026", "ok", "✓", "normal"))

PAGES = {
    "tracker": ("forward_test.html", dict(forward_since=None, cost_bp=170, min_n=50, min_span=60,
                                          insample=[], forward=[], computed_at=None, cache_hit=False)),
    "screener": ("screener.html", dict(session_date=None, rows=[])),
    "ranking": ("ranking.html", dict(session_date=None, h="5d", panels=[])),
}


def _page(active, strip=OK_STRIP, **over):
    name, base = PAGES[active]
    ctx = dict(base, active_page=active, error=None, nav_groups=NAV, strip=strip)
    ctx.update(over)
    return ENV.get_template(name).render(ctx)


def _current_links(html: str) -> list[str]:
    """The real <a> tags marked aria-current — NOT the CSS selector that mentions the attribute."""
    return re.findall(r'<a [^>]*aria-current="page"[^>]*>', html)


@pytest.mark.parametrize("active", list(PAGES))
def test_sidebar_lists_every_route_and_marks_only_the_active_one(active):
    out = _page(active)
    for href in ("/", "/screener", "/ranking"):
        assert f'href="{href}"' in out
    current = _current_links(out)
    assert len(current) == 1
    expected = {"tracker": 'href="/"', "screener": 'href="/screener"', "ranking": 'href="/ranking"'}[active]
    assert expected in current[0]


@pytest.mark.parametrize("active", list(PAGES))
def test_skip_link_and_main_landmark(active):
    out = _page(active)
    assert 'class="skip" href="#main"' in out
    assert '<main id="main"' in out


@pytest.mark.parametrize("active", list(PAGES))
def test_hamburger_is_wired_for_keyboard_and_screen_readers(active):
    out = _page(active)
    assert 'id="menu-btn"' in out and 'aria-controls="sidebar"' in out and 'aria-expanded="false"' in out
    assert 'id="sidebar"' in out
    assert "Escape" in out                                   # keyboard can close it


@pytest.mark.parametrize("active", list(PAGES))
def test_exactly_one_h1_per_page(active):
    assert _page(active).count("<h1") == 1


def test_strip_shows_text_symbol_and_status_word_never_color_only():
    bad = _it("price", "Prices", "5 stocks — gap (normal ≈ 962)", "bad", "✖", "problem")
    out = _page("tracker", strip=_strip(OK_STRIP["items"][0], bad))
    assert 'class="lv-bad"' in out
    assert "5 stocks — gap (normal ≈ 962)" in out
    assert "✖" in out and "status problem" in out          # symbol + a screen-reader word
    assert 'class="lv-ok"' in out and "status normal" in out
    assert "checked 21:40" in out


def test_strip_error_is_shown_as_a_red_strip_not_hidden():
    out = _page("tracker", strip=_strip(error="OperationalError: connection refused"))
    assert "Data freshness could not be checked" in out and "connection refused" in out
    assert 'class="lv-bad"' in out


def test_page_without_strip_context_still_renders_without_a_strip():
    out = ENV.get_template("screener.html").render(active_page="screener", error=None,
                                                   session_date=None, rows=[])
    assert 'class="strip"' not in out and "<h1" in out        # tolerant of legacy callers


# ---- F1: coverage heatmap + "what is missing" page ----------------------------------------

def _coverage_ctx(**over):
    from datetime import timedelta

    from vg.web import coverage as cov

    days, d = [], date(2026, 10, 30)
    while len(days) < 40:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.sort()
    price = {x: 962 for x in days}
    for g in (date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 25)):
        price[g] = 5
    assert days[0] == date(2026, 9, 7)                              # 40 weekdays back from 30 Oct
    broker = {x: 190 for x in days if x >= date(2026, 9, 21)}      # began mid-window -> earlier days = na
    rows, summary = cov.grid_view(cov.build_grid(days, {"price": price, "broker": broker}))
    ctx = dict(active_page="coverage", error=None, n_days=40, windows=(30, 60, 120, 250),
               rows=rows, summary=summary, nav_groups=NAV, strip=OK_STRIP)
    ctx.update(over)
    return ENV.get_template("coverage.html").render(ctx)


def test_coverage_incident_days_are_red_links_to_the_missing_list():
    out = _coverage_ctx()
    for iso in ("2026-09-14", "2026-09-15", "2026-09-25"):
        assert f'href="/coverage/missing?date={iso}&amp;source=price"' in out
    assert out.count('class="cov-bad"') >= 3
    assert "gap" in out and "✖" in out


def test_coverage_healthy_and_not_yet_existing_cells_are_not_links():
    out = _coverage_ctx()
    assert out.count("/coverage/missing?date=2026-10-30&amp;source=price") == 0     # a healthy day
    assert "date=2026-09-10&amp;source=broker" not in out                           # before broker existed: no link
    assert "no data yet" in out                                                   # na is announced, not blank


def test_coverage_cells_carry_text_not_just_colour_and_a_label_for_screen_readers():
    out = _coverage_ctx()
    assert 'aria-label="Prices 15 Sep 2026: gap, 5 against a normal of about 962' in out
    assert "<span aria-hidden=\"true\">✓</span> 962" in out


def test_coverage_table_is_accessible_and_explains_its_legend():
    out = _coverage_ctx()
    assert out.count('<caption class="sr-only">') == 2
    assert 'scope="col"' in out and 'scope="row"' in out
    for text in ("normal (≥ 90% of normal)", "partial", "(50–90%)", "gap", "(&lt; 50%)"):
        assert text in out


def test_coverage_window_switch_marks_the_current_one():
    out = _coverage_ctx(n_days=60)
    assert 'href="/coverage?days=60" class="active" aria-current="true"' in out
    assert 'href="/coverage?days=30">' in out


def test_coverage_error_and_empty_states():
    assert "Cannot read the data" in _coverage_ctx(error="OperationalError: down", rows=[], summary=[])
    assert "No trading days yet" in _coverage_ctx(rows=[], summary=[])


def _missing_ctx(**over):
    m = {"source": "price", "source_label": "Prices", "unit": "stocks", "date_label": "15 Sep 2026",
         "iso": "2026-09-15", "cell": {"level": "bad", "count": 5, "symbol": "✖", "word": "gap", "pct": 1},
         "baseline": 962, "present": 5, "ref_label": "16 Sep 2026", "expected": 962,
         "missing_total": 957, "missing": ["AALI", "ABBA", "ADRO"],
         "commands": ["vg collect-price --date 2026-09-15", "vg compute --date 2026-09-15"]}
    ctx = dict(active_page="coverage", error=None, bad_request=None, m=m, nav_groups=NAV, strip=OK_STRIP)
    ctx.update(over)
    return ENV.get_template("coverage_missing.html").render(ctx)


def test_missing_page_lists_names_reference_day_and_exact_repair_commands():
    out = _missing_ctx()
    assert "Prices — 15 Sep 2026" in out and "gap" in out
    assert "5 stocks available; normal ≈ 962" in out and "(1% of normal)" in out
    assert "16 Sep 2026" in out and "962 stocks" in out
    for t in ("AALI", "ABBA", "ADRO"):
        assert f'class="chip">{t}<' in out
    assert "Missing 957 of 962" in out and "and 954 more" in out         # truncated list is disclosed
    assert "cd ~/vektor-gauss &amp;&amp; source .venv/bin/activate\nvg collect-price --date 2026-09-15\n" in out
    assert "vg compute --date 2026-09-15" in out


def test_missing_page_when_nothing_is_missing_or_no_reference_exists():
    ok = _missing_ctx(m={"source": "broker", "source_label": "Broker", "unit": "stocks",
                         "date_label": "8 Sep 2026", "iso": "2026-09-08",
                         "cell": {"level": "ok", "count": 190, "symbol": "✓", "word": "normal", "pct": 100},
                         "baseline": 190, "present": 190, "ref_label": "9 Sep 2026", "expected": 190,
                         "missing_total": 0, "missing": [], "commands": ["vg compute --date 2026-09-08"]})
    assert "Nothing is missing relative to the reference day" in ok
    none = _missing_ctx(m={"source": "price", "source_label": "Prices", "unit": "stocks", "date_label": "1 Sep 2026",
                           "iso": "2026-09-01", "cell": {"level": "bad", "count": 0, "symbol": "✖", "word": "gap", "pct": 0},
                           "baseline": 962, "present": 0, "ref_label": None, "expected": 0,
                           "missing_total": 0, "missing": [], "commands": ["vg compute --date 2026-09-01"]})
    assert "no normal day in this range" in none


def test_missing_page_bad_request_and_error_states():
    bad = _missing_ctx(m=None, bad_request="Invalid date: 'x'. Use the format YYYY-MM-DD.")
    assert "That request cannot be processed" in bad and "Invalid date" in bad and 'href="/coverage"' in bad
    assert "Cannot read the data" in _missing_ctx(m=None, error="OperationalError: down")
    assert "<h1>Data coverage &amp; gaps</h1>" in bad


def test_strip_links_to_the_coverage_page():
    assert 'href="/coverage">data coverage</a>' in _page("tracker")


# ---- E1: no hard-coded coverage numbers that can drift from the live count ------------

def test_ranking_has_no_stale_coverage_claims():
    out = ENV.get_template("ranking.html").render(
        active_page="ranking", error=None, session_date=date(2026, 10, 30), h="5d",
        panels=[_panel(834, [_rank_row()])],
    )
    assert "thousands" not in out and "dozens" not in out
    assert "ranked from 834 stocks" in out              # the live count is the source of truth
    assert "Each panel says how many stocks it was ranked from" in out


@pytest.mark.parametrize("path", sorted(TEMPLATES.glob("*.html")) + [ROOT / "src" / "vg" / "engine" / "__init__.py"])
def test_no_hardcoded_stale_coverage_text_in_sources(path):
    text = path.read_text(encoding="utf-8")
    for stale in ("dozens of stocks", "thousands of stocks", "~64 only", "~64, 1D"):
        assert stale not in text, f"{path.name} still contains stale coverage text {stale!r}"


# ---- C4: quadrant is text, not emoji-only ----------------------------------------------

def test_ranking_quadrant_shows_text_next_to_emoji():
    out = ENV.get_template("ranking.html").render(
        active_page="ranking", error=None, session_date=date(2026, 10, 30), h="5d",
        panels=[_panel(834, [_rank_row("DISTRIBUSI", "🔻"), _rank_row("", "▪️")])],
    )
    assert ">Distribution</a>" in out                   # the quadrant is a word with an explanation link
    assert 'class="quad">—<' in out                     # unknown quadrant shows a dash, never blank


def test_screener_quadrant_shows_text_next_to_emoji():
    row = {"ticker": "BBCA", "in_wl": True, "held": False, "quad": "AKUMULASI", "quad_emoji": "🟢",
           "price": "6,075", "chg": _cell(), "nff_d0": "+1B", "nff_d0_c": "pos", "nff_w1": "+2B",
           "nff_w1_c": "pos", "bkr_net": "n/a", "bkr_net_c": "", "cr3": "—", "cons": "7/10",
           "ksei": "↑", "tags": "AKUM-ASING", "tag_list": ["AKUM-ASING"]}
    from vg.web import listing

    p = listing.clean({})
    out = ENV.get_template("screener.html").render(
        active_page="screener", error=None, session_date=date(2026, 10, 30), rows=[row], p=p, total_all=1, pg=listing.paginate(1, 1),
        chips=listing.chips(p), sorts={k: listing.sort_link(p, k) for k in listing.SORTS}, q_text="", pg_prev=None, pg_next=None,
        filtered=False, clear="?")
    assert "Quadrant</a></th>" in out
    assert ">Accumulation</a>" in out
    assert 'href="/stock/BBCA"' in out                  # every ticker links to its detail page
    assert 'td title=' not in out                       # meaning no longer lives only in a tooltip


# ---- E2: small-n label + computed-at note ------------------------------------------------

def _tag_row(tag, n, small):
    return {"tag": tag, "n": n, "small_n": small, "n_liquid": n // 2, "span_days": 30,
            "first_date": date(2026, 9, 4), "last_date": date(2026, 10, 15),
            "edge_h1": _cell(), "edge_h5": _cell(), "edge_h10": _cell(), "edge_h5_liquid": _cell(),
            "hit": "50%", "univ_hit": "45%",
            "status": type("S", (), {"code": "probation", "label": "PROBATION"})(),
            "edge_after_cost": _cell("-0.70%", "neg")}


def _tracker(**over):
    ctx = dict(active_page="tracker", forward_since=date(2026, 9, 4), cost_bp=170, min_n=50,
               min_span=60, error=None, insample=[], computed_at="31 Oct 2026 21:40:00",
               cache_hit=False,
               forward=[_tag_row("BROKER-ASING-AKUM", 1320, False), _tag_row("FLOAT-ASING-PADAT", 34, True)])
    ctx.update(over)
    return ENV.get_template("forward_test.html").render(ctx)


def test_small_n_tag_is_labelled_and_large_n_is_not():
    out = _tracker()
    rows = out.split("<tr>")
    seg_small = next(r for r in rows if "/pattern/FLOAT-ASING-PADAT" in r)
    seg_large = next(r for r in rows if "/pattern/BROKER-ASING-AKUM" in r)
    assert "small n" in seg_small
    assert "small n" not in seg_large


def test_tracker_states_the_rule_and_when_it_was_computed():
    out = _tracker()
    assert "n &lt; 50" in out and "always sorted" in out       # the rule is explained on the page
    assert "Calculated 31 Oct 2026 21:40:00" in out


def test_cache_hit_is_disclosed_and_fresh_hint_shown():
    hit = _tracker(cache_hit=True, cache_minutes=30)
    assert "?fresh=1" in hit and "from a 30-minute cache" in hit   # window comes from the real TTL
    assert "?fresh=1" not in _tracker(cache_hit=False, cache_minutes=30)
