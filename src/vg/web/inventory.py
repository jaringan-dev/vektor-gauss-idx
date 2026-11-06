"""Broker inventory (`/inventory`): for one stock, the running net position of the five biggest net-buying and five biggest net-selling brokers
over a chosen period (2 weeks ... 1 year), counted from zero on the first day, with the closing price underneath.

A broker's "inventory" here is the sum of its daily net (bought minus sold) in the period - what it accumulated or gave up while we watched,
not its absolute holding (no data source reports that). It is summed from the complete broker table (`broker_flows_full`), so it needs broker data
for that stock; the page says when there is none.

COMPLETE TABLES ONLY. It reads `broker_flows_full` (every broker that traded, from `vg collect-broker-full`), not `broker_flows`, which keeps just
the 20 biggest NET BUYERS of each stock each day: no net seller, so a running net from it would be inflated. A day counts only when its table is
complete (bought and sold value agree within 1%). The page says how many days it had to leave out and shows nothing when
no day qualifies. Unless at least 80% of the period's days qualify it shows nothing either: the tables that are complete are almost only the quiet days
of thinly traded stocks (fewer than 20 brokers touched them), and a running net over those alone would misrepresent the period.

`load()` touches the database (SELECT only); the rest is pure and unit-tested.
"""

from __future__ import annotations

import re
from datetime import date

from . import charts, fmt

RANGES = (("2W", 10), ("1M", 21), ("3M", 63), ("6M", 126), ("YTD", None), ("1Y", 250))
DEFAULT_RANGE = "3M"
UNITS = (("value", "Value"), ("lot", "Lot"))
INVESTORS = (("all", "All"), ("domestic", "Domestic brokers"), ("foreign", "Foreign brokers"))
TOP = 5
COMPLETE_TOLERANCE = 0.01
COVERAGE_MIN = 0.8               # share of the period's days that must have a complete table; fewer and the running net would only reflect the quiet days
DEFAULT_TICKER = "BBCA"
_CODE = re.compile(r"^[A-Z0-9]{4}$")
_FLAG = {"domestic": "D", "foreign": "F"}


def clean_ticker(raw: str | None) -> str:
    t = (raw or "").strip().upper()
    return t if _CODE.match(t) else DEFAULT_TICKER


def clean_range(raw: str | None) -> str:
    r = (raw or "").strip().upper()
    return r if r in dict(RANGES) else DEFAULT_RANGE


def clean_unit(raw: str | None) -> str:
    return raw if raw in dict(UNITS) else "value"


def clean_investor(raw: str | None) -> str:
    return raw if raw in dict(INVESTORS) else "all"


def _f(v) -> float:
    return 0.0 if v is None else float(v)


def load(ticker: str, rng: str) -> dict:
    """The stock's broker rows for the newest sessions in the range, plus the close of each of those sessions."""
    from ..db import connect

    n = dict(RANGES)[rng]
    with connect() as conn, conn.cursor() as cur:
        having = f"HAVING sum(buy_val) > 0 AND abs(sum(buy_val) - sum(sell_val)) <= {COMPLETE_TOLERANCE} * sum(buy_val)"
        if rng == "YTD":
            cur.execute("SELECT max(session_date) FROM broker_flows_full WHERE ticker = %s", (ticker,))
            latest = cur.fetchone()[0]
            cur.execute("SELECT DISTINCT session_date FROM broker_flows_full WHERE ticker = %s AND session_date >= %s ORDER BY session_date",
                        (ticker, date(latest.year, 1, 1) if latest else date.today()))  # noqa: DTZ011 - local date, as elsewhere
        else:
            cur.execute("SELECT DISTINCT session_date FROM broker_flows_full WHERE ticker = %s ORDER BY session_date DESC LIMIT %s", (ticker, int(n)))
        period = sorted(r[0] for r in cur.fetchall())                  # every day the period has broker data for
        days: list = []
        if period:
            cur.execute("SELECT session_date FROM broker_flows_full WHERE ticker = %s AND session_date = ANY(%s) GROUP BY session_date " + having +
                        " ORDER BY session_date", (ticker, period))
            days = [r[0] for r in cur.fetchall()]
        n_all = len(period)
        if days and len(days) < COVERAGE_MIN * n_all:
            days = []                                                 # a few quiet days must not stand for the period: show nothing
        rows: list[tuple] = []
        closes: dict = {}
        if days:
            cur.execute("SELECT session_date, broker_code, side_flag, buy_val, sell_val, buy_vol, sell_vol FROM broker_flows_full "
                        "WHERE ticker = %s AND session_date = ANY(%s)", (ticker, days))
            rows = cur.fetchall()
            cur.execute("SELECT session_date, close FROM price_bars WHERE ticker = %s AND session_date = ANY(%s)", (ticker, days))
            closes = {d: (None if c is None else float(c)) for d, c in cur.fetchall()}
    return {"days": days, "rows": rows, "closes": closes, "n_all": n_all}


def running(days: list[date], rows: list[tuple], *, unit: str, investor: str) -> dict[str, dict]:
    """{broker: {"side": 'F'|'D'|None, "vals": running net per day}} - a day the broker did not trade adds nothing."""
    want = _FLAG.get(investor)
    index = {d: i for i, d in enumerate(days)}
    daily: dict[str, list[float]] = {}
    side: dict[str, str | None] = {}
    for d, code, flag, bval, sval, bvol, svol in rows:
        if want and flag != want:
            continue
        i = index.get(d)
        if i is None:
            continue
        net = (_f(bval) - _f(sval)) if unit == "value" else (_f(bvol) - _f(svol)) / charts.SHARES_PER_LOT
        daily.setdefault(code, [0.0] * len(days))[i] += net
        side[code] = flag
    out = {}
    for code, nets in daily.items():
        run, vals = 0.0, []
        for v in nets:
            run += v
            vals.append(run)
        out[code] = {"side": side.get(code), "vals": vals}
    return out


def pick(series: dict[str, dict], top: int = TOP) -> tuple[list[str], list[str]]:
    """The `top` biggest net buyers (final position above zero, largest first) and net sellers (below zero, most negative first)."""
    final = {c: s["vals"][-1] for c, s in series.items() if s["vals"]}
    buyers = sorted((c for c, v in final.items() if v > 0), key=lambda c: (-final[c], c))[:top]
    sellers = sorted((c for c, v in final.items() if v < 0), key=lambda c: (final[c], c))[:top]
    return buyers, sellers


def _money(v: float, unit: str) -> str:
    return fmt.idr(v) if unit == "value" else f"{v:+,.0f}"


def build_view(raw: dict, ticker: str, rng: str, unit: str, investor: str) -> dict:
    days = raw.get("days") or []
    series = running(days, raw.get("rows") or [], unit=unit, investor=investor) if days else {}
    buyers, sellers = pick(series)
    lines = []
    for i, code in enumerate(buyers):
        lines.append({"code": code, "kind": "buy", "color": charts.INV_COLORS[i], "vals": series[code]["vals"]})
    for j, code in enumerate(sellers):
        lines.append({"code": code, "kind": "sell", "color": charts.INV_COLORS[TOP + j], "vals": series[code]["vals"]})
    closes = [raw.get("closes", {}).get(d) for d in days]
    chart = charts.inventory_chart(days, lines, closes, unit=unit, uid="inv")
    chart_m = charts.inventory_chart(days, lines, closes, unit=unit, width=charts.W_MOBILE, uid="inv-m", tips=False)
    flag = {"F": "Foreign", "D": "Domestic"}
    table = [{"code": ln["code"], "kind": "Net buyer" if ln["kind"] == "buy" else "Net seller", "dash": ln["kind"] == "sell",
              "color": ln["color"], "side": flag.get(series[ln["code"]]["side"], "?"), "net": _money(ln["vals"][-1], unit),
              "cls": fmt.signcls(ln["vals"][-1])} for ln in lines]
    return {
        "ticker": ticker, "range": rng, "unit": unit, "investor": investor,
        "ranges": [{"key": k, "on": k == rng} for k, _n in RANGES], "units": [{"key": k, "label": lb, "on": k == unit} for k, lb in UNITS],
        "investors": [{"key": k, "label": lb, "on": k == investor} for k, lb in INVESTORS],
        "n_all": raw.get("n_all", 0), "incomplete": not days and bool(raw.get("n_all")),
        "has_data": bool(days), "n_days": len(days), "first": days[0] if days else None, "last": days[-1] if days else None,
        "short": 0 < len(days) < 5, "chart": chart, "chart_m": chart_m, "table": table, "n_brokers": len(series),
        "unit_word": "rupiah" if unit == "value" else "lots",
    }
