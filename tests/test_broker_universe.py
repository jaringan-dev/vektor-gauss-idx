"""Checklist G3 — the daily broker pass covers the whole liquid universe, and the repair commands
the coverage page recommends really exist (no DB, no network)."""

from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from vg import collect, engine  # noqa: E402
from vg.collect import CollectOutcome  # noqa: E402
from vg.config import Config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LATEST = date(2026, 10, 30)
OLDER = date(2026, 10, 16)


def test_select_broker_universe_keeps_priority_order_and_dedupes():
    out = collect.select_broker_universe(
        base=["BBCA", "BBRI"],                 # watchlist/holdings first
        liquid=["TLKM", "BBCA", "ASII"],       # BBCA repeats -> keep its first (base) position
        radar=["ASII", "ZZZZ"],                # ASII repeats; ZZZZ is a new illiquid radar name
    )
    assert out == ["BBCA", "BBRI", "TLKM", "ASII", "ZZZZ"]


def test_liquid_threshold_matches_the_engines_liquid_definition():
    """The broker universe must be the same 'liquid' as edge H5 (liquid) in the eval."""
    assert collect._BROKER_LIQUID_VALUE_IDR == engine.LIQUID_VALUE_IDR


B = 1_000_000_000


def test_sticky_liquid_keeps_a_name_that_was_liquid_earlier_in_the_window():
    """6 Nov 2026: liquid-on-the-day fell 202 -> 136 in two weeks with no fetch failing; names near Rp5B left holes."""
    rows = [
        ("BBCA", 900 * B, 1200 * B),   # liquid today
        ("NEAR", 4 * B, 6 * B),        # below the line today, above it earlier -> kept (sticky)
        ("TINY", 1 * B, 2 * B),        # never liquid in the window -> out
        ("HALT", None, 9 * B),         # liquid earlier but did not trade today -> out (the call would only return no_data)
        ("ZERO", 0, 8 * B),            # traded nothing today -> out
        ("MID", 30 * B, 30 * B),       # liquid today
        ("EDGE", 5 * B, 5 * B),        # exactly the threshold is NOT above it -> out (same `>` as the engine)
    ]
    names, n_today = collect.sticky_liquid(rows, threshold=5 * B)
    assert names == ["BBCA", "MID", "NEAR"]      # liquid-today first by value, then the sticky extras
    assert n_today == 2


def test_sticky_liquid_with_a_one_day_window_is_the_old_rule():
    rows = [("A", 7 * B, 7 * B), ("B", 3 * B, 3 * B)]
    assert collect.sticky_liquid(rows, threshold=5 * B) == (["A"], 1)


def test_broker_liquid_lookback_defaults_to_twenty_sessions(monkeypatch):
    monkeypatch.delenv("VG_BROKER_LIQUID_LOOKBACK", raising=False)
    assert Config().broker_liquid_lookback == 20
    monkeypatch.setenv("VG_BROKER_LIQUID_LOOKBACK", "1")
    assert Config().broker_liquid_lookback == 1


# ---- collect_broker_summary_full -------------------------------------------------------

@pytest.fixture()
def rig(monkeypatch):
    """Fake plan + fake per-ticker fetch + recorded sleeps. `rig.status` maps ticker -> status."""
    class Rig:
        pass

    rig = Rig()
    rig.todo, rig.have, rig.status, rig.fetched, rig.kwargs, rig.sleeps = [], set(), {}, [], [], []
    rig.latest, rig.newest, rig.reason = LATEST, LATEST, None

    def fake_plan(**k):
        rig.plan_args = k
        if rig.reason:
            return {"latest": None, "newest": rig.newest, "universe": [], "have": set(), "todo": [],
                    "n_liquid": 0, "reason": rig.reason}
        return {"latest": rig.latest, "newest": rig.newest, "universe": rig.todo + sorted(rig.have),
                "have": rig.have, "todo": rig.todo, "n_liquid": len(rig.todo)}

    def fake_fetch(session_date, ticker, **kw):
        rig.fetched.append(ticker)
        rig.kwargs.append(kw)
        st = rig.status.get(ticker, "ok")
        return CollectOutcome(session_date, st, 20 if st == "ok" else 0, f"{ticker}")

    rig.stale, rig.probed = [], []                            # answers of the "is the feed still on yesterday?" probe

    def fake_probe(session_date, ticker):
        rig.probed.append(ticker)
        return rig.stale.pop(0) if rig.stale else False

    monkeypatch.setattr(collect, "plan_broker_full", fake_plan)
    monkeypatch.setattr(collect, "collect_broker_summary_ticker", fake_fetch)
    monkeypatch.setattr(collect, "_feed_day_stale", fake_probe)
    monkeypatch.delenv("VG_FEED_WAIT_MINUTES", raising=False)
    monkeypatch.setattr(collect.time, "sleep", lambda s: rig.sleeps.append(s))
    monkeypatch.setattr(collect.CONFIG, "feed_delay_seconds", 0.25)
    monkeypatch.setattr(collect.CONFIG, "request_delay_seconds", 5.0)
    return rig


def test_fetches_exactly_the_planned_names_in_order_and_returns_one_summary(rig):
    rig.todo = ["BBCA", "BBRI", "TLKM"]
    rig.have = {"ASII"}
    outs = collect.collect_broker_summary_full()
    assert rig.fetched == ["BBCA", "BBRI", "TLKM"]            # ASII already stored -> not re-fetched
    assert len(outs) == 1 and outs[0].status == "ok" and outs[0].rows == 60
    assert "fetched 3" in outs[0].detail and "skipped 1 already-have" in outs[0].detail


def test_uses_the_feed_delay_not_the_idx_delay(rig):
    rig.todo = ["A", "B", "C", "D"]
    collect.collect_broker_summary_full()
    assert rig.sleeps == [0.25, 0.25, 0.25]                   # between calls only, none after the last
    assert 5.0 not in rig.sleeps


def test_explicit_delay_overrides_config(rig):
    rig.todo = ["A", "B"]
    collect.collect_broker_summary_full(delay=0)
    assert rig.sleeps == [0]


def test_no_data_is_benign_and_never_counts_toward_the_abort(rig):
    rig.todo = [f"T{i}" for i in range(30)]
    rig.status = {t: "no_data" for t in rig.todo}             # 30 names that simply did not trade
    outs = collect.collect_broker_summary_full()
    assert len(rig.fetched) == 30                              # kept going
    assert outs[-1].status == "ok" and "no_data 30" in outs[-1].detail and "errors 0" in outs[-1].detail


def test_ten_consecutive_hard_errors_abort_the_run(rig):
    rig.todo = [f"T{i}" for i in range(50)]
    rig.status = {t: "error" for t in rig.todo}                # feed dead / quota gone
    outs = collect.collect_broker_summary_full()
    assert len(rig.fetched) == 10                              # stopped, did not grind through 50
    assert outs[-1].status == "error" and "ABORTED" in outs[-1].detail
    assert sum(1 for o in outs if o.status == "error") == 11   # 10 failures kept visible + the summary


def test_interleaved_errors_do_not_abort(rig):
    rig.todo = [f"T{i}" for i in range(19)]
    rig.status = {f"T{i}": "error" for i in range(19) if i != 9}   # 9 errors, 1 ok, 9 errors
    outs = collect.collect_broker_summary_full()
    assert len(rig.fetched) == 19
    assert outs[-1].status == "ok" and "errors 18" in outs[-1].detail


def test_nothing_to_fetch_returns_just_a_summary_and_never_sleeps(rig):
    rig.todo, rig.have = [], {"BBCA", "BBRI"}
    outs = collect.collect_broker_summary_full()
    assert rig.fetched == [] and rig.sleeps == []
    assert len(outs) == 1 and outs[0].status == "ok" and "skipped 2" in outs[0].detail


def test_no_trading_calendar_is_a_clear_error(rig):
    rig.reason = None
    rig.latest = None
    outs = collect.collect_broker_summary_full()
    assert len(outs) == 1 and outs[0].status == "error" and "trading_calendar" in outs[0].detail


def test_a_date_that_is_not_a_trading_day_reports_why(rig):
    rig.reason = "2026-10-31 is not a trading day in trading_calendar"
    outs = collect.collect_broker_summary_full(session_date=date(2026, 10, 31))
    assert len(outs) == 1 and outs[0].status == "error" and "not a trading day" in outs[0].detail
    assert outs[0].session_date == date(2026, 10, 31)


def test_newest_day_keeps_the_original_undated_call(rig):
    """Production has always called the feed without a date for 'today'; that must not change."""
    rig.todo = ["BBCA"]
    collect.collect_broker_summary_full()
    assert rig.kwargs == [{"use_date": False}]


def test_a_past_day_requests_that_exact_date(rig):
    rig.todo, rig.latest, rig.newest = ["BBCA", "BBRI"], OLDER, LATEST
    collect.collect_broker_summary_full(session_date=OLDER)
    assert rig.plan_args == {"session_date": OLDER, "have_table": "broker_flows"}
    assert rig.kwargs == [{"use_date": True}, {"use_date": True}]


def test_asking_for_the_newest_day_explicitly_still_uses_the_undated_call(rig):
    rig.todo = ["BBCA"]
    collect.collect_broker_summary_full(session_date=LATEST)
    assert rig.kwargs == [{"use_date": False}]


def test_waits_for_the_feed_to_publish_the_day_then_fetches(rig):
    rig.todo = ["BBCA", "BBRI"]
    rig.stale = [True, True, False]                           # two asks still on yesterday, the third has today
    outs = collect.collect_broker_summary_full()
    assert rig.probed == ["BBCA", "BBCA", "BBCA"]             # always the same, first-planned name
    assert rig.sleeps[:2] == [300, 300]                       # five minutes between asks
    assert rig.fetched == ["BBCA", "BBRI"]
    assert outs[-1].status == "ok" and "waited 10 min" in outs[-1].detail


def test_gives_up_after_the_limit_and_stores_nothing(rig, monkeypatch):
    rig.todo = ["BBCA", "BBRI"]
    rig.stale = [True] * 50
    monkeypatch.setenv("VG_FEED_WAIT_MINUTES", "15")
    outs = collect.collect_broker_summary_full()
    assert rig.fetched == []                                  # did not grind through 171 names that are all stale
    assert len(rig.probed) == 4                               # at 0, 5, 10 and 15 minutes
    assert len(outs) == 1 and outs[0].status == "no_data" and "still serving an earlier day after 15 min" in outs[0].detail


def test_wait_zero_means_one_look_and_no_waiting(rig, monkeypatch):
    rig.todo = ["BBCA"]
    rig.stale = [True]
    monkeypatch.setenv("VG_FEED_WAIT_MINUTES", "0")
    outs = collect.collect_broker_summary_full()
    assert rig.sleeps == [] and rig.fetched == [] and outs[0].status == "no_data"


def test_a_garbled_wait_setting_falls_back_to_an_hour(monkeypatch):
    monkeypatch.setenv("VG_FEED_WAIT_MINUTES", "soon")
    assert collect._feed_wait_max_s() == 3600


def test_a_past_day_never_waits_for_the_feed(rig):
    rig.todo, rig.latest, rig.newest = ["BBCA"], OLDER, LATEST
    rig.stale = [True]                                        # would block forever if it were consulted
    outs = collect.collect_broker_summary_full(session_date=OLDER)
    assert rig.probed == [] and rig.fetched == ["BBCA"] and outs[-1].status == "ok"


def test_nothing_to_fetch_does_not_probe_the_feed(rig):
    rig.todo, rig.have = [], {"BBCA"}
    collect.collect_broker_summary_full()
    assert rig.probed == []


def test_the_probe_reads_the_stale_marker_of_the_source(monkeypatch):
    from vg.sources.base import BrokerSummaryFetchResult
    from vg.sources.vektorgauss import STALE_PREFIX

    def fetch_returning(status, detail):
        return lambda self, d, t, **k: BrokerSummaryFetchResult(status, d, t, "feed", 3, detail=detail)

    src = collect.VgBrokerSummarySource
    monkeypatch.setattr(src, "fetch", fetch_returning("no_data", f"{STALE_PREFIX}the feed served 2026-10-29"))
    assert collect._feed_day_stale(LATEST, "BBCA") is True
    monkeypatch.setattr(src, "fetch", fetch_returning("no_data", "no rows"))       # a name that did not trade
    assert collect._feed_day_stale(LATEST, "BBCA") is False
    monkeypatch.setattr(src, "fetch", fetch_returning("error", "ConnectError"))    # an outage is not "not published yet"
    assert collect._feed_day_stale(LATEST, "BBCA") is False


# ---- backfill_broker_full ---------------------------------------------------------------

class _Cur:
    def __init__(self, one=None, rows=None):
        self.one, self.rows, self.executed = one, rows or [], []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_connect(cur):
    class _Conn:
        def cursor(self):
            return cur

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    @contextmanager
    def connect():
        yield _Conn()

    return connect


def test_backfill_runs_each_trading_day_oldest_first(monkeypatch):
    days = [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)]
    monkeypatch.setattr(collect, "connect", _fake_connect(_Cur(rows=[(d,) for d in days])))
    seen = []

    def fake_full(*, delay=None, session_date=None, have_table="broker_flows"):
        seen.append(session_date)
        return [CollectOutcome(session_date, "ok", 100, "190 names")]

    monkeypatch.setattr(collect, "collect_broker_summary_full", fake_full)
    outs = collect.backfill_broker_full(date(2026, 10, 16), date(2026, 10, 20))
    assert seen == days and [o.session_date for o in outs] == days


def test_backfill_stops_at_the_first_failing_day(monkeypatch):
    days = [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)]
    monkeypatch.setattr(collect, "connect", _fake_connect(_Cur(rows=[(d,) for d in days])))
    seen = []

    def fake_full(*, delay=None, session_date=None, have_table="broker_flows"):
        seen.append(session_date)
        st = "error" if session_date == days[1] else "ok"
        return [CollectOutcome(session_date, st, 0, "x")]

    monkeypatch.setattr(collect, "collect_broker_summary_full", fake_full)
    outs = collect.backfill_broker_full(days[0], days[2])
    assert seen == days[:2]                                    # never reached the third day
    assert outs[-1].status == "error"


def test_backfill_with_no_trading_days_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(collect, "connect", _fake_connect(_Cur(rows=[])))
    outs = collect.backfill_broker_full(date(2026, 10, 24), date(2026, 10, 25))      # a weekend
    assert len(outs) == 1 and outs[0].status == "error" and "no trading days" in outs[0].detail


# ---- collect_price_history_all ---------------------------------------------------------------

@pytest.fixture()
def price_rig(monkeypatch):
    class Rig:
        pass

    rig = Rig()
    rig.status, rig.fetched, rig.sleeps = {}, [], []
    monkeypatch.setattr(collect, "connect",
                        _fake_connect(_Cur(one=(LATEST,), rows=[(t,) for t in ("AAAA", "BBBB", "CCCC")])))

    def fake_ticker(t):
        rig.fetched.append(t)
        st = rig.status.get(t, "ok")
        return CollectOutcome(LATEST, st, 120 if st == "ok" else 0, t)

    monkeypatch.setattr(collect, "collect_price_history_ticker", fake_ticker)
    monkeypatch.setattr(collect.time, "sleep", lambda s: rig.sleeps.append(s))
    monkeypatch.setattr(collect.CONFIG, "feed_delay_seconds", 0.25)
    return rig


def test_price_history_all_visits_every_ticker_even_those_that_already_have_the_newest_day(price_rig):
    """The whole point: repair OLDER gaps, so nothing may be skipped for already having today."""
    outs = collect.collect_price_history_all()
    assert price_rig.fetched == ["AAAA", "BBBB", "CCCC"]
    assert price_rig.sleeps == [0.25, 0.25]
    assert len(outs) == 1 and outs[0].status == "ok" and outs[0].rows == 360
    assert "3 tickers: filled 3" in outs[0].detail


def test_price_history_all_aborts_on_consecutive_errors(price_rig, monkeypatch):
    monkeypatch.setattr(collect, "connect", _fake_connect(
        _Cur(one=(LATEST,), rows=[(f"T{i:03d}",) for i in range(40)])))
    price_rig.status = {f"T{i:03d}": "error" for i in range(40)}
    outs = collect.collect_price_history_all()
    assert len(price_rig.fetched) == 10
    assert outs[-1].status == "error" and "ABORTED" in outs[-1].detail


def test_price_history_all_without_any_price_data_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(collect, "connect", _fake_connect(_Cur(one=(None,))))
    outs = collect.collect_price_history_all()
    assert len(outs) == 1 and outs[0].status == "error" and "collect-price" in outs[0].detail


# ---- configuration ------------------------------------------------------------------------

def test_feed_delay_defaults_to_one_second_and_is_overridable(monkeypatch):
    monkeypatch.delenv("VG_FEED_DELAY_SECONDS", raising=False)
    assert Config.load().feed_delay_seconds == 1.0
    monkeypatch.setenv("VG_FEED_DELAY_SECONDS", "2.5")
    assert Config.load().feed_delay_seconds == 2.5


def test_feed_delay_is_independent_of_the_idx_delay(monkeypatch):
    monkeypatch.setenv("VG_REQUEST_DELAY_SECONDS", "5")
    monkeypatch.delenv("VG_FEED_DELAY_SECONDS", raising=False)
    cfg = Config.load()
    assert cfg.request_delay_seconds == 5.0 and cfg.feed_delay_seconds == 1.0


# ---- CLI ------------------------------------------------------------------------------------

def _plan(**over):
    base = {"latest": LATEST, "newest": LATEST, "universe": ["A", "B", "C"], "have": {"A"},
            "todo": ["B", "C"], "n_liquid": 3}
    base.update(over)
    return lambda **k: base


def test_cli_plan_prints_the_plan_and_fetches_nothing(monkeypatch, capsys):
    from vg.__main__ import main

    monkeypatch.setattr(collect, "plan_broker_full", _plan())

    def must_not_run(*a, **k):
        raise AssertionError("--plan must not fetch")

    monkeypatch.setattr(collect, "collect_broker_summary_full", must_not_run)
    assert main(["collect-broker-summary", "--full", "--plan"]) == 0
    out = capsys.readouterr().out
    assert "universe 3" in out and "already stored 1" in out and "would fetch 2" in out and "B, C" in out


def test_cli_plan_with_a_date_passes_the_date_through(monkeypatch):
    from vg.__main__ import main

    got = {}
    monkeypatch.setattr(collect, "plan_broker_full", lambda **k: got.update(k) or _plan()(**k))
    assert main(["collect-broker-summary", "--full", "--plan", "--date", "2026-10-16"]) == 0
    assert got == {"session_date": OLDER, "have_table": "broker_flows"}


def test_cli_plan_for_a_non_trading_day_exits_1_with_the_reason(monkeypatch, capsys):
    from vg.__main__ import main

    monkeypatch.setattr(collect, "plan_broker_full", _plan(latest=None, reason="2026-10-31 is not a trading day"))
    assert main(["collect-broker-summary", "--full", "--plan", "--date", "2026-10-31"]) == 1
    assert "not a trading day" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["collect-broker-summary", "--latest", "--plan"],
    ["collect-broker-summary", "--radar", "--date", "2026-10-16"],
    ["collect-broker-summary", "--latest", "--backfill", "2026-10-16", "2026-10-20"],
])
def test_cli_rejects_full_only_flags_without_full(monkeypatch, capsys, argv):
    from vg.__main__ import main

    def must_not_run(*a, **k):
        raise AssertionError("must not fetch")

    for name in ("collect_broker_summary_latest", "collect_broker_summary_radar", "collect_broker_summary_full"):
        monkeypatch.setattr(collect, name, must_not_run)
    assert main(argv) == 2
    assert "only apply together with --full" in capsys.readouterr().out


def test_cli_backfill_cannot_be_combined_with_date_or_plan(capsys):
    from vg.__main__ import main

    assert main(["collect-broker-summary", "--full", "--backfill", "2026-10-16", "2026-10-20",
                 "--date", "2026-10-16"]) == 2
    assert "cannot be combined" in capsys.readouterr().out


def test_cli_full_date_calls_the_dated_pass(monkeypatch):
    from vg.__main__ import main

    got = {}
    monkeypatch.setattr(collect, "collect_broker_summary_full",
                        lambda **k: got.update(k) or [CollectOutcome(OLDER, "ok", 100, "190 names")])
    assert main(["collect-broker-summary", "--full", "--date", "2026-10-16"]) == 0
    assert got == {"session_date": OLDER, "have_table": "broker_flows"}


def test_cli_full_backfill_calls_the_range_pass(monkeypatch):
    from vg.__main__ import main

    got = []
    monkeypatch.setattr(collect, "backfill_broker_full",
                        lambda a, b, **k: got.append((a, b)) or [CollectOutcome(b, "ok", 100, "190 names")])
    assert main(["collect-broker-summary", "--full", "--backfill", "2026-10-16", "2026-10-20"]) == 0
    assert got == [(date(2026, 10, 16), date(2026, 10, 20))]


def test_cli_full_reports_failure_through_the_exit_code(monkeypatch):
    from vg.__main__ import main

    monkeypatch.setattr(collect, "collect_broker_summary_full",
                        lambda **k: [CollectOutcome(LATEST, "error", 0, "ABORTED after 10 consecutive errors")])
    assert main(["collect-broker-summary", "--full"]) == 1
    monkeypatch.setattr(collect, "collect_broker_summary_full",
                        lambda **k: [CollectOutcome(LATEST, "ok", 3800, "190 names")])
    assert main(["collect-broker-summary", "--full"]) == 0


def test_cli_price_history_all_routes_to_the_all_pass(monkeypatch):
    from vg.__main__ import main

    monkeypatch.setattr(collect, "collect_price_history_all",
                        lambda: [CollectOutcome(LATEST, "ok", 100000, "962 tickers")])
    monkeypatch.setattr(collect, "collect_price_history_universe",
                        lambda: (_ for _ in ()).throw(AssertionError("--all must not use the universe pass")))
    assert main(["collect-price-history", "--all"]) == 0
    monkeypatch.setattr(collect, "collect_price_history_all",
                        lambda: [CollectOutcome(LATEST, "error", 0, "ABORTED")])
    assert main(["collect-price-history", "--all"]) == 1


# ---- production wiring --------------------------------------------------------------------

def test_eod_script_runs_the_full_pass_not_the_radar_cap():
    """The nightly run is what keeps coverage up. Regressing to --radar brings back the ~60/day sag."""
    lines = [ln.strip() for ln in (ROOT / "scripts" / "eod.sh").read_text(encoding="utf-8").splitlines()
             if not ln.strip().startswith("#")]
    broker = [ln for ln in lines if ln.startswith("vg collect-broker-summary")]
    assert broker == ["vg collect-broker-summary --full || true"]


# ---- K6: re-fetch the gross sell side for history stored before it was parsed -------------

def test_plan_rejects_an_unknown_have_table():
    with pytest.raises(ValueError):
        collect.plan_broker_full(have_table="price_bars; DROP TABLE x")


def test_levels_only_flag_requires_full_and_is_wired_to_have_table():
    from vg.__main__ import build_parser
    p = build_parser()
    a = p.parse_args(["collect-broker-summary", "--full", "--backfill", "2026-06-03", "2026-10-30", "--levels-only"])
    assert a.levels_only is True and a.full is True
    assert p.parse_args(["collect-broker-summary", "--full"]).levels_only is False


# ---- backfill_broker_deep (K7) ----------------------------------------------------------------

def _deep_rig(monkeypatch, todo_by_day, fail_on=None):
    days = sorted(todo_by_day)
    monkeypatch.setattr(collect, "connect", _fake_connect(_Cur(rows=[(d,) for d in reversed(days)])))
    fetched = []
    monkeypatch.setattr(collect, "plan_broker_full",
                        lambda *, session_date=None, have_table="broker_flows", **k: {"todo": ["T"] * todo_by_day[session_date]})

    def fake_full(*, delay=None, session_date=None, have_table="broker_flows"):
        fetched.append(session_date)
        return [CollectOutcome(session_date, "error" if session_date == fail_on else "ok", 10, "x")]

    monkeypatch.setattr(collect, "collect_broker_summary_full", fake_full)
    return days, fetched


def test_deep_backfill_goes_newest_first_and_never_exceeds_the_budget(monkeypatch):
    d = [date(2026, 5, 29), date(2026, 5, 28), date(2026, 5, 27), date(2026, 5, 26)]
    days, fetched = _deep_rig(monkeypatch, {d[0]: 200, d[1]: 200, d[2]: 200, d[3]: 200})
    res = collect.backfill_broker_deep(d[3], d[0], budget=450)
    assert fetched == [d[0], d[1]] and res["calls"] == 400 and res["filled"] == [d[0], d[1]]
    assert res["pending_days"] == 2 and res["pending_calls"] == 400 and res["error"] is None


def test_deep_backfill_skips_complete_days_and_resumes_where_it_stopped(monkeypatch):
    d = [date(2026, 5, 29), date(2026, 5, 28), date(2026, 5, 27)]
    _days, fetched = _deep_rig(monkeypatch, {d[0]: 0, d[1]: 120, d[2]: 130})
    res = collect.backfill_broker_deep(d[2], d[0], budget=10_000)
    assert fetched == [d[1], d[2]] and res["pending_days"] == 0 and res["calls"] == 250


def test_deep_backfill_plan_only_fetches_nothing(monkeypatch):
    d = [date(2026, 5, 29), date(2026, 5, 28)]
    _days, fetched = _deep_rig(monkeypatch, {d[0]: 100, d[1]: 100})
    res = collect.backfill_broker_deep(d[1], d[0], budget=10_000, plan_only=True)
    assert fetched == [] and res["pending_calls"] == 200 and res["calls"] == 0


def test_deep_backfill_stops_at_the_first_failing_day_and_reports_why(monkeypatch):
    d = [date(2026, 5, 29), date(2026, 5, 28), date(2026, 5, 27)]
    _days, fetched = _deep_rig(monkeypatch, {x: 100 for x in d}, fail_on=d[1])
    res = collect.backfill_broker_deep(d[2], d[0], budget=10_000)
    assert fetched == [d[0], d[1]] and res["error"] and res["filled"] == [d[0]]
    assert res["pending_days"] == 2 and res["pending_calls"] == 200


def test_deep_script_runs_the_budgeted_command_and_logs():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "scripts" / "deep_backfill.sh").read_text(encoding="utf-8")
    assert "vg broker-deep" in text and "--compute" in text and "VG_DEEP_BUDGET" in text and "deep_backfill.log" in text
