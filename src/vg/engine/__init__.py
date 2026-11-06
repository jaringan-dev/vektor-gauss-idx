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

from ..broker_bounds import cohort_net_bounds
from ..db import connect, upsert_many
from ..sources.vektorgauss import FOREIGN_BROKERS, is_retail
from .scoring import TickerScore, score_ticker

# -- tunables (all guesses — the forward test decides) -----------------------
PRICE_FLAT_BAND = 0.02          # |5d return| < 2% counts as "sideways"
CONSISTENCY_ACC = 6            # >= 6 of last 10 days foreign net-buy -> accumulation
CONSISTENCY_DIST = 3          # <= 3 of last 10 -> distribution
NONREG_HIGH = 0.20            # negotiated value / total value > 20% -> CROSSING-TINGGI
KSEI_TREND_BAND = 0.005      # +/- 0.5pp month-over-3-months
# KSEI2 (audit 2026-11-07, B5/B8): a month-end file counts only once it was available (stored before the next open, or, for
# months loaded later in a backfill, KSEI_PUBLISH_LAG_DAYS after month end), and no delta is taken across months where the
# scripless share of all shares moved by SCRIPLESS_JUMP or more (old shares deposited into KSEI, not trading).
KSEI_PUBLISH_LAG_DAYS = 7
SCRIPLESS_JUMP = 0.05
# FLOAT2 (B3): real free float from the issuers' monthly registry reports (freefloat.py). A report older than this is stale.
FREE_FLOAT_MAX_AGE_DAYS = 75
REF_JUMP = 0.02               # an IDX reference price this far from the previous close = a corporate action (outcomes2.REF_JUMP)
_LOOKBACK_TD = 26            # trading days of history to load per ticker
_FWD_HORIZONS = (1, 3, 5, 10)
LIQUID_VALUE_IDR = 5_000_000_000   # >Rp5B traded that day => "liquid" for eval breakdown

# B13 (audit 2026-11-07): from this session on, the evaluation counts a tag only as FIRST computed (signal_snapshot) and only
# if that happened before 08:00 WIB of the next calendar day. Earlier sessions can only be read from signal_log, whose
# unresolved rows were rewritten by the nightly recompute; their first-seen time is lost, and the pages say so.
FREEZE_SINCE = date(2026, 11, 9)
# The v2 tags (audit 2026-11-07) start a NEW forward clock: `compute` emits them only for sessions on/after V2_TAGS_SINCE, so a
# nightly recompute of the days before cannot slip them into the forward window with data that arrived later. Their history
# before VG_FORWARD_SINCE comes only from `vg backfill-tags-v2` (in-sample); the sessions in between stay empty on purpose.
V2_TAGS = frozenset({"KSEI2-INST-AKUM", "KSEI2-RITEL-EXIT", "KSEI2-KONFIRM", "KSEI2-DIVERGEN", "FOREIGN-INST2", "DOM-RETAIL2",
                     "BROKER2-DOMISILI-KONFIRM", "BROKER2-DOMISILI-DIVERGEN", "FLOAT2-ASING-PADAT"})
V2_TAGS_SINCE = FREEZE_SINCE
# The rows every evaluation reads (eval_tags, tag_monthly_edge, the pattern page, the expectancy panel): one definition, so
# the numbers cannot disagree. Columns: session_date, ticker, tag, ret2_h1/h3/h5/h10 (outcome v2), ret_h5 (v1, comparison).
# The date literal comes from the constant above, never from input.
EVAL_SOURCE = f"""(
    SELECT session_date, ticker, tag, ret2_h1, ret2_h3, ret2_h5, ret2_h10, ret_h5
    FROM signal_log WHERE session_date < DATE '{FREEZE_SINCE.isoformat()}'
    UNION ALL
    SELECT ss.session_date, ss.ticker, ss.tag, ss.ret2_h1, ss.ret2_h3, ss.ret2_h5, ss.ret2_h10, sl.ret_h5
    FROM signal_snapshot ss
    LEFT JOIN signal_log sl USING (session_date, ticker, tag)
    WHERE ss.session_date >= DATE '{FREEZE_SINCE.isoformat()}'
      AND ss.captured_at <= ((ss.session_date + 1)::timestamp + interval '8 hours') AT TIME ZONE 'Asia/Jakarta'
)"""

# Phase 1.25 tunables (guesses — the forward test decides)
TICKET_Z_HIGH = 1.5          # avg-ticket z >= this => unusually large trades (institutional footprint)
TICKET_Z_LOW = -1.0          # avg-ticket z <= this => unusually small trades (retail churn)
FLOAT_ACCUM_BAND = 0.01      # 5d foreign net shares / free float >= this => meaningful foreign take
_MIN_FREQ_FOR_TICKET = 20    # skip avg-ticket z below this many trades (too noisy)

# Disclosure-filing activity (disclosure_filings). A weight-0 proxy: it has no
# direction (that's in the PDF — Phase B). The forward test decides if it matters.
DISCLOSURE_MIN_5D = 3         # absolute filing cluster in the trailing 5 td
DISCLOSURE_SURGE_MULT = 2.0   # ... AND >= this x the trailing-20 td rate (per 5 td)

# Broker-summary signals (vektorgauss, Tier 3, forward-only). Broker-house
# foreign flow (SUM net_val of foreign-broker houses) is a DIFFERENT measure than
# IDX account-domicile foreign flow (nff_*) — never summed, only cross-checked.
# Only populated for tickers actually collected (watchlist + holdings). Guesses.
BROKER_FOREIGN_BAND = 0.05    # |foreign-broker net| >= 5% of day's value => meaningful
BROKER_XCHECK_BAND = 0.01     # each measure >= 1% of value to enter the konfirm/divergen check

# Heuristic participant classifier (INFERENCE, forward-tested). Bands vs day value.
PARTICIPANT_BAND = 0.05       # cohort net-buy >= 5% of day's value => that cohort is active
BANDAR_CR1_HIGH = 0.40        # one broker >= 40% of all net buying => concentration
BANDAR_TICKET_MIN = 50_000_000  # dominant buyer avg ticket >= Rp50jt/trade (institutional footprint)
# Wash-overlap filter on the Bandar Power Index (probation; both numbers are guesses to be forward-tested). When two
# or more of the top-3 buyers are ALSO top-3 sellers, a high concentration is the same houses on both sides
# (crossing / market making), not one hand accumulating.
WASH_OVERLAP_MIN = 2
BPI_HIGH = 0.10    # bpi3 >= 10 points of the day's value ~ the top fifth of the 5 Nov 2026 live distribution (80th pct +0.099)


def is_wash(bpi3: float | None, overlap3: int | None) -> bool:
    """The AKUM-NETRAL-WASH rule, in one place so `compute` and `backfill_wash` can never disagree about it."""
    return bpi3 is not None and overlap3 is not None and overlap3 >= WASH_OVERLAP_MIN and bpi3 >= BPI_HIGH


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
    foreign_float_5d: float | None = None    # 5d foreign net shares / tradable_shares (= listed shares for ~96% of stocks)
    foreign_net_shares_5d: float | None = None   # transient: the numerator above, reused by FLOAT2
    foreign_float2_5d: float | None = None   # transient: 5d foreign net shares / REAL free float (LBRE, freefloat.py)
    disclosure_5d: int | None = None         # ownership-disclosure filings, trailing 5 td
    disclosure_20d: int | None = None        # ... trailing 20 td (baseline)
    # Broker-summary (vektorgauss, Tier 3). Foreign-BROKER net in IDR — a
    # different measure than nff_* (account domicile); cross-checked, never summed.
    bkr_foreign_net_d0: float | None = None   # SUM net_val of side_flag='F' brokers today
    bkr_foreign_net_5d: float | None = None   # ... trailing 5 td that have broker data
    bkr_cr3: float | None = None              # top-3 net buyers / all net buyers today (0..1)
    # CERTAIN bounds on the foreign-broker net today (IDR), from top-20 net buyers + the gross ladders
    # (broker_bounds.py). bkr_foreign_net_d0 above sums NET BUYERS ONLY and so cannot show selling.
    bkr_foreign_net_lo: float | None = None
    bkr_foreign_net_hi: float | None = None
    day_value: float | None = None            # today's traded value (IDR), transient — for ratios
    # Heuristic participant classifier (INFERENCE — RETAIL_BROKERS + avg-ticket).
    bkr_foreign_inst_net: float | None = None # F & not-retail net (IDR)
    bkr_dom_retail_net: float | None = None   # D & retail net (IDR)
    bkr_cr1: float | None = None              # top net buyer share (0..1)
    bkr_top_buyer: str | None = None          # dominant net-buyer broker (transient)
    bkr_top_buyer_ticket: float | None = None # its avg ticket (transient)
    bkr_top_buyer_inv_rising: bool | None = None  # its inventory rising (transient)
    # Broker scoring (engine/scoring.py) - PROBATION, weight 0: stored and tagged, never feeding another signal.
    bpi3: float | None = None                 # Bandar Power Index top-3 = cr3_buy - cr3_sell, in [-1, 1]
    bpi5: float | None = None
    bpi_overlap3: int | None = None           # brokers in both the top-3 buyers and the top-3 sellers
    aggr_haka_share: float | None = None      # HAKA / (HAKA + HAKI), regular board (needs done_by_price)
    aggr_ratio: float | None = None           # HAKA / HAKI
    aggr_coverage: float | None = None        # share of volume that carries an aggressor flag
    retail_net_lo: float | None = None        # certain bounds, IDR, retail-broker cohort net
    retail_net_hi: float | None = None
    finst_net_lo: float | None = None         # ... foreign institutions (foreign houses that are not retail)
    finst_net_hi: float | None = None
    retail_div_state: str | None = None
    # Phase B (disclosure_pdf.py parse of the archived KSEI forms) — direction
    # for *today's* filings only, from the header's reliably-parsed shares
    # before/after (not the fragile per-row transaction-type breakdown).
    # Transient — not persisted, only used to fire today's tag.
    insider_akum: bool | None = None
    insider_dist: bool | None = None
    substantial_akum: bool | None = None
    substantial_dist: bool | None = None
    ksei_foreign_trend_3m: str = "nodata"
    ksei_retail_trend_3m: str = "nodata"
    ksei_foreign_pct: float | None = None      # level, transient (not persisted)
    ksei_inst_pct: float | None = None
    ksei_indiv_pct: float | None = None
    ksei_foreign_d3m: float | None = None      # 3-month change (fraction), persisted
    ksei_inst_d3m: float | None = None
    ksei_indiv_d3m: float | None = None
    # KSEI2 (transient, not persisted): the same 3-month deltas, only from files available at the time and never across a
    # scripless jump (None = not measurable that way).
    ksei2_foreign_trend: str = "nodata"
    ksei2_inst_d3m: float | None = None
    ksei2_indiv_d3m: float | None = None
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


def _disclosure_direction(cur: psycopg.Cursor, session_date: date) -> dict[str, dict[str, bool]]:
    """Per-ticker direction from *today's* disclosure_holdings (Phase B parse),
    split by is_insider. Uses the header's shares_before/after — reliably
    regexed even on filings where the per-row transaction-type table didn't
    structurally parse (see disclosure_pdf.py). One row per (ticker,
    is_insider); a ticker with both insider and non-insider filings today
    gets both."""
    cur.execute(
        """
        SELECT ticker, is_insider,
               bool_or(shares_after > shares_before) AS akum,
               bool_or(shares_after < shares_before) AS dist
        FROM disclosure_holdings
        WHERE filed_date = %s AND shares_before IS NOT NULL AND shares_after IS NOT NULL
        GROUP BY ticker, is_insider
        """,
        (session_date,),
    )
    out: dict[str, dict[str, bool]] = {}
    for ticker, is_insider, akum, dist in cur.fetchall():
        key = "insider" if is_insider else "substantial"
        d = out.setdefault(ticker, {})
        d[f"{key}_akum"] = bool(akum)
        d[f"{key}_dist"] = bool(dist)
    return out


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


def ksei2_from_rows(rows: list[tuple]) -> tuple[str, float | None, float | None]:
    """KSEI2: (foreign trend, local-institution 3-month delta, local-individual 3-month delta) from the available month-end
    rows, newest first, each (detail, local_individual, local_institution, foreign_total, scripless_pct). Same arithmetic as
    v1, but nothing is measured across months whose scripless share of all shares moved by SCRIPLESS_JUMP or more: those
    percentages shift because old shares were deposited into KSEI, not because anyone traded (SGER Oct-2026: 44.7% -> 100%).
    Pure, unit-tested. Returns ("nodata", None, None) when it cannot measure."""
    if len(rows) < 3:
        return "nodata", None, None
    used = rows[: min(4, len(rows))]
    sps = [r[4] for r in used if r[4] is not None]
    if sps and max(sps) - min(sps) >= SCRIPLESS_JUMP:
        return "nodata", None, None
    now, then = _ksei_shares(*used[0][:4]), _ksei_shares(*used[-1][:4])
    if now is None or then is None:
        return "nodata", None, None
    fd = now[0] - then[0]
    trend = "up" if fd > KSEI_TREND_BAND else "down" if fd < -KSEI_TREND_BAND else "flat"
    return trend, now[1] - then[1], now[2] - then[2]


def _ksei_metrics2(cur: psycopg.Cursor, ticker: str, session_date: date) -> tuple[str, float | None, float | None]:
    """The month-end rows that were AVAILABLE on `session_date` (stored before 08:00 WIB of the next day, or, for months
    loaded later in a backfill, at least KSEI_PUBLISH_LAG_DAYS after month end), then `ksei2_from_rows`."""
    cur.execute(
        """
        SELECT detail, local_individual, local_institution, foreign_total, (detail->>'scripless_pct')::float8
        FROM ownership_monthly
        WHERE ticker = %(t)s AND month_end <= %(d)s
          AND (month_end + %(lag)s <= %(d)s
               OR fetched_at <= ((%(d)s::date + 1)::timestamp + interval '8 hours') AT TIME ZONE 'Asia/Jakarta')
        ORDER BY month_end DESC LIMIT 4
        """,
        {"t": ticker, "d": session_date, "lag": KSEI_PUBLISH_LAG_DAYS},
    )
    return ksei2_from_rows(cur.fetchall())


def _free_float(cur: psycopg.Cursor, ticker: str, session_date: date) -> float | None:
    """The real free-float share count (LBRE, freefloat.py) usable on `session_date`, or None. Usable means: the month was
    first announced before 08:00 WIB of the next day, the report is internally consistent, it is at most FREE_FLOAT_MAX_AGE_DAYS
    old, and NO corporate action happened after its month end (an IDX reference-price reset on an IDX bar): after a rights
    issue, split or bonus the share count in the report no longer describes the stock (FORU, rights on 22-Sep-2026)."""
    cur.execute(
        """
        SELECT month_end, free_float_shares FROM free_float_monthly
        WHERE ticker = %(t)s AND consistent AND month_end < %(d)s AND month_end >= %(d)s::date - %(age)s
          AND first_announced_at <= (%(d)s::date + 1)::timestamp + interval '8 hours'
        ORDER BY month_end DESC LIMIT 1
        """,
        {"t": ticker, "d": session_date, "age": FREE_FLOAT_MAX_AGE_DAYS},
    )
    row = cur.fetchone()
    if not row or not row[1] or float(row[1]) <= 0:
        return None
    month_end, shares = row
    cur.execute(
        """
        SELECT 1 FROM (
            SELECT session_date, prev, source_tier, lag(close) OVER (ORDER BY session_date) AS pc
            FROM price_bars WHERE ticker = %(t)s AND session_date > %(m)s::date - 10 AND session_date <= %(d)s) x
        WHERE session_date > %(m)s AND source_tier = 0 AND pc > 0 AND prev > 0 AND abs(prev / pc - 1) > %(ref)s
        LIMIT 1
        """,
        {"t": ticker, "m": month_end, "d": session_date, "ref": REF_JUMP},
    )
    return None if cur.fetchone() else float(shares)


@dataclass(slots=True)
class _BrokerSnap:
    foreign_net_d0: float | None = None
    foreign_net_5d: float | None = None
    cr3: float | None = None
    has_d0: bool = False
    # Heuristic participant breakdown (INFERENCE — see RETAIL_BROKERS). All IDR net.
    foreign_inst_net: float | None = None    # side_flag='F' AND not retail
    dom_retail_net: float | None = None      # side_flag='D' AND retail cohort
    cr1: float | None = None                 # top net buyer / all net buying (0..1)
    top_buyer: str | None = None             # dominant net-buyer broker code
    top_buyer_ticket: float | None = None    # its avg ticket (buy_val/buy_freq)
    top_buyer_inv_rising: bool | None = None # its cum_net_val rising over the window
    foreign_net_lo: float | None = None      # certain bounds on foreign-broker net (needs broker_levels)
    foreign_net_hi: float | None = None


def _broker_metrics(cur: psycopg.Cursor, ticker: str, cal: list[date]) -> _BrokerSnap:
    """Broker-house foreign net + concentration + a HEURISTIC participant breakdown
    (foreign-institution vs domestic-retail, dominant "bandar" candidate) from
    broker_flows + broker_inventory. Tier-3, inference only — forward-tested."""
    d0 = cal[-1]
    cur.execute(
        "SELECT session_date, COALESCE(SUM(net_val) FILTER (WHERE side_flag = 'F'), 0) "
        "FROM broker_flows WHERE ticker = %s AND session_date = ANY(%s) "
        "GROUP BY session_date",
        (ticker, cal),
    )
    by_date = {d: float(v) for d, v in cur.fetchall()}
    snap = _BrokerSnap()
    if d0 in by_date:
        snap.has_d0 = True
        snap.foreign_net_d0 = by_date[d0]
    vals5 = [by_date[d] for d in cal[-5:] if d in by_date]
    if vals5:
        snap.foreign_net_5d = sum(vals5)

    # Full per-broker rows for d0 → concentration + participant cohorts in one pass.
    cur.execute(
        "SELECT broker_code, side_flag, net_val, buy_val, buy_freq, sell_val "
        "FROM broker_flows WHERE ticker = %s AND session_date = %s",
        (ticker, d0),
    )
    rows = cur.fetchall()
    if rows:
        f_inst = d_retail = 0.0
        buyers: list[tuple[float, str, float | None]] = []  # (net, code, avg_ticket)
        for code, sf, net_val, buy_val, buy_freq, _sell_val in rows:
            net = float(net_val) if net_val is not None else 0.0
            retail = is_retail(code)
            if sf == "F" and not retail:
                f_inst += net
            if sf == "D" and retail:
                d_retail += net
            if net > 0:
                tkt = (float(buy_val) / float(buy_freq)) if (buy_val and buy_freq) else None
                buyers.append((net, code, tkt))
        snap.foreign_inst_net = f_inst
        snap.dom_retail_net = d_retail
        buyers.sort(reverse=True)
        total_buy = sum(b[0] for b in buyers)
        if total_buy > 0:
            snap.cr3 = sum(b[0] for b in buyers[:3]) / total_buy
            snap.cr1 = buyers[0][0] / total_buy
            snap.top_buyer = buyers[0][1]
            snap.top_buyer_ticket = buyers[0][2]

    # Certain bounds on the foreign-broker net. Only when the gross ladder exists for d0 (collected
    # from 2 Nov 2026 on, or re-fetched); NULL otherwise, and the *_PASTI tags below stay silent.
    if rows:
        cur.execute(
            "SELECT side, broker_code, val FROM broker_levels WHERE ticker = %s AND session_date = %s",
            (ticker, d0),
        )
        levels = cur.fetchall()
        bnd = cohort_net_bounds(
            [(r[0], r[3], r[5]) for r in rows], levels, FOREIGN_BROKERS) if levels else None
        if bnd is not None:
            snap.foreign_net_lo, snap.foreign_net_hi = bnd.lo, bnd.hi

    # "Bandar" persistence: is the dominant buyer's inventory (cum_net_val) rising
    # across the window? Needs broker_inventory (collected for the harvest set).
    if snap.top_buyer:
        cur.execute(
            "SELECT cum_net_val FROM broker_inventory "
            "WHERE ticker = %s AND broker_code = %s AND session_date = ANY(%s) "
            "ORDER BY session_date",
            (ticker, snap.top_buyer, cal),
        )
        cums = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
        if len(cums) >= 2:
            snap.top_buyer_inv_rising = cums[-1] > cums[0]
    return snap


def _apply_scoring(c: Components, s: TickerScore) -> None:
    """Copy the broker-scoring measures onto the components. A measure that could not be computed stays None."""
    if s.bandar is not None:
        c.bpi3, c.bpi5, c.bpi_overlap3 = s.bandar.bpi3, s.bandar.bpi5, s.bandar.overlap3
    if s.aggressor is not None:
        c.aggr_haka_share, c.aggr_ratio, c.aggr_coverage = (
            s.aggressor.haka_share, s.aggressor.ratio, s.aggressor.coverage)
    if s.divergence is not None:
        c.retail_net_lo, c.retail_net_hi = s.divergence.retail
        c.finst_net_lo, c.finst_net_hi = s.divergence.foreign_institutions
        c.retail_div_state = s.divergence.state


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
    c.day_value = float(b0.value) if b0.value else None
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

    five = [back(k) for k in range(5)]
    if all(g is not None for g in five):
        c.foreign_net_shares_5d = sum(g.foreign_net_shares for g in five)
        if b0.tradable_shares and b0.tradable_shares > 0:
            c.foreign_float_5d = c.foreign_net_shares_5d / b0.tradable_shares

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
    # v2 of the two above (audit 2026-11-07): only files available at the time, never across a scripless jump. New clock.
    if c.ksei2_inst_d3m is not None and c.ksei2_inst_d3m > KSEI_TREND_BAND:
        tags.append("KSEI2-INST-AKUM")
    if c.ksei2_indiv_d3m is not None and c.ksei2_indiv_d3m < -KSEI_TREND_BAND:
        tags.append("KSEI2-RITEL-EXIT")
    if c.foreign_float_5d is not None and c.foreign_float_5d > FLOAT_ACCUM_BAND:
        tags.append("FLOAT-ASING-PADAT")       # foreign net-took a slice of a thin float
    # v2 (audit 2026-11-07, B3): the same 1% band against the REAL free float from the issuer's monthly registry report,
    # only when that report was public and no corporate action has changed the share count since. New forward clock.
    if c.foreign_float2_5d is not None and c.foreign_float2_5d > FLOAT_ACCUM_BAND:
        tags.append("FLOAT2-ASING-PADAT")

    # Unusual ownership-disclosure activity (>5% / insider filings). No direction —
    # weight-0 proxy pending the PDF parse (Phase B).
    if (c.disclosure_5d or 0) >= DISCLOSURE_MIN_5D:
        rate_5td = (c.disclosure_20d or c.disclosure_5d) / 4.0   # trailing rate per 5 td
        if c.disclosure_5d >= DISCLOSURE_SURGE_MULT * rate_5td:
            tags.append("LAPOR-KEPEMILIKAN-RAMAI")

    # Phase B — direction from today's parsed disclosure filings (see
    # _disclosure_direction). Independent of the surge-count proxy above.
    if c.insider_akum:
        tags.append("INSIDER-AKUM")
    if c.insider_dist:
        tags.append("INSIDER-DIST")
    if c.substantial_akum:
        tags.append("SUBSTANTIAL-AKUM")
    if c.substantial_dist:
        tags.append("SUBSTANTIAL-DIST")

    # Bandar Power Index, wash filter: the buy side looks concentrated, but two or more of the top-3 buyers are also
    # top-3 sellers - the same houses on both sides - so it is NOT read as pure accumulation. Descriptive, probation.
    # Needs no price history, so it sits above the early return.
    if is_wash(c.bpi3, c.bpi_overlap3):
        tags.append("AKUM-NETRAL-WASH")

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
    # v2 cross-check on the KSEI2 trend (available files only, no scripless jump). Annotation only, new clock.
    if "AKUM-ASING" in tags:
        if c.ksei2_foreign_trend == "down":
            tags.append("KSEI2-DIVERGEN")
        elif c.ksei2_foreign_trend == "up":
            tags.append("KSEI2-KONFIRM")
    if "DISTRIBUSI-ASING" in tags:
        if c.ksei2_foreign_trend == "up":
            tags.append("KSEI2-DIVERGEN")
        elif c.ksei2_foreign_trend == "down":
            tags.append("KSEI2-KONFIRM")

    if "CROSSING-TINGGI" in c.data_flags:
        tags.append("CROSSING-TINGGI")

    # Phase 1.25 — average ticket size (value / trade count) as an institutional-
    # footprint proxy. Big/rising ticket into weakness vs small ticket chasing.
    if c.avg_ticket_z is not None:
        if c.avg_ticket_z > TICKET_Z_HIGH and direction in ("flat", "down"):
            tags.append("TIKET-BESAR-AKUM")
        if c.avg_ticket_z < TICKET_Z_LOW and direction == "up":
            tags.append("TIKET-KECIL-MARKUP")

    # Broker-house foreign flow (vektorgauss, Tier 3). A DIFFERENT lens than
    # nff_* (account domicile): here 'foreign' = trades executed via foreign-broker
    # houses (see FOREIGN_BROKERS). Net-buy into weakness = accumulation; the
    # cross-check flags when the two foreign measures disagree.
    if c.bkr_foreign_net_d0 is not None and c.day_value:
        rb = c.bkr_foreign_net_d0 / c.day_value
        if rb >= BROKER_FOREIGN_BAND and direction in ("flat", "down"):
            tags.append("BROKER-ASING-AKUM")
        if rb <= -BROKER_FOREIGN_BAND and direction == "up":
            tags.append("BROKER-ASING-DIST")
        # cross-check vs account-domicile foreign flow (nff_d0), both normalized
        if c.nff_d0 is not None:
            rd = c.nff_d0 / c.day_value
            if abs(rb) >= BROKER_XCHECK_BAND and abs(rd) >= BROKER_XCHECK_BAND:
                if (rb > 0) == (rd > 0):
                    tags.append("BROKER-DOMISILI-KONFIRM")
                else:
                    tags.append("BROKER-DOMISILI-DIVERGEN")
                    c.data_flags.append("BROKER-DOMISILI-DIVERGEN")

    # v2 (2 Nov 2026): the same question as BROKER-ASING-* but answered with CERTAIN bounds — the tag fires
    # only if the WHOLE interval clears the band. The v1 tags above sum net buyers only (a one-sided
    # measure that disagreed in sign with the bounds for ~71% of liquid names on 30 Oct), so they are
    # kept untouched for the running forward test but should not be read as foreign net flow.
    # A NEW forward-test clock starts for these two (ARSITEKTUR-DATA-v1, K2).
    if c.bkr_foreign_net_lo is not None and c.bkr_foreign_net_hi is not None and c.day_value:
        # Same price-context rule as v1, so the ONLY difference between v1 and v2 is the measure.
        if c.bkr_foreign_net_lo / c.day_value >= BROKER_FOREIGN_BAND and direction in ("flat", "down"):
            tags.append("BROKER2-ASING-AKUM")
        if c.bkr_foreign_net_hi / c.day_value <= -BROKER_FOREIGN_BAND and direction == "up":
            tags.append("BROKER2-ASING-DIST")
        # v2 of BROKER-DOMISILI-*: the broker side counts only when its CERTAIN interval says which way it went.
        if c.nff_d0 is not None:
            rd = c.nff_d0 / c.day_value
            lo, hi = c.bkr_foreign_net_lo / c.day_value, c.bkr_foreign_net_hi / c.day_value
            bsign = 1 if lo >= BROKER_XCHECK_BAND else -1 if hi <= -BROKER_XCHECK_BAND else 0
            if bsign and abs(rd) >= BROKER_XCHECK_BAND:
                if (rd > 0) == (bsign > 0):
                    tags.append("BROKER2-DOMISILI-KONFIRM")
                else:
                    tags.append("BROKER2-DOMISILI-DIVERGEN")

    # v2 of FOREIGN-INST / DOM-RETAIL (audit 2026-11-07, B4): v1 sums net buyers only, so it can only ever see buying.
    # These use the CERTAIN cohort bounds the scoring step already computes (retail houses, foreign institutions) and fire
    # only when the whole interval clears the band. New clock.
    if c.day_value:
        if c.finst_net_lo is not None and c.finst_net_lo / c.day_value >= PARTICIPANT_BAND:
            tags.append("FOREIGN-INST2")
        if c.retail_net_lo is not None and c.retail_net_lo / c.day_value >= PARTICIPANT_BAND:
            tags.append("DOM-RETAIL2")

    # HEURISTIC participant classifier (INFERENCE — RETAIL_BROKERS + avg-ticket +
    # concentration; a broker aggregates many clients, so these are model labels,
    # never ground truth). All probation, forward-tested.
    if c.day_value:
        if (c.bkr_foreign_inst_net is not None
                and c.bkr_foreign_inst_net / c.day_value >= PARTICIPANT_BAND):
            tags.append("FOREIGN-INST")          # foreign institutional houses net-buying hard
        if (c.bkr_dom_retail_net is not None
                and c.bkr_dom_retail_net / c.day_value >= PARTICIPANT_BAND):
            tags.append("DOM-RETAIL")            # domestic retail crowding the buy side (caution)
        # "Bandar" candidate: one broker dominates the net buying, with an
        # institutional-size ticket, and (where inventory data exists) a rising
        # position. Concentration + big ticket + persistence.
        if (c.bkr_cr1 is not None and c.bkr_cr1 >= BANDAR_CR1_HIGH
                and (c.bkr_top_buyer_ticket or 0) >= BANDAR_TICKET_MIN
                and c.bkr_top_buyer_inv_rising is not False):
            tags.append("BANDAR-CANDIDATE")

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
    "bkr_foreign_net_d0", "bkr_foreign_net_5d", "bkr_cr3",
    "bkr_foreign_inst_net", "bkr_dom_retail_net", "bkr_cr1", "bkr_top_buyer",
    "bkr_foreign_net_lo", "bkr_foreign_net_hi",
    "bpi3", "bpi5", "bpi_overlap3", "aggr_haka_share", "aggr_ratio", "aggr_coverage",
    "retail_net_lo", "retail_net_hi", "finst_net_lo", "finst_net_hi", "retail_div_state",
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
        c.bkr_foreign_net_d0, c.bkr_foreign_net_5d, c.bkr_cr3,
        c.bkr_foreign_inst_net, c.bkr_dom_retail_net, c.bkr_cr1, c.bkr_top_buyer,
        c.bkr_foreign_net_lo, c.bkr_foreign_net_hi,
        c.bpi3, c.bpi5, c.bpi_overlap3, c.aggr_haka_share, c.aggr_ratio, c.aggr_coverage,
        c.retail_net_lo, c.retail_net_hi, c.finst_net_lo, c.finst_net_hi, c.retail_div_state,
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
        dirn = _disclosure_direction(cur, session_date)
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
            c.ksei2_foreign_trend, c.ksei2_inst_d3m, c.ksei2_indiv_d3m = _ksei_metrics2(cur, t, session_date)
            if c.foreign_net_shares_5d is not None:
                ffs = _free_float(cur, t, session_date)
                c.foreign_float2_5d = None if ffs is None else c.foreign_net_shares_5d / ffs
            c.disclosure_5d, c.disclosure_20d = disc.get(t, (0, 0))
            bsnap = _broker_metrics(cur, t, cal)
            c.bkr_foreign_net_d0 = bsnap.foreign_net_d0
            c.bkr_foreign_net_5d = bsnap.foreign_net_5d
            c.bkr_cr3 = bsnap.cr3
            c.bkr_foreign_net_lo = bsnap.foreign_net_lo
            c.bkr_foreign_net_hi = bsnap.foreign_net_hi
            c.bkr_foreign_inst_net = bsnap.foreign_inst_net
            c.bkr_dom_retail_net = bsnap.dom_retail_net
            c.bkr_cr1 = bsnap.cr1
            c.bkr_top_buyer = bsnap.top_buyer
            c.bkr_top_buyer_ticket = bsnap.top_buyer_ticket
            c.bkr_top_buyer_inv_rising = bsnap.top_buyer_inv_rising
            if bsnap.has_d0:   # scoring reads the same broker rows; skip tickers the feed did not cover today
                _apply_scoring(c, score_ticker(cur, t, session_date))
            dflags = dirn.get(t, {})
            c.insider_akum = dflags.get("insider_akum")
            c.insider_dist = dflags.get("insider_dist")
            c.substantial_akum = dflags.get("substantial_akum")
            c.substantial_dist = dflags.get("substantial_dist")
            tags = _classify(c)
            if session_date < V2_TAGS_SINCE:          # new clock: no v2 tag for a session computed (or recomputed) before it
                tags = [tg for tg in tags if tg not in V2_TAGS]
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
        # The forward record (B13): a tag's FIRST appearance, never overwritten or deleted by a later recompute, so the
        # evaluation can count only what was known before the next open. See signal_snapshot in db/schema.sql.
        upsert_many(
            cur, "signal_snapshot", ("session_date", "ticker", "tag"),
            [(r[0], r[1], r[2]) for r in log_rows], conflict=("session_date", "ticker", "tag"),
        )
        _log(cur, "compute", session_date, "ok", n_comp,
             f"{len(tickers)} tickers, {len(log_rows)} tags")

    return ComputeSummary(session_date, "ok", len(tickers), len(log_rows))


WASH_TAG = "AKUM-NETRAL-WASH"


class WashBackfillRefused(ValueError):
    """The requested range would touch the forward-test window (or the window is not defined)."""


def backfill_wash(start: date | None = None, end: date | None = None, *, dry_run: bool = False) -> dict:
    """Write AKUM-NETRAL-WASH for sessions BEFORE the forward-test window, from the broker scores `compute` already stored.

    This is the in-sample history of one tag. It changes nothing else: no other tag, no component, nothing on or after
    VG_FORWARD_SINCE (a range that reaches it is refused, and if VG_FORWARD_SINCE is not set nothing is written at all).
    The tag is read from `signal_components.bpi3 / bpi_overlap3` with the same `is_wash` rule `compute` uses, so there is no
    second copy of the logic and no data is recomputed. The 0.10 threshold was picked looking at the 5 Nov 2026 distribution,
    so every row written here is IN-SAMPLE: it shows what the tag would have said, it is not evidence the tag works.

    Returns {"start", "end", "days", "rows", "by_month", "written"}; `dry_run` counts without writing.
    """
    from ..config import CONFIG

    fwd = CONFIG.forward_since
    if fwd is None:
        raise WashBackfillRefused("VG_FORWARD_SINCE is not set, so I cannot tell where the forward test starts; nothing written")
    last_allowed = date.fromordinal(fwd.toordinal() - 1)
    if end is None:
        end = last_allowed
    if end > last_allowed:
        raise WashBackfillRefused(f"{end} is inside the forward-test window (from {fwd}); the last date allowed is {last_allowed}")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT min(session_date) FROM signal_components WHERE bpi3 IS NOT NULL")
        first = cur.fetchone()[0]
        if first is None:
            return {"start": None, "end": end, "days": 0, "rows": 0, "by_month": {}, "written": 0}
        start = max(start, first) if start is not None else first
        cur.execute(
            "SELECT sc.session_date, sc.ticker, sc.bpi3, sc.bpi_overlap3, pb.close "
            "FROM signal_components sc JOIN price_bars pb ON pb.session_date = sc.session_date AND pb.ticker = sc.ticker "
            "WHERE sc.session_date BETWEEN %s AND %s AND sc.bpi3 IS NOT NULL AND sc.bpi_overlap3 IS NOT NULL",
            (start, end))
        hits = [(d, t, WASH_TAG, "probation", c) for d, t, b3, o3, c in cur.fetchall() if is_wash(float(b3), int(o3))]
        by_month: dict[str, int] = {}
        for d, *_ in hits:
            by_month[f"{d:%Y-%m}"] = by_month.get(f"{d:%Y-%m}", 0) + 1
        written = 0
        if not dry_run and hits:
            written = upsert_many(cur, "signal_log", ("session_date", "ticker", "tag", "layer", "close"), hits,
                                  conflict=("session_date", "ticker", "tag"), update=("close",))
            _log(cur, "backfill_wash", end, "ok", written, f"{start}..{end}: in-sample history of {WASH_TAG}, nothing at or after {fwd}")
    return {"start": start, "end": end, "days": len({h[0] for h in hits}), "rows": len(hits), "by_month": dict(sorted(by_month.items())),
            "written": written}


def _fns5_by_ticker(cur: psycopg.Cursor, session_date: date) -> dict[str, float]:
    """5-session foreign net shares per ticker up to `session_date`, exactly as `_components` computes it (the 5 latest
    trading-calendar sessions, every one with a bar; a bar missing either foreign side counts 0)."""
    cur.execute(
        """
        WITH cal5 AS (SELECT session_date FROM trading_calendar WHERE session_date <= %(d)s ORDER BY session_date DESC LIMIT 5)
        SELECT ticker, sum(CASE WHEN foreign_buy_vol IS NULL OR foreign_sell_vol IS NULL THEN 0
                                ELSE foreign_buy_vol - foreign_sell_vol END)::float8
        FROM price_bars WHERE session_date IN (SELECT session_date FROM cal5)
        GROUP BY ticker HAVING count(*) = 5
        """,
        {"d": session_date},
    )
    return dict(cur.fetchall())


def backfill_tags_v2(start: date | None = None, end: date | None = None, *, dry_run: bool = False) -> dict:
    """Write the IN-SAMPLE history of the v2 tags (V2_TAGS) for sessions BEFORE the forward-test window.

    Same contract as `backfill_wash`: nothing on or after VG_FORWARD_SINCE (a range reaching it is refused; unset = refused),
    and nothing else changes - rows are INSERTed with ON CONFLICT DO NOTHING, so no existing row (v1 or v2) is updated, no
    component is recomputed or rewritten. Each session's stored `signal_components` row is loaded into `Components`, the v2
    inputs that are not stored are computed the way `compute_day` computes them (`_ksei_metrics2`, `_free_float`, the 5-session
    foreign net shares), and the ONE `_classify` decides; only V2_TAGS are kept. In-sample: shows what the tag would have said,
    not evidence that it works. The forward window between VG_FORWARD_SINCE and V2_TAGS_SINCE stays empty for these tags on
    purpose: no v2 tag there was computed at the time.
    """
    from dataclasses import fields as _fields

    from ..config import CONFIG

    fwd = CONFIG.forward_since
    if fwd is None:
        raise WashBackfillRefused("VG_FORWARD_SINCE is not set, so I cannot tell where the forward test starts; nothing written")
    last_allowed = date.fromordinal(fwd.toordinal() - 1)
    end = end or last_allowed
    if end > last_allowed:
        raise WashBackfillRefused(f"{end} is inside the forward-test window (from {fwd}); the last date allowed is {last_allowed}")
    names = {f.name for f in _fields(Components)}
    cols = [c for c in _COMP_COLS if c in names]
    stats: dict = {"days": 0, "rows": 0, "written": 0, "by_tag": {}}
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT min(session_date), max(session_date) FROM signal_components WHERE session_date <= %s", (end,))
        first, last = cur.fetchone()
        if first is None:
            return {**stats, "start": None, "end": end}
        start = max(start, first) if start is not None else first
        cur.execute("SELECT min(month_end) FROM free_float_monthly")
        ff_first = cur.fetchone()[0]
        cur.execute("SELECT DISTINCT session_date FROM signal_components WHERE session_date BETWEEN %s AND %s ORDER BY 1", (start, end))
        days = [r[0] for r in cur.fetchall()]
        for d in days:
            cur.execute(
                f"SELECT {', '.join('sc.' + c for c in cols)}, p.value FROM signal_components sc "
                "JOIN price_bars p ON p.session_date = sc.session_date AND p.ticker = sc.ticker WHERE sc.session_date = %s",
                (d,),
            )
            comp_rows = cur.fetchall()
            fns5 = _fns5_by_ticker(cur, d) if ff_first is not None and ff_first < d else {}
            hits = []
            for row in comp_rows:
                kw = {c: (float(v) if isinstance(v, Decimal) else v) for c, v in zip(cols, row[:-1], strict=True)}
                kw["data_flags"] = list(kw.get("data_flags") or [])
                c = Components(**kw)
                c.day_value = float(row[-1]) if row[-1] else None
                c.ksei2_foreign_trend, c.ksei2_inst_d3m, c.ksei2_indiv_d3m = _ksei_metrics2(cur, c.ticker, d)
                if c.ticker in fns5:
                    c.foreign_net_shares_5d = fns5[c.ticker]
                    ffs = _free_float(cur, c.ticker, d)
                    c.foreign_float2_5d = None if ffs is None else c.foreign_net_shares_5d / ffs
                for tag in _classify(c):
                    if tag in V2_TAGS:
                        hits.append((d, c.ticker, tag, "probation", c.close))
                        stats["by_tag"][tag] = stats["by_tag"].get(tag, 0) + 1
            stats["days"] += 1
            stats["rows"] += len(hits)
            if hits and not dry_run:
                stats["written"] += upsert_many(cur, "signal_log", ("session_date", "ticker", "tag", "layer", "close"), hits,
                                                conflict=("session_date", "ticker", "tag"))   # DO NOTHING: never touch a row
                conn.commit()                                                            # an interrupted run keeps its days
        if not dry_run:
            _log(cur, "backfill_tags_v2", end, "ok", stats["written"], f"{start}..{end}: in-sample v2 tags, nothing at or after {fwd}")
    return {**stats, "start": start, "end": end}


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

    Since 2026-11-07 every number here is OUTCOME V2 (`outcomes2.py`): entry at the VWAP of t+1, total return adjusted
    with IDX's own reference prices + cash dividends, against `market_breadth2` (same formula). The old v1 numbers
    (entry at the signal-day close, split-only adjustment, see the 2026-11-07 audit) are kept as `edge_h5_v1` so a reader
    can see how much the correction moved each tag. Needs `vg backfill-outcomes2`.
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
                   count(*)                                        AS n,
                   min(s.session_date)                             AS first_date,
                   max(s.session_date)                             AS last_date,
                   avg(s.ret2_h5)                                  AS avg_h5,
                   avg(s.ret2_h1  - m2.ret2_h1_all)                AS edge_h1,
                   avg(s.ret2_h5  - m2.ret2_h5_all)                AS edge_h5,
                   avg(s.ret2_h10 - m2.ret2_h10_all)               AS edge_h10,
                   avg((s.ret2_h5 > 0)::int)::float                AS hit_rate,
                   avg(m2.hit2_h5_all)                             AS univ_hit_rate,
                   count(*) FILTER (WHERE p.value > %(liq)s)       AS n_liquid,
                   avg(s.ret2_h5 - m2.ret2_h5_liquid)
                       FILTER (WHERE p.value > %(liq)s)            AS edge_h5_liquid,
                   (avg((s.ret2_h5 > 0)::int)
                       FILTER (WHERE p.value > %(liq)s))::float    AS hit_rate_liquid,
                   avg(s.ret_h5 - m.ret_h5_all)                    AS edge_h5_v1,
                   -- signal_flags (flags.py): rows whose input was known broken at the time; shown, never silently dropped
                   count(*) FILTER (WHERE f.flags IS NOT NULL)     AS n_flagged,
                   avg(s.ret2_h5 - m2.ret2_h5_all)
                       FILTER (WHERE f.flags IS NULL)              AS edge_h5_clean
            FROM {EVAL_SOURCE} s
            JOIN market_breadth2 m2 USING (session_date)
            LEFT JOIN market_breadth m USING (session_date)
            LEFT JOIN signal_flags f USING (session_date, ticker, tag)
            JOIN price_bars p ON p.session_date = s.session_date AND p.ticker = s.ticker
            WHERE s.ret2_h5 IS NOT NULL{since_clause}
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


def tag_monthly_edge(since: date | None = None) -> dict[str, list[tuple[date, int, float | None]]]:
    """Per tag, per calendar month: (month_start, n, edge_h5), oldest month first.

    Same definition as `eval_tags` (tag ret_h5 minus the date-matched equal-weight universe ret_h5, so a month's number is
    directly comparable with the tag's pooled edge), only grouped by month; `since` restricts to signals on/after that date,
    exactly like `eval_tags`. Only rows whose 5-day outcome has matured count, so the newest month is usually thin: callers
    must show `n` next to the number (checklist F5 / G2).
    """
    params: dict = {}
    since_clause = ""
    if since is not None:
        since_clause = " AND s.session_date >= %(since)s"
        params["since"] = since
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT s.tag, date_trunc('month', s.session_date)::date AS month, count(*) AS n,
                   avg(s.ret2_h5 - m2.ret2_h5_all) AS edge_h5
            FROM {EVAL_SOURCE} s
            JOIN market_breadth2 m2 USING (session_date)
            WHERE s.ret2_h5 IS NOT NULL{since_clause}
            GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            params,
        )
        out: dict[str, list[tuple[date, int, float | None]]] = {}
        for tag, month, n, edge in cur.fetchall():
            out.setdefault(tag, []).append((month, int(n), None if edge is None else float(edge)))
        return out


def latest_signal_date() -> date | None:
    """Most recent session_date that has any signal_log rows (may trail price)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(session_date) FROM signal_log")
        return cur.fetchone()[0]


# Columns that may be ranked by the web /ranking view. Whitelist — the column name
# is interpolated into SQL, so it must never come from untrusted input.
_RANKABLE = {
    "nff_d0", "nff_w1",                       # net foreign flow (from IDX: near market-wide)
    "bkr_foreign_net_d0", "bkr_foreign_net_5d",  # broker-foreign net (feed universe; coverage varies by day)
    "bkr_dom_retail_net",                     # broker domestic-retail net (feed universe, 1D only)
}


def rank_components(
    column: str, *, desc: bool = True, limit: int = 15, session_date: date | None = None
) -> tuple[date | None, int, list[dict]]:
    """Top-N tickers on the latest session ranked by a signal_components column.

    Returns (session_date, total_with_value, rows). `total_with_value` is how many
    tickers have that column non-null that day — the honest coverage figure
    (foreign ~market-wide, broker/retail only the thin vektorgauss universe).
    Column is whitelisted; order is a literal. Read-only, no new methodology.
    """
    if column not in _RANKABLE:
        raise ValueError(f"unrankable column: {column!r}")
    order = "DESC" if desc else "ASC"
    with connect() as conn, conn.cursor() as cur:
        if session_date is None:
            cur.execute("SELECT max(session_date) FROM signal_components")
            session_date = cur.fetchone()[0]
        if session_date is None:
            return None, 0, []
        cur.execute(
            f"SELECT count(*) FROM signal_components "
            f"WHERE session_date = %s AND {column} IS NOT NULL",
            (session_date,),
        )
        total = cur.fetchone()[0]
        cur.execute(
            f"""
            SELECT sc.ticker, sc.{column} AS val, pb.close, pb.prev, sc.quadrant,
                   (SELECT string_agg(DISTINCT sl.tag, ', ' ORDER BY sl.tag)
                      FROM signal_log sl
                     WHERE sl.session_date = sc.session_date AND sl.ticker = sc.ticker) AS tags
            FROM signal_components sc
            JOIN price_bars pb
              ON pb.session_date = sc.session_date AND pb.ticker = sc.ticker
            WHERE sc.session_date = %s AND sc.{column} IS NOT NULL
            ORDER BY sc.{column} {order}
            LIMIT %s
            """,
            (session_date, limit),
        )
        cols = [d[0] for d in cur.description]
        rows = [
            {k: (float(v) if isinstance(v, Decimal) else v) for k, v in zip(cols, r)}
            for r in cur.fetchall()
        ]
        return session_date, total, rows


def screener_rows(session_date: date) -> list[dict]:
    """Every ticker tagged on `session_date`, with price + flow + broker context.

    One row per ticker (tags string_agg'd), joining signal_log x price_bars x
    signal_components — the per-stock "which names today" view behind the web
    Screener (RENCANA-UI-UX, inspired by NeoBDM). Read-only; reuses the exact
    columns the digest watchlist/RADAR already read, no new methodology.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT sl.ticker,
                   string_agg(DISTINCT sl.tag, ', ' ORDER BY sl.tag)   AS tags,
                   pb.close, pb.prev, pb.value,
                   sc.nff_d0, sc.nff_w1, sc.quadrant,
                   sc.foreign_consistency_10d, sc.ksei_foreign_trend_3m,
                   sc.bkr_foreign_net_d0, sc.bkr_cr3
            FROM signal_log sl
            JOIN price_bars pb
              ON pb.session_date = sl.session_date AND pb.ticker = sl.ticker
            LEFT JOIN signal_components sc
              ON sc.session_date = sl.session_date AND sc.ticker = sl.ticker
            WHERE sl.session_date = %s
            GROUP BY sl.ticker, pb.close, pb.prev, pb.value, sc.nff_d0, sc.nff_w1, sc.quadrant,
                     sc.foreign_consistency_10d, sc.ksei_foreign_trend_3m,
                     sc.bkr_foreign_net_d0, sc.bkr_cr3
            ORDER BY sc.nff_w1 DESC NULLS LAST, sl.ticker
            """,
            (session_date,),
        )
        cols = [d[0] for d in cur.description]
        return [
            {k: (float(v) if isinstance(v, Decimal) else v) for k, v in zip(cols, r)}
            for r in cur.fetchall()
        ]
