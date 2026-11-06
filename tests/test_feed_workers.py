"""VG_FEED_WORKERS: several feed calls at once (K7 speed-up), judged by the same rules as the one-at-a-time loop."""
from __future__ import annotations

import threading
import time
from datetime import date
from pathlib import Path

import pytest

from vg import collect
from vg.collect import CollectOutcome

D = date(2026, 11, 6)
REAL_SLEEP = time.sleep                              # the fixture below silences collect.time.sleep, which is this same function
BAD_GATEWAY = "HTTPStatusError: Server error '502 Bad Gateway' for url 'https://feed.example/api/broker-summary/BBCA'"


def ok(item, rows=1):
    return CollectOutcome(D, "ok", rows, item)


def bad(item, detail="HTTPStatusError: 401"):
    return CollectOutcome(D, "error", 0, f"{item}: {detail}")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    monkeypatch.setattr(collect, "_feed_cooldowns", lambda: (1.0, 2.0))
    monkeypatch.delenv("VG_FEED_WORKERS", raising=False)


@pytest.mark.parametrize(("raw", "want"), [(None, 1), ("", 1), ("x", 1), ("0", 1), ("-3", 1), ("1", 1), ("4", 4), ("8", 8), ("99", 8)])
def test_the_worker_count_defaults_to_one_and_is_capped(monkeypatch, raw, want):
    if raw is not None:
        monkeypatch.setenv("VG_FEED_WORKERS", raw)
    assert collect._feed_workers() == want


def test_one_worker_is_the_original_one_at_a_time_loop(monkeypatch):
    order, live, peak = [], [0], [0]
    lock = threading.Lock()

    def fetch(item):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        order.append(item)
        with lock:
            live[0] -= 1
        return ok(item)

    run = collect._feed_loop(list("abcdef"), fetch, 0)
    assert order == list("abcdef") and peak[0] == 1 and (run.ok, run.errors, run.aborted) == (6, 0, False)


def test_several_workers_really_run_at_once_and_count_everything(monkeypatch):
    monkeypatch.setenv("VG_FEED_WORKERS", "4")
    live, peak, lock = [0], [0], threading.Lock()

    def fetch(item):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        REAL_SLEEP(0.03)
        with lock:
            live[0] -= 1
        return ok(item, rows=2)

    run = collect._feed_loop(list("abcdefghij"), fetch, 0)
    assert 2 <= peak[0] <= 4
    assert (run.ok, run.rows, run.errors, run.aborted) == (10, 20, 0, False)


def test_every_item_is_fetched_exactly_once_whatever_the_batch_size(monkeypatch):
    for w in ("1", "3", "8"):
        monkeypatch.setenv("VG_FEED_WORKERS", w)
        seen, lock = [], threading.Lock()

        def fetch(item, seen=seen, lock=lock):
            with lock:
                seen.append(item)
            return ok(item)

        items = [f"t{i}" for i in range(23)]                 # not a multiple of the batch size
        collect._feed_loop(items, fetch, 0)
        assert sorted(seen) == sorted(items), w


def test_a_hard_error_streak_still_aborts_and_the_rest_of_the_batch_is_counted(monkeypatch):
    monkeypatch.setenv("VG_FEED_WORKERS", "4")
    monkeypatch.setattr(collect, "_FEED_ABORT_AFTER_ERRORS", 3)
    run = collect._feed_loop([f"t{i}" for i in range(40)], lambda t: bad(t), 0)
    assert run.aborted and not run.transient
    assert run.errors == 4 and len(run.failures) == 4 and run.ok == 0               # the batch that tripped it: 4 calls, all counted; no second batch


def test_interleaved_errors_never_abort_with_workers(monkeypatch):
    monkeypatch.setenv("VG_FEED_WORKERS", "4")
    monkeypatch.setattr(collect, "_FEED_ABORT_AFTER_ERRORS", 3)
    run = collect._feed_loop([f"t{i}" for i in range(24)], lambda t: bad(t) if int(t[1:]) % 2 else ok(t), 0)
    assert not run.aborted and (run.ok, run.errors) == (12, 12)


def test_a_transient_outage_is_waited_out_and_the_names_asked_again(monkeypatch):
    monkeypatch.setenv("VG_FEED_WORKERS", "3")
    monkeypatch.setattr(collect, "_FEED_ABORT_AFTER_ERRORS", 3)
    flaky = {"on": True}
    waits = []
    monkeypatch.setattr(collect.time, "sleep", lambda s: (waits.append(s), flaky.update(on=False) if s == 1.0 else None))

    def fetch(item):
        return bad(item, BAD_GATEWAY) if flaky["on"] else ok(item)

    items = [f"t{i}" for i in range(9)]
    run = collect._feed_loop(items, fetch, 0)
    assert run.cooldowns == 1 and 1.0 in waits and not run.aborted
    assert run.ok == 9 and run.errors == 0 and run.requeued == 3                   # the failed batch went back to the front and then succeeded


def test_the_k7_script_spends_the_weekend_budget_on_saturday_and_sunday_only():
    sh = (Path(__file__).resolve().parents[1] / "scripts" / "deep_backfill.sh").read_text(encoding="utf-8")
    assert 'DOW="$(date +%u)"' in sh and '[ "$DOW" -ge 6 ]' in sh
    assert 'BUDGET="${VG_DEEP_BUDGET_WEEKEND:-${VG_DEEP_BUDGET:-15000}}"' in sh      # weekend: its own budget, falling back to the weekday one
    assert sh.count("--budget") == 1 and '--budget "$BUDGET"' in sh                    # the command uses the chosen value, not VG_DEEP_BUDGET directly
    # run the selection logic for real: Mon-Fri -> weekday budget, Sat/Sun -> weekend budget
    import subprocess
    snippet = 'if [ "$DOW" -ge 6 ]; then BUDGET="${VG_DEEP_BUDGET_WEEKEND:-${VG_DEEP_BUDGET:-15000}}"; else BUDGET="${VG_DEEP_BUDGET:-15000}"; fi; echo $BUDGET'
    for dow, want in (("1", "37500"), ("5", "37500"), ("6", "40500"), ("7", "40500")):
        out = subprocess.run(["bash", "-c", snippet], env={"DOW": dow, "VG_DEEP_BUDGET": "37500", "VG_DEEP_BUDGET_WEEKEND": "40500", "PATH": "/usr/bin:/bin"},
                             capture_output=True, text=True, check=True).stdout.strip()
        assert out == want, dow
    out = subprocess.run(["bash", "-c", snippet], env={"DOW": "6", "VG_DEEP_BUDGET": "37500", "PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=True).stdout.strip()
    assert out == "37500"                                                              # no weekend value set -> the weekday one


def test_the_k7_script_passes_the_worker_count():
    sh = (Path(__file__).resolve().parents[1] / "scripts" / "deep_backfill.sh").read_text(encoding="utf-8")
    assert 'export VG_FEED_WORKERS="${VG_DEEP_WORKERS:-${VG_FEED_WORKERS:-1}}"' in sh
    assert sh.index("export VG_FEED_WORKERS") < sh.index("  vg broker-deep --start")          # exported before the command runs
