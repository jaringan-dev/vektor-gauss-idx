"""Stock page: KSEI balance-position chart and the ownership-filings table. Pure functions first, then the page with the database stubbed."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from vg.web import app as webapp
from vg.web import charts, freshness
from vg.web import stock as stockmod

from .test_web_stock import TRACK, _raw


def _month(i, foreign_share=0.6, total=1_000_000_000_000):
    """Month i counted from Jan 2025: a simple split of `total` shares between a few holder types."""
    y, m = 2025 + i // 12, i % 12 + 1
    f, l = total * foreign_share, total * (1 - foreign_share)
    return {"d": date(y, m, 28),
            "local": {"ID": l * 0.7, "CP": l * 0.2, "MF": l * 0.1, "IB": None},
            "foreign": {"CP": f * 0.9, "MF": f * 0.1}}


def _series(n=14, **kw):
    return [_month(i, **kw) for i in range(n)]


def _detail(m):
    return {"local": dict(m["local"]), "foreign": dict(m["foreign"]), "price": 100, "sec_num": 1, "scripless_pct": 100}


def _rows(n=14, **kw):
    return [(m["d"], _detail(m)) for m in _series(n, **kw)]


# ---- chart -------------------------------------------------------------------------------------------------

def test_group_shares_derives_local_institutions_as_local_total_minus_individuals():
    g = charts.group_shares({"local": {"ID": 70, "CP": 20, "MF": 10, "TOTAL": 999}, "foreign": {"CP": 5, "XX": 7}})
    assert g == {"local_ind": 70, "local_inst": 30, "foreign": 5}                    # TOTAL and unknown codes never count


@pytest.mark.parametrize("v,expected", [(0, 1.0), (3.7e9, 4e9), (4.0e9, 4e9), (2.99e8, 3e8), (900, 1000), (2_100, 2_500), (12, 15)])
def test_nice_ceil_gives_a_clean_top_gridline(v, expected):
    assert charts._nice_ceil(v) == expected


def test_ownership_chart_needs_three_usable_months():
    assert charts.ownership_stack(_series(2)) is None
    empty = [{"d": date(2025, i, 1), "local": {}, "foreign": {}} for i in range(1, 6)]
    assert charts.ownership_stack(empty) is None                                      # months with nothing held are not usable


def test_ownership_chart_is_an_accessible_svg_with_exact_numbers_in_the_hover_text():
    svg = charts.ownership_stack(_series(14))
    assert svg.startswith("<svg") and 'role="img"' in svg and "<title" in svg and "<desc" in svg
    assert "KSEI balance position by holder type" in svg
    assert "Local individual: " in svg and "Foreign corporate: " in svg and "% of all)" in svg
    assert "Foreign" in svg and "100%" in svg and "lot" in svg
    assert "None" not in svg and "nan" not in svg.lower()


def test_ownership_chart_draws_one_slice_per_nonzero_category_and_a_three_group_strip():
    svg = charts.ownership_stack(_series(3))
    stack = len(re.findall(r"<title>[^<]* lot \(", svg))
    strip = len(re.findall(r"<title>[A-Z][a-z]{2} \d{4} · (?:Local individual|Local institution|Foreign): [\d.]+% of all shares", svg))
    assert stack == 3 * 5                                                              # ID, CP, MF local + CP, MF foreign (IB is None -> skipped)
    assert strip == 3 * 3


def test_ownership_chart_phone_variant_is_narrower_with_fewer_month_labels():
    wide, narrow = charts.ownership_stack(_series(24)), charts.ownership_stack(_series(24), width=charts.W_MOBILE, uid="own-m")
    assert 'viewBox="0 0 720' in wide and f'viewBox="0 0 {charts.W_MOBILE}' in narrow
    labels = lambda s: len(re.findall(r'text-anchor="middle"', s))
    assert labels(wide) == 24 and labels(narrow) == 8                                  # every month vs every third


def test_chart_never_breaks_on_zero_or_missing_values():
    months = _series(5)
    months[2]["local"] = {"ID": 0, "CP": None}
    months[2]["foreign"] = {"CP": 10.0}
    assert charts.ownership_stack(months).startswith("<svg")


# ---- views -------------------------------------------------------------------------------------------------

def test_ownership_series_skips_months_without_usable_detail():
    rows = [(date(2025, 1, 31), None), (date(2025, 2, 28), {}), (date(2025, 3, 31), {"local": {"ID": 5}, "foreign": {}}),
            (date(2025, 4, 30), {"local": {}, "foreign": {}})]
    out = stockmod.ownership_series(rows)
    assert [m["d"] for m in out] == [date(2025, 3, 31)] and out[0]["local"] == {"ID": 5.0}
    assert stockmod.ownership_series(None) == []


def test_ownership_view_reports_the_latest_month_and_the_year_on_year_shift():
    v = stockmod.ownership_view(_rows(14, foreign_share=0.6))
    assert v["latest"] == "Feb 2026" and v["since"] == "Feb 2025" and v["n_months"] == 14
    legend = {r["label"]: r for r in v["legend"]}
    assert legend["Individual"]["local"] == "28.0%" and legend["Corporate"]["foreign"] == "54.0%"       # of ALL shares: 0.4*0.7 / 0.6*0.9
    shift = {g["label"]: g for g in v["shift"]}
    assert shift["Foreign"]["now"] == "60.0%" and shift["Foreign"]["delta"] == "+0.0 pts" and shift["Foreign"]["delta_c"] == ""
    assert [g["label"] for g in v["shift"]] == ["Foreign", "Local institution", "Local individual"]       # top of the stack first
    assert v["chart"].startswith("<svg") and v["chart_m"].startswith("<svg")


def test_ownership_view_shows_a_real_change_and_no_change_without_a_year_of_history():
    rows = _rows(14, foreign_share=0.5)
    rows[-1] = (rows[-1][0], _detail(_month(13, foreign_share=0.7)))                        # foreigners 50% -> 70% in the latest month
    shift = {g["label"]: g for g in stockmod.ownership_view(rows)["shift"]}
    assert shift["Foreign"]["then"] == "50.0%" and shift["Foreign"]["delta"] == "+20.0 pts" and shift["Foreign"]["delta_c"] == "pos"
    assert shift["Local individual"]["delta_c"] == "neg"
    short = stockmod.ownership_view(_rows(6))
    assert short["since"] is None and all(g["then"] is None and g["delta"] is None for g in short["shift"])
    assert stockmod.ownership_view(_rows(2)) is None


H = (date(2026, 10, 20), "Budi Santoso", True, False, "Direktur Utama", "buy", "Pembelian", 1_500_000, 4_250, date(2026, 10, 17),
     12_000_000, 0.000346, 0.00039, "https://example.test/f1.pdf")


def test_disclosure_view_formats_a_parsed_filing():
    v = stockmod.disclosure_view({"rows": [H], "total": 30, "filings": []})
    r = v["rows"][0]
    assert v["state"] == "parsed" and v["shown"] == 1 and v["total"] == 30
    assert r["role"] == "Director / commissioner" and "position" not in r and r["controller"] is False
    assert r["action"] == "Bought" and r["action_c"] == "pos" and r["raw_type"] == "Pembelian"
    assert r["qty"] == "1,500,000" and r["price"] == "4,250" and r["tdate"] == "2026-10-17"
    assert r["holding"] == "0.0346% → 0.0390%" and r["after_shares"] == "12,000,000"          # small holdings keep four decimals


def test_disclosure_view_handles_sells_other_and_missing_numbers():
    sell = H[:2] + (False, True) + H[4:5] + ("sell", "Penjualan") + (None, None, None, None, 0.052, 0.0498, None)
    other = H[:5] + (None, None) + (None,) * 7
    v = stockmod.disclosure_view({"rows": [sell, other], "total": 2, "filings": []})
    a, b = v["rows"]
    assert a["role"] == "Substantial holder (5%+)" and a["controller"] and a["action"] == "Sold" and a["action_c"] == "neg"
    assert a["qty"] == "—" and a["price"] == "—" and a["tdate"] == "—" and a["holding"] == "5.20% → 4.98%" and a["pdf"] == ""
    assert b["action"] == "Other" and b["action_c"] == "" and b["holding"] == "—"


def test_disclosure_view_falls_back_to_headlines_then_to_empty():
    f = [(date(2026, 9, 1), "PT Contoh", "Laporan Kepemilikan 5%", "https://example.test/x.pdf")]
    v = stockmod.disclosure_view({"rows": [], "total": 0, "filings": f})
    assert v["state"] == "filings" and v["rows"][0]["party"] == "PT Contoh"
    assert stockmod.disclosure_view({"rows": [], "total": 0, "filings": []})["state"] == "empty"
    assert stockmod.disclosure_view(None)["state"] == "empty"


def test_build_view_without_the_new_data_still_works_for_old_callers():
    v = stockmod.build_view(_raw(), TRACK)
    assert v["own_chart"] is None and v["disclosures"]["state"] == "empty"


# ---- the page ----------------------------------------------------------------------------------------------

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


def _page(client, monkeypatch, **extra):
    raw = {**_raw(), **extra}
    monkeypatch.setattr(stockmod, "load", lambda t: raw)
    r = client.get("/stock/BBCA")
    assert r.status_code == 200
    return r.text


def test_stock_page_shows_the_balance_position_chart_with_its_tables(client, monkeypatch):
    t = _page(client, monkeypatch, own_series=_rows(14))
    for needle in ("Ownership (KSEI, monthly)", "KSEI balance position by holder type", 'aria-label="Holder types, latest month"',
                   'aria-label="Change in the three big holder groups"', "now vs Feb 2025", "Total held:",
                   "number of individual shareholders is not in our data"):
        assert needle in t, needle
    assert t.count('class="chart-wide"') >= 1 and 'id="own-m-t"' in t and 'id="own-t"' in t        # wide and phone variants
    assert "not a prediction" in t


def test_stock_page_keeps_the_plain_table_when_there_is_no_chart_data(client, monkeypatch):
    t = _page(client, monkeypatch, own_series=[])
    assert "KSEI balance position by holder type" not in t and 'aria-label="KSEI ownership by month"' in t


def test_stock_page_lists_ownership_filings_safely(client, monkeypatch):
    t = _page(client, monkeypatch, disclosures={"rows": [H], "total": 30, "filings": []})
    for needle in ("Ownership filings: 5%+ holders and insiders", 'aria-label="Ownership filings"', "Budi Santoso", "Director / commissioner",
                   "Bought", "0.0346% → 0.0390%", "Showing 1 of 30 reported transactions", "not a full list"):
        assert needle in t, needle
    link = re.search(r'<a href="https://example\.test/f1\.pdf"[^>]*>', t).group(0)
    assert 'rel="noopener noreferrer"' in link and 'target="_blank"' in link             # an external link never gets the dashboard's origin
    assert "None" not in re.search(r'id="filings".*?</table>', t, re.DOTALL).group(0)


def test_stock_page_escapes_names_from_filings(client, monkeypatch):
    evil = (date(2026, 10, 20), "<script>alert(1)</script>") + H[2:]
    t = _page(client, monkeypatch, disclosures={"rows": [evil], "total": 1, "filings": []})
    assert "<script>alert(1)</script>" not in t and "&lt;script&gt;alert(1)&lt;/script&gt;" in t


def test_stock_page_filing_headlines_and_empty_state(client, monkeypatch):
    f = [(date(2026, 9, 1), "PT Contoh", "Laporan Kepemilikan 5%", None)]
    t = _page(client, monkeypatch, disclosures={"rows": [], "total": 0, "filings": f})
    assert "their tables have not been read into numbers yet" in t and "PT Contoh" in t and 'aria-label="Ownership filings, headlines only"' in t
    empty = _page(client, monkeypatch, disclosures={"rows": [], "total": 0, "filings": []})
    assert "No ownership filings from 5%+ holders or insiders have been collected" in empty


def test_job_title_is_translated_for_insiders_and_never_shown_for_other_reporters():
    row = lambda insider, pos: H[:2] + (insider,) + (False, pos) + H[5:]
    rows = [row(True, "Direksi"), row(True, "Dewan Komisaris"), row(False, "Alamat Identitas : Tidak ditampilkan"), row(None, None)]
    v = stockmod.disclosure_view({"rows": rows, "total": 4, "filings": []})
    assert [r["role"] for r in v["rows"]] == ["Director", "Commissioner", "Substantial holder (5%+)", "Reporter type not read"]
    assert "Alamat" not in str(v)                                                       # the parser's address note never reaches the page
