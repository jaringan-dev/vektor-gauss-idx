"""Backtest of the mechanical stop/target rule in `risk.py`, per pattern (RENCANA-ROMBAK-UI-v3 §9).

The UI shows a stop at (reference − 2 x ATR14) and targets at 1R/2R/3R. Those are arithmetic until measured. This module
replays the rule on every historical pattern signal and asks the only question that matters: after costs, did entering at
the reference price and exiting on whichever comes first — stop, target, or a 10-trading-day time exit — beat doing
nothing special? The same rule is also applied to ALL stocks on the same days, because a rule can look good simply
because the market went up.

Replay rules (all conservative):
  * entry = the signal day's close (the same reference price the UI uses); bars after it are replayed one by one
  * a gap through the stop exits at the open (worse than planned); a gap through the target exits at the open
  * if stop and target are both inside one bar's range, the STOP is assumed to hit first
  * no hit within HORIZON trading days -> exit at that day's close
  * prices are adjusted for splits/bonus issues (price_adj); cash dividends are not (a small, real price drop)
  * round-trip trading cost ROUND_TRIP_COST is subtracted from every trade
  * only signals with HORIZON later bars are used (matured), so nothing is judged on partial information

Pure functions (`simulate`, `evaluate`) are unit-tested with synthetic bars; `load_inputs` is the only DB part.
Retrospective by construction: it replays history, it is not a forward test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import risk

HORIZON = 10                  # trading days a trade may stay open
ROUND_TRIP_COST = 0.017       # same assumption as the forward-test gate (web/status.py)
TARGET_R = (1.0, 2.0, 3.0)
LIQUID_VALUE_IDR = 5_000_000_000
BASELINE = "ALL STOCKS"       # the same rule applied to every stock with an ATR on those days


@dataclass(frozen=True)
class Bar:
    o: float | None
    h: float
    l: float
    c: float


@dataclass(frozen=True)
class Sig:
    day: date
    ticker: str
    tag: str
    liquid: bool


def simulate(entry: float, stop: float, target: float, bars: list[Bar]) -> tuple[str, float]:
    """(kind, exit_price) where kind is 'stop' | 'target' | 'time'. See the module docstring for the replay rules."""
    for b in bars:
        if b.o is not None:
            if b.o <= stop:
                return "stop", b.o
            if b.o >= target:
                return "target", b.o
        if b.l <= stop:                      # stop first when both are inside the range
            return "stop", stop
        if b.h >= target:
            return "target", target
    return "time", bars[-1].c


@dataclass
class Stats:
    n: int = 0
    stop: int = 0
    target: int = 0
    time: int = 0
    net_sum: float = 0.0
    gross_r_sum: float = 0.0
    wins: int = 0

    def add(self, kind: str, entry: float, stop_price: float, exit_price: float, cost: float) -> None:
        self.n += 1
        setattr(self, kind, getattr(self, kind) + 1)
        net = exit_price / entry - 1 - cost
        self.net_sum += net
        self.gross_r_sum += (exit_price - entry) / (entry - stop_price)
        self.wins += net > 0

    def row(self) -> dict:
        n = self.n or 1
        return {"n": self.n, "stop_pct": self.stop / n, "target_pct": self.target / n, "time_pct": self.time / n,
                "net": self.net_sum / n, "win": self.wins / n, "exp_r": self.gross_r_sum / n}


@dataclass
class Group:
    n: int = 0
    hold_net_sum: float = 0.0
    by_r: dict = field(default_factory=lambda: {r: Stats() for r in TARGET_R})


def evaluate(signals: list[tuple[Sig, float, float, list[Bar]]], *, k: float = risk.STOP_ATR_MULT,
             cost: float = ROUND_TRIP_COST) -> dict:
    """signals: (signal, entry, atr, following bars [HORIZON]). Returns {(group, scope): Group}.

    A signal with a tag feeds (tag, 'all') and, when liquid, (tag, 'liquid'). Baseline signals use the tag BASELINE.
    Signals without a valid plan (no ATR, stop at or below zero) are skipped, never guessed."""
    out: dict[tuple[str, str], Group] = {}
    for sig, entry, atr, bars in signals:
        if len(bars) < HORIZON:
            continue
        plan = risk.build_plan(entry, atr, k=k, r_multiples=TARGET_R)
        if plan is None:
            continue
        scopes = ("all", "liquid") if sig.liquid else ("all",)
        hold = bars[HORIZON - 1].c / entry - 1 - cost
        for scope in scopes:
            g = out.setdefault((sig.tag, scope), Group())
            g.n += 1
            g.hold_net_sum += hold
            for t in plan.targets:
                kind, px = simulate(entry, plan.stop, t.price, bars[:HORIZON])
                g.by_r[t.r].add(kind, entry, plan.stop, px, cost)
    return out


def table(groups: dict, scope: str = "all", min_n: int = 1) -> list[dict]:
    """Flat display rows, baseline first then patterns by descending n."""
    rows = []
    for (tag, sc), g in groups.items():
        if sc != scope or g.n < min_n:
            continue
        rows.append({"tag": tag, "n": g.n, "hold": g.hold_net_sum / g.n,
                     "r": {r: s.row() for r, s in g.by_r.items()}})
    rows.sort(key=lambda r: (r["tag"] != BASELINE, -r["n"]))
    return rows


# ---------------------------------------------------------------------------------------
# DB part
# ---------------------------------------------------------------------------------------

def load_inputs(since: date | None = None, *, with_baseline: bool = True):
    """(signals, window) where signals feed `evaluate`. Window = (first_day, last_day) actually used."""
    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        if since is None:
            cur.execute("SELECT min(session_date) FROM signal_log")
            since = cur.fetchone()[0]
        if since is None:
            return [], None
        cur.execute(
            "SELECT b.ticker, b.session_date, b.open, b.high, b.low, b.close, COALESCE(a.split_factor, 1) "
            "FROM price_bars b LEFT JOIN price_adj a USING (session_date, ticker) "
            "WHERE b.session_date >= %s AND b.close > 0 AND b.high > 0 AND b.low > 0 ORDER BY b.ticker, b.session_date",
            (since,))
        bars: dict[str, list[tuple[date, Bar]]] = {}
        for tk, d, o, h, l, c, f in cur.fetchall():
            f = float(f)
            bars.setdefault(tk, []).append((d, Bar(float(o) * f if o else None, float(h) * f, float(l) * f, float(c) * f)))
        index = {tk: {d: i for i, (d, _b) in enumerate(rows)} for tk, rows in bars.items()}
        factor: dict[tuple[str, date], float] = {}
        cur.execute("SELECT ticker, session_date, split_factor FROM price_adj WHERE session_date >= %s", (since,))
        for tk, d, f in cur.fetchall():
            factor[(tk, d)] = float(f)
        cur.execute(
            "SELECT sl.session_date, sl.ticker, sl.tag, sc.atr14, pb.value FROM signal_log sl "
            "JOIN signal_components sc ON sc.session_date = sl.session_date AND sc.ticker = sl.ticker "
            "JOIN price_bars pb ON pb.session_date = sl.session_date AND pb.ticker = sl.ticker "
            "WHERE sl.session_date >= %s AND sc.atr14 IS NOT NULL", (since,))
        raw = [(d, tk, tag, float(atr), float(val or 0)) for d, tk, tag, atr, val in cur.fetchall()]
        if with_baseline:
            cur.execute(
                "SELECT sc.session_date, sc.ticker, sc.atr14, pb.value FROM signal_components sc "
                "JOIN price_bars pb ON pb.session_date = sc.session_date AND pb.ticker = sc.ticker "
                "WHERE sc.session_date >= %s AND sc.atr14 IS NOT NULL", (since,))
            raw += [(d, tk, BASELINE, float(atr), float(val or 0)) for d, tk, atr, val in cur.fetchall()]

    sigs = []
    first = last = None
    for d, tk, tag, atr, val in raw:
        i = index.get(tk, {}).get(d)
        if i is None:
            continue
        rows = bars[tk]
        if i + HORIZON >= len(rows):
            continue                                                   # not matured: never judged on partial data
        f = factor.get((tk, d), 1.0)
        entry = rows[i][1].c
        sigs.append((Sig(d, tk, tag, val >= LIQUID_VALUE_IDR), entry, atr * f, [b for _d, b in rows[i + 1:i + 1 + HORIZON]]))
        first = d if first is None or d < first else first
        last = d if last is None or d > last else last
    return sigs, ((first, last) if first else None)


def run(since: date | None = None) -> tuple[dict, tuple | None]:
    sigs, window = load_inputs(since)
    return evaluate(sigs), window


def format_text(rows: list[dict], window: tuple | None) -> str:
    """Plain-text table for the CLI."""
    lines = []
    if window:
        lines.append(f"signals {window[0]} .. {window[1]} (matured: {HORIZON} later trading days); "
                     f"cost {ROUND_TRIP_COST * 100:.1f}% round trip; stop = entry - {risk.STOP_ATR_MULT:g} x ATR14")
    lines.append(f"{'group':26} {'n':>7} {'hold10':>8} | " + " | ".join(f"{f'{r:g}R: stop/tgt/time  net  win' :>34}" for r in TARGET_R))
    for r in rows:
        cells = []
        for t in TARGET_R:
            s = r["r"][t]
            cells.append(f"{s['stop_pct'] * 100:3.0f}%/{s['target_pct'] * 100:3.0f}%/{s['time_pct'] * 100:3.0f}%  {s['net'] * 100:+6.2f}%  {s['win'] * 100:3.0f}%")
        lines.append(f"{r['tag']:26} {r['n']:7d} {r['hold'] * 100:+7.2f}% | " + " | ".join(f"{c:>34}" for c in cells))
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------
# Snapshot: computed offline (`vg risk-eval --save`), read by the dashboard
# ---------------------------------------------------------------------------------------

SNAPSHOT_NAME = "risk_eval_v1"


def snapshot_payload(groups: dict, window: tuple | None, *, min_n: int = 30) -> dict:
    """JSON-safe, self-describing result for both scopes (float R keys become '1', '2', '3')."""
    def rows(scope: str) -> list[dict]:
        return [{"tag": r["tag"], "n": r["n"], "hold": r["hold"], "r": {f"{k:g}": v for k, v in r["r"].items()}}
                for r in table(groups, scope, min_n)]

    return {
        "window": [window[0].isoformat(), window[1].isoformat()] if window else None,
        "horizon": HORIZON, "cost": ROUND_TRIP_COST, "stop_atr": risk.STOP_ATR_MULT,
        "targets": [f"{r:g}" for r in TARGET_R], "baseline": BASELINE, "min_n": min_n,
        "scopes": {"all": rows("all"), "liquid": rows("liquid")},
    }


def save_snapshot(payload: dict) -> None:
    import json

    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO analysis_snapshot (name, payload, computed_at) VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (name) DO UPDATE SET payload = EXCLUDED.payload, computed_at = now()",
            (SNAPSHOT_NAME, json.dumps(payload)))


def load_snapshot() -> tuple[dict, object] | None:
    """(payload, computed_at) or None when `vg risk-eval --save` has never been run."""
    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT payload, computed_at FROM analysis_snapshot WHERE name = %s", (SNAPSHOT_NAME,))
        row = cur.fetchone()
    return (row[0], row[1]) if row else None
