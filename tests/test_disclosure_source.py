"""Offline tests for the IDX ownership-disclosure filing source."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from vg.sources import idx
from vg.sources.disclosure import IdxDisclosureSource, _parse_party

FIXT = Path(__file__).parent / "fixtures" / "idx_disclosure_20260903.json"


@pytest.fixture
def json_bytes() -> bytes:
    return FIXT.read_bytes()


def test_parse_party():
    assert _parse_party(
        "LAPORAN ... SAHAM PERUSAHAAN TERBUKA - SAMUEL SEKURITAS INDONESIA - Group:-"
    ) == "SAMUEL SEKURITAS INDONESIA"
    assert _parse_party("LAPORAN ... TERBUKA - JIMMY TANDYO   - Group:-") == "JIMMY TANDYO"
    assert _parse_party(None) is None
    assert _parse_party("no dash here") is None


def test_parse_basic(json_bytes):
    rows = IdxDisclosureSource._parse(json_bytes)
    assert len(rows) == 5
    assert [r.ticker for r in rows] == ["AKRA", "NSSS", "FILM", "RGAS", "RGAS"]

    r0 = rows[0]
    assert r0.no_pengumuman == "LK/03092026/0011/1"
    assert r0.filed_at.isoformat() == "2026-09-03T21:18:25"
    assert r0.filed_date == date(2026, 9, 3)
    assert r0.party == "JIMMY TANDYO"
    assert r0.jenis == "STOCK"
    assert r0.attachments[0]["full_save_path"].endswith("LK-03092026-8380-00.pdf-0.pdf")
    assert r0.attachments[0]["original_filename"] == "LK-03092026-8380-00.pdf"
    assert r0.raw["pengumuman"]["Kode_Emiten"] == "AKRA"


def test_parse_skips_non_ownership_and_untickered():
    raw = (
        b'{"Replies":[{"pengumuman":{"Kode_Emiten":"","NoPengumuman":"X",'
        b'"TglPengumuman":"2026-09-03T10:00:00","JudulPengumuman":"LAPORAN KEPEMILIKAN"}},'
        b'{"pengumuman":{"Kode_Emiten":"BBCA","NoPengumuman":"Y",'
        b'"TglPengumuman":"2026-09-03T10:00:00","JudulPengumuman":"RUPS TAHUNAN"}}]}'
    )
    assert IdxDisclosureSource._parse(raw) == []


def test_fetch_injected(monkeypatch, json_bytes):
    src = IdxDisclosureSource()
    monkeypatch.setattr(src, "_download", lambda sd: json_bytes)
    res = src.fetch(date(2026, 9, 3))
    assert res.status == "ok"
    assert res.raw_ext == "json"
    assert res.session_date == date(2026, 9, 3)
    assert len(res.rows) == 5


def test_fetch_no_data(monkeypatch):
    src = IdxDisclosureSource()
    monkeypatch.setattr(src, "_download", lambda sd: b'{"ResultCount":0,"Replies":[]}')
    res = src.fetch(date(2026, 9, 3))
    assert res.status == "no_data"
    assert res.rows == []


def test_fetch_blocked(monkeypatch):
    src = IdxDisclosureSource()

    def boom(sd):
        raise idx.IdxAccessBlocked("1020")

    monkeypatch.setattr(src, "_download", boom)
    res = src.fetch(date(2026, 9, 3))
    assert res.status == "blocked"


def test_disclosure_columns_align(json_bytes):
    from vg.collect import _DISC_COLS, _disc_row

    rows = IdxDisclosureSource._parse(json_bytes)
    row = _disc_row(rows[0], None, "idx_disclosure", 0)
    assert len(row) == len(_DISC_COLS)
    m = dict(zip(_DISC_COLS, row))
    assert m["no_pengumuman"] == "LK/03092026/0011/1"
    assert m["ticker"] == "AKRA"
    assert m["source_tier"] == 0
    assert m["pdf_artifact_path"] is None
    assert m["pdf_url"].endswith(".pdf")
