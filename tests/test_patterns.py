"""Home-screen pattern badges (vg.web.patterns): pure, no database."""

from datetime import date, timedelta

import pytest

from vg.web import patterns as P

D0 = date(2026, 9, 1)


def bars(closes, vols, highs=None, lows=None, prevs=None):
    """(session, prev, high, low, close, volume) oldest first; prev = previous close unless given."""
    out = []
    for i, (c, v) in enumerate(zip(closes, vols, strict=True)):
        prev = prevs[i] if prevs else (closes[i - 1] if i else c)
        hi = highs[i] if highs else c * 1.01
        lo = lows[i] if lows else c * 0.99
        out.append((D0 + timedelta(days=i), prev, hi, lo, c, v))
    return out


N = P.WINDOW + P.BASE + 1


def test_tight_range_on_heavy_volume_is_compression():
    closes = [1000.0] * N
    vols = [100.0] * (N - P.WINDOW) + [200.0] * P.WINDOW          # 2x the usual volume in the window
    c = P.compression(bars(closes, vols))
    assert abs(c.vol_x - 2.0) < 1e-9 and abs(c.move_pct) < 1e-9 and c.range_pct < 0.03
    assert P.is_compression(c)


def test_a_wide_range_or_normal_volume_is_not():
    closes = [1000.0] * (N - P.WINDOW) + [1000.0, 1100.0, 900.0, 1050.0, 1000.0]
    vols = [100.0] * (N - P.WINDOW) + [200.0] * P.WINDOW
    assert not P.is_compression(P.compression(bars(closes, vols)))             # range ~22%: price did not hold
    assert not P.is_compression(P.compression(bars([1000.0] * N, [100.0] * N)))  # volume normal


def test_a_corporate_action_reset_blocks_the_numbers():
    """A 1:2 split mid-window: close halves, IDX resets prev to the adjusted price. Must not read as anything."""
    closes = [1000.0] * (N - 3) + [500.0] * 3
    prevs = [closes[i - 1] if i else closes[0] for i in range(N)]
    prevs[N - 3] = 500.0                                           # reference price reset on the ex-date
    c = P.compression(bars(closes, [100.0] * N, prevs=prevs))
    assert c.range_pct is None and "corporate action" in c.reason


def test_short_history_says_so():
    c = P.compression(bars([1000.0] * 10, [100.0] * 10))
    assert c.range_pct is None and "10 sessions" in c.reason


def test_a_session_without_trading_in_the_window_gives_no_numbers():
    vols = [100.0] * (N - 1) + [0.0]
    assert P.compression(bars([1000.0] * N, vols)).range_pct is None


DAYS = [date(2026, 11, 2) + timedelta(days=i) for i in range(5)]


def test_the_broker_buying_most_sessions_wins_and_missing_days_do_not_count():
    days = {d: {"YP": 10.0, "CC": -5.0} for d in DAYS[:4]}
    days[DAYS[4]] = {"CC": 50.0}                                      # YP not in the lists that day: not a net-buy day
    n = P.persistent_net_buyer(days, values={d: 50.0 for d in DAYS})
    assert (n.broker, n.days, n.days_ok, n.net_val) == ("YP", 4, 5, 40.0)
    assert abs(n.share - 40.0 / 250.0) < 1e-9 and P.is_persistent(n)
    b = P.badges({"comp": P.Compression(None, None, None, "x"), "net": n})
    assert [x["text"] for x in b] == ["YP net buy 4 days"]


def test_a_steady_but_tiny_net_buyer_is_not_a_badge():
    """6 Nov 2026: SGER showed 'IF net buy 5 days' for Rp0.0B. Every day counts, but the amount must matter too."""
    days = {d: {"IF": 1.0} for d in DAYS}
    n = P.persistent_net_buyer(days, values={d: 1000.0 for d in DAYS})       # 5 of 5000 = 0.1%
    assert n.days == 5 and not P.is_persistent(n)
    assert not P.is_persistent(P.persistent_net_buyer(days))                 # no traded value known: no share, no badge


def test_net_buy_needs_enough_sessions_with_data():
    days = {d: {"YP": 10.0} for d in DAYS[:3]}
    n = P.persistent_net_buyer(days)
    assert n.days == 3 and n.days_ok == 3 and not P.is_persistent(n)


def test_net_buy_days_with_a_net_loss_overall_do_not_qualify():
    days = {d: {"YP": 1.0} for d in DAYS[:4]}
    days[DAYS[4]] = {"YP": -100.0}                                    # bought a little 4 days, dumped a lot on the 5th
    assert not P.is_persistent(P.persistent_net_buyer(days))


B = 1e9


def vbars(values, nonreg=None):
    nonreg = nonreg or [0.0] * len(values)
    return [(D0 + timedelta(days=i), v, n) for i, (v, n) in enumerate(zip(values, nonreg, strict=True))]


def test_regular_value_handles_both_ways_idx_reports_the_negotiated_market():
    assert P.regular_value(235.91 * B, 234.37 * B) == pytest.approx(1.54 * B)   # NATO 6 Nov: value includes the negotiated trade
    assert P.regular_value(87.9 * B, 26097.6 * B) == 87.9 * B                   # BYAN 7 Oct: value is clearly regular only
    assert P.regular_value(5 * B, 0) == 5 * B and P.regular_value(None, None) == 0.0


def test_a_busy_day_is_a_surge_and_says_how_many_times_usual():
    s = P.volume_surge(vbars([2 * B] * P.BASE + [7 * B]))
    assert s.x == pytest.approx(3.5) and P.is_surge(s)
    b = P.badges({"comp": P.Compression(None, None, None, "x"), "net": P.NetBuyer(None, 0, 0, 0.0, 5), "surge": s})
    assert [x["text"] for x in b] == ["Volume 3.5x usual"]


def test_a_negotiated_block_trade_is_not_a_surge():
    """NATO 6 Nov 2026: 1,060x on total value, all of it one negotiated trade."""
    vals = [0.2 * B] * P.BASE + [235.9 * B]
    nonreg = [0.0] * P.BASE + [234.4 * B]
    s = P.volume_surge(vbars(vals, nonreg))
    assert s.value == pytest.approx(1.5 * B) and s.x == pytest.approx(7.5)    # the regular part is busier, but not 1,000x


def test_one_earlier_busy_day_does_not_hide_todays_surge():
    base = [2 * B] * (P.BASE - 1) + [100 * B]                                  # the median ignores the outlier
    assert P.volume_surge(vbars(base + [6 * B])).x == pytest.approx(3.0)


def test_small_stocks_and_short_history_get_no_surge():
    assert not P.is_surge(P.volume_surge(vbars([0.02 * B] * P.BASE + [0.5 * B])))    # 25x but only Rp0.5B
    assert P.volume_surge(vbars([2 * B] * 5)).x is None


def test_no_data_no_badges():
    assert P.badges(None) == []
