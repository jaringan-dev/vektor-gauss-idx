"""Server-side filtering, sorting and paging for the stock list (UX review 4 Nov 2026).

The list used to ship every stock with a pattern (603 rows, 845 KB) and could only be filtered by typing. Now the page is small,
and filters, sorting and page numbers are plain links with the state in the address, so a view can be bookmarked and the back button
works. Pure functions; the app just feeds them rows.
"""

from __future__ import annotations

from urllib.parse import urlencode

PER_PAGE = 50
LIQUID_VALUE_IDR = 5_000_000_000
QUADS = (("AKUMULASI", "Accumulation"), ("MARKUP", "Markup"), ("DISTRIBUSI", "Distribution"), ("MARKDOWN", "Markdown"))
# sort key -> (raw field, natural first direction)
SORTS = {"ticker": ("ticker", "asc"), "price": ("price_raw", "desc"), "change": ("chg_raw", "desc"), "foreign_today": ("nff_d0_raw", "desc"),
         "foreign_1w": ("nff_w1_raw", "desc"), "brokers": ("bkr_raw", "desc"), "top3": ("cr3_raw", "desc"), "days": ("cons_raw", "desc"),
         "patterns": ("n_tags", "desc")}
DEFAULTS = {"q": "", "quad": "", "liquid": 0, "flow": "", "wl": 0, "sort": "foreign_1w", "dir": "desc", "page": 1}


def clean(params: dict) -> dict:
    """Coerce untrusted query values into safe, known ones."""
    p = dict(DEFAULTS)
    p["q"] = str(params.get("q", ""))[:40].strip()
    p["quad"] = params.get("quad") if params.get("quad") in {k for k, _ in QUADS} else ""
    p["liquid"] = 1 if str(params.get("liquid", "0")) in ("1", "true", "True") else 0
    p["flow"] = params.get("flow") if params.get("flow") in ("in", "out") else ""
    p["wl"] = 1 if str(params.get("wl", "0")) in ("1", "true", "True") else 0
    p["sort"] = params.get("sort") if params.get("sort") in SORTS else DEFAULTS["sort"]
    p["dir"] = params.get("dir") if params.get("dir") in ("asc", "desc") else SORTS[p["sort"]][1]
    try:
        p["page"] = max(1, int(params.get("page", 1)))
    except (TypeError, ValueError):
        p["page"] = 1
    return p


def apply(rows: list[dict], p: dict) -> list[dict]:
    out = rows
    if p["q"]:
        t = p["q"].upper()
        out = [r for r in out if t in r.get("search", r.get("ticker", "") + " " + r.get("tags", "")).upper()]
    if p["quad"]:
        out = [r for r in out if r.get("quad") == p["quad"]]
    if p["liquid"]:
        out = [r for r in out if (r.get("value_raw") or 0) >= LIQUID_VALUE_IDR]
    if p["flow"] == "in":
        out = [r for r in out if (r.get("nff_w1_raw") or 0) > 0]
    elif p["flow"] == "out":
        out = [r for r in out if (r.get("nff_w1_raw") or 0) < 0]
    if p["wl"]:
        out = [r for r in out if r.get("in_wl") or r.get("held")]
    field = SORTS[p["sort"]][0]
    known = [r for r in out if r.get(field) is not None]
    unknown = [r for r in out if r.get(field) is None]                       # missing values always sort last
    known.sort(key=lambda r: (r[field], r.get("ticker", "")), reverse=(p["dir"] == "desc"))
    if p["sort"] == "ticker":
        known.sort(key=lambda r: r.get("ticker", ""), reverse=(p["dir"] == "desc"))
    return known + unknown


def paginate(total: int, page: int, per: int = PER_PAGE) -> dict:
    pages = max(1, -(-total // per))
    page = min(page, pages)
    start = (page - 1) * per
    return {"page": page, "pages": pages, "start": start, "end": min(total, start + per), "total": total,
            "has_prev": page > 1, "has_next": page < pages}


def query(p: dict, **over) -> str:
    """Query string for `p` with `over` applied; defaults are left out so addresses stay short. Changing a filter returns to page 1."""
    q = dict(p)
    if any(k != "page" for k in over):
        q["page"] = 1
    q.update(over)
    items = {k: v for k, v in q.items() if v not in ("", 0) and not (k in DEFAULTS and v == DEFAULTS[k])}
    if q.get("sort") != DEFAULTS["sort"] or q.get("dir") != SORTS[q["sort"]][1]:
        items["sort"], items["dir"] = q["sort"], q["dir"]
    return "?" + urlencode(items) if items else "?"


def sort_link(p: dict, key: str) -> dict:
    """Link for a column header: first click sorts in the column's natural direction, a second click reverses it."""
    natural = SORTS[key][1]
    if p["sort"] == key:
        new_dir = "asc" if p["dir"] == "desc" else "desc"
        arrow = "▼" if p["dir"] == "desc" else "▲"
    else:
        new_dir, arrow = natural, ""
    return {"href": query(p, sort=key, dir=new_dir), "arrow": arrow, "active": p["sort"] == key}


def chips(p: dict) -> list[dict]:
    """Filter chips: each toggles one filter; quadrants and flow direction are single-choice groups."""
    out = []
    for code, label in QUADS:
        on = p["quad"] == code
        out.append({"group": "quad", "label": label, "on": on, "href": query(p, quad="" if on else code)})
    out.append({"group": "flow", "label": "Foreign inflow", "on": p["flow"] == "in", "href": query(p, flow="" if p["flow"] == "in" else "in")})
    out.append({"group": "flow", "label": "Foreign outflow", "on": p["flow"] == "out", "href": query(p, flow="" if p["flow"] == "out" else "out")})
    out.append({"group": "more", "label": "Liquid stocks only", "on": bool(p["liquid"]), "href": query(p, liquid=0 if p["liquid"] else 1)})
    out.append({"group": "more", "label": "My stocks", "on": bool(p["wl"]), "href": query(p, wl=0 if p["wl"] else 1)})
    return out
