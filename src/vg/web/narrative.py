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


def broker_summary(r: dict, tags: list[str], verdicts: dict | None, edge_h5: dict, plain=lambda t: t) -> list[str]:
    """The Executive Summary on a stock page: what the broker tables say over five sessions and today, how many accumulation signs
    agree, and what the research says about today's patterns. `r` is stock.broker_reading(); `verdicts` {tag: research verdict} or
    None (not researched); `edge_h5` {tag: edge cell at H+5}; `plain` turns a tag code into words. Describes, never advises."""
    out: list[str] = []
    w = r.get("win")
    if w is not None and w.bpi3 is not None:
        pts = round(w.bpi3 * 100, 1) + 0.0                   # + 0.0 turns -0.0 into 0.0
        side = ("buying was the more concentrated side" if pts > 0 else "selling was the more concentrated side" if pts < 0
                else "buying and selling were about equally concentrated")
        s = (f"Over the last {w.days_ok} sessions with broker data, {side} ({pts:+.1f} points on the five-session index); "
             f"the daily index was positive on {w.days_up} of them")
        if w.top_buyer:
            s += f", and the biggest buyer over the window, {w.top_buyer}, was among the day's top 3 buyers on {w.top_buyer_days}"
        out.append(s + ".")
        if w.n_crossing:
            out.append(f"{w.n_crossing} of those sessions {'was' if w.n_crossing == 1 else 'were'} mostly negotiated-market (block) trades, "
                       "which weigh heavily in that sum.")
    elif w is not None:
        out.append(f"There is broker data on only {w.days_ok} of the last {w.n_window} sessions: too little for a five-session reading.")
    else:
        out.append("There is no broker table for this stock in the last five sessions.")
    n = r.get("net")
    if n is not None:
        out.append(f"{n.broker} was a net buyer on {n.days} of {n.days_ok} sessions, {fmt.money_words(n.net_val)} in all "
                   f"({n.share * 100:.0f}% of the stock's traded value on those days).")
    if r.get("haka") is not None:
        out.append(f"Today buyers lifted the offer on {r['haka'] * 100:.0f}% of the volume whose aggressor is known.")
    if r.get("retail_div"):
        out.append(f"Retail versus foreign institutions today: {r['retail_div'].lower()}.")
    if r.get("wash"):
        out.append("The same houses sat on both sides (the wash flag), so today's concentration says little about one hand.")
    if r.get("crossing") is not None and r["crossing"] >= 0.5:
        out.append(f"{r['crossing'] * 100:.0f}% of today's value went through the negotiated market; today's broker numbers mostly describe that block.")
    ev = r.get("evidence")
    if ev and ev["state"] != "unknown":
        out.append(f"Evidence reading: {ev['text']} ({ev['agree']} of {ev['known']} accumulation signs that can be read today).")
    for t in tags:
        v = (verdicts or {}).get(t)
        cell = edge_h5.get(t)
        part = f"Research on {plain(t)} (all stocks, 2020-2026, after costs): {v.lower() if v else 'not researched yet'}"
        if cell and cell.get("n"):
            part += f"; past signals were up after 5 sessions in {cell['win']} of {cell['n']:,} cases (raw, before costs)"
        out.append(part + ".")
    out.append("This describes data. It is not a recommendation, and this combination of readings has not been tested.")
    return out
