"""Methodology v0 — RENCANA-FASE-1 §5-6, PRINSIP-DATA §C.

Everything here is a PROBATION signal (weight 0). The point of Phase 1 is not the
tags but the forward-test evidence: compute_day() logs tags, backfill_outcomes()
fills their H+1/3/5/10 returns, eval_tags() reports whether any tag actually
precedes a move.

Design choices:
- NFF (net foreign flow) is computed in IDR = foreign_net_shares * VWAP. This is
  split-robust (share count x10, VWAP /10 cancel), so the rolling sums need no
  corporate-action adjustment.
- Price returns / RS DO need adjustment: split-adjusted closes via
  corp_actions(type='share_count_change') rows whose note starts 'split:'.
- Foreign buy/sell coverage (all-boards vs regular-only) is UNVERIFIED
  (PRINSIP-DATA §A). High negotiated-board days are flagged CROSSING-TINGGI
  rather than netted out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import psycopg

from .db import connect, upsert_many

# -- tunables (all guesses — the forward test decides) -----------------------
PRICE_FLAT_BAND = 0.02          # |5d return| < 2% counts as "sideways"
CONSISTENCY_ACC = 6            # >= 6 of last 10 days foreign net-buy -> accumulation
CONSISTENCY_DIST = 3          # <= 3 of last 10 -> distribution
NONREG_HIGH = 0.20            # negotiated value / total value > 20% -> CROSSING-TINGGI
KSEI_TREND_BAND = 0.005      # +/- 0.5pp month-over-3-months
_LOOKBACK_TD = 26            # trading days of history to load per ticker
_FWD_HORIZONS = (1, 3, 5, 10)


@dataclass(slots=True)
class _Bar:
    d: date
    close: float | None
    prev: float | None
    volume: float | None
    value: float | None
    fbuy: float | None
    fsell: float | None
    nonreg_value: float | None

    @property
    def nff_idr(self) -> float:
        if not self.volume or self.fbuy is None or self.fsell is None or self.value is None:
            return 0.0
        vwap = self.value / self.volume
        return (self.fbuy - self.fsell) * vwap

    @property
    def foreign_net_shares(self) -> float:
        if self.fbuy is None or self.fsell is None:
            return 0.0
        return self.fbuy - self.fsell


@dataclass(slots=True)
class Components:
    ticker: str
    close: float | None = None
    price_chg_5d: float | None = None
    nff_d0: float | None = None
    nff_w1: float | None = None
    nff_w2: float | None = None
    nff_w3: float | None = None
    nff_w4: float | None = None
    foreign_consistency_10d: int | None = None
    vol_surge: float | None = None
    rs_ihsg_5d: float | None = None
    rs_ihsg_10d: float | None = None
    rs_ihsg_20d: float | None = None
    nonreg_ratio: float | None = None
    ksei_foreign_trend_3m: str = "nodata"
    ksei_retail_trend_3m: str = "nodata"
    quadrant: str | None = None
    data_flags: list[str] = field(default_factory=list)


@dataclass
class ComputeSummary:
    session_date: date
    status: str
    tickers: int = 0
    signal_rows: int = 0
    detail: str = ""


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------

def _calendar(cur: psycopg.Cursor, session_date: date, n: int) -> list[date]:
    cur.execute(
        "SELECT session_date FROM trading_calendar WHERE session_date <= %s "
        "ORDER BY session_date DESC LIMIT %s",
        (session_date, n),
    )
    return [r[0] for r in cur.fetchall()][::-1]  # oldest -> newest


def _ihsg_series(cur: psycopg.Cursor, cal: list[date]) -> dict[date, float]:
    cur.execute(
        "SELECT session_date, close FROM index_bars "
        "WHERE index_code = 'COMPOSITE' AND session_date = ANY(%s)",
        (cal,),
    )
    return {d: float(c) for d, c in cur.fetchall() if c is not None}


def _tickers_on(cur: psycopg.Cursor, session_date: date) -> list[str]:
    cur.execute(
        "SELECT ticker FROM price_bars WHERE session_date = %s ORDER BY ticker",
        (session_date,),
    )
    return [r[0] for r in cur.fetchall()]


def _bars(cur: psycopg.Cursor, ticker: str, cal: list[date]) -> dict[date, _Bar]:
    cur.execute(
        """
        SELECT session_date, close, prev, volume, value,
               foreign_buy_vol, foreign_sell_vol, nonreg_value
        FROM price_bars
        WHERE ticker = %s AND session_date = ANY(%s)
        """,
        (ticker, cal),
    )
    out: dict[date, _Bar] = {}
    for d, c, p, vol, val, fb, fs, nrv in cur.fetchall():
        f = float
        out[d] = _Bar(
            d=d,
            close=f(c) if c is not None else None,
            prev=f(p) if p is not None else None,
            volume=f(vol) if vol is not None else None,
            value=f(val) if val is not None else None,
            fbuy=f(fb) if fb is not None else None,
            fsell=f(fs) if fs is not None else None,
            nonreg_value=f(nrv) if nrv is not None else None,
        )
    return out


def _share_changes(
    cur: psycopg.Cursor, ticker: str, lo: date, hi: date
) -> tuple[list[tuple[date, float]], bool]:
    """Returns (splits, any_change): `splits` = (ex_date, ratio) for market-cap-
    conserving changes ('split:' note) in (lo, hi]; `any_change` = True if any
    share_count_change (incl. dilutions) fell in the window."""
    cur.execute(
        """
        SELECT ex_date, ratio, notes FROM corp_actions
        WHERE ticker = %s AND type = 'share_count_change'
          AND ex_date > %s AND ex_date <= %s
        """,
        (ticker, lo, hi),
    )
    splits, any_change = [], False
    for d, r, notes in cur.fetchall():
        any_change = True
        if r and (notes or "").startswith("split:"):
            splits.append((d, float(r)))
    return splits, any_change


def _split_factor(events: list[tuple[date, float]], lo: date, hi: date) -> float:
    factor = 1.0
    for ex, ratio in events:
        if lo < ex <= hi:
            factor *= ratio
    return factor


def _ksei_trend(cur: psycopg.Cursor, ticker: str, session_date: date) -> tuple[str, str]:
    cur.execute(
        """
        SELECT local_individual, local_institution, foreign_total
        FROM ownership_monthly
        WHERE ticker = %s AND month_end <= %s
        ORDER BY month_end DESC LIMIT 4
        """,
        (ticker, session_date),
    )
    rows = cur.fetchall()
    if len(rows) < 3:
        return "nodata", "nodata"
    latest, older = rows[0], rows[min(3, len(rows) - 1)]

    def pct(row, which):
        li, lin, ft = (float(x) if x is not None else 0.0 for x in row)
        tot = li + lin + ft
        if tot <= 0:
            return None
        return {"foreign": ft, "retail": li}[which] / tot

    def band(a, b):
        if a is None or b is None:
            return "nodata"
        d = a - b
        return "up" if d > KSEI_TREND_BAND else "down" if d < -KSEI_TREND_BAND else "flat"

    return (
        band(pct(latest, "foreign"), pct(older, "foreign")),
        band(pct(latest, "retail"), pct(older, "retail")),
    )


# ---------------------------------------------------------------------------
# component maths
# ---------------------------------------------------------------------------

def _components(
    ticker: str,
    cal: list[date],
    bars: dict[date, _Bar],
    ihsg: dict[date, float],
    splits: list[tuple[date, float]],
    share_change_in_window: bool = False,
) -> Components | None:
    d0 = cal[-1]
    b0 = bars.get(d0)
    if b0 is None or not b0.volume:  # not traded / suspended today -> skip
        return None

    c = Components(ticker=ticker, close=b0.close)
    c.nff_d0 = b0.nff_idr
    if share_change_in_window:
        c.data_flags.append("SHARE_COUNT_CHANGE")

    def back(k: int) -> _Bar | None:
        return bars.get(cal[-1 - k]) if len(cal) > k else None

    def wsum(a: int, z: int) -> float | None:
        got = [back(k) for k in range(a, z + 1)]
        if any(g is None for g in got):
            return None
        return sum(g.nff_idr for g in got)

    c.nff_w1, c.nff_w2, c.nff_w3, c.nff_w4 = wsum(1, 5), wsum(6, 10), wsum(11, 15), wsum(16, 20)

    last10 = [back(k) for k in range(0, 10)]
    if all(g is not None for g in last10):
        c.foreign_consistency_10d = sum(1 for g in last10 if g.foreign_net_shares > 0)

    vols = [g.volume for g in (back(k) for k in range(1, 21)) if g and g.volume]
    if len(vols) >= 10 and b0.volume:
        c.vol_surge = b0.volume / (sum(vols) / len(vols))

    if b0.value and b0.nonreg_value is not None:
        c.nonreg_ratio = b0.nonreg_value / b0.value
        if c.nonreg_ratio > NONREG_HIGH:
            c.data_flags.append("CROSSING-TINGGI")

    # split-adjusted returns
    def adj_ret(k: int) -> float | None:
        bk = back(k)
        if bk is None or bk.close is None or not b0.close:
            return None
        adj = bk.close / _split_factor(splits, bk.d, d0)
        return (b0.close / adj) - 1 if adj else None

    def ihsg_ret(k: int) -> float | None:
        dk = cal[-1 - k] if len(cal) > k else None
        if dk is None or dk not in ihsg or d0 not in ihsg or not ihsg[dk]:
            return None
        return (ihsg[d0] / ihsg[dk]) - 1

    c.price_chg_5d = adj_ret(5)
    for k, attr in ((5, "rs_ihsg_5d"), (10, "rs_ihsg_10d"), (20, "rs_ihsg_20d")):
        pr, ir = adj_ret(k), ihsg_ret(k)
        if pr is not None and ir is not None:
            setattr(c, attr, pr - ir)

    return c


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

def _classify(c: Components) -> list[str]:
    """Returns the list of tags that fired. Also sets c.quadrant and may append
    to c.data_flags. Each tag is forward-tested independently."""
    tags: list[str] = []

    if c.price_chg_5d is None or c.nff_w1 is None:
        return tags  # not enough history

    direction = (
        "up" if c.price_chg_5d > PRICE_FLAT_BAND
        else "down" if c.price_chg_5d < -PRICE_FLAT_BAND
        else "flat"
    )
    nff = "pos" if c.nff_w1 > 0 else "neg" if c.nff_w1 < 0 else "zero"

    c.quadrant = {
        ("up", "pos"): "MARKUP",
        ("up", "neg"): "DISTRIBUSI",
        ("flat", "pos"): "AKUMULASI",
        ("down", "pos"): "AKUMULASI",
        ("flat", "neg"): "MARKDOWN",
        ("down", "neg"): "MARKDOWN",
    }.get((direction, nff), "NETRAL")

    cons = c.foreign_consistency_10d
    if (
        direction in ("flat", "down")
        and c.nff_w1 > 0
        and (c.nff_w2 or 0) > 0
        and cons is not None
        and cons >= CONSISTENCY_ACC
    ):
        tags.append("AKUM-ASING")
    if (
        direction == "up"
        and c.nff_w1 < 0
        and cons is not None
        and cons <= CONSISTENCY_DIST
    ):
        tags.append("DISTRIBUSI-ASING")
    if direction == "up" and c.nff_w1 > 0:
        tags.append("MARKUP")
    if direction == "down" and c.nff_w1 < 0:
        tags.append("MARKDOWN")

    # KSEI monthly cross-validation (PRINSIP-DATA §C.7) — annotation tags
    if "AKUM-ASING" in tags:
        if c.ksei_foreign_trend_3m == "down":
            tags.append("KSEI-DIVERGEN")
            c.data_flags.append("KSEI-DIVERGEN")
        elif c.ksei_foreign_trend_3m == "up":
            tags.append("KSEI-KONFIRM")
    if "DISTRIBUSI-ASING" in tags:
        if c.ksei_foreign_trend_3m == "up":
            tags.append("KSEI-DIVERGEN")
            c.data_flags.append("KSEI-DIVERGEN")
        elif c.ksei_foreign_trend_3m == "down":
            tags.append("KSEI-KONFIRM")

    if "CROSSING-TINGGI" in c.data_flags:
        tags.append("CROSSING-TINGGI")

    return tags


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

_COMP_COLS = (
    "session_date", "ticker", "close", "price_chg_5d", "nff_d0", "nff_w1", "nff_w2",
    "nff_w3", "nff_w4", "foreign_consistency_10d", "vol_surge", "rs_ihsg_5d",
    "rs_ihsg_10d", "rs_ihsg_20d", "nonreg_ratio", "ksei_foreign_trend_3m",
    "ksei_retail_trend_3m", "quadrant", "data_flags",
)
_COMP_UPDATE = tuple(c for c in _COMP_COLS if c not in ("session_date", "ticker"))


def _comp_row(sd: date, c: Components) -> tuple:
    return (
        sd, c.ticker, c.close, c.price_chg_5d, c.nff_d0, c.nff_w1, c.nff_w2, c.nff_w3,
        c.nff_w4, c.foreign_consistency_10d, c.vol_surge, c.rs_ihsg_5d, c.rs_ihsg_10d,
        c.rs_ihsg_20d, c.nonreg_ratio, c.ksei_foreign_trend_3m, c.ksei_retail_trend_3m,
        c.quadrant, sorted(set(c.data_flags)),
    )


def compute_day(session_date: date) -> ComputeSummary:
    with connect() as conn, conn.cursor() as cur:
        cal = _calendar(cur, session_date, _LOOKBACK_TD)
        if not cal or cal[-1] != session_date:
            _log(cur, "compute", session_date, "no_data", 0,
                 "session_date not in trading_calendar (run collect-price first)")
            return ComputeSummary(session_date, "no_data",
                                  detail="not a confirmed trading day")

        ihsg = _ihsg_series(cur, cal)
        tickers = _tickers_on(cur, session_date)

        comp_rows: list[tuple] = []
        log_rows: list[tuple] = []
        for t in tickers:
            bars = _bars(cur, t, cal)
            splits, changed = _share_changes(cur, t, cal[0], session_date)
            c = _components(t, cal, bars, ihsg, splits, changed)
            if c is None:
                continue
            c.ksei_foreign_trend_3m, c.ksei_retail_trend_3m = _ksei_trend(cur, t, session_date)
            tags = _classify(c)
            comp_rows.append(_comp_row(session_date, c))
            for tag in tags:
                log_rows.append((session_date, t, tag, "probation", c.close))

        n_comp = upsert_many(
            cur, "signal_components", _COMP_COLS, comp_rows,
            conflict=("session_date", "ticker"), update=_COMP_UPDATE,
        )
        cur.execute(
            "UPDATE signal_components SET computed_at = now() WHERE session_date = %s",
            (session_date,),
        )
        # re-compute cleanly: drop this day's un-resolved probation tags, re-insert
        cur.execute(
            "DELETE FROM signal_log WHERE session_date = %s AND layer = 'probation' "
            "AND ret_h5 IS NULL",
            (session_date,),
        )
        upsert_many(
            cur, "signal_log", ("session_date", "ticker", "tag", "layer", "close"),
            log_rows, conflict=("session_date", "ticker", "tag"),
            update=("close",),
        )
        _log(cur, "compute", session_date, "ok", n_comp,
             f"{len(tickers)} tickers, {len(log_rows)} tags")

    return ComputeSummary(session_date, "ok", len(tickers), len(log_rows))


def _log(cur, phase, sd, status, rows, err):
    cur.execute(
        "INSERT INTO run_log (phase, batch_date, status, rows_affected, error, finished_at) "
        "VALUES (%s, %s, %s, %s, %s, now())",
        (phase, sd, status, rows, err),
    )


# ---------------------------------------------------------------------------
# forward-test outcome fill
# ---------------------------------------------------------------------------

def backfill_outcomes(as_of: date | None = None) -> int:
    """Fill ret_h1/h3/h5/h10 + ret_vs_ihsg_h5 for signal_log rows old enough that
    the forward window has closed. Idempotent, safe to run every EOD."""
    updated = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT session_date FROM trading_calendar ORDER BY session_date")
        cal = [r[0] for r in cur.fetchall()]
        pos = {d: i for i, d in enumerate(cal)}
        if as_of is None:
            as_of = cal[-1] if cal else None
        if as_of is None:
            return 0

        cur.execute(
            "SELECT DISTINCT session_date FROM signal_log WHERE ret_h5 IS NULL ORDER BY session_date"
        )
        pending_dates = [r[0] for r in cur.fetchall()]

        for sd in pending_dates:
            i = pos.get(sd)
            if i is None or i + max(_FWD_HORIZONS) >= len(cal):
                continue  # forward window not fully available yet
            fwd = {h: cal[i + h] for h in _FWD_HORIZONS}

            cur.execute(
                "SELECT ticker, tag, close FROM signal_log "
                "WHERE session_date = %s AND ret_h5 IS NULL",
                (sd,),
            )
            rows = cur.fetchall()
            tickers = tuple({r[0] for r in rows})
            if not tickers:
                continue

            # split-adjusted forward close per ticker/horizon
            cur.execute(
                """
                SELECT ticker, session_date, close FROM price_bars
                WHERE ticker = ANY(%s) AND session_date = ANY(%s)
                """,
                (list(tickers), list(fwd.values()) + [sd]),
            )
            px: dict[tuple[str, date], float] = {
                (t, d): float(c) for t, d, c in cur.fetchall() if c is not None
            }
            cur.execute(
                """
                SELECT ticker, ex_date, ratio FROM corp_actions
                WHERE ticker = ANY(%s) AND type = 'share_count_change'
                  AND notes LIKE 'split:%%' AND ex_date > %s AND ex_date <= %s
                """,
                (list(tickers), sd, fwd[max(_FWD_HORIZONS)]),
            )
            sp: dict[str, list[tuple[date, float]]] = {}
            for t, ex, r in cur.fetchall():
                if r:
                    sp.setdefault(t, []).append((ex, float(r)))

            ih = _ihsg_series(cur, [sd, fwd[5]])

            for ticker, tag, close in rows:
                c0 = px.get((ticker, sd)) or (float(close) if close is not None else None)
                if not c0:
                    continue
                vals: dict[str, float | None] = {}
                for h in _FWD_HORIZONS:
                    ck = px.get((ticker, fwd[h]))
                    if ck is None:
                        vals[f"ret_h{h}"] = None
                        continue
                    factor = _split_factor(sp.get(ticker, []), sd, fwd[h])
                    vals[f"ret_h{h}"] = (ck * factor) / c0 - 1
                rv = None
                if vals.get("ret_h5") is not None and sd in ih and fwd[5] in ih and ih[sd]:
                    rv = vals["ret_h5"] - ((ih[fwd[5]] / ih[sd]) - 1)

                cur.execute(
                    """
                    UPDATE signal_log SET ret_h1=%s, ret_h3=%s, ret_h5=%s, ret_h10=%s,
                                          ret_vs_ihsg_h5=%s
                    WHERE session_date=%s AND ticker=%s AND tag=%s
                    """,
                    (vals["ret_h1"], vals["ret_h3"], vals["ret_h5"], vals["ret_h10"],
                     rv, sd, ticker, tag),
                )
                updated += 1

        _log(cur, "backfill_outcomes", as_of, "ok", updated, None)
    return updated


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def eval_tags() -> list[dict]:
    """Per-tag forward-return stats over all resolved signal_log rows."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT tag,
                   count(*)                                        AS n,
                   avg(ret_h5)                                     AS avg_h5,
                   avg(ret_vs_ihsg_h5)                             AS avg_vs_ihsg_h5,
                   avg((ret_h5 > 0)::int)::float                   AS hit_rate,
                   avg(ret_h1) AS avg_h1, avg(ret_h10) AS avg_h10
            FROM signal_log
            WHERE ret_h5 IS NOT NULL
            GROUP BY tag
            ORDER BY n DESC
            """
        )
        cols = [d[0] for d in cur.description]
        return [
            {k: (float(v) if isinstance(v, Decimal) else v) for k, v in zip(cols, r)}
            for r in cur.fetchall()
        ]
