"""Read-only data-quality audit of the VG Postgres (checklist K-series, ARSITEKTUR-DATA-v1.md §3).

Every check is a plain SQL statement that returns the OFFENDING rows/counts, so "clean" means a
measured zero, not an absence of looking. Nothing here writes. Run inside the repo venv with .env:

    python scripts/data_audit.py            # all sections
    python scripts/data_audit.py price corp # only sections whose key starts with these

A section that raises is reported as ERROR and the rest still run (one bad query must not hide the others).
"""

from __future__ import annotations

import sys
from datetime import date

from vg.db import connect

SECTIONS: list = []


def section(key: str, title: str):
    def deco(fn):
        SECTIONS.append((key, title, fn))
        return fn
    return deco


def q(cur, sql: str, params=None):
    cur.execute(sql, params)
    return cur.fetchall()


def show(label: str, rows, *, limit: int = 8, ok_when_empty: bool = True) -> int:
    n = len(rows)
    if n == 0 and ok_when_empty:
        print(f"  OK    {label}: 0")
        return 0
    print(f"  FLAG  {label}: {n if n < 10_000 else '10k+'}")
    for r in rows[:limit]:
        print("          ", tuple(str(x)[:40] for x in r))
    return n


@section("price", "price_bars — invariants & lineage")
def price(cur):
    show("high < low", q(cur, "SELECT session_date,ticker,high,low FROM price_bars WHERE high < low LIMIT 20"))
    show("close outside [low,high] (traded days)", q(cur, """SELECT session_date,ticker,low,close,high FROM price_bars
        WHERE volume>0 AND low>0 AND (close<low OR close>high) LIMIT 20"""))
    show("open outside [low,high]", q(cur, """SELECT session_date,ticker,low,open,high FROM price_bars
        WHERE volume>0 AND open>0 AND low>0 AND (open<low OR open>high) LIMIT 20"""))
    show("close <= 0", q(cur, "SELECT session_date,ticker,close FROM price_bars WHERE close<=0 LIMIT 20"))
    show("negative volume/value", q(cur, "SELECT session_date,ticker,volume,value FROM price_bars WHERE volume<0 OR value<0 LIMIT 20"))
    show("value/volume implies price outside day range x1.5 (unit errors)", q(cur, """SELECT session_date,ticker,volume,value,low,high
        FROM price_bars WHERE volume>0 AND value>0 AND high>0
          AND (value/volume > high*1.5 OR value/volume < low*0.5) LIMIT 20"""))
    print("  -- null share of key columns, by year (%) --")
    rows = q(cur, """SELECT extract(year FROM session_date)::int y, count(*),
        round(100.0*avg((foreign_buy_vol IS NULL)::int),1) fb,
        round(100.0*avg((listed_shares IS NULL)::int),1) ls,
        round(100.0*avg((frequency IS NULL)::int),1) fr,
        round(100.0*avg((bid IS NULL)::int),1) bid
        FROM price_bars GROUP BY 1 ORDER BY 1""")
    print("     year  rows     foreign_null  listed_null  freq_null  bid_null")
    for r in rows:
        print(f"     {r[0]}  {r[1]:>7}  {r[2]:>8}      {r[3]:>8}     {r[4]:>6}    {r[5]:>6}")
    print("  -- lineage --")
    for r in q(cur, "SELECT source, source_tier, count(*), min(session_date), max(session_date) FROM price_bars GROUP BY 1,2 ORDER BY 3 DESC"):
        print("     ", r)


@section("jump", "price_bars — jumps & adjustment")
def jump(cur):
    rows = q(cur, """WITH x AS (
        SELECT session_date, ticker, close, prev, volume,
               lag(close) OVER (PARTITION BY ticker ORDER BY session_date) pc
        FROM price_bars WHERE close>0)
      SELECT extract(year FROM session_date)::int y,
             count(*) FILTER (WHERE pc>0 AND abs(close/pc-1) > 0.35) big35,
             count(*) FILTER (WHERE pc>0 AND abs(close/pc-1) > 0.60) big60,
             count(*) FILTER (WHERE pc>0 AND prev>0 AND abs(prev/pc-1) > 0.005) prev_ne_lagclose
      FROM x GROUP BY 1 ORDER BY 1""")
    print("     year  |ret|>35%  |ret|>60%  prev!=prev-day-close(>0.5%)")
    for r in rows:
        print(f"     {r[0]}  {r[1]:>8}  {r[2]:>8}  {r[3]:>10}")
    print("     full count |ret|>60% without a corp_action within +-3d:", q(cur, """WITH x AS (
        SELECT session_date, ticker, close, lag(close) OVER (PARTITION BY ticker ORDER BY session_date) pc
        FROM price_bars WHERE close>0)
      SELECT count(*) FROM x WHERE pc>0 AND abs(close/pc-1) > 0.60
        AND NOT EXISTS (SELECT 1 FROM corp_actions c WHERE c.ticker=x.ticker AND c.ex_date BETWEEN x.session_date-3 AND x.session_date+3)""")[0][0])
    show("|ret|>60% days WITHOUT a corp_action within +-3d (likely bad/unadjusted data)", q(cur, """WITH x AS (
        SELECT session_date, ticker, close, lag(close) OVER (PARTITION BY ticker ORDER BY session_date) pc
        FROM price_bars WHERE close>0)
      SELECT x.session_date, x.ticker, x.pc, x.close FROM x
      WHERE pc>0 AND abs(close/pc-1) > 0.60
        AND NOT EXISTS (SELECT 1 FROM corp_actions c WHERE c.ticker=x.ticker
                        AND c.ex_date BETWEEN x.session_date-3 AND x.session_date+3)
      ORDER BY abs(close/pc-1) DESC LIMIT 40"""), limit=12)


@section("universe", "Universe, survivorship & listing churn")
def universe(cur):
    rows = q(cur, """SELECT extract(year FROM session_date)::int y, count(DISTINCT ticker) tickers,
        count(*)/count(DISTINCT session_date) avg_per_day FROM price_bars GROUP BY 1 ORDER BY 1""")
    print("     year  distinct_tickers  avg_rows/day")
    for r in rows:
        print(f"     {r[0]}  {r[1]:>10}  {r[2]:>10}")
    print("     delisting_date populated:", q(cur, "SELECT count(*), count(DISTINCT ticker) FROM price_bars WHERE delisting_date IS NOT NULL")[0])
    r = q(cur, """WITH last AS (SELECT ticker, max(session_date) d, min(session_date) f FROM price_bars GROUP BY 1)
        SELECT count(*) FILTER (WHERE d < (SELECT max(session_date) FROM price_bars) - 30) vanished,
               count(*) FILTER (WHERE f > date '2020-01-10') born_after_start,
               count(*) total FROM last""")[0]
    print(f"     tickers whose last bar is >30d old (vanished/delisted): {r[0]}; first seen after 2020-01: {r[1]}; total ever: {r[2]}")
    show("vanished tickers WITHOUT delisting_date (survivorship unknowable)", q(cur, """WITH last AS (SELECT ticker, max(session_date) d FROM price_bars GROUP BY 1)
        SELECT ticker, d FROM last WHERE d < (SELECT max(session_date) FROM price_bars) - 30
          AND NOT EXISTS (SELECT 1 FROM price_bars p WHERE p.ticker=last.ticker AND p.delisting_date IS NOT NULL)
        ORDER BY d DESC LIMIT 25"""), limit=10)
    show("latest-day tickers with no sector (ticker_profile_history)", q(cur, """SELECT p.ticker FROM price_bars p
        WHERE p.session_date=(SELECT max(session_date) FROM price_bars)
          AND NOT EXISTS (SELECT 1 FROM ticker_profile_history t WHERE t.ticker=p.ticker AND t.sector IS NOT NULL)
        LIMIT 60"""), limit=10)


@section("calendar", "trading_calendar vs actual bars")
def calendar(cur):
    show("calendar days with <300 price rows (since 2020)", q(cur, """SELECT c.session_date, count(p.ticker) FROM trading_calendar c
        LEFT JOIN price_bars p USING (session_date) GROUP BY 1 HAVING count(p.ticker) < 300 ORDER BY 1"""), limit=15)
    show("price_bars dates missing from calendar", q(cur, """SELECT DISTINCT p.session_date FROM price_bars p
        LEFT JOIN trading_calendar c USING (session_date) WHERE c.session_date IS NULL ORDER BY 1 LIMIT 30"""))
    show("weekdays absent from calendar (holidays expected ~12-18/yr)", q(cur, """SELECT extract(year FROM d)::int y, count(*) FROM generate_series(date '2020-01-01', (SELECT max(session_date) FROM trading_calendar), '1 day') d
        WHERE extract(isodow FROM d) < 6 AND d NOT IN (SELECT session_date FROM trading_calendar) GROUP BY 1 ORDER BY 1"""), limit=10)
    show("calendar days duplicated by weekday==weekend", q(cur, "SELECT session_date FROM trading_calendar WHERE extract(isodow FROM session_date) >= 6 LIMIT 10"))


@section("index", "index_bars")
def index(cur):
    rows = q(cur, """SELECT index_code, min(session_date), max(session_date), count(*) FROM index_bars GROUP BY 1 ORDER BY 4 DESC""")
    print(f"     {len(rows)} indices; with <500 days:")
    for r in [x for x in rows if x[3] < 500][:20]:
        print("       ", tuple(str(v) for v in r))
    show("COMPOSITE missing trading days since 2020", q(cur, """SELECT c.session_date FROM trading_calendar c
        WHERE NOT EXISTS (SELECT 1 FROM index_bars i WHERE i.index_code='COMPOSITE' AND i.session_date=c.session_date) ORDER BY 1"""), limit=20)
    show("index close <= 0 / high<low", q(cur, "SELECT session_date,index_code,close FROM index_bars WHERE close<=0 OR high<low LIMIT 10"))


@section("ksei", "ownership_monthly (KSEI)")
def ksei(cur):
    # month_end is the LAST BUSINESS DAY (28-31), so compare by calendar month, not by date
    show("calendar months missing between first and last", q(cur, """WITH m AS (SELECT DISTINCT date_trunc('month', month_end)::date mo FROM ownership_monthly)
        SELECT g::date FROM generate_series((SELECT min(mo) FROM m)::timestamp, (SELECT max(mo) FROM m)::timestamp, '1 month') g
        WHERE g::date NOT IN (SELECT mo FROM m) LIMIT 30"""))
    show("local+foreign shares don't reconcile to listed_shares (>5% off, month-end bar)", q(cur, """SELECT o.month_end, o.ticker,
        o.local_individual+o.local_institution+o.foreign_total tot, p.listed_shares
        FROM ownership_monthly o JOIN LATERAL (SELECT listed_shares FROM price_bars p WHERE p.ticker=o.ticker AND p.session_date<=o.month_end
              AND listed_shares>0 ORDER BY session_date DESC LIMIT 1) p ON true
        WHERE p.listed_shares>0 AND abs((o.local_individual+o.local_institution+o.foreign_total)/p.listed_shares-1) > 0.05
        ORDER BY o.month_end DESC LIMIT 400"""), limit=6)
    print("     rows/month min/median/max:", q(cur, """SELECT min(c), percentile_disc(0.5) WITHIN GROUP (ORDER BY c), max(c)
        FROM (SELECT month_end, count(*) c FROM ownership_monthly GROUP BY 1) s""")[0])
    print("     holders_count null %:", q(cur, "SELECT round(100.0*avg((holders_count IS NULL)::int),1) FROM ownership_monthly")[0][0])


@section("corp", "corp_actions")
def corp(cur):
    for r in q(cur, "SELECT type, count(*), min(ex_date), max(ex_date), count(*) FILTER (WHERE ratio IS NULL) null_ratio FROM corp_actions GROUP BY 1 ORDER BY 2 DESC"):
        print("     ", tuple(str(x) for x in r))
    show("ex_date before cum_date", q(cur, "SELECT ticker, cum_date, ex_date FROM corp_actions WHERE cum_date IS NOT NULL AND ex_date < cum_date LIMIT 10"))
    rows = q(cur, "SELECT extract(year FROM ex_date)::int, count(*) FROM corp_actions GROUP BY 1 ORDER BY 1")
    print("     events per ex-year:", {int(a): b for a, b in rows})
    show("share-count changes in price_bars with no corp_action (+-5d)", q(cur, """WITH x AS (SELECT session_date, ticker, listed_shares,
          lag(listed_shares) OVER (PARTITION BY ticker ORDER BY session_date) pl FROM price_bars WHERE listed_shares>0)
      SELECT x.session_date, x.ticker, x.pl, x.listed_shares FROM x
      WHERE pl>0 AND abs(listed_shares/pl-1) > 0.10
        AND NOT EXISTS (SELECT 1 FROM corp_actions c WHERE c.ticker=x.ticker AND c.ex_date BETWEEN x.session_date-5 AND x.session_date+5)
      ORDER BY x.session_date DESC LIMIT 60"""), limit=10)


@section("broker", "broker_flows — internal & cross-source reconciliation")
def broker(cur):
    show("net_val != buy_val - sell_val (>1 rupiah)", q(cur, "SELECT session_date,ticker,broker_code,net_val,buy_val-sell_val FROM broker_flows WHERE abs(net_val-(buy_val-sell_val))>1 LIMIT 10"))
    show("per ticker-day: sum(buy_val) != sum(sell_val) (>0.5%)", q(cur, """SELECT session_date,ticker,sum(buy_val),sum(sell_val) FROM broker_flows
        GROUP BY 1,2 HAVING sum(buy_val)>0 AND abs(sum(buy_val)/NULLIF(sum(sell_val),0)-1) > 0.005 LIMIT 400"""), limit=6)
    r = q(cur, """WITH b AS (SELECT session_date, ticker, sum(buy_val) bv FROM broker_flows GROUP BY 1,2),
        j AS (SELECT b.bv, p.value FROM b JOIN price_bars p USING (session_date, ticker) WHERE p.value>0)
        SELECT count(*), round(100.0*avg((bv/value BETWEEN 0.9 AND 1.1)::int),1),
               round(100.0*avg((bv/value BETWEEN 0.5 AND 1.5)::int),1),
               percentile_disc(0.5) WITHIN GROUP (ORDER BY bv/value) FROM j""")[0]
    print(f"     broker sum(buy_val) vs price_bars.value: n={r[0]}; within +-10%: {r[1]}%; within +-50%: {r[2]}%; median ratio {r[3]}")
    print("     note: ratio ~0.5 or ~2 would mean the board/side double-counts (RG+TN/NG); see side_flag/board.")
    for r in q(cur, "SELECT board, side_flag, count(*) FROM broker_flows GROUP BY 1,2 ORDER BY 3 DESC LIMIT 6"):
        print("      board/side:", r)


@section("fresh", "Freshness & benchmark lag")
def fresh(cur):
    for t, c in (("price_bars", "session_date"), ("index_bars", "session_date"), ("broker_flows", "session_date"),
                 ("signal_components", "session_date"), ("signal_log", "session_date"), ("market_breadth", "session_date"),
                 ("ownership_monthly", "month_end"), ("corp_actions", "ex_date"), ("disclosure_filings", "filed_date"),
                 ("trading_halts", "filed_date"), ("margin_securities", "session_date"), ("ticker_profile_history", "observed_on")):
        try:
            cur.execute(f"SELECT max({c}) FROM {t}")
            print(f"     {t:<24} newest {cur.fetchone()[0]}")
        except Exception as e:  # noqa: BLE001 — a missing/renamed column must not stop the audit
            cur.connection.rollback()
            print(f"     {t:<24} (skip: {str(e).splitlines()[0][:60]})")
    # D7: market_breadth ends 10 trading days before the last bar because H10 needs 10 forward days.
    r = q(cur, """SELECT (SELECT max(session_date) FROM market_breadth), (SELECT max(session_date) FROM trading_calendar),
        (SELECT count(*) FROM trading_calendar WHERE session_date > (SELECT max(session_date) FROM market_breadth))""")[0]
    print(f"     market_breadth newest {r[0]}; calendar newest {r[1]}; trading days between = {r[2]} (10 expected => D7 explained)")


@section("ops", "run_log / raw_artifacts oddities (D8)")
def ops(cur):
    show("raw_artifacts logical_date in the future / before 2015", q(cur, """SELECT source, logical_date, path FROM raw_artifacts
        WHERE logical_date > current_date + 1 OR logical_date < date '2015-01-01' ORDER BY logical_date DESC LIMIT 20"""))
    print("     run_log status:", dict(q(cur, "SELECT status, count(*) FROM run_log GROUP BY 1")))
    show("run_log failures in the last 30 days", q(cur, """SELECT phase, batch_date, status, left(error,50) FROM run_log
        WHERE status NOT IN ('ok','success') AND started_at > now() - interval '30 days' ORDER BY started_at DESC LIMIT 30"""), limit=10)
    print("     table sizes:")
    for r in q(cur, """SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) FROM pg_catalog.pg_statio_user_tables
        ORDER BY pg_total_relation_size(relid) DESC LIMIT 8"""):
        print("       ", r)


def main(argv: list[str]) -> int:
    want = argv[1:]
    print(f"VG data audit — {date.today()}  (read-only)")
    flagged = 0
    with connect() as conn:
        for key, title, fn in SECTIONS:
            if want and not any(key.startswith(w) for w in want):
                continue
            print(f"\n== {key}: {title}")
            try:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = '120s'")
                    fn(cur)
            except Exception as e:  # noqa: BLE001 — see module docstring
                conn.rollback()
                print(f"  ERROR {type(e).__name__}: {str(e).splitlines()[0][:160]}")
                flagged += 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
