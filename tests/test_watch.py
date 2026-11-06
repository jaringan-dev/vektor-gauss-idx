"""Every collection is watched: a collector that silently stops running, a recorder that missed a session, a feed allowance that is nearly
spent or no longer the paid plan all become visible checks."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from vg import status as st
from vg import watch

SESSION = date(2026, 11, 6)          # a Friday
PREV = date(2026, 11, 5)
EVENING = datetime(2026, 11, 6, 21, 0)
MORNING = datetime(2026, 11, 7, 7, 30)   # Saturday, after the Friday session


def inputs(now=EVENING, session=SESSION, ok=None, last=None, **kw):
    all_ok = {p: session for p, _l, _c in watch.DAILY}
    all_ok.update({p: date(2026, 11, 5) for p, *_ in watch.PERIODIC})
    all_ok.update(ok or {})
    return watch.WatchInputs(now=now, session=session, sessions=[SESSION, PREV, date(2026, 11, 4), date(2026, 11, 3), date(2026, 11, 2)],
                             last_ok=all_ok, last_run=last or {}, **kw)


# ---- collectors ----------------------------------------------------------------------------------------------------------------

def test_everything_ran_is_one_ok_line():
    out = watch.assess(inputs())
    assert out == [("ok", "Collectors", f"all {len(watch.DAILY)} daily collections ran for the 6 Nov session")]


def test_a_collector_that_stopped_running_is_named_with_its_last_good_day():
    out = watch.assess(inputs(ok={"collect_trading_halts": PREV}))
    assert out[0][0] == "warn" and "trading halts (last good run 5 Nov)" in out[0][2]


def test_a_stale_critical_collector_is_a_problem_not_a_warning():
    for phase in ("collect_price", "collect_broker", "compute"):
        assert watch.assess(inputs(ok={phase: PREV}))[0][0] == "bad"


def test_a_collector_that_never_ran_says_so():
    inp = inputs()
    inp.last_ok.pop("collect_index")
    assert "index (has never run)" in watch.assess(inp)[0][2]


def test_a_failed_latest_run_is_reported_as_failed():
    inp = inputs(ok={"collect_disclosure": PREV}, last={"collect_disclosure": ("error", SESSION, "boom")})
    assert "disclosure filings (its last run failed)" in watch.assess(inp)[0][2]


def test_no_data_counts_as_a_good_run():
    # the query puts no_data into last_ok; a holiday with nothing to report must not warn
    assert watch.assess(inputs(ok={"collect_trading_halts": SESSION}))[0][0] == "ok"


def test_while_the_nightly_job_is_still_running_a_missing_run_is_due_not_stale():
    now = datetime(2026, 11, 6, 19, 20)                       # EOD is mid-run
    out = watch.assess(inputs(now=now, ok={"compute": PREV, "backfill_outcomes": PREV}))
    assert out[0][0] == "ok" and "more still due tonight" in out[0][2]
    assert "still due" not in watch.assess(inputs(now=now))[0][2]               # nothing waiting -> no caveat
    late = watch.assess(inputs(now=datetime(2026, 11, 6, 21, 0), ok={"compute": PREV}))
    assert late[0][0] == "bad"


def test_over_a_weekend_the_friday_runs_are_fresh():
    assert watch.assess(inputs(now=MORNING))[0][0] == "ok"


def test_a_failed_run_is_still_reported_while_the_night_is_in_progress():
    now = datetime(2026, 11, 6, 19, 20)
    inp = inputs(now=now, ok={"collect_index": PREV}, last={"collect_index": ("error", SESSION, "x")})
    assert watch.assess(inp)[0][0] == "warn"


def test_no_session_is_a_warning_not_a_crash():
    assert watch.assess(watch.WatchInputs(now=EVENING, session=None))[0][0] == "warn"


@pytest.mark.parametrize(("days", "level"), [(3, None), (11, "warn"), (31, "bad")])
def test_periodic_collectors_age(days, level):
    inp = inputs(ok={"collect_corpactions": EVENING.date() - timedelta(days=days)})
    rest = [c for c in watch.assess(inp) if c[1].startswith("Collector:")]
    assert (rest[0][0] if rest else None) == level


def test_a_periodic_collector_that_never_ran_warns():
    inp = inputs()
    inp.last_ok.pop("collect_corpactions")
    assert ("warn", "Collector: corporate actions", "has never run") in watch.assess(inp)


# ---- recorder ----------------------------------------------------------------------------------------------------------------

def rec(runs, first=date(2026, 11, 2), now=EVENING):
    return watch.assess_recorder(inputs(now=now, recorder=runs, recorder_first=first))[0]


def day(d, trades=200_000, status="ok", stop="market_closed"):
    return (d, status, trades, f"trades {trades:,}; stopped: {stop}")


ALL5 = [day(date(2026, 11, n)) for n in (2, 3, 4, 5, 6)]


def test_recorder_that_has_never_run_is_not_a_problem_yet():
    assert watch.assess_recorder(inputs(recorder=[], recorder_first=None))[0][0] == "ok"


def test_recorder_ok_when_every_session_has_trades():
    level, _n, text = rec(ALL5)
    assert level == "ok" and "recorded the last 5 sessions" in text and "6 Nov: 200,000 trades" in text


def test_a_missed_session_is_named():
    level, _n, text = rec([r for r in ALL5 if r[0] != date(2026, 11, 4)])
    assert level == "warn" and "no recording for 4 Nov" in text


def test_a_run_that_captured_nothing_and_a_run_that_stopped_early_are_flagged():
    level, _n, text = rec([*ALL5[:-2], day(date(2026, 11, 5), trades=0), day(date(2026, 11, 6), stop="gave_up")])
    assert level == "warn" and "no trades captured on 5 Nov" in text and "6 Nov: stopped early (gave_up)" in text


def test_every_run_failed_is_flagged():
    level, _n, text = rec([*ALL5[:-1], day(date(2026, 11, 6), trades=0, status="error")])
    assert level == "warn" and "6 Nov: every run failed" in text


def test_sessions_before_the_first_recording_are_not_judged():
    level, _n, text = rec([day(date(2026, 11, 6))], first=date(2026, 11, 6))
    assert level == "ok" and "recorded the last session" in text


def test_today_is_not_judged_until_the_market_has_closed():
    now = datetime(2026, 11, 6, 12, 0)
    assert rec(ALL5[:-1], now=now)[0] == "ok"                              # 6 Nov not finished yet
    assert rec(ALL5[:-1], now=datetime(2026, 11, 6, 17, 0))[0] == "warn"   # closed, nothing recorded


def test_two_runs_on_one_day_add_up():
    runs = [*ALL5[:-1], day(date(2026, 11, 6), trades=9_000, stop="max_seconds"), day(date(2026, 11, 6), trades=100_000)]
    assert "109,000 trades" in rec(runs)[2]


# ---- feed allowance ----------------------------------------------------------------------------------------------------------

def test_quota_normal_low_and_plan_ended():
    assert watch.assess_quota({"remaining": 5583, "limit": 41000}) == [("ok", "Feed quota", "5,583 calls left today of 41,000")]
    low = watch.assess_quota({"remaining": 300, "limit": 41000})[0]
    assert low[0] == "warn" and "only 300 calls left" in low[2]
    ended = watch.assess_quota({"remaining": 900, "limit": 1000})[0]
    assert ended[0] == "warn" and "only 1,000 calls" in ended[2] and "plan has ended" in ended[2]


def test_quota_unknown_or_unreported():
    assert watch.assess_quota(None) == []
    assert watch.assess_quota({"remaining": None, "limit": None})[0][0] == "warn"


# ---- wiring into status ------------------------------------------------------------------------------------------------------

def test_checks_combines_everything_and_survives_unreadable_facts():
    out = watch.checks(inputs(recorder=ALL5, recorder_first=date(2026, 11, 2)), {"remaining": 5000, "limit": 41000})
    assert [c[1] for c in out] == ["Collectors", "Trade recorder", "Feed quota"]
    assert [c[1] for c in watch.checks(None, {"remaining": 5000, "limit": 41000})] == ["Feed quota"]
    assert watch.checks(None, None) == []


def test_status_shows_the_watch_lines_and_escapes_them():
    f = {"now": EVENING, "strip": [], "watch": [["warn", "Collectors", "x <b>stale</b>"]]}
    shown = [c for c in st.build_checks(f) if c.name == "Collectors"]
    assert shown and shown[0].level == "warn" and "<b>" not in shown[0].text


def test_a_headline_goes_to_a_warning_when_a_collector_is_missing():
    f = {"now": EVENING, "strip": [], "watch": [["warn", "Collectors", "halts"]]}
    assert st.headline(st.build_checks(f))[0] == st.WARN


class FakeResp:
    def __init__(self, status=200, headers=None):
        self.status_code, self.headers = status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def feed(base, key, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("vg.config.CONFIG", SimpleNamespace(feed_base_url=base, feed_api_key=key, http_timeout=5))


def test_quota_probe_reads_the_headers(monkeypatch):
    import httpx

    feed("https://feed.test", "k", monkeypatch)
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return FakeResp(200, {"x-ratelimit-remaining": "5583", "x-ratelimit-limit": "41000"})

    monkeypatch.setattr(httpx, "get", fake_get)
    assert st._quota_fact() == {"remaining": 5583, "limit": 41000} and seen["url"] == "https://feed.test/api/price/BBCA"
    monkeypatch.setattr(httpx, "get", lambda url, **kw: FakeResp(429, {"x-ratelimit-limit": "41000"}))
    assert st._quota_fact() == {"remaining": 0, "limit": 41000}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: FakeResp(200, {}))
    assert st._quota_fact() == {"remaining": None, "limit": None}


def test_quota_probe_without_a_feed_makes_no_call(monkeypatch):
    import httpx

    feed("", "", monkeypatch)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("must not call"))
    assert st._quota_fact() is None
