"""Offline tests for digest / telegram formatting (no DB, no network)."""

from __future__ import annotations

from vg import digest
from vg.telegram import _split


def test_split_short():
    assert _split("hello") == ["hello"]


def test_split_on_blank_lines():
    para = "x" * 2000
    chunks = _split(f"{para}\n\n{para}\n\n{para}", limit=2500)
    assert len(chunks) == 3
    assert all(len(c) <= 2500 for c in chunks)


def test_split_hard_split_long_paragraph():
    lines = "\n".join("line" + str(i) for i in range(2000))
    chunks = _split(lines, limit=500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)


def test_idr():
    assert digest._idr(45_200_000_000) == "+45,2B"
    assert digest._idr(-1_300_000_000_000) == "-1,3T"
    assert digest._idr(None) == "n/a"


def test_pct():
    assert digest._pct(0.003) == "+0,3%"
    assert digest._pct(-0.052, 2) == "-5,20%"
    assert digest._pct(None) == "n/a"


def test_price_indonesian_thousands():
    assert digest._price(3390) == "3.390"
    assert digest._price(10650) == "10.650"


def test_primary_tag_priority():
    assert digest._primary({"AKUM-ASING", "CROSSING-TINGGI"}) == "AKUM-ASING"
    assert digest._primary({"MARKUP", "MARKDOWN"}) == "MARKUP"
    assert digest._primary({"CROSSING-TINGGI"}) is None


def test_weekly_eval_empty(monkeypatch):
    monkeypatch.setattr(digest.engine, "eval_tags", lambda: [])
    out = digest.build_weekly_eval()
    assert "EVAL MINGGUAN" in out
    assert "Belum ada" in out


def test_weekly_eval_table(monkeypatch):
    monkeypatch.setattr(
        digest.engine, "eval_tags",
        lambda: [
            {"tag": "AKUM-ASING", "n": 63, "avg_h5": 0.018, "avg_vs_ihsg_h5": 0.007,
             "hit_rate": 0.58, "avg_h1": 0.003, "avg_h10": 0.024},
        ],
    )
    out = digest.build_weekly_eval()
    assert "<pre>" in out and "AKUM-ASING" in out
    assert "63" in out
