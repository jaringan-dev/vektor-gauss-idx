"""Offline tests for the paper-trading simulation core (no DB, no network)."""

from __future__ import annotations

from datetime import date, timedelta

from vg.paper import MAX_HOLD_DAYS, resolve_one

ENTRY, SL, TARGET = 1000.0, 900.0, 1250.0   # risk=100, target=+2.5R


def _days(n: int) -> list[date]:
    return [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]


def _bar(d: date, hi: float, lo: float, cl: float) -> tuple:
    return (d, hi, lo, cl)


def test_win_hits_target_first():
    ds = _days(3)
    window = [_bar(ds[0], 1050, 980, 1040), _bar(ds[1], 1260, 1200, 1255), _bar(ds[2], 1300, 1240, 1290)]
    res = resolve_one(ENTRY, SL, TARGET, window)
    assert res.status == "win"
    assert res.resolved_date == ds[1]
    assert res.exit_price == TARGET
    assert res.days_held == 2
    assert res.r_multiple == 2.5   # (1250-1000)/(1000-900)


def test_loss_hits_stop_first():
    ds = _days(2)
    window = [_bar(ds[0], 1020, 950, 1000), _bar(ds[1], 1010, 890, 895)]
    res = resolve_one(ENTRY, SL, TARGET, window)
    assert res.status == "loss"
    assert res.resolved_date == ds[1]
    assert res.exit_price == SL
    assert res.r_multiple == -1.0


def test_same_day_stop_and_target_stop_wins_conservatively():
    ds = _days(1)
    # a wild bar that both breaches SL and clears target intraday
    window = [_bar(ds[0], 1300, 850, 1100)]
    res = resolve_one(ENTRY, SL, TARGET, window)
    assert res.status == "loss"   # stop assumed to hit first — no look-ahead bias


def test_timeout_after_max_hold_days_marks_to_last_close():
    ds = _days(MAX_HOLD_DAYS)
    window = [_bar(d, 1100, 950, 1080) for d in ds]   # never breaches SL or TARGET
    res = resolve_one(ENTRY, SL, TARGET, window)
    assert res.status == "timeout"
    assert res.resolved_date == ds[-1]
    assert res.exit_price == 1080.0
    assert res.days_held == MAX_HOLD_DAYS
    assert res.r_multiple == (1080.0 - ENTRY) / (ENTRY - SL)


def test_still_open_when_window_shorter_than_max_hold():
    window = [_bar(_days(1)[0], 1100, 950, 1080)]   # only 1 bar so far, no breach
    res = resolve_one(ENTRY, SL, TARGET, window)
    assert res.status == "open"
    assert res.resolved_date is None
    assert res.r_multiple is None


def test_empty_window_is_open():
    res = resolve_one(ENTRY, SL, TARGET, [])
    assert res.status == "open"


def test_zero_risk_gives_no_r_multiple():
    ds = _days(1)
    window = [_bar(ds[0], 1300, 1290, 1295)]
    res = resolve_one(1000.0, 1000.0, 1250.0, window)   # SL == entry -> zero risk
    assert res.status == "win"
    assert res.r_multiple is None
