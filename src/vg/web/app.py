"""Local superadmin dashboard — Layar 3: forward-test / gate tracker.

RENCANA-UI-UX §3.4. A thin, read-only FastAPI layer over `engine.eval_tags()`:
no new methodology, no writes. Single-operator; `vg serve` binds to 127.0.0.1
(§3.6). The forward-test table rendered here is the same component reused later
as the public "Rapor Publik" (§4.2), once the gate is cleared.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
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
def forward_tracker(request: Request):
    ctx: dict = {
        "request": request,
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
    return _TEMPLATES.TemplateResponse("forward_test.html", ctx)


@app.get("/healthz")
def healthz():
    return {"ok": True}
