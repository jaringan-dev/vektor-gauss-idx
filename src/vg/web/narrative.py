"""Plain-language summaries built from fixed templates — no model, no free text, fully auditable.

RENCANA-ROMBAK-UI-v3 §2.5. Each sentence is a template filled with numbers that are already on the page, so the
summary can never say something the data does not. It describes; it never advises. `FORBIDDEN` lists words that must
not appear in any generated sentence (a test runs every template through it).
"""

from __future__ import annotations

from . import fmt
from .glossary import QUADRANTS

# Words that would turn a description into advice. "net buyers"/"net sellers" are descriptions of flow and are allowed;
# imperative or recommending phrasing is not.
FORBIDDEN = ("you should", "we recommend", "recommended", "buy now", "sell now", "good time to", "strong buy",
             "strong sell", "must", "guaranteed profit", "guaranteed return", "risk-free", "will rise", "will fall")

_QUAD_MEANING = {
    "AKUMULASI": "price is down or flat while foreigners buy on balance",
    "MARKUP": "price is rising and foreigners are buying on balance",
    "DISTRIBUSI": "price is rising while foreigners sell on balance",
    "MARKDOWN": "price is down or flat and foreigners are selling on balance",
    "NETRAL": "the price and foreign-flow signals do not fall into one of the four quadrants",
}


def _direction_word(chg: float | None) -> str:
    if chg is None:
        return "with no price change available"
    if abs(chg) < 0.0005:
        return "unchanged"
    return f"{'up' if chg > 0 else 'down'} {abs(chg) * 100:.1f}%"


def _side(x: float | None) -> str | None:
    if x is None:
        return None
    if x == 0:
        return "flat (zero net flow)"
    return f"net {'buyers' if x > 0 else 'sellers'} of {fmt.money_words(x)}"


def stock_summary(d: dict) -> list[str]:
    """Sentences describing one stock today. `d` keys: ticker, close, chg, nff_d0, nff_w1, consistency, quadrant,
    n_tags, n_passed (patterns that cleared the forward-test bar), bounds_lo, bounds_hi, day_value (all optional)."""
    out: list[str] = []
    t = d["ticker"]
    if d.get("close") is not None:
        out.append(f"{t} closed at {fmt.price(d['close'])}, {_direction_word(d.get('chg'))} on the day.")
    today, week = _side(d.get("nff_d0")), _side(d.get("nff_w1"))
    if today or week:
        parts = []
        if today:
            parts.append(f"Today foreigners were {today}")
        if week:
            parts.append(("over the last week they were " if today else "Over the last week foreigners were ") + week)
        s = "; ".join(parts)
        if d.get("consistency") is not None:
            s += f" (net buying on {int(d['consistency'])} of the last 10 trading days)"
        out.append(s + ".")
    q = QUADRANTS.get(d.get("quadrant") or "")
    if q:
        out.append(f"That combination is classed as {q[1]}: {_QUAD_MEANING[d['quadrant']]}.")
    lo, hi, dv = d.get("bounds_lo"), d.get("bounds_hi"), d.get("day_value")
    if lo is not None and hi is not None and dv:
        out.append(f"By foreign brokers' own trades, net flow today was between {fmt.pct(lo / dv, 1)} and {fmt.pct(hi / dv, 1)} "
                   "of traded value (a range that is certain to contain the true figure).")
    n_tags = d.get("n_tags") or 0
    if n_tags:
        passed = d.get("n_passed") or 0
        verdict = ("none of them has passed the forward test yet" if passed == 0
                   else f"{passed} of the patterns being tracked have passed the forward test")
        out.append(f"{n_tags} pattern{'s' if n_tags != 1 else ''} apply to it today; {verdict}.")
    out.append("This describes data. It is not a recommendation.")
    return out
