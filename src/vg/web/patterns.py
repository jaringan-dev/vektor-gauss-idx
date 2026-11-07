"""Descriptive multi-day patterns for the home screen badges: volume-price compression and a broker's persistent net buying.

    ⚡ Vol/price compression   price held in a narrow band while volume ran well above its own normal (price_bars)
    🏦 <broker> net buy N days the same broker was a net buyer on most sessions of the window (broker_flows)

Descriptive only, like bpi_window.py: not tags, no forward record, nothing written. They are shown as neutral badges with the
numbers in the tooltip, never in the signal colours, because no forward test has said they mean anything yet.

Honesty rules built in:
  * Compression is not computed across a corporate-action day. On such a day IDX resets the reference price (`prev` differs from the
    previous close), so a high-low range spanning it mixes two price scales (a split looks like a crash). Those rows say why instead.
  * The broker table lists each day's top-10 net buyers and top-10 net sellers. A broker missing from a day's list was neither, as far
    as we know, so that day does NOT count as a net-buy day (conservative). Sessions with no broker table at all are not counted either
    way and the badge needs data on at least MIN_DAYS of them.

`load` is the database part (SELECT only); everything else is pure.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

WINDOW = 5            # sessions the pattern is read over, ending at the screen's session
BASE = 20             # sessions before the window that define the stock's own "normal" volume
MIN_DAYS = 4          # sessions with broker data needed before a net-buy badge is shown
CA_TOLERANCE = 0.01   # |prev / previous close - 1| above this = a corporate-action reset of the reference price

# ---- compression thresholds -------------------------------------------------------------------------------------
# PROVISIONAL, to be set by the operator (see `is_compression`). Starting points, not research results:
COMP_MAX_RANGE = 0.06     # 5-session high-low range at most 6% of the close
COMP_MAX_MOVE = 0.02      # and the close moved at most 2% over the window (the engine's own "sideways" band, PRICE_FLAT_BAND)
COMP_MIN_VOL_X = 1.5      # while average volume was at least 1.5x its own 20-session average
NET_MIN_DAYS = 4          # a broker net buying on at least 4 sessions of the window
# ...and for an amount that matters: its summed net is at least this share of the stock's traded value over the window.
# Without it the badge is noise: on 6 Nov 2026, 121 of 173 stocks had SOME broker net buying 4+ of 5 days (one by Rp0.0B);
# with 10% it is 37.
NET_MIN_SHARE = 0.10
# ---- volume surge ---------------------------------------------------------------------------------------------------
SURGE_MIN_X = 2.0         # regular-market value at least 2x its median over the BASE sessions before (6 Nov 2026: 42 of 374 stocks)
SURGE_MIN_VALUE = 1e9     # and at least Rp1B on the day: a quiet stock going from Rp20M to Rp60M is not "busy"


@dataclass(frozen=True, slots=True)
class Compression:
    range_pct: float | None    # (highest high - lowest low) / last close over the window
    move_pct: float | None     # last close / close before the window - 1
    vol_x: float | None        # average volume in the window / average volume over the BASE sessions before it
    reason: str = ""           # why the numbers are missing (short history, corporate action, no trading)


@dataclass(frozen=True, slots=True)
class NetBuyer:
    broker: str | None         # the broker with the most net-buy sessions (ties: biggest summed net value)
    days: int                  # sessions on which it was a net buyer
    days_ok: int               # sessions of the window that have a broker table at all
    net_val: float             # its summed net value over the window, rupiah (net sells subtract)
    n_window: int
    share: float | None = None  # net_val / the stock's traded value over the window's sessions with broker data


def _f(v):
    return None if v is None else float(v)


def compression(bars: list[tuple]) -> Compression:
    """`bars`: (session, prev, high, low, close, volume) oldest first, the window's sessions LAST. Needs WINDOW + BASE + 1 bars."""
    need = WINDOW + BASE + 1
    if len(bars) < need:
        return Compression(None, None, None, f"only {len(bars)} sessions of prices")
    bars = bars[-need:]
    # A reference-price reset anywhere in the 26 sessions breaks the range (two price scales) or the volume base (a split
    # multiplies the share count), so neither number is computed across one.
    for (_d0, _p0, _h0, _l0, c0, _v0), (_d1, p1, _h1, _l1, _c1, _v1) in pairwise(bars):
        if c0 and p1 and abs(p1 / c0 - 1) > CA_TOLERANCE:
            return Compression(None, None, None, "corporate action in the last 26 sessions")
    base, win = bars[1:BASE + 1], bars[BASE + 1:]
    traded = [b for b in win if (b[5] or 0) > 0 and (b[2] or 0) > 0 and (b[3] or 0) > 0]
    if len(traded) < WINDOW or not win[-1][4]:
        return Compression(None, None, None, "did not trade on every session of the window")
    base_vol = sum((b[5] or 0) for b in base) / BASE
    rng = (max(b[2] for b in win) - min(b[3] for b in win)) / win[-1][4]
    before = bars[BASE][4]
    move = (win[-1][4] / before - 1) if before else None
    vol_x = (sum(b[5] for b in win) / WINDOW) / base_vol if base_vol > 0 else None
    return Compression(rng, move, vol_x)


def regular_value(value: float | None, nonreg: float | None) -> float:
    """Traded value on the regular board. IDX's `value` sometimes INCLUDES the negotiated market (NATO 6 Nov 2026: Rp235.9B of which
    Rp234.4B was one negotiated trade) and sometimes clearly does not (BYAN 7 Oct 2026: value Rp88B, negotiated Rp26,098B). So: when the
    negotiated part fits inside `value`, take it out; when it is bigger, `value` was already the regular part."""
    v, n = value or 0.0, nonreg or 0.0
    return v - n if 0 < n <= v else v


@dataclass(frozen=True, slots=True)
class Surge:
    x: float | None            # regular value on the day / median regular value over the BASE sessions before
    value: float               # regular value on the day, rupiah


def volume_surge(bars: list[tuple]) -> Surge:
    """`bars`: (session, value, nonreg_value) oldest first, the day LAST. Value in rupiah, not shares, so a split cannot fake a surge.
    The median (not the mean) of the base, so one earlier busy day does not hide today's."""
    if len(bars) < BASE + 1:
        return Surge(None, 0.0)
    regs = [regular_value(v, n) for _d, v, n in bars[-(BASE + 1):]]
    base = sorted(regs[:-1])
    mid = (base[BASE // 2 - 1] + base[BASE // 2]) / 2 if BASE % 2 == 0 else base[BASE // 2]
    return Surge(regs[-1] / mid if mid > 0 else None, regs[-1])


def is_surge(s: Surge) -> bool:
    return s.x is not None and s.x >= SURGE_MIN_X and s.value >= SURGE_MIN_VALUE


def is_compression(c: Compression) -> bool:
    """The ⚡ badge rule. PROVISIONAL thresholds above; the operator decides the final ones (a tighter range, a bigger volume
    multiple, whether the move band should scale with the stock's own volatility)."""
    return (c.range_pct is not None and c.move_pct is not None and c.vol_x is not None
            and c.range_pct <= COMP_MAX_RANGE and abs(c.move_pct) <= COMP_MAX_MOVE and c.vol_x >= COMP_MIN_VOL_X)


def persistent_net_buyer(days: dict[date, dict[str, float]], n_window: int = WINDOW,
                         values: dict[date, float] | None = None) -> NetBuyer:
    """`days`: {session: {broker: net value}} for ONE ticker; only sessions that HAVE a broker table are keys.
    `values`: {session: traded value}; the share is taken over the sessions in `days` only (same base as the net)."""
    count: dict[str, int] = defaultdict(int)
    total: dict[str, float] = defaultdict(float)
    for flows in days.values():
        for code, net in flows.items():
            total[code] += net
            if net > 0:
                count[code] += 1
    if not count:
        return NetBuyer(None, 0, len(days), 0.0, n_window)
    best = max(count, key=lambda c: (count[c], total[c], c))
    base = sum((values or {}).get(d) or 0.0 for d in days)
    return NetBuyer(best, count[best], len(days), total[best], n_window, total[best] / base if base > 0 else None)


def is_persistent(n: NetBuyer) -> bool:
    return (n.broker is not None and n.days_ok >= MIN_DAYS and n.days >= NET_MIN_DAYS and n.net_val > 0
            and n.share is not None and n.share >= NET_MIN_SHARE)


def load(cur, session_date: date, tickers: list[str]) -> dict[str, dict]:
    """{ticker: {"comp": Compression, "net": NetBuyer}}. Three SELECTs for all tickers together."""
    if not tickers:
        return {}
    cur.execute("SELECT session_date FROM trading_calendar WHERE session_date <= %s ORDER BY session_date DESC LIMIT %s",
                (session_date, WINDOW + BASE + 1))
    cal = sorted(r[0] for r in cur.fetchall())
    if not cal:
        return {}
    window = cal[-WINDOW:]
    cur.execute("SELECT ticker, session_date, prev, high, low, close, volume, value, nonreg_value FROM price_bars "
                "WHERE session_date = ANY(%s) AND ticker = ANY(%s) ORDER BY ticker, session_date", (cal, tickers))
    bars: dict[str, list] = defaultdict(list)
    for t, d, prev, hi, lo, close, vol, value, nonreg in cur.fetchall():
        bars[t].append((d, _f(prev), _f(hi), _f(lo), _f(close), _f(vol), _f(value), _f(nonreg)))
    cur.execute("SELECT ticker, session_date, broker_code, sum(net_val) FROM broker_flows "
                "WHERE session_date = ANY(%s) AND ticker = ANY(%s) GROUP BY 1, 2, 3", (window, tickers))
    flows: dict[str, dict[date, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for t, d, code, net in cur.fetchall():
        flows[t][d][code] = _f(net) or 0.0
    values = {t: {b[0]: (b[6] or 0.0) for b in bs} for t, bs in bars.items()}
    return {t: {"comp": compression([b[:6] for b in bars.get(t, [])]),
                # a surge is about the screen's session: only if the stock has a bar ON that day
                "surge": (volume_surge([(b[0], b[6], b[7]) for b in bars[t]])
                          if bars.get(t) and bars[t][-1][0] == session_date else Surge(None, 0.0)),
                "net": persistent_net_buyer(flows.get(t, {}), len(window), values.get(t))} for t in tickers}


def _bn(x: float) -> str:
    return f"Rp{x / 1e9:,.1f}B"


# What the retrospective test (`vg research-patterns`, 2020-01-02..2026-10-23, 5-day outcome v2, entry at the next session's VWAP)
# found, shown in every tooltip so a badge is never read as a buy signal. Update these lines when the test is re-run.
TESTED = {
    "surge": "Tested 2020-2026 (122k cases): no edge after costs; vs similar stocks +0.08% in 5 days, not significant.",
    "comp": "Tested 2020-2026 (30k cases): no edge; vs similar stocks -0.02% in 5 days. "
            "With a volume surge on the same day: +0.42% vs similar stocks, still below costs and not steady over time.",
    "net": "Not tested yet: the broker history needed for the test is still being filled.",
}


def badges(p: dict | None) -> list[dict]:
    """The badges for one row: [{"icon", "text", "title"}], in a fixed order. Empty when nothing qualifies."""
    if not p:
        return []
    out = []
    c, n, s = p["comp"], p["net"], p.get("surge")
    if s is not None and is_surge(s):
        out.append({"key": "surge", "icon": "📊", "text": f"Volume {s.x:.1f}x usual",
                    "title": f"Regular-market value today {_bn(s.value)} = {s.x:.1f}x its median over the previous {BASE} sessions "
                             f"(negotiated-market trades left out). {TESTED['surge']}"})
    if is_compression(c):
        out.append({"key": "comp", "icon": "⚡", "text": "Vol/price compression",
                    "title": f"Last {WINDOW} sessions: high-low range {c.range_pct * 100:.1f}% of the close, close moved {c.move_pct * 100:+.1f}%, "
                             f"volume {c.vol_x:.1f}x its {BASE}-session average. {TESTED['comp']}"})
    if is_persistent(n):
        out.append({"key": "net", "icon": "🏦", "text": f"{n.broker} net buy {n.days} days",
                    "title": f"{n.broker} was a net buyer on {n.days} of the {n.days_ok} sessions with broker data (window {n.n_window}), "
                             f"{_bn(n.net_val)} net in all = {n.share * 100:.0f}% of the stock's traded value on those sessions. "
                             f"Days it was not in the top-10 lists are not counted. {TESTED['net']}"})
    return out
