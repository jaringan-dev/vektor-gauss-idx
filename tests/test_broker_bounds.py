"""Certain bounds on a broker cohort's net (broker_bounds.py) + the ladder parser + immutable raw archive.

The numbers in test_bbca_style_* mirror the failure that motivated this: summing NET BUYERS only reported
foreign accumulation where the ladder proved net selling (BBCA 30 Oct 2026: recorded +16.6% of day value,
certain interval [-6.3%, -0.1%])."""

from datetime import date

import pytest

from vg import artifacts
from vg.broker_bounds import DEPTH, cohort_net_bounds
from vg.sources.vektorgauss import PARSER_VERSION, VgBrokerSummarySource

COHORT = {"F1", "F2", "F3"}


def _ladder(side, vals, prefix):
    return [(side, f"{prefix}{i}", v) for i, v in enumerate(vals)]


def test_none_without_a_ladder():
    assert cohort_net_bounds([("F1", 100, 10)], [], COHORT) is None


def test_exact_when_ladders_are_not_full():
    """Fewer than DEPTH rungs on a side = every active broker is visible, so nothing is hidden."""
    levels = [("B", "F1", 100.0), ("S", "F1", 30.0), ("S", "F2", 50.0), ("B", "X", 70.0)]
    b = cohort_net_bounds([], levels, COHORT)
    assert (b.lo, b.hi) == (100 - 30 - 50, 100 - 30 - 50)          # F1: +70, F2: -50, F3 absent = 0
    assert b.cut_buy == 0 and b.cut_sell == 0


def test_full_ladder_widens_by_the_cutoff_only_for_unseen_sides():
    buys = _ladder("B", [1000 - i for i in range(DEPTH)], "Z") + [("B", "F1", 500.0)]     # F1 visible on buy
    buys = buys[:DEPTH - 1] + [("B", "F1", 976.0)]                                       # keep it exactly DEPTH rungs
    sells = _ladder("S", [900 - i for i in range(DEPTH)], "Y")
    b = cohort_net_bounds([], buys + sells, COHORT)
    cut_b = min(v for s, _, v in buys if s == "B")
    cut_s = min(v for s, _, v in sells if s == "S")
    assert b.cut_buy == cut_b and b.cut_sell == cut_s
    # F1: buy known (976), sell unknown in [0, cut_s]; F2/F3: nothing known -> [-cut_s, +cut_b]
    assert b.lo == pytest.approx(976 - cut_s + 2 * (0 - cut_s))
    assert b.hi == pytest.approx(976 + 2 * cut_b)
    assert b.lo <= b.hi


def test_net_buyer_rows_fill_gaps_with_exact_values():
    levels = _ladder("B", [500.0 - i for i in range(DEPTH)], "Z") + _ladder("S", [400.0 - i for i in range(DEPTH)], "Y")
    only_ladder = cohort_net_bounds([], levels, {"F1"})
    with_row = cohort_net_bounds([("F1", 80.0, 60.0)], levels, {"F1"})        # exact: net +20
    assert (with_row.lo, with_row.hi) == (20.0, 20.0)
    assert only_ladder.width > with_row.width


def test_bbca_style_buyers_only_sum_can_have_the_wrong_sign():
    """Two foreign houses are the top net buyers (+60, +40 => recorded +100) but a third sells 160 gross
    and appears only on the sell ladder. Buyers-only says +100; the interval is entirely negative."""
    net_rows = [("F1", 100.0, 40.0), ("F2", 60.0, 20.0)]                                     # +60, +40
    levels = [("S", "F3", 160.0), ("B", "F1", 100.0), ("B", "F2", 60.0), ("S", "F1", 40.0), ("S", "F2", 20.0)]
    b = cohort_net_bounds(net_rows, levels, COHORT)
    recorded = sum(bv - sv for _, bv, sv in net_rows)
    assert recorded == 100.0
    assert b.hi < 0 < recorded                                                              # interval excludes the old number


# ---- ladder parser ----------------------------------------------------------------------

_RESP = (
    b'{"stock_code":"BBCA","brokers":[{"broker_code":"BK","bval":232,"sval":85,"nval":147,"bvol":1,"svol":1,"bfrq":1,"sfrq":1}],'
    b'"broker_levels":['
    b'{"buy":{"broker_code":"ZP","broker_name":"MAYBANK","bval":675,"bvol":131,"bfrq":13,"bavg":5138.5},'
    b'"sell":{"broker_code":"CC","broker_name":"MANDIRI","sval":702,"svol":137,"sfrq":11,"savg":5108.6}},'
    b'{"buy":{"broker_code":"BK","bval":485,"bvol":94,"bfrq":9,"bavg":5118.6}}]}'
)


def test_parse_levels_reads_both_sides_and_skips_a_missing_side():
    lv = VgBrokerSummarySource._parse_levels(_RESP)
    assert [(x.side, x.rank, x.broker_code) for x in lv] == [("B", 1, "ZP"), ("S", 1, "CC"), ("B", 2, "BK")]
    assert float(next(x for x in lv if x.side == "S").val) == 702.0
    assert len(lv) == 3                                  # rung 2 has no seller: skipped, never zero-filled


def test_parse_levels_empty_when_response_has_no_ladder():
    assert VgBrokerSummarySource._parse_levels(b'{"brokers":[]}') == []


def test_fetch_attaches_levels_and_parser_version_is_bumped():
    src = VgBrokerSummarySource()
    src._download = lambda ticker: _RESP
    res = src.fetch(date(2026, 10, 30), "bbca")
    assert res.status == "ok" and len(res.levels) == 3 and len(res.rows) == 1
    assert PARSER_VERSION == "vg-broker/2"


# ---- immutable raw archive (K1) ---------------------------------------------------------

def test_immutable_store_keeps_every_distinct_response(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts.CONFIG, "artifact_dir", tmp_path)
    d = date(2026, 10, 30)
    p1, s1, _ = artifacts.store("vektorgauss", d, b'{"v":1}', stem="BBCA", immutable=True)
    p2, s2, _ = artifacts.store("vektorgauss", d, b'{"v":2}', stem="BBCA", immutable=True)
    p3, s3, _ = artifacts.store("vektorgauss", d, b'{"v":1}', stem="BBCA", immutable=True)
    assert p1 != p2 and p1 == p3 and s1 == s3 != s2         # new content -> new file; same bytes dedupe
    assert p1.read_bytes() == b'{"v":1}' and p2.read_bytes() == b'{"v":2}'
    assert p1.parent.name == "BBCA" and p1.name.startswith(s1[:16])        # ticker stays in the path


def test_legacy_store_still_overwrites_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts.CONFIG, "artifact_dir", tmp_path)
    a, _, _ = artifacts.store("src", date(2026, 9, 3), b"one")
    b, _, _ = artifacts.store("src", date(2026, 9, 3), b"two")
    assert a == b and a.read_bytes() == b"two"            # unchanged behaviour for sources not yet migrated
