"""How long the end-of-day run takes, read from `eod.log` (the file `scripts/eod.sh` appends to).

The EOD script writes three kinds of marker lines, and this module reads nothing else from it:

    ===================== EOD 2026-11-05T19:15:02+07:00 =====================         start of a run
    -- order-flow harvest took 412s (budget 15 min) --                              the time-boxed done-details step (newer runs only)
    ----- done 2026-11-05T19:25:27+07:00 -----                                       end of a run

A run with a start but no `done` line is shown as unfinished (still running, or it died). A run without the harvest line (runs
before the line was added) simply has no harvest figure: it is N/A, never a guess. The file is only read, never written, and only
its last part (`TAIL_BYTES`), so a log that grew for years costs nothing.

`parse` is pure and tested; `load` is the only part that touches the file system.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

TAIL_BYTES = 600_000
DEFAULT_BUDGET_MIN = 15
_START = re.compile(r"^=+ EOD (\S+) =+\s*$")
_DONE = re.compile(r"^-+ done (\S+) -+\s*$")
_HARVEST = re.compile(r"^-- order-flow harvest took (\d+)s \(budget (\d+) min\) --\s*$")


def _ts(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def parse(text: str, limit: int = 7) -> list[dict]:
    """The last `limit` runs, newest first: {start, end, seconds, harvest_s, budget_min}. `end`/`seconds` are None while unfinished."""
    runs: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = _START.match(line)
        if m:
            start = _ts(m.group(1))
            cur = {"start": start, "end": None, "seconds": None, "harvest_s": None, "budget_min": DEFAULT_BUDGET_MIN} if start else None
            if cur:
                runs.append(cur)
            continue
        if cur is None:
            continue
        m = _HARVEST.match(line)
        if m:
            cur["harvest_s"], cur["budget_min"] = int(m.group(1)), int(m.group(2))
            continue
        m = _DONE.match(line)
        if m:
            end = _ts(m.group(1))
            if end:
                cur["end"], cur["seconds"] = end, max(0, round((end - cur["start"]).total_seconds()))
            cur = None
    return runs[-limit:][::-1]


def minutes(seconds: int | None) -> str:
    if seconds is None:
        return "N/A"
    m, s = divmod(int(seconds), 60)
    return f"{m} min {s:02d} s" if m else f"{s} s"


def view(runs: list[dict], budget_min: int = DEFAULT_BUDGET_MIN) -> dict | None:
    """Template data: the newest run as the headline, the harvest against its budget (a bar that fills to 100% and turns red past it),
    and one row per run. None when the log has no run at all."""
    if not runs:
        return None
    last = runs[0]
    budget = last["budget_min"] or budget_min
    h = last["harvest_s"]
    pct = None if h is None else round(h / (budget * 60) * 100)
    rows = [{"day": r["start"].strftime("%a %d %b"), "start": r["start"].strftime("%H:%M"),
             "total": minutes(r["seconds"]) if r["seconds"] is not None else "unfinished",
             "harvest": minutes(r["harvest_s"]), "over": bool(r["harvest_s"] is not None and r["harvest_s"] > r["budget_min"] * 60)}
            for r in runs]
    return {
        "day": last["start"].strftime("%a %d %b %Y"), "start": last["start"].strftime("%H:%M"),
        "total": minutes(last["seconds"]) if last["seconds"] is not None else "unfinished",
        "finished": last["seconds"] is not None,
        "harvest": minutes(h), "harvest_pct": pct, "harvest_bar": None if pct is None else min(100, pct),
        "budget": f"{budget} min", "over": bool(pct is not None and pct > 100), "rows": rows,
    }


def log_path() -> Path:
    env = os.environ.get("VG_EOD_LOG")
    return Path(env) if env else Path(__file__).resolve().parents[3] / "eod.log"


def load(path: Path | None = None, limit: int = 7) -> dict | None:
    """Read the tail of the log. None when there is no file (the page then says the log is not available here)."""
    p = path or log_path()
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    return view(parse(text, limit))
