"""Five-session Bandar Power Index for the home screen: is the buying concentrated over several days, by the same hands?

A one-day BPI says only that the buying was more concentrated than the selling on that day. One fund rebalancing, one crossing,
or a different broker on top every day all look the same in a single day. This module answers the multi-day question with the
engine's own formula (`engine.scoring.bandar_power`), applied to the window's ladders ADDED UP per broker:

    bpi3_5d      = BPI of the summed ladders over the window's sessions with data (top 3 buyers' share - top 3 sellers' share
                   of the summed traded value). A broker buying 8% every day rises to the top here even if no single day shows it;
                   five different brokers each topping one day do not.
    days_up      = sessions of the window whose own BPI was positive
    same_buyer   = the window's top buyer, and on how many sessions it was among that day's top 3 buyers
    days_ok      = sessions of the window that have both ladders and a traded value (coverage)

Descriptive only: this is not a tag and has no forward record. Nothing is written. A row with fewer than MIN_DAYS sessions of
data gets no 5-day number (it would be a 2-day number wearing a 5-day label); it shows how many sessions it has instead.

Caveat: the feed's ladders are top-N per side, so a broker below the cut on a day counts 0 for that day in the sum.

`load` is the database part (SELECT only); `window_score` and `is_confirmed` are pure.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from ..engine.scoring import bandar_power

WINDOW = 5          # sessions, ending at (and including) the screen's session
MIN_DAYS = 4        # sessions with data needed before a 5-day number is shown


@dataclass(frozen=True, slots=True)
class WindowScore:
    n_window: int                 # sessions in the window (WINDOW, fewer only at the very start of the calendar)
    days_ok: int                  # sessions with both ladders and a traded value
    bpi3: float | None            # BPI of the summed ladders; None when days_ok < MIN_DAYS or the sum is unusable
    overlap3: int | None          # brokers in both summed top-3 lists (crossing / market making over the window)
    days_up: int                  # sessions whose own BPI was > 0
    top_buyer: str | None         # the summed ladder's biggest buyer
    top_buyer_days: int           # sessions on which that broker was among the day's top 3 buyers
    n_crossing: int = 0           # sessions in the sum whose value was mostly negotiated-market trades (they dominate the summed ladders)


def _f(v):
    return None if v is None else float(v)


def window_score(days: dict[date, tuple[list, list, float | None]], n_window: int = WINDOW, min_days: int = MIN_DAYS,
                 crossing: frozenset[date] = frozenset()) -> WindowScore:
    """`days`: {session: (buy ladder, sell ladder, traded value)} for ONE ticker over the window; ladders are (broker, gross value).
    `crossing`: sessions that were mostly negotiated-market trades. They stay in the sum (a block buyer may really be accumulating)
    but are counted, so the cell can say so."""
    usable = {d: (b, s, v) for d, (b, s, v) in days.items() if b and s and v and v > 0}
    daily = {d: bandar_power(b, s, v) for d, (b, s, v) in usable.items()}
    daily = {d: bp for d, bp in daily.items() if bp is not None}
    buy_sum: dict[str, float] = defaultdict(float)
    sell_sum: dict[str, float] = defaultdict(float)
    for d in daily:
        b, s, _v = usable[d]
        for code, val in b:
            buy_sum[code] += val or 0.0
        for code, val in s:
            sell_sum[code] += val or 0.0
    total = sum(usable[d][2] for d in daily)
    days_up = sum(bp.bpi3 > 0 for bp in daily.values())
    agg = bandar_power(buy_sum.items(), sell_sum.items(), total) if len(daily) >= min_days else None
    if agg is None:
        return WindowScore(n_window, len(daily), None, None, days_up, None, 0)

    def top3_buyers(d: date) -> set[str]:
        ranked = sorted(((c, v or 0.0) for c, v in usable[d][0]), key=lambda x: x[1], reverse=True)
        return {c for c, _v in ranked[:3]}

    persist = sum(agg.top_buyer in top3_buyers(d) for d in daily)
    return WindowScore(n_window, len(daily), agg.bpi3, agg.overlap3, days_up, agg.top_buyer, persist,
                       sum(d in crossing for d in daily))


def is_confirmed(w: WindowScore) -> bool | None:
    """Does this window read as accumulation by the same hands, not one lucky day? None = no rule decided yet (no badge drawn).

    TODO(user): the rule is yours to set from how you read IDX bandarmology. Fields available on `w`:
      w.bpi3 (points as a fraction, 0.10 = +10 pts; None when coverage is short), w.days_ok, w.days_up,
      w.top_buyer_days, w.overlap3, w.n_window.
    Things to weigh: how strong the 5-day BPI must be (engine.BPI_HIGH = 0.10 is the daily wash line), how many up days out of
    the window, how persistent the top buyer must be, and whether a high overlap3 (same houses on both sides) should veto it.
    """
    return None


def load(cur, session_date: date, tickers: list[str], n: int = WINDOW) -> dict[str, WindowScore]:
    """{ticker: WindowScore} for the `n` trading sessions ending at `session_date`. Two SELECTs for all tickers together."""
    if not tickers:
        return {}
    cur.execute("SELECT session_date FROM trading_calendar WHERE session_date <= %s ORDER BY session_date DESC LIMIT %s",
                (session_date, n))
    window = [r[0] for r in cur.fetchall()]
    if not window:
        return {}
    per: dict[str, dict[date, list]] = defaultdict(lambda: defaultdict(lambda: [[], [], None]))
    cur.execute("SELECT ticker, session_date, side, broker_code, val FROM broker_levels "
                "WHERE session_date = ANY(%s) AND ticker = ANY(%s)", (window, tickers))
    for t, d, side, code, val in cur.fetchall():
        per[t][d][0 if side == "B" else 1].append((code, _f(val)))
    if not per:
        return {}
    from .patterns import CROSSING_SHARE, crossing_share

    cur.execute("SELECT ticker, session_date, value, nonreg_value FROM price_bars WHERE session_date = ANY(%s) AND ticker = ANY(%s)",
                (window, tickers))
    cross: dict[str, set] = defaultdict(set)
    for t, d, value, nonreg in cur.fetchall():
        if t in per and d in per[t]:
            per[t][d][2] = _f(value)
            share = crossing_share(_f(value), _f(nonreg))
            if share is not None and share >= CROSSING_SHARE:
                cross[t].add(d)
    return {t: window_score({d: tuple(x) for d, x in per[t].items()}, n_window=len(window), crossing=frozenset(cross[t]))
            for t in tickers if t in per}
