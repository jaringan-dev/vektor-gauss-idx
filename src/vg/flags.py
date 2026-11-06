"""Data-quality flags on signal_log rows (audit 2026-11-07, plan phase 2.4).

Each flag marks a signal whose INPUT was known to be broken AT THE TIME it was raised - so leaving those rows out of an
evaluation uses no hindsight. The rows themselves are never changed; `signal_flags` sits beside them and the evaluation shows
the edge with and without them.

  ksei_scripless_jump  KSEI-* (v1) tag whose 3-month KSEI window spans a month where the KSEI-registered share of all shares
                       moved >= 5 points (old shares deposited at KSEI: the percentages moved without any trading; SGER Oct-26)
  ksei_unpublished     KSEI-* (v1) tag that used a month-end file which could not have been public yet: month end within
                       KSEI_PUBLISH_LAG_DAYS of the session and stored only after the next open (in-sample recomputes)
  corp_action_window   FLOAT-ASING-PADAT (v1) within 10 calendar days after an IDX reference-price reset (a corporate action):
                       the listed-share denominator is changing (rights, split, bonus) and IDX updates it days late
  broker_copy          broker-based tag on a stock-day whose broker table is an exact copy of the previous day's
  fallback_bar         any tag on a stock-day whose price bar came from the Tier-3 fallback feed, not IDX

`rebuild()` recomputes the whole table in one SQL statement (minutes); `FLAGS_SQL` is the single definition.
"""

from __future__ import annotations

from .engine import KSEI_PUBLISH_LAG_DAYS, REF_JUMP, SCRIPLESS_JUMP

KSEI_TAGS = ("KSEI-INST-AKUM", "KSEI-RITEL-EXIT", "KSEI-KONFIRM", "KSEI-DIVERGEN")
BROKER_TAGS = ("BROKER-ASING-AKUM", "BROKER-ASING-DIST", "BROKER2-ASING-AKUM", "BROKER2-ASING-DIST", "BROKER-DOMISILI-KONFIRM",
               "BROKER-DOMISILI-DIVERGEN", "BROKER2-DOMISILI-KONFIRM", "BROKER2-DOMISILI-DIVERGEN", "FOREIGN-INST", "DOM-RETAIL",
               "FOREIGN-INST2", "DOM-RETAIL2", "BANDAR-CANDIDATE", "AKUM-NETRAL-WASH")
CORP_WINDOW_DAYS = 10


def _in(values) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"     # module constants only, never input


# One row per flagged signal. Built from signal_log; constants are inlined (CREATE TABLE AS takes no bind parameters).
FLAGS_SQL = f"""
WITH sig AS (SELECT session_date, ticker, tag FROM signal_log),
om AS (
    SELECT ticker, month_end, fetched_at,
           lead(month_end) OVER (PARTITION BY ticker ORDER BY month_end) AS next_month,
           max((detail->>'scripless_pct')::float8) OVER w - min((detail->>'scripless_pct')::float8) OVER w AS sp_range
    FROM ownership_monthly
    WINDOW w AS (PARTITION BY ticker ORDER BY month_end ROWS BETWEEN 3 PRECEDING AND CURRENT ROW)),
ksei AS (
    SELECT s.session_date, s.ticker, s.tag,
           (om.sp_range >= {SCRIPLESS_JUMP}) AS jump,
           (om.month_end + {KSEI_PUBLISH_LAG_DAYS} > s.session_date
              AND om.fetched_at > ((s.session_date + 1)::timestamp + interval '8 hours') AT TIME ZONE 'Asia/Jakarta') AS unpublished
    FROM sig s JOIN om ON om.ticker = s.ticker AND om.month_end <= s.session_date
                      AND (om.next_month IS NULL OR om.next_month > s.session_date)
    WHERE s.tag IN {_in(KSEI_TAGS)}),
refj AS (
    SELECT ticker, session_date FROM (
        SELECT ticker, session_date, prev, source_tier, lag(close) OVER (PARTITION BY ticker ORDER BY session_date) AS pc
        FROM price_bars) x
    WHERE source_tier = 0 AND pc > 0 AND prev > 0 AND abs(prev / pc - 1) > {REF_JUMP}),
corp AS (
    SELECT DISTINCT s.session_date, s.ticker, s.tag FROM sig s
    JOIN refj r ON r.ticker = s.ticker AND r.session_date <= s.session_date AND r.session_date > s.session_date - {CORP_WINDOW_DAYS}
    WHERE s.tag = 'FLOAT-ASING-PADAT'),
bh AS (
    SELECT ticker, session_date, h, lag(h) OVER (PARTITION BY ticker ORDER BY session_date) AS ph, bv FROM (
        SELECT ticker, session_date, sum(buy_val) AS bv,
               md5(string_agg(broker_code || ':' || buy_val || ':' || sell_val, ',' ORDER BY broker_code)) AS h
        FROM broker_flows GROUP BY ticker, session_date) y),
copy AS (
    SELECT s.session_date, s.ticker, s.tag FROM sig s
    JOIN bh ON bh.ticker = s.ticker AND bh.session_date = s.session_date AND bh.h = bh.ph AND bh.bv > 0
    WHERE s.tag IN {_in(BROKER_TAGS)}),
fb AS (
    SELECT s.session_date, s.ticker, s.tag FROM sig s
    JOIN price_bars p ON p.ticker = s.ticker AND p.session_date = s.session_date AND p.source_tier > 0),
allf AS (
    SELECT session_date, ticker, tag, 'ksei_scripless_jump' AS flag FROM ksei WHERE jump
    UNION ALL SELECT session_date, ticker, tag, 'ksei_unpublished' FROM ksei WHERE unpublished
    UNION ALL SELECT session_date, ticker, tag, 'corp_action_window' FROM corp
    UNION ALL SELECT session_date, ticker, tag, 'broker_copy' FROM copy
    UNION ALL SELECT session_date, ticker, tag, 'fallback_bar' FROM fb)
SELECT session_date, ticker, tag, array_agg(DISTINCT flag ORDER BY flag) AS flags
FROM allf GROUP BY session_date, ticker, tag
"""


def rebuild() -> dict:
    """Recompute signal_flags from scratch. Returns rows per flag."""
    from .db import connect
    from .engine import _log

    with connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE signal_flags")
        cur.execute(f"INSERT INTO signal_flags (session_date, ticker, tag, flags) {FLAGS_SQL}")
        n = cur.rowcount
        cur.execute("SELECT f, count(*) FROM signal_flags, unnest(flags) AS f GROUP BY f ORDER BY f")
        by_flag = dict(cur.fetchall())
        from datetime import datetime
        from zoneinfo import ZoneInfo

        _log(cur, "flag_signals", datetime.now(ZoneInfo("Asia/Jakarta")).date(), "ok", n, None)
    return {"flagged_rows": n, "by_flag": by_flag}
