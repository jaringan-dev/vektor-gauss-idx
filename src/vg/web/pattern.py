"""Pattern (tag) detail page data (RENCANA-ROMBAK-UI-v3 §3.3): "what is this pattern, when does it fire, did it ever pay?"

`load()` is the only DB part; `build_view()` is pure. The headline verdict rows (forward test and full history) come
from the same cached `eval_tags` rows the Track record page uses, so the two pages can never disagree.
"""

from __future__ import annotations

from datetime import date

from . import charts, fmt
from .glossary import TAGS, plain_tag

LATEST_LIST = 30
CHART_SESSIONS = 90
SMALL_MONTH_N = 30


def _f(v):
    return None if v is None else float(v)


def load(tag: str) -> dict:
    from ..db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            # Outcome v2 (VWAP t+1 entry, total return): the same numbers as the Track record (engine.eval_tags).
            "SELECT date_trunc('month', s.session_date)::date, count(*), avg(s.ret2_h5 - m.ret2_h5_all), "
            "avg((s.ret2_h5 > 0)::int)::float, avg(m.hit2_h5_all) "
            "FROM signal_log s JOIN market_breadth2 m USING (session_date) "
            "WHERE s.tag = %s AND s.ret2_h5 IS NOT NULL GROUP BY 1 ORDER BY 1 DESC LIMIT 24", (tag,))
        monthly = cur.fetchall()
        cur.execute("SELECT max(session_date) FROM signal_log WHERE tag = %s", (tag,))
        last_day = cur.fetchone()[0]
        latest, n_latest = [], 0
        if last_day:
            cur.execute("SELECT count(*) FROM signal_log WHERE tag = %s AND session_date = %s", (tag, last_day))
            n_latest = cur.fetchone()[0]
            cur.execute(
                "SELECT s.ticker, p.close, p.prev, p.value FROM signal_log s "
                "JOIN price_bars p ON p.session_date = s.session_date AND p.ticker = s.ticker "
                "WHERE s.tag = %s AND s.session_date = %s ORDER BY p.value DESC NULLS LAST LIMIT %s",
                (tag, last_day, LATEST_LIST))
            latest = cur.fetchall()
        cur.execute("SELECT session_date FROM trading_calendar ORDER BY session_date DESC LIMIT %s", (CHART_SESSIONS,))
        cal = sorted(r[0] for r in cur.fetchall())
        counts = {}
        if cal:
            cur.execute("SELECT session_date, count(*) FROM signal_log WHERE tag = %s AND session_date >= %s GROUP BY 1",
                        (tag, cal[0]))
            counts = {d: int(n) for d, n in cur.fetchall()}
    return {"tag": tag, "monthly": monthly, "last_day": last_day, "n_latest": n_latest, "latest": latest,
            "calendar": cal, "counts": counts}


def build_view(raw: dict, forward: dict | None, full: dict | None, rule: dict | None) -> dict:
    """`forward`/`full`: the tag's row from the Track record tables (already formatted), or None. `rule`: the tag's row from the
    saved stop/target test (scope all) plus the baseline, or None."""
    tag = raw["tag"]
    info = TAGS[tag]
    monthly = [{"month": m.strftime("%b %Y"), "n": n, "small": n < SMALL_MONTH_N, "edge": fmt.cell(_f(e)),
                "hit": fmt.hit(h), "uhit": fmt.hit(u)} for m, n, e, h, u in raw["monthly"]]
    latest = []
    for tk, close, prev, value in raw["latest"]:
        close, prev = _f(close), _f(prev)
        latest.append({"ticker": tk, "price": fmt.price(close), "chg": fmt.cell((close / prev - 1) if (close and prev) else None, 1),
                       "value": fmt.idr(_f(value)).lstrip("+")})
    cal = raw["calendar"]
    series = [raw["counts"].get(d, 0) for d in cal]
    chart = charts.count_bars(cal, series) if cal else None
    chart_m = charts.count_bars(cal, series, width=charts.W_MOBILE, uid="count-m") if cal else None
    return {"tag": tag, "name": info.name, "what": info.what, "rule": info.rule, "caveat": info.caveat, "family": info.family,
            "forward": forward, "full": full, "monthly": monthly, "latest": latest, "n_latest": raw["n_latest"],
            "last_day": raw["last_day"].isoformat() if raw["last_day"] else None, "chart": chart, "chart_m": chart_m, "rule_test": rule,
            "siblings": [{"tag": t.tag, "name": plain_tag(t.tag)} for t in TAGS.values() if t.family == info.family and t.tag != tag]}
