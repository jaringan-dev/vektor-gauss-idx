"""Retrospective badge test (vg.pattern_research): the event walk, pure, no database."""

from datetime import date, timedelta

from vg import pattern_research as R
from vg import research
from vg.web import patterns as P

D0 = date(2025, 1, 1)
B = 1e9


def bar(i, close=1000.0, vol=1e6, value=2 * B, prev=None, nonreg=0.0):
    return (D0 + timedelta(days=i), prev if prev is not None else close, close * 1.01, close * 0.99, close, vol, value, nonreg)


def tags_on(events, i):
    return {t for d, _tk, t in events if d == D0 + timedelta(days=i)}


def test_a_surge_day_up_and_down_are_split_and_quiet_days_give_nothing():
    bars = [bar(i) for i in range(30)]
    bars[25] = bar(25, close=1050.0, prev=1000.0, value=6 * B)          # 3x, closed up
    bars[28] = bar(28, close=950.0, prev=1000.0, value=6 * B)           # 3x, closed down
    ev = list(R.events_for("XXXX", bars))
    assert tags_on(ev, 25) == {"P-SURGE", "P-SURGE-UP"}
    assert tags_on(ev, 28) == {"P-SURGE", "P-SURGE-DOWN"}
    assert tags_on(ev, 26) == set()


def test_events_use_only_bars_up_to_that_day():
    """No look-ahead: a busy day AFTER day i cannot change what day i shows."""
    bars = [bar(i) for i in range(40)]
    before = tags_on(list(R.events_for("XXXX", bars)), 30)
    bars[35] = bar(35, value=50 * B)
    assert tags_on(list(R.events_for("XXXX", bars)), 30) == before


def test_compression_and_surge_together():
    n = R.NEED
    bars = [bar(i, vol=1e6) for i in range(n - P.WINDOW)] + [bar(i, vol=3e6, value=6 * B) for i in range(n - P.WINDOW, n)]
    ev = list(R.events_for("XXXX", bars))
    assert {"P-COMPRESSION", "P-SURGE", "P-COMP+SURGE"} <= tags_on(ev, n - 1)


def test_the_event_walk_agrees_with_the_badge_functions():
    """The research must measure exactly what the screen draws."""
    bars = [bar(i, value=(2 + (i % 7)) * B) for i in range(60)]
    bars[50] = bar(50, value=40 * B, close=1020.0, prev=1000.0)
    for i in range(P.BASE, len(bars)):
        surge = P.is_surge(P.volume_surge([(b[0], b[6], b[7]) for b in bars[i - P.BASE:i + 1]]))
        assert ("P-SURGE" in tags_on(list(R.events_for("XXXX", bars)), i)) == surge


def test_the_source_has_the_columns_research_reads():
    sql = research._sql(R.SOURCE)
    assert "pattern_events" in sql and "FROM signal_log" not in sql.split("FROM (")[-1]
    for col in ("session_date", "ticker", "tag", "ret2_h5"):
        assert col in R.SOURCE
