"""A feed that blinks (5xx, timeouts) is waited out and asked again; a feed that refuses us (4xx) still aborts at once.

5 Nov 2026: ten 502s in a row from the feed ended the night's K7 backfill with "ABORTED ... (quota/ban?)", although nothing was
wrong with the quota and the same names answered 200 an hour later. No network, no database: `_feed_loop` is driven by a script.
"""

from datetime import date

import httpx
import pytest

pytest.importorskip("psycopg")

from vg import collect
from vg.collect import CollectOutcome

D = date(2026, 11, 5)
BAD_GATEWAY = "HTTPStatusError: Server error '502 Bad Gateway' for url 'https://feed.example/api/broker-summary/BBCA'"
TOO_MANY = "HTTPStatusError: Client error '429 Too Many Requests' for url 'https://feed.example/api/broker-summary/BBCA'"


@pytest.fixture()
def sleeps(monkeypatch):
    log: list[float] = []
    monkeypatch.setattr(collect.time, "sleep", lambda s: log.append(s))
    monkeypatch.delenv("VG_FEED_COOLDOWNS", raising=False)
    return log


def scripted(down_calls, detail=BAD_GATEWAY):
    """A fetch that fails (with `detail`) for the first `down_calls` calls overall and answers ok afterwards. `calls` records the items."""
    calls: list[str] = []

    def fetch(item):
        calls.append(item)
        if len(calls) <= down_calls:
            return CollectOutcome(D, "error", 0, f"{item}: {detail}")
        return CollectOutcome(D, "ok", 20, item)

    fetch.calls = calls
    return fetch


NAMES = [f"T{i:02d}" for i in range(25)]


# ---- what counts as the feed blinking ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("detail, expected", [
    (BAD_GATEWAY, True),
    ("TNCA: HTTPStatusError: Server error '503 Service Unavailable' for url 'x'", True),
    (f"BBCA: {type(httpx.ConnectError('dns')).__name__}: dns", True),
    (f"BBCA: {type(httpx.ReadTimeout('slow')).__name__}: slow", True),
    (f"BBCA: {type(httpx.RemoteProtocolError('cut')).__name__}: cut", True),
    (TOO_MANY, False),                                           # quota: asking again would only make it worse
    ("BBCA: HTTPStatusError: Client error '403 Forbidden' for url 'x'", False),
    ("BBCA: parse: KeyError: 'broker_levels'", False),
    ("BBCA: OperationalError: connection to the database failed", False),
    ("", False),
    (None, False),
])
def test_only_server_and_network_errors_read_as_transient(detail, expected):
    assert collect._is_transient(detail) is expected


def test_the_waits_come_from_the_environment_with_a_safe_default(monkeypatch):
    monkeypatch.delenv("VG_FEED_COOLDOWNS", raising=False)
    assert collect._feed_cooldowns() == (120.0, 300.0, 600.0)
    monkeypatch.setenv("VG_FEED_COOLDOWNS", "30, 60")
    assert collect._feed_cooldowns() == (30.0, 60.0)
    monkeypatch.setenv("VG_FEED_COOLDOWNS", "")                 # empty = no waiting, the old behaviour
    assert collect._feed_cooldowns() == ()
    monkeypatch.setenv("VG_FEED_COOLDOWNS", "0")
    assert collect._feed_cooldowns() == ()
    monkeypatch.setenv("VG_FEED_COOLDOWNS", "soon")             # garbage: do not crash a night's run over a typo
    assert collect._feed_cooldowns() == (120.0, 300.0, 600.0)


# ---- the loop ---------------------------------------------------------------------------------------------------------------------

def test_a_feed_outage_is_waited_out_and_the_failed_names_are_asked_again(sleeps):
    fetch = scripted(down_calls=10)                              # exactly the 5 Nov shape: ten 502s, then fine
    run = collect._feed_loop(NAMES, fetch, pause=0.25)
    assert not run.aborted and run.errors == 0 and run.failures == []
    assert run.ok == 25                                          # every name got stored, none was skipped
    assert (run.cooldowns, run.cooled_s, run.requeued) == (1, 120.0, 10)
    assert 120.0 in sleeps
    assert fetch.calls[:10] == NAMES[:10] and fetch.calls[10:20] == NAMES[:10]     # the same ten, in the same order, once more
    assert fetch.calls[20:] == NAMES[10:]


def test_after_a_wait_one_failed_probe_starts_the_next_wait_without_ten_more_doomed_calls(sleeps):
    fetch = scripted(down_calls=11)                              # ten fail, the first retry still fails, then the feed is back
    run = collect._feed_loop(NAMES, fetch, pause=0.0)
    assert not run.aborted and run.ok == 25
    assert [s for s in sleeps if s > 1] == [120.0, 300.0]       # a longer wait the second time
    assert len(fetch.calls) == 25 + 11                           # 11 failing calls in all, not 20


def test_a_success_restores_the_full_patience(sleeps):
    calls = {"n": 0}

    def fetch(item):                                             # two separate outages, each of ten failures
        calls["n"] += 1
        down = calls["n"] <= 10 or 30 <= calls["n"] <= 39
        return CollectOutcome(D, "error", 0, f"{item}: {BAD_GATEWAY}") if down else CollectOutcome(D, "ok", 1, item)

    names = [f"T{i:02d}" for i in range(40)]                     # enough names for the second outage to be reached
    run = collect._feed_loop(names, fetch, pause=0.0)
    assert not run.aborted and run.ok == 40 and run.errors == 0
    assert [s for s in sleeps if s > 1] == [120.0, 120.0]       # the second outage started again from the shortest wait


def test_an_outage_that_outlasts_every_wait_aborts_and_says_it_was_the_feed(sleeps):
    run = collect._feed_loop(NAMES, scripted(down_calls=10_000), pause=0.0)
    assert run.aborted and run.transient
    assert run.cooldowns == 3 and run.cooled_s == 120 + 300 + 600
    assert len(run.failures) == 1                                # only the last probe; the others were asked again, not abandoned
    note = collect._with_feed_notes(run, "25 names")
    assert note.startswith("ABORTED") and "not a quota or a ban" in note and "quota/ban?" not in note
    assert "waited 17 min" in note and "25 names" in note


def test_a_refusal_is_never_waited_out(sleeps):
    run = collect._feed_loop(NAMES, scripted(down_calls=10_000, detail=TOO_MANY), pause=0.0)
    assert run.aborted and not run.transient and run.cooldowns == 0
    assert len(run.failures) == 10 and not [s for s in sleeps if s > 1]             # stopped after ten, no waiting
    assert "quota/ban?" in collect._with_feed_notes(run, "x")


def test_one_non_transient_error_in_the_streak_cancels_the_waiting(sleeps):
    calls = {"n": 0}

    def fetch(item):
        calls["n"] += 1
        detail = TOO_MANY if calls["n"] == 4 else BAD_GATEWAY
        return CollectOutcome(D, "error", 0, f"{item}: {detail}")

    run = collect._feed_loop(NAMES, fetch, pause=0.0)
    assert run.aborted and not run.transient and run.cooldowns == 0 and calls["n"] == 10


def test_waiting_can_be_turned_off(sleeps, monkeypatch):
    monkeypatch.setenv("VG_FEED_COOLDOWNS", "")
    fetch = scripted(down_calls=10)
    run = collect._feed_loop(NAMES, fetch, pause=0.0)
    assert run.aborted and run.cooldowns == 0 and len(fetch.calls) == 10


def test_errors_between_successes_still_never_abort_or_wait(sleeps):
    pattern = {f"T{i:02d}": ("error" if i % 2 else "ok") for i in range(25)}

    def fetch(item):
        st = pattern[item]
        return CollectOutcome(D, st, 1 if st == "ok" else 0, f"{item}: {BAD_GATEWAY}" if st == "error" else item)

    run = collect._feed_loop(NAMES, fetch, pause=0.0)
    assert not run.aborted and run.cooldowns == 0 and run.errors == 12 and run.ok == 13


# ---- wired into the real callers ---------------------------------------------------------------------------------------------------

def test_the_broker_pass_reports_the_outage_it_sat_out(sleeps, monkeypatch):
    monkeypatch.setattr(collect, "plan_broker_full", lambda **k: {
        "latest": D, "newest": D, "universe": NAMES, "have": set(), "todo": NAMES, "n_liquid": len(NAMES)})
    fetch = scripted(down_calls=10)
    monkeypatch.setattr(collect, "_feed_day_stale", lambda *a: False)
    monkeypatch.setattr(collect, "collect_broker_summary_ticker", lambda day, t, **kw: fetch(t))
    outs = collect.collect_broker_summary_full()
    assert len(outs) == 1 and outs[0].status == "ok" and outs[0].rows == 25 * 20      # nothing lost, no failure lines
    assert "fetched 25" in outs[0].detail and "errors 0" in outs[0].detail
    assert "waited 2 min out the feed's server errors (1 cool-down(s), 10 names asked again)" in outs[0].detail


def test_the_broker_pass_that_gives_up_reports_an_error_with_the_real_reason(sleeps, monkeypatch):
    monkeypatch.setattr(collect, "plan_broker_full", lambda **k: {
        "latest": D, "newest": D, "universe": NAMES, "have": set(), "todo": NAMES, "n_liquid": len(NAMES)})
    fetch = scripted(down_calls=10_000)
    monkeypatch.setattr(collect, "_feed_day_stale", lambda *a: False)
    monkeypatch.setattr(collect, "collect_broker_summary_ticker", lambda day, t, **kw: fetch(t))
    outs = collect.collect_broker_summary_full()
    assert outs[-1].status == "error" and "ABORTED" in outs[-1].detail and "not a quota or a ban" in outs[-1].detail
