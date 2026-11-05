"""Broker-flow scoring: three plain, explainable measures of who is on each side of a ticker-day.

    1. Bandar Power Index   how concentrated the BUY side is versus the SELL side (top-3 / top-5 brokers)
    2. Aggressor Ratio      who crossed the spread: buyers lifting offers (HAKA) vs sellers hitting bids (HAKI)
    3. Retail Net-Flow Divergence   whether the retail cohort and the foreign-institution cohort are on opposite sides

Principles (PRINSIP-DATA, RENCANA-FASE-1 §5): every number is a documented formula over stored rows, with no learned
weights and no composite 0-100 score. Nothing here says buy or sell - it describes. All inputs are Tier 3 (the VG feed)
so these are probation-layer descriptors, to be forward-tested before they move any decision.

Where the data comes from, and what it can and cannot show
----------------------------------------------------------
* `broker_flows`  holds only the feed's top-20 NET buyers (with their buy AND sell values). Summing it can never show
  net selling, so no cohort net is read from it alone.
* `broker_levels` holds the top-25 GROSS buyers and top-25 GROSS sellers. Combined with `broker_flows` it gives
  *certain bounds* on a cohort's net (`vg.broker_bounds`): net lies in [lo, hi], and a state is reported only when the
  whole interval is on one side of zero. Unit check on the live feed: the 25 largest buyers add up to 95-99 % of
  `price_bars.value` and never exceed it, so shares of `price_bars.value` are well-defined.
* `done_by_price` is harvested on demand (one done-details fetch is ~130 pages per liquid stock-day), so most days have
  no row. The Aggressor Ratio then simply is not available - it returns None, it never guesses.

Each function returns None when its inputs cannot support an honest answer; `score_ticker` records why in
`TickerScore.missing`. Reads only (SELECT); no table is created, altered or written.

Pure functions (`bandar_power`, `net_buyer_concentration`, `aggressor_ratio`, `retail_divergence`) take plain
sequences and are unit-tested without a database; `score_ticker` is the thin psycopg wrapper.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import psycopg

from ..broker_bounds import group_net_bounds
from ..sources.vektorgauss import FOREIGN_BROKERS, RETAIL_BROKERS

# A top-N share can exceed 1 only if the units disagree (IDR vs lots, or a wrong day). Tolerate rounding, no more.
_SHARE_TOLERANCE = 1e-6

# Foreign houses that are not retail-flagged: the engine's FOREIGN-INST cohort (side_flag F and not retail).
FOREIGN_INSTITUTIONS: frozenset[str] = FOREIGN_BROKERS - RETAIL_BROKERS


def _f(v) -> float | None:
    return None if v is None else float(v)


# ---------------------------------------------------------------------------------------------------------------
# 1. Bandar Power Index
# ---------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BandarPower:
    """Top-N concentration of GROSS buying and selling, as a share of the day's traded value.

        cr{N}_buy   = (value bought by the N largest buyers)  / day_value          in [0, 1]
        cr{N}_sell  = (value sold   by the N largest sellers) / day_value          in [0, 1]
        bpi{N}      = cr{N}_buy - cr{N}_sell                                       in [-1, 1]

    bpi > 0: the buy side is more concentrated than the sell side (few hands buy, many sell); < 0: the reverse.
    day_value is `price_bars.value`: every trade has one buyer and one seller, so it is also the total bought and the
    total sold. `overlap3` is how many brokers sit in BOTH top-3 lists - a high value means the same houses are on
    both sides (crossing / market making), which makes a large cr3 much less informative."""

    day_value: float
    cr3_buy: float
    cr5_buy: float
    cr3_sell: float
    cr5_sell: float
    bpi3: float
    bpi5: float
    top_buyer: str
    top_seller: str
    overlap3: int
    n_buy_rungs: int
    n_sell_rungs: int


def _ranked(levels: Iterable[tuple[str, float | None]]) -> list[tuple[str, float]]:
    out = [(c, float(v)) for c, v in levels if v is not None and float(v) > 0]
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def bandar_power(
    buy_levels: Iterable[tuple[str, float | None]],
    sell_levels: Iterable[tuple[str, float | None]],
    day_value: float | None,
) -> BandarPower | None:
    """`buy_levels` / `sell_levels`: (broker_code, gross value in IDR) for the feed's gross ladders.
    None when either ladder is empty, `day_value` is missing/non-positive, or the shares exceed 1 (unit mismatch)."""
    if not day_value or day_value <= 0:
        return None
    buys, sells = _ranked(buy_levels), _ranked(sell_levels)
    if not buys or not sells:
        return None
    dv = float(day_value)

    def share(ranked: list[tuple[str, float]], n: int) -> float:
        return sum(v for _c, v in ranked[:n]) / dv

    cr3b, cr5b, cr3s, cr5s = share(buys, 3), share(buys, 5), share(sells, 3), share(sells, 5)
    if max(cr3b, cr5b, cr3s, cr5s) > 1 + _SHARE_TOLERANCE:
        return None
    return BandarPower(
        day_value=dv, cr3_buy=cr3b, cr5_buy=cr5b, cr3_sell=cr3s, cr5_sell=cr5s,
        bpi3=cr3b - cr3s, bpi5=cr5b - cr5s, top_buyer=buys[0][0], top_seller=sells[0][0],
        overlap3=len({c for c, _ in buys[:3]} & {c for c, _ in sells[:3]}),
        n_buy_rungs=len(buys), n_sell_rungs=len(sells),
    )


@dataclass(frozen=True, slots=True)
class NetBuyerConcentration:
    """Share of the listed NET buying held by the top 1 / 3 / 5 net buyers. This is the engine's `bkr_cr1/cr3` view,
    extended to 5: it needs only `broker_flows`, but it sees net buyers only (the top 20), so it measures how
    lopsided the BUYING is among them, not the market. Use `BandarPower` when the gross ladders exist."""

    cr1: float
    cr3: float
    cr5: float
    n_net_buyers: int
    total_net_buying: float


def net_buyer_concentration(net_vals: Iterable[float | None]) -> NetBuyerConcentration | None:
    """`net_vals`: net value (IDR) per broker from `broker_flows`; only positive nets count. None if nobody net-bought."""
    buyers = sorted((float(v) for v in net_vals if v is not None and float(v) > 0), reverse=True)
    total = sum(buyers)
    if total <= 0:
        return None
    return NetBuyerConcentration(
        cr1=buyers[0] / total, cr3=sum(buyers[:3]) / total, cr5=sum(buyers[:5]) / total,
        n_net_buyers=len(buyers), total_net_buying=total,
    )


# ---------------------------------------------------------------------------------------------------------------
# 2. Aggressor Ratio
# ---------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AggressorRatio:
    """HAKA (buyer lifts the offer) vs HAKI (seller hits the bid), by volume, over the chosen boards.

        haka_share = haka / (haka + haki)      in [0, 1]; 0.5 = balanced      (None if neither side classified)
        ratio      = haka / haki               >1 = buyers more aggressive    (None if haki == 0)
        coverage   = (haka + haki) / qty       how much of the volume carries an aggressor flag

    Default board is RG (regular). On NG / TN (negotiated / cash) the feed flags the aggressor as BUY for nearly
    everything - a negotiated cross has no spread to cross - so those boards are excluded unless asked for."""

    boards: tuple[str, ...]
    qty: float
    haka_qty: float
    haki_qty: float
    unclassified_qty: float
    haka_share: float | None
    ratio: float | None
    coverage: float | None
    price_levels: int


def aggressor_ratio(
    rows: Iterable[tuple[str, float | None, float | None, float | None]],
    boards: Sequence[str] = ("RG",),
) -> AggressorRatio | None:
    """`rows`: (board, qty, buy_aggressor_qty, sell_aggressor_qty) per `done_by_price` price level.
    None when no row falls on `boards` or the volume is zero."""
    want = set(boards)
    qty = haka = haki = 0.0
    levels = 0
    for board, q, b, s in rows:
        if board not in want:
            continue
        levels += 1
        qty += float(q or 0)
        haka += float(b or 0)
        haki += float(s or 0)
    if levels == 0 or qty <= 0:
        return None
    classified = haka + haki
    return AggressorRatio(
        boards=tuple(boards), qty=qty, haka_qty=haka, haki_qty=haki,
        unclassified_qty=max(qty - classified, 0.0),
        haka_share=(haka / classified) if classified > 0 else None,
        ratio=(haka / haki) if haki > 0 else None,
        coverage=classified / qty,
        price_levels=levels,
    )


# ---------------------------------------------------------------------------------------------------------------
# 3. Retail Net-Flow Divergence
# ---------------------------------------------------------------------------------------------------------------

DivergenceState = Literal[
    "retail_buying_institutions_selling",
    "retail_selling_institutions_buying",
    "both_buying",
    "both_selling",
    "indeterminate",
]


@dataclass(frozen=True, slots=True)
class RetailDivergence:
    """Net flow of the retail houses against the foreign-institution houses, with CERTAIN bounds (IDR).

    `retail`, `foreign_institutions`, `other` are (lo, hi) intervals. The three cohorts cover every broker and
    every buy is somebody's sell, so they sum to zero; `other` is derived as the complement and is as wide as the
    other two intervals added together. `gap` is (foreign_institutions - retail) / day_value as an interval.

    `state` is set only when BOTH cohort intervals lie strictly beyond +/- `band` x day_value; otherwise it is
    "indeterminate" - the data does not settle it, which is the normal outcome when the ladders cut brokers off."""

    day_value: float
    retail: tuple[float, float]
    foreign_institutions: tuple[float, float]
    other: tuple[float, float]
    gap: tuple[float, float]
    state: DivergenceState


def _side(iv: tuple[float, float], floor: float) -> int:
    """+1 if the whole interval is above +floor, -1 if wholly below -floor, else 0 (not settled)."""
    lo, hi = iv
    return 1 if lo > floor else -1 if hi < -floor else 0


def retail_divergence(
    net_rows: Sequence[tuple[str, float | None, float | None]],
    levels: Sequence[tuple[str, str, float | None]],
    day_value: float | None,
    *,
    band: float = 0.0,
    retail: frozenset[str] = RETAIL_BROKERS,
    institutions: frozenset[str] = FOREIGN_INSTITUTIONS,
) -> RetailDivergence | None:
    """`net_rows`: (broker_code, buy_val, sell_val) from `broker_flows`; `levels`: (side 'B'|'S', broker_code, val)
    from `broker_levels`; `band`: minimum |net| as a fraction of day_value for a cohort to count as buying/selling
    (0 = any certain sign). None without a ladder or a positive `day_value`."""
    if not day_value or day_value <= 0:
        return None
    groups = group_net_bounds(net_rows, levels, institutions - retail, retail)
    if groups is None:
        return None
    dv = float(day_value)
    r, f, o = groups["retail"], groups["foreign"], groups["other"]
    floor = band * dv
    rs, fs = _side(r, floor), _side(f, floor)
    if rs > 0 and fs < 0:
        state: DivergenceState = "retail_buying_institutions_selling"
    elif rs < 0 and fs > 0:
        state = "retail_selling_institutions_buying"
    elif rs > 0 and fs > 0:
        state = "both_buying"
    elif rs < 0 and fs < 0:
        state = "both_selling"
    else:
        state = "indeterminate"
    return RetailDivergence(
        day_value=dv, retail=r, foreign_institutions=f, other=o,
        gap=((f[0] - r[1]) / dv, (f[1] - r[0]) / dv), state=state,
    )


# ---------------------------------------------------------------------------------------------------------------
# One ticker-day, from the database
# ---------------------------------------------------------------------------------------------------------------

@dataclass(slots=True)
class TickerScore:
    ticker: str
    session_date: date
    bandar: BandarPower | None = None
    net_buyers: NetBuyerConcentration | None = None
    aggressor: AggressorRatio | None = None
    divergence: RetailDivergence | None = None
    missing: dict[str, str] = field(default_factory=dict)     # measure -> why it is not available


def score_ticker(cur: psycopg.Cursor, ticker: str, session_date: date) -> TickerScore:
    """Compute every measure that the stored rows of one ticker-day support. SELECT-only."""
    out = TickerScore(ticker=ticker, session_date=session_date)

    cur.execute(
        "SELECT value FROM price_bars WHERE ticker = %s AND session_date = %s", (ticker, session_date))
    row = cur.fetchone()
    day_value = _f(row[0]) if row else None

    cur.execute(
        "SELECT broker_code, buy_val, sell_val, net_val FROM broker_flows "
        "WHERE ticker = %s AND session_date = %s AND board = 'ALL'", (ticker, session_date))
    flows = cur.fetchall()
    cur.execute(
        "SELECT side, broker_code, val FROM broker_levels WHERE ticker = %s AND session_date = %s",
        (ticker, session_date))
    levels = [(s, c, _f(v)) for s, c, v in cur.fetchall()]
    cur.execute(
        "SELECT board, qty, buy_aggressor_qty, sell_aggressor_qty FROM done_by_price "
        "WHERE ticker = %s AND session_date = %s", (ticker, session_date))
    done = cur.fetchall()

    out.net_buyers = net_buyer_concentration(f[3] for f in flows)
    if out.net_buyers is None:
        out.missing["net_buyers"] = "no broker_flows rows with net buying for the day"

    if day_value is None or day_value <= 0:
        out.missing["bandar"] = out.missing["divergence"] = "no price_bars.value for the day"
    elif not levels:
        out.missing["bandar"] = out.missing["divergence"] = "no broker_levels ladder for the day"
    else:
        out.bandar = bandar_power(
            [(c, v) for s, c, v in levels if s == "B"], [(c, v) for s, c, v in levels if s == "S"], day_value)
        if out.bandar is None:
            out.missing["bandar"] = "ladder incomplete or inconsistent with price_bars.value"
        out.divergence = retail_divergence(
            [(c, b, s) for c, b, s, _n in flows], levels, day_value)
        if out.divergence is None:
            out.missing["divergence"] = "no certain bounds available"

    out.aggressor = aggressor_ratio(done)
    if out.aggressor is None:
        out.missing["aggressor"] = "no regular-board done_by_price rows (harvested on demand only)"
    return out
