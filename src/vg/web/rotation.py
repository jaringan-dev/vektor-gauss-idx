"""Sector rotation screen (`/rotation`): a relative-rotation chart of sectors against the market, or of one sector's stocks
against their sector index.

Method (an approximation of the well-known "relative rotation graph", NOT the proprietary JdK formula; the page says so):

    RS          = adjusted close of the item / close of the benchmark              (relative strength line)
    RS-Ratio    = 100 x RS / simple moving average of RS over `l_ratio` bars        (above 100: stronger than its own recent norm)
    RS-Momentum = 100 x RS-Ratio / simple moving average of RS-Ratio over `l_mom`   (above 100: that strength is still rising)

The chart plots RS-Ratio (x) against RS-Momentum (y) with a tail of the last few bars. The four quadrants are the sign of
each axis around 100. Everything is descriptive: no forecast, no signal, not forward-tested.

`load()` is the only part that touches the database (psycopg, SELECT only); everything else is pure and unit-tested.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import charts, fmt, listing

# IDX-IC sector name (as stored in `tickers.sector`) -> the IDX sector index in `index_bars`.
SECTOR_INDEX = {
    "Energi": "IDXENERGY", "Barang Baku": "IDXBASIC", "Perindustrian": "IDXINDUST",
    "Barang Konsumen Primer": "IDXNONCYC", "Barang Konsumen Non-Primer": "IDXCYCLIC", "Kesehatan": "IDXHEALTH",
    "Keuangan": "IDXFINANCE", "Properti & Real Estat": "IDXPROPERT", "Teknologi": "IDXTECHNO",
    "Infrastruktur": "IDXINFRA", "Transportasi & Logistik": "IDXTRANS",
}
MARKET_INDEX = "COMPOSITE"
FREQS = ("daily", "weekly")
# bars in each moving average, per frequency: (RS-Ratio window, RS-Momentum window)
WINDOWS = {"daily": (20, 10), "weekly": (10, 5)}
TAIL_DEFAULT, TAIL_MIN, TAIL_MAX = 8, 3, 15
MAX_STOCKS = 25                       # the most liquid stocks of a sector; more than that is unreadable
LIQUID_AVG_VALUE = listing.LIQUID_VALUE_IDR
HISTORY_DAYS = 330                    # calendar days of prices read; weekly bars need about 23 weeks
LIQUIDITY_SESSIONS = 20

QUADRANT_WORDS = {
    "Outperform": "Stronger than the benchmark and still gaining",
    "Weakening": "Stronger than the benchmark but losing pace",
    "Underperform": "Weaker than the benchmark and still losing ground",
    "Improving": "Weaker than the benchmark but gaining",
}
QUADRANT_ORDER = ("Outperform", "Improving", "Weakening", "Underperform")


# ---- database --------------------------------------------------------------------------------------------------

def _series(rows) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {}
    for key, d, close in rows:
        if close is not None and float(close) > 0:
            out.setdefault(key, []).append((d, float(close)))
    return {k: sorted(v) for k, v in out.items()}


def load(sector: str) -> dict:
    """Prices for one screen. `sector` '' = every sector index against the market index; a sector name = that sector's stocks
    against its index. READ-ONLY. Returns {latest, bench, series, avg_value}."""
    from ..db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(session_date) FROM index_bars WHERE index_code = %s", (MARKET_INDEX,))
        latest = cur.fetchone()[0]
        if latest is None:
            return {"latest": None, "bench": [], "series": {}, "avg_value": {}}
        since = latest - timedelta(days=HISTORY_DAYS)
        if not sector:
            codes = [*SECTOR_INDEX.values(), MARKET_INDEX]
            cur.execute("SELECT index_code, session_date, close FROM index_bars WHERE index_code = ANY(%s) AND session_date >= %s",
                        (codes, since))
            series = _series(cur.fetchall())
            bench = series.pop(MARKET_INDEX, [])
            names = {v: k for k, v in SECTOR_INDEX.items()}
            return {"latest": latest, "bench": bench, "series": {names[c]: s for c, s in series.items() if c in names}, "avg_value": {}}
        cur.execute("SELECT session_date, close FROM index_bars WHERE index_code = %s AND session_date >= %s",
                    (SECTOR_INDEX[sector], since))
        bench = [(d, float(c)) for d, c in sorted(cur.fetchall()) if c is not None and float(c) > 0]
        cur.execute(
            "SELECT b.ticker, b.session_date, b.close * COALESCE(a.split_factor, 1), b.value "
            "FROM price_bars b JOIN tickers t ON t.ticker = b.ticker "
            "LEFT JOIN price_adj a ON a.session_date = b.session_date AND a.ticker = b.ticker "
            "WHERE t.sector = %s AND b.session_date >= %s AND b.close > 0", (sector, since))
        rows = cur.fetchall()
    by_value: dict[str, list[tuple[date, float]]] = {}
    for tk, d, _c, v in rows:
        by_value.setdefault(tk, []).append((d, float(v) if v is not None else 0.0))
    avg_value = {}
    for tk, vs in by_value.items():                    # average traded value over the last LIQUIDITY_SESSIONS sessions
        recent = [v for _d, v in sorted(vs)[-LIQUIDITY_SESSIONS:]]
        avg_value[tk] = sum(recent) / len(recent)
    series = _series((tk, d, c) for tk, d, c, _v in rows)
    return {"latest": latest, "bench": bench, "series": series, "avg_value": avg_value}


# ---- maths (pure) ----------------------------------------------------------------------------------------------

def to_weekly(series: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Last bar of each ISO week (the newest week may be unfinished: it is that week's latest close)."""
    last: dict[tuple[int, int], tuple[date, float]] = {}
    for d, c in sorted(series):
        iso = d.isocalendar()
        last[(iso[0], iso[1])] = (d, c)
    return sorted(last.values())


def _sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= n:
            run -= values[i - n]
        out.append(run / n if i >= n - 1 else None)
    return out


def rrg_points(series: list[tuple[date, float]], bench: list[tuple[date, float]], *, l_ratio: int, l_mom: int,
               tail: int) -> list[tuple[date, float, float]] | None:
    """The last `tail` (date, RS-Ratio, RS-Momentum) points of `series` against `bench`, on the dates both have.
    None when there is not enough history for a full tail (a short history must never be stretched into a tail)."""
    b = dict(bench)
    both = [(d, c / b[d]) for d, c in series if d in b]
    if len(both) < l_ratio + l_mom - 2 + tail:
        return None
    rs = [v for _d, v in both]
    ma = _sma(rs, l_ratio)
    ratio = [100 * r / m if m else None for r, m in zip(rs, ma, strict=True)]
    first = next(i for i, v in enumerate(ratio) if v is not None)
    valid = [v for v in ratio[first:]]
    mom_ma = _sma(valid, l_mom)
    mom = [None] * first + [100 * r / m if m else None for r, m in zip(valid, mom_ma, strict=True)]
    pts = [(d, ratio[i], mom[i]) for i, (d, _v) in enumerate(both) if ratio[i] is not None and mom[i] is not None]
    return pts[-tail:] if len(pts) >= tail else None


def quadrant(x: float, y: float) -> str:
    if x >= 100:
        return "Outperform" if y >= 100 else "Weakening"
    return "Improving" if y >= 100 else "Underperform"


# ---- view (pure) -----------------------------------------------------------------------------------------------

def clean(params: dict) -> dict:
    """Coerce untrusted query values into safe, known ones."""
    sector = params.get("sector") if params.get("sector") in SECTOR_INDEX else ""
    freq = params.get("freq") if params.get("freq") in FREQS else "daily"
    try:
        tail = int(params.get("tail", TAIL_DEFAULT))
    except (TypeError, ValueError):
        tail = TAIL_DEFAULT
    return {"sector": sector, "freq": freq, "tail": max(TAIL_MIN, min(TAIL_MAX, tail))}


def build_view(raw: dict, p: dict) -> dict:
    """Items, the table rows and the two chart widths. Items without enough history are listed in `dropped`, never guessed."""
    l_ratio, l_mom = WINDOWS[p["freq"]]
    weekly = p["freq"] == "weekly"
    bench = to_weekly(raw["bench"]) if weekly else raw["bench"]
    series = raw["series"]
    if p["sector"]:                                    # stocks: the most liquid ones only
        liquid = sorted((t for t in series if raw["avg_value"].get(t, 0) >= LIQUID_AVG_VALUE), key=lambda t: -raw["avg_value"][t])
        series = {t: series[t] for t in liquid[:MAX_STOCKS]}
    items, dropped = [], []
    for name, s in sorted(series.items()):
        pts = rrg_points(to_weekly(s) if weekly else s, bench, l_ratio=l_ratio, l_mom=l_mom, tail=p["tail"])
        if pts is None:
            dropped.append(name)
            continue
        x, y = pts[-1][1], pts[-1][2]
        items.append({"label": name, "short": name if p["sector"] else SECTOR_INDEX[name].removeprefix("IDX"), "quad": quadrant(x, y), "x": x, "y": y, "pts": [(a, b) for _d, a, b in pts],
                      "d_x": x - pts[0][1], "d_y": y - pts[0][2], "date": pts[-1][0]})
    rows = []
    for it in sorted(items, key=lambda i: (QUADRANT_ORDER.index(i["quad"]), -i["x"])):
        rows.append({"label": it["label"], "quad": it["quad"], "x": f"{it['x']:.1f}", "y": f"{it['y']:.1f}",
                     "d_x": f"{it['d_x']:+.1f}", "d_x_c": fmt.signcls(it["d_x"]), "d_y": f"{it['d_y']:+.1f}", "d_y_c": fmt.signcls(it["d_y"])})
    chart = charts.rotation_chart(items, p["tail"])
    chart_m = charts.rotation_chart(items, p["tail"], width=charts.W_MOBILE, height=340, uid="rot-m", tips=False)
    counts = {q: sum(1 for i in items if i["quad"] == q) for q in QUADRANT_ORDER}
    return {"items": items, "rows": rows, "dropped": dropped, "chart": chart, "chart_m": chart_m, "counts": counts,
            "latest": raw["latest"].isoformat() if raw["latest"] else None,
            "last_bar": items[0]["date"].isoformat() if items else None,
            "windows": f"{l_ratio} and {l_mom}", "unit": "weeks" if weekly else "sessions",
            "n_shown": len(items), "bench_name": ("the Jakarta Composite" if not p["sector"] else f"the {SECTOR_INDEX.get(p['sector'], '')} index")}


def chips(p: dict) -> dict:
    """Link targets for the controls: one per sector, one per frequency; the current choice is marked."""
    from urllib.parse import urlencode

    def href(**over):
        q = {"sector": p["sector"], "freq": p["freq"], "tail": p["tail"], **over}
        q = {k: v for k, v in q.items() if v not in ("", None) and not (k == "tail" and v == TAIL_DEFAULT) and not (k == "freq" and v == "daily")}
        return "?" + urlencode(q) if q else "?"

    return {
        "sectors": [{"label": "All sectors", "on": p["sector"] == "", "href": href(sector="")},
                    *[{"label": s, "on": p["sector"] == s, "href": href(sector=s)} for s in SECTOR_INDEX]],
        "freqs": [{"label": f.capitalize(), "on": p["freq"] == f, "href": href(freq=f)} for f in FREQS],
    }
