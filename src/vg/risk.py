"""Mechanical risk plan: stop-loss level, R-multiple targets and position size (RENCANA-ROMBAK-UI-v3 §9).

Pure arithmetic over a reference price and ATR14 — no prediction, no DB, no judgement about whether to trade.
The numbers are risk-management scaffolding: a stop at k x ATR below the reference price, targets at multiples of the
distance to that stop (R), and a lot count that caps the loss at a chosen share of the account if the stop is hit.
Whether such levels make any tag profitable is a separate, measured question (see `risk_eval.py`); the UI shows both.

IDX specifics encoded here: tick size (fraksi harga) bands and 1 lot = 100 shares. Auto-rejection limits (ARA/ARB) and
gap-through-the-stop slippage are deliberately NOT modelled — the UI says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

LOT_SIZE = 100
STOP_ATR_MULT = 2.0
R_MULTIPLES = (1.0, 2.0, 3.0)
DEFAULT_FEE = 0.004          # round-trip commission + levy, as in common lot-size calculators
WIDE_STOP_PCT = 0.15         # a stop further than this from the reference price is flagged as very wide

# IDX tick size by price band: (upper bound exclusive, tick)
_TICKS = ((200.0, 1), (500.0, 2), (2000.0, 5), (5000.0, 10), (math.inf, 25))


def tick_size(price: float) -> int:
    for upper, tick in _TICKS:
        if price < upper:
            return tick
    return 25


def round_down(price: float) -> float:
    t = tick_size(price)
    return math.floor(price / t + 1e-9) * t


def round_nearest(price: float) -> float:
    t = tick_size(price)
    return round(price / t) * t


@dataclass(frozen=True)
class Target:
    r: float
    price: float
    gain_pct: float


@dataclass(frozen=True)
class RiskPlan:
    ref: float
    atr: float
    stop: float
    risk_per_share: float
    risk_pct: float                    # stop distance as a fraction of the reference price
    targets: tuple[Target, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LotPlan:
    lots: int
    shares: int
    position_value: float
    max_loss: float                    # loss if the stop is hit exactly, including fees
    capped_by_balance: bool


def build_plan(ref: float | None, atr: float | None, *, k: float = STOP_ATR_MULT,
               r_multiples: tuple[float, ...] = R_MULTIPLES) -> RiskPlan | None:
    """None when there is nothing honest to compute (no price/ATR, or the stop would be at or below zero)."""
    if not ref or ref <= 0 or not atr or atr <= 0:
        return None
    raw_stop = ref - k * atr
    if raw_stop <= 0:
        return None
    stop = round_down(raw_stop)
    if stop <= 0 or stop >= ref:
        return None
    risk = ref - stop
    warnings: list[str] = []
    if risk / ref > WIDE_STOP_PCT:
        warnings.append(f"The stop level is {risk / ref * 100:.0f}% below the reference price: this stock is very volatile, "
                        "so the position size will be small.")
    targets = tuple(Target(r, round_nearest(ref + r * risk), (round_nearest(ref + r * risk) / ref - 1)) for r in r_multiples)
    return RiskPlan(ref=ref, atr=atr, stop=stop, risk_per_share=risk, risk_pct=risk / ref, targets=targets,
                    warnings=tuple(warnings))


def lot_size(balance: float, risk_fraction: float, ref: float, stop: float, *, fee: float = DEFAULT_FEE) -> LotPlan | None:
    """Lots such that hitting the stop loses at most `risk_fraction` of `balance`, fees included.

    `balance` is the capital you allocate to trading, not account equity and not cash — the figure the risk percent
    applies to. Returns None for impossible inputs. The result is capped by what the balance can actually buy.
    """
    if balance <= 0 or risk_fraction <= 0 or ref <= 0 or stop <= 0 or stop >= ref:
        return None
    per_share_loss = (ref - stop) + ref * fee
    shares_by_risk = (balance * risk_fraction) / per_share_loss
    lots = int(shares_by_risk // LOT_SIZE)
    affordable = int(balance // (ref * LOT_SIZE))
    capped = lots > affordable
    lots = max(0, min(lots, affordable))
    shares = lots * LOT_SIZE
    return LotPlan(lots=lots, shares=shares, position_value=shares * ref,
                   max_loss=shares * per_share_loss, capped_by_balance=capped)
