"""Is the system actually running? One set of checks, two outputs: the Telegram status message and the dashboard's /health page.

Rule of thumb behind it: a system that fails SILENTLY is worse than one that fails loudly. So

  * `build_checks` is pure: facts in, a list of ok/warn/bad checks out (unit-tested);
  * `gather` collects the facts and never raises: anything it cannot check becomes a visible WARN, not a missing line;
  * the Telegram message is sent every day at fixed times, so the ABSENCE of a message is itself the alarm when the whole
    machine is off (add an external dead-man's switch, see README "Uptime monitoring", for that case);
  * OK messages are sent silently, problems with a notification sound.
"""

from __future__ import annotations

import html
import json
import shutil
from datetime import date, datetime, timedelta

OK, WARN, BAD = "ok", "warn", "bad"
_RANK = {OK: 0, WARN: 1, BAD: 2}
_ICON = {OK: "✅", WARN: "⚠️", BAD: "🔴"}
_MARK = {OK: "✓", WARN: "⚠", BAD: "✖"}

DEEP_BUDGET = 15000
DEEP_START, DEEP_END = date(2023, 1, 2), date(2026, 6, 2)
DEEP_WARN_HOURS, DEEP_BAD_HOURS = 30, 60
BACKUP_WARN_DAYS, BACKUP_BAD_DAYS = 9, 16
DISK_WARN_PCT, DISK_BAD_PCT = 15.0, 7.0
SNAPSHOT_WARN_DAYS = 10
FAILED_RUNS_WARN = 5
SNAPSHOT_NAME = "status_last_v1"
DEEP_SNAPSHOT_NAME = "deep_progress_v1"


class Check:
    __slots__ = ("level", "name", "text")

    def __init__(self, level: str, name: str, text: str) -> None:
        self.level, self.name, self.text = level, name, text

    def as_dict(self) -> dict:
        return {"level": self.level, "name": self.name, "text": self.text, "mark": _MARK[self.level]}

    def __repr__(self) -> str:  # pragma: no cover
        return f"Check({self.level}, {self.name!r}, {self.text!r})"


def worst(levels) -> str:
    return max(levels, key=_RANK.__getitem__, default=OK)


def _age_hours(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    t = datetime.fromisoformat(iso)
    if t.tzinfo is None and now.tzinfo is not None:
        t = t.replace(tzinfo=now.tzinfo)
    return (now - t).total_seconds() / 3600


def _ago(hours: float | None) -> str:
    if hours is None:
        return "never"
    if hours < 1:
        return "under an hour ago"
    if hours < 48:
        return f"{hours:.0f} h ago"
    return f"{hours / 24:.0f} days ago"


# ---------------------------------------------------------------------------------------
# Pure part
# ---------------------------------------------------------------------------------------

def deep_progress(deep: dict | None) -> dict | None:
    """Numbers for the K7 backfill progress bar, or None when unknown."""
    if not deep or deep.get("total_days") in (None, 0):
        return None
    total, pend = deep["total_days"], deep["pending_days"]
    return {"done_days": total - pend, "total_days": total, "pending_days": pend, "pending_calls": deep["pending_calls"],
            "pct": round(100 * (total - pend) / total), "nights": (deep["pending_calls"] / DEEP_BUDGET) if deep["pending_calls"] else 0.0}


def build_checks(f: dict) -> list[Check]:
    now: datetime = f["now"]
    out: list[Check] = []
    for it in f.get("strip", []):
        out.append(Check(it["level"], it["label"], it["text"]))

    failed = f.get("failed_24h") or {}
    n_failed = sum(failed.values())
    if f.get("blocked_24h"):
        out.append(Check(BAD, "Quality gate", f"blocked the compute step {f['blocked_24h']}× in the last 24 h (inputs were broken)"))
    elif n_failed >= FAILED_RUNS_WARN:
        top = ", ".join(f"{k} ×{v}" for k, v in sorted(failed.items(), key=lambda kv: -kv[1])[:3])
        out.append(Check(WARN, "Collector errors", f"{n_failed} failed runs in the last 24 h ({top})"))
    else:
        out.append(Check(OK, "Collector errors", f"{n_failed} failed run{'s' if n_failed != 1 else ''} in the last 24 h"))

    deep = f.get("deep")
    prog = deep_progress(deep)
    if prog is not None:
        last = deep.get("last")
        age = _age_hours(last["started_at"], now) if last else None
        if prog["pending_calls"] == 0:
            out.append(Check(OK, "Broker history (K7)", "complete: every day from 2023 is filled"))
        elif prog["done_days"] == 0 and last is None:
            out.append(Check(OK, "Broker history (K7)", f"not started yet ({prog['pending_calls']:,} calls to fetch)"))
        elif age is None or age > DEEP_BAD_HOURS:
            out.append(Check(BAD, "Broker history (K7)", f"has not run for {_ago(age)}; {prog['pending_days']} days still to fetch"))
        elif age > DEEP_WARN_HOURS:
            out.append(Check(WARN, "Broker history (K7)", f"last run {_ago(age)} (a night was missed?); {prog['pending_days']} days to fetch"))
        elif last["status"] != "ok":
            out.append(Check(WARN, "Broker history (K7)", f"last run ended with an error: {html.escape(str(last.get('error') or ''))[:120]}"))
        else:
            out.append(Check(OK, "Broker history (K7)", f"{prog['pct']}% done, ~{prog['nights']:.1f} nights left; last run {_ago(age)}"))

    bk = f.get("backup")
    if bk is not None:
        if bk.get("tag") is None:
            out.append(Check(WARN, "Backup", "no backup release found on GitHub"))
        else:
            level = BAD if bk["age_days"] > BACKUP_BAD_DAYS else WARN if bk["age_days"] > BACKUP_WARN_DAYS else OK
            out.append(Check(level, "Backup", f"{bk['tag']} ({bk['age_days']:.0f} days old)"))

    if f.get("dashboard") is not None:
        out.append(Check(OK if f["dashboard"] else WARN, "Dashboard", "answering on :8787" if f["dashboard"] else "not answering on :8787"))

    d = f.get("disk_free_pct")
    if d is not None:
        out.append(Check(BAD if d < DISK_BAD_PCT else WARN if d < DISK_WARN_PCT else OK, "Disk", f"{d:.0f}% free"))

    for name, label in (("risk_eval_v1", "Stop/target test"), ("research_v1", "Edge analysis")):
        iso = (f.get("snapshots") or {}).get(name)
        if iso is not None:
            age = _age_hours(iso, now) / 24
            out.append(Check(WARN if age > SNAPSHOT_WARN_DAYS else OK, label, f"refreshed {age:.0f} days ago" if age >= 1 else "refreshed today"))

    for msg in f.get("errors", []):
        out.append(Check(WARN, "Status check", msg))
    return out


def headline(checks: list[Check]) -> tuple[str, str]:
    bad = sum(1 for c in checks if c.level == BAD)
    warn = sum(1 for c in checks if c.level == WARN)
    if bad:
        return BAD, f"{bad} problem{'s' if bad != 1 else ''}" + (f", {warn} warning{'s' if warn != 1 else ''}" if warn else "")
    if warn:
        return WARN, f"{warn} warning{'s' if warn != 1 else ''}"
    return OK, "All systems normal"


def _bar(pct: int, width: int = 10) -> str:
    filled = round(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


def telegram_text(f: dict, checks: list[Check], kind: str) -> tuple[str, str]:
    """(level, HTML text). `kind`: 'morning' | 'evening' | 'backfill'."""
    level, head = headline(checks)
    when = f["now"].strftime("%a %-d %b %H:%M") if hasattr(f["now"], "strftime") else str(f["now"])
    label = {"morning": "morning status", "evening": "after-EOD status", "backfill": "backfill status"}.get(kind, "status")
    lines = [f"{_ICON[level]} <b>Vektor Gauss — {label}</b> · {when}", head]
    attention = [c for c in checks if c.level != OK]
    if attention:
        lines += ["", "<b>Needs attention</b>"] + [f"{_MARK[c.level]} <b>{html.escape(c.name)}</b>: {html.escape(c.text)}" for c in attention]
    data = [c for c in checks if c.level == OK and c.name in ("EOD", "Prices", "Broker", "KSEI")]
    if data:
        lines += ["", "<b>Data</b>", " · ".join(f"✓ {html.escape(c.name)} {html.escape(c.text)}" for c in data)]
    prog = deep_progress(f.get("deep"))
    if prog is not None and prog["pending_calls"]:
        last = (f.get("deep") or {}).get("last")
        tail = ""
        if last:
            tail = f"\nLast run: {last.get('calls', 0):,} calls, {last['status']}, {_ago(_age_hours(last['started_at'], f['now']))}"
        lines += ["", "<b>Broker history (K7)</b>",
                  f"{_bar(prog['pct'])} {prog['pct']}% · {prog['pending_days']} of {prog['total_days']} days left · ~{prog['pending_calls']:,} calls · ~{prog['nights']:.1f} nights" + tail]
    other = [c for c in checks if c.level == OK and c.name in ("Backup", "Dashboard", "Disk", "Edge analysis", "Stop/target test", "Collector errors")]
    if other:
        lines += ["", "<b>Other</b>", " · ".join(f"{html.escape(c.name)}: {html.escape(c.text)}" for c in other)]
    return level, "\n".join(lines)


def should_be_silent(level: str, kind: str) -> bool:
    """OK reports from the evening and backfill runs are delivered without a notification sound; everything else is loud."""
    return level == OK and kind in ("evening", "backfill")


# ---------------------------------------------------------------------------------------
# Collection (never raises)
# ---------------------------------------------------------------------------------------

def _try(errors: list[str], what: str, fn, default=None):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — a failed check must show up as a warning, not vanish
        errors.append(f"could not check {what}: {type(exc).__name__}: {str(exc)[:80]}")
        return default


def _run_log_facts(now: datetime) -> dict:
    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT phase, status, count(*) FROM run_log WHERE started_at > now() - interval '24 hours' "
                    "AND status IN ('error', 'blocked') GROUP BY 1, 2")
        failed, blocked = {}, 0
        for phase, status, n in cur.fetchall():
            if status == "blocked":
                blocked += n
            else:
                failed[phase] = failed.get(phase, 0) + n
        cur.execute("SELECT phase, status, rows_affected, error, started_at FROM run_log WHERE phase = 'broker_deep' "
                    "ORDER BY started_at DESC LIMIT 1")
        row = cur.fetchone()
        last_deep = None if not row else {"status": row[1], "calls": row[2], "error": row[3], "started_at": row[4].isoformat()}
        cur.execute("SELECT DISTINCT ON (phase) phase, status, rows_affected, started_at FROM run_log "
                    "WHERE started_at > now() - interval '3 days' ORDER BY phase, started_at DESC")
        recent = sorted(({"phase": p, "status": s, "rows": r, "started_at": t.isoformat()} for p, s, r, t in cur.fetchall()),
                        key=lambda x: x["started_at"], reverse=True)
        cur.execute("SELECT name, computed_at FROM analysis_snapshot WHERE name IN ('risk_eval_v1', 'research_v1')")
        snaps = {n: t.isoformat() for n, t in cur.fetchall()}
    return {"failed_24h": failed, "blocked_24h": blocked, "last_deep": last_deep, "recent_runs": recent, "snapshots": snaps}


def _backup_fact(now: datetime, repo: str) -> dict | None:
    import subprocess

    p = subprocess.run(["gh", "release", "list", "--repo", repo, "--limit", "20", "--json", "tagName,createdAt"],
                       capture_output=True, text=True, timeout=25)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:80] or "gh failed")
    tags = [(r["tagName"], r["createdAt"]) for r in json.loads(p.stdout) if r["tagName"].startswith("backup-")]
    if not tags:
        return {"tag": None, "age_days": 0.0}
    # the tag date (backup-YYYY-MM-DD) is the day the backup was taken; the release's own createdAt is the tagged commit's date
    tag = max(t for t, _c in tags)
    taken = date.fromisoformat(tag[len("backup-"):])
    return {"tag": tag, "age_days": float((now.date() - taken).days)}


def _dashboard_fact() -> bool:
    import httpx

    try:
        return httpx.get("http://127.0.0.1:8787/healthz", timeout=5).status_code == 200
    except httpx.HTTPError:
        return False


def _deep_plan() -> dict:
    from . import collect

    res = collect.backfill_broker_deep(DEEP_START, DEEP_END, budget=0, plan_only=True)
    return {"total_days": res["days_total"], "pending_days": res["pending_days"], "pending_calls": res["pending_calls"]}


def gather(now: datetime | None = None, *, full: bool = True) -> dict:
    """Collect the facts. `full=False` (used by the dashboard) skips the slow or external probes (GitHub, the 800-day backfill plan)
    and reuses what the last `vg status` run saved."""
    from .config import CONFIG
    from .web import freshness as fr

    now = now or datetime.now().astimezone()
    errors: list[str] = []
    f: dict = {"now": now, "strip": [], "errors": errors}
    strip = _try(errors, "data freshness", lambda: fr.build_strip(fr.load_inputs()))
    if strip:
        f["strip"] = [{"label": i.label, "text": i.text, "level": i.level} for i in strip]
    rl = _try(errors, "run log", lambda: _run_log_facts(now), {})
    f.update({k: v for k, v in rl.items() if k != "last_deep"})
    last_saved = _try(errors, "saved status", load_snapshot, None)
    saved = (last_saved[0] if last_saved else {}) or {}
    deep_plan = _try(errors, "broker backfill plan", _deep_plan) if full else saved.get("deep_plan")
    if deep_plan:
        f["deep"] = {**deep_plan, "last": rl.get("last_deep")}
    if full:
        f["backup"] = _try(errors, "backups", lambda: _backup_fact(now, getattr(CONFIG, "backup_repo", "jaringan-dev/vektor-gauss-idx")))
        f["dashboard"] = _try(errors, "dashboard", _dashboard_fact)
    else:
        f["backup"], f["dashboard"] = saved.get("backup"), True          # a page that renders is the dashboard answering
    f["disk_free_pct"] = _try(errors, "disk", lambda: 100.0 * shutil.disk_usage("/").free / shutil.disk_usage("/").total)
    return f


def to_jsonable(f: dict) -> dict:
    out = dict(f)
    out["now"] = f["now"].isoformat()
    return out


def save_snapshot(f: dict, checks: list[Check]) -> None:
    from .db import connect

    payload = {"facts": to_jsonable(f), "checks": [c.as_dict() for c in checks],
               "deep_plan": ({k: f["deep"][k] for k in ("total_days", "pending_days", "pending_calls")} if f.get("deep") else None),
               "backup": f.get("backup")}
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO analysis_snapshot (name, payload, computed_at) VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (name) DO UPDATE SET payload = EXCLUDED.payload, computed_at = now()",
            (SNAPSHOT_NAME, json.dumps(payload)))


def load_snapshot():
    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT payload, computed_at FROM analysis_snapshot WHERE name = %s", (SNAPSHOT_NAME,))
        row = cur.fetchone()
    return (row[0], row[1]) if row else None


def log_deep_run(calls: int, error: str | None) -> None:
    """Record a `vg broker-deep` run so the status report can tell a missed night from a quiet one."""
    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO run_log (phase, batch_date, status, rows_affected, error, finished_at) VALUES ('broker_deep', %s, %s, %s, %s, now())",
            (date.today(), "error" if error else "ok", calls, error))


def report(kind: str = "morning", *, full: bool = True) -> tuple[str, str, dict, list[Check]]:
    f = gather(full=full)
    checks = build_checks(f)
    level, text = telegram_text(f, checks, kind)
    return level, text, f, checks
