"""v2 tags from the 2026-11-07 data-integrity audit: KSEI2-* (published files only, no scripless jump) and the certain-bound
broker tags (FOREIGN-INST2, DOM-RETAIL2, BROKER2-DOMISILI-*). Pure functions only (no DB)."""

from __future__ import annotations

from vg.engine import (
    BROKER_XCHECK_BAND,
    KSEI_TREND_BAND,
    PARTICIPANT_BAND,
    Components,
    _classify,
    ksei2_from_rows,
)


def _detail(indiv, corp, foreign):
    """A KSEI detail blob: local individual, local corporate (institution), foreign; shares."""
    return {"local": {"ID": indiv, "CP": corp, "TOTAL": indiv + corp}, "foreign": {"TOTAL": foreign}}


def _row(indiv, corp, foreign, scripless):
    return (_detail(indiv, corp, foreign), None, None, None, scripless)


# ---- KSEI2 ---------------------------------------------------------------------------------------------------------

def test_sger_october_2026_a_block_deposit_is_not_an_institutional_buy():
    # SGER, shares in billions: Sep and earlier 44.7% registered at KSEI; Oct 100% (7.69 B newly deposited as local corporate).
    rows = [_row(4.05, 10.81, 0.73, 1.0),       # Oct (newest)
            _row(3.16, 3.11, 0.70, 0.447),      # Sep
            _row(3.23, 3.09, 0.64, 0.447),      # Aug
            _row(3.28, 3.06, 0.63, 0.447)]      # Jul
    assert ksei2_from_rows(rows) == ("nodata", None, None)          # v1 read this as KSEI-INST-AKUM + KSEI-RITEL-EXIT


def test_without_a_jump_ksei2_measures_exactly_like_v1():
    rows = [_row(3.0, 5.0, 2.0, 0.9), _row(3.2, 4.8, 2.0, 0.9), _row(3.3, 4.7, 2.0, 0.9), _row(3.5, 4.5, 2.0, 0.89)]
    trend, inst_d, indiv_d = ksei2_from_rows(rows)
    assert inst_d == 5.0 / 10 - 4.5 / 10 and indiv_d == 3.0 / 10 - 3.5 / 10 and trend == "flat"


def test_fewer_than_three_published_months_is_nodata():
    assert ksei2_from_rows([_row(3.0, 5.0, 2.0, 0.9), _row(3.2, 4.8, 2.0, 0.9)]) == ("nodata", None, None)


def test_ksei2_tags_follow_their_own_deltas_not_v1():
    c = Components(ticker="X", ksei_inst_d3m=0.10, ksei_indiv_d3m=-0.10,               # v1 (scripless artefact) fires...
                   ksei2_inst_d3m=None, ksei2_indiv_d3m=None)                        # ...v2 refuses to measure
    tags = _classify(c)
    assert {"KSEI-INST-AKUM", "KSEI-RITEL-EXIT"} <= set(tags) and not any(t.startswith("KSEI2-") for t in tags)
    c2 = Components(ticker="X", ksei2_inst_d3m=KSEI_TREND_BAND * 2, ksei2_indiv_d3m=-KSEI_TREND_BAND * 2)
    assert {"KSEI2-INST-AKUM", "KSEI2-RITEL-EXIT"} <= set(_classify(c2))


# ---- certain-bound broker tags -------------------------------------------------------------------------------------

V = 1_000.0


def test_foreign_inst2_and_dom_retail2_need_the_whole_interval_above_the_band():
    above, straddle = V * (PARTICIPANT_BAND + 0.01), (-V * 0.02, V * 0.30)
    px = {"close": 100.0, "price_chg_5d": 0.0, "nff_w1": 0.0}                     # broker tags need a priced stock, like v1
    t = _classify(Components(ticker="X", day_value=V, finst_net_lo=above, finst_net_hi=V, retail_net_lo=above, retail_net_hi=V, **px))
    assert {"FOREIGN-INST2", "DOM-RETAIL2"} <= set(t)
    t2 = _classify(Components(ticker="X", day_value=V, finst_net_lo=straddle[0], finst_net_hi=straddle[1],
                              retail_net_lo=straddle[0], retail_net_hi=straddle[1], **px))
    assert "FOREIGN-INST2" not in t2 and "DOM-RETAIL2" not in t2                 # could be selling: no tag


def _dom(lo, hi, nff):
    return Components(ticker="X", close=100.0, price_chg_5d=0.0, nff_w1=0.0, day_value=V,
                      bkr_foreign_net_lo=lo, bkr_foreign_net_hi=hi, nff_d0=nff)


def test_broker2_domisili_compares_only_a_certain_broker_sign():
    b = BROKER_XCHECK_BAND
    assert "BROKER2-DOMISILI-KONFIRM" in _classify(_dom(V * (b + 0.01), V * 0.2, V * 0.05))        # both certainly buying
    assert "BROKER2-DOMISILI-DIVERGEN" in _classify(_dom(-V * 0.2, -V * (b + 0.01), V * 0.05))     # brokers certainly selling
    unsure = set(_classify(_dom(-V * 0.05, V * 0.05, V * 0.05)))                                    # range straddles zero
    assert not unsure & {"BROKER2-DOMISILI-KONFIRM", "BROKER2-DOMISILI-DIVERGEN"}


# ---- FLOAT2 --------------------------------------------------------------------------------------------------------

def test_float2_uses_the_real_free_float_not_the_listed_shares():
    from vg.engine import FLOAT_ACCUM_BAND

    # BBCA-like: 0.6% of listed shares bought by foreigners in a week is 1.4% of its 42% free float.
    listed, free = 122_042_299_500, 51_820_646_928
    net = 0.006 * listed
    c = Components(ticker="X", foreign_float_5d=net / listed, foreign_float2_5d=net / free)
    tags = _classify(c)
    assert net / listed < FLOAT_ACCUM_BAND < net / free
    assert "FLOAT2-ASING-PADAT" in tags and "FLOAT-ASING-PADAT" not in tags
    assert "FLOAT2-ASING-PADAT" not in _classify(Components(ticker="X", foreign_float2_5d=None))   # no usable report: silent
