"""Offline tests for digest / telegram formatting (no DB, no network)."""

from __future__ import annotations

from datetime import date

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
    monkeypatch.setattr(digest.CONFIG, "forward_since", None)
    monkeypatch.setattr(digest.engine, "eval_tags", lambda since=None: [])
    out = digest.build_weekly_eval()
    assert "EVAL MINGGUAN" in out
    assert "Belum ada" in out


def test_action_logic():
    assert digest._action("AKUM-ASING", set(), held=False) == "BUY"
    assert digest._action("AKUM-ASING", set(), held=True) == "ADD"
    assert digest._action("MARKUP", set(), held=True) == "HOLD"
    assert digest._action("MARKUP", set(), held=False) == "WATCH"
    assert digest._action("DISTRIBUSI-ASING", set(), held=True) == "REDUCE/SELL"
    assert digest._action("DISTRIBUSI-ASING", set(), held=False) == "AVOID"
    assert digest._action(None, {"KSEI-DIVERGEN"}, held=True) == "HATI-HATI"
    assert digest._action(None, set(), held=False) == "WATCH"


def test_trade_line_buy(monkeypatch):
    monkeypatch.setattr(digest.CONFIG, "digest_trade_calls", True)
    monkeypatch.setattr(digest.CONFIG, "atr_sl_mult", 2.0)
    monkeypatch.setattr(digest.CONFIG, "target_r", 2.5)
    monkeypatch.setattr(digest.CONFIG, "account_balance", 0.0)
    monkeypatch.setattr(digest.CONFIG, "fee_pct", 0.0)
    monkeypatch.setattr(digest.CONFIG, "risk_pct", 0.01)

    line = digest._trade_line(3390.0, 105.0, "AKUM-ASING", set(), held=False)
    assert "aksi <b>BUY</b>" in line
    assert "SL 3.180" in line             # 3390 - 2*105
    assert "R:R 2,5" in line
    assert "mekanis, bukan prediksi" in line


def test_trade_line_disabled(monkeypatch):
    monkeypatch.setattr(digest.CONFIG, "digest_trade_calls", False)
    assert digest._trade_line(3390.0, 105.0, "AKUM-ASING", set(), held=False) is None


def _fake_stats(tag="AKUM-ASING", n=63):
    return [
        {"tag": tag, "n": n, "avg_h5": 0.018, "edge_h1": 0.001,
         "edge_h5": 0.007, "edge_h10": 0.011, "hit_rate": 0.58, "univ_hit_rate": 0.40,
         "n_liquid": 40, "edge_h5_liquid": 0.009, "hit_rate_liquid": 0.55},
    ]


def test_weekly_eval_table(monkeypatch):
    monkeypatch.setattr(digest.CONFIG, "forward_since", None)
    monkeypatch.setattr(digest.engine, "eval_tags", lambda since=None: _fake_stats())
    out = digest.build_weekly_eval()
    assert "<pre>" in out and "AKUM-ASING" in out
    assert "63" in out
    assert out.count("<pre>") == 1        # in-sample only, no forward block


def test_weekly_eval_forward_block(monkeypatch):
    monkeypatch.setattr(digest.CONFIG, "forward_since", date(2026, 9, 4))
    seen = []

    def fake(since=None):
        seen.append(since)
        return _fake_stats("FLOAT-ASING-PADAT", 12)

    monkeypatch.setattr(digest.engine, "eval_tags", fake)
    out = digest.build_weekly_eval()
    assert seen == [None, date(2026, 9, 4)]          # in-sample call, then forward call
    assert "in-sample" in out and "forward (sejak 2026-09-04)" in out
    assert out.count("<pre>") == 2
