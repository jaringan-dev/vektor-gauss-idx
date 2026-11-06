"""The v2 tags' new forward clock (audit 2026-11-07): compute emits them only from V2_TAGS_SINCE on, and their in-sample history
comes only from backfill-tags-v2, which refuses the forward window and never updates a row."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from vg import engine
from vg.engine import Components

ROOT = Path(__file__).resolve().parents[1]


class _Cur:
    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return None

    def fetchall(self):
        return []


def _run_compute(monkeypatch, day):
    """compute_day over fakes, with _classify forced to return one v1 and two v2 tags; returns the tags it would log."""
    monkeypatch.setattr(engine, "connect", lambda: _ConnCtx())
    for name, fn in {
        "_calendar": lambda cur, sd, n: [sd], "_ihsg_series": lambda cur, cal: {}, "_disclosure_counts": lambda cur, sd, cal: {},
        "_disclosure_direction": lambda cur, sd: {}, "_tickers_on": lambda cur, sd: ["AAAA"], "_bars": lambda cur, t, cal: {},
        "_share_changes": lambda cur, t, lo, hi: ([], False),
        "_components": lambda t, cal, bars, ihsg, sp, ch: Components(ticker=t, close=100.0),
        "_ksei_metrics": lambda cur, t, sd: engine._KseiSnap(), "_ksei_metrics2": lambda cur, t, sd: ("nodata", None, None),
        "_broker_metrics": lambda cur, t, cal: engine._BrokerSnap(),
        "_classify": lambda c: ["MARKUP", "FOREIGN-INST2", "KSEI2-INST-AKUM"],
    }.items():
        monkeypatch.setattr(engine, name, fn)
    calls = {}
    monkeypatch.setattr(engine, "upsert_many", lambda cur, table, cols, rows, **kw: calls.setdefault(table, rows) and len(rows))
    engine.compute_day(day)
    return {r[2] for r in calls.get("signal_log", [])}


class _ConnCtx:
    def __enter__(self):
        return _Conn()

    def __exit__(self, *a):
        return False


class _Conn:
    def cursor(self):
        return self

    def __enter__(self):
        return _Cur()

    def __exit__(self, *a):
        return False


def test_compute_emits_v2_tags_only_from_the_new_clock(monkeypatch):
    before = _run_compute(monkeypatch, date(2026, 11, 6))                 # a nightly recompute of a pre-clock session
    assert before == {"MARKUP"}
    after = _run_compute(monkeypatch, engine.V2_TAGS_SINCE)
    assert after == {"MARKUP", "FOREIGN-INST2", "KSEI2-INST-AKUM"}


def test_backfill_refuses_the_forward_window(monkeypatch):
    from vg.config import CONFIG

    monkeypatch.setattr(CONFIG, "forward_since", date(2026, 9, 4), raising=False)
    with pytest.raises(engine.WashBackfillRefused):
        engine.backfill_tags_v2(end=date(2026, 9, 4))
    monkeypatch.setattr(CONFIG, "forward_since", None, raising=False)
    with pytest.raises(engine.WashBackfillRefused):
        engine.backfill_tags_v2()


def test_v2_tags_are_exactly_the_new_tags_the_engine_emits():
    src = (ROOT / "src" / "vg" / "engine" / "__init__.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'tags\.append\("([A-Z0-9-]+)"\)', src))
    new = {t for t in emitted if re.match(r"(KSEI2|FLOAT2|FOREIGN-INST2|DOM-RETAIL2|BROKER2-DOMISILI)", t)}
    assert new == set(engine.V2_TAGS)


def test_backfill_inserts_without_updating_any_row():
    src = (ROOT / "src" / "vg" / "engine" / "__init__.py").read_text(encoding="utf-8")
    body = src[src.index("def backfill_tags_v2"):src.index("def _log(")]
    assert 'conflict=("session_date", "ticker", "tag"))' in body and "update=" not in body
