"""Broker scoring wired into the daily compute: stored in signal_components, tagged at weight 0, and the
AKUM-NETRAL-WASH overlap filter (no DB, no network)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from vg import engine
from vg.engine import Components, _classify
from vg.engine import scoring as sc

ROOT = Path(__file__).resolve().parent.parent
DAY = date(2026, 11, 5)

NEW_COLS = (
    "bpi3", "bpi5", "bpi_overlap3", "aggr_haka_share", "aggr_ratio", "aggr_coverage",
    "retail_net_lo", "retail_net_hi", "finst_net_lo", "finst_net_hi", "retail_div_state",
)


# ---- AKUM-NETRAL-WASH --------------------------------------------------------------------------------------------

def _tags(**kw):
    return _classify(Components(ticker="AAAA", **kw))


def test_wash_tag_fires_when_two_top_buyers_are_also_top_sellers_and_bpi_is_high():
    assert "AKUM-NETRAL-WASH" in _tags(bpi3=0.15, bpi_overlap3=2)
    assert "AKUM-NETRAL-WASH" in _tags(bpi3=0.40, bpi_overlap3=3)


def test_wash_tag_boundaries_are_inclusive_at_both_thresholds():
    assert engine.WASH_OVERLAP_MIN == 2 and engine.BPI_HIGH == 0.10
    assert "AKUM-NETRAL-WASH" in _tags(bpi3=engine.BPI_HIGH, bpi_overlap3=engine.WASH_OVERLAP_MIN)


@pytest.mark.parametrize("bpi3, overlap", [
    (0.15, 1),        # concentrated but only one shared house: not wash
    (0.15, 0),
    (0.099, 2),       # overlapping but not concentrated
    (-0.30, 3),       # overlapping and SELL-side concentrated: not an accumulation look-alike
    (None, 2),        # no ladder that day
    (0.15, None),
])
def test_wash_tag_stays_silent_otherwise(bpi3, overlap):
    assert "AKUM-NETRAL-WASH" not in _tags(bpi3=bpi3, bpi_overlap3=overlap)


def test_wash_tag_needs_no_price_history_and_adds_nothing_else():
    base = Components(ticker="AAAA")                       # no price/NFF history at all
    plain = _classify(base)
    flagged = _classify(Components(ticker="AAAA", bpi3=0.2, bpi_overlap3=2))
    assert flagged == plain + ["AKUM-NETRAL-WASH"]         # it neither suppresses nor creates another tag


def test_wash_tag_leaves_other_broker_tags_alone():
    cands = {"bkr_cr1": 0.5, "bkr_top_buyer_ticket": 60_000_000, "day_value": 1e9, "price_chg_5d": 0.0, "nff_w1": 1.0}
    without = _tags(**cands)
    with_wash = _tags(bpi3=0.2, bpi_overlap3=2, **cands)
    assert "BANDAR-CANDIDATE" in without and "BANDAR-CANDIDATE" in with_wash        # unchanged (not suppressed)
    assert [t for t in with_wash if t != "AKUM-NETRAL-WASH"] == without


# ---- copying scores onto the components ----------------------------------------------------------------------------

def _score(**kw) -> sc.TickerScore:
    return sc.TickerScore(ticker="AAAA", session_date=DAY, **kw)


def test_apply_scoring_copies_every_measure():
    bandar = sc.bandar_power([("A", 30.0), ("B", 20.0), ("C", 10.0)], [("A", 20.0), ("B", 15.0), ("X", 5.0)], 100.0)
    aggr = sc.aggressor_ratio([("RG", 150, 80, 50)])
    net_rows = [("YP", 60, 20), ("AK", 10, 40), ("ZZ", 30, 40)]
    levels = [("B", c, b) for c, b, _ in net_rows] + [("S", c, s) for c, _, s in net_rows]
    div = sc.retail_divergence(net_rows, levels, 100.0)
    c = Components(ticker="AAAA")
    engine._apply_scoring(c, _score(bandar=bandar, aggressor=aggr, divergence=div))
    assert (c.bpi3, c.bpi_overlap3) == (pytest.approx(0.2), 2) and c.bpi5 == pytest.approx(bandar.bpi5)
    assert c.aggr_haka_share == pytest.approx(80 / 130) and c.aggr_ratio == pytest.approx(1.6)
    assert c.aggr_coverage == pytest.approx(130 / 150)
    assert (c.retail_net_lo, c.retail_net_hi) == (40, 40) and (c.finst_net_lo, c.finst_net_hi) == (-30, -30)
    assert c.retail_div_state == "retail_buying_institutions_selling"


def test_apply_scoring_leaves_missing_measures_none():
    c = Components(ticker="AAAA")
    engine._apply_scoring(c, _score())
    assert all(getattr(c, col) is None for col in NEW_COLS)


def test_new_columns_are_persisted_in_the_same_order_they_are_declared():
    c = Components(ticker="AAAA")
    for i, col in enumerate(NEW_COLS, start=1):
        setattr(c, col, "indeterminate" if col == "retail_div_state" else float(i))
    row = dict(zip(engine._COMP_COLS, engine._comp_row(DAY, c), strict=True))
    for i, col in enumerate(NEW_COLS, start=1):
        assert row[col] == ("indeterminate" if col == "retail_div_state" else float(i)), col


# ---- schema: additive and nullable only ----------------------------------------------------------------------------

def test_schema_adds_the_columns_additively_and_nullable():
    sql = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    for col in NEW_COLS:
        m = re.search(rf"ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS {col}\s+(\w+)([^;]*);", sql)
        assert m, f"{col} missing from schema.sql"
        assert "NOT NULL" not in m.group(2).upper() and "DEFAULT" not in m.group(2).upper(), col
    # nothing destructive was introduced for this table
    assert not re.search(r"ALTER TABLE signal_components\s+(DROP|ALTER COLUMN|RENAME)", sql, re.IGNORECASE)


# ---- compute_day end to end over fakes ------------------------------------------------------------------------------

class _Cur:
    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return None

    def fetchall(self):
        return []


def test_compute_day_stores_scores_and_logs_the_wash_tag_as_probation(monkeypatch):
    class _Conn:
        def cursor(self):
            return self

        def __enter__(self):
            return _Cur()

        def __exit__(self, *a):
            return False

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *a):
            return False

    bandar = sc.bandar_power([("A", 30.0), ("B", 20.0), ("C", 10.0)], [("A", 20.0), ("B", 15.0), ("X", 5.0)], 100.0)
    scored: list[str] = []

    monkeypatch.setattr(engine, "connect", lambda: _Ctx())
    monkeypatch.setattr(engine, "_calendar", lambda cur, sd, n: [sd])
    monkeypatch.setattr(engine, "_ihsg_series", lambda cur, cal: {})
    monkeypatch.setattr(engine, "_disclosure_counts", lambda cur, sd, cal: {})
    monkeypatch.setattr(engine, "_disclosure_direction", lambda cur, sd: {})
    monkeypatch.setattr(engine, "_tickers_on", lambda cur, sd: ["AAAA", "BBBB"])
    monkeypatch.setattr(engine, "_bars", lambda cur, t, cal: {})
    monkeypatch.setattr(engine, "_share_changes", lambda cur, t, lo, hi: ([], False))
    monkeypatch.setattr(engine, "_components", lambda t, cal, bars, ihsg, splits, ch: Components(ticker=t, close=100.0))
    monkeypatch.setattr(engine, "_ksei_metrics", lambda cur, t, sd: engine._KseiSnap())
    monkeypatch.setattr(engine, "_broker_metrics", lambda cur, t, cal: engine._BrokerSnap(has_d0=(t == "AAAA")))

    def fake_score(cur, t, sd):
        scored.append(t)
        return sc.TickerScore(ticker=t, session_date=sd, bandar=bandar)

    monkeypatch.setattr(engine, "score_ticker", fake_score)
    calls = {}
    monkeypatch.setattr(
        engine, "upsert_many",
        lambda cur, table, cols, rows, **kw: calls.setdefault(table, (cols, rows)) and len(rows))

    engine.compute_day(DAY)

    assert scored == ["AAAA"]                                  # only a ticker the feed covered today is scored
    cols, rows = calls["signal_components"]
    by_ticker = {r[1]: dict(zip(cols, r, strict=True)) for r in rows}
    assert by_ticker["AAAA"]["bpi3"] == pytest.approx(0.2) and by_ticker["AAAA"]["bpi_overlap3"] == 2
    assert by_ticker["BBBB"]["bpi3"] is None
    _log_cols, log_rows = calls["signal_log"]
    assert [(r[1], r[2], r[3]) for r in log_rows] == [("AAAA", "AKUM-NETRAL-WASH", "probation")]
