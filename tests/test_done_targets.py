"""Done-details are harvested for the watchlist (+ configured LQ45) only, inside a time budget (no DB, no network)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from vg import collect
from vg.collect import CollectOutcome

ROOT = Path(__file__).resolve().parent.parent
DAY = date(2026, 11, 5)


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setattr(collect.CONFIG, "watchlist", ("BBRI", "BBCA"))
    monkeypatch.setattr(collect.CONFIG, "holdings", ("BBRI", "ANTM"))
    monkeypatch.setattr(collect.CONFIG, "lq45_tickers", ("TLKM", "BBCA", "ASII"))
    monkeypatch.setattr(collect.CONFIG, "done_budget_minutes", 15.0)
    return collect.CONFIG


def test_targets_are_watchlist_then_holdings_then_lq45_without_duplicates(cfg):
    assert collect.done_targets() == ["BBRI", "BBCA", "ANTM", "TLKM", "ASII"]


def test_targets_are_empty_when_nothing_is_configured(monkeypatch):
    for name in ("watchlist", "holdings", "lq45_tickers"):
        monkeypatch.setattr(collect.CONFIG, name, ())
    assert collect.done_targets() == []


def test_lq45_is_never_invented_by_the_repo():
    from vg.config import Settings

    assert Settings.model_fields["lq45_tickers"].default == ()


def _recorder(monkeypatch, clock_state=None, cost=0.0):
    calls = []

    def fake(ticker, session_date=None, *, max_pages=200, force=False):
        calls.append((ticker, session_date, max_pages, force))
        if clock_state is not None:
            clock_state[0] += cost
        return CollectOutcome(session_date or DAY, "ok", 3, ticker)

    monkeypatch.setattr(collect, "collect_done_details_ticker", fake)
    return calls


def test_universe_harvests_exactly_the_targets_in_priority_order(cfg, monkeypatch):
    calls = _recorder(monkeypatch)
    outs = collect.collect_done_details_universe(session_date=DAY, max_pages=50, force=True)
    assert [c[0] for c in calls] == ["BBRI", "BBCA", "ANTM", "TLKM", "ASII"]
    assert all(c[1:] == (DAY, 50, True) for c in calls)
    assert [o.status for o in outs] == ["ok"] * 5


def test_universe_never_touches_a_ticker_outside_the_targets(cfg, monkeypatch):
    calls = _recorder(monkeypatch)
    collect.collect_done_details_universe()
    assert {c[0] for c in calls} <= set(collect.done_targets())       # no whole-market fallback


def test_explicit_empty_list_means_nothing_to_do_not_everything(cfg, monkeypatch):
    calls = _recorder(monkeypatch)
    assert collect.collect_done_details_universe([]) == [] and calls == []


def test_budget_stops_new_tickers_and_reports_the_rest_as_skipped(cfg, monkeypatch):
    now = [0.0]
    calls = _recorder(monkeypatch, now, cost=400.0)                    # each ticker "takes" 400 s
    outs = collect.collect_done_details_universe(budget_seconds=600, clock=lambda: now[0])
    assert [c[0] for c in calls] == ["BBRI", "BBCA"]                   # 0 s and 400 s are inside 600; 800 s is not
    assert [o.status for o in outs] == ["ok", "ok", "skipped", "skipped", "skipped"]
    assert "ANTM" in outs[2].detail and "budget" in outs[2].detail


def test_budget_comes_from_config_and_zero_means_uncapped(cfg, monkeypatch):
    now = [0.0]
    calls = _recorder(monkeypatch, now, cost=10_000.0)
    monkeypatch.setattr(collect.CONFIG, "done_budget_minutes", 0.0)
    collect.collect_done_details_universe(clock=lambda: now[0])
    assert len(calls) == 5

    calls.clear()
    now[0] = 0.0
    monkeypatch.setattr(collect.CONFIG, "done_budget_minutes", 1.0)    # 60 s: spent after the first ticker
    collect.collect_done_details_universe(clock=lambda: now[0])
    assert len(calls) == 1


# ---- CLI -----------------------------------------------------------------------------------------------------------

def test_cli_watchlist_only_runs_the_targets_and_summarises(cfg, monkeypatch, capsys):
    from vg.__main__ import main

    got = {}

    def fake_universe(tickers, day, **kw):
        got.update(tickers=tickers, day=day, **kw)
        return [CollectOutcome(DAY, "ok", 4, "BBRI"), CollectOutcome(DAY, "skipped", 0, "ASII: budget")]

    monkeypatch.setattr(collect, "collect_done_details_universe", fake_universe)
    assert main(["collect-done-details", "--watchlist-only", "--date", "2026-11-05", "--max-pages", "9"]) == 0
    assert got["tickers"] == ["BBRI", "BBCA", "ANTM", "TLKM", "ASII"] and got["day"] == DAY
    assert got["max_pages"] == 9 and got["force"] is False
    assert "done: 1/2 tickers with data, 1 skipped (budget)" in capsys.readouterr().out


def test_cli_watchlist_only_without_configuration_says_so_and_fetches_nothing(monkeypatch, capsys):
    from vg.__main__ import main

    for name in ("watchlist", "holdings", "lq45_tickers"):
        monkeypatch.setattr(collect.CONFIG, name, ())
    monkeypatch.setattr(collect, "collect_done_details_universe",
                        lambda *a, **k: pytest.fail("must not fetch without targets"))
    assert main(["collect-done-details", "--watchlist-only"]) == 0
    assert "VG_WATCHLIST" in capsys.readouterr().out


def test_cli_single_ticker_mode_still_works(monkeypatch):
    from vg.__main__ import main

    got = {}
    monkeypatch.setattr(collect, "collect_done_details_ticker",
                        lambda t, d=None, **k: got.update(t=t, d=d, **k) or CollectOutcome(DAY, "ok", 1, t))
    assert main(["collect-done-details", "--ticker", "bbca", "--force"]) == 0
    assert got["t"] == "bbca" and got["force"] is True


def test_cli_requires_exactly_one_of_ticker_or_watchlist_only():
    from vg.__main__ import main

    with pytest.raises(SystemExit):
        main(["collect-done-details"])                                 # no whole-market default
    with pytest.raises(SystemExit):
        main(["collect-done-details", "--ticker", "BBCA", "--watchlist-only"])


# ---- eod.sh wiring ---------------------------------------------------------------------------------------------------

def _eod_lines():
    return [ln.strip() for ln in (ROOT / "scripts" / "eod.sh").read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def test_eod_harvests_done_details_watchlist_only_between_broker_summary_and_the_second_compute():
    lines = _eod_lines()
    done = [i for i, ln in enumerate(lines) if "collect-done-details" in ln]
    assert len(done) == 1 and "--watchlist-only" in lines[done[0]] and "|| true" in lines[done[0]]
    summary = next(i for i, ln in enumerate(lines) if "collect-broker-summary --full" in ln)
    computes = [i for i, ln in enumerate(lines) if "vg compute" in ln]
    assert summary < done[0] < computes[1]                             # lands before pass 2, where it is scored


def test_eod_applies_the_schema_before_any_compute():
    lines = _eod_lines()
    init = next(i for i, ln in enumerate(lines) if ln.startswith("vg init-db"))
    first_compute = next(i for i, ln in enumerate(lines) if "vg compute" in ln)
    assert init < first_compute


def test_eod_script_has_unix_line_endings():
    assert b"\r" not in (ROOT / "scripts" / "eod.sh").read_bytes()
