"""Coverage heatmap data (checklist F1, RANCANGAN-UI-UX-v2 §5.4).

Day x source grid: how many rows each source has per trading day, judged against what is NORMAL for
that source — so a day with 5 price rows (the 14/15/25 Sep incident) reads "gap" even though
"some data exists". Clicking a non-normal cell lists the tickers that are missing relative to the
nearest healthy day, with the repair commands to run.

The grid/missing logic is pure and DB-free (unit-tested with the real incident's numbers);
`load_counts` / `load_tickers` are the only DB-touching parts and import the driver lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median

from .freshness import GAP_RATIO, PARTIAL_RATIO, fmt_date

OK, WARN, BAD, NA = "ok", "warn", "bad", "na"
REFERENCE_MAX_RATIO = 1.5      # a day above 1.5x the normal count is a one-off, not a reference
_SYMBOL = {OK: "✓", WARN: "⚠", BAD: "✖", NA: "—"}
_WORD = {OK: "normal", WARN: "partial", BAD: "gap", NA: "no data yet"}

# key, label, unit, table, entity column
SOURCES = (
    ("price", "Prices", "stocks", "price_bars", "ticker"),
    ("index", "Indices", "indices", "index_bars", "index_code"),
    ("broker", "Broker", "stocks", "broker_flows", "ticker"),
    ("signal", "Signals", "stocks", "signal_components", "ticker"),
)
SOURCE_KEYS = tuple(s[0] for s in SOURCES)
_META = {s[0]: s for s in SOURCES}


def source_label(key: str) -> str:
    return _META[key][1]


def classify(count: int, baseline: float | None) -> str:
    """ok / warn / bad for one day's count against the source's normal level."""
    if baseline is None or baseline <= 0:
        return OK if count > 0 else BAD
    ratio = count / baseline
    if ratio < GAP_RATIO:
        return BAD
    if ratio < PARTIAL_RATIO:
        return WARN
    return OK


@dataclass(frozen=True)
class Cell:
    level: str
    count: int | None
    ratio: float | None

    def as_dict(self) -> dict:
        return {"level": self.level, "count": self.count, "symbol": _SYMBOL[self.level],
                "word": _WORD[self.level],
                "pct": None if self.ratio is None else round(self.ratio * 100)}


def build_grid(days: list[date], counts: dict) -> dict:
    """counts[source][date] -> n. Returns {'rows': newest-first [{date, cells}], 'summary': {...}}.

    Per source: `start` = first day with any data (earlier days are 'na', not red — e.g. broker
    history begins 3 Jun 2026); `baseline` = median count from `start` on (robust to a few gaps).
    """
    days = sorted(days)
    meta: dict = {}
    for key in SOURCE_KEYS:
        c = counts.get(key, {})
        start = next((d for d in days if c.get(d, 0) > 0), None)
        series = [c.get(d, 0) for d in days if start is not None and d >= start]
        baseline = float(median(series)) if series else None
        meta[key] = {"start": start, "baseline": baseline, "bad": 0, "warn": 0}

    rows = []
    for d in reversed(days):
        cells = {}
        for key in SOURCE_KEYS:
            m, n = meta[key], counts.get(key, {}).get(d, 0)
            if m["start"] is None or d < m["start"]:
                cells[key] = Cell(NA, None, None)
                continue
            level = classify(n, m["baseline"])
            ratio = (n / m["baseline"]) if m["baseline"] else None
            cells[key] = Cell(level, n, ratio)
            if level in (WARN, BAD):
                m[level] += 1
        rows.append({"date": d, "cells": cells})
    return {"rows": rows, "summary": meta}


def grid_view(grid: dict) -> tuple[list[dict], list[dict]]:
    """Template-ready (rows, summary) from `build_grid` output: plain dicts and formatted dates."""
    rows = [
        {"date": r["date"], "label": fmt_date(r["date"]), "iso": r["date"].isoformat(),
         "cells": {k: c.as_dict() for k, c in r["cells"].items()}}
        for r in grid["rows"]
    ]
    summary = []
    for key, label, unit, _table, _col in SOURCES:
        m = grid["summary"][key]
        summary.append({
            "key": key, "label": label, "unit": unit,
            "baseline": round(m["baseline"]) if m["baseline"] else None,
            "start": fmt_date(m["start"]) if m["start"] else None,
            "bad": m["bad"], "warn": m["warn"],
        })
    return rows, summary


def nearest_healthy_day(target: date, days: list[date], counts: dict, baseline: float | None) -> date | None:
    """The closest day (earlier wins a tie) on which this source was NORMAL — the reference that
    defines which tickers 'should' be there. Normal means healthy AND not anomalously large: a
    one-off over-covered day (broker for the whole market on 15 Oct: 952 vs ~190) is healthy but
    would make an ordinary day look like it is missing hundreds of names."""
    best, best_key = None, None
    for d in days:
        n = counts.get(d, 0)
        if d == target or n == 0 or classify(n, baseline) != OK:
            continue
        if baseline and n > baseline * REFERENCE_MAX_RATIO:
            continue
        key = (abs((d - target).days), 0 if d < target else 1)
        if best_key is None or key < best_key:
            best, best_key = d, key
    return best


def missing_names(expected: set, present: set) -> list[str]:
    return sorted(expected - present)


def repair_commands(source: str, d: date) -> list[str]:
    """Shell commands (run in WSL, inside the repo venv) that fix a gap on day `d`. Every command
    here is parsed against the real CLI by a test, so the page never recommends one that does not
    exist. A trailing '  # …' is an explanation, stripped before parsing."""
    iso = d.isoformat()
    if source == "price":
        return [
            f"vg collect-price --date {iso}",
            "vg collect-price-history --all   # fallback via the VG feed when the IDX feed is blocked (403)",
            f"vg compute --date {iso}",
        ]
    if source == "index":
        return [f"vg collect-index --date {iso}"]
    if source == "broker":
        return [f"vg collect-broker-summary --full --date {iso}", f"vg compute --date {iso}"]
    if source == "signal":
        return [f"vg compute --date {iso}"]
    raise KeyError(source)


# ---------------------------------------------------------------------------------------
# DB-touching part
# ---------------------------------------------------------------------------------------

def load_counts(n_days: int = 60) -> tuple[list[date], dict]:
    """(trading days, {source: {date: rows}}) for the newest `n_days` trading days."""
    from ..db import connect  # lazy: keeps this module importable without the Postgres driver

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT session_date FROM trading_calendar ORDER BY session_date DESC LIMIT %s", (n_days,))
        days = sorted(r[0] for r in cur.fetchall())
        counts: dict = {k: {} for k in SOURCE_KEYS}
        if not days:
            return days, counts
        lo = days[0]
        for key, _label, _unit, table, col in SOURCES:
            agg = "count(*)" if key in ("price", "signal") else f"count(DISTINCT {col})"
            cur.execute(f"SELECT session_date, {agg} FROM {table} WHERE session_date >= %s GROUP BY 1", (lo,))
            counts[key] = {d: int(n) for d, n in cur.fetchall()}
    return days, counts


def load_tickers(source: str, d: date) -> set[str]:
    """Distinct entities (tickers / index codes) a source has on day `d`."""
    from ..db import connect

    _key, _label, _unit, table, col = _META[source]      # whitelisted: KeyError for anything else
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT {col} FROM {table} WHERE session_date = %s", (d,))
        return {r[0] for r in cur.fetchall()}
