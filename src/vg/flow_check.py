"""Can the free running-trade recorder replace done-details? (`vg compare-flow`)

The arjum quota drops to 1,000 calls a day around 6 Dec 2026, and done-details (HAKA/HAKI per price) costs ~1,000 calls a day for just
the 5 watchlist stocks. The WebSocket recorder (`tradeflow.py`, no quota) sees every trade of ~810 stocks with a `c` = "buy" / "sell"
label, but that label is DOCUMENTED as the aggressive side and was never checked. This module checks it, per stock and day:

    buy share, recorder   = buy lots / (buy + sell lots)          from trade_flow_1m
    buy share, done       = HAKA qty / (HAKA + HAKI qty)          from done_by_price (the feed's tick-rule aggressor)

If `c` is the aggressor, the two shares agree within a few points. If `c` is the other side, recorder ~= 1 - done. Anything else means
the label is something different again.

Only COMPLETE stock-days are judged: both sources must cover at least MIN_COVERAGE of the day's regular-board volume. Done-details stops
at 200 pages (20,000 trades: BBCA/BBRI/TLKM on 6 Nov 2026 were cut), and the recorder misses the seconds it is disconnected or a day it
started late (6 Nov 2026: it ran from mid-afternoon only). A partial day compares two different slices of the session, so it is listed
with its coverage but never counted.

`compare` and `verdict` are pure; `load` is SELECT only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

MIN_COVERAGE = 0.95     # of the day's regular-board volume, for each source
AGREE_PP = 0.03         # |recorder - done| buy share within 3 points = agrees
MIN_JUDGED = 10         # complete stock-days needed before a verdict


@dataclass(frozen=True, slots=True)
class Row:
    day: date
    ticker: str
    reg_volume: float           # regular-board shares (negotiated taken out, see web/patterns.regular_value)
    done_cov: float | None      # done-details shares / reg_volume
    rec_cov: float | None       # recorder shares / reg_volume
    done_buy: float | None      # HAKA share per done-details
    rec_buy: float | None       # buy share per the recorder's `c`
    status: str                 # "agree" | "inverted" | "differs" | "partial: ..." | "missing: ..."

    @property
    def judged(self) -> bool:
        return self.status in ("agree", "inverted", "differs")


def _share(a: float, b: float) -> float | None:
    return a / (a + b) if a + b > 0 else None


def _cov(part: float, whole: float) -> float | None:
    return part / whole if whole > 0 else None


def compare(day: date, ticker: str, volume: float | None, nonreg_volume: float | None,
            done: tuple[float, float, float] | None, rec: tuple[float, float] | None,
            freq: tuple[float | None, float | None, float | None] = (None, None, None)) -> Row:
    """`done`: (qty lots, HAKA lots, HAKI lots), REGULAR board only; `rec`: (buy lots, sell lots). Volumes in shares; 1 lot = 100 shares.
    `freq`: (regular-board trades of the day, done-details trades, recorder trades). Coverage is the LOWER of volume and trade-count
    coverage: BBCA 6 Nov 2026 had 97% of its regular volume in done-details but only 19,984 of ~32,000 trades (the 200-page cap)."""
    v, n = volume or 0.0, nonreg_volume or 0.0
    reg = v - n if 0 < n <= v else v
    if done is None or rec is None:
        missing = " and ".join(s for s, x in (("done-details", done), ("recorder", rec)) if x is None)
        return Row(day, ticker, reg, None, None, None, None, f"missing: {missing}")
    qty, haka, haki = done
    bl, sl = rec
    reg_trades, done_trades, rec_trades = freq
    done_cov = _cov(qty * 100, reg)
    rec_cov = _cov((bl + sl) * 100, reg)
    if reg_trades:
        if done_cov is not None and done_trades is not None:
            done_cov = min(done_cov, done_trades / reg_trades)
        if rec_cov is not None and rec_trades is not None:
            rec_cov = min(rec_cov, rec_trades / reg_trades)
    d_buy, r_buy = _share(haka, haki), _share(bl, sl)
    short = [f"{name} {cov * 100:.0f}%" for name, cov in (("done-details", done_cov), ("recorder", rec_cov))
             if cov is None or cov < MIN_COVERAGE]
    if short or d_buy is None or r_buy is None:
        return Row(day, ticker, reg, done_cov, rec_cov, d_buy, r_buy, "partial: " + (", ".join(short) or "no aggressor split"))
    if abs(r_buy - d_buy) <= AGREE_PP:
        status = "agree"
    elif abs(r_buy - (1 - d_buy)) <= AGREE_PP:
        status = "inverted"
    else:
        status = "differs"
    return Row(day, ticker, reg, done_cov, rec_cov, d_buy, r_buy, status)


def verdict(rows: list[Row]) -> tuple[str, str]:
    """(short verdict, one line of evidence) over the judged rows."""
    judged = [r for r in rows if r.judged]
    if len(judged) < MIN_JUDGED:
        return "not enough data", f"{len(judged)} complete stock-days (need {MIN_JUDGED}); {len(rows) - len(judged)} partial or missing"
    agree = sum(r.status == "agree" for r in judged)
    inv = sum(r.status == "inverted" for r in judged)
    gap = sum(abs(r.rec_buy - r.done_buy) for r in judged) / len(judged)
    ev = f"{agree} agree, {inv} inverted, {len(judged) - agree - inv} differ of {len(judged)} complete stock-days; mean gap {gap * 100:.1f} pts"
    if agree >= 0.9 * len(judged):
        return "recorder can replace done-details", ev
    if inv >= 0.9 * len(judged):
        return "recorder side is INVERTED (fixable: flip it)", ev
    return "recorder does NOT match done-details", ev


def load(cur, since: date, until: date | None = None) -> list[Row]:
    """Every stock-day in [since, until] that has done-details OR recorder data for a stock that has done-details at all. SELECT only."""
    until = until or date(9999, 12, 31)
    cur.execute(
        """
        -- done-details also carries the negotiated board (NG: BBCA 6 Nov 2026, 426,018 lots); the recorder and the regular volume do not
        WITH d AS (SELECT session_date, ticker, sum(qty) q, sum(buy_aggressor_qty) h, sum(sell_aggressor_qty) s, sum(trades) n
                   FROM done_by_price WHERE session_date BETWEEN %(a)s AND %(b)s AND board = 'RG' GROUP BY 1, 2),
             r AS (SELECT session_date, ticker, sum(buy_lot) bl, sum(sell_lot) sl, sum(n_buy + n_sell) n
                   FROM trade_flow_1m WHERE session_date BETWEEN %(a)s AND %(b)s
                     AND ticker IN (SELECT DISTINCT ticker FROM done_by_price) GROUP BY 1, 2),
             k AS (SELECT session_date, ticker FROM d UNION SELECT session_date, ticker FROM r)
        SELECT k.session_date, k.ticker, p.volume, p.nonreg_volume,
               p.frequency - coalesce(p.nonreg_frequency, 0), d.q, d.h, d.s, d.n, r.bl, r.sl, r.n
        FROM k LEFT JOIN d USING (session_date, ticker) LEFT JOIN r USING (session_date, ticker)
        LEFT JOIN price_bars p USING (session_date, ticker)
        ORDER BY 1, 2
        """, {"a": since, "b": until})
    f = (lambda x: None if x is None else float(x))
    return [compare(day, t, f(vol), f(nv), (f(q), f(h) or 0.0, f(s) or 0.0) if q is not None else None,
                    (f(bl), f(sl)) if bl is not None else None, (f(rf), f(dn), f(rn)))
            for day, t, vol, nv, rf, q, h, s, dn, bl, sl, rn in cur.fetchall()]


def format_text(rows: list[Row]) -> str:
    def pct(x):
        return "  n/a" if x is None else f"{x * 100:5.1f}"

    lines = [f"{'day':10} {'stock':6} {'done cov':>8} {'rec cov':>8} {'HAKA% done':>10} {'buy% rec':>9}  status"]
    for r in rows:
        lines.append(f"{r.day.isoformat():10} {r.ticker:6} {pct(r.done_cov):>8} {pct(r.rec_cov):>8} {pct(r.done_buy):>10} {pct(r.rec_buy):>9}  {r.status}")
    v, ev = verdict(rows)
    lines += ["", f"VERDICT: {v}", f"         {ev}"]
    return "\n".join(lines)
