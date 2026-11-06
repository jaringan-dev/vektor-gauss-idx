"""Data-integrity audit: does the data the engine measures with actually say what we think it says?

Born from the 2026-11-07 audit, which found returns that ignored rights issues (FORU: -95.8% recorded where holders gained),
corporate actions misread as splits, a "free float" column that is the listed share count, broker measures that only see the
buy side, and KSEI percentages computed on a base that jumps when old shares are deposited. Each of those is a check here, so
it cannot come back silently.

Same shape as `status.py`:
  * `gather` runs read-only queries (the session is set read-only) and never raises: a check it cannot run becomes a WARN;
  * `build_findings` is pure: facts in, a list of ok/warn/bad findings out (unit-tested without a database).

`vg audit-data` prints the findings and exits 1 when any is BAD, so it can gate a script.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from .status import BAD, OK, WARN, Check, worst

WIB = ZoneInfo("Asia/Jakarta")

# A day whose IDX reference price (`prev`) differs from the previous close by more than this is a corporate-action day:
# IDX resets `prev` to the theoretical price on an ex-date (split, bonus, rights). Ordinary days match to the rupiah.
REF_JUMP = 0.02
SCRIPLESS_JUMP = 0.05          # KSEI scripless share of all shares moving this much in a month = shares deposited, not traded
EXTREME_RET = -0.40            # a 5-day loss beyond this is almost always a corporate action the return did not adjust for
ADJ_STALE_DAYS = 7
SINCE = date(2025, 1, 1)


def _q(cur, sql: str, params: dict | None = None):
    cur.execute(sql, params or {})
    return cur.fetchone()


def _facts(cur, since: date) -> dict:
    p = {"since": since, "ref": REF_JUMP, "jump": SCRIPLESS_JUMP, "extreme": EXTREME_RET}
    f: dict = {"since": since.isoformat()}

    # Corporate-action days as IDX sees them, and whether our adjustment tables know about them.
    f["ref_days"], f["ref_days_unknown"], f["ref_days_shares_lag"] = _q(cur, """
        WITH b AS (
          SELECT ticker, session_date, prev, listed_shares,
                 lag(close)         OVER (PARTITION BY ticker ORDER BY session_date) AS pc,
                 lag(listed_shares) OVER (PARTITION BY ticker ORDER BY session_date) AS pls
          FROM price_bars WHERE session_date >= %(since)s::date - 10),
        d AS (SELECT * FROM b WHERE session_date >= %(since)s AND pc > 0 AND prev > 0 AND abs(prev / pc - 1) > %(ref)s)
        SELECT count(*),
               count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM adj_factor a WHERE a.ticker = d.ticker AND a.kind IN ('split', 'bonus')
                     AND a.ex_date BETWEEN d.session_date - 3 AND d.session_date + 3)),
               count(*) FILTER (WHERE prev < pc AND listed_shares = pls)
        FROM d""", p)

    # "Splits" derived from a listed-share jump that IDX did not price as a split (placements, conversions, buybacks).
    f["splits"], f["splits_not_priced"] = _q(cur, """
        WITH s AS (SELECT ticker, ex_date FROM corp_actions WHERE type = 'share_count_change' AND notes LIKE 'split:%%'),
        r AS (SELECT s.ticker, s.ex_date,
                     (SELECT b.prev / NULLIF(b.pc, 0) FROM (
                        SELECT session_date, prev, lag(close) OVER (ORDER BY session_date) AS pc FROM price_bars
                        WHERE ticker = s.ticker AND session_date BETWEEN s.ex_date - 10 AND s.ex_date) b
                      WHERE b.session_date = s.ex_date) AS f
              FROM s)
        SELECT count(*), count(*) FILTER (WHERE f IS NOT NULL AND abs(f - 1) <= %(ref)s) FROM r""", p)

    f["bars_latest"], f["tradable_eq_listed"] = _q(cur, """
        SELECT count(*), count(*) FILTER (WHERE tradable_shares = listed_shares) FROM price_bars
        WHERE session_date = (SELECT max(session_date) FROM price_bars) AND listed_shares > 0""")

    f["scripless_month"], f["scripless_tickers"], f["scripless_jumps"] = _q(cur, """
        WITH m AS (SELECT ticker, month_end, (detail->>'scripless_pct')::numeric AS sp,
                          lag((detail->>'scripless_pct')::numeric) OVER (PARTITION BY ticker ORDER BY month_end) AS prev_sp
                   FROM ownership_monthly WHERE detail->>'scripless_pct' IS NOT NULL)
        SELECT max(month_end)::text, count(*) FILTER (WHERE month_end = (SELECT max(month_end) FROM m)),
               count(*) FILTER (WHERE month_end = (SELECT max(month_end) FROM m) AND abs(sp - prev_sp) >= %(jump)s)
        FROM m""", p)

    f["broker_days_30d"], f["broker_copies_30d"] = _q(cur, """
        WITH d AS (SELECT ticker, session_date, sum(buy_val) AS bv,
                          md5(string_agg(broker_code || ':' || buy_val || ':' || sell_val, ',' ORDER BY broker_code)) AS h
                   FROM broker_flows WHERE session_date >= current_date - 45 GROUP BY 1, 2),
        l AS (SELECT *, lag(h) OVER (PARTITION BY ticker ORDER BY session_date) AS ph FROM d)
        SELECT count(*) FILTER (WHERE session_date >= current_date - 30),
               count(*) FILTER (WHERE session_date >= current_date - 30 AND h = ph AND bv > 0) FROM l""")

    f["adj_computed"], f["adj_last_div"], f["ca_last_div"] = _q(cur, """
        SELECT (SELECT max(computed_at)::date::text FROM adj_factor),
               (SELECT max(ex_date)::text FROM adj_factor WHERE kind = 'dividend'),
               (SELECT max(ex_date)::text FROM corp_actions WHERE type = 'cash_dividend' AND ex_date <= current_date)""")

    f["ret_rows_120d"], f["ret_extreme_120d"] = _q(cur, """
        SELECT count(DISTINCT (ticker, session_date)), count(DISTINCT (ticker, session_date)) FILTER (WHERE ret_h5 < %(extreme)s)
        FROM signal_log WHERE ret_h5 IS NOT NULL AND session_date >= current_date - 120""", p)

    f["bars_30d"], f["bars_bad_30d"] = _q(cur, """
        SELECT count(*), count(*) FILTER (WHERE close <= 0 OR (volume > 0 AND (close > high OR close < low OR high < low))
                                         OR foreign_buy_vol > volume OR foreign_sell_vol > volume)
        FROM price_bars WHERE session_date >= current_date - 30""")

    f["akum_90d"], f["dist_90d"] = _q(cur, """
        SELECT count(*) FILTER (WHERE tag = 'BROKER-ASING-AKUM'), count(*) FILTER (WHERE tag = 'BROKER-ASING-DIST')
        FROM signal_log WHERE session_date >= current_date - 90""")
    return f


def gather(since: date = SINCE) -> tuple[dict, list[str]]:
    """Facts for `build_findings`, from read-only queries. A failure is returned as text, never raised."""
    from .db import connect

    try:
        with connect() as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                return _facts(cur, since), []
    except Exception as exc:  # noqa: BLE001 — an audit that cannot run must say so, not crash
        return {}, [f"{type(exc).__name__}: {exc}"]


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "n/a"


def build_findings(f: dict, errors: list[str] | None = None, today: date | None = None) -> list[Check]:
    """Pure: facts in, findings out. BAD = the numbers the engine measures with are wrong; WARN = a known limit to keep in view."""
    out: list[Check] = [Check(BAD, "Audit could not run", e) for e in (errors or [])]
    if not f:
        return out
    today = today or datetime.now(WIB).date()

    if f.get("ref_days") is not None:
        unknown, lag = f["ref_days_unknown"], f["ref_days_shares_lag"]
        out.append(Check(BAD if unknown else OK, "Corporate-action days",
                         f"{f['ref_days']} days since {f['since']} where IDX reset the reference price; "
                         f"{unknown} have no split/bonus factor (rights issues: returns across them are not adjusted)"))
        out.append(Check(WARN if lag else OK, "Share count lag",
                         f"{lag} of those days still show the old listed share count (any per-share ratio is inflated until IDX updates it)"))

    if f.get("splits") is not None:
        n = f["splits_not_priced"]
        out.append(Check(BAD if n else OK, "Splits not priced by IDX",
                         f"{n} of {f['splits']} 'split' records have an unchanged IDX reference price: they are placements or conversions, "
                         "and a split factor applied to them makes a return wrong by the share-count change"))

    if f.get("bars_latest"):
        eq, n = f["tradable_eq_listed"], f["bars_latest"]
        out.append(Check(WARN if eq / n > 0.5 else OK, "Free float column",
                         f"tradable shares equal listed shares for {_pct(eq, n)} of stocks on the latest session: it is not a free-float figure"))

    if f.get("scripless_tickers"):
        j = f["scripless_jumps"]
        out.append(Check(WARN if j else OK, "KSEI scripless base",
                         f"{j} of {f['scripless_tickers']} stocks moved >= {SCRIPLESS_JUMP:.0%} of their shares into or out of KSEI in "
                         f"{f['scripless_month']}: their ownership percentages shift without any trading"))

    if f.get("broker_days_30d"):
        c = f["broker_copies_30d"]
        out.append(Check(BAD if c else OK, "Broker table copies",
                         f"{c} of {f['broker_days_30d']} stock-days in the last 30 days are an exact copy of the previous day"))

    if f.get("adj_computed"):
        age = (today - date.fromisoformat(f["adj_computed"])).days
        behind = bool(f.get("ca_last_div") and f.get("adj_last_div") and f["ca_last_div"] > f["adj_last_div"])
        level = WARN if (age > ADJ_STALE_DAYS or behind) else OK
        out.append(Check(level, "Adjustment factors",
                         f"last rebuilt {f['adj_computed']} ({age} d ago); newest dividend: {f.get('adj_last_div')} in the factors, "
                         f"{f.get('ca_last_div')} announced"))

    if f.get("ret_rows_120d"):
        x = f["ret_extreme_120d"]
        out.append(Check(BAD if x else OK, "Extreme 5-day returns",
                         f"{x} signalled stock-days in the last 120 days lost more than {abs(EXTREME_RET):.0%} in 5 days "
                         "(mostly unadjusted corporate actions, not real losses)"))

    if f.get("bars_30d"):
        b = f["bars_bad_30d"]
        out.append(Check(BAD if b else OK, "Price bar sanity", f"{b} of {f['bars_30d']} bars in the last 30 days are impossible"))

    if f.get("akum_90d"):
        a, d = f["akum_90d"], f["dist_90d"]
        lop = a > 20 * max(d, 1)
        out.append(Check(WARN if lop else OK, "One-sided broker measure",
                         f"BROKER-ASING-AKUM {a} vs -DIST {d} in 90 days: the v1 measure sums net buyers only and cannot see selling"))
    return out


def report(since: date = SINCE) -> tuple[str, list[Check]]:
    facts, errors = gather(since)
    findings = build_findings(facts, errors)
    return worst(c.level for c in findings), findings
