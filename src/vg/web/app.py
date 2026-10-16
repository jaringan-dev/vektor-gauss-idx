"""Local superadmin dashboard — Layar 3: forward-test / gate tracker.

RENCANA-UI-UX §3.4. A thin, read-only FastAPI layer over `engine.eval_tags()`:
no new methodology, no writes. Single-operator; `vg serve` binds to 127.0.0.1
(§3.6). The forward-test table rendered here is the same component reused later
as the public "Rapor Publik" (§4.2), once the gate is cleared.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import engine
from ..config import CONFIG
from . import status as gate

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="Vektor Gauss — Superadmin")


def _pct(x: float | None, dp: int = 2) -> str:
    """Indonesian-format signed percent (comma decimal), matching the digest (§2.5)."""
    return f"{x * 100:+.{dp}f}%".replace(".", ",") if x is not None else "n/a"


def _cell(x: float | None, dp: int = 2) -> dict:
    """A numeric cell: formatted text + a sign class for green/red (never color-only — §4.5)."""
    return {"t": _pct(x, dp), "c": "" if x is None else ("pos" if x >= 0 else "neg")}


def _hit(x: float | None) -> str:
    return f"{x * 100:.0f}%" if x is not None else "-"


# Visual vocabulary shared with the Telegram digest (RENCANA-UI-UX §2.5).
_QUAD_EMOJI = {"MARKUP": "📈", "AKUMULASI": "🟢", "DISTRIBUSI": "🔻", "MARKDOWN": "📉", "NETRAL": "▪️"}
_TREND = {"up": "↑", "down": "↓", "flat": "→", "nodata": "?"}


def _price(v) -> str:
    return f"{float(v):,.0f}".replace(",", ".") if v is not None else "?"


def _idr(x: float | None) -> str:
    """Rupiah magnitude with sign, Indonesian decimal comma (matches digest._idr)."""
    if x is None:
        return "n/a"
    a = abs(x)
    if a >= 1e12:
        return f"{x / 1e12:+.1f}T".replace(".", ",")
    if a >= 1e9:
        return f"{x / 1e9:+.1f}B".replace(".", ",")
    if a >= 1e6:
        return f"{x / 1e6:+.0f}Jt".replace(".", ",")
    return f"{x:+.0f}"


def _signcls(x: float | None) -> str:
    return "" if x is None else ("pos" if x >= 0 else "neg")


def _view_rows(since) -> list[dict]:
    """Shape `engine.eval_tags(since)` into display rows, each with an honest verdict."""
    rows = []
    for r in engine.eval_tags(since):
        st = gate.classify(r)
        rows.append(
            {
                "tag": r["tag"],
                "n": r["n"],
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
    return rows


@app.get("/", response_class=HTMLResponse)
def forward_tracker() -> HTMLResponse:
    ctx: dict = {
        "active_page": "tracker",
        "forward_since": CONFIG.forward_since,
        "cost_bp": round(gate.ROUND_TRIP_COST * 10000),
        "min_n": gate.MIN_SIGNALS,
        "min_span": gate.MIN_SPAN_DAYS,
        "error": None,
        "insample": [],
        "forward": [],
    }
    try:
        ctx["insample"] = _view_rows(None)
        ctx["forward"] = _view_rows(CONFIG.forward_since) if CONFIG.forward_since else []
    except Exception as exc:  # noqa: BLE001 — DB down / schema not applied: show it, don't hide it
        ctx["error"] = f"{type(exc).__name__}: {exc}"
    # Render manually + wrap in HTMLResponse: avoids depending on the TemplateResponse
    # positional signature, which changed across Starlette versions (request-first).
    return HTMLResponse(_TEMPLATES.get_template("forward_test.html").render(ctx))


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
                    "quad_emoji": _QUAD_EMOJI.get(r.get("quadrant") or "", "▪️"),
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
    return HTMLResponse(_TEMPLATES.get_template("screener.html").render(ctx))


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
                "quad_emoji": _QUAD_EMOJI.get(r.get("quadrant") or "", "▪️"),
                "val": _idr(r.get("val")), "val_c": _signcls(r.get("val")),
                "price": _price(close), "chg": _cell(chg, 1),
                "tags": r.get("tags") or "",
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
        ("Top Akum Asing", fcol, True, "arus asing bersih — akumulasi tertinggi (se-pasar)"),
        ("Top Distribusi Asing", fcol, False, "arus asing bersih — distribusi terbesar (se-pasar)"),
        ("Top Akum Bandar", bcol, True, "net broker asing — hanya universe broker vektorgauss"),
        ("Top Retail Jualan", "bkr_dom_retail_net", False,
         "net retail broker, jual terbesar — universe broker, selalu 1D"),
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
    return HTMLResponse(_TEMPLATES.get_template("ranking.html").render(ctx))


@app.get("/healthz")
def healthz():
    return {"ok": True}
