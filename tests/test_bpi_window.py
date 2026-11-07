"""Five-session BPI on the home screen (vg.web.bpi_window): pure, no database."""

from datetime import date, timedelta

from vg.web import bpi_window, home

D0 = date(2026, 11, 2)
DAYS = [D0 + timedelta(days=i) for i in range(5)]


def day(buys, sells, value):
    return (list(buys.items()), list(sells.items()), value)


SPREAD_SELL = {f"S{i}": 10.0 for i in range(10)}      # 10 sellers x 10 = 100 = the day's value


def test_the_same_broker_buying_every_day_is_seen_over_the_window():
    """YP buys 40 of 100 every day; sellers are spread. Every day is up, YP tops all five days."""
    days = {d: day({"YP": 40.0, "AA": 30.0, "BB": 20.0, "CC": 10.0}, SPREAD_SELL, 100.0) for d in DAYS}
    w = bpi_window.window_score(days)
    assert w.days_ok == 5 and w.days_up == 5
    assert w.top_buyer == "YP" and w.top_buyer_days == 5
    assert abs(w.bpi3 - (0.9 - 0.3)) < 1e-9          # top-3 buys 90/100, top-3 sells 30/100


def test_a_different_top_buyer_every_day_is_not_the_same_hands():
    """Each day a different house buys 50 of 100 (ten retail houses buy 5 each); summed over five days no house dominates."""
    retail = {f"R{i}": 5.0 for i in range(10)}
    days = {d: day({f"H{i}": 50.0, **retail}, SPREAD_SELL, 100.0) for i, d in enumerate(DAYS)}
    w = bpi_window.window_score(days)
    assert w.days_up == 5                               # every single day looks concentrated (+30 pts)...
    assert w.top_buyer_days == 1                        # ...but the window's top buyer was on top on one day only
    daily = bpi_window.window_score({DAYS[0]: days[DAYS[0]]}, min_days=1).bpi3
    assert abs(daily - 0.3) < 1e-9 and abs(w.bpi3 - 0.0) < 1e-9   # (50+5+5 - 30)/100 = +30 pts each day, 0 over the window


def test_short_coverage_gives_no_number_but_says_how_many_sessions():
    days = {d: day({"YP": 60.0, "AA": 40.0}, SPREAD_SELL, 100.0) for d in DAYS[:3]}
    w = bpi_window.window_score(days)
    assert w.bpi3 is None and w.days_ok == 3
    cell = home.win_cell(w)
    assert cell["raw"] is None and cell["short"] and "3 of 5" in cell["sub"]


def test_a_day_without_traded_value_or_a_ladder_does_not_count():
    good = day({"YP": 60.0, "AA": 40.0}, SPREAD_SELL, 100.0)
    days = {DAYS[0]: good, DAYS[1]: good, DAYS[2]: good, DAYS[3]: good,
            DAYS[4]: day({"YP": 60.0}, {}, 100.0)}          # no sell ladder that day
    w = bpi_window.window_score(days)
    assert w.days_ok == 4 and w.bpi3 is not None


def test_no_broker_data_at_all():
    cell = home.win_cell(None)
    assert cell["raw"] is None and "no broker data" in cell["sub"]


def test_bpi5d_is_a_sort_key_and_unknowns_sort_last():
    rows = [{"ticker": t, "bpi5d_raw": v, "search": t} for t, v in (("A", 0.1), ("B", None), ("C", 0.3))]
    p = home.clean({"sort": "bpi5d", "dir": "desc"})
    assert [r["ticker"] for r in home.apply(rows, p)] == ["C", "A", "B"]
