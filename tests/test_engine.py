"""Offline tests for the v0 engine — pure functions only (no DB)."""

from __future__ import annotations

from datetime import date, timedelta

from vg.engine import (
    CONSISTENCY_ACC,
    FLOAT_ACCUM_BAND,
    TICKET_Z_HIGH,
    TICKET_Z_LOW,
    Components,
    _Bar,
    _classify,
    _components,
    _ksei_shares,
    _split_factor,
)


def _bar(
    d: date, close: float, fnet: float, volume: float = 1_000_000,
    *, frequency: float = 1_000.0, bid_volume: float | None = None,
    offer_volume: float | None = None, tradable_shares: float | None = None,
    listed_shares: float | None = None, value: float | None = None,
) -> _Bar:
    # value chosen so VWAP == close  ->  nff_idr == fnet * close
    return _Bar(
        d=d, close=close, prev=close, high=close * 1.01, low=close * 0.99,
        volume=volume, value=close * volume if value is None else value,
        fbuy=max(fnet, 0.0) + 100.0, fsell=max(-fnet, 0.0) + 100.0, nonreg_value=0.0,
        frequency=frequency, bid_volume=bid_volume, offer_volume=offer_volume,
        tradable_shares=tradable_shares, listed_shares=listed_shares,
    )


def test_bar_nff_idr():
    b = _bar(date(2026, 1, 5), close=1000.0, fnet=5000.0)
    assert b.nff_idr == 5000.0 * 1000.0
    assert b.foreign_net_shares == 5000.0


def test_split_factor():
    ev = [(date(2026, 3, 10), 10.0), (date(2026, 6, 1), 0.5)]
    assert _split_factor(ev, date(2026, 1, 1), date(2026, 12, 31)) == 5.0
    assert _split_factor(ev, date(2026, 3, 10), date(2026, 5, 1)) == 1.0  # exclusive lo
    assert _split_factor(ev, date(2026, 2, 1), date(2026, 4, 1)) == 10.0


def _calendar(n: int, end: date) -> list[date]:
    # n consecutive weekdays ending at `end`
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days[::-1]


def test_components_accumulation_shape():
    cal = _calendar(21, date(2026, 9, 1))
    d0 = cal[-1]
    # price flat (~1000 throughout), foreign net-buying every day
    bars = {d: _bar(d, close=1000.0, fnet=4000.0) for d in cal}
    ihsg = {d: 100.0 for d in cal}

    c = _components("BBRI", cal, bars, ihsg, splits=[])
    assert c is not None
    assert c.nff_d0 == 4000.0 * 1000.0
    assert c.nff_w1 == 5 * 4000.0 * 1000.0
    assert c.nff_w4 == 5 * 4000.0 * 1000.0
    assert c.foreign_consistency_10d == 10
    assert abs(c.vol_surge - 1.0) < 1e-9
    assert abs(c.price_chg_5d) < 1e-9          # flat
    assert abs(c.rs_ihsg_5d) < 1e-9            # both flat
    assert abs(c.atr14 - 20.0) < 1e-6          # high/low span 2% of 1000


def test_components_split_adjusted_return():
    cal = _calendar(10, date(2026, 9, 1))
    d0, d5 = cal[-1], cal[-6]
    bars = {d: _bar(d, close=1000.0, fnet=0.0) for d in cal}
    # a 10:1 split with ex_date between d5 and d0 halves... no, /10 the raw price
    for d in cal:
        if d <= d5:
            bars[d] = _bar(d, close=10000.0, fnet=0.0)   # pre-split price 10x
    ex = d5 + timedelta(days=1)
    c = _components("X", cal, bars, {d: 1.0 for d in cal}, splits=[(ex, 10.0)])
    assert c is not None
    # adjusted: pre-split 10000 / 10 == 1000 == today -> ~0% change, not -90%
    assert abs(c.price_chg_5d) < 1e-6


def test_bar_avg_ticket():
    assert _bar(date(2026, 1, 5), 1000.0, 0.0, volume=1_000_000, frequency=2_000).avg_ticket == \
        1000.0 * 1_000_000 / 2_000
    assert _bar(date(2026, 1, 5), 1000.0, 0.0, frequency=0).avg_ticket is None


def test_components_phase125_fields():
    cal = _calendar(22, date(2026, 9, 1))
    d0 = cal[-1]
    # 21 history days at a steady ~1M avg ticket (small cyclic noise), today a spike.
    bars = {}
    for i, d in enumerate(cal[:-1]):
        freq = 900 + (i % 5) * 50            # 900..1100 -> some variance
        bars[d] = _bar(d, 1000.0, fnet=1000.0, volume=1_000_000, value=1e9,
                       frequency=freq, tradable_shares=100_000.0, listed_shares=125_000.0)
    bars[d0] = _bar(d0, 1000.0, fnet=1000.0, volume=1_000_000, value=1e9,
                    frequency=200, tradable_shares=100_000.0, listed_shares=125_000.0)
    c = _components("X", cal, bars, {d: 1.0 for d in cal}, splits=[])
    assert c is not None
    assert abs(c.avg_ticket - 1e9 / 200) < 1e-6           # 5M today
    assert c.avg_ticket_z is not None and c.avg_ticket_z > TICKET_Z_HIGH
    assert abs(c.float_ratio - 0.8) < 1e-9                 # 100k / 125k
    assert abs(c.foreign_float_5d - 5 * 1000.0 / 100_000.0) < 1e-9  # 5d fnet / float = 0.05


def test_classify_tiket_and_float_tags():
    # big ticket into weakness
    c = Components(ticker="X", close=100.0, price_chg_5d=-0.01, nff_w1=0.0,
                   avg_ticket_z=TICKET_Z_HIGH + 0.5)
    assert "TIKET-BESAR-AKUM" in _classify(c)
    # small ticket chasing price up
    c = Components(ticker="X", close=100.0, price_chg_5d=0.06, nff_w1=0.0,
                   avg_ticket_z=TICKET_Z_LOW - 0.5)
    assert "TIKET-KECIL-MARKUP" in _classify(c)
    # foreign took a slice of thin float — fires with no price history
    c = Components(ticker="X", foreign_float_5d=FLOAT_ACCUM_BAND + 0.02)
    assert _classify(c) == ["FLOAT-ASING-PADAT"]


def test_classify_broker_foreign_akum_and_konfirm():
    # foreign-broker net-buy = 10% of the day's value into flat price, and the
    # domicile measure (nff_d0) agrees in sign -> AKUM + KONFIRM.
    c = Components(
        ticker="BBCA", close=8000.0, price_chg_5d=0.0, nff_w1=0.0,
        day_value=1_000_000_000.0, bkr_foreign_net_d0=100_000_000.0,
        nff_d0=50_000_000.0,
    )
    tags = _classify(c)
    assert "BROKER-ASING-AKUM" in tags
    assert "BROKER-DOMISILI-KONFIRM" in tags
    assert "BROKER-DOMISILI-DIVERGEN" not in tags


def test_classify_broker_domicile_divergen():
    # foreign-BROKER net-buy but account-domicile foreign net-sell -> the two
    # foreign measures disagree; annotate DIVERGEN (never sum them).
    c = Components(
        ticker="BBCA", close=8000.0, price_chg_5d=0.0, nff_w1=0.0,
        day_value=1_000_000_000.0, bkr_foreign_net_d0=80_000_000.0,
        nff_d0=-60_000_000.0,
    )
    tags = _classify(c)
    assert "BROKER-DOMISILI-DIVERGEN" in tags
    assert "BROKER-DOMISILI-DIVERGEN" in c.data_flags


def test_classify_no_broker_tag_below_band():
    # net-buy only 1% of value (< BROKER_FOREIGN_BAND) -> no AKUM tag
    c = Components(
        ticker="BBCA", close=8000.0, price_chg_5d=0.0, nff_w1=0.0,
        day_value=1_000_000_000.0, bkr_foreign_net_d0=10_000_000.0, nff_d0=0.0,
    )
    assert "BROKER-ASING-AKUM" not in _classify(c)


def test_classify_akum_asing():
    c = Components(
        ticker="BBRI", close=1000.0, price_chg_5d=-0.01,   # down/flat
        nff_w1=1e9, nff_w2=1e9, foreign_consistency_10d=CONSISTENCY_ACC,
        ksei_foreign_trend_3m="up",
    )
    tags = _classify(c)
    assert "AKUM-ASING" in tags
    assert "KSEI-KONFIRM" in tags
    assert c.quadrant == "MARKDOWN" or c.quadrant == "AKUMULASI"


def test_classify_distribusi_with_ksei_divergen():
    c = Components(
        ticker="ADRO", close=2000.0, price_chg_5d=0.06,     # up
        nff_w1=-5e8, foreign_consistency_10d=2,
        ksei_foreign_trend_3m="up",                          # foreign ownership rising -> divergent
    )
    tags = _classify(c)
    assert "DISTRIBUSI-ASING" in tags
    assert "KSEI-DIVERGEN" in tags
    assert "KSEI-DIVERGEN" in c.data_flags


def test_classify_lapor_kepemilikan_ramai():
    # surge: 3 filings in 5td vs a baseline rate of 4/20td (=1/5td). No price history needed.
    assert "LAPOR-KEPEMILIKAN-RAMAI" in _classify(
        Components(ticker="X", disclosure_5d=3, disclosure_20d=4)
    )
    # below the absolute cluster floor
    assert "LAPOR-KEPEMILIKAN-RAMAI" not in _classify(
        Components(ticker="X", disclosure_5d=2, disclosure_20d=2)
    )
    # elevated but in line with a busy baseline (16/20td = 4/5td) -> not a surge
    assert "LAPOR-KEPEMILIKAN-RAMAI" not in _classify(
        Components(ticker="X", disclosure_5d=4, disclosure_20d=16)
    )


def test_classify_disclosure_direction():
    # Phase B tags fire independently of each other and of the surge-count proxy.
    assert _classify(Components(ticker="X", insider_akum=True)) == ["INSIDER-AKUM"]
    assert _classify(Components(ticker="X", insider_dist=True)) == ["INSIDER-DIST"]
    assert _classify(Components(ticker="X", substantial_akum=True)) == ["SUBSTANTIAL-AKUM"]
    assert _classify(Components(ticker="X", substantial_dist=True)) == ["SUBSTANTIAL-DIST"]
    # a ticker can have both an insider and a substantial-holder filing the same day
    tags = _classify(Components(ticker="X", insider_akum=True, substantial_dist=True))
    assert set(tags) == {"INSIDER-AKUM", "SUBSTANTIAL-DIST"}
    # False/None must not fire
    assert _classify(Components(ticker="X", insider_akum=False, insider_dist=None)) == []


def test_classify_needs_history():
    assert _classify(Components(ticker="X")) == []          # no price_chg_5d / nff_w1


def test_ksei_shares_from_detail():
    detail = {"local": {"ID": 20.0, "TOTAL": 50.0}, "foreign": {"TOTAL": 50.0}}
    fgn, inst, indiv = _ksei_shares(detail, None, None, None)
    assert abs(fgn - 0.5) < 1e-9
    assert abs(indiv - 0.2) < 1e-9          # 20 / 100
    assert abs(inst - 0.3) < 1e-9           # (50 - 20) / 100


def test_ksei_shares_fallback_to_columns():
    fgn, inst, indiv = _ksei_shares(None, 30.0, 20.0, 50.0)   # li, lin, ft
    assert abs(fgn - 0.5) < 1e-9
    assert abs(indiv - 0.3) < 1e-9
    assert abs(inst - 0.2) < 1e-9                             # (50 - 30) / 100
    assert _ksei_shares(None, 0, 0, 0) is None


def test_classify_ksei_standalone_tags():
    c = Components(
        ticker="X", close=100.0, price_chg_5d=0.0, nff_w1=0.0,
        ksei_inst_d3m=0.02, ksei_indiv_d3m=-0.03,
    )
    tags = _classify(c)
    assert "KSEI-INST-AKUM" in tags
    assert "KSEI-RITEL-EXIT" in tags


def test_comp_columns_align():
    from dataclasses import fields

    from vg.engine import _COMP_COLS

    attrs = {f.name for f in fields(Components)}
    for col in _COMP_COLS:
        if col in ("session_date",):
            continue
        assert col in attrs, f"signal_components column {col!r} missing on Components"


def test_classify_participant_heuristics():
    from vg.engine import Components, _classify, BANDAR_TICKET_MIN
    # foreign-institution net-buy = 8% of value, domestic retail also crowding = 6%
    c = Components(
        ticker="X", close=1000.0, price_chg_5d=0.0, nff_w1=0.0, day_value=1_000_000_000.0,
        bkr_foreign_inst_net=80_000_000.0, bkr_dom_retail_net=60_000_000.0,
        bkr_cr1=0.5, bkr_top_buyer="YU", bkr_top_buyer_ticket=BANDAR_TICKET_MIN + 1,
        bkr_top_buyer_inv_rising=True,
    )
    tags = _classify(c)
    assert "FOREIGN-INST" in tags
    assert "DOM-RETAIL" in tags
    assert "BANDAR-CANDIDATE" in tags


def test_bandar_needs_concentration_and_ticket():
    from vg.engine import Components, _classify
    # concentrated (cr1 high) but tiny ticket -> NOT a bandar
    c = Components(ticker="X", close=100.0, price_chg_5d=0.0, nff_w1=0.0,
                   day_value=1_000_000_000.0, bkr_cr1=0.8, bkr_top_buyer="XL",
                   bkr_top_buyer_ticket=1_000_000, bkr_top_buyer_inv_rising=True)
    assert "BANDAR-CANDIDATE" not in _classify(c)
