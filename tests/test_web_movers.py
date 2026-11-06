"""Top movers: the five daily rankings (gainer / loser / value / volume / frequency) from the stored end-of-day summary."""

from __future__ import annotations

import re
from datetime import date

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from vg import engine
from vg.web import app as webapp
from vg.web import movers

from .test_web_ios_ui import client  # noqa: F401 — the shared page-test fixture

SD = date(2026, 11, 5)
OLDER = date(2026, 11, 4)


def row(ticker, close=100.0, prev=100.0, volume=1_000_000.0, value=10_000_000_000.0, freq=500.0, nv=None, nvol=None, nf=None):
    return {"ticker": ticker, "close": close, "prev": prev, "volume": volume, "value": value, "frequency": freq,
            "nonreg_value": nv, "nonreg_volume": nvol, "nonreg_frequency": nf}


MARKET = [
    row("UPA", close=130, prev=100, value=9e9, volume=5e6, freq=900),       # +30%
    row("UPB", close=110, prev=100, value=80e9, volume=2e6, freq=4000),      # +10%
    row("DNA", close=70, prev=100, value=2e9, volume=9e6, freq=300),         # -30%
    row("DNB", close=95, prev=100, value=30e9, volume=1e6, freq=2500),       # -5%
    row("FLAT", close=100, prev=100, value=500e9, volume=7e6, freq=9000),    # 0%
    row("NOPREV", close=50, prev=None, value=7e9, volume=3e6, freq=700),     # no previous close: no change
]


# ---- the pure ranking -------------------------------------------------------------------------------------------------------------

def test_the_liquid_line_is_the_one_the_evaluation_uses():
    assert movers.LIQUID_VALUE_IDR == engine.LIQUID_VALUE_IDR


def test_change_needs_a_positive_previous_close():
    assert movers.change(row("A", close=110, prev=100)) == pytest.approx(0.10)
    assert movers.change(row("A", close=110, prev=None)) is None
    assert movers.change(row("A", close=110, prev=0)) is None


def test_gainers_are_only_risers_biggest_first_and_losers_only_fallers_biggest_fall_first():
    up = movers.rank(MARKET, "gainer")
    assert [r["ticker"] for r in up] == ["UPA", "UPB"]                       # FLAT (0%) and NOPREV (no change) are not gainers
    assert up[0]["chg"]["t"] == "+30.00%" and up[0]["chg"]["c"] == "pos"
    down = movers.rank(MARKET, "loser")
    assert [r["ticker"] for r in down] == ["DNA", "DNB"]
    assert down[0]["chg"]["t"] == "-30.00%" and down[0]["chg"]["c"] == "neg"


def test_value_volume_and_frequency_rank_by_their_own_figure():
    assert [r["ticker"] for r in movers.rank(MARKET, "value")][:3] == ["FLAT", "UPB", "DNB"]
    assert [r["ticker"] for r in movers.rank(MARKET, "volume")][:3] == ["DNA", "FLAT", "UPA"]
    assert [r["ticker"] for r in movers.rank(MARKET, "freq")][:3] == ["FLAT", "UPB", "DNB"]
    top = movers.rank(MARKET, "volume")[0]
    assert top["lots"] == "90,000" and top["freq"] == "300" and top["value"] == "2.0B"      # shares are shown as lots (1 lot = 100 shares)


def test_a_stock_without_a_change_still_ranks_on_value():
    assert "NOPREV" in [r["ticker"] for r in movers.rank(MARKET, "value")]
    assert "NOPREV" not in [r["ticker"] for r in movers.rank(MARKET, "gainer") + movers.rank(MARKET, "loser")]


def test_ties_break_by_ticker_so_the_order_never_flips():
    rows = [row("ZZ", close=110), row("AA", close=110), row("MM", close=110)]
    assert [r["ticker"] for r in movers.rank(rows, "gainer")] == ["AA", "MM", "ZZ"]
    rows = [row("ZZ", close=90), row("AA", close=90)]
    assert [r["ticker"] for r in movers.rank(rows, "loser")] == ["AA", "ZZ"]


def test_the_leader_fills_the_bar_and_the_rest_are_in_proportion():
    up = movers.rank(MARKET, "gainer")
    assert up[0]["bar"] == 100 and up[1]["bar"] == round(100 * 0.10 / 0.30)


def test_liquid_only_drops_the_stocks_that_traded_little():
    assert [r["ticker"] for r in movers.rank(MARKET, "gainer", liquid=True)] == ["UPA", "UPB"]       # 9B and 80B are both above 5B
    assert [r["ticker"] for r in movers.rank(MARKET, "loser", liquid=True)] == ["DNB"]                # DNA traded only 2B
    assert movers.rank([row("TINY", close=130, value=4_999_999_999.0)], "gainer", liquid=True) == []  # the line itself is not liquid (strictly above)


def test_regular_board_only_leaves_the_negotiated_board_out():
    block = [row("BLOCK", value=900e9, volume=50e6, freq=40, nv=880e9, nvol=49e6, nf=2), row("REG", value=100e9, volume=2e6, freq=3000)]
    assert [r["ticker"] for r in movers.rank(block, "value")] == ["BLOCK", "REG"]                    # all boards: the block deal wins
    regular = movers.rank(block, "value", regular=True)
    assert [r["ticker"] for r in regular] == ["REG", "BLOCK"] and regular[1]["value"] == "20.0B"
    assert movers.rank(block, "freq", regular=True)[0]["ticker"] == "REG"


def test_a_missing_negotiated_figure_counts_as_zero_and_never_goes_below_zero():
    assert movers.rank([row("A", value=10e9)], "value", regular=True)[0]["value"] == "10.0B"
    assert movers.rank([row("A", value=10e9, nv=12e9)], "value", regular=True) == []               # negative would be nonsense: clamped to 0, not ranked


def test_the_table_is_capped_and_an_unknown_board_falls_back_to_gainers():
    many = [row(f"T{i:02d}", close=100 + i + 1) for i in range(40)]
    assert len(movers.rank(many, "gainer")) == movers.LIMIT
    assert movers.rank(many, "nonsense")[0]["ticker"] == movers.rank(many, "gainer")[0]["ticker"]
    assert movers.rank([], "gainer") == []


# ---- the view ---------------------------------------------------------------------------------------------------------------------

def raw(session=SD, days=(SD, OLDER), rows=MARKET, wanted=None):
    return {"session_date": session, "days": list(days), "rows": rows, "wanted": wanted}


def test_the_view_has_five_pills_one_of_them_on_and_leaders_for_every_board():
    v = movers.build_view(raw(), "loser")
    assert [p["label"] for p in v["pills"]] == ["Top gainer", "Top loser", "Top value", "Top volume", "Top frequency"]
    assert [p["on"] for p in v["pills"]] == [False, True, False, False, False]
    assert [s["key"] for s in v["summary"]] == ["gainer", "loser", "value", "volume", "freq"]
    assert [l["ticker"] for l in v["summary"][0]["leaders"]] == ["UPA", "UPB"] and len(v["summary"][2]["leaders"]) == 3
    assert v["board_label"] == "Top loser" and [r["ticker"] for r in v["rows"]] == ["DNA", "DNB"] and v["n_stocks"] == 6


def test_the_switches_and_the_date_survive_a_click_on_another_ranking():
    v = movers.build_view(raw(session=OLDER), "gainer", liquid=True, regular=True)
    href = next(p["href"] for p in v["pills"] if p["key"] == "value")
    assert href == "/movers?board=value&liquid=1&reg=1&date=2026-11-04"
    plain = movers.build_view(raw(), "gainer")
    assert next(p["href"] for p in plain["pills"] if p["key"] == "value") == "/movers?board=value"          # the newest session needs no date
    toggles = {t["label"]: t for t in v["toggles"]}
    assert toggles["Liquid only"]["on"] and "liquid=1" not in toggles["Liquid only"]["href"]               # clicking an "on" switch turns it off
    assert toggles["Regular board only"]["on"] and "reg=1" not in toggles["Regular board only"]["href"]


def test_asking_for_a_session_that_is_not_offered_says_so():
    v = movers.build_view(raw(wanted=date(2020, 1, 2)))
    assert v["is_latest"] and "2020-01-02" in v["notice"] and "newest session is shown" in v["notice"]
    assert movers.build_view(raw(wanted=SD))["notice"] is None
    assert movers.build_view(raw(session=OLDER, wanted=OLDER))["is_latest"] is False


def test_an_empty_database_gives_an_empty_view_not_a_crash():
    v = movers.build_view({"session_date": None, "days": [], "rows": [], "wanted": None})
    assert v["session_date"] is None and v["rows"] == [] and all(s["leaders"] == [] for s in v["summary"])


# ---- the database part only reads -------------------------------------------------------------------------------------------------

def test_loading_only_selects_and_falls_back_to_the_newest_session(monkeypatch):
    seen = []

    class Cur:
        description = ()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            seen.append((sql, params))
            self.sql = sql
            if "FROM price_bars WHERE" in sql:
                self.description = [(c,) for c in ("ticker", "close", "prev", "volume", "value", "frequency",
                                                    "nonreg_volume", "nonreg_value", "nonreg_frequency", "listed_shares")]

        def fetchall(self):
            if "trading_calendar" in self.sql:
                return [(SD,), (OLDER,)]
            return [("AAAA", 1100, 1000, 5_000_000, 50_000_000_000, 800, None, None, None, 2_000_000_000)]

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return Cur()

    monkeypatch.setattr(movers, "connect", lambda: Conn())
    out = movers.load(date(2020, 1, 2))                                  # not an offered session -> the newest
    assert out["session_date"] == SD and out["days"] == [SD, OLDER] and out["wanted"] == date(2020, 1, 2)
    assert out["rows"] == [{"ticker": "AAAA", "close": 1100.0, "prev": 1000.0, "volume": 5e6, "value": 5e10, "frequency": 800.0,
                            "nonreg_volume": None, "nonreg_value": None, "nonreg_frequency": None, "listed_shares": 2e9}]
    assert seen and all(s.lstrip().upper().startswith("SELECT") for s, _p in seen)
    assert seen[-1][1] == (SD,)
    assert movers.load(OLDER)["session_date"] == OLDER                    # an offered session is honoured


# ---- market cap and turnover ------------------------------------------------------------------------------------------------------

def test_market_cap_is_close_times_listed_shares_and_turnover_is_value_over_it():
    r = row("BIG", close=1000.0, value=10_000_000_000.0)
    r["listed_shares"] = 100_000_000_000.0                            # cap Rp100T
    assert movers.market_cap(r) == 100_000_000_000_000.0
    top = movers.rank([r], "value")[0]
    assert top["mcap"] == "100.0T" and top["turn"] == "0.01%"


def test_missing_or_non_positive_shares_give_n_a_not_a_crash():
    for shares in (None, 0.0, -5.0):
        r = row("ODD", value=10_000_000_000.0)
        r["listed_shares"] = shares
        assert movers.market_cap(r) is None
        top = movers.rank([r], "value")[0]
        assert top["mcap"] == "n/a" and top["turn"] == "n/a"
    assert movers.market_cap(row("NOKEY")) is None                  # the key absent altogether


def test_turnover_follows_the_regular_board_switch():
    r = row("BLK", value=10_000_000_000.0, nv=6_000_000_000.0)
    r["listed_shares"] = 1_000_000_000.0                              # cap 100 x 1e9 = Rp100B
    assert movers.rank([r], "value")[0]["turn"] == "10.00%"
    assert movers.rank([r], "value", regular=True)[0]["turn"] == "4.00%"


# ---- the page ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def page(client, monkeypatch):  # noqa: F811
    webapp._MOVERS_CACHE.clear()
    monkeypatch.setattr(movers, "load", lambda want=None: raw(wanted=want))
    return client


def test_the_page_shows_five_rankings_a_leader_strip_and_a_table(page):
    r = page.get("/movers")
    assert r.status_code == 200
    t = r.text
    pills = re.search(r'<p class="chips pills" aria-label="Ranking">(.*?)</p>', t, re.DOTALL).group(1)
    assert [m for m in re.findall(r'>(Top [a-z]+)</a>', pills)] == ["Top gainer", "Top loser", "Top value", "Top volume", "Top frequency"]
    assert t.count('class="kpi') >= 5 and "Leaders of every ranking" in t
    body = re.search(r"<tbody.*?</tbody>", t, re.DOTALL).group(0)
    assert body.index("UPA") < body.index("UPB") and "+30.00%" in body                                  # the default board is the gainers
    assert len(re.findall(r'<a [^>]*aria-current="page"[^>]*href="/movers"|<a [^>]*href="/movers"[^>]*aria-current="page"', t)) == 1


def test_each_board_and_both_switches_work_from_the_address(page):
    t = page.get("/movers?board=loser").text
    body = re.search(r"<tbody.*?</tbody>", t, re.DOTALL).group(0)
    assert body.index("DNA") < body.index("DNB") and "-30.00%" in body
    t = page.get("/movers?board=value&liquid=1&reg=1").text
    assert "liquid stocks only" in t and "regular board only" in t
    assert 'class="fchip on" href="/movers?board=value&amp;reg=1"' in t                                 # the Liquid switch is on; clicking it drops it
    assert "Nothing qualifies" not in page.get("/movers?board=volume").text


def test_a_mistyped_date_or_board_does_not_break_the_page(page):
    assert page.get("/movers?date=not-a-date&board=zzz").status_code == 200
    t = page.get("/movers?date=2020-01-02").text
    assert "newest session is shown" in t


def test_no_data_and_a_database_failure_are_shown_not_hidden(client, monkeypatch):  # noqa: F811
    webapp._MOVERS_CACHE.clear()
    monkeypatch.setattr(movers, "load", lambda want=None: {"session_date": None, "days": [], "rows": [], "wanted": None})
    assert "No data yet" in client.get("/movers").text
    webapp._MOVERS_CACHE.clear()

    def boom(want=None):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(movers, "load", boom)
    r = client.get("/movers")
    assert r.status_code == 200 and "Cannot read the data" in r.text and "connection refused" in r.text
