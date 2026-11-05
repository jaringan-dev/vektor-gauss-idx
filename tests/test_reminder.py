"""`vg remind-verify`: the one-off post-EOD checklist on Telegram (no DB, no network)."""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from vg import collect, engine, reminder

ROOT = Path(__file__).resolve().parent.parent
WATCH = ("BBRI", "BBCA", "ADRO", "ANTM", "TLKM")


# ---- the message ---------------------------------------------------------------------------------------------------

def test_message_has_the_four_checks_the_owner_asked_for():
    m = reminder.build_message(WATCH, 15)
    assert "PENGINGAT VERIFIKASI EOD VEKTOR GAUSS" in m
    assert "Proses EOD &amp; Init-DB telah selesai. Silakan periksa:" in m
    assert "1. <code>eod.log</code> &amp; <code>run_log</code>" in m and "<code>schema:</code>" in m
    assert "2. Tabel <code>signal_components</code> — cek keterisian 11 kolom baru" in m
    assert "<code>AKUM-NETRAL-WASH</code>" in m
    assert "3. Tabel <code>done_by_price</code>" in m and "5 saham watchlist (BBRI, BBCA, ADRO, ANTM, TLKM)" in m
    assert "4. Durasi eksekusi order flow — pastikan muat dalam anggaran 15 menit." in m


def test_message_is_valid_telegram_html():
    m = reminder.build_message(WATCH, 15)
    assert "&" not in re.sub(r"&amp;", "", m)                    # every ampersand is escaped
    assert m.count("<b>") == m.count("</b>") == 1
    assert m.count("<code>") == m.count("</code>")
    assert len(m) < 3800                                         # one Telegram message, no splitting


def test_message_follows_the_live_configuration_not_a_hardcoded_list():
    m = reminder.build_message(("BBCA", "TLKM", "GOTO"), 7.5)
    assert "3 saham watchlist (BBCA, TLKM, GOTO)" in m and "anggaran 7.5 menit" in m
    assert "0 saham watchlist (belum diatur di VG_WATCHLIST)" in reminder.build_message((), 15)
    assert "tidak dibatasi" in reminder.build_message(WATCH, 0)


def test_message_escapes_what_it_interpolates():
    assert "&lt;b&gt;" in reminder.build_message(("<b>",), 15)


def test_the_column_count_in_the_message_is_the_real_one():
    sql = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    assert len(reminder.SCORING_COLUMNS) == 11
    for col in reminder.SCORING_COLUMNS:
        assert f"ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS {col} " in sql, col
        assert col in engine._COMP_COLS, col


# ---- sending policy ------------------------------------------------------------------------------------------------

class _Sender:
    def __init__(self, configured=True, fail=False):
        self.configured, self.fail, self.sent = configured, fail, []

    def send(self, text, **kw):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append((text, kw))
        return 1


class _Db:
    """Scripted run_log / information_schema answers, and a record of every statement."""

    def __init__(self, sent_before=False, compute_ok=True, columns=11, broken=False):
        self.sent_before, self.compute_ok, self.columns, self.broken = sent_before, compute_ok, columns, broken
        self.executed: list[tuple[str, tuple | None]] = []
        self._last = ""

    def execute(self, sql, params=None):
        if self.broken:
            raise OSError("db down")
        self.executed.append((sql, params))
        self._last = sql

    def fetchone(self):
        if "phase = %s AND status = 'ok'" in self._last:
            return (1,) if self.sent_before else None
        if "phase = 'compute'" in self._last:
            return (1,) if self.compute_ok else None
        if "information_schema" in self._last:
            return (self.columns,)
        raise AssertionError(self._last)

    @property
    def inserts(self):
        return [p for s, p in self.executed if s.lstrip().upper().startswith("INSERT")]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(collect.CONFIG, "watchlist", WATCH)
    monkeypatch.setattr(collect.CONFIG, "holdings", ())
    monkeypatch.setattr(collect.CONFIG, "lq45_tickers", ())
    monkeypatch.setattr(collect.CONFIG, "done_budget_minutes", 15.0)

    def use(db):
        @contextmanager
        def connect():
            class Conn:
                def cursor(self):
                    return self

                def __enter__(self):
                    return db

                def __exit__(self, *a):
                    return False

            yield Conn()

        monkeypatch.setattr(reminder, "connect", connect)
        return db

    return use


def test_first_healthy_eod_sends_the_checklist_and_records_it(env):
    db, s = env(_Db()), _Sender()
    assert reminder.run(sender=s) == 0
    assert len(s.sent) == 1 and "PENGINGAT VERIFIKASI" in s.sent[0][0]
    assert s.sent[0][1] == {}                                  # default HTML, with notification sound
    assert len(db.inserts) == 1 and db.inserts[0][0] == reminder.PHASE


def test_it_is_sent_only_once(env, capsys):
    db, s = env(_Db(sent_before=True)), _Sender()
    assert reminder.run(sender=s) == 0
    assert s.sent == [] and db.inserts == []
    assert "already sent" in capsys.readouterr().out


@pytest.mark.parametrize("db, why", [
    (_Db(compute_ok=False), "no successful compute"),
    (_Db(columns=0), "0/11 scoring columns"),
    (_Db(columns=7), "7/11 scoring columns"),
])
def test_it_waits_for_a_healthy_eod_with_the_migration_applied(env, capsys, db, why):
    env(db)
    s = _Sender()
    assert reminder.run(sender=s) == 0                        # not an error: it will try again tomorrow
    assert s.sent == [] and db.inserts == []
    assert why in capsys.readouterr().out


def test_force_sends_regardless_and_does_not_use_up_the_one_off(env):
    db, s = env(_Db(sent_before=True, compute_ok=False, columns=0)), _Sender()
    assert reminder.run(force=True, sender=s) == 0
    assert len(s.sent) == 1 and db.inserts == [] and db.executed == []


def test_dry_run_prints_and_touches_nothing(env, capsys):
    _db, s = env(_Db(broken=True)), _Sender()                 # a dead database must not matter
    assert reminder.run(dry_run=True, sender=s) == 0
    out = capsys.readouterr().out
    assert "PENGINGAT VERIFIKASI EOD VEKTOR GAUSS" in out and "<b>" not in out and "&amp;" not in out
    assert "Proses EOD & Init-DB telah selesai" in out and s.sent == []


def test_missing_telegram_credentials_are_reported_not_raised(env, capsys):
    db, s = env(_Db()), _Sender(configured=False)
    assert reminder.run(sender=s) == 2
    assert "not configured" in capsys.readouterr().out and db.executed == []


def test_a_failed_send_writes_no_marker_so_tomorrow_retries(env, capsys):
    db, s = env(_Db()), _Sender(fail=True)
    assert reminder.run(sender=s) == 2
    assert db.inserts == [] and "TELEGRAM SEND FAILED" in capsys.readouterr().out


def test_when_the_database_cannot_be_read_it_does_not_risk_a_duplicate(env, capsys):
    env(_Db(broken=True))
    s = _Sender()
    assert reminder.run(sender=s) == 2
    assert s.sent == [] and "database check failed" in capsys.readouterr().out


# ---- CLI + eod.sh ----------------------------------------------------------------------------------------------------

def test_cli_dry_run(env, capsys):
    from vg.__main__ import main

    env(_Db(broken=True))
    assert main(["remind-verify", "--dry-run"]) == 0
    assert "PENGINGAT VERIFIKASI" in capsys.readouterr().out


def test_cli_passes_the_flags_through(monkeypatch):
    from vg.__main__ import main

    got = {}
    monkeypatch.setattr(reminder, "run", lambda **k: got.update(k) or 0)
    assert main(["remind-verify", "--force"]) == 0 and got == {"force": True, "dry_run": False}


def _eod():
    return [ln.strip() for ln in (ROOT / "scripts" / "eod.sh").read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def test_eod_sends_the_reminder_last_after_digest_and_status_and_never_fails_the_run():
    lines = _eod()
    digest = lines.index("vg digest || true")                  # the daily digest line is untouched
    status = next(i for i, ln in enumerate(lines) if ln.startswith("vg status --telegram"))
    remind = [i for i, ln in enumerate(lines) if "remind-verify" in ln]
    assert len(remind) == 1 and lines[remind[0]] == "vg remind-verify || true"
    assert digest < status < remind[0]
    hc = next(i for i, ln in enumerate(lines) if "VG_HEALTHCHECK_URL" in ln)
    assert remind[0] < hc                                      # before the dead-man's-switch ping


def test_eod_logs_how_long_the_order_flow_harvest_took():
    text = (ROOT / "scripts" / "eod.sh").read_text(encoding="utf-8")
    assert "T_DONE=$SECONDS" in text and "order-flow harvest took $((SECONDS - T_DONE))s" in text
    assert b"\r" not in (ROOT / "scripts" / "eod.sh").read_bytes()
