"""The honest one-line reading per stock (vg.web.evidence): pure, no database."""

from vg.web import evidence as E
from vg.web import home
from vg.web.bpi_window import WindowScore
from vg.web.patterns import NetBuyer

STRONG = WindowScore(5, 5, 0.25, 0, 5, "YP", 5)
WEAK = WindowScore(5, 5, 0.02, 0, 2, "YP", 1)
SELLING = WindowScore(5, 5, -0.18, 0, 1, "CC", 1)
NET_OK = NetBuyer("YP", 5, 5, 50.0, 5, 0.20)


def test_signs_that_agree_read_as_agree_and_count():
    r = E.read(STRONG, NET_OK, haka=0.62, retail_div=E.RETAIL_SELL_INST_BUY)
    assert r["state"] == "agree" and (r["agree"], r["known"]) == (5, 5) and r["text"] == "5 of 5 agree"


def test_unknown_signs_are_not_counted_against_the_stock():
    """Order flow (HAKA) exists for watchlist stocks only, and retail divergence for engine-scored sessions only."""
    r = E.read(STRONG, NET_OK)
    assert (r["agree"], r["known"]) == (3, 3) and r["state"] == "agree"
    assert "unknown: Buyers lifting the offer" in r["title"]


def test_a_warning_overrides_agreement():
    r = E.read(STRONG, NET_OK, tags=["AKUM-ASING", "AKUM-NETRAL-WASH"])
    assert r["state"] == "warning" and r["warnings"] == ["wash"] and r["text"] == "Warning: wash"
    assert E.read(STRONG, NET_OK, crossing=0.99)["warnings"] == ["crossing"]
    assert E.read(SELLING)["warnings"] == ["dist"]


def test_weak_signs_are_mixed_and_no_data_is_unknown():
    assert E.read(WEAK, NetBuyer(None, 0, 5, 0.0, 5))["state"] == "mixed"
    r = E.read(None, None)
    assert r["state"] == "unknown" and r["text"] == "No broker data"


def test_short_bpi_coverage_is_unknown_not_no():
    short = WindowScore(5, 3, None, None, 2, None, 0)
    r = E.read(short)
    assert r["signs"]["bpi5"] is None and r["signs"]["buyer"] is None


def test_every_reading_carries_the_tested_note_and_no_advice_words():
    from vg.web.narrative import FORBIDDEN

    for r in (E.read(STRONG, NET_OK), E.read(SELLING), E.read(None, None)):
        assert E.TESTED_NOTE in r["title"]
        assert not any(w in (r["title"] + r["text"]).lower() for w in FORBIDDEN)


def _row(t, state_win, net=None, tags="", value=1e9):
    r = {"ticker": t, "close": 100.0, "prev": 100.0, "value": value, "tags": tags, "win": state_win,
         "pat": {"net": net, "comp": None, "surge": None, "cross": None}}
    return home._row(r, set(), set())


def test_clusters_split_agreement_and_warnings_and_views_filter():
    rows = [_row("AAAA", STRONG, NET_OK), _row("BBBB", WindowScore(5, 5, 0.12, 0, 4, "YP", 3)),
            _row("CCCC", SELLING), _row("DDDD", None)]
    c = home.clusters(rows)
    assert [r["ticker"] for r in c["agree"]] == ["AAAA"] and [r["ticker"] for r in c["warn"]] == ["CCCC"]
    assert [r["ticker"] for r in home.apply(rows, home.clean({"view": "warn"}))] == ["CCCC"]
    ordered = [r["ticker"] for r in home.apply(rows, home.clean({"sort": "evidence", "dir": "desc"}))]
    assert ordered[0] == "AAAA" and ordered[-1] == "DDDD"            # unknown always last


def test_four_boxes_and_their_views():
    from vg.web.patterns import Surge

    surge_row = home._row({"ticker": "SSSS", "close": 1.0, "prev": 1.0, "value": 1e9, "tags": "", "win": WEAK,
                           "pat": {"net": None, "comp": None, "surge": Surge(3.4, 5e9), "cross": None}}, set(), set())
    rows = [_row("AAAA", STRONG, NET_OK), surge_row, _row("CCCC", SELLING)]
    c = home.clusters(rows)
    assert [k for k, _t in c["order"]] == ["agree", "warn", "surge", "net"]
    assert [r["ticker"] for r in c["surge"]] == ["SSSS"] and [r["ticker"] for r in c["net"]] == ["AAAA"]
    assert (c["n_surge"], c["n_net"]) == (1, 1)
    assert [r["ticker"] for r in home.apply(rows, home.clean({"view": "surge"}))] == ["SSSS"]
    assert [r["ticker"] for r in home.apply(rows, home.clean({"view": "net"}))] == ["AAAA"]


def test_see_all_jumps_to_the_filtered_table():
    """7 Nov 2026: '?view=agree' did filter the table, but the top of the page looked the same, so it seemed nothing opened."""
    from pathlib import Path

    tpl = (Path(home.__file__).parent / "templates" / "home.html").read_text(encoding="utf-8")
    assert 'href="?view={{ key }}#stocks"' in tpl and 'id="stocks"' in tpl and "full list below" in tpl


def test_haka_line_matches_the_home_screen():
    assert E.HAKA_MIN == home.HAKA_DOMINANT


def test_indeterminate_retail_divergence_is_unknown():
    assert E.read(STRONG, retail_div="indeterminate")["signs"]["retail"] is None
