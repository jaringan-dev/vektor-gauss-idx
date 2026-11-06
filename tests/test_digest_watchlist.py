"""The Telegram digest's WATCHLIST section follows the list the user keeps on /today, not VG_WATCHLIST."""
from __future__ import annotations

from vg import digest
from vg.web import watchlist as wl


def test_manual_list_is_what_the_table_holds(monkeypatch):
    monkeypatch.setattr(wl, "load", lambda: ["BBCA", "TLKM"])
    assert digest.manual_watchlist() == ["BBCA", "TLKM"]


def test_empty_table_means_empty_list_not_env_fallback(monkeypatch):
    monkeypatch.setattr(wl, "load", lambda: [])
    monkeypatch.setattr(digest.CONFIG.__class__, "watchlist", ["BBRI"], raising=False)
    assert digest.manual_watchlist() == []


def test_unreadable_table_does_not_crash_the_digest(monkeypatch):
    def boom():
        raise RuntimeError('relation "user_watchlist" does not exist')

    monkeypatch.setattr(wl, "load", boom)
    assert digest.manual_watchlist() == []
