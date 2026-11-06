"""Outcome v2: forward returns a holder could actually have earned (audit 2026-11-07, plan phase 1).

What v1 (`engine.backfill_outcomes`) gets wrong, and what this does instead:

  * v1 enters at the CLOSE of the signal day. The signal only exists after the close (EOD 19:15), so nobody can buy there.
    v2 enters at the VWAP of the next session (value / volume): buying through day t+1 is what an EOD signal allows. The
    `open` column cannot be used: IDX fills it on only 4-8% of bars before 2025 (user decision 2026-11-07).
  * v1 adjusts only for 'split:' records read straight from corp_actions; 64 of those 136 are placements IDX did not price
    as a split (+/-14% fake returns), and rights, bonus and stock dividends are not adjusted at all (FORU, rights on
    22-Sep-2026, theoretical price Rp131 after Rp2,560: v1 recorded -95.8% where holders gained).
    v2 takes the adjustment from IDX itself: on an ex-date IDX resets the reference price (`prev`) to the theoretical price,
    so prev_t / close_(t-1) is the factor for splits, reverse splits, bonus and rights alike (it differs from 1 by > 2% on
    corporate-action days only). Cash dividends are not in `prev`; their factor comes from `adj_factor` (kind 'dividend').
    Together that is a total-return basis.
  * v1's benchmark (`market_breadth`) is computed under other rules than the tag returns (it drops no-trade days at the
    exit, uses raw prices). v2's benchmark (`market_breadth2`) uses exactly the same formula as the tag returns.

Nothing in v1 is touched: v2 writes only `signal_log.ret2_*` and `market_breadth2`.

    adjusted return = exit close / (entry price x product of the day factors after the entry day up to the exit day) - 1

A missing price is a gap, never a guess: no trade on t+1 means no entry (ret2 stays NULL); after the stock's last bar there is
no exit; between bars the last close carries forward (a suspended stock is worth its last price, for tags and benchmark alike).
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")

REF_JUMP = 0.02                     # same threshold as `audit.REF_JUMP`: a larger prev-vs-close gap is a corporate action
VWAP_TOLERANCE = 0.02               # a VWAP more than 2% outside the day's low-high is a bad bar: no entry
LIQUID_VALUE_IDR = 5_000_000_000    # the engine's liquid cut (engine.LIQUID_VALUE_IDR)
HORIZONS = (1, 3, 5, 10)


# ---- pure (mirrors the SQL below; unit-tested) ------------------------------------------------------------------

def day_factor(prev: float | None, prev_close: float | None, div_factor: float | None = None) -> float:
    """The day's adjustment factor: IDX's reference-price reset (corporate actions) times the cash-dividend factor."""
    f = 1.0
    if prev and prev_close and prev > 0 and prev_close > 0 and abs(prev / prev_close - 1) > REF_JUMP:
        f = prev / prev_close
    if div_factor and div_factor > 0:
        f *= div_factor
    return f


def entry_vwap(value: float | None, volume: float | None, low: float | None, high: float | None) -> float | None:
    """VWAP of the entry day, or None when nobody traded or the bar is impossible."""
    if not value or not volume or volume <= 0 or value <= 0:
        return None
    v = value / volume
    if low and high and (v < low * (1 - VWAP_TOLERANCE) or v > high * (1 + VWAP_TOLERANCE)):
        return None
    return v


def adjusted_return(entry: float | None, exit_close: float | None, factors_after_entry: list[float]) -> float | None:
    """exit / (entry x product of the factors of the days after the entry day, up to and including the exit day) - 1."""
    if not entry or not exit_close or entry <= 0 or exit_close <= 0:
        return None
    return exit_close / (entry * math.prod(factors_after_entry)) - 1


# ---- database -----------------------------------------------------------------------------------------------------

# One row per (ticker, calendar day) between the stock's first and last bar: the close carried forward over gaps, and the
# running sum of ln(day factor). The factor product between two days is exp(cum[x] - cum[e]).
# The thresholds are module constants inlined with str.format (CREATE TABLE AS takes no bind parameters); never user input.
_GRID = """
CREATE TEMP TABLE o2_grid ON COMMIT DROP AS
WITH cal AS (SELECT session_date, row_number() OVER (ORDER BY session_date)::int AS rn FROM trading_calendar),
span AS (SELECT ticker, min(session_date) AS d0, max(session_date) AS d1 FROM price_bars GROUP BY ticker),
bars AS (
    SELECT ticker, session_date, close::float8 AS close, volume::float8 AS volume, value::float8 AS value,
           high::float8 AS high, low::float8 AS low,
           -- IDX rows only (source_tier 0): the Tier-3 fallback bars (vektorgauss_history, 14/15/25-Sep-2026) carry a `prev`
           -- already adjusted for corporate actions that happened LATER (FORU 14-Sep prev 190.36 = 3,720 x the 22-Sep rights
           -- factor), which would read as a corporate action and blow a return up by thousands of percent.
           CASE WHEN source_tier = 0 AND pc > 0 AND prev > 0 AND abs(prev / pc - 1) > {ref}
                THEN (prev / pc)::float8 ELSE 1.0 END AS f_ref
    FROM (SELECT *, lag(close) OVER (PARTITION BY ticker ORDER BY session_date) AS pc FROM price_bars) x),
divs AS (
    SELECT ticker, ex_date, exp(sum(ln(factor::float8))) AS f_div FROM adj_factor
    WHERE kind = 'dividend' AND factor > 0 GROUP BY ticker, ex_date),
grid AS (
    SELECT s.ticker, c.session_date, c.rn, b.close, b.volume, b.value, b.high, b.low,
           coalesce(b.f_ref, 1.0) * coalesce(d.f_div, 1.0) AS f
    FROM span s
    JOIN cal c ON c.session_date BETWEEN s.d0 AND s.d1
    LEFT JOIN bars b ON b.ticker = s.ticker AND b.session_date = c.session_date
    LEFT JOIN divs d ON d.ticker = s.ticker AND d.ex_date = c.session_date),
cum AS (
    SELECT *, sum(ln(f)) OVER (PARTITION BY ticker ORDER BY rn) AS cum,
              count(close) OVER (PARTITION BY ticker ORDER BY rn) AS grp
    FROM grid)
SELECT ticker, session_date, rn, volume, value, high, low, close,
       first_value(close) OVER (PARTITION BY ticker, grp ORDER BY rn) AS close_ff, cum,
       CASE WHEN volume > 0 AND value > 0
                 AND value / volume BETWEEN low * (1 - {tol}) AND high * (1 + {tol})
            THEN value / volume END AS vwap
FROM cum
"""
_GRID_INDEXES = ("CREATE INDEX ON o2_grid (ticker, rn)", "CREATE INDEX ON o2_grid (session_date)", "ANALYZE o2_grid")

# Shared by the tag returns and the benchmark, so both use exactly one formula. t0 = signal day, e = t+1 (entry), xN = t+N.
_RETURNS = """
    SELECT t0.ticker, t0.session_date, t0.value AS value0,
           x1.close_ff  / (e.vwap * exp(x1.cum  - e.cum)) - 1 AS r1,
           x3.close_ff  / (e.vwap * exp(x3.cum  - e.cum)) - 1 AS r3,
           x5.close_ff  / (e.vwap * exp(x5.cum  - e.cum)) - 1 AS r5,
           x10.close_ff / (e.vwap * exp(x10.cum - e.cum)) - 1 AS r10,
           CASE WHEN t0.close > 0 THEN x5.close_ff / (t0.close * exp(x5.cum - t0.cum)) - 1 END AS rc5
    FROM o2_grid t0
    JOIN o2_grid e        ON e.ticker   = t0.ticker AND e.rn   = t0.rn + 1
    LEFT JOIN o2_grid x1  ON x1.ticker  = t0.ticker AND x1.rn  = t0.rn + 1
    LEFT JOIN o2_grid x3  ON x3.ticker  = t0.ticker AND x3.rn  = t0.rn + 3
    LEFT JOIN o2_grid x5  ON x5.ticker  = t0.ticker AND x5.rn  = t0.rn + 5
    LEFT JOIN o2_grid x10 ON x10.ticker = t0.ticker AND x10.rn = t0.rn + 10
    WHERE t0.rn + 10 <= (SELECT max(rn) FROM o2_grid)
"""


def backfill(*, full: bool = False) -> dict:
    """Fill `signal_log.ret2_*` (rows not done yet, or every row with `full`) and rebuild `market_breadth2`.

    A row is done once its t+10 session exists, whether or not it got an entry (`ret2_at` is set either way), so a stock that
    did not trade on t+1 is not recomputed every night. Returns counts."""
    from .db import connect
    from .engine import _log

    params = {"liq": LIQUID_VALUE_IDR}
    pending = "" if full else "AND s.ret2_at IS NULL"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(_GRID.format(ref=float(REF_JUMP), tol=float(VWAP_TOLERANCE)))
        for stmt in _GRID_INDEXES:
            cur.execute(stmt)
        cur.execute(
            f"""
            WITH r AS ({_RETURNS})
            UPDATE signal_log s
            SET ret2_h1 = r.r1, ret2_h3 = r.r3, ret2_h5 = r.r5, ret2_h10 = r.r10, ret2c_h5 = r.rc5, ret2_at = now()
            FROM r
            WHERE s.ticker = r.ticker AND s.session_date = r.session_date {pending}
            """,
            params,
        )
        rows = cur.rowcount
        # Equal-weight benchmark, same formula: every stock that traded on the signal day. Rebuilt whole (cheap, and it keeps
        # the benchmark consistent with the factors as they stand today).
        cur.execute("TRUNCATE market_breadth2")
        cur.execute(
            f"""
            WITH r AS ({_RETURNS} AND t0.volume > 0 AND t0.close > 0)
            INSERT INTO market_breadth2 (session_date, n_all, ret2_h1_all, ret2_h3_all, ret2_h5_all, ret2_h10_all, hit2_h5_all,
                                         ret2c_h5_all, n_liquid, ret2_h5_liquid, hit2_h5_liquid)
            SELECT session_date, count(r5), avg(r1), avg(r3), avg(r5), avg(r10), avg((r5 > 0)::int)::float8, avg(rc5),
                   count(r5) FILTER (WHERE value0 > %(liq)s), avg(r5) FILTER (WHERE value0 > %(liq)s),
                   (avg((r5 > 0)::int) FILTER (WHERE value0 > %(liq)s))::float8
            FROM r GROUP BY session_date
            """,
            params,
        )
        days = cur.rowcount
        cur.execute("SELECT count(*), count(ret2_h5) FROM signal_log WHERE ret2_at IS NOT NULL")
        done, with_entry = cur.fetchone()
        _log(cur, "backfill_outcomes2", datetime.now(WIB).date(), "ok", rows, None)
    return {"updated": rows, "breadth_days": days, "done": done, "with_entry": with_entry}


def compare(since: date | None = None) -> list[dict]:
    """Per tag, the v1 and v2 numbers side by side: edge at 5 sessions vs the equal-weight universe, hit rate, and v2 on the
    liquid subset. Only rows that have BOTH outcomes, so the two columns describe the same signals."""
    from .db import connect

    params: dict = {"liq": LIQUID_VALUE_IDR}
    since_clause = ""
    if since is not None:
        since_clause = " AND s.session_date >= %(since)s"
        params["since"] = since
    with connect() as conn, conn.cursor() as cur:
        conn.read_only = True
        cur.execute(
            f"""
            SELECT s.tag, count(*) AS n,
                   avg(s.ret_h5 - m.ret_h5_all)                        AS edge1_h5,
                   avg((s.ret_h5 > 0)::int)::float8                     AS hit1,
                   avg(s.ret2_h5 - m2.ret2_h5_all)                     AS edge2_h5,
                   avg((s.ret2_h5 > 0)::int)::float8                    AS hit2,
                   avg(s.ret2c_h5 - m2.ret2c_h5_all)                   AS edge2c_h5,
                   count(*) FILTER (WHERE p.value > %(liq)s)           AS n_liquid,
                   avg(s.ret2_h5 - m2.ret2_h5_liquid) FILTER (WHERE p.value > %(liq)s) AS edge2_h5_liquid,
                   count(*) FILTER (WHERE abs(s.ret_h5 - s.ret2c_h5) > 0.05) AS n_moved
            FROM signal_log s
            JOIN market_breadth m USING (session_date)
            JOIN market_breadth2 m2 USING (session_date)
            JOIN price_bars p ON p.session_date = s.session_date AND p.ticker = s.ticker
            WHERE s.ret_h5 IS NOT NULL AND s.ret2_h5 IS NOT NULL{since_clause}
            GROUP BY s.tag ORDER BY n DESC
            """,
            params,
        )
        cols = [d[0] for d in cur.description]
        return [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in zip(cols, r, strict=True)} for r in cur.fetchall()]
