"""Per-stock detail page data (RENCANA-ROMBAK-UI-v3 §3.2).

`load()` is the only DB-touching part (a handful of small indexed reads, all by ticker); `build_view()` is pure — it
turns the raw rows into what the template shows (formatted cells, plain-language summary, the mechanical risk plan),
so it is unit-tested without a database. Read-only; no new methodology: every number already exists in the tables.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .. import broker_bounds, risk
from ..engine import BPI_HIGH, WASH_OVERLAP_MIN
from ..engine.scoring import SCORING_COLUMNS
from ..sources.vektorgauss import FOREIGN_BROKERS, RETAIL_BROKERS
from . import bpi_window, brokermap, charts, edge, fmt, live_scores
from .glossary import plain_tag, quad_key, quad_word
from .narrative import broker_summary, stock_summary

TICKER_RE = re.compile(r"^[A-Z0-9]{3,6}$")
HISTORY_DAYS = 190
OWNERSHIP_MONTHS = 24
DISCLOSURE_ROWS = 12
CHART_SESSIONS = 90


def _f(v):
    return None if v is None else float(v)


def _load_scoring(cur, ticker: str) -> list | None:
    """The last 10 SCORED sessions of the broker-scoring columns (session_date first), newest first. READ-ONLY.

    "Scored" matters: the newest sessions of a stock can be unscored (the broker table did not cover it that day, or the
    scores were not computed for that stretch yet), and taking the last 10 sessions regardless would then show an empty
    panel although older scores exist. The page labels it when the newest scored session is not the stock's latest one.

    None = the columns do not exist yet: they are created by `vg init-db`, which the EOD runs, so until the first EOD of
    the scoring release a SELECT naming them would fail and take the whole page down with it. We ask the catalogue
    first and only then select. [] = the columns exist but this stock has no row."""
    cur.execute(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'signal_components' AND column_name = ANY(%s)",
        (list(SCORING_COLUMNS),))
    if cur.fetchone()[0] != len(SCORING_COLUMNS):
        return None
    cur.execute(
        f"SELECT session_date, {', '.join(SCORING_COLUMNS)} FROM signal_components "
        f"WHERE ticker = %s AND num_nonnulls({', '.join(SCORING_COLUMNS)}) > 0 ORDER BY session_date DESC LIMIT 10",
        (ticker,))
    return cur.fetchall()


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
        scoring = _load_scoring(cur, ticker)
        scoring_live: set = set()
        if scoring is not None:                          # sessions without stored scores: computed live from the stored ladders (SELECT only)
            have_days = {r[0] for r in scoring}
            live = live_scores.live_rows(cur, ticker, [b[0] for b in bars[:10] if b[0] not in have_days])
            scoring_live = {r[0] for r in live}
            scoring = sorted([*live, *scoring], key=lambda r: r[0], reverse=True)[:10]
        cur.execute(
            "SELECT b.session_date, b.open, b.high, b.low, b.close, b.volume, COALESCE(a.split_factor, 1) "
            "FROM price_bars b LEFT JOIN price_adj a USING (session_date, ticker) "
            "WHERE b.ticker = %s AND b.close > 0 AND b.high > 0 AND b.low > 0 ORDER BY b.session_date DESC LIMIT %s",
            (ticker, CHART_SESSIONS))
        chart_bars = cur.fetchall()[::-1]
        bpi_levels, bpi_values = [], []
        if chart_bars:                                   # the daily BPI under the candles: same ladders, same formula as everywhere
            cur.execute("SELECT session_date, side, broker_code, val FROM broker_levels WHERE ticker = %s AND session_date >= %s",
                        (ticker, chart_bars[0][0]))
            bpi_levels = cur.fetchall()
            cur.execute("SELECT session_date, value, nonreg_value FROM price_bars WHERE ticker = %s AND session_date >= %s",
                        (ticker, chart_bars[0][0]))
            bpi_values = cur.fetchall()
        cur.execute(
            "SELECT sc.session_date, sc.nff_d0, sc.bkr_foreign_net_lo, sc.bkr_foreign_net_hi, pb.value "
            "FROM signal_components sc JOIN price_bars pb ON pb.session_date = sc.session_date AND pb.ticker = sc.ticker "
            "WHERE sc.ticker = %s ORDER BY sc.session_date DESC LIMIT %s", (ticker, CHART_SESSIONS))
        chart_flow = cur.fetchall()[::-1]
        chart_brokers = None
        if chart_flow:                                   # the per-broker rows behind the "net flow by group" chart
            first = chart_flow[0][0]
            cur.execute("SELECT session_date, broker_code, buy_val, sell_val FROM broker_flows "
                        "WHERE ticker = %s AND session_date >= %s", (ticker, first))
            bf = cur.fetchall()
            cur.execute("SELECT session_date, side, broker_code, val FROM broker_levels "
                        "WHERE ticker = %s AND session_date >= %s", (ticker, first))
            chart_brokers = {"flows": bf, "levels": cur.fetchall()}
        cur.execute(
            "SELECT session_date, tag, ret_h5, ret_vs_ihsg_h5 FROM signal_log WHERE ticker = %s AND session_date >= %s "
            "ORDER BY session_date DESC, tag", (ticker, latest - timedelta(days=HISTORY_DAYS)))
        tags = cur.fetchall()
        flagged_today = sorted({r[1] for r in tags if r[0] == latest})
        edge_rows = edge.load_cur(cur, flagged_today) if flagged_today else {}
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
            "SELECT month_end, detail FROM ownership_monthly WHERE ticker = %s AND detail IS NOT NULL "
            "ORDER BY month_end DESC LIMIT %s", (ticker, OWNERSHIP_MONTHS))
        own_series = cur.fetchall()[::-1]
        cur.execute(
            "SELECT h.filed_date, h.reporter_name, h.is_insider, h.is_controller, h.position, h.direction, h.transaction_type, "
            "h.shares_qty, h.price, h.transaction_date, h.shares_after, h.voting_rights_before, h.voting_rights_after, f.pdf_url "
            "FROM disclosure_holdings h JOIN disclosure_filings f USING (no_pengumuman) WHERE h.ticker = %s "
            "ORDER BY h.filed_date DESC, h.no_pengumuman DESC, h.row_seq LIMIT %s", (ticker, DISCLOSURE_ROWS))
        disc_rows = cur.fetchall()
        cur.execute("SELECT count(*) FROM disclosure_holdings WHERE ticker = %s", (ticker,))
        disc_total = cur.fetchone()[0]
        cur.execute("SELECT filed_date, party, title, pdf_url FROM disclosure_filings WHERE ticker = %s "
                    "ORDER BY filed_at DESC LIMIT %s", (ticker, DISCLOSURE_ROWS))
        disc_filings = cur.fetchall()
        cur.execute(
            "SELECT ex_date, type, exercise_ratio, proceed_ratio FROM corp_actions WHERE ticker = %s "
            "ORDER BY ex_date DESC LIMIT 5", (ticker,))
        corp = cur.fetchall()
        bmap_day, bmap_rows = brokermap.load(cur, ticker)      # the complete broker table (broker_flows_full), newest session
    return {"chart_bars": chart_bars, "chart_flow": chart_flow, "chart_brokers": chart_brokers,
            "bpi_levels": bpi_levels, "bpi_values": bpi_values, "research": research_verdicts(flagged_today),
            "bmap_day": bmap_day, "bmap_rows": bmap_rows,
            "ticker": ticker, "meta": meta, "bars": bars, "comps": comps, "tags": tags, "levels": levels,
            "levels_day": lvl_day, "ownership": own, "corp": corp, "latest": latest, "scoring": scoring, "scoring_live": scoring_live, "edge": edge_rows,
            "own_series": own_series, "disclosures": {"rows": disc_rows, "total": disc_total, "filings": disc_filings}}


def research_verdicts(tags: list[str]) -> dict[str, str] | None:
    """{tag: verdict} from the stored `vg research --save` result (all stocks, full sample) for today's patterns. None = no stored
    research (or it cannot be read): the summary then says the patterns are not researched yet. READ-ONLY, never raises."""
    if not tags:
        return {}
    try:
        from ..research import load_snapshot

        snap = load_snapshot()
    except Exception:  # noqa: BLE001 - a missing table or a DB hiccup must not take the stock page down
        return None
    if not snap:
        return None
    res = ((snap[0] or {}).get("results") or {}).get("all", {}).get("full", {})
    return {t: res[t]["strength"] for t in tags if t in res and res[t].get("strength")}


def broker_reading(raw: dict, tags_today: list[str]) -> dict:
    """The inputs of the Executive Summary: the five-session BPI window, the persistent net buyer, today's HAKA / divergence / wash /
    crossing, and the evidence reading, all from rows already loaded for this page. Pure."""
    from . import evidence, patterns

    latest = raw["latest"]
    days = [r[0] for r in raw.get("chart_bars") or []][-bpi_window.WINDOW:]
    vals = {r[0]: (_f(r[1]), _f(r[2]) if len(r) > 2 else None) for r in raw.get("bpi_values") or []}
    lad: dict = {}
    for d, side, code, val in raw.get("bpi_levels") or []:
        if d in days:
            lad.setdefault(d, ([], []))[0 if side == "B" else 1].append((code, _f(val)))
    crossing = frozenset(d for d in days if (s := patterns.crossing_share(*vals.get(d, (None, None)))) is not None
                         and s >= patterns.CROSSING_SHARE)
    win = bpi_window.window_score({d: (b, s, vals.get(d, (None, None))[0]) for d, (b, s) in lad.items()},
                                  n_window=len(days) or bpi_window.WINDOW, crossing=crossing) if lad else None
    net = None
    flows = (raw.get("chart_brokers") or {}).get("flows") or []
    if flows:
        per: dict = {}
        for d, code, bv, sv in flows:
            if d in days:
                per.setdefault(d, {})[code] = (_f(bv) or 0.0) - (_f(sv) or 0.0)
        net = patterns.persistent_net_buyer(per, len(days), {d: v[0] for d, v in vals.items()})
    sc = raw.get("scoring") or []
    today = dict(zip(SCORING_COLUMNS, sc[0][1:], strict=True)) if sc and sc[0][0] == latest else {}
    haka = _f(today.get("aggr_haka_share"))
    cross_today = patterns.crossing_share(*vals.get(latest, (None, None)))
    ev = evidence.read(win, net, haka=haka, retail_div=today.get("retail_div_state"), tags=tags_today, crossing=cross_today)
    return {"win": win, "net": net if net is not None and patterns.is_persistent(net) else None, "haka": haka,
            "overlap": today.get("bpi_overlap3"), "wash": "AKUM-NETRAL-WASH" in tags_today, "crossing": cross_today,
            "retail_div": _DIV_WORDS.get(today.get("retail_div_state") or ""), "evidence": ev}


# retail_div_state (engine/scoring.py) -> words for the page. Descriptive only.
_DIV_WORDS = {
    "retail_buying_institutions_selling": "Retail buying, foreign institutions selling",
    "retail_selling_institutions_buying": "Retail selling, foreign institutions buying",
    "both_buying": "Both buying",
    "both_selling": "Both selling",
    "indeterminate": "Not settled by the data",
}
_NA = "N/A"


def _range(lo, hi) -> str:
    """'-133.2B to -127.0B', a single figure when both ends coincide, N/A when the bounds are missing."""
    lo, hi = _f(lo), _f(hi)
    if lo is None or hi is None:
        return _NA
    a, b = fmt.idr(lo), fmt.idr(hi)
    return a if a == b else f"{a} to {b}"


def _pts(x) -> dict:
    """A concentration index in [-1, 1] shown as percentage points of the day's traded value, with a sign and class."""
    x = _f(x)
    return {"t": f"{x * 100:+.1f} pts", "c": fmt.signcls(x)} if x is not None else {"t": _NA, "c": ""}


def _score_row(day, vals: tuple, live: bool = False) -> dict:
    """One session of scoring columns -> display cells. Every missing measure becomes N/A, never an error."""
    r = dict(zip(SCORING_COLUMNS, vals, strict=True))
    bpi3, overlap = _f(r["bpi3"]), r["bpi_overlap3"]
    haka, ratio, cov = _f(r["aggr_haka_share"]), _f(r["aggr_ratio"]), _f(r["aggr_coverage"])
    return {
        "date": day.isoformat(), "live": live,
        "has_bpi": bpi3 is not None, "has_aggr": haka is not None, "has_div": r["retail_div_state"] is not None,
        "bpi3": _pts(bpi3), "bpi5": _pts(r["bpi5"]), "bpi_points": None if bpi3 is None else round(bpi3 * 100, 1),
        "haka_pct": None if haka is None else round(max(0.0, min(1.0, haka)) * 100),
        "overlap": f"{overlap} of 3" if overlap is not None else _NA,
        # same_houses = the overlap alone (what the caption states); wash = overlap AND a high index (the tag's rule)
        "same_houses": bool(overlap is not None and overlap >= WASH_OVERLAP_MIN),
        "wash": bool(bpi3 is not None and overlap is not None and overlap >= WASH_OVERLAP_MIN and bpi3 >= BPI_HIGH),
        "haka": fmt.pct_plain(haka, 0) if haka is not None else _NA,
        "ratio": f"{ratio:.2f}" if ratio is not None else _NA,
        "coverage": fmt.pct_plain(cov, 0) if cov is not None else _NA,
        "retail": _range(r["retail_net_lo"], r["retail_net_hi"]),
        "inst": _range(r["finst_net_lo"], r["finst_net_hi"]),
        "div": _DIV_WORDS.get(r["retail_div_state"] or "", _NA),
    }


def overlap_matrix(levels: list, day) -> dict | None:
    """Top-3 buyers next to top-3 sellers on the latest broker session. `levels` rows: (side, rank, code, name, value), rank 1 first.
    A broker that appears on both sides is `shared`; `shared_n` of them make the day an alert from `WASH_OVERLAP_MIN` on (the same
    rule as the shared-houses measure). None when there is no broker ladder for the stock."""
    buy = [r for r in levels or [] if r[0] == "B"][:3]
    sell = [r for r in levels or [] if r[0] == "S"][:3]
    if not buy and not sell:
        return None
    b_codes, s_codes = {r[2] for r in buy}, {r[2] for r in sell}
    shared = b_codes & s_codes

    def side(rows):
        return [{"rank": i + 1, "code": r[2], "name": r[3] or "", "val": fmt.idr(_f(r[4])).lstrip("+"), "shared": r[2] in shared}
                for i, r in enumerate(rows)]

    rows = [{"rank": i + 1, "buy": (side(buy) + [None] * 3)[i], "sell": (side(sell) + [None] * 3)[i]} for i in range(3)]
    n = len(shared)
    return {"day": day.isoformat() if day else None, "rows": rows, "shared_n": n, "alert": n >= WASH_OVERLAP_MIN,
            "shared": sorted(shared)}


def edge_card(data: dict | None, tags_today_view: list[dict]) -> dict | None:
    """The "Signal Historical Edge" card: for each pattern flagged today, win rate and expectancy at H+1/H+3/H+5/H+10. None = no pattern
    today or no stored result for any of them (the page then says so)."""
    if not tags_today_view:
        return None
    rows = []
    for t in tags_today_view:
        d = (data or {}).get(t["code"])
        if d:
            r = edge.row_view(t["code"], d)
            r["name"] = t["name"]
            rows.append(r)
    return {"rows": rows, "horizons": [f"H+{h}" for h in edge.HORIZONS], "small_n": edge.SMALL_N} if rows else None


def scoring_view(rows: list | None, latest: date, tags_today: list[str], live_days=frozenset()) -> dict:
    """The broker-scoring panel. Pure. Three states, so the page always renders:
    absent = the columns are not in the database yet; empty = they are, but this stock has no scored session;
    ok = at least one scored session (`current` is the newest one, `history` the last 10)."""
    if rows is None:
        return {"state": "absent"}
    scored = [(r[0], r[1:]) for r in rows if any(v is not None for v in r[1:])]
    if not scored:
        return {"state": "empty"}
    view = [_score_row(d, v, d in live_days) for d, v in scored]
    cur = view[0]
    return {
        "state": "ok", "current": {**cur, "gauge": charts.bpi_gauge(cur["bpi_points"])}, "history": view,
        "stale": scored[0][0] != latest,                         # newest scored session is not the stock's latest session
        "wash_today": "AKUM-NETRAL-WASH" in tags_today,
        "bpi_high_pts": f"{BPI_HIGH * 100:.0f}", "wash_min": WASH_OVERLAP_MIN,
    }


def _ownership_row(o) -> dict:
    """KSEI gives share counts per holder category; show each category's share of the three shown together."""
    vals = [_f(o[1]), _f(o[2]), _f(o[3])]
    total = sum(v for v in vals if v)
    cells = [fmt.pct_plain(v / total, 1) if (v is not None and total) else "—" for v in vals]
    return {"month": o[0].strftime("%b %Y"), "indiv": cells[0], "inst": cells[1], "foreign": cells[2]}


def ownership_series(rows) -> list[dict]:
    """[(month_end, detail json)] oldest first -> [{d, local, foreign}] in shares. A month whose detail is missing or empty is skipped."""
    out = []
    for month_end, detail in rows or []:
        if not isinstance(detail, dict):
            continue
        local, foreign = detail.get("local") or {}, detail.get("foreign") or {}
        if not any(v for v in (*local.values(), *foreign.values())):
            continue
        out.append({"d": month_end, "local": {k: _f(v) for k, v in local.items()}, "foreign": {k: _f(v) for k, v in foreign.items()},
                    "scripless": _f(detail.get("scripless_pct"))})
    return out


def ownership_view(rows) -> dict | None:
    """The balance-position panel: two chart widths, the latest month as a table, and how the three big groups moved over 12 months.
    None when there is no usable month (the template then keeps the plain table only)."""
    months = ownership_series(rows)
    chart, chart_m = charts.ownership_stack(months), charts.ownership_stack(months, width=charts.W_MOBILE, uid="own-m", tips=False)
    if not chart:
        return None
    last = months[-1]
    total = sum(charts.group_shares(last).values())
    legend = []
    for code in charts.OWNER_ORDER:
        loc, fgn = (last["local"].get(code) or 0), (last["foreign"].get(code) or 0)
        legend.append({"label": charts.OWNER_LABEL[code], "color": charts.OWNER_COLOR[code],
                       "local": fmt.pct_plain(loc / total, 1) if total else "—",
                       "foreign": fmt.pct_plain(fgn / total, 1) if total else "—"})
    year_ago = next((m for m in reversed(months[:-1]) if (last["d"].year - m["d"].year) * 12 + last["d"].month - m["d"].month >= 12), None)
    now_g = charts.group_shares(last)
    then_g = charts.group_shares(year_ago) if year_ago else None
    shift = []
    for key, label, color in charts.GROUP_STRIP[::-1]:
        now = now_g[key] / total
        row = {"label": label, "color": color, "now": fmt.pct_plain(now, 1), "then": None, "delta": None, "delta_c": ""}
        if then_g and sum(then_g.values()):
            then = then_g[key] / sum(then_g.values())
            pts = round((now - then) * 100, 1) + 0.0                  # + 0.0 turns -0.0 into 0.0, so a flat group never prints "-0.0 pts"
            row.update(then=fmt.pct_plain(then, 1), delta=f"{pts:+.1f} pts", delta_c="" if pts == 0 else fmt.signcls(pts))
        shift.append(row)
    return {"chart": chart, "chart_m": chart_m, "legend": legend, "shift": shift, "latest": last["d"].strftime("%b %Y"),
            "since": year_ago["d"].strftime("%b %Y") if year_ago else None, "n_months": len(months),
            "total_lots": f"{total / charts.SHARES_PER_LOT:,.0f}",
            "scripless": fmt.pct_plain(last["scripless"], 1) if last.get("scripless") is not None else None}


def _vr(x) -> str:
    """Voting-right fraction as a percentage; small holdings keep four decimals so 0.0346% does not become 0.03%."""
    x = _f(x)
    if x is None:
        return "—"
    return f"{x * 100:.4f}%" if abs(x) < 0.001 else f"{x * 100:.2f}%"


_DIRECTION = {"buy": ("Bought", "pos"), "sell": ("Sold", "neg")}
# The filing's own job title, in English. It is only meaningful for insiders: for every other reporter the parser reads an address
# note ("Alamat Identitas : Tidak ditampilkan") into the same field, so the title is never shown for them.
_POSITION = {"Direksi": "Director", "Dewan Komisaris": "Commissioner"}


def _role(insider, position) -> str:
    if insider is None:
        return "Reporter type not read"
    return _POSITION.get(position or "", "Director / commissioner") if insider else "Substantial holder (5%+)"


def disclosure_view(d: dict | None) -> dict:
    """Ownership filings for the stock page. state: parsed (rows with names and numbers), filings (only the filing list, the PDF was not
    parsed), empty (nothing filed). Pure."""
    d = d or {}
    rows, filings, total = d.get("rows") or [], d.get("filings") or [], d.get("total") or 0
    if rows:
        out = []
        for (filed, who, insider, controller, position, direction, ttype, qty, price, tdate, after, vr_b, vr_a, pdf) in rows:
            word, cls = _DIRECTION.get(direction or "", ("Other", ""))
            out.append({
                "filed": filed.isoformat(), "who": who or "—",
                "role": _role(insider, position), "controller": bool(controller),
                "action": word, "action_c": cls, "raw_type": ttype or "",
                "qty": f"{_f(qty):,.0f}" if qty is not None else "—", "price": fmt.price(price) if price is not None else "—",
                "tdate": tdate.isoformat() if tdate else "—",
                "holding": f"{_vr(vr_b)} → {_vr(vr_a)}" if (vr_b is not None or vr_a is not None) else "—",
                "after_shares": f"{_f(after):,.0f}" if after is not None else "", "pdf": pdf or "",
            })
        return {"state": "parsed", "rows": out, "total": total, "shown": len(out)}
    if filings:
        return {"state": "filings", "total": len(filings), "shown": len(filings), "rows": [
            {"filed": f.isoformat(), "party": party or "—", "title": title or "", "pdf": pdf or ""} for f, party, title, pdf in filings]}
    return {"state": "empty", "rows": [], "total": 0, "shown": 0}


def group_series(raw: dict) -> dict | None:
    """Per-day certain net ranges for foreign houses / domestic retail houses / everyone else, and the round-trip floor,
    all as a share of the day's traded value. Pure. A day whose broker rows are an exact copy of the previous day's
    (the feed once repeated yesterday's table) is left out and counted in `hidden`, so a repeat never looks like new data.
    Returns None when there is nothing to draw."""
    cb, flow = raw.get("chart_brokers"), raw.get("chart_flow") or []
    if not cb or not flow:
        return None
    flows: dict[date, list] = {}
    levels: dict[date, list] = {}
    for d, code, buy, sell in cb["flows"]:
        flows.setdefault(d, []).append((code, _f(buy), _f(sell)))
    for d, side, code, val in cb["levels"]:
        levels.setdefault(d, []).append((side, code, _f(val)))
    days = [r[0] for r in flow]
    value = {r[0]: _f(r[4]) for r in flow}
    out: dict = {"days": days, "foreign": ([], []), "retail": ([], []), "other": ([], []), "churn": [], "hidden": 0,
                 "value": [value.get(d) for d in days]}
    prev_sig = None
    for d in days:
        rows, lv = flows.get(d), levels.get(d)
        res = floor = None
        if rows and lv and value.get(d):
            sig = (sorted(rows, key=lambda r: r[0]), sorted(lv, key=lambda r: (r[0], r[1])))
            if sig == prev_sig:
                out["hidden"] += 1
            else:
                res = broker_bounds.group_net_bounds(rows, lv, FOREIGN_BROKERS, RETAIL_BROKERS)
                floor = broker_bounds.roundtrip_floor(rows, lv)
            prev_sig = sig
        for name in ("foreign", "retail", "other"):
            lo, hi = out[name]
            lo.append(res[name][0] / value[d] if res else None)
            hi.append(res[name][1] / value[d] if res else None)
        out["churn"].append(floor / value[d] if floor is not None else None)
    if not any(v is not None for v in out["foreign"][0]):
        return None
    return out


def range_summary(rows) -> dict | None:
    """What a run of daily guaranteed ranges settles, in words and numbers. `rows` = [(lo, hi)] in rupiah, days without data left out.
    Day counts say how often the direction was certain; the cumulative range is the sum of the daily lows and of the daily highs, which is
    itself guaranteed to contain the period's net flow. None when there is no day."""
    rows = [(a, b) for a, b in rows if a is not None and b is not None]
    if not rows:
        return None
    buy, sell = sum(1 for a, _ in rows if a > 0), sum(1 for _, b in rows if b < 0)
    cum_lo, cum_hi = sum(a for a, _ in rows), sum(b for _, b in rows)
    if cum_lo > 0:
        verdict, cls = "net buying over the whole period is certain", "pos"
    elif cum_hi < 0:
        verdict, cls = "net selling over the whole period is certain", "neg"
    else:
        verdict, cls = "the data cannot say whether it was net buying or net selling over the whole period", ""
    return {"n": len(rows), "buy": buy, "sell": sell, "unclear": len(rows) - buy - sell, "cum_lo": fmt.idr(cum_lo),
            "cum_hi": fmt.idr(cum_hi), "verdict": verdict, "cls": cls}


GROUP_LABELS = (("foreign", "Foreign houses"), ("retail", "Domestic retail brokers"), ("other", "All other brokers"))


def group_summary(g: dict | None) -> list[dict]:
    """One `range_summary` per broker group, in rupiah (the chart data is a share of each day's traded value)."""
    out = []
    for key, label in GROUP_LABELS if g else ():
        lo, hi = g[key]
        rows = [(a * v, b * v) if (a is not None and b is not None and v) else (None, None) for a, b, v in zip(lo, hi, g["value"], strict=True)]
        s = range_summary(rows)
        if s:
            out.append({"label": label, **s})
    return out


def _build_charts(raw: dict, plan, g: dict | None = None) -> dict:
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
    flows = [_f(r[1]) for r in flow]
    m = charts.W_MOBILE                                   # a narrower variant keeps the text legible on phones (CSS swaps them)
    # BPI under the candles: the candle days exactly (candles drops bars without a high/low; so do we), points per day or None
    bpi = bpi_window.daily_bpi(raw.get("bpi_levels") or [], raw.get("bpi_values") or [])
    cdays = [b["d"] for b in bars if b["h"] and b["l"]]
    cpts = [None if bpi.get(d) is None else round(bpi[d] * 100, 1) for d in cdays]
    def group_chart(width: int, uid: str):
        if not g:
            return None
        nets = [("Foreign houses", *g["foreign"]), ("Domestic retail brokers", *g["retail"]), ("All other brokers", *g["other"])]
        return charts.group_panels(g["days"], nets, ("Same brokers on both sides (at least this share)", g["churn"]), width=width, uid=uid)

    return {
        "groups": group_chart(charts.W, "groups"),
        "groups_m": group_chart(m, "groups-m"),
        "price": charts.candles(bars, levels=levels, tag_counts=counts),
        "price_m": charts.candles(bars, levels=levels, tag_counts=counts, width=m, uid="price-m"),
        "bpi": charts.bpi_bars(cdays, cpts),
        "bpi_m": charts.bpi_bars(cdays, cpts, width=m, uid="bpi-m"),
        "flow": charts.flow_bars(days, flows),
        "flow_m": charts.flow_bars(days, flows, width=m, uid="flow-m"),
        "band": charts.bounds_band(days, lo, hi),
        "band_m": charts.bounds_band(days, lo, hi, width=m, uid="band-m"),
    }


def _pct_of(a, b):
    return (a / b - 1) if (a is not None and b) else None


def build_view(raw: dict, track: dict | None = None, *, balance: float | None = None,
               risk_pct: float = 0.01, fee: float = risk.DEFAULT_FEE, stop_mult: float = risk.STOP_ATR_MULT,
               r_multiples: tuple[float, ...] = risk.R_MULTIPLES) -> dict:
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
        plan = risk.build_plan(close, atr, k=stop_mult, r_multiples=r_multiples)
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
            "warnings": list(plan.warnings), "tick": risk.tick_size(plan.ref), "k": f"{stop_mult:g}", "fee": f"{fee * 100:.1f}%",
            "lots": None if lots is None else {
                "lots": lots.lots, "shares": f"{lots.shares:,}", "value": f"{lots.position_value:,.0f}",
                "max_loss": f"{lots.max_loss:,.0f}", "capped": lots.capped_by_balance},
        }

    groups = group_series(raw)
    charts_view = _build_charts(raw, plan, groups)
    reading = broker_reading(raw, tags_today)
    h5 = edge.HORIZONS.index(5)
    edge_h5 = {t: edge.row_view(t, d)["cells"][h5] for t, d in (raw.get("edge") or {}).items()}
    bm = brokermap.view(brokermap.build(raw.get("bmap_rows") or [], FOREIGN_BROKERS, RETAIL_BROKERS))
    return {
        "bmap": bm, "bmap_day": raw["bmap_day"].isoformat() if raw.get("bmap_day") else None,
        "bmap_stale": bool(raw.get("bmap_day") and raw["bmap_day"] != raw["latest"]),
        "exec": broker_summary(reading, tags_today, raw.get("research"), edge_h5, plain_tag), "evidence": reading["evidence"],
        "charts": charts_view, "group_hidden": groups["hidden"] if groups else 0,
        "band_summary": range_summary([(_f(r[2]), _f(r[3])) for r in raw.get("chart_flow") or []]),
        "group_summary": group_summary(groups),
        "ticker": t, "name": name, "sector": sector, "latest": raw["latest"].isoformat(),
        "close": fmt.price(close), "chg": fmt.cell(chg, 1), "comp": comp_view, "tags_today": tags_today_view,
        "summary": stock_summary(summary_in), "recent": recent, "history": history, "buyers": buyers,
        "sellers": sellers, "levels_day": raw["levels_day"].isoformat() if raw["levels_day"] else None,
        "ownership": own, "corp": [{"date": c[0].isoformat(), "type": c[1], "ratio": c[3] or c[2] or ""} for c in raw["corp"]],
        "risk": risk_view, "has_atr": plan is not None,
        "scoring": scoring_view(raw.get("scoring"), raw["latest"], tags_today, raw.get("scoring_live") or frozenset()),
        "matrix": overlap_matrix(raw["levels"], raw["levels_day"]),
        "edge": edge_card(raw.get("edge"), tags_today_view),
        "own_chart": ownership_view(raw.get("own_series")),
        "disclosures": disclosure_view(raw.get("disclosures")),
    }
