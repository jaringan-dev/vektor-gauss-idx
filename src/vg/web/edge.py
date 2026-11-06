"""Historical edge by horizon: how past signals of a pattern actually turned out, one, three, five and ten sessions later.

Two screens use it: the "Signal Historical Edge" card on a stock page (the patterns flagged on that stock today) and the
"Expectancy by horizon" panel on the Signal Evaluator (every pattern, plus the pure-accumulation vs wash comparison).

Per pattern and horizon H the numbers are, over every past signal whose H-session result is known:
  * win rate    = the share of signals whose return was above zero;
  * expectancy  = the average return of those signals (a plain average of ret2_hH).

Both are RAW: not market-adjusted and before trading costs, over the whole stored history, so they flatter a pattern the
way every in-sample number does. They sit next to the forward-test verdict (which is the number that decides anything)
and the pages say so. Since 2026-11-07 they are outcome v2 (`outcomes2.py`: entry at the VWAP of t+1, total return), the
same basis as the Track record; `ret2_h1/h3/h5/h10` are filled by `vg backfill-outcomes2`. No known result = N/A.

`load_cur` is the only part that touches the database (psycopg, read-only, one aggregate query); everything else is pure.
"""

from __future__ import annotations

from . import fmt

HORIZONS = (1, 3, 5, 10)
SMALL_N = 30                       # below this many signals a horizon's numbers are marked as thin
NA = "N/A"
_COLS = tuple(f"ret2_h{h}" for h in HORIZONS)         # names come from this constant, never from user input

PURE_TAG, WASH_TAG = "AKUM-ASING", "AKUM-NETRAL-WASH"


def load_cur(cur, tags: list[str] | None = None) -> dict[str, dict]:
    """{tag: {h: (n, win_rate, expectancy)}} for `tags` (None = every tag). READ-ONLY. Tags with no signal are absent."""
    parts = []
    for c in _COLS:
        parts.append(f"count({c}), avg(({c} > 0)::int)::float, avg({c})::float")
    where = "" if tags is None else "WHERE tag = ANY(%s)"
    sql = f"SELECT tag, {', '.join(parts)} FROM signal_log {where} GROUP BY tag ORDER BY tag"
    cur.execute(sql, (list(tags),) if tags is not None else None)
    out: dict[str, dict] = {}
    for row in cur.fetchall():
        tag, vals = row[0], row[1:]
        out[tag] = {h: (int(vals[3 * i] or 0), vals[3 * i + 1], vals[3 * i + 2]) for i, h in enumerate(HORIZONS)}
    return out


def load(tags: list[str] | None = None) -> dict[str, dict]:
    from ..db import connect

    with connect() as conn, conn.cursor() as cur:
        return load_cur(cur, tags)


def _cell(n: int, win, exp) -> dict:
    """One horizon: text for the win rate and the expectancy, sign classes, and whether the sample is thin."""
    if not n or win is None or exp is None:
        return {"n": 0, "win": NA, "win_c": "", "exp": NA, "exp_c": "", "thin": False, "win_raw": None, "exp_raw": None}
    return {"n": n, "win": fmt.pct_plain(win, 0), "win_c": "pos" if win > 0.5 else ("neg" if win < 0.5 else ""),
            "exp": fmt.pct(exp, 2), "exp_c": fmt.signcls(exp), "thin": n < SMALL_N, "win_raw": win, "exp_raw": exp}


def row_view(tag: str, data: dict) -> dict:
    """{tag, n (the largest horizon sample), cells: [one dict per horizon, in HORIZONS order]}."""
    cells = [_cell(*data.get(h, (0, None, None))) for h in HORIZONS]
    return {"tag": tag, "n": max((c["n"] for c in cells), default=0), "cells": cells}


def view(data: dict[str, dict], order: list[str] | None = None) -> list[dict]:
    """Rows for a table: `order` first (tags without data are skipped), then the rest by tag. Empty input gives an empty list."""
    tags = [t for t in (order or []) if t in data] + sorted(t for t in data if t not in (order or []))
    return [row_view(t, data[t]) for t in tags]


def compare(data: dict[str, dict], a: str = PURE_TAG, b: str = WASH_TAG) -> dict | None:
    """Side by side: pure accumulation `a` vs the wash flag `b`, horizon by horizon, with the difference in expectancy.
    None when either pattern has no signal at all (the page then says so instead of showing half a matrix)."""
    if a not in data or b not in data:
        return None
    rows = []
    for i, h in enumerate(HORIZONS):
        ca, cb = _cell(*data[a].get(h, (0, None, None))), _cell(*data[b].get(h, (0, None, None)))
        diff = None
        if ca["exp_raw"] is not None and cb["exp_raw"] is not None:
            diff = ca["exp_raw"] - cb["exp_raw"]
        rows.append({"h": h, "a": ca, "b": cb, "diff": fmt.pct(diff, 2) if diff is not None else NA, "diff_c": fmt.signcls(diff),
                     "thin": ca["thin"] or cb["thin"] or not ca["n"] or not cb["n"]})
    return {"a": a, "b": b, "rows": rows}
