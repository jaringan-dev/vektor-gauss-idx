"""Certain bounds on a broker cohort's NET value from the feed's partial views (ARSITEKTUR-DATA-v1 K2).

Why this exists. The feed shows each ticker-day through two windows:
  * `brokers`       — the top-20 NET buyers, with BOTH their buy and sell values (stored in broker_flows)
  * `broker_levels` — a ladder of the top-`DEPTH` GROSS buyers and the top-`DEPTH` GROSS sellers (broker_levels)
A cohort's net (e.g. "all foreign houses") = Σ buy − Σ sell. Summing `broker_flows` alone covers only
net buyers, so it can never go meaningfully negative — a "foreign net" that cannot show selling. Combining the
two windows gives, for every broker, either an exact side value or the knowledge that it sits below the ladder
cut-off on that side (value ∈ [0, cut-off]). Summing those intervals gives bounds that are CERTAIN, not
estimates: net ∈ [lo, hi]. A tag may fire only when the whole interval clears its threshold.

Pure functions, no DB. When a ladder has fewer than `DEPTH` rungs on a side, every active broker on that
side is visible, so the cut-off is 0 and the bound collapses to the exact value.
"""

from __future__ import annotations

from dataclasses import dataclass

DEPTH = 25     # rungs per side the feed returns (observed 2026-11-02; fewer = everyone visible)


@dataclass(frozen=True)
class Bounds:
    lo: float
    hi: float
    n_exact: int          # cohort brokers whose both sides are known exactly
    n_partial: int        # cohort brokers with at least one side only bounded by the cut-off
    cut_buy: float        # buy-side ladder cut-off (0 = everyone visible)
    cut_sell: float

    @property
    def width(self) -> float:
        return self.hi - self.lo


def _cutoff(vals: list[float]) -> float:
    """Smallest visible value if the ladder is full (anything unseen is below it), else 0."""
    return min(vals) if len(vals) >= DEPTH else 0.0


def cohort_net_bounds(net_rows, levels, cohort) -> Bounds | None:
    """net_rows: iterable of (broker_code, buy_val, sell_val) — the top net buyers, both sides exact.
    levels   : iterable of (side 'B'|'S', broker_code, val) — the gross ladders.
    cohort   : set of broker codes whose net we want (e.g. FOREIGN_BROKERS).
    Returns None when there is no ladder at all (nothing to bound with)."""
    buys: dict[str, float] = {}
    sells: dict[str, float] = {}
    b_vals: list[float] = []
    s_vals: list[float] = []
    have_ladder = False
    for side, code, val in levels:
        if val is None:
            continue
        have_ladder = True
        if side == "B":
            buys[code] = float(val)
            b_vals.append(float(val))
        else:
            sells[code] = float(val)
            s_vals.append(float(val))
    if not have_ladder:
        return None
    for code, bv, sv in net_rows:                      # exact on both sides; also fills ladder gaps
        if bv is not None:
            buys.setdefault(code, float(bv))
        if sv is not None:
            sells.setdefault(code, float(sv))
    cut_b, cut_s = _cutoff(b_vals), _cutoff(s_vals)

    lo = hi = 0.0
    exact = partial = 0
    for code in cohort:
        b, s = buys.get(code), sells.get(code)
        b_lo, b_hi = (b, b) if b is not None else (0.0, cut_b)
        s_lo, s_hi = (s, s) if s is not None else (0.0, cut_s)
        lo += b_lo - s_hi
        hi += b_hi - s_lo
        if b is not None and s is not None:
            exact += 1
        elif b is not None or s is not None or cut_b or cut_s:
            partial += 1
    return Bounds(lo, hi, exact, partial, cut_b, cut_s)
