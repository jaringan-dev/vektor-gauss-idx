"""The `run_log` table as a viewer: every collector / compute / quality-gate run with its outcome, so a failing step or a schema-validation
error is visible without opening a terminal.

`load` is the only part that touches the database (psycopg, read-only, one small query); `view` is pure. A status other than `ok` or
`no_data` is a problem (`quality_gate` rows that fail validation land here too, with the reason in `error`). Long error text is cut for the
table and kept whole in the cell's hover text."""

from __future__ import annotations

LIMIT = 80
OK_STATUSES = ("ok", "no_data")
ERR_CHARS = 140
FILTERS = ("all", "problems")


def load(limit: int = LIMIT) -> list[tuple]:
    from ..db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT phase, batch_date, status, rows_affected, error, started_at, finished_at FROM run_log "
                    "ORDER BY started_at DESC NULLS LAST, run_id DESC LIMIT %s", (int(limit),))
        return cur.fetchall()


def _secs(a, b) -> int | None:
    return None if a is None or b is None else max(0, round((b - a).total_seconds()))


def view(rows: list[tuple], only: str = "all") -> dict:
    """{rows, shown, total, problems, filter}. `only='problems'` keeps the runs whose status is not ok/no_data."""
    out = []
    for phase, batch, status, n, err, started, finished in rows:
        bad = status not in OK_STATUSES
        s = _secs(started, finished)
        out.append({
            "phase": phase, "batch": batch.isoformat() if batch else "—", "status": status, "bad": bad,
            "rows": f"{n:,}" if n is not None else "—",
            "started": started.strftime("%d %b %H:%M") if started else "—",
            "took": "—" if s is None else (f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"),
            "error": (err or "")[:ERR_CHARS] + ("…" if err and len(err) > ERR_CHARS else ""), "error_full": err or "",
        })
    problems = sum(r["bad"] for r in out)
    only = only if only in FILTERS else "all"
    shown = [r for r in out if r["bad"]] if only == "problems" else out
    return {"rows": shown, "shown": len(shown), "total": len(out), "problems": problems, "filter": only}
