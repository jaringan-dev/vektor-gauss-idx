"""Offline tests for Config.load() env parsing (no DB, no network)."""

from __future__ import annotations

from datetime import date

from vg.config import DEFAULT_RADAR_TAGS, Config

_KEYS = ("VG_RADAR_TAGS", "VG_FORWARD_SINCE", "VG_WATCHLIST", "VG_HOLDINGS")


def _load(monkeypatch, **env) -> Config:
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Config.load()


def test_radar_tags_default_when_unset(monkeypatch):
    assert _load(monkeypatch).radar_tags == DEFAULT_RADAR_TAGS


def test_radar_tags_blank_falls_back_to_default(monkeypatch):
    assert _load(monkeypatch, VG_RADAR_TAGS="   ").radar_tags == DEFAULT_RADAR_TAGS


def test_radar_tags_custom_csv_is_upper_stripped(monkeypatch):
    assert _load(monkeypatch, VG_RADAR_TAGS="akum-asing, markup ").radar_tags == (
        "AKUM-ASING",
        "MARKUP",
    )


def test_forward_since_none_when_unset(monkeypatch):
    assert _load(monkeypatch).forward_since is None


def test_forward_since_parses_iso_date(monkeypatch):
    assert _load(monkeypatch, VG_FORWARD_SINCE="2026-09-04").forward_since == date(2026, 9, 4)


def test_forward_since_garbage_is_none(monkeypatch):
    assert _load(monkeypatch, VG_FORWARD_SINCE="not-a-date").forward_since is None
