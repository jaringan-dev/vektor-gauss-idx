"""Per-stock detail page data (RENCANA-ROMBAK-UI-v3 §3.2).

`load()` is the only DB-touching part (a handful of small indexed reads, all by ticker); `build_view()` is pure — it
turns the raw rows into what the template shows (formatted cells, plain-language summary, the mechanical risk plan),
so it is unit-tested without a database. Read-only; no new methodology: every number already exists in the tables.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .. import risk
from . import charts, fmt
from .glossary import plain_tag, quad_key, quad_word
from .narrative import stock_summary

TICKER_RE = re.compile(r"^[A-Z0-9]{3,6}$")
HISTORY_DAYS = 190
CHART_SESSIONS = 90


def _f(v):
    return None if v is None else float(v)


def load(ticker: str) -> dict | None:
    """Raw rows for one ticker, or None when the ticker has no price history at all."""
    from ..db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT name, sector FROM tickers WHERE ticker = %s", (ticker,))
        meta = cur.fetchone()
        cur.execute(
            "SELECT session_date, close, prev, high, low, volume, value FROM price_bars "
            "WHERE ticker = %s ORDER BY session_date DESC LIMIT 15", (ticker,))
        bars = cur.fetchall()
        if not bars:
            return None
        latest = bars[0][0]
        cur.execute(
            "SELECT session_date, nff_d0, nff_w1, quadrant, foreign_consistency_10d, atr14, bkr_cr3, "
            "bkr_foreign_net_d0, bkr_foreign_net_lo, bkr_foreign_net_hi, ksei_foreign_trend_3m, price_chg_5d "
            "FROM signal_components WHERE ticker = %s ORDER BY session_date DESC LIMIT 15", (ticker,))
        comps = cur.fetchall()
        cur.execute(
            "SELECT b.session_date, b.open, b.high, b.low, b.close, b.volume, COALESCE(a.split_factor, 1) "
            "FROM price_bars b LEFT JOIN price_adj a USING (session_date, ticker) "
            "WHERE b.ticker = %s AND b.close > 0 AND b.high > 0 AND b.low > 0 ORDER BY b.session_date DESC LIMIT %s",
            (ticker, CHART_SESSIONS))
        chart_bars = cur.fetchall()[::-1]
        cur.execute(
            "SELECT sc.session_date, sc.nff_d0, sc.bkr_foreign_net_lo, sc.bkr_foreign_net_hi, pb.value "
            "FROM signal_components sc JOIN price_bars pb ON pb.session_date = sc.session_date AND pb.ticker = sc.ticker "
            "WHERE sc.ticker = %s ORDER BY sc.session_date DESC LIMIT %s", (ticker, CHART_SESSIONS))
        chart_flow = cur.fetchall()[::-1]
        cur.execute(
            "SELECT session_date, tag, ret_h5, ret_vs_ihsg_h5 FROM signal_log WHERE ticker = %s AND session_date >= %s "
            "ORDER BY session_date DESC, tag", (ticker, latest - timedelta(days=HISTORY_DAYS)))
        tags = cur.fetchall()
        cur.execute("SELECT max(session_date) FROM broker_levels WHERE ticker = %s", (ticker,))
        lvl_day = cur.fetchone()[0]
        levels = []
        if lvl_day:
            cur.execute(
                "SELECT side, rank, broker_code, broker_name, val FROM broker_levels "
                "WHERE ticker = %s AND session_date = %s AND rank <= 5 ORDER BY side, rank", (ticker, lvl_day))
            levels = cur.fetchall()
        cur.execute(
            "SELECT month_end, local_individual, local_institution, foreign_total FROM ownership_monthly "
            "WHERE ticker = %s ORDER BY month_end DESC LIMIT 6", (ticker,))
        own = cur.fetchall()
        cur.execute(
            "SELECT ex_date, type, exercise_ratio, proceed_ratio FROM corp_actions WHERE ticker = %s "
            "ORDER BY ex_date DESC LIMIT 5", (ticker,))
        corp = cur.fetchall()
    return {"chart_bars": chart_bars, "chart_flow": chart_flow,
            "ticker": ticker, "meta": meta, "bars": bars, "comps": comps, "tags": tags, "levels": levels,
            "levels_day": lvl_day, "ownership": own, "corp": corp, "latest": latest}


def _ownership_row(o) -> dict:
    """KSEI gives share counts per holder category; show each category's share of the three shown together."""
    vals = [_f(o[1]), _f(o[2]), _f(o[3])]
    total = sum(v for v in vals if v)
    cells = [fmt.pct_plain(v / total, 1) if (v is not None and total) else "—" for v in vals]
    return {"month": o[0].strftime("%b %Y"), "indiv": cells[0], "inst": cells[1], "foreign": cells[2]}


def _build_charts(raw: dict, plan) -> dict:
    """SVG strings (or None when there is not enough data). Split/bonus-adjusted prices; risk levels drawn only if a plan exists."""
    bars = []
    for d, o, h, l, c, v, f in raw.get("chart_bars", []):
        f = float(f)
        bars.append({"d": d, "o": float(o) * f if o else None, "h": float(h) * f, "l": float(l) * f, "c": float(c) * f,
                     "v": float(v) if v is not None else 0.0})
    counts: dict[date, int] = {}
    for sd, _tag, _r5, _rv in raw["tags"]:
        counts[sd] = counts.get(sd, 0) + 1
    levels = []
    if plan:
        levels = [{"price": plan.stop, "label": "Stop", "kind": "stop"}] +                  [{"price": t.price, "label": f"{t.r:g}R", "kind": "target"} for t in plan.targets]
    flow = raw.get("chart_flow", [])
    days = [r[0] for r in flow]
    lo = [(_f(r[2]) / _f(r[4])) if (r[2] is not None and r[4]) else None for r in flow]
    hi = [(_f(r[3]) / _f(r[4])) if (r[3] is not None and r[4]) else None for r in flow]
    return {
        "price": charts.candles(bars, levels=levels, tag_counts=counts),
        "flow": charts.flow_bars(days, [_f(r[1]) for r in flow]),
        "band": charts.bounds_band(days, lo, hi),
    }


def _pct_of(a, b):
    return (a / b - 1) if (a is not None and b) else None


def build_view(raw: dict, track: dict | None = None, *, balance: float | None = None,
               risk_pct: float = 0.01, fee: float = risk.DEFAULT_FEE) -> dict:
    """Template context for one stock. `track` maps tag -> forward-test row ({status, n, edge_h5, edge_after_cost})."""
    track = track or {}
    t = raw["ticker"]
    name, sector = raw["meta"] if raw["meta"] else (None, None)
    bars = raw["bars"]
    d0 = bars[0]
    close, prev, day_value = _f(d0[1]), _f(d0[2]), _f(d0[6])
    chg = _pct_of(close, prev)
    comp = raw["comps"][0] if raw["comps"] else None

    tags_today = [r[1] for r in raw["tags"] if r[0] == raw["latest"]] if raw["tags"] else []
    tags_today_view = []
    n_passed = 0
    for code in tags_today:
        tr = track.get(code)
        if tr and tr.get("code") == "ready":
            n_passed += 1
        tags_today_view.append({"code": code, "name": plain_tag(code), "track": tr})

    summary_in = {"ticker": t, "close": close, "chg": chg, "n_tags": len(tags_today), "n_passed": n_passed}
    comp_view = None
    plan = None
    lots = None
    if comp:
        (_sd, nff_d0, nff_w1, quad, cons, atr, cr3, bkr_net, lo, hi, ksei_tr, chg5) = comp
        nff_d0, nff_w1, atr, bkr_net, lo, hi = (_f(x) for x in (nff_d0, nff_w1, atr, bkr_net, lo, hi))
        summary_in.update(nff_d0=nff_d0, nff_w1=nff_w1, consistency=cons, quadrant=quad,
                          bounds_lo=lo, bounds_hi=hi, day_value=day_value)
        comp_view = {
            "quad": quad, "quad_key": quad_key(quad), "quad_word": quad_word(quad),
            "nff_d0": fmt.idr(nff_d0), "nff_d0_c": fmt.signcls(nff_d0),
            "nff_w1": fmt.idr(nff_w1), "nff_w1_c": fmt.signcls(nff_w1),
            "cons": f"{int(cons)}/10" if cons is not None else "—",
            "cr3": fmt.pct_plain(_f(cr3), 0) if cr3 is not None else "—",
            "ksei": {"up": "↑ rising", "down": "↓ falling", "flat": "→ flat"}.get(ksei_tr or "", "no data"),
            "chg5": fmt.cell(_f(chg5), 1),
            "bounds": (f"{fmt.pct(lo / day_value, 1)} and {fmt.pct(hi / day_value, 1)} of traded value"
                       if lo is not None and hi is not None and day_value else None),
            "bkr_net_old": fmt.idr(bkr_net),
        }
        plan = risk.build_plan(close, atr)
        if plan and balance:
            lots = risk.lot_size(balance, risk_pct, plan.ref, plan.stop, fee=fee)

    recent = []
    comps_by_day = {c[0]: c for c in raw["comps"]}
    for b in bars[:10]:
        c = comps_by_day.get(b[0])
        recent.append({
            "date": b[0].isoformat(), "close": fmt.price(b[1]), "chg": fmt.cell(_pct_of(_f(b[1]), _f(b[2])), 1),
            "foreign": fmt.idr(_f(c[1])) if c else "—", "foreign_c": fmt.signcls(_f(c[1])) if c else "",
            "value": fmt.idr(_f(b[6])).lstrip("+") if b[6] is not None else "—",
        })

    # tag history: newest first, one line per day
    hist: dict[date, list[str]] = {}
    for sd, tag, _r5, _rv in raw["tags"]:
        hist.setdefault(sd, []).append(tag)
    history = [{"date": d.isoformat(), "tags": v} for d, v in sorted(hist.items(), reverse=True)[:15]]

    buyers = [{"code": r[2], "name": r[3] or "", "val": fmt.idr(_f(r[4])).lstrip("+")} for r in raw["levels"] if r[0] == "B"]
    sellers = [{"code": r[2], "name": r[3] or "", "val": fmt.idr(_f(r[4])).lstrip("+")} for r in raw["levels"] if r[0] == "S"]

    own = [_ownership_row(o) for o in raw["ownership"]]

    risk_view = None
    if plan:
        risk_view = {
            "ref": fmt.price(plan.ref), "atr": f"{plan.atr:,.1f}", "stop": fmt.price(plan.stop),
            "risk_pct": fmt.pct_plain(plan.risk_pct, 1), "risk_per_share": fmt.price(plan.risk_per_share),
            "targets": [{"r": f"{x.r:g}R", "price": fmt.price(x.price), "gain": fmt.pct(x.gain_pct, 1)} for x in plan.targets],
            "warnings": list(plan.warnings), "tick": risk.tick_size(plan.ref),
            "lots": None if lots is None else {
                "lots": lots.lots, "shares": f"{lots.shares:,}", "value": f"{lots.position_value:,.0f}",
                "max_loss": f"{lots.max_loss:,.0f}", "capped": lots.capped_by_balance},
        }

    charts_view = _build_charts(raw, plan)
    return {
        "charts": charts_view,
        "ticker": t, "name": name, "sector": sector, "latest": raw["latest"].isoformat(),
        "close": fmt.price(close), "chg": fmt.cell(chg, 1), "comp": comp_view, "tags_today": tags_today_view,
        "summary": stock_summary(summary_in), "recent": recent, "history": history, "buyers": buyers,
        "sellers": sellers, "levels_day": raw["levels_day"].isoformat() if raw["levels_day"] else None,
        "ownership": own, "corp": [{"date": c[0].isoformat(), "type": c[1], "ratio": c[3] or c[2] or ""} for c in raw["corp"]],
        "risk": risk_view, "has_atr": plan is not None,
    }
