"""Is a pattern's edge real? (ARSITEKTUR-DATA-v1 K10–K13)

`eval_tags` reports one number per pattern: the average 5-day return of flagged stocks minus the average of all stocks that
day. That number hides four problems, and this module measures each one:

  K10  Signals on the same day (and overlapping 5-day windows on neighbouring days) are not independent, so `n` overstates the
       evidence. A circular BLOCK BOOTSTRAP over trading days gives confidence intervals that respect that dependence, and
       Benjamini-Hochberg turns the p-values of ~26 patterns into false-discovery-controlled q-values (test 26 patterns and
       a couple will look good by luck).
  K11  A pattern can "work" only because it flags small or illiquid stocks that move more. Each signal is compared with the
       average 5-day return of stocks in the SAME size x liquidity bucket on the same day (25 buckets), not with the whole
       universe. (Beta and sector are not neutralised: sectors need the K9 map.)
  K12  Walk-forward view: the edge in consecutive half-year folds (the last 10 trading days of every fold are purged so
       neighbouring folds do not share overlapping return windows), and by market regime (COMPOSITE 60-day trend up/flat/down).
  K13  Costs are not one flat number: each signal pays the commission, the tick-size spread, and a square-root market-impact
       term that grows with the order size relative to that stock's traded value and its volatility.

Everything except `load_*` is pure (plain lists in, dicts out) and unit-tested; the SQL is the only DB part. Retrospective:
no result here is a forward test. Pure Python by design (no numpy in this environment).
"""

from __future__ import annotations

import math
import random
from datetime import date

BLOCK = 5                 # days per bootstrap block: 5-day returns on neighbouring days overlap
REPS = 2000
EMBARGO = 10              # trading days purged from the end of each walk-forward fold
FOLD_MONTHS = 6
REGIME_WINDOW = 60        # trading days of COMPOSITE trend that define the market regime
REGIME_BAND = 0.03
MIN_FOLD_N = 30
FEE = 0.003               # round-trip commission + levy
KAPPA = 0.5               # market-impact coefficient (square-root law); an assumption, stated on the page
QTY_IDR = 100_000_000     # assumed order size per signal
COST_CAP = 0.20
LIQUID_VALUE_IDR = 5_000_000_000
ALPHA = 0.05
SEED = 20261103


# ---------------------------------------------------------------------------------------
# K10: dependence-aware confidence intervals and false-discovery control
# ---------------------------------------------------------------------------------------

def _prefix(a: list[float]) -> list[float]:
    out = [0.0]
    for x in a:
        out.append(out[-1] + x)
    return out


def block_bootstrap(sums: list[float], counts: list[float], *, block: int = BLOCK, reps: int = REPS, seed: int = SEED) -> list[float]:
    """Distribution of (sum of `sums`) / (sum of `counts`) when whole BLOCKS of consecutive days are resampled (circular).
    `sums[t]` is the total edge of the signals on day t, `counts[t]` how many there were (0 on days without signals)."""
    T = len(sums)
    if T == 0 or sum(counts) <= 0:
        return []
    block = max(1, min(block, T))                 # a block can never be longer than the series
    ps, pn = _prefix(sums), _prefix(counts)
    k = math.ceil(T / block)
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        s = n = 0.0
        for _b in range(k):
            a = rng.randrange(T)
            e = a + block
            if e <= T:
                s += ps[e] - ps[a]
                n += pn[e] - pn[a]
            else:
                s += ps[T] - ps[a] + ps[e - T]
                n += pn[T] - pn[a] + pn[e - T]
        if n > 0:
            out.append(s / n)
    return out


def percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        raise ValueError("empty")
    i = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(i), math.ceil(i)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def interval(sums: list[float], counts: list[float], *, null: float = 0.0, block: int = BLOCK, reps: int = REPS, seed: int = SEED) -> dict:
    """Point estimate, (1-ALPHA) bootstrap interval, and one-sided p for H0: mean <= `null`."""
    total_n = sum(counts)
    if total_n <= 0:
        return {"point": None, "lo": None, "hi": None, "p": None}
    point = sum(sums) / total_n
    boot = sorted(block_bootstrap(sums, counts, block=block, reps=reps, seed=seed))
    if not boot:
        return {"point": point, "lo": None, "hi": None, "p": None}
    below = sum(1 for b in boot if b <= null)
    return {"point": point, "lo": percentile(boot, ALPHA / 2), "hi": percentile(boot, 1 - ALPHA / 2),
            "p": (below + 1) / (len(boot) + 1)}


def bh_qvalues(pvals: list[float | None]) -> list[float | None]:
    """Benjamini-Hochberg adjusted p-values (q-values); None stays None and is not counted among the tests."""
    idx = [i for i, p in enumerate(pvals) if p is not None]
    m = len(idx)
    out: list[float | None] = [None] * len(pvals)
    order = sorted(idx, key=lambda i: pvals[i])
    prev = 1.0
    for rank in range(m, 0, -1):                          # from the largest p down, enforcing monotonicity
        i = order[rank - 1]
        prev = min(prev, pvals[i] * m / rank)
        out[i] = prev
    return out


def effective_blocks(counts: list[float], block: int = BLOCK) -> float:
    """Roughly how many independent observations the signals amount to: days with any signal / block length."""
    return sum(1 for c in counts if c > 0) / block


# ---------------------------------------------------------------------------------------
# K12: walk-forward folds and market regimes
# ---------------------------------------------------------------------------------------

def fold_labels(days: list[date], months: int = FOLD_MONTHS) -> list[str]:
    """Half-year (or `months`-year) fold label of each day, e.g. '2024H2'."""
    out = []
    for d in days:
        out.append(f"{d.year}H{(d.month - 1) // months + 1}" if months == 6 else f"{d.year}-{(d.month - 1) // months + 1}")
    return out


def purge_mask(labels: list[str], embargo: int = EMBARGO) -> list[bool]:
    """True for days that are KEPT: the last `embargo` days of each fold are dropped (their forward windows reach into the next fold)."""
    keep = [True] * len(labels)
    i = len(labels)
    while i > 0:
        j = i
        while j > 0 and labels[j - 1] == labels[i - 1]:
            j -= 1
        for k in range(max(j, i - embargo), i):
            keep[k] = False
        i = j
    return keep


def fold_edges(days: list[date], sums: list[float], counts: list[float], *, months: int = FOLD_MONTHS,
               embargo: int = EMBARGO, min_n: int = MIN_FOLD_N) -> list[dict]:
    labels = fold_labels(days, months)
    keep = purge_mask(labels, embargo)
    order: list[str] = []
    acc: dict[str, list[float]] = {}
    for lab, k, s, n in zip(labels, keep, sums, counts):
        if lab not in acc:
            acc[lab] = [0.0, 0.0]
            order.append(lab)
        if k:
            acc[lab][0] += s
            acc[lab][1] += n
    return [{"label": lab, "n": int(acc[lab][1]), "edge": (acc[lab][0] / acc[lab][1]) if acc[lab][1] >= min_n else None} for lab in order]


def regime_labels(days: list[date], index_close: dict[date, float], window: int = REGIME_WINDOW, band: float = REGIME_BAND) -> list[str]:
    """'up' / 'down' / 'flat' from the COMPOSITE's trailing `window`-trading-day return (days without index data are 'flat')."""
    series = [(d, index_close.get(d)) for d in days]
    out = []
    for i, (_d, c) in enumerate(series):
        past = series[i - window][1] if i >= window else None
        if c is None or past is None or past <= 0:
            out.append("flat")
            continue
        r = c / past - 1
        out.append("up" if r > band else "down" if r < -band else "flat")
    return out


def regime_edges(labels: list[str], sums: list[float], counts: list[float]) -> dict:
    acc = {k: [0.0, 0.0] for k in ("up", "flat", "down")}
    for lab, s, n in zip(labels, sums, counts):
        acc[lab][0] += s
        acc[lab][1] += n
    return {k: {"n": int(v[1]), "edge": (v[0] / v[1]) if v[1] >= MIN_FOLD_N else None} for k, v in acc.items()}


# ---------------------------------------------------------------------------------------
# K13: liquidity-aware round-trip cost
# ---------------------------------------------------------------------------------------

def tick_size(price: float) -> int:
    for upper, tick in ((200, 1), (500, 2), (2000, 5), (5000, 10)):
        if price < upper:
            return tick
    return 25


def round_trip_cost(price: float, value: float, atr: float | None, *, fee: float = FEE, kappa: float = KAPPA,
                    qty: float = QTY_IDR) -> float:
    """Commission + crossing the tick-size spread (one tick round trip) + square-root impact on each leg."""
    sigma = (atr / price) if (atr and price) else 0.03
    impact = 2 * kappa * sigma * math.sqrt(qty / max(value, 1.0))
    return min(COST_CAP, fee + tick_size(price) / price + impact)


# ---------------------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------------------

def strength(r: dict) -> str:
    """One plain label from the measured pieces; thresholds are written here so they can be argued with."""
    if r["n"] < 100 or r["days"] < 40:
        return "Too little data"
    net, neut, raw = r["edge_net"], r["edge_neut"], r["edge_raw"]
    q, pos = r.get("q_net"), r.get("folds_pos")
    if net["lo"] is not None and net["lo"] > 0 and q is not None and q < 0.10 and (pos or 0) >= 0.7:
        return "Candidate: survives costs, factors and time"
    if neut["lo"] is not None and neut["lo"] > 0:
        return "Real but too small to pay the costs"
    if raw["lo"] is not None and raw["lo"] > 0:
        return "Mostly a size or liquidity effect"
    return "No evidence of an edge"


def analyze(days: list[date], per_tag: dict, index_close: dict[date, float], *, block: int = BLOCK, reps: int = REPS) -> dict:
    """per_tag: {tag: {"n": [...], "raw": [...], "neut": [...], "cost": [...]}} aligned with `days`. Returns {tag: result}."""
    reg = regime_labels(days, index_close)
    out: dict[str, dict] = {}
    for tag, a in per_tag.items():
        n = a["n"]
        if sum(n) <= 0:
            continue
        net = [s - c for s, c in zip(a["neut"], a["cost"])]
        total_n = sum(n)
        r = {
            "n": int(total_n), "days": sum(1 for x in n if x > 0), "eff_blocks": round(effective_blocks(n, block), 1),
            "mean_cost": sum(a["cost"]) / total_n,
            "edge_raw": interval(a["raw"], n, block=block, reps=reps),
            "edge_neut": interval(a["neut"], n, block=block, reps=reps),
            "edge_net": interval(net, n, block=block, reps=reps),
            "folds": fold_edges(days, net, n),
            "regimes": regime_edges(reg, net, n),
        }
        r["p_net"] = r["edge_net"]["p"]
        valid = [f for f in r["folds"] if f["edge"] is not None]
        r["folds_pos"] = (sum(1 for f in valid if f["edge"] > 0) / len(valid)) if valid else None
        out[tag] = r
    tags = list(out)
    for tag, q in zip(tags, bh_qvalues([out[t]["p_net"] for t in tags])):
        out[tag]["q_net"] = q
    for r in out.values():
        r["strength"] = strength(r)
    return out


# ---------------------------------------------------------------------------------------
# DB part
# ---------------------------------------------------------------------------------------

# Since 2026-11-07 both sides are OUTCOME V2 (outcomes2.py): entry at the VWAP of t+1, total return from IDX's own reference
# prices + cash dividends. The raw edge reads the tag rows (engine.EVAL_SOURCE: the same rows as the Track record) against
# market_breadth2; the size x liquidity neutral edge reads every traded stock-day's v2 return from ret2_daily, so a signal and
# its bucket are measured with one formula. (Before: raw used v1, and the buckets used close-to-close split-only returns, which
# turned rights issues into -90% "returns".)
def _sql(source: str | None = None) -> str:
    """`source`: a subquery with columns session_date, ticker, tag, ret2_h5 (default: the tag rows, engine.EVAL_SOURCE). Always a
    constant from code (pattern_research.SOURCE), never user input."""
    from .engine import EVAL_SOURCE

    src = source or EVAL_SOURCE
    return f"""
WITH bk AS (
  SELECT r.session_date, r.ticker, r.r5,
         ntile(5) OVER (PARTITION BY r.session_date ORDER BY p.close * p.listed_shares) AS sz,
         ntile(5) OVER (PARTITION BY r.session_date ORDER BY p.value) AS lq
  FROM ret2_daily r JOIN price_bars p USING (session_date, ticker)
  WHERE r.r5 IS NOT NULL AND p.value > 0 AND p.listed_shares > 0
), bm AS (
  SELECT session_date, sz, lq, avg(r5) AS m5 FROM bk GROUP BY 1, 2, 3
)
SELECT s.tag, s.session_date, (p.value >= %(liq)s) AS liq, count(*) AS n,
       sum(s.ret2_h5 - mb.ret2_h5_all) AS s_raw,
       sum(bk.r5 - bm.m5) AS s_neut,
       sum(LEAST(%(cap)s, %(fee)s
             + (CASE WHEN p.close < 200 THEN 1 WHEN p.close < 500 THEN 2 WHEN p.close < 2000 THEN 5 WHEN p.close < 5000 THEN 10 ELSE 25 END) / p.close
             + 2 * %(kappa)s * COALESCE(sc.atr14 / p.close, 0.03) * sqrt(%(qty)s / GREATEST(p.value, 1)))) AS cost
FROM {src} s
JOIN market_breadth2 mb ON mb.session_date = s.session_date
JOIN price_bars p ON p.session_date = s.session_date AND p.ticker = s.ticker
JOIN bk ON bk.session_date = s.session_date AND bk.ticker = s.ticker
JOIN bm ON bm.session_date = bk.session_date AND bm.sz = bk.sz AND bm.lq = bk.lq
LEFT JOIN signal_components sc ON sc.session_date = s.session_date AND sc.ticker = s.ticker
WHERE s.ret2_h5 IS NOT NULL
GROUP BY 1, 2, 3
"""


def load_series(source: str | None = None):
    """(calendar, rows, index_close). rows: [(tag, day, liquid, n, s_raw, s_neut, cost)]. `source`: see _sql."""
    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT session_date FROM market_breadth2 ORDER BY session_date")
        calendar = [r[0] for r in cur.fetchall()]
        cur.execute(_sql(source), {"liq": LIQUID_VALUE_IDR, "cap": COST_CAP, "fee": FEE, "kappa": KAPPA, "qty": QTY_IDR})
        rows = [(t, d, bool(l), int(n), float(a), float(b), float(c)) for t, d, l, n, a, b, c in cur.fetchall()]
        cur.execute("SELECT session_date, close FROM index_bars WHERE index_code = 'COMPOSITE' ORDER BY session_date")
        index_close = {d: float(c) for d, c in cur.fetchall() if c}
    return calendar, rows, index_close


def arrays(calendar: list[date], rows: list[tuple], *, scope: str, since: date | None = None) -> tuple[list[date], dict]:
    """Per-tag arrays aligned with the calendar (restricted to days >= since). scope: 'all' or 'liquid'."""
    days = [d for d in calendar if since is None or d >= since]
    pos = {d: i for i, d in enumerate(days)}
    per: dict[str, dict] = {}
    for tag, day, liq, n, raw, neut, cost in rows:
        if day not in pos or (scope == "liquid" and not liq):
            continue
        a = per.setdefault(tag, {k: [0.0] * len(days) for k in ("n", "raw", "neut", "cost")})
        i = pos[day]
        a["n"][i] += n
        a["raw"][i] += raw
        a["neut"][i] += neut
        a["cost"][i] += cost
    return days, per


def run(forward_since: date | None, *, reps: int = REPS, source: str | None = None) -> dict:
    calendar, rows, index_close = load_series(source)
    result: dict = {"window": [calendar[0].isoformat(), calendar[-1].isoformat()] if calendar else None,
                    "forward_since": forward_since.isoformat() if forward_since else None,
                    "params": {"block": BLOCK, "reps": reps, "embargo": EMBARGO, "fold": "half-year", "regime_window": REGIME_WINDOW,
                               "regime_band": REGIME_BAND, "fee": FEE, "kappa": KAPPA, "qty_idr": QTY_IDR, "alpha": ALPHA},
                    "results": {}}
    for scope in ("all", "liquid"):
        for window, since in (("full", None), ("forward", forward_since)):
            if window == "forward" and since is None:
                continue
            days, per = arrays(calendar, rows, scope=scope, since=since)
            res = analyze(days, per, index_close, reps=reps)
            result["results"].setdefault(scope, {})[window] = res
    return result


SNAPSHOT_NAME = "research_v1"


def save_snapshot(payload: dict) -> None:
    import json

    from .db import connect

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


def format_text(result: dict, scope: str = "all", window: str = "full") -> str:
    res = result["results"].get(scope, {}).get(window, {})
    lines = [f"window {result['window']}  scope={scope}  sample={window}  (retrospective; neutral = size x liquidity matched; costs modelled per signal)",
             f"{'pattern':26} {'n':>7} {'days':>5} {'raw edge [95% CI]':>24} {'neutral [95% CI]':>24} {'net of cost [CI]':>24} {'q':>6} {'folds+':>6}  verdict"]

    def cell(e):
        if e["point"] is None or e["lo"] is None:
            return "n/a"
        return f"{e['point'] * 100:+.2f} [{e['lo'] * 100:+.2f},{e['hi'] * 100:+.2f}]"

    for tag, r in sorted(res.items(), key=lambda kv: -kv[1]["n"]):
        q = "-" if r.get("q_net") is None else f"{r['q_net']:.2f}"
        fp = "-" if r.get("folds_pos") is None else f"{r['folds_pos'] * 100:.0f}%"
        lines.append(f"{tag:26} {r['n']:7d} {r['days']:5d} {cell(r['edge_raw']):>24} {cell(r['edge_neut']):>24} {cell(r['edge_net']):>24} {q:>6} {fp:>6}  {r['strength']}")
    return "\n".join(lines)
