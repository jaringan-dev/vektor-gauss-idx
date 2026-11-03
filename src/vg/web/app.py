"""Local superadmin dashboard — Layar 3: forward-test / gate tracker.

RENCANA-UI-UX §3.4. A thin, read-only FastAPI layer over `engine.eval_tags()`:
no new methodology, no writes. Single-operator; `vg serve` binds to 127.0.0.1
(§3.6). The forward-test table rendered here is the same component reused later
as the public "Rapor Publik" (§4.2), once the gate is cleared.
"""

from __future__ import annotations

import json
import re
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import engine, research, risk, risk_eval
from ..config import CONFIG
from ..db import connect
from . import coverage as cov
from . import freshness, fmt, glossary
from .auth import AuthMiddleware
from . import status as gate
from . import pattern as patternmod
from . import stock as stockmod
from .cache import TTLCache

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
glossary.install(_TEMPLATES.env)
# Popover data for the explain-in-place cards; `</` is escaped so it can never close the <script> tag early.
_GLOSSARY_JSON = json.dumps(glossary.popover_data(), ensure_ascii=False).replace("</", "<\\/")

# Sidebar (A1). `key` must equal the `active_page` each route passes to its template.
_NAV = [
    {"title": "Patterns", "items": [
        {"key": "tracker", "label": "Track record", "href": "/"},
        {"key": "risk", "label": "Stop & target test", "href": "/risk-test"},
        {"key": "research", "label": "Is the edge real?", "href": "/research"},
    ]},
    {"title": "Stocks", "items": [
        {"key": "screener", "label": "Today's stocks", "href": "/screener"},
        {"key": "ranking", "label": "Top rankings", "href": "/ranking"},
    ]},
    {"title": "Data", "items": [
        {"key": "coverage", "label": "Coverage & gaps", "href": "/coverage"},
    ]},
    {"title": "Help", "items": [
        {"key": "guide", "label": "Guide & glossary", "href": "/guide"},
    ]},
]

# Coverage heatmap (F1): per-day row counts for 4 sources; cheap but not free, so 2 minutes.
_COVERAGE_CACHE = TTLCache(ttl_seconds=120)
_COVERAGE_WINDOWS = (30, 60, 120, 250)

# FreshnessStrip (A2): four small queries, checked at most once a minute across all pages.
_STRIP_CACHE = TTLCache(ttl_seconds=60)


def _strip_ctx() -> dict:
    """Context for the global freshness strip. A failure is shown AS a red strip — never hidden,
    and never allowed to break the page it decorates."""
    try:
        items, wall, _hit = _STRIP_CACHE.get_or_compute(
            ("strip", date.today()), lambda: freshness.build_strip(freshness.load_inputs())
        )
        return {
            "items": [i.as_dict() for i in items],
            "level": freshness.worst(i.level for i in items),
            "checked_at": datetime.fromtimestamp(wall).strftime("%H:%M"),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"items": [], "level": freshness.BAD, "checked_at": None,
                "error": f"{type(exc).__name__}: {exc}"}


def _render(name: str, ctx: dict, status_code: int = 200) -> HTMLResponse:
    """Render a page inside the shell. Renders manually + wraps in HTMLResponse: avoids depending on
    the TemplateResponse positional signature, which changed across Starlette versions."""
    ctx["nav_groups"] = _NAV
    ctx["strip"] = _strip_ctx()
    ctx["glossary_json"] = _GLOSSARY_JSON
    return HTMLResponse(_TEMPLATES.get_template(name).render(ctx), status_code=status_code)

# Tag-eval tables are slow (in-sample ≈ 2.9s) but only change after `vg compute` /
# `vg backfill-outcomes`. 30-minute TTL, invalidated early by _eval_version() (which moves
# whenever outcomes are rewritten); the page always prints when the numbers were computed,
# so the cache is never hidden (B1).
_EVAL_CACHE = TTLCache(ttl_seconds=1800)


def _warm_cache() -> None:
    """Fill the tracker cache in the background at startup so the first visit is not the slow one."""
    try:
        _cached_view_rows(None)
        if CONFIG.forward_since:
            _cached_view_rows(CONFIG.forward_since)
    except Exception:  # noqa: BLE001 — DB down at startup: the first real request shows the error panel
        pass
    _strip_ctx()  # never raises; primes the freshness cache


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    threading.Thread(target=_warm_cache, daemon=True, name="vg-cache-warm").start()
    yield


app = FastAPI(title="Vektor Gauss — Superadmin", lifespan=_lifespan)
# Password gate + security headers. Credentials are read per request; with no VG_WEB_PASSWORD only the headers apply.
app.add_middleware(AuthMiddleware, creds=lambda: (CONFIG.web_user, CONFIG.web_password))


# Formatting lives in fmt.py (shared with the plain-language summaries); short aliases keep the call sites readable.
_pct, _cell, _hit, _price, _idr, _signcls = fmt.pct, fmt.cell, fmt.hit, fmt.price, fmt.idr, fmt.signcls
_TREND = {"up": "↑", "down": "↓", "flat": "→", "nodata": "?"}


def _eval_version() -> tuple:
    """Cheap change token for the eval tables: `market_breadth` is tiny (~1.6k rows) and is
    rewritten by every `vg backfill-outcomes`, so its newest computed_at + row count moves
    exactly when the underlying outcomes do."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(computed_at), count(*) FROM market_breadth")
        ts, n = cur.fetchone()
    return (str(ts), n)


def _cached_view_rows(since, fresh: bool = False) -> tuple[list[dict], float, bool]:
    """(rows, computed_at_epoch, was_cache_hit) for the tracker tables."""
    return _EVAL_CACHE.get_or_compute(
        ("tracker", since), lambda: _view_rows(since), version=_eval_version(), fresh=fresh
    )


def _view_rows(since) -> list[dict]:
    """Shape `engine.eval_tags(since)` into display rows, each with an honest verdict."""
    rows = []
    for r in engine.eval_tags(since):
        st = gate.classify(r)
        rows.append(
            {
                "tag": r["tag"],
                "n": r["n"],
                "small_n": gate.is_small_n(r["n"]),
                "n_liquid": r.get("n_liquid") or 0,
                "span_days": gate.span_days(r.get("first_date"), r.get("last_date")),
                "first_date": r.get("first_date"),
                "last_date": r.get("last_date"),
                "edge_h1": _cell(r.get("edge_h1")),
                "edge_h5": _cell(r.get("edge_h5")),
                "edge_h10": _cell(r.get("edge_h10")),
                "edge_h5_liquid": _cell(r.get("edge_h5_liquid")),
                "hit": _hit(r.get("hit_rate")),
                "univ_hit": _hit(r.get("univ_hit_rate")),
                "status": st,
                "edge_after_cost": _cell(st.edge_after_cost),
            }
        )
    return gate.order_rows(rows)


@app.get("/", response_class=HTMLResponse)
def forward_tracker(fresh: bool = False) -> HTMLResponse:
    ctx: dict = {
        "active_page": "tracker",
        "forward_since": CONFIG.forward_since,
        "cost_bp": round(gate.ROUND_TRIP_COST * 10000),
        "min_n": gate.MIN_SIGNALS,
        "min_span": gate.MIN_SPAN_DAYS,
        "error": None,
        "insample": [],
        "forward": [],
        "computed_at": None,
        "cache_hit": False,
        "cache_minutes": int(_EVAL_CACHE.ttl // 60),
        "n_passed": 0,
    }
    try:
        ctx["insample"], t_in, hit_in = _cached_view_rows(None, fresh)
        if CONFIG.forward_since:
            ctx["forward"], t_fw, hit_fw = _cached_view_rows(CONFIG.forward_since, fresh)
        else:
            t_fw, hit_fw = t_in, hit_in
        ctx["n_passed"] = sum(1 for r in ctx["forward"] if r["status"].code == gate.READY)
        # Report the OLDER of the two timestamps: the page is only as fresh as its stalest table.
        ctx["computed_at"] = datetime.fromtimestamp(min(t_in, t_fw)).strftime("%d %b %Y %H:%M:%S")
        ctx["cache_hit"] = hit_in and hit_fw
    except Exception as exc:  # noqa: BLE001 — DB down / schema not applied: show it, don't hide it
        ctx["error"] = f"{type(exc).__name__}: {exc}"
    return _render("forward_test.html", ctx)


def _screener_view() -> tuple:
    """Latest signalled session + one decorated row per tagged ticker (newest flow first)."""
    sd = engine.latest_signal_date()
    wl = set(CONFIG.watchlist)
    held = set(CONFIG.holdings)
    rows: list[dict] = []
    if sd is not None:
        for r in engine.screener_rows(sd):
            close, prev = r.get("close"), r.get("prev")
            chg = (close / prev - 1) if (close and prev) else None
            cr3 = r.get("bkr_cr3")
            cons = r.get("foreign_consistency_10d")
            rows.append(
                {
                    "ticker": r["ticker"],
                    "in_wl": r["ticker"] in wl,
                    "held": r["ticker"] in held,
                    "quad": r.get("quadrant") or "",
                    "price": _price(close),
                    "chg": _cell(chg, 1),
                    "nff_d0": _idr(r.get("nff_d0")), "nff_d0_c": _signcls(r.get("nff_d0")),
                    "nff_w1": _idr(r.get("nff_w1")), "nff_w1_c": _signcls(r.get("nff_w1")),
                    "bkr_net": _idr(r.get("bkr_foreign_net_d0")),
                    "bkr_net_c": _signcls(r.get("bkr_foreign_net_d0")),
                    "cr3": f"{cr3 * 100:.0f}%" if cr3 is not None else "—",
                    "cons": f"{int(cons)}/10" if cons is not None else "—",
                    "ksei": _TREND.get(r.get("ksei_foreign_trend_3m") or "", "?"),
                    "tags": r.get("tags") or "",
                    "tag_list": glossary.split_tags(r.get("tags")),
                }
            )
    return sd, rows


@app.get("/screener", response_class=HTMLResponse)
def screener() -> HTMLResponse:
    ctx: dict = {"active_page": "screener", "error": None, "session_date": None, "rows": []}
    try:
        ctx["session_date"], ctx["rows"] = _screener_view()
    except Exception as exc:  # noqa: BLE001 — DB down / schema not applied: show it, don't hide it
        ctx["error"] = f"{type(exc).__name__}: {exc}"
    return _render("screener.html", ctx)


def _rank_rows(column: str, desc: bool) -> tuple:
    sd, total, raw = engine.rank_components(column, desc=desc, limit=15)
    rows = []
    for i, r in enumerate(raw, 1):
        close, prev = r.get("close"), r.get("prev")
        chg = (close / prev - 1) if (close and prev) else None
        rows.append(
            {
                "rank": i,
                "ticker": r["ticker"],
                "quad": r.get("quadrant") or "",
                "val": _idr(r.get("val")), "val_c": _signcls(r.get("val")),
                "price": _price(close), "chg": _cell(chg, 1),
                "tags": r.get("tags") or "",
                "tag_list": glossary.split_tags(r.get("tags")),
            }
        )
    return sd, total, rows


def _ranking_view(h: str) -> tuple:
    """Build the four ranked panels for horizon h ('1d' or '5d')."""
    h = "1d" if h == "1d" else "5d"
    fcol = "nff_d0" if h == "1d" else "nff_w1"
    bcol = "bkr_foreign_net_d0" if h == "1d" else "bkr_foreign_net_5d"
    # (title, column, desc, note)
    spec = [
        ("Biggest foreign buying", fcol, True, "net foreign flow, largest inflow (whole market)"),
        ("Biggest foreign selling", fcol, False, "net foreign flow, largest outflow (whole market)"),
        ("Foreign brokers buying most", bcol, True, "net buying by foreign brokers (broker-feed stocks only)"),
        ("Retail brokers selling most", "bkr_dom_retail_net", False,
         "net selling by domestic retail brokers (broker-feed stocks only, always 1 day)"),
    ]
    sd = None
    panels = []
    for title, col, desc, note in spec:
        s, total, rows = _rank_rows(col, desc)
        sd = sd or s
        panels.append({"title": title, "note": note, "total": total, "rows": rows})
    return sd, h, panels


@app.get("/ranking", response_class=HTMLResponse)
def ranking(h: str = "5d") -> HTMLResponse:
    ctx: dict = {"active_page": "ranking", "error": None, "session_date": None, "h": "5d", "panels": []}
    try:
        ctx["session_date"], ctx["h"], ctx["panels"] = _ranking_view(h)
    except Exception as exc:  # noqa: BLE001 — DB down / schema not applied: show it, don't hide it
        ctx["error"] = f"{type(exc).__name__}: {exc}"
    return _render("ranking.html", ctx)


def _counts(n_days: int) -> tuple[list, dict]:
    return _COVERAGE_CACHE.get_or_compute(("counts", n_days), lambda: cov.load_counts(n_days))[0]


@app.get("/coverage", response_class=HTMLResponse)
def coverage(days: int = 60) -> HTMLResponse:
    n = max(10, min(250, days))
    ctx: dict = {"active_page": "coverage", "error": None, "n_days": n, "windows": _COVERAGE_WINDOWS,
                 "rows": [], "summary": []}
    try:
        day_list, counts = _counts(n)
        ctx["rows"], ctx["summary"] = cov.grid_view(cov.build_grid(day_list, counts))
    except Exception as exc:  # noqa: BLE001 — DB down / schema not applied: show it, don't hide it
        ctx["error"] = f"{type(exc).__name__}: {exc}"
    return _render("coverage.html", ctx)


def _missing_error(message: str, status: int) -> HTMLResponse:
    return _render("coverage_missing.html", {"active_page": "coverage", "error": None,
                                             "bad_request": message, "m": None}, status_code=status)


@app.get("/coverage/missing", response_class=HTMLResponse)
def coverage_missing(d: str = Query("", alias="date"), source: str = "price") -> HTMLResponse:
    """Which tickers/indices a source is missing on a day, relative to its nearest healthy day."""
    if source not in cov.SOURCE_KEYS:
        return _missing_error(f"Unknown source: {source!r}. Options: {', '.join(cov.SOURCE_KEYS)}.", 400)
    try:
        target = date.fromisoformat(d)
    except ValueError:
        return _missing_error(f"Invalid date: {d!r}. Use the format YYYY-MM-DD.", 400)
    ctx: dict = {"active_page": "coverage", "error": None, "bad_request": None, "m": None}
    try:
        day_list, counts = _counts(250)
        if target not in day_list:
            return _missing_error(f"{target} is not one of the last 250 trading days.", 404)
        grid = cov.build_grid(day_list, counts)
        meta = grid["summary"][source]
        cell = next(r["cells"][source] for r in grid["rows"] if r["date"] == target)
        ref = cov.nearest_healthy_day(target, day_list, counts[source], meta["baseline"])
        present = cov.load_tickers(source, target)
        expected = cov.load_tickers(source, ref) if ref else set()
        missing = cov.missing_names(expected, present)
        unit = next(u for k, _l, u, _t, _c in cov.SOURCES if k == source)
        ctx["m"] = {
            "source": source, "source_label": cov.source_label(source), "unit": unit,
            "date_label": freshness.fmt_date(target), "iso": target.isoformat(),
            "cell": cell.as_dict(), "baseline": round(meta["baseline"]) if meta["baseline"] else None,
            "present": len(present), "ref_label": freshness.fmt_date(ref) if ref else None,
            "expected": len(expected), "missing_total": len(missing), "missing": missing[:400],
            "commands": cov.repair_commands(source, target),
        }
    except Exception as exc:  # noqa: BLE001
        ctx["error"] = f"{type(exc).__name__}: {exc}"
    return _render("coverage_missing.html", ctx)


def _rule_test_for(codes: list[str]) -> dict | None:
    """Evidence for the stock page: how the 2R rule performed across all stocks, and on this stock's flagged patterns."""
    payload, _computed = _risk_snapshot()
    if not payload:
        return None
    rows = {r["tag"]: r for r in payload["scopes"]["all"]}
    base = rows.get(payload["baseline"])
    if not base:
        return None
    mine = [{"tag": c, "name": glossary.plain_tag(c), "n": rows[c]["n"], "res": _rule_cells(rows[c]["r"]["2"]),
             "hold": fmt.cell(rows[c]["hold"])} for c in codes if c in rows]
    return {"window": payload["window"], "n": base["n"], "base": _rule_cells(base["r"]["2"]), "hold": fmt.cell(base["hold"]),
            "cost": f"{payload['cost'] * 100:.1f}%", "horizon": payload["horizon"], "mine": mine,
            "tested_k": f"{payload['stop_atr']:g}", "differs": abs(payload["stop_atr"] - CONFIG.atr_sl_mult) > 1e-9}


def _track_by_tag() -> dict:
    """Forward-test verdict per tag, for the pattern chips on the stock page. Never raises (the page still renders)."""
    try:
        rows, _t, _hit_ = _cached_view_rows(CONFIG.forward_since)
    except Exception:  # noqa: BLE001
        return {}
    return {
        r["tag"]: {"code": r["status"].code, "label": r["status"].label, "n": r["n"], "small_n": r["small_n"],
                   "edge_h5": r["edge_h5"], "edge_after_cost": r["edge_after_cost"]}
        for r in rows
    }


_RISK_CACHE = TTLCache(ttl_seconds=300)


def _risk_snapshot():
    """(payload, computed_at_epoch) of the saved stop/target backtest, or (None, None). Never raises."""
    try:
        snap, _wall, _hit = _RISK_CACHE.get_or_compute(("risk",), lambda: risk_eval.load_snapshot() or (None, None))
    except Exception:  # noqa: BLE001 — table missing / DB down: pages simply omit the evidence panel
        return None, None
    payload, computed = snap
    return payload, computed


def _rule_cells(r: dict) -> dict:
    return {"stop": fmt.pct_plain(r["stop_pct"], 0), "target": fmt.pct_plain(r["target_pct"], 0),
            "time": fmt.pct_plain(r["time_pct"], 0), "net": fmt.cell(r["net"]), "win": fmt.pct_plain(r["win"], 0)}


def _risk_rows(payload: dict, scope: str) -> list[dict]:
    out = []
    for row in payload["scopes"][scope]:
        out.append({"tag": row["tag"], "is_baseline": row["tag"] == payload["baseline"], "n": row["n"],
                    "hold": fmt.cell(row["hold"]), "r": {k: _rule_cells(v) for k, v in row["r"].items()}})
    return out


_RESEARCH_CACHE = TTLCache(ttl_seconds=300)


def _research_snapshot():
    """(payload, computed_at) of the saved K10-K13 analysis, or (None, None). Never raises."""
    try:
        snap, _wall, _hit = _RESEARCH_CACHE.get_or_compute(("research",), lambda: research.load_snapshot() or (None, None))
    except Exception:  # noqa: BLE001
        return None, None
    return snap


def _ci_cell(e: dict) -> dict:
    if not e or e.get("point") is None:
        return {"t": "n/a", "ci": "", "c": ""}
    ci = "" if e.get("lo") is None else f"[{e['lo'] * 100:+.2f}, {e['hi'] * 100:+.2f}]"
    return {"t": fmt.pct(e["point"]), "ci": ci, "c": fmt.signcls(e["point"]), "sig": e.get("lo") is not None and e["lo"] > 0}


def _research_row(tag: str, r: dict) -> dict:
    folds = [{"label": f["label"], "n": f["n"], "edge": fmt.cell(f["edge"]) if f["edge"] is not None else None} for f in r["folds"]]
    regimes = {k: {"n": v["n"], "edge": fmt.cell(v["edge"]) if v["edge"] is not None else None} for k, v in r["regimes"].items()}
    return {"tag": tag, "n": r["n"], "days": r["days"], "eff": r["eff_blocks"], "raw": _ci_cell(r["edge_raw"]),
            "neut": _ci_cell(r["edge_neut"]), "net": _ci_cell(r["edge_net"]), "cost": fmt.pct_plain(r["mean_cost"], 2),
            "q": "-" if r.get("q_net") is None else f"{r['q_net']:.2f}",
            "folds_pos": "-" if r.get("folds_pos") is None else f"{r['folds_pos'] * 100:.0f}%", "folds": folds, "regimes": regimes,
            "strength": r["strength"], "candidate": r["strength"].startswith("Candidate")}


@app.get("/research", response_class=HTMLResponse)
def research_page(scope: str = "all", sample: str = "full") -> HTMLResponse:
    scope = "liquid" if scope == "liquid" else "all"
    sample = "forward" if sample == "forward" else "full"
    payload, computed = _research_snapshot()
    ctx: dict = {"active_page": "research", "scope": scope, "sample": sample, "payload": payload, "error": None}
    if payload:
        res = payload["results"].get(scope, {}).get(sample, {})
        rows = [_research_row(t, r) for t, r in sorted(res.items(), key=lambda kv: -kv[1]["n"])]
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["strength"]] = counts.get(r["strength"], 0) + 1
        ctx.update(rows=rows, counts=counts, n_patterns=len(rows), n_candidates=sum(1 for r in rows if r["candidate"]),
                   computed_at=computed.strftime("%d %b %Y %H:%M") if computed else None, params=payload["params"],
                   window=payload["window"], forward_since=payload.get("forward_since"),
                   has_forward=bool(payload["results"].get(scope, {}).get("forward")))
    return _render("research.html", ctx)


@app.get("/risk-test", response_class=HTMLResponse)
def risk_test(scope: str = "all") -> HTMLResponse:
    scope = "liquid" if scope == "liquid" else "all"
    payload, computed = _risk_snapshot()
    ctx: dict = {"active_page": "risk", "scope": scope, "payload": payload, "error": None}
    if payload:
        rows = _risk_rows(payload, scope)
        base = next((r for r in payload["scopes"][scope] if r["tag"] == payload["baseline"]), None)
        big = [r for r in payload["scopes"][scope] if r["tag"] != payload["baseline"] and r["n"] >= 200]
        two = lambda r: r["r"]["2"]["net"]  # noqa: E731
        ctx.update(
            rows=rows, window=payload["window"], computed_at=computed.strftime("%d %b %Y %H:%M") if computed else None,
            cost_pct=f"{payload['cost'] * 100:.1f}%", horizon=payload["horizon"], stop_atr=f"{payload['stop_atr']:g}",
            n_total=base["n"] if base else None,
            base_net=fmt.pct(two(base)) if base else None, base_hold=fmt.pct(base["hold"]) if base else None,
            n_big=len(big), n_positive=sum(1 for r in big if two(r) > 0),
            best=max(big, key=two) if big else None, best_net=fmt.pct(two(max(big, key=two))) if big else None,
        )
    return _render("risk_test.html", ctx)


@app.get("/guide", response_class=HTMLResponse)
def guide() -> HTMLResponse:
    groups = [{"key": k, "title": title, "terms": [t for t in glossary.TERMS.values() if t.group == k]}
              for k, title in glossary.GROUPS]
    families = [{"key": k, "title": title, "tags": [t for t in glossary.TAGS.values() if t.family == k]}
                for k, title in glossary.TAG_FAMILIES.items()]
    return _render("guide.html", {"active_page": "guide", "groups": groups, "families": families,
                                  "cost_bp": round(gate.ROUND_TRIP_COST * 10000), "min_n": gate.MIN_SIGNALS,
                                  "min_span": gate.MIN_SPAN_DAYS})


def _ticker_exists(ticker: str) -> bool:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM price_bars WHERE ticker = %s LIMIT 1", (ticker,))
        return cur.fetchone() is not None


@app.get("/search", response_class=HTMLResponse)
def search(q: str = ""):
    q = q.strip()[:60]
    ctx: dict = {"active_page": "", "q": q, "tickers": [], "terms": [], "tags": [], "error": None}
    if not q:
        return _render("search.html", ctx)
    up = q.upper()
    try:
        if stockmod.TICKER_RE.match(up) and _ticker_exists(up):
            return RedirectResponse(f"/stock/{up}", status_code=303)
        like = f"%{q}%"
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker, name FROM tickers WHERE ticker ILIKE %s OR name ILIKE %s ORDER BY ticker LIMIT 20",
                        (f"{q}%", like))
            ctx["tickers"] = [{"ticker": r[0], "name": r[1] or ""} for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        ctx["error"] = f"{type(exc).__name__}: {exc}"
    ql = q.lower()
    ctx["terms"] = [t for t in glossary.TERMS.values() if ql in t.name.lower() or ql in t.key or ql in t.short.lower()][:12]
    ctx["tags"] = [t for t in glossary.TAGS.values() if ql in t.name.lower() or ql in t.tag.lower()][:12]
    return _render("search.html", ctx)


def _rule_all_targets(code: str) -> dict | None:
    """The saved stop/target test for one pattern at every target, next to the all-stocks baseline. None without a snapshot."""
    payload, _computed = _risk_snapshot()
    if not payload:
        return None
    rows = {r["tag"]: r for r in payload["scopes"]["all"]}
    mine, base = rows.get(code), rows.get(payload["baseline"])
    if not mine or not base:
        return None
    cells = lambda r: {k: _rule_cells(v) for k, v in r["r"].items()}  # noqa: E731
    return {"targets": payload["targets"], "window": payload["window"], "cost": f"{payload['cost'] * 100:.1f}%",
            "n": mine["n"], "mine": cells(mine), "hold": fmt.cell(mine["hold"]), "base": cells(base),
            "base_n": base["n"], "base_hold": fmt.cell(base["hold"])}


@app.get("/pattern/{tag}", response_class=HTMLResponse)
def pattern_detail(tag: str) -> HTMLResponse:
    code = tag.strip().upper()
    ctx: dict = {"active_page": "tracker", "code": code, "error": None, "not_found": False, "v": None,
                 "min_n": gate.MIN_SIGNALS, "min_span": gate.MIN_SPAN_DAYS, "cost_bp": round(gate.ROUND_TRIP_COST * 10000),
                 "forward_since": CONFIG.forward_since}
    if code not in glossary.TAGS:
        ctx["not_found"] = True
        return _render("pattern.html", ctx, status_code=404)
    try:
        raw = patternmod.load(code)
        forward = full = None
        if CONFIG.forward_since:
            forward = next((r for r in _cached_view_rows(CONFIG.forward_since)[0] if r["tag"] == code), None)
        full = next((r for r in _cached_view_rows(None)[0] if r["tag"] == code), None)
        ctx["v"] = patternmod.build_view(raw, forward, full, _rule_all_targets(code))
        rp, _c = _research_snapshot()
        ctx["research_row"] = None
        if rp:
            r = rp["results"].get("all", {}).get("full", {}).get(code)
            ctx["research_row"] = _research_row(code, r) if r else None
    except Exception as exc:  # noqa: BLE001 — DB down / schema not applied: show it, don't hide it
        ctx["error"] = f"{type(exc).__name__}: {exc}"
    return _render("pattern.html", ctx)


@app.get("/stock/{ticker}", response_class=HTMLResponse)
def stock_detail(ticker: str, capital: float | None = None, risk_pct: float | None = None) -> HTMLResponse:
    t = ticker.strip().upper()
    # Defaults come from .env, the same settings the Telegram digest uses (VG_ACCOUNT_BALANCE, VG_RISK_PCT, VG_FEE_PCT, ...).
    if capital is None and CONFIG.account_balance > 0:
        capital = CONFIG.account_balance / 1_000_000
    if risk_pct is None:
        risk_pct = CONFIG.risk_pct * 100
    ctx: dict = {"active_page": "screener", "ticker": t, "error": None, "not_found": False, "v": None,
                 "capital": capital, "risk_pct": risk_pct}
    if not stockmod.TICKER_RE.match(t):
        ctx["not_found"] = True
        return _render("stock.html", ctx, status_code=404)
    try:
        raw = stockmod.load(t)
        if raw is None:
            ctx["not_found"] = True
            return _render("stock.html", ctx, status_code=404)
        rp = min(max(risk_pct, 0.1), 5.0)
        ctx["risk_pct"] = rp
        balance = capital * 1_000_000 if capital and capital > 0 else None
        targets = tuple(sorted({*risk.R_MULTIPLES, CONFIG.target_r}))       # 1R-3R plus the digest's own target
        ctx["v"] = stockmod.build_view(raw, _track_by_tag(), balance=balance, risk_pct=rp / 100, fee=CONFIG.fee_pct,
                                       stop_mult=CONFIG.atr_sl_mult, r_multiples=targets)
        ctx["rule_test"] = _rule_test_for([t["code"] for t in ctx["v"]["tags_today"]])
    except Exception as exc:  # noqa: BLE001 — DB down / schema not applied: show it, don't hide it
        ctx["error"] = f"{type(exc).__name__}: {exc}"
    return _render("stock.html", ctx)


@app.get("/healthz")
def healthz():
    return {"ok": True}
