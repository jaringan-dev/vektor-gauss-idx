"""Position-sizing / trade-plan maths — the logic behind NeoBDM's calculator.

Pure functions, no I/O. The web calculator (Phase 2) and the digest's trade
calls both call this.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_LOT = 100  # IDX board lot


@dataclass(slots=True)
class TradePlan:
    avg_entry: float
    stop_loss: float
    target: float | None
    rr: float | None                 # reward : risk at `target`
    risk_per_share: float            # entry - sl, incl. round-trip fee
    # sizing (only when balance > 0)
    lots: int | None = None
    shares: int | None = None
    position_idr: float | None = None
    risk_idr: float | None = None
    risk_pct_of_balance: float | None = None


def average_entry(entries: list[float]) -> float:
    xs = [e for e in entries if e and e > 0]
    if not xs:
        raise ValueError("need at least one positive entry price")
    return sum(xs) / len(xs)


def plan(
    entries: list[float] | float,
    stop_loss: float,
    *,
    target: float | None = None,
    target_r: float | None = None,
    balance: float = 0.0,
    risk_pct: float = 0.01,          # fraction, e.g. 0.01 = 1%
    fee_pct: float = 0.003,          # fraction, round-trip
) -> TradePlan:
    """Long-only. `entries` is one price or several (averaging). Either pass an
    explicit `target` or a `target_r` (target = entry + R*(entry - sl))."""
    avg = average_entry([entries] if isinstance(entries, (int, float)) else list(entries))
    if stop_loss >= avg:
        raise ValueError("stop_loss must be below the average entry (long)")

    # fee eats into the effective risk: you pay fee on the way in and the way out
    fee_drag = fee_pct * (avg + stop_loss)
    risk_ps = (avg - stop_loss) + fee_drag

    if target is None and target_r is not None:
        target = avg + target_r * (avg - stop_loss)
    rr = ((target - avg) / (avg - stop_loss)) if target is not None else None

    p = TradePlan(
        avg_entry=round(avg, 4),
        stop_loss=round(stop_loss, 4),
        target=round(target, 4) if target is not None else None,
        rr=round(rr, 2) if rr is not None else None,
        risk_per_share=round(risk_ps, 4),
    )

    if balance and balance > 0 and risk_ps > 0:
        risk_idr = balance * risk_pct
        raw_shares = risk_idr / risk_ps
        lots = max(0, math.floor(raw_shares / _LOT))
        shares = lots * _LOT
        p.lots = lots
        p.shares = shares
        p.risk_idr = round(risk_idr, 0)
        p.position_idr = round(shares * avg, 0)
        p.risk_pct_of_balance = round((shares * risk_ps) / balance, 4) if balance else None
    return p


def atr_stop(close: float, atr14: float, mult: float = 2.0) -> float:
    return close - mult * atr14
