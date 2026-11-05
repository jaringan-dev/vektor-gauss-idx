"""vg.engine.scoring: Bandar Power Index, Aggressor Ratio, Retail Net-Flow Divergence (no DB, no network).
All expected numbers are worked out by hand in the comments."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("psycopg")

from vg.engine import scoring as sc
from vg.sources.vektorgauss import FOREIGN_BROKERS, RETAIL_BROKERS

DAY = date(2026, 11, 5)


# ---- 1. Bandar Power Index ---------------------------------------------------------------------------------------

BUYS = [("A", 40.0), ("B", 30.0), ("C", 10.0), ("D", 5.0), ("E", 5.0), ("F", 1.0)]
SELLS = [("X", 30.0), ("Y", 20.0), ("Z", 10.0), ("W", 5.0)]


def test_bandar_power_shares_and_index():
    b = sc.bandar_power(BUYS, SELLS, 100.0)
    # top-3 buy = 40+30+10 = 80 -> 0.80 ; top-5 buy = 90 -> 0.90
    # top-3 sell = 30+20+10 = 60 -> 0.60 ; top-5 sell = all 4 = 65 -> 0.65
    assert (b.cr3_buy, b.cr5_buy, b.cr3_sell, b.cr5_sell) == pytest.approx((0.80, 0.90, 0.60, 0.65))
    assert (b.bpi3, b.bpi5) == pytest.approx((0.20, 0.25))
    assert (b.top_buyer, b.top_seller, b.overlap3) == ("A", "X", 0)
    assert (b.n_buy_rungs, b.n_sell_rungs) == (6, 4)


def test_bandar_power_is_order_independent_and_ignores_empty_values():
    shuffled = [BUYS[3], BUYS[0], ("G", None), BUYS[5], BUYS[2], BUYS[1], BUYS[4], ("H", 0.0)]
    assert sc.bandar_power(shuffled, SELLS, 100.0) == sc.bandar_power(BUYS, SELLS, 100.0)


def test_bandar_power_overlap_flags_the_same_house_on_both_sides():
    sells = [("A", 35.0), ("Y", 20.0), ("Z", 10.0)]          # A is also the largest seller
    b = sc.bandar_power(BUYS, sells, 100.0)
    assert b.overlap3 == 1 and b.top_seller == "A"


def test_bandar_power_negative_when_selling_is_more_concentrated():
    b = sc.bandar_power([("A", 10.0), ("B", 10.0)], [("X", 50.0), ("Y", 30.0)], 100.0)
    assert b.bpi3 == pytest.approx(0.20 - 0.80)


@pytest.mark.parametrize("buys, sells, dv", [
    ([], SELLS, 100.0),                          # no buy ladder
    (BUYS, [], 100.0),                           # no sell ladder
    (BUYS, SELLS, None),                         # no day value
    (BUYS, SELLS, 0.0),
    (BUYS, SELLS, 50.0),                         # top-3 buy = 80 > 50: units disagree -> refuse, do not guess
])
def test_bandar_power_none_when_it_cannot_be_trusted(buys, sells, dv):
    assert sc.bandar_power(buys, sells, dv) is None


def test_net_buyer_concentration():
    n = sc.net_buyer_concentration([50, 30, 10, 5, 5, -20, None, 0])
    # net buyers 50,30,10,5,5 = 100 total
    assert (n.cr1, n.cr3, n.cr5) == pytest.approx((0.5, 0.9, 1.0))
    assert n.n_net_buyers == 5 and n.total_net_buying == 100
    assert sc.net_buyer_concentration([-5, None, 0]) is None


# ---- 2. Aggressor Ratio ------------------------------------------------------------------------------------------

DONE = [("RG", 100, 60, 30), ("RG", 50, 20, 20), ("NG", 1000, 1000, 0)]


def test_aggressor_ratio_defaults_to_the_regular_board_only():
    a = sc.aggressor_ratio(DONE)
    # RG: qty 150, haka 80, haki 50, unclassified 150-130 = 20
    assert (a.qty, a.haka_qty, a.haki_qty, a.unclassified_qty) == (150, 80, 50, 20)
    assert a.haka_share == pytest.approx(80 / 130) and a.ratio == pytest.approx(1.6)
    assert a.coverage == pytest.approx(130 / 150) and a.price_levels == 2 and a.boards == ("RG",)


def test_aggressor_ratio_negotiated_board_is_excluded_unless_asked():
    both = sc.aggressor_ratio(DONE, boards=("RG", "NG"))
    assert both.haka_qty == 1080 and both.haki_qty == 50      # the NG "all BUY" flag would swamp the signal


def test_aggressor_ratio_handles_one_sided_and_missing_flags():
    only_haka = sc.aggressor_ratio([("RG", 10, 10, 0)])
    assert only_haka.haka_share == 1.0 and only_haka.ratio is None       # haki == 0: ratio undefined, share is not
    unflagged = sc.aggressor_ratio([("RG", 10, None, None)])
    assert unflagged.haka_share is None and unflagged.ratio is None and unflagged.coverage == 0.0


@pytest.mark.parametrize("rows", [[], [("NG", 5, 5, 0)], [("RG", 0, 0, 0)]])
def test_aggressor_ratio_none_without_regular_volume(rows):
    assert sc.aggressor_ratio(rows) is None


# ---- 3. Retail Net-Flow Divergence --------------------------------------------------------------------------------

RETAIL = next(iter(sorted(RETAIL_BROKERS)))                      # a retail-flagged house, e.g. YP
INST = next(iter(sorted(FOREIGN_BROKERS - RETAIL_BROKERS)))      # a foreign institution, e.g. AK
OTHER = "ZZ"


def _scenario(retail, inst, other):
    """Each arg is (buy, sell). The ladders list every broker (fewer than 25 rungs per side), so the bounds are
    exact. Returns (net_rows, levels) in the shapes broker_flows / broker_levels give."""
    book = {RETAIL: retail, INST: inst, OTHER: other}
    assert sum(b for b, _ in book.values()) == sum(s for _, s in book.values()), "every buy needs a seller"
    net_rows = [(c, b, s) for c, (b, s) in book.items()]
    levels = [("B", c, b) for c, (b, _s) in book.items()] + [("S", c, s) for c, (_b, s) in book.items()]
    return net_rows, levels


@pytest.mark.parametrize("retail, inst, other, state", [
    ((60, 20), (10, 40), (30, 40), "retail_buying_institutions_selling"),    # retail +40, inst -30, other -10
    ((20, 60), (40, 10), (40, 30), "retail_selling_institutions_buying"),    # retail -40, inst +30, other +10
    ((60, 20), (30, 10), (10, 70), "both_buying"),                           # retail +40, inst +20, other -60
    ((20, 60), (10, 30), (70, 10), "both_selling"),                          # retail -40, inst -20, other +60
])
def test_divergence_states_when_the_data_settles_it(retail, inst, other, state):
    net_rows, levels = _scenario(retail, inst, other)
    d = sc.retail_divergence(net_rows, levels, 100.0)
    assert d.state == state


def test_divergence_intervals_gap_and_complement():
    net_rows, levels = _scenario((60, 20), (10, 40), (30, 40))
    d = sc.retail_divergence(net_rows, levels, 100.0)
    assert d.retail == (40, 40) and d.foreign_institutions == (-30, -30)
    assert d.other == (-10, -10)                       # complement: -(−30 + 40)
    assert d.gap == (pytest.approx(-0.70), pytest.approx(-0.70))        # (−30 − 40) / 100
    assert d.retail[0] + d.foreign_institutions[0] + d.other[0] == 0


def test_divergence_band_demands_a_material_net():
    net_rows, levels = _scenario((60, 20), (10, 40), (30, 40))        # retail +40, institutions -30, day value 100
    # band 25 %: |+40| and |-30| both clear 25 -> settled
    assert sc.retail_divergence(net_rows, levels, 100.0, band=0.25).state == "retail_buying_institutions_selling"
    # band 35 %: retail (+40) clears 35, institutions (-30) do not -> not settled
    assert sc.retail_divergence(net_rows, levels, 100.0, band=0.35).state == "indeterminate"


def test_divergence_indeterminate_when_the_ladder_cuts_brokers_off():
    # 25 rungs per side: anyone below the cut-off is unseen, so the retail cohort's net straddles zero.
    levels = [("B", f"B{i:02d}", 100.0 - i) for i in range(25)] + [("S", f"S{i:02d}", 100.0 - i) for i in range(25)]
    d = sc.retail_divergence([], levels, 5000.0)
    assert d.state == "indeterminate" and d.retail[0] < 0 < d.retail[1]


def test_divergence_none_without_ladder_or_day_value():
    net_rows, levels = _scenario((60, 20), (10, 40), (30, 40))
    assert sc.retail_divergence(net_rows, [], 100.0) is None
    assert sc.retail_divergence(net_rows, levels, None) is None
    assert sc.retail_divergence(net_rows, levels, 0.0) is None


def test_cohorts_do_not_overlap():
    assert not (sc.FOREIGN_INSTITUTIONS & RETAIL_BROKERS)


# ---- score_ticker over a fake cursor ------------------------------------------------------------------------------

class _Cur:
    """Answers each SELECT by table name and records every statement."""

    def __init__(self, price=None, flows=(), levels=(), done=()):
        self.price, self.flows, self.levels, self.done = price, list(flows), list(levels), list(done)
        self.sql = []
        self._last = ""

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._last = sql

    def fetchone(self):
        return None if self.price is None else (self.price,)

    def fetchall(self):
        for table, rows in (("FROM broker_flows", self.flows), ("FROM broker_levels", self.levels),
                            ("FROM done_by_price", self.done)):
            if table in self._last:
                return rows
        raise AssertionError(self._last)


def test_score_ticker_full_day():
    net_rows, levels = _scenario((60, 20), (10, 40), (30, 40))
    flows = [(c, b, s, b - s) for c, b, s in net_rows]
    cur = _Cur(price=100, flows=flows, levels=levels, done=DONE)
    s = sc.score_ticker(cur, "BBCA", DAY)
    assert s.missing == {}
    assert s.bandar.cr3_buy == pytest.approx(1.0)               # three buyers hold all 100
    assert s.divergence.state == "retail_buying_institutions_selling"
    assert s.aggressor.ratio == pytest.approx(1.6)
    assert s.net_buyers.n_net_buyers == 1                       # only retail net-bought (+40)


def test_score_ticker_reports_why_a_measure_is_missing():
    s = sc.score_ticker(_Cur(price=100, flows=[("YP", 10, 5, 5)]), "BBCA", DAY)
    assert s.bandar is None and s.divergence is None and s.aggressor is None
    assert "ladder" in s.missing["bandar"] and "ladder" in s.missing["divergence"]
    assert "done_by_price" in s.missing["aggressor"]
    assert s.net_buyers is not None                              # broker_flows alone still supports this one

    s2 = sc.score_ticker(_Cur(price=None), "BBCA", DAY)
    assert "price_bars" in s2.missing["bandar"] and "net_buyers" in s2.missing


def test_score_ticker_is_read_only():
    cur = _Cur(price=100, flows=[("YP", 10, 5, 5)], levels=[("B", "YP", 10.0), ("S", "AK", 10.0)], done=DONE)
    sc.score_ticker(cur, "BBCA", DAY)
    assert cur.sql and all(q.lstrip().upper().startswith("SELECT") for q in cur.sql)
