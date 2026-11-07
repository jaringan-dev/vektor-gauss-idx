"""Home screen: the interactive score screener (`/`).

One row per stock that has a pattern OR a Bandar Power Index on the latest signalled session, with the broker-scoring
numbers next to the price (BPI, how many houses sit on both sides, Aggressor Ratio) and the wash flag as a badge. Three
header cards summarise the session: how many stocks carry an accumulation pattern, how many carry the wash flag, and
the average BPI of the user's watchlist.

`load()` is the only part that touches the database (psycopg, read-only, two or three small queries); `clean`, `apply`,
`build_view`, `chips`, `sort_link` and `query` are pure, so everything is unit-tested without Postgres. Filters, sorting and
page numbers are plain links with the state in the address (same approach as `listing.py` for `/screener`): bookmarkable,
back button works, no JavaScript needed.

No new methodology: every number already exists in `signal_components` / `signal_log` / `price_bars`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from . import bpi_window, fmt, glossary, listing, live_scores, patterns

WASH_TAG = "AKUM-NETRAL-WASH"
# "Accumulation" = any pattern named *-AKUM / AKUM-*. The wash flag is excluded on purpose: its own description says
# it is NOT read as one hand accumulating, so it gets its own card instead of inflating this one. Derived from the
# glossary, so a newly documented AKUM pattern is counted without touching this file.
ACCUMULATION_TAGS = frozenset(
    t for t in glossary.TAGS if (t.startswith("AKUM-") or t.endswith("-AKUM")) and t != WASH_TAG
)

# The broker-scoring columns this screen reads. They are created by `vg init-db`; until it has run, each one is
# selected as NULL so the page still renders (everything shows N/A) instead of failing.
SCORE_COLS = ("bpi3", "bpi_overlap3", "aggr_haka_share", "aggr_ratio", "aggr_coverage")
NA = "N/A"
BAR_FULL = 0.5            # a BPI of +/-50 points fills the little bar; larger values are clipped

PER_PAGE = listing.PER_PAGE
# sort key -> (row field, natural first direction)
SORTS = {"ticker": ("ticker", "asc"), "price": ("price_raw", "desc"), "change": ("chg_raw", "desc"), "bpi": ("bpi_raw", "desc"),
         "bpi5d": ("bpi5d_raw", "desc"), "overlap": ("overlap_raw", "desc"), "aggr": ("aggr_raw", "desc"), "signals": ("n_tags", "desc")}
# The quick pills, in the order they are drawn. `acc` stays a valid address (the Total-accumulation card links to it) but has no pill.
VIEWS = (("acc", "Accumulation"), ("top", "Top Accumulation"), ("haka", "Haka Dominant"), ("wash", "Wash Alert"))
PILL_VIEWS = ("top", "haka", "wash")
HAKA_DOMINANT = 0.55      # buyers lifted the offer on at least 55% of the volume whose aggressor is known
AGGR_MIN_SHARE = 0.10     # show the Aggressor-ratio column only when at least 10% of the session's rows have order-flow data
DEFAULTS = {"q": "", "view": "", "liquid": 0, "wl": 0, "sort": "bpi", "dir": "desc", "page": 1}


# ---- database --------------------------------------------------------------------------------------------------

def load(session_date: date) -> dict:
    """Raw rows for `session_date`. READ-ONLY.

    `columns_present` is False when `vg init-db` has not created the scoring columns yet; `last_scored` is the newest
    session that has any BPI, looked up only when this session has none (so the page can say where the scores stop)."""
    from ..db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'signal_components' AND column_name = ANY(%s)",
            (list(SCORE_COLS),))
        have = {r[0] for r in cur.fetchall()}
        select = ", ".join(f"sc.{c}" if c in have else f"NULL AS {c}" for c in SCORE_COLS)      # names come from the constant above
        scored = "OR sc.bpi3 IS NOT NULL" if "bpi3" in have else ""
        cur.execute(
            f"""
            SELECT pb.ticker, pb.close, pb.prev, pb.value, sc.quadrant, {select}, tg.tags
            FROM price_bars pb
            LEFT JOIN signal_components sc ON sc.session_date = pb.session_date AND sc.ticker = pb.ticker
            LEFT JOIN (SELECT ticker, string_agg(DISTINCT tag, ', ' ORDER BY tag) AS tags
                       FROM signal_log WHERE session_date = %(d)s GROUP BY ticker) tg ON tg.ticker = pb.ticker
            WHERE pb.session_date = %(d)s AND (tg.tags IS NOT NULL {scored})
            ORDER BY pb.ticker
            """,
            {"d": session_date})
        cols = [d[0] for d in cur.description]
        rows = [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in zip(cols, r, strict=True)} for r in cur.fetchall()]
        last_scored = None
        stored_n = sum(r["bpi3"] is not None for r in rows)
        live_n = 0
        if "bpi3" in have and stored_n < len(rows):
            if not stored_n:
                cur.execute("SELECT max(session_date) FROM signal_components WHERE bpi3 IS NOT NULL")
                last_scored = cur.fetchone()[0]
            # Sessions the engine has not scored (the forward-test window) get the same numbers computed on the fly from the stored
            # broker ladders. Read-only; marked computed on the page. See live_scores.py for why they are not stored.
            live_n = live_scores.fill_rows(rows, live_scores.load_ladders(cur, session_date))
        # The five-session view of the same index (bpi_window.py): summed ladders, up days, the same top buyer, coverage. Read-only.
        windows = bpi_window.load(cur, session_date, [r["ticker"] for r in rows])
        pats = patterns.load(cur, session_date, [r["ticker"] for r in rows])      # badges: compression, persistent net buyer
        for r in rows:
            r["win"] = windows.get(r["ticker"])
            r["pat"] = pats.get(r["ticker"])
    return {"session_date": session_date, "rows": rows, "columns_present": "bpi3" in have, "last_scored": last_scored,
            "stored_n": stored_n, "live_n": live_n}


# ---- view (pure) -----------------------------------------------------------------------------------------------

def _pts(x: float | None) -> dict:
    """A concentration index in [-1, 1] as percentage points of the day's traded value, with a sign (never colour alone)."""
    return {"t": f"{x * 100:+.1f} pts", "c": fmt.signcls(x)} if x is not None else {"t": NA, "c": ""}


def split_bar(haka: float | None) -> dict | None:
    """HAKA vs HAKI as two shares of the volume whose aggressor is known (they add up to 100%). None when there is no order-flow data."""
    if haka is None:
        return None
    h = max(0.0, min(1.0, float(haka)))
    return {"haka": round(h * 100), "haki": 100 - round(h * 100), "dominant": h >= HAKA_DOMINANT}


def win_cell(w) -> dict:
    """The five-session BPI cell: the number, then the evidence under it (up days, the same top buyer), or why there is no number."""
    if w is None:
        return {"bpi": _pts(None), "raw": None, "sub": f"no broker data in the last {bpi_window.WINDOW} sessions", "short": True,
                "confirmed": None, "title": ""}
    if w.bpi3 is None:
        return {"bpi": _pts(None), "raw": None, "sub": f"data on {w.days_ok} of {w.n_window} sessions", "short": True,
                "confirmed": None, "title": f"A {w.n_window}-session number needs broker data on at least {bpi_window.MIN_DAYS} of them."}
    sub = f"up {w.days_up}/{w.days_ok} · {w.top_buyer} top-3 {w.top_buyer_days}/{w.days_ok}"
    if w.days_ok < w.n_window:
        sub += f" · {w.days_ok} of {w.n_window} sessions"
    if w.n_crossing:
        sub += f" · {w.n_crossing} crossing day{'s' if w.n_crossing > 1 else ''}"
    title = (f"Summed over {w.days_ok} sessions. BPI was positive on {w.days_up} of them; the biggest buyer over the window, "
             f"{w.top_buyer}, was among the day's top 3 buyers on {w.top_buyer_days}. Same houses on both sides: {w.overlap3} of 3.")
    if w.n_crossing:
        title += (f" {w.n_crossing} of these sessions were mostly negotiated-market (block/crossing) trades; being large, they weigh "
                  "heavily in the sum.")
    return {"bpi": _pts(w.bpi3), "raw": w.bpi3, "sub": sub, "short": False, "confirmed": bpi_window.is_confirmed(w), "title": title}


def _row(r: dict, wl: set, held: set) -> dict:
    tags = glossary.split_tags(r.get("tags"))
    close, prev = r.get("close"), r.get("prev")
    chg = (close / prev - 1) if (close and prev) else None
    bpi, overlap, ratio, cov = r.get("bpi3"), r.get("bpi_overlap3"), r.get("aggr_ratio"), r.get("aggr_coverage")
    haka = r.get("aggr_haka_share")
    win = win_cell(r.get("win"))
    return {
        "win": win, "bpi5d_raw": win["raw"], "badges": patterns.badges(r.get("pat")),
        "ticker": r["ticker"], "in_wl": r["ticker"] in wl, "held": r["ticker"] in held,
        "price": fmt.price(close), "chg": fmt.cell(chg, 1),
        "bpi_live": bool(r.get("bpi_live")),
        "bpi": _pts(bpi), "bpi_bar": 0 if bpi is None else round(min(abs(bpi), BAR_FULL) / BAR_FULL * 100),
        "bpi_heat": 0 if bpi is None else round(min(abs(bpi), BAR_FULL) / BAR_FULL, 2),      # 0..1: how strongly the cell is tinted
        "overlap": f"{overlap} of 3" if overlap is not None else NA,
        "aggr": f"{ratio:.2f}" if ratio is not None else NA,
        "haka": split_bar(haka),
        "aggr_note": f"{cov * 100:.0f}% of the volume was classified" if (ratio is not None and cov is not None) else "",
        "wash": WASH_TAG in tags,
        "acc": any(t in ACCUMULATION_TAGS for t in tags),
        "acc_tags": [t for t in tags if t in ACCUMULATION_TAGS],
        "tag_list": [t for t in tags if t != WASH_TAG and t not in ACCUMULATION_TAGS],
        # raw values for server-side filtering and sorting
        "price_raw": close, "chg_raw": chg, "bpi_raw": bpi, "overlap_raw": overlap, "aggr_raw": ratio, "haka_raw": haka,
        "value_raw": r.get("value"), "n_tags": len(tags),
        "search": " ".join([r["ticker"], r.get("tags") or "", *[glossary.plain_tag(t) for t in tags],
                            glossary.quad_word(r.get("quadrant"))]).upper(),
    }


def eod_card(raw: dict, scored_today: bool) -> dict:
    """Where the end-of-day run stands: the newest session that has prices and signals, and the newest one that has broker scores.
    Three honest states: scored (this session has BPI), behind (an older session has scores), none (no session has any)."""
    sd = raw["session_date"]
    if not raw.get("columns_present", True):
        return {"state": "none", "t": NA, "word": "Not set up", "c": "", "sub": f"EOD session {sd}; the scoring columns are not in the database yet."}
    if scored_today and raw.get("live_n", 0) and not raw.get("stored_n", 0):
        last = raw.get("last_scored")
        return {"state": "computed", "t": sd.isoformat(), "word": "Computed", "c": "warnfg",
                "sub": f"EOD session {sd}: scores computed on the fly, not stored yet" + (f" (last stored: {last})." if last else ".")}
    if scored_today:
        return {"state": "scored", "t": sd.isoformat(), "word": "Scored", "c": "pos", "sub": f"EOD session {sd}: broker scores are in."}
    last = raw.get("last_scored")
    if last:
        gap = (sd - last).days
        return {"state": "behind", "t": last.isoformat(), "word": "Behind", "c": "warnfg",
                "sub": f"Newest EOD session is {sd}, {gap} day{'s' if gap != 1 else ''} after the last scored one."}
    return {"state": "none", "t": NA, "word": "No scores yet", "c": "", "sub": f"EOD session {sd}; no session has a broker score yet."}


def build_view(raw: dict, watchlist=(), holdings=()) -> dict:
    """Rows + the three header cards + an honest notice when the scores are missing for this session."""
    wl, held = set(watchlist), set(holdings)
    rows = [_row(r, wl, held) for r in raw["rows"]]
    scored = [r for r in rows if r["bpi_raw"] is not None]
    mine = [r["bpi_raw"] for r in scored if r["in_wl"]]
    avg = sum(mine) / len(mine) if mine else None
    if not wl:
        avg_sub = "Your watchlist is empty."
    elif not mine:
        avg_sub = f"None of your {len(wl)} watchlist stocks has a BPI on this session."
    else:
        avg_sub = f"{len(mine)} of {len(wl)} watchlist stocks have a BPI."
    notice = None
    live_n, stored_n = raw.get("live_n", 0), raw.get("stored_n", len(scored) - raw.get("live_n", 0))
    if not raw.get("columns_present", True):
        notice = "The scoring columns are not in the database yet (they are created by vg init-db), so BPI and Aggressor Ratio show N/A."
    elif live_n and not stored_n:
        notice = (f"The engine has not stored scores for {raw['session_date']} yet, so BPI and the shared-houses count are computed on the fly from today's "
                  "broker table (marked computed). They are the same numbers the engine would store; nothing is written. The wash flag only appears once the engine has run.")
    elif not scored:
        last = raw.get("last_scored")
        notice = (f"BPI has not been computed for {raw['session_date']}; the newest scored session is {last}. "
                  "Run vg compute to fill it." if last else
                  f"BPI has not been computed for {raw['session_date']} (no session has a score yet). Run vg compute to fill it.")
    n_aggr = sum(r["haka_raw"] is not None for r in rows)
    return {
        "session_date": raw["session_date"], "rows": rows, "n_rows": len(rows), "notice": notice,
        # A column that is N/A on (nearly) every row is noise: hide it and say why in the note under the table instead.
        "n_aggr": n_aggr, "show_aggr": bool(rows) and n_aggr / len(rows) >= AGGR_MIN_SHARE,
        "cards": {
            "acc": {"n": sum(r["acc"] for r in rows), "of": len(rows)},
            "wash": {"n": sum(r["wash"] for r in rows)},
            "avg": {**_pts(avg), "sub": avg_sub, "wl_empty": not wl},
            "eod": eod_card(raw, bool(scored)),
        },
    }


# ---- filter, sort, page ----------------------------------------------------------------------------------------

def clean(params: dict) -> dict:
    """Coerce untrusted query values into safe, known ones."""
    p = dict(DEFAULTS)
    p["q"] = str(params.get("q", ""))[:40].strip()
    p["view"] = params.get("view") if params.get("view") in {k for k, _ in VIEWS} else ""
    p["liquid"] = 1 if str(params.get("liquid", "0")) in ("1", "true", "True") else 0
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
        out = [r for r in out if p["q"].upper() in r["search"]]
    if p["view"] == "acc":
        out = [r for r in out if r["acc"]]
    elif p["view"] == "top":                                      # accumulation pattern, and not a negative BPI (an unscored stock stays, last)
        out = [r for r in out if r["acc"] and (r["bpi_raw"] is None or r["bpi_raw"] > 0)]
    elif p["view"] == "haka":
        out = [r for r in out if r["haka_raw"] is not None and r["haka_raw"] >= HAKA_DOMINANT]
    elif p["view"] == "wash":
        out = [r for r in out if r["wash"]]
    if p["liquid"]:
        out = [r for r in out if (r.get("value_raw") or 0) >= listing.LIQUID_VALUE_IDR]
    if p["wl"]:
        out = [r for r in out if r["in_wl"] or r["held"]]
    field = SORTS[p["sort"]][0]
    known = [r for r in out if r.get(field) is not None]
    unknown = [r for r in out if r.get(field) is None]                      # N/A always sorts last, in either direction
    known.sort(key=lambda r: (r[field], r["ticker"]), reverse=(p["dir"] == "desc"))
    if p["sort"] == "ticker":
        known.sort(key=lambda r: r["ticker"], reverse=(p["dir"] == "desc"))
    return known + unknown


def query(p: dict, **over) -> str:
    """Query string for `p` with `over` applied; defaults are left out. Changing a filter returns to page 1."""
    q = dict(p)
    if any(k != "page" for k in over):
        q["page"] = 1
    q.update(over)
    items = {k: v for k, v in q.items() if v not in ("", 0) and not (k in DEFAULTS and v == DEFAULTS[k])}
    if q.get("sort") != DEFAULTS["sort"] or q.get("dir") != SORTS[q["sort"]][1]:
        items["sort"], items["dir"] = q["sort"], q["dir"]
    return "?" + urlencode(items) if items else "?"


def sort_link(p: dict, key: str) -> dict:
    """Header link: first click sorts in the column's natural direction, a second click reverses it."""
    if p["sort"] == key:
        return {"href": query(p, sort=key, dir="asc" if p["dir"] == "desc" else "desc"),
                "arrow": "▼" if p["dir"] == "desc" else "▲", "active": True}
    return {"href": query(p, sort=key, dir=SORTS[key][1]), "arrow": "", "active": False}


PILL_TITLES = {
    "All": "Every stock with a pattern or a score; clears the filters.",
    "Liquid Only": "Stocks that traded more than Rp5 billion on the day (about 200).",
    "Top Accumulation": "Stocks with an accumulation pattern and no negative BPI; stocks without a score come last.",
    "Haka Dominant": "Buyers lifted the offer on at least 55% of the volume whose aggressor is known (needs order-flow data).",
    "Wash Alert": "Concentrated buying, but the same houses also sell (AKUM-NETRAL-WASH).",
    "My Watchlist": "Your watchlist and your holdings.",
}


def chips(p: dict) -> list[dict]:
    """The quick pills: All | Liquid Only | Top Accumulation | Haka Dominant | Wash Alert | My Watchlist. The three views are one
    single-choice group; Liquid Only and My Watchlist toggle on their own; All is lit when nothing is filtered."""
    labels = dict(VIEWS)
    out = [{"label": "All", "on": not any((p["view"], p["liquid"], p["wl"])), "href": query(p, view="", liquid=0, wl=0)},
           {"label": "Liquid Only", "on": bool(p["liquid"]), "href": query(p, liquid=0 if p["liquid"] else 1)}]
    for code in PILL_VIEWS:
        out.append({"label": labels[code], "on": p["view"] == code, "href": query(p, view="" if p["view"] == code else code)})
    out.append({"label": "My Watchlist", "on": bool(p["wl"]), "href": query(p, wl=0 if p["wl"] else 1)})
    for c in out:
        c["title"] = PILL_TITLES[c["label"]]
    return out
