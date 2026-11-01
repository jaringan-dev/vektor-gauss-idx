"""Promotion-gate classification for forward-test tags.

RENCANA-UI-UX §2.1 (rapor jujur) + RENCANA-FASE-2 §1.2.A (the promotion bar).

Turns one raw per-tag row from `engine.eval_tags()` into an honest verdict. The
product's whole promise is an honest track record: a tag that has been tested and
does NOT work must read as loudly as one that does (§2.1). So the verdict is one
of three *distinct* states — never a soft 0-100 score:

  READY      the bar is cleared: enough signals, enough calendar span, and a clear
             edge that survives a realistic round-trip cost. ONLY this state earns
             "trusted" visuals downstream (§2.3). No tag has cleared it yet.
  FAILING    tested enough to judge, but no edge survives cost (or it is negative).
             Shown in full, never buried.
  PROBATION  not enough data to judge either way. Neutral; never dressed up to look
             like a working signal (§2.3).

Promotion bar (digest footer / RENCANA-FASE-2 §1.2.A): n >= 50, span >= ~2 months,
edge clearly positive after a ~150-190bp round-trip cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# --- policy knobs (RENCANA-FASE-2 §1.2.A) — tweak here to retune the gate -----
MIN_SIGNALS = 50          # "n >= 50"
MIN_SPAN_DAYS = 60        # "~2 bulan" of calendar coverage
ROUND_TRIP_COST = 0.017   # ~170bp, middle of the stated 150-190bp band

READY = "ready"
FAILING = "failing"
PROBATION = "probation"


@dataclass(frozen=True)
class TagStatus:
    code: str                       # READY | FAILING | PROBATION
    label: str                      # short human label (Indonesian)
    edge_after_cost: float | None   # edge_h5 minus cost — the number the verdict turns on
    enough_data: bool               # has it been tested enough to judge at all?


def span_days(first: date | None, last: date | None) -> int:
    """Calendar days spanned by a tag's signals (0 if either bound is unknown)."""
    if first is None or last is None:
        return 0
    return (last - first).days


def is_small_n(n: int | None) -> bool:
    """True when a tag has too few signals to be judged (below the promotion bar's n)."""
    return (n or 0) < MIN_SIGNALS


def order_rows(rows: list[dict]) -> list[dict]:
    """Display order: well-sampled tags first (largest n first), small-n tags always last.

    A tiny sample can show a spectacular edge (FLOAT-ASING-PADAT, n=34, +6.6%) purely by
    chance; it must never sit above a tag with thousands of observations by default
    (checklist E2). Stable for equal n.
    """
    return sorted(rows, key=lambda r: (is_small_n(r.get("n")), -(r.get("n") or 0)))


def classify(row: dict) -> TagStatus:
    """Verdict for one `engine.eval_tags()` row. See the module docstring for the bar."""
    n = row.get("n") or 0
    edge = row.get("edge_h5")
    enough = (
        n >= MIN_SIGNALS
        and span_days(row.get("first_date"), row.get("last_date")) >= MIN_SPAN_DAYS
    )
    edge_after_cost = (edge - ROUND_TRIP_COST) if edge is not None else None

    if not enough:
        return TagStatus(PROBATION, "PROBATION", edge_after_cost, False)
    if edge_after_cost is not None and edge_after_cost > 0:
        return TagStatus(READY, "LULUS — berbobot", edge_after_cost, True)
    return TagStatus(FAILING, "GAGAL — tak ada edge", edge_after_cost, True)
