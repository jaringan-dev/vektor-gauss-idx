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
LIQUID_VALUE_IDR = 5_000_000_000   # >Rp5B traded that day => "liquid" for eval breakdown

# Phase 1.25 tunables (guesses — the forward test decides)
TICKET_Z_HIGH = 1.5          # avg-ticket z >= this => unusually large trades (institutional footprint)
TICKET_Z_LOW = -1.0          # avg-ticket z <= this => unusually small trades (retail churn)
FLOAT_ACCUM_BAND = 0.01      # 5d foreign net shares / free float >= this => meaningful foreign take
_MIN_FREQ_FOR_TICKET = 20    # skip avg-ticket z below this many trades (too noisy)

# Disclosure-filing activity (disclosure_filings). A weight-0 proxy: it has no
# direction (that's in the PDF — Phase B). The forward test decides if it matters.
DISCLOSURE_MIN_5D = 3         # absolute filing cluster in the trailing 5 td
DISCLOSURE_SURGE_MULT = 2.0   # ... AND >= this x the trailing-20 td rate (per 5 td)


@dataclass(slots=True)
class _Bar:
    d: date
    close: float | None
    prev: float | None
    high: float | None
    low: float | None
    volume: float | None
    value: float | None
    fbuy: float | None
    fsell: float | None
    nonreg_value: float | None
    frequency: float | None = None
    bid_volume: float | None = None
    offer_volume: float | None = None
    tradable_shares: float | None = None
    listed_shares: float | None = None

    @property
    def avg_ticket(self) -> float | None:
        """Rupiah per trade — the institutional-footprint proxy."""
        if not self.value or not self.frequency:
            return None
        return self.value / self.frequency

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
    atr14: float | None = None
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
    avg_ticket: float | None = None          # value / frequency today (IDR/trade)
    avg_ticket_z: float | None = None        # z vs own trailing ~20d
    bid_offer_imb: float | None = None       # (bidvol - offervol) / (bidvol + offervol) at close
    float_ratio: float | None = None         # tradable_shares / listed_shares
    foreign_float_5d: float | None = None    # 5d foreign net shares / tradable_shares
    disclosure_5d: int | None = None         # ownership-disclosure filings, trailing 5 td
    disclosure_20d: int | None = None        # ... trailing 20 td (baseline)
    ksei_foreign_trend_3m: str = "nodata"
    ksei_retail_trend_3m: str = "nodata"
    ksei_foreign_pct: float | None = None      # level, transient (not persisted)
    ksei_inst_pct: float | None = None
    ksei_indiv_pct: float | None = None
    ksei_foreign_d3m: float | None = None      # 3-month change (fraction), persisted
    ksei_inst_d3m: float | None = None
    ksei_indiv_d3m: float | None = None
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


def _disclosure_counts(
    cur: psycopg.Cursor, session_date: date, cal: list[date]
) -> dict[str, tuple[int, int]]:
    """Per-ticker ownership-disclosure filing counts in the trailing 5 / 20 trading-day
    windows (by calendar range, so weekend/holiday filings aren't dropped)."""
    lo5 = cal[-5] if len(cal) >= 5 else cal[0]
    lo20 = cal[-20] if len(cal) >= 20 else cal[0]
    cur.execute(
        """
        SELECT ticker,
               count(*) FILTER (WHERE filed_date >= %s),
               count(*)
        FROM disclosure_filings
        WHERE filed_date >= %s AND filed_date <= %s
        GROUP BY ticker
        """,
        (lo5, lo20, session_date),
    )
    return {t: (c5, c20) for t, c5, c20 in cur.fetchall()}


def _tickers_on(cur: psycopg.Cursor, session_date: date) -> list[str]:
    cur.execute(
        "SELECT ticker FROM price_bars WHERE session_date = %s ORDER BY ticker",
        (session_date,),
    )
    return [r[0] for r in cur.fetchall()]


def _bars(cur: psycopg.Cursor, ticker: str, cal: list[date]) -> dict[date, _Bar]:
    cur.execute(
        """
        SELECT session_date, close, prev, high, low, volume, value,
               foreign_buy_vol, foreign_sell_vol, nonreg_value,
               frequency, bid_volume, offer_volume, tradable_shares, listed_shares
        FROM price_bars
        WHERE ticker = %s AND session_date = ANY(%s)
        """,
        (ticker, cal),
    )
    out: dict[date, _Bar] = {}
    for d, c, p, hi, lo, vol, val, fb, fs, nrv, freq, bv, ov, tsh, lsh in cur.fetchall():
        def f(x):
            return float(x) if x is not None else None

        out[d] = _Bar(
            d=d, close=f(c), prev=f(p), high=f(hi), low=f(lo), volume=f(vol),
            value=f(val), fbuy=f(fb), fsell=f(fs), nonreg_value=f(nrv),
            frequency=f(freq), bid_volume=f(bv), offer_volume=f(ov),
            tradable_shares=f(tsh), listed_shares=f(lsh),
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


@dataclass(slots=True)
class _KseiSnap:
    foreign_pct: float | None = None
    inst_pct: float | None = None
    indiv_pct: float | None = None
    foreign_d3m: float | None = None
    inst_d3m: float | None = None
    indiv_d3m: float | None = None
    foreign_trend: str = "nodata"
    retail_trend: str = "nodata"


def _ksei_shares(detail, li, lin, ft) -> tuple[float, float, float] | None:
    """(%foreign, %local_institution, %local_individual) of the scripless base.
    Prefers the 18-way `detail` jsonb; falls back to the 3 aggregate columns."""
    try:
        loc, fgn = detail["local"], detail["foreign"]
        loc_tot, fgn_tot = float(loc["TOTAL"]), float(fgn["TOTAL"])
        indiv = float(loc.get("ID", 0.0))
    except (TypeError, KeyError, ValueError):
        loc_tot = float(lin or 0.0) + float(li or 0.0)
        fgn_tot = float(ft or 0.0)
        indiv = float(li or 0.0)
    base = loc_tot + fgn_tot
    if base <= 0:
        return None
    return fgn_tot / base, (loc_tot - indiv) / base, indiv / base


def _ksei_metrics(cur: psycopg.Cursor, ticker: str, session_date: date) -> _KseiSnap:
    cur.execute(
        """
        SELECT detail, local_individual, local_institution, foreign_total
        FROM ownership_monthly
        WHERE ticker = %s AND month_end <= %s
        ORDER BY month_end DESC LIMIT 4
        """,
        (ticker, session_date),
    )
    rows = cur.fetchall()
    if len(rows) < 3:
        return _KseiSnap()
    now = _ksei_shares(*rows[0])
    then = _ksei_shares(*rows[min(3, len(rows) - 1)])
    if now is None or then is None:
        return _KseiSnap()

    def band(d: float) -> str:
        return "up" if d > KSEI_TREND_BAND else "down" if d < -KSEI_TREND_BAND else "flat"

    fd, id_, dd = now[0] - then[0], now[1] - then[1], now[2] - then[2]
    return _KseiSnap(
        foreign_pct=now[0], inst_pct=now[1], indiv_pct=now[2],
        foreign_d3m=fd, inst_d3m=id_, indiv_d3m=dd,
        foreign_trend=band(fd), retail_trend=band(dd),
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

    # ATR(14) — for the mechanical stop-loss / target in the digest
    trs: list[float] = []
    for k in range(0, 14):
        bk = back(k)
        if bk is None or bk.high is None or bk.low is None:
            break
        pc = (bk_next.close if (bk_next := back(k + 1)) else None) or bk.prev or bk.close
        if pc is None:
            break
        trs.append(max(bk.high - bk.low, abs(bk.high - pc), abs(bk.low - pc)))
    if len(trs) >= 10 and "SHARE_COUNT_CHANGE" not in c.data_flags:
        c.atr14 = sum(trs) / len(trs)  # skip if a split distorts the recent range

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

    # Phase 1.25 — signals from price_bars fields not used above.
    c.avg_ticket = b0.avg_ticket
    if b0.frequency and b0.frequency >= _MIN_FREQ_FOR_TICKET:
        hist = [
            bk.avg_ticket
            for bk in (back(k) for k in range(1, 21))
            if bk and bk.frequency and bk.frequency >= _MIN_FREQ_FOR_TICKET
        ]
        hist = [t for t in hist if t is not None]
        if len(hist) >= 10 and c.avg_ticket is not None:
            mean = sum(hist) / len(hist)
            var = sum((t - mean) ** 2 for t in hist) / len(hist)
            sd = var**0.5
            if sd > 0:
                c.avg_ticket_z = (c.avg_ticket - mean) / sd

    if b0.bid_volume is not None and b0.offer_volume is not None:
        denom = b0.bid_volume + b0.offer_volume
        if denom > 0:
            c.bid_offer_imb = (b0.bid_volume - b0.offer_volume) / denom

    if b0.tradable_shares and b0.listed_shares and b0.listed_shares > 0:
        c.float_ratio = b0.tradable_shares / b0.listed_shares

    if b0.tradable_shares and b0.tradable_shares > 0:
        five = [back(k) for k in range(5)]
        if all(g is not None for g in five):
            c.foreign_float_5d = sum(g.foreign_net_shares for g in five) / b0.tradable_shares

    return c


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

def _classify(c: Components) -> list[str]:
    """Returns the list of tags that fired. Also sets c.quadrant and may append
    to c.data_flags. Each tag is forward-tested independently."""
    tags: list[str] = []

    # Signals that do NOT need price direction / weekly NFF — must fire even for a
    # ticker with < 5 price bars, so they go above the early-return below.
    if c.ksei_inst_d3m is not None and c.ksei_inst_d3m > KSEI_TREND_BAND:
        tags.append("KSEI-INST-AKUM")          # local institution % rising 3m
    if c.ksei_indiv_d3m is not None and c.ksei_indiv_d3m < -KSEI_TREND_BAND:
        tags.append("KSEI-RITEL-EXIT")         # local individual % falling 3m
    if c.foreign_float_5d is not None and c.foreign_float_5d > FLOAT_ACCUM_BAND:
        tags.append("FLOAT-ASING-PADAT")       # foreign net-took a slice of a thin float

    # Unusual ownership-disclosure activity (>5% / insider filings). No direction —
    # weight-0 proxy pending the PDF parse (Phase B).
    if (c.disclosure_5d or 0) >= DISCLOSURE_MIN_5D:
        rate_5td = (c.disclosure_20d or c.disclosure_5d) / 4.0   # trailing rate per 5 td
        if c.disclosure_5d >= DISCLOSURE_SURGE_MULT * rate_5td:
            tags.append("LAPOR-KEPEMILIKAN-RAMAI")

    if c.price_chg_5d is None or c.nff_w1 is None:
        return tags  # not enough price history for the rest

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

    # Phase 1.25 — average ticket size (value / trade count) as an institutional-
    # footprint proxy. Big/rising ticket into weakness vs small ticket chasing.
    if c.avg_ticket_z is not None:
        if c.avg_ticket_z > TICKET_Z_HIGH and direction in ("flat", "down"):
            tags.append("TIKET-BESAR-AKUM")
        if c.avg_ticket_z < TICKET_Z_LOW and direction == "up":
            tags.append("TIKET-KECIL-MARKUP")

    return tags


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

_COMP_COLS = (
    "session_date", "ticker", "close", "atr14", "price_chg_5d", "nff_d0", "nff_w1",
    "nff_w2", "nff_w3", "nff_w4", "foreign_consistency_10d", "vol_surge", "rs_ihsg_5d",
    "rs_ihsg_10d", "rs_ihsg_20d", "nonreg_ratio", "ksei_foreign_trend_3m",
    "ksei_retail_trend_3m", "ksei_foreign_d3m", "ksei_inst_d3m", "ksei_indiv_d3m",
    "avg_ticket", "avg_ticket_z", "bid_offer_imb", "float_ratio", "foreign_float_5d",
    "disclosure_5d", "disclosure_20d",
    "quadrant", "data_flags",
)
_COMP_UPDATE = tuple(c for c in _COMP_COLS if c not in ("session_date", "ticker"))


def _comp_row(sd: date, c: Components) -> tuple:
    return (
        sd, c.ticker, c.close, c.atr14, c.price_chg_5d, c.nff_d0, c.nff_w1, c.nff_w2,
        c.nff_w3, c.nff_w4, c.foreign_consistency_10d, c.vol_surge, c.rs_ihsg_5d,
        c.rs_ihsg_10d, c.rs_ihsg_20d, c.nonreg_ratio, c.ksei_foreign_trend_3m,
        c.ksei_retail_trend_3m, c.ksei_foreign_d3m, c.ksei_inst_d3m, c.ksei_indiv_d3m,
        c.avg_ticket, c.avg_ticket_z, c.bid_offer_imb, c.float_ratio, c.foreign_float_5d,
        c.disclosure_5d, c.disclosure_20d,
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
        disc = _disclosure_counts(cur, session_date, cal)
        tickers = _tickers_on(cur, session_date)

        comp_rows: list[tuple] = []
        log_rows: list[tuple] = []
        for t in tickers:
            bars = _bars(cur, t, cal)
            splits, changed = _share_changes(cur, t, cal[0], session_date)
            c = _components(t, cal, bars, ihsg, splits, changed)
            if c is None:
                continue
            snap = _ksei_metrics(cur, t, session_date)
            c.ksei_foreign_trend_3m, c.ksei_retail_trend_3m = snap.foreign_trend, snap.retail_trend
            c.ksei_foreign_pct, c.ksei_inst_pct, c.ksei_indiv_pct = (
                snap.foreign_pct, snap.inst_pct, snap.indiv_pct
            )
            c.ksei_foreign_d3m, c.ksei_inst_d3m, c.ksei_indiv_d3m = (
                snap.foreign_d3m, snap.inst_d3m, snap.indiv_d3m
            )
            c.disclosure_5d, c.disclosure_20d = disc.get(t, (0, 0))
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

        if len(cal) > max(_FWD_HORIZONS):
            _refresh_market_breadth(cur, cal[-1 - max(_FWD_HORIZONS)])
        _log(cur, "backfill_outcomes", as_of, "ok", updated, None)
    return updated


def _refresh_market_breadth(cur: psycopg.Cursor, cutoff: date) -> int:
    """Equal-weight universe forward returns per trading day, up to `cutoff` (the
    last day whose H+10 window has closed). Incremental — skips dates already
    stored. Raw closes: splits are ~0.02% of ticker-days, negligible in a
    cross-sectional mean. This is the benchmark eval_tags() measures edge against."""
    cur.execute(
        """
        INSERT INTO market_breadth (
            session_date, n_all, ret_h1_all, ret_h3_all, ret_h5_all, ret_h10_all,
            hit_h5_all, n_liquid, ret_h5_liquid, hit_h5_liquid
        )
        WITH cal AS (
            SELECT session_date, row_number() OVER (ORDER BY session_date) AS rn
            FROM trading_calendar
        ),
        px AS (
            SELECT p.ticker, p.close::float AS c, p.value::float AS val,
                   cal.rn, cal.session_date
            FROM price_bars p JOIN cal USING (session_date)
            WHERE p.close > 0 AND COALESCE(p.volume, 0) > 0
        ),
        fr AS (
            SELECT a.session_date, a.val,
                   b1.c  / a.c - 1 AS r1,
                   b3.c  / a.c - 1 AS r3,
                   b5.c  / a.c - 1 AS r5,
                   b10.c / a.c - 1 AS r10
            FROM px a
            LEFT JOIN px b1  ON b1.ticker  = a.ticker AND b1.rn  = a.rn + 1
            LEFT JOIN px b3  ON b3.ticker  = a.ticker AND b3.rn  = a.rn + 3
            LEFT JOIN px b5  ON b5.ticker  = a.ticker AND b5.rn  = a.rn + 5
            LEFT JOIN px b10 ON b10.ticker = a.ticker AND b10.rn = a.rn + 10
            WHERE a.session_date <= %(cutoff)s
              AND a.session_date NOT IN (SELECT session_date FROM market_breadth)
        )
        SELECT session_date,
               count(*) FILTER (WHERE r5 IS NOT NULL),
               avg(r1), avg(r3), avg(r5), avg(r10),
               avg((r5 > 0)::int)::float,
               count(*) FILTER (WHERE r5 IS NOT NULL AND val > %(liq)s),
               avg(r5) FILTER (WHERE val > %(liq)s),
               (avg((r5 > 0)::int) FILTER (WHERE val > %(liq)s))::float
        FROM fr
        GROUP BY session_date
        ON CONFLICT (session_date) DO NOTHING
        """,
        {"cutoff": cutoff, "liq": LIQUID_VALUE_IDR},
    )
    return cur.rowcount


# ---------------------------------------------------------------------------
# ownership history (display)
# ---------------------------------------------------------------------------

def ownership_history(ticker: str, months: int = 12) -> list[dict]:
    """KSEI monthly ownership for one ticker, newest first — %foreign / %local
    institution / %local individual of the scripless base, + scripless % and
    holder count when present."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT month_end, detail, local_individual, local_institution,
                   foreign_total, holders_count
            FROM ownership_monthly WHERE ticker = %s
            ORDER BY month_end DESC LIMIT %s
            """,
            (ticker, months),
        )
        out: list[dict] = []
        for me, detail, li, lin, ft, hc in cur.fetchall():
            sh = _ksei_shares(detail, li, lin, ft)
            try:
                sp = float(detail["scripless_pct"]) if detail and "scripless_pct" in detail else None
            except (TypeError, ValueError):
                sp = None
            out.append({
                "month": me,
                "foreign_pct": sh[0] if sh else None,
                "inst_pct": sh[1] if sh else None,
                "indiv_pct": sh[2] if sh else None,
                "scripless_pct": sp,
                "holders": float(hc) if hc is not None else None,
            })
    return out


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def eval_tags(since: date | None = None) -> list[dict]:
    """Per-tag forward returns vs the equal-weight universe, date-matched.

    `edge_h5` is the number that matters: avg(tag ret_h5 - universe ret_h5 on the
    same day). IHSG is large-cap-weighted and not the tradeable universe, so
    comparing to it flatters everything. `*_liquid` restricts to signals on names
    that traded > LIQUID_VALUE_IDR that day, vs the liquid-universe benchmark.
    Needs market_breadth populated (run `vg backfill-outcomes`).

    `since` restricts to signals emitted on/after that date — the forward-test
    window (VG_FORWARD_SINCE). Without it the whole history is pooled (in-sample).
    """
    params: dict = {"liq": LIQUID_VALUE_IDR}
    since_clause = ""
    if since is not None:
        since_clause = " AND s.session_date >= %(since)s"
        params["since"] = since
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT s.tag,
                   count(*)                                       AS n,
                   avg(s.ret_h5)                                  AS avg_h5,
                   avg(s.ret_h1  - m.ret_h1_all)                  AS edge_h1,
                   avg(s.ret_h5  - m.ret_h5_all)                  AS edge_h5,
                   avg(s.ret_h10 - m.ret_h10_all)                 AS edge_h10,
                   avg((s.ret_h5 > 0)::int)::float                AS hit_rate,
                   avg(m.hit_h5_all)                              AS univ_hit_rate,
                   count(*) FILTER (WHERE p.value > %(liq)s)      AS n_liquid,
                   avg(s.ret_h5 - m.ret_h5_liquid)
                       FILTER (WHERE p.value > %(liq)s)           AS edge_h5_liquid,
                   (avg((s.ret_h5 > 0)::int)
                       FILTER (WHERE p.value > %(liq)s))::float   AS hit_rate_liquid
            FROM signal_log s
            JOIN market_breadth m USING (session_date)
            JOIN price_bars p ON p.session_date = s.session_date AND p.ticker = s.ticker
            WHERE s.ret_h5 IS NOT NULL{since_clause}
            GROUP BY s.tag
            ORDER BY n DESC
            """,
            params,
        )
        cols = [d[0] for d in cur.description]
        return [
            {k: (float(v) if isinstance(v, Decimal) else v) for k, v in zip(cols, r)}
            for r in cur.fetchall()
        ]
