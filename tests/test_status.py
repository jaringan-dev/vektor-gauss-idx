"""System status (vg.status): checks, Telegram text, silence rules, never-raising collection, scripts and the /health page."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from vg import status as st

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 4, 8, 0, tzinfo=timezone(timedelta(hours=7)))


def _iso(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _facts(**over):
    f = {"now": NOW, "errors": [],
         "strip": [{"label": "EOD", "text": "3 Nov 2026", "level": "ok"}, {"label": "Prices", "text": "963 stocks", "level": "ok"},
                   {"label": "Broker", "text": "202 stocks", "level": "ok"}, {"label": "KSEI", "text": "Oct 2026", "level": "ok"}],
         "failed_24h": {}, "blocked_24h": 0,
         "deep": {"total_days": 805, "pending_days": 700, "pending_calls": 140_000,
                  "last": {"status": "ok", "calls": 15_000, "error": None, "started_at": _iso(8)}},
         "backup": {"tag": "backup-2026-11-03", "age_days": 1.0}, "dashboard": True, "disk_free_pct": 90.0,
         "snapshots": {"risk_eval_v1": _iso(20), "research_v1": _iso(20)}}
    f.update(over)
    return f


def _by(checks):
    return {c.name: c for c in checks}


def test_everything_fine_is_all_ok_and_says_so():
    checks = st.build_checks(_facts())
    assert st.headline(checks) == (st.OK, "All systems normal") and all(c.level == st.OK for c in checks)


def test_freshness_levels_pass_straight_through():
    f = _facts()
    f["strip"][1] = {"label": "Prices", "text": "5 stocks — gap (normal ≈ 962)", "level": "bad"}
    c = _by(st.build_checks(f))
    assert c["Prices"].level == st.BAD and "gap" in c["Prices"].text


def test_failed_runs_warn_only_above_the_noise_floor_and_a_blocked_gate_is_a_problem():
    assert _by(st.build_checks(_facts(failed_24h={"collect_broker": 3})))["Collector errors"].level == st.OK
    w = _by(st.build_checks(_facts(failed_24h={"collect_broker": 4, "compute": 2})))["Collector errors"]
    assert w.level == st.WARN and "collect_broker ×4" in w.text
    assert _by(st.build_checks(_facts(blocked_24h=1)))["Quality gate"].level == st.BAD


@pytest.mark.parametrize("age,status,level", [(8, "ok", st.OK), (31, "ok", st.WARN), (61, "ok", st.BAD), (8, "error", st.WARN)])
def test_backfill_silence_and_errors_are_detected(age, status, level):
    deep = {"total_days": 805, "pending_days": 700, "pending_calls": 140_000,
            "last": {"status": status, "calls": 15_000, "error": "429 quota", "started_at": _iso(age)}}
    assert _by(st.build_checks(_facts(deep=deep)))["Broker history (K7)"].level == level


def test_backfill_before_the_first_run_is_information_and_complete_is_ok():
    fresh = {"total_days": 805, "pending_days": 805, "pending_calls": 165_000, "last": None}
    c = _by(st.build_checks(_facts(deep=fresh)))["Broker history (K7)"]
    assert c.level == st.OK and "not started yet" in c.text
    done = {"total_days": 805, "pending_days": 0, "pending_calls": 0, "last": None}
    assert "complete" in _by(st.build_checks(_facts(deep=done)))["Broker history (K7)"].text
    gone = {"total_days": 805, "pending_days": 700, "pending_calls": 100, "last": None}      # progress made, no run on record
    assert _by(st.build_checks(_facts(deep=gone)))["Broker history (K7)"].level == st.BAD


@pytest.mark.parametrize("days,level", [(1, st.OK), (10, st.WARN), (20, st.BAD)])
def test_backup_age(days, level):
    assert _by(st.build_checks(_facts(backup={"tag": "backup-x", "age_days": float(days)})))["Backup"].level == level
    assert _by(st.build_checks(_facts(backup={"tag": None, "age_days": 0.0})))["Backup"].level == st.WARN


def test_dashboard_disk_snapshots_and_collection_errors_become_visible_checks():
    c = _by(st.build_checks(_facts(dashboard=False, disk_free_pct=10.0, snapshots={"research_v1": _iso(24 * 12)},
                                   errors=["could not check backups: RuntimeError: gh failed"])))
    assert c["Dashboard"].level == st.WARN and c["Disk"].level == st.WARN and c["Edge analysis"].level == st.WARN
    assert c["Status check"].level == st.WARN and "gh failed" in c["Status check"].text
    assert _by(st.build_checks(_facts(disk_free_pct=3.0)))["Disk"].level == st.BAD


def test_headline_counts_problems_and_warnings():
    cs = [st.Check(st.BAD, "a", "x"), st.Check(st.WARN, "b", "y"), st.Check(st.WARN, "c", "z")]
    assert st.headline(cs) == (st.BAD, "1 problem, 2 warnings")
    assert st.headline(cs[1:]) == (st.WARN, "2 warnings") and st.headline(cs[1:2]) == (st.WARN, "1 warning")


def test_progress_numbers():
    p = st.deep_progress({"total_days": 805, "pending_days": 705, "pending_calls": 150_000})
    assert p["done_days"] == 100 and p["pct"] == 12 and p["nights"] == pytest.approx(10.0)
    assert st.deep_progress(None) is None and st.deep_progress({"total_days": 0, "pending_days": 0, "pending_calls": 0}) is None


def test_telegram_message_is_readable_escaped_and_has_the_progress_bar():
    f = _facts()
    level, text = st.telegram_text(f, st.build_checks(f), "morning")
    assert level == st.OK and text.startswith("✅ <b>Vektor Gauss — morning status</b> · Wed 4 Nov 08:00")
    for needle in ("All systems normal", "✓ EOD 3 Nov 2026", "Broker history (K7)", "█", "░", "700 of 805 days left", "~140,000 calls",
                   "Last run: 15,000 calls, ok, 8 h ago", "backup-2026-11-03", "Dashboard: answering on :8787", "Disk: 90% free"):
        assert needle in text, needle
    bad = _facts(errors=["could not check backups: <script>x</script>"])
    _l, t2 = st.telegram_text(bad, st.build_checks(bad), "evening")
    assert "<script>" not in t2 and "&lt;script&gt;" in t2 and "Needs attention" in t2 and t2.startswith("⚠️")


def test_only_ok_evening_and_backfill_reports_are_silent():
    assert st.should_be_silent(st.OK, "evening") and st.should_be_silent(st.OK, "backfill")
    assert not st.should_be_silent(st.OK, "morning")
    assert not st.should_be_silent(st.WARN, "evening") and not st.should_be_silent(st.BAD, "backfill")


def test_gather_never_raises_and_turns_failures_into_warnings(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(st, "_run_log_facts", boom)
    monkeypatch.setattr(st, "_deep_plan", boom)
    monkeypatch.setattr(st, "_backup_fact", boom)
    monkeypatch.setattr(st, "_dashboard_fact", lambda: False)
    monkeypatch.setattr(st, "load_snapshot", boom)
    pytest.importorskip("psycopg")
    from vg.web import freshness

    monkeypatch.setattr(freshness, "load_inputs", boom)
    f = st.gather(NOW)
    assert any("could not check run log" in e for e in f["errors"]) and any("could not check backups" in e for e in f["errors"])
    checks = st.build_checks(f)
    assert st.headline(checks)[0] == st.WARN and any(c.name == "Status check" for c in checks)


def test_scripts_are_wired_so_the_owner_hears_every_day():
    eod = (ROOT / "scripts" / "eod.sh").read_text(encoding="utf-8")
    assert "vg status --telegram --kind evening || true" in eod and eod.index("vg digest || true") < eod.index("vg status --telegram")
    deep = (ROOT / "scripts" / "deep_backfill.sh").read_text(encoding="utf-8")
    assert "vg status --telegram --kind backfill" in deep
    sh = (ROOT / "scripts" / "status.sh").read_text(encoding="utf-8")
    assert "vg status --telegram --kind" in sh and "VG_STATUS_HEALTHCHECK_URL" in sh and "exit $rc" in sh


def test_telegram_sender_supports_silent_delivery(monkeypatch):
    pytest.importorskip("httpx")
    from vg import telegram

    sent = []

    class _Resp:
        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json):
            sent.append(json)
            return _Resp()

    monkeypatch.setattr(telegram.httpx, "Client", _Client)
    s = telegram.TelegramSender(token="t", chat_id="c")
    s.send("hi", silent=True)
    s.send("hi")
    assert sent[0]["disable_notification"] is True and sent[1]["disable_notification"] is False


def test_log_deep_run_records_the_outcome(monkeypatch):
    pytest.importorskip("psycopg")
    rows = []

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            rows.append((sql, params))

        def cursor(self):
            return self

    from vg import db

    monkeypatch.setattr(db, "connect", lambda: _Cur())
    st.log_deep_run(15000, None)
    st.log_deep_run(0, "429")
    assert rows[0][1][1:] == ("ok", 15000, None) and rows[1][1][1:] == ("error", 0, "429")
    assert isinstance(rows[0][1][0], date)
