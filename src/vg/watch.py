"""Is every data collection actually running? The existing checks count FAILED runs; a collector that simply stops running produces no
failure and so no warning. This module closes that gap: for each collector that is expected to run on every trading day it looks for a
good run since the latest session, and it judges the running-trade recorder day by day.

Pure rules (`assess`, `assess_recorder`, `assess_quota`) are unit-tested; `load()` is the only part that touches the database
(SELECT only). Results are plain `(level, name, text)` tuples that `vg.status` turns into its checks, so they reach both the Telegram
status message and the dashboard's system page.

Rules, deliberately simple:
  * a collector is FRESH when it has a run with status ok / no_data that started on or after the latest trading session's date
    (no_data is a normal answer: no halts that day, no filings, a holiday);
  * until 20:30 on the session day itself a collector that has not run yet is "still due", not stale - the nightly job takes ~20 minutes;
  * the three that everything else hangs on (prices, broker flows, compute) are a problem (bad) when stale, the rest a warning;
  * the recorder must have a good run with trades for every completed session since its first run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time

OK, WARN, BAD = "ok", "warn", "bad"

# (run_log phase, label, critical). Runs on every trading day in scripts/eod.sh.
DAILY = (
    ("collect_price", "prices", True),
    ("collect_index", "index", False),
    ("collect_broker", "broker flows", True),
    ("collect_broker_inventory", "broker inventory", False),
    ("collect_broker_full", "complete broker tables", False),
    ("collect_done_details", "done details", False),
    ("collect_disclosure", "disclosure filings", False),
    ("parse_disclosure", "disclosure parsing", False),
    ("collect_trading_halts", "trading halts", False),
    ("quality_gate", "quality gate", False),
    ("compute", "signal compute", True),
    ("backfill_outcomes", "signal outcomes", False),
)
# (phase, label, warn after days, bad after days). Run in the weekly / monthly slot of eod.sh.
PERIODIC = (
    ("collect_corpactions", "corporate actions", 10, 30),
)
GOOD = ("ok", "no_data")
STILL_DUE_UNTIL = time(20, 30)       # the nightly job is done well before this
RECORDER_PHASE = "trade_record"
RECORDER_DONE_AT = time(16, 30)      # the market closes 16:00 (pre-close to 16:05), the recorder stops with it
RECORDER_LOOKBACK = 5                # sessions judged
RECORDER_FINE_STOPS = ("market_closed", "max_seconds")
QUOTA_LOW = 1000                     # calls left today below which the next collection is at risk
QUOTA_PLAN_FLOOR = 5000              # a daily limit below this means the paid plan is not active


@dataclass
class WatchInputs:
    now: datetime
    session: date | None                                  # latest trading session in the calendar
    sessions: list[date] = field(default_factory=list)    # the latest few, newest first
    last_ok: dict = field(default_factory=dict)           # phase -> date of the last good run
    last_run: dict = field(default_factory=dict)          # phase -> (status, date, error) of the latest run of any kind
    recorder: list = field(default_factory=list)          # (batch_date, status, trades, detail) of the recorder's runs
    recorder_first: date | None = None                    # the date of its very first run


def _d(d: date | None) -> str:
    return f"{d.day} {d:%b}" if d else "?"


def assess(inp: WatchInputs) -> list[tuple[str, str, str]]:
    """The 'Collectors' check: which expected collections have not produced a good run since the latest session."""
    if inp.session is None:
        return [(WARN, "Collectors", "no trading session found, so I cannot tell whether the collectors ran")]
    still_due = inp.now.date() == inp.session and inp.now.time() < STILL_DUE_UNTIL
    stale: list[tuple[str, str, bool]] = []
    waiting = False
    for phase, label, critical in DAILY:
        ok_day = inp.last_ok.get(phase)
        if ok_day is not None and ok_day >= inp.session:
            continue
        last = inp.last_run.get(phase)
        if last and last[0] not in GOOD and last[1] >= inp.session:
            stale.append((label, "its last run failed", critical))
        elif still_due:
            waiting = True
            continue
        elif ok_day is None:
            stale.append((label, "has never run", critical))
        else:
            stale.append((label, f"last good run {_d(ok_day)}", critical))
    out: list[tuple[str, str, str]] = []
    if stale:
        level = BAD if any(c for _l, _w, c in stale) else WARN
        out.append((level, "Collectors", f"not run for the {_d(inp.session)} session: " + "; ".join(f"{label} ({why})" for label, why, _c in stale)))
    else:
        n = len(DAILY)
        out.append((OK, "Collectors", f"all {n} daily collections ran for the {_d(inp.session)} session" + (" (more still due tonight)" if waiting else "")))
    today = inp.now.date()
    for phase, label, warn_d, bad_d in PERIODIC:
        ok_day = inp.last_ok.get(phase)
        if ok_day is None:
            out.append((WARN, f"Collector: {label}", "has never run"))
            continue
        age = (today - ok_day).days
        if age > bad_d:
            out.append((BAD, f"Collector: {label}", f"last good run {age} days ago"))
        elif age > warn_d:
            out.append((WARN, f"Collector: {label}", f"last good run {age} days ago"))
    return out


def assess_recorder(inp: WatchInputs) -> list[tuple[str, str, str]]:
    """The running-trade recorder: a good run with trades for every completed session since its first run."""
    name = "Trade recorder"
    if inp.recorder_first is None:
        return [(OK, name, "has not run yet (first session pending)")]
    today, now_t = inp.now.date(), inp.now.time()
    per_day: dict[date, list] = {}
    for batch, status, trades, detail in inp.recorder:
        per_day.setdefault(batch, []).append((status, trades or 0, detail or ""))
    judged = [s for s in inp.sessions if s >= inp.recorder_first and (s < today or now_t >= RECORDER_DONE_AT)][:RECORDER_LOOKBACK]
    if not judged:
        return [(OK, name, "nothing to judge yet")]
    missing, empty, odd = [], [], []
    for s in judged:
        runs = per_day.get(s)
        if not runs:
            missing.append(s)
            continue
        good = [r for r in runs if r[0] == "ok"]
        if not good:
            odd.append((s, "every run failed"))
        elif sum(r[1] for r in good) == 0:
            empty.append(s)
        else:
            stop = re.search(r"stopped: (\w+)", good[-1][2])
            if stop and stop.group(1) not in RECORDER_FINE_STOPS:
                odd.append((s, f"stopped early ({stop.group(1)})"))
    problems = []
    if missing:
        problems.append("no recording for " + ", ".join(_d(s) for s in missing))
    if empty:
        problems.append("no trades captured on " + ", ".join(_d(s) for s in empty))
    problems += [f"{_d(s)}: {why}" for s, why in odd]
    if problems:
        return [(WARN, name, "; ".join(problems))]
    newest = judged[0]
    total = sum(r[1] for r in per_day.get(newest, []) if r[0] == "ok")
    return [(OK, name, (f"recorded the last {len(judged)} sessions" if len(judged) > 1 else "recorded the last session") + f"; {_d(newest)}: {total:,} trades")]


def assess_quota(q: dict | None) -> list[tuple[str, str, str]]:
    """The data feed's daily call allowance, read from the response headers of one probe call."""
    if not q:
        return []
    remaining, limit = q.get("remaining"), q.get("limit")
    if remaining is None and limit is None:
        return [(WARN, "Feed quota", "the feed did not report its allowance")]
    if limit is not None and limit < QUOTA_PLAN_FLOOR:
        return [(WARN, "Feed quota", f"daily limit is only {limit:,} calls ({'' if remaining is None else f'{remaining:,} left; '}the paid plan has ended?) - the nightly collection needs more")]
    if remaining is not None and remaining < QUOTA_LOW:
        return [(WARN, "Feed quota", f"only {remaining:,} calls left today" + ("" if limit is None else f" of {limit:,}") + " - the next collection may be cut short")]
    return [(OK, "Feed quota", f"{'?' if remaining is None else f'{remaining:,}'} calls left today" + ("" if limit is None else f" of {limit:,}"))]


def checks(facts: dict | None, quota: dict | None) -> list[tuple[str, str, str]]:
    """Everything above for `vg.status`; `facts` is `load()`'s answer (None when it could not be read, which gather already reports)."""
    out: list[tuple[str, str, str]] = []
    if facts is not None:
        out += assess(facts) + assess_recorder(facts)
    return out + assess_quota(quota)


def load(now: datetime | None = None) -> WatchInputs:
    """Read-only: the latest sessions, the last good / latest run of every watched phase, and the recorder's runs."""
    from .db import connect

    now = now or datetime.now().astimezone()
    phases = [p for p, _l, _c in DAILY] + [p for p, *_ in PERIODIC]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT session_date FROM trading_calendar ORDER BY session_date DESC LIMIT 12")
        sessions = [r[0] for r in cur.fetchall()]
        cur.execute(
            "SELECT phase, (max(started_at) FILTER (WHERE status = ANY(%s)) AT TIME ZONE 'Asia/Jakarta')::date "
            "FROM run_log WHERE phase = ANY(%s) AND started_at > now() - interval '120 days' GROUP BY phase",
            (list(GOOD), phases))
        last_ok = {p: d for p, d in cur.fetchall() if d is not None}
        cur.execute(
            "SELECT DISTINCT ON (phase) phase, status, (started_at AT TIME ZONE 'Asia/Jakarta')::date, error FROM run_log "
            "WHERE phase = ANY(%s) AND started_at > now() - interval '120 days' ORDER BY phase, started_at DESC",
            (phases,))
        last_run = {p: (s, d, e) for p, s, d, e in cur.fetchall()}
        cur.execute("SELECT min(batch_date) FROM run_log WHERE phase = %s", (RECORDER_PHASE,))
        first = cur.fetchone()[0]
        since = sessions[-1] if sessions else date.today()  # noqa: DTZ011 - local date, as elsewhere
        cur.execute("SELECT batch_date, status, rows_affected, error FROM run_log WHERE phase = %s AND batch_date >= %s "
                    "ORDER BY started_at", (RECORDER_PHASE, since))
        rec = cur.fetchall()
    return WatchInputs(now=now, session=sessions[0] if sessions else None, sessions=sessions, last_ok=last_ok, last_run=last_run,
                       recorder=rec, recorder_first=first)
