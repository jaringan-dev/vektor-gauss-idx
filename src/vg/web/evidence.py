"""One honest reading per stock for a 3-second scan: how many accumulation signs agree, and what warns against reading it.

The proposal behind this screen asked for "High Conviction / Wait & See / Avoid" badges. The owner chose this version instead
(2026-11-07): VG's own research found no edge in AKUM-ASING (3.5-year in-sample test) or in the volume-surge / compression badges
(`vg research-patterns`), and the dashboard describes, it never advises (narrative.FORBIDDEN). So the reading counts EVIDENCE that
already exists on the page and says plainly what has and has not been tested. Nothing new is computed and nothing is written.

Accumulation signs (True / False / None = not known for this stock today):
    bpi5      five-session BPI at least +BPI_PTS, with broker data on at least bpi_window.MIN_DAYS sessions
    buyer     the window's biggest buyer was in that day's top 3 buyers on at least BUYER_DAYS sessions
    net       a broker net bought on most sessions for a material amount (patterns.is_persistent)
    haka      buyers lifted the offer on at least home.HAKA_DOMINANT of the classified volume (order-flow stocks only)
    retail    retail brokers net selling while foreign institutions net buy (engine retail_div_state)
Warnings (any one turns the reading to "warning"):
    wash      AKUM-NETRAL-WASH: the same houses sit on both sides, so concentration means little
    crossing  most of the day's value went through the negotiated market (patterns.CROSSING_SHARE)
    dist      five-session BPI at most -BPI_PTS: the selling is the concentrated side

Thresholds are PROVISIONAL and written here so they can be argued with. `read` is pure.
"""

from __future__ import annotations

BPI_PTS = 0.10            # the engine's BPI_HIGH line (+10 points of the value), same as the wash rule and the gauge band
BUYER_DAYS = 3
AGREE_MIN = 3             # signs that must agree (with no warning) for the "agree" reading
HAKA_MIN = 0.55           # = home.HAKA_DOMINANT (a test keeps them equal)
RETAIL_SELL_INST_BUY = "retail_selling_institutions_buying"
WASH_TAG = "AKUM-NETRAL-WASH"

TESTED_NOTE = ("Not a signal: this combination has not been tested. Tested so far (2020-2026, 5 days, after costs): "
               "volume surge and compression showed no edge; BPI and broker net buying are not tested yet.")

SIGN_LABELS = {
    "bpi5": "Buying concentrated over 5 sessions",
    "buyer": "Same top buyer most days",
    "net": "A broker net buying most days",
    "haka": "Buyers lifting the offer",
    "retail": "Retail selling, foreign institutions buying",
}
WARN_LABELS = {"wash": "Wash: same houses on both sides", "crossing": "Crossing day", "dist": "Selling concentrated over 5 sessions"}
WARN_SHORT = {"wash": "wash", "crossing": "crossing day", "dist": "concentrated selling"}


def read(win=None, net=None, haka: float | None = None, retail_div: str | None = None, tags=(), crossing: float | None = None) -> dict:
    """`win`: bpi_window.WindowScore | None; `net`: patterns.NetBuyer | None; `haka`: HAKA share of classified volume | None;
    `retail_div`: engine retail_div_state | None; `tags`: today's tags; `crossing`: patterns.crossing_share | None.
    Returns the reading as a plain dict (see the module docstring)."""
    from .patterns import CROSSING_SHARE, is_persistent

    bpi_known = win is not None and win.bpi3 is not None
    signs = {
        "bpi5": (win.bpi3 >= BPI_PTS) if bpi_known else None,
        "buyer": (win.top_buyer_days >= BUYER_DAYS) if bpi_known else None,
        "net": (is_persistent(net) if net is not None and net.days_ok else None),
        "haka": (haka >= HAKA_MIN) if haka is not None else None,
        "retail": (retail_div == RETAIL_SELL_INST_BUY) if retail_div not in (None, "indeterminate") else None,
    }
    warnings = []
    if WASH_TAG in set(tags or ()):
        warnings.append("wash")
    if crossing is not None and crossing >= CROSSING_SHARE:
        warnings.append("crossing")
    if bpi_known and win.bpi3 <= -BPI_PTS:
        warnings.append("dist")
    known = [k for k, v in signs.items() if v is not None]
    agree = [k for k in known if signs[k]]
    if warnings:
        state = "warning"
    elif not known:
        state = "unknown"
    elif len(agree) >= AGREE_MIN:
        state = "agree"
    else:
        state = "mixed"
    if state == "unknown":
        text = "No broker data"
    elif warnings:
        text = "Warning: " + ", ".join(WARN_SHORT[w] for w in warnings)
    else:
        text = f"{len(agree)} of {len(known)} agree"
    lines = [f"{'yes' if signs[k] else 'no'}: {SIGN_LABELS[k]}" for k in known]
    lines += [f"unknown: {SIGN_LABELS[k]}" for k in signs if signs[k] is None]
    lines += [f"warning: {WARN_LABELS[w]}" for w in warnings]
    return {"agree": len(agree), "known": len(known), "state": state, "text": text, "signs": signs, "warnings": warnings,
            "agree_keys": agree, "title": " · ".join(lines) + ". " + TESTED_NOTE}
