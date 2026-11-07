"""Do the home-screen badges mean anything? A retrospective test of ⚡ vol/price compression and 📊 volume surge (`vg research-patterns`).

The badges are drawn by `web/patterns.py`. This module walks the WHOLE price history (2020 on) and marks every stock-day on which a
badge would have been drawn, with the very same functions (`compression`, `is_compression`, `volume_surge`, `is_surge`), so the test
and the screen cannot drift apart. The events go to `pattern_events` (rebuilt on every run) and are then measured by `research.py`,
the same machinery that judges the tags: outcome v2 (entry at the VWAP of t+1, total return), block-bootstrap intervals, size x
liquidity neutral edge, half-year folds, market regimes, the per-signal cost model, false-discovery control.

Patterns measured (names are research labels, not tags; nothing here fires a signal):
    P-COMPRESSION    the ⚡ badge
    P-SURGE          the 📊 badge
    P-SURGE-UP       📊 on a day the close was above the reference price
    P-SURGE-DOWN     📊 on a day the close was below it
    P-COMP+SURGE     both on the same day

No look-ahead: a day's badge uses bars up to and including that day; the return starts at the next session's VWAP. RETROSPECTIVE:
the thresholds were chosen on 6 Nov 2026 data before this test, but anything found here is in-sample and is not a forward test.

`events_for` is pure; `build` / `run` touch the database (build writes only `pattern_events`).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from .web import patterns as P

TAGS = ("P-COMPRESSION", "P-SURGE", "P-SURGE-UP", "P-SURGE-DOWN", "P-COMP+SURGE")
NEED = P.WINDOW + P.BASE + 1          # bars a compression reading needs; a surge needs BASE + 1

# The rows research.py reads instead of the tag rows: same columns as engine.EVAL_SOURCE needs (session_date, ticker, tag, ret2_h5).
SOURCE = """(
    SELECT e.session_date, e.ticker, e.tag, r.r5 AS ret2_h5
    FROM pattern_events e JOIN ret2_daily r USING (session_date, ticker)
)"""


def events_for(ticker: str, bars: list[tuple]) -> Iterable[tuple[date, str, str]]:
    """(session, ticker, pattern) for one stock. `bars`: (session, prev, high, low, close, volume, value, nonreg_value), oldest first."""
    for i in range(P.BASE, len(bars)):
        d, prev, _hi, _lo, close, _vol, value, _nr = bars[i]
        if not value:
            continue                                           # no trading that day: no badge can be drawn
        surge = P.is_surge(P.volume_surge([(b[0], b[6], b[7]) for b in bars[i - P.BASE:i + 1]]))
        comp = i + 1 >= NEED and P.is_compression(P.compression([b[:6] for b in bars[i + 1 - NEED:i + 1]]))
        if comp:
            yield d, ticker, "P-COMPRESSION"
        if surge:
            yield d, ticker, "P-SURGE"
            if prev and close:
                if close > prev:
                    yield d, ticker, "P-SURGE-UP"
                elif close < prev:
                    yield d, ticker, "P-SURGE-DOWN"
        if comp and surge:
            yield d, ticker, "P-COMP+SURGE"


def _f(v):
    return None if v is None else float(v)


def build() -> dict:
    """Rebuild `pattern_events` from the whole price history. Returns counts per pattern."""
    from .db import connect

    counts = dict.fromkeys(TAGS, 0)
    out: list[tuple] = []
    with connect() as conn:
        with conn.cursor(name="pattern_bars") as cur:            # server-side: ~1.5M bars are streamed, not loaded at once
            cur.itersize = 50_000
            cur.execute("SELECT ticker, session_date, prev, high, low, close, volume, value, nonreg_value FROM price_bars "
                        "ORDER BY ticker, session_date")
            current, bars = None, []
            for t, d, prev, hi, lo, close, vol, value, nonreg in cur:
                if t != current:
                    if bars:
                        out.extend(events_for(current, bars))
                    current, bars = t, []
                bars.append((d, _f(prev), _f(hi), _f(lo), _f(close), _f(vol), _f(value), _f(nonreg)))
            if bars:
                out.extend(events_for(current, bars))
        with conn.cursor() as cur:
            cur.execute("TRUNCATE pattern_events")
            cur.executemany("INSERT INTO pattern_events (session_date, ticker, tag) VALUES (%s, %s, %s)", out)
    for _d, _t, tag in out:
        counts[tag] += 1
    return counts


def run(*, reps: int = 2000, rebuild: bool = True) -> tuple[dict, dict | None]:
    """(research result, event counts or None when not rebuilt)."""
    from . import research

    counts = build() if rebuild else None
    return research.run(None, reps=reps, source=SOURCE), counts
