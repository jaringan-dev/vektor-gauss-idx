"""`vg collect-broker-full`: the complete per-broker table in a table of its own, stored only when complete."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from vg import __main__ as cli
from vg import brokerfull, watch
from vg.collect import CollectOutcome
from vg.sources import vektorgauss as vgsrc
from vg.sources.vektorgauss import VgBrokerSummarySource, VgFullBrokerSource

D1, D2 = date(2026, 11, 6), date(2026, 11, 5)


def row(code, buy, sell, flag="D"):
    return SimpleNamespace(broker_code=code, side_flag=flag, buy_val=Decimal(buy), sell_val=Decimal(sell), buy_vol=Decimal(buy) / 100, sell_vol=Decimal(sell) / 100)


# ---- the source -----------------------------------------------------------------------------------------------------------------

class Resp:
    status_code = 200
    content = b"{}"

    def raise_for_status(self):
        pass


@pytest.fixture()
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(vgsrc, "CONFIG", SimpleNamespace(feed_base_url="https://feed.test", feed_api_key="k", http_timeout=5))

    def fake_get(url, **kw):
        seen.append((url, kw.get("params")))
        return Resp()

    monkeypatch.setattr(vgsrc.httpx, "get", fake_get)
    return seen


def test_the_default_source_asks_exactly_what_it_always_asked(calls):
    VgBrokerSummarySource()._download("BBCA")
    VgBrokerSummarySource()._download("BBCA", on_date=D1)
    assert calls[0] == ("https://feed.test/api/broker-summary/BBCA", None)                       # no parameters at all
    assert calls[1][1] == {"start_date": "2026-11-06", "end_date": "2026-11-06"}                 # no all_data leaked into the old path


def test_the_full_source_adds_all_data_to_the_same_endpoint(calls):
    VgFullBrokerSource()._download("BBCA")
    VgFullBrokerSource()._download("BBCA", on_date=D1)
    assert calls[0] == ("https://feed.test/api/broker-summary/BBCA", {"all_data": "true"})
    assert calls[1][1] == {"all_data": "true", "start_date": "2026-11-06", "end_date": "2026-11-06"}
    assert VgFullBrokerSource.name == "vektorgauss_full" and VgBrokerSummarySource._extra_params == {}


def test_the_full_source_parses_every_broker_in_the_response():
    doc = {"stock_code": "BBCA", "broker_end_date": "2026-11-06", "brokers": [
        {"broker_code": "YU", "bval": 100, "bvol": 10, "sval": 40, "svol": 4, "nval": 60, "nvol": 6},
        {"broker_code": "XX", "bval": 10, "bvol": 1, "sval": 70, "svol": 7, "nval": -60, "nvol": -6}]}
    rows = VgFullBrokerSource._parse(json.dumps(doc).encode())
    assert [r.broker_code for r in rows] == ["YU", "XX"] and rows[1].sell_val == 70                  # a net SELLER is in it


# ---- completeness ---------------------------------------------------------------------------------------------------------------

def test_complete_means_bought_and_sold_agree_within_one_percent():
    assert brokerfull.complete([row("A", 100, 40), row("B", 60, 120)])[0]                         # 160 vs 160
    ok, buy, sell = brokerfull.complete([row("A", 100, 40), row("B", 60, 100)])                    # 160 vs 140: the top-20-by-net shape
    assert not ok and buy == 160 and sell == 140
    assert not brokerfull.complete([])[0] and not brokerfull.complete([row("A", 0, 0)])[0]
    assert brokerfull.complete([row("A", 1000, 995)])[0] and not brokerfull.complete([row("A", 1000, 980)])[0]


class Src:
    """A stand-in for VgFullBrokerSource.fetch."""

    def __init__(self, status="ok", rows=(), detail=""):
        self.res = SimpleNamespace(status=status, rows=list(rows), detail=detail)

    def __call__(self):
        return self

    def fetch(self, session_date, ticker, *, use_date=False):
        self.asked = (session_date, ticker, use_date)
        return self.res


@pytest.fixture()
def db(monkeypatch):
    stored = []

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return Cur()

    monkeypatch.setattr(brokerfull, "connect", lambda: Conn())
    monkeypatch.setattr(brokerfull, "upsert_many", lambda cur, table, cols, rows, **kw: (stored.append((table, cols, list(rows), kw)), len(rows))[1])
    monkeypatch.setattr(brokerfull, "_log", lambda cur, sd, status, rows, detail, phase: stored.append(("LOG", sd, status, rows, detail, phase)))
    monkeypatch.setattr(brokerfull, "_log_error", lambda sd, exc, phase: stored.append(("ERR", str(exc))))
    return stored


def test_a_complete_table_is_stored_in_the_new_table_only(monkeypatch, db):
    src = Src(rows=[row("YU", 100, 40, "F"), row("XX", 60, 120)])
    monkeypatch.setattr(brokerfull, "VgFullBrokerSource", src)
    out = brokerfull.collect_one(D1, "BBCA", use_date=False)
    assert (out.status, out.rows, out.detail) == ("ok", 2, "BBCA")
    table, cols, rows, kw = db[0]
    assert table == "broker_flows_full" and cols[:4] == ("session_date", "ticker", "broker_code", "side_flag")
    assert rows[0] == (D1, "BBCA", "YU", "F", Decimal(100), Decimal(40), Decimal(1), Decimal("0.4")) and kw["conflict"] == ("ticker", "session_date", "broker_code")
    assert src.asked == (D1, "BBCA", False)


def test_an_incomplete_table_is_not_stored(monkeypatch, db):
    monkeypatch.setattr(brokerfull, "VgFullBrokerSource", Src(rows=[row("YU", 100, 40), row("AA", 60, 100)]))
    out = brokerfull.collect_one(D1, "BBCA", use_date=False)
    assert out.status == "no_data" and "incomplete table" in out.detail and db == []


def test_no_data_stale_and_errors_pass_through_without_a_write(monkeypatch, db):
    monkeypatch.setattr(brokerfull, "VgFullBrokerSource", Src(status="no_data", detail="stale: the feed served 2026-11-05, not 2026-11-06"))
    out = brokerfull.collect_one(D1, "BBCA", use_date=False)
    assert out.status == "no_data" and "stale" in out.detail and db == []

    class Boom(Src):
        def fetch(self, *a, **k):
            raise RuntimeError("feed down")

    monkeypatch.setattr(brokerfull, "VgFullBrokerSource", Boom())
    out = brokerfull.collect_one(D1, "BBCA", use_date=True)
    assert out.status == "error" and "feed down" in out.detail and db == [("ERR", "feed down")]


# ---- the pass -------------------------------------------------------------------------------------------------------------------

def plan(todo, have=(), latest=D1, newest=D1):
    return {"latest": latest, "newest": newest, "universe": list(todo) + list(have), "have": set(have), "todo": list(todo), "n_liquid": 0}


def test_a_day_skips_what_it_has_logs_one_line_and_asks_undated_for_the_newest(monkeypatch, db):
    asked = []
    monkeypatch.setattr(brokerfull, "plan_broker_full", lambda **kw: (asked.append(kw), plan(["AAAA", "BBBB"], have=["CCCC"]))[1])
    monkeypatch.setattr(brokerfull, "collect_one", lambda d, t, use_date: CollectOutcome(d, "ok", 70, t) if t == "AAAA" else CollectOutcome(d, "no_data", 0, t))
    out = brokerfull.collect_day(delay=0)
    assert asked[0]["have_table"] == "broker_flows_full"
    assert out[-1].status == "ok" and out[-1].rows == 70 and "stored 1" in out[-1].detail and "already had 1" in out[-1].detail
    log = next(x for x in db if x[0] == "LOG")
    assert log[2] == "ok" and log[5] == "collect_broker_full"


def test_a_past_day_is_asked_with_a_dated_request(monkeypatch, db):
    modes = []
    monkeypatch.setattr(brokerfull, "plan_broker_full", lambda **kw: plan(["AAAA"], latest=D2, newest=D1))
    monkeypatch.setattr(brokerfull, "collect_one", lambda d, t, use_date: (modes.append(use_date), CollectOutcome(d, "ok", 5, t))[1])
    brokerfull.collect_day(session_date=D2, delay=0)
    assert modes == [True]


def test_the_budget_caps_the_calls_and_says_what_is_left(monkeypatch, db):
    seen = []
    monkeypatch.setattr(brokerfull, "plan_broker_full", lambda **kw: plan(["A", "B", "C", "D"]))
    monkeypatch.setattr(brokerfull, "collect_one", lambda d, t, use_date: (seen.append(t), CollectOutcome(d, "ok", 1, t))[1])
    out = brokerfull.collect_day(delay=0, budget=3)
    assert seen == ["A", "B", "C"] and "1 left for the budget" in out[-1].detail


def test_no_trading_day_is_an_error_not_a_crash(monkeypatch, db):
    monkeypatch.setattr(brokerfull, "plan_broker_full", lambda **kw: {"latest": None, "reason": "no trading_calendar rows"})
    out = brokerfull.collect_day(delay=0)
    assert out[-1].status == "error" and "trading_calendar" in out[-1].detail


def test_recent_sessions_run_newest_first_inside_one_budget(monkeypatch, db):
    monkeypatch.setattr(brokerfull, "recent_sessions", lambda n: [D1, D2][:n])
    plans = {D1: plan(["A", "B"], latest=D1), D2: plan(["C", "D", "E"], latest=D2, newest=D1)}
    monkeypatch.setattr(brokerfull, "plan_broker_full", lambda session_date=None, **kw: plans[session_date])
    seen = []
    monkeypatch.setattr(brokerfull, "collect_one", lambda d, t, use_date: (seen.append((d, t)), CollectOutcome(d, "ok", 1, t))[1])
    out = brokerfull.collect_recent(2, budget=4, delay=0)
    assert [t for _d, t in seen] == ["A", "B", "C", "D"]                                           # today first, then 2 of the next day
    assert "1 left for the budget" in out[-1].detail
    seen.clear()
    brokerfull.collect_recent(2, budget=2, delay=0)
    assert [t for _d, t in seen] == ["A", "B"]                                                      # the budget is spent: the second day is not started


def test_a_day_that_fails_outright_stops_the_run(monkeypatch, db):
    monkeypatch.setattr(brokerfull, "recent_sessions", lambda n: [D1, D2])
    monkeypatch.setattr(brokerfull, "plan_broker_full", lambda session_date=None, **kw: plan(["A"], latest=session_date))
    monkeypatch.setattr(brokerfull, "collect_one", lambda d, t, use_date: CollectOutcome(d, "error", 0, "HTTPStatusError: 401"))
    monkeypatch.setattr("vg.collect._FEED_ABORT_AFTER_ERRORS", 1)
    out = brokerfull.collect_recent(2, delay=0)
    assert out[-1].status == "error" and sum(1 for o in out if o.detail.startswith("1 names") or "ABORTED" in o.detail) == 1


# ---- wiring ---------------------------------------------------------------------------------------------------------------------

def test_the_command_is_registered_and_wired_into_eod_and_monitoring():
    args = cli.build_parser().parse_args(["collect-broker-full", "--sessions", "3", "--budget", "500"])
    assert (args.cmd, args.sessions, args.budget, args.plan) == ("collect-broker-full", 3, 500, False)
    eod = (Path(__file__).resolve().parents[1] / "scripts" / "eod.sh").read_text(encoding="utf-8")
    assert "vg collect-broker-full --sessions 1 || true" in eod and eod.index("collect-broker-summary --full") < eod.index("collect-broker-full")
    assert ("collect_broker_full", "complete broker tables", False) in watch.DAILY


def test_the_command_prints_the_outcomes_and_its_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(brokerfull, "collect_recent", lambda n, budget=None: [CollectOutcome(D1, "ok", 7, "done")])
    assert cli.main(["collect-broker-full"]) == 0 and "rows=7" in capsys.readouterr().out
    monkeypatch.setattr(brokerfull, "collect_recent", lambda n, budget=None: [CollectOutcome(D1, "error", 0, "ABORTED")])
    assert cli.main(["collect-broker-full"]) == 1
    monkeypatch.setattr(brokerfull, "recent_sessions", lambda n: [D1])
    monkeypatch.setattr("vg.collect.plan_broker_full", lambda **kw: plan(["A", "B"], have=["C"]))
    assert cli.main(["collect-broker-full", "--plan"]) == 0 and "would fetch 2" in capsys.readouterr().out
