"""Offline tests for the raw-artifact store."""

from __future__ import annotations

from datetime import date

from vg import artifacts


def test_store_default_stem_is_iso_date(monkeypatch, tmp_path):
    monkeypatch.setattr(artifacts.CONFIG, "artifact_dir", tmp_path)
    path, sha, n = artifacts.store("src", date(2026, 9, 3), b"hello", ext="json")
    assert path == tmp_path / "src" / "2026-09-03.json"
    assert path.read_bytes() == b"hello"
    assert n == 5 and len(sha) == 64


def test_store_stem_override_allows_many_per_date(monkeypatch, tmp_path):
    monkeypatch.setattr(artifacts.CONFIG, "artifact_dir", tmp_path)
    p1, _, _ = artifacts.store("pdf", date(2026, 9, 3), b"a", ext="pdf", stem="LK-1")
    p2, _, _ = artifacts.store("pdf", date(2026, 9, 3), b"b", ext="pdf", stem="LK-2")
    assert p1.name == "LK-1.pdf" and p2.name == "LK-2.pdf"
    assert p1.parent == tmp_path / "pdf"
