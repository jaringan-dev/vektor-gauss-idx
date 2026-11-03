"""Server-side stock list (vg.web.listing): filters, sorting, paging, links, and the /screener route."""

from datetime import date

import pytest

from vg.web import listing


def _row(tk, quad="AKUMULASI", value=10e9, d0=1.0, w1=1.0, chg=0.01, wl=False, tags="AKUM-ASING", cr3=0.5, cons=5, bkr=None, price=1000.0):
    names = [t for t in tags.split(",") if t]
    return {"ticker": tk, "quad": quad, "value_raw": value, "nff_d0_raw": d0, "nff_w1_raw": w1, "chg_raw": chg, "in_wl": wl, "held": False,
            "n_tags": len(names), "cr3_raw": cr3, "cons_raw": cons, "bkr_raw": bkr, "price_raw": price,
            "search": f"{tk} {tags} {quad}".upper(), "price": "1,000", "chg": {"t": "+1.0%", "c": "pos"}, "nff_d0": "+1B", "nff_d0_c": "pos",
            "nff_w1": "+1B", "nff_w1_c": "pos", "bkr_net": "n/a", "bkr_net_c": "", "cr3": "50%", "cons": "5/10", "ksei": "↑",
            "tags": tags, "tag_list": names, "in_wl_": wl}


ROWS = [_row("AAAA", "MARKUP", 20e9, 5, 50, 0.03), _row("BBBB", "MARKDOWN", 1e9, -3, -40, -0.02, tags="MARKDOWN,DOM-RETAIL"),
        _row("CCCC", "AKUMULASI", 8e9, 2, 10, 0.0, wl=True), _row("DDDD", "DISTRIBUSI", 6e9, None, None, None, tags="")]


def _tickers(p):
    return [r["ticker"] for r in listing.apply(ROWS, listing.clean(p))]


def test_default_order_is_one_week_foreign_flow_largest_first_and_missing_values_go_last():
    assert _tickers({}) == ["AAAA", "CCCC", "BBBB", "DDDD"]
    assert _tickers({"sort": "foreign_1w", "dir": "asc"}) == ["BBBB", "CCCC", "AAAA", "DDDD"]          # still last


@pytest.mark.parametrize("params,expected", [
    ({"quad": "MARKUP"}, ["AAAA"]), ({"liquid": "1"}, ["AAAA", "CCCC", "DDDD"]),
    ({"flow": "in"}, ["AAAA", "CCCC"]), ({"flow": "out"}, ["BBBB"]), ({"wl": "1"}, ["CCCC"]),
    ({"q": "dom-retail"}, ["BBBB"]), ({"q": "bb"}, ["BBBB"]), ({"q": "zzz"}, []),
    ({"quad": "NOPE"}, ["AAAA", "CCCC", "BBBB", "DDDD"]),                      # unknown values are ignored, not trusted
])
def test_filters(params, expected):
    assert _tickers(params) == expected


def test_filters_combine():
    assert _tickers({"liquid": "1", "flow": "in", "quad": "AKUMULASI"}) == ["CCCC"]


def test_sorting_by_each_column_uses_a_sensible_first_direction():
    assert _tickers({"sort": "ticker"}) == ["AAAA", "BBBB", "CCCC", "DDDD"]
    assert _tickers({"sort": "ticker", "dir": "desc"}) == ["DDDD", "CCCC", "BBBB", "AAAA"]
    assert _tickers({"sort": "change"})[:2] == ["AAAA", "CCCC"] and _tickers({"sort": "patterns"})[0] == "BBBB"
    assert listing.clean({"sort": "bogus"})["sort"] == "foreign_1w" and listing.clean({"dir": "sideways"})["dir"] == "desc"


def test_paging_math_and_clamping():
    pg = listing.paginate(603, 3)
    assert (pg["pages"], pg["start"], pg["end"], pg["has_prev"], pg["has_next"]) == (13, 100, 150, True, True)
    last = listing.paginate(603, 99)
    assert last["page"] == 13 and last["end"] == 603 and not last["has_next"]
    assert listing.paginate(0, 1)["pages"] == 1 and listing.clean({"page": "abc"})["page"] == 1 and listing.clean({"page": "-4"})["page"] == 1


def test_links_keep_state_leave_out_defaults_and_return_to_page_one_when_filters_change():
    p = listing.clean({"quad": "MARKUP", "page": "3"})
    assert listing.query(p, liquid=1) == "?quad=MARKUP&liquid=1"                  # page reset, default sort omitted
    assert listing.query(p, page=4) == "?quad=MARKUP&page=4"
    assert listing.query(listing.DEFAULTS) == "?"
    assert "sort=price" in listing.query(p, sort="price", dir="desc") and "page" not in listing.query(p, sort="price", dir="desc")


def test_sort_links_toggle_direction_and_show_the_active_arrow():
    p = listing.clean({})
    first = listing.sort_link(p, "price")
    assert first["arrow"] == "" and "sort=price" in first["href"] and "dir=desc" in first["href"]
    cur = listing.sort_link(p, "foreign_1w")
    assert cur["active"] and cur["arrow"] == "▼" and "dir=asc" in cur["href"]
    assert listing.sort_link(listing.clean({"sort": "foreign_1w", "dir": "asc"}), "foreign_1w")["arrow"] == "▲"


def test_chips_toggle_single_choice_groups():
    p = listing.clean({"quad": "MARKUP"})
    ch = {c["label"]: c for c in listing.chips(p)}
    assert ch["Markup"]["on"] and ch["Markup"]["href"] == "?"                          # clicking the active chip clears it
    assert not ch["Markdown"]["on"] and "quad=MARKDOWN" in ch["Markdown"]["href"]
    assert "liquid=1" in ch["Liquid stocks only"]["href"] and "quad=MARKUP" in ch["Liquid stocks only"]["href"]


# ---- route -------------------------------------------------------------------------------------

def test_screener_route_pages_filters_and_stays_small(monkeypatch):
    pytest.importorskip("psycopg")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from vg.web import app as webapp
    from vg.web import freshness

    eod = date(2026, 10, 30)
    monkeypatch.setattr(freshness, "load_inputs", lambda today=None: freshness.FreshInputs(
        today=date(2026, 10, 31), eod_date=eod, price_counts={eod: 962}, broker_counts={eod: 190}, ksei_month_end=date(2026, 9, 30)))
    big = [_row(f"T{i:03d}", w1=1000 - i, value=10e9 if i % 2 else 1e9) for i in range(120)]
    monkeypatch.setattr(webapp, "_screener_view", lambda: (eod, big))
    webapp._STRIP_CACHE.clear()
    c = TestClient(webapp.app)
    r = c.get("/screener")
    assert r.status_code == 200 and "Showing 1–50 of 120" in r.text
    assert "T000" in r.text and "T049" in r.text and "T050" not in r.text
    assert 'rel="next"' in r.text and "Page 1 of 3" in r.text
    p3 = c.get("/screener?page=3")
    assert "Showing 101–120 of 120" in p3.text and "T119" in p3.text
    f = c.get("/screener?liquid=1")
    assert "Showing 1–50 of 60 (filtered from 120)" in f.text and "Clear filters" in f.text and 'class="fchip on"' in f.text
    none = c.get("/screener?q=zzz")
    assert "No stock matches these filters." in none.text
    s = c.get("/screener?sort=ticker&dir=asc")
    assert 'aria-sort="ascending"' in s.text and s.text.index("T000") < s.text.index("T001")
    assert len(r.text) < 200_000                                                      # 120 rows no longer means every row on one page
    assert c.get("/screener?page=abc&sort=bogus&quad=evil<script>").status_code == 200
