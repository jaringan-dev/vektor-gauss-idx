"""Broker stalker (`/stalker`): pick one broker and a window (today ... 60 sessions) and see which stocks it bought and sold the most, net.

For every stock the broker traded in the window: net value, value bought and sold, and the average price it paid and received
(value divided by shares). All of it is summed from the stored broker table (`broker_flows`), so it only covers stocks and days the feed
returned: coverage was about 250 liquid stocks a day until mid-October 2026 and is nearly the whole market since, which the page says.

COMPLETE TABLES ONLY. Until now the stored table keeps just the 20 biggest NET BUYERS of each stock each day: no net seller is in it, and a
broker's days outside the top 20 are missing, so any per-broker "net" would be wrong (inflated). A stock-day counts only when its table is
complete, meaning bought and sold value agree within 1% (in practice only thinly traded stocks that fewer than 20 brokers touched). Unless at
least half of the window's stock-days qualify the page shows nothing, because a handful of small stocks must not pass for the market; above that
it says how much it left out.

`load()` touches the database (SELECT only); `clean_*`, `shape` and `build_view` are pure and unit-tested.
"""

from __future__ import annotations

import re

from . import fmt

# A stock-day's table is complete when what was bought and what was sold agree (every trade has two sides).
COMPLETE_TOLERANCE = 0.01
COVERAGE_MIN = 0.5                # below this share of complete stock-days the page shows nothing: a few thinly traded stocks must not pass for the market
COMPLETE_SQL = f"sb > 0 AND abs(sb - ss) <= {COMPLETE_TOLERANCE} * sb"
COMPLETE_HAVING = f"sum(buy_val) > 0 AND abs(sum(buy_val) - sum(sell_val)) <= {COMPLETE_TOLERANCE} * sum(buy_val)"
WINDOWS = ((1, "Today"), (2, "2d"), (3, "3d"), (5, "5d"), (10, "10d"), (20, "20d"), (60, "60d"))
DEFAULT_WINDOW = 5
LIMIT = 20
_CODE = re.compile(r"^[A-Z]{2}$")


def clean_broker(raw: str | None) -> str | None:
    b = (raw or "").strip().upper()
    return b if _CODE.match(b) else None


def clean_window(raw) -> int:
    try:
        w = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW
    return w if w in dict(WINDOWS) else DEFAULT_WINDOW


def _f(v) -> float | None:
    return None if v is None else float(v)


def load_brokers() -> list[dict]:
    """Every broker code in the table with its name (when some table knows it) and whether it is a foreign house."""
    from ..db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT f.broker_code, bool_or(f.side_flag = 'F') AS foreign_house, "
                    "(SELECT max(l.broker_name) FROM broker_levels l WHERE l.broker_code = f.broker_code) AS name "
                    "FROM broker_flows f GROUP BY f.broker_code ORDER BY f.broker_code")
        return [{"code": c, "foreign": bool(fh), "name": n or ""} for c, fh, n in cur.fetchall()]


def load(broker: str, window: int) -> dict:
    """That broker's per-stock totals over the newest `window` sessions that have broker data."""
    from ..db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT session_date FROM broker_flows ORDER BY session_date DESC LIMIT %s", (int(window),))
        days = [r[0] for r in cur.fetchall()]
        rows: list[tuple] = []
        n_pairs = n_complete = 0
        if days:
            cur.execute(
                "SELECT count(*), count(*) FILTER (WHERE " + COMPLETE_SQL + ") FROM ("
                "SELECT sum(buy_val) AS sb, sum(sell_val) AS ss FROM broker_flows WHERE session_date = ANY(%s) GROUP BY session_date, ticker) t",
                (days,))
            n_pairs, n_complete = cur.fetchone()
            if n_pairs and n_complete >= COVERAGE_MIN * n_pairs:
                cur.execute(
                    "WITH ok AS (SELECT session_date, ticker FROM broker_flows WHERE session_date = ANY(%s) GROUP BY session_date, ticker "
                    "            HAVING " + COMPLETE_HAVING + ") "
                    "SELECT f.ticker, sum(f.buy_val), sum(f.sell_val), sum(f.buy_vol), sum(f.sell_vol) FROM broker_flows f "
                    "JOIN ok USING (session_date, ticker) WHERE f.broker_code = %s AND f.session_date = ANY(%s) GROUP BY f.ticker",
                    (days, broker, days))
                rows = cur.fetchall()
    return {"days": sorted(days), "rows": rows, "n_pairs": int(n_pairs or 0), "n_complete": int(n_complete or 0)}


def shape(rows: list[tuple]) -> list[dict]:
    out = []
    for ticker, bval, sval, bvol, svol in rows:
        bval, sval, bvol, svol = (_f(bval) or 0.0), (_f(sval) or 0.0), (_f(bvol) or 0.0), (_f(svol) or 0.0)
        out.append({"ticker": ticker, "net": bval - sval, "buy": bval, "sell": sval,
                    "avg_buy": bval / bvol if bvol > 0 else None, "avg_sell": sval / svol if svol > 0 else None})
    return out


def _row(r: dict) -> dict:
    return {"ticker": r["ticker"], "net": fmt.idr(r["net"]), "buy": fmt.idr(r["buy"]).lstrip("+"), "sell": fmt.idr(r["sell"]).lstrip("+"),
            "avg_buy": "n/a" if r["avg_buy"] is None else f"{r['avg_buy']:,.0f}",
            "avg_sell": "n/a" if r["avg_sell"] is None else f"{r['avg_sell']:,.0f}", "cls": fmt.signcls(r["net"])}


def build_view(raw: dict, brokers: list[dict], broker: str | None, window: int, foreign_only: bool) -> dict:
    shown = [b for b in brokers if b["foreign"]] if foreign_only else brokers
    if broker and broker not in {b["code"] for b in brokers}:
        broker = None                                           # a code the table has never seen
    items = shape(raw.get("rows") or []) if broker else []
    buys = sorted((r for r in items if r["net"] > 0), key=lambda r: (-r["net"], r["ticker"]))
    sells = sorted((r for r in items if r["net"] < 0), key=lambda r: (r["net"], r["ticker"]))
    days = raw.get("days") or []
    n_pairs, n_complete = raw.get("n_pairs", 0), raw.get("n_complete", 0)
    name = next((b["name"] for b in brokers if b["code"] == broker), "")
    return {
        "broker": broker, "broker_name": name, "window": window, "foreign_only": foreign_only,
        "windows": [{"n": n, "label": label, "on": n == window} for n, label in WINDOWS],
        "brokers": [{"code": b["code"], "label": f"{b['code']} · {b['name']}" if b["name"] else b["code"], "on": b["code"] == broker} for b in shown],
        "n_pairs": n_pairs, "n_complete": n_complete,
        "incomplete": bool(days) and n_complete < COVERAGE_MIN * n_pairs, "partial": COVERAGE_MIN * n_pairs <= n_complete < n_pairs and n_pairs > 0,
        "days": days, "first": days[0] if days else None, "last": days[-1] if days else None,
        "short": bool(days) and len(days) < window, "n_stocks": len(items),
        "buys": [_row(r) for r in buys[:LIMIT]], "sells": [_row(r) for r in sells[:LIMIT]],
        "n_buys": len(buys), "n_sells": len(sells),
        "total_net": fmt.idr(sum(r["net"] for r in items)) if items else None,
    }
