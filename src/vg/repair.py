"""One-off, auditable repairs of known-bad price rows (ARSITEKTUR-DATA-v1 K5, findings Q4/Q5).

Q4. On 14/15/25 Sep 2026 the IDX feed was blocked and prices came from the cheaper fallback feed
    (`vektorgauss_history`). Most rows are raw, but ~0.6% (≈6/day) carry dividend/split-ADJUSTED prices (non-integer
    closes such as 215.95 — IDX prices are integers). Mixed into raw data, they fake a -95% / +1,800% day.
    Fix: the next IDX-sourced day's `prev` is that day's RAW previous close, so it is the true raw close; the same
    factor rescales open/high/low (rounded to integers). If the next day is also a fallback day (14 Sep) there is
    no raw reference; the vendor applies ONE constant backward factor across the whole window (measured: VKTR
    1.237 on 15 Sep and on 25 Sep, AHAP 1.222 on both), so the factor of the same ticker's later repaired days is
    reused — but only when those later factors agree within `FACTOR_AGREE`. Otherwise the row is flagged, not guessed.
    Leaving it unrepaired would just move the artifact to the boundary (a fake +1,700% FORU jump on 15 Sep).
Q5. `open` outside [low, high] (16 traded rows on 16 Oct 2026; another 112 have open = 0, left alone) is NULLed —
    nothing in the engine reads it.

Dry-run by default; `--apply` writes. Every touched row gets a `quality_note` saying what happened.
"""

from __future__ import annotations

from dataclasses import dataclass

REL_TOL = 0.005       # a fallback close within 0.5% of the next day's `prev` is already raw
FACTOR_AGREE = 0.002  # later repaired days must agree this closely before their factor is reused


@dataclass(frozen=True)
class RowFix:
    close: float
    open: float | None
    high: float
    low: float
    note: str


def plan_row_fix(close, open_, high, low, next_prev) -> RowFix | None:
    """None = row is fine. next_prev: raw previous close from the next IDX-sourced bar, or None if unavailable."""
    close = float(close)
    if next_prev is not None and float(next_prev) > 0:
        ref = float(next_prev)
        if abs(close / ref - 1) <= REL_TOL and close == round(close):
            return None
        f = ref / close
        return RowFix(
            close=ref,
            open=round(float(open_) * f) if open_ is not None else None,
            high=round(float(high) * f), low=round(float(low) * f),
            note=f"repaired: adjusted fallback price rescaled x{f:.4f} to the raw close implied by the next IDX day's prev",
        )
    if close != round(close):
        return RowFix(close, open_, high, low, "flagged: non-integer fallback close, no raw reference available")
    return None


def infer_factor(factors: list[float]) -> float | None:
    """One factor for a ticker iff every later repaired day agrees; None if there is no evidence or it disagrees."""
    if not factors:
        return None
    lo, hi = min(factors), max(factors)
    return sum(factors) / len(factors) if hi / lo - 1 <= FACTOR_AGREE else None


def rescale(close, open_, high, low, f: float, note: str) -> RowFix:
    return RowFix(round(float(close) * f), round(float(open_) * f) if open_ is not None else None,
                  round(float(high) * f), round(float(low) * f), note)


def run(apply: bool = False) -> dict:
    from .db import connect

    result = {"fallback_candidates": 0, "repaired": 0, "flagged": 0, "open_nulled": 0, "plan": []}
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.session_date, p.ticker, p.close, p.open, p.high, p.low,
                   (SELECT n.prev FROM price_bars n
                     WHERE n.ticker = p.ticker AND n.session_date > p.session_date AND n.source = 'idx_stock_summary'
                       AND n.session_date = (SELECT min(c.session_date) FROM trading_calendar c WHERE c.session_date > p.session_date))
            FROM price_bars p
            WHERE p.source = 'vektorgauss_history' AND p.close > 0 AND p.quality_note IS NULL
            """
        )
        rows = cur.fetchall()
        result["fallback_candidates"] = len(rows)
        fixes: dict = {}
        factors: dict[str, list[float]] = {}
        for d, tk, close, open_, high, low, nprev in rows:
            fix = plan_row_fix(close, open_, high, low, nprev)
            if fix:
                fixes[(d, tk)] = (close, open_, high, low, fix)
                if fix.note.startswith("repaired"):
                    factors.setdefault(tk, []).append(fix.close / float(close))
        for (d, tk), (close, open_, high, low, fix) in list(fixes.items()):
            if fix.note.startswith("flagged"):
                f = infer_factor(factors.get(tk, []))
                if f:
                    fixes[(d, tk)] = (close, open_, high, low, rescale(
                        close, open_, high, low, f,
                        f"repaired: adjusted fallback price rescaled x{f:.4f}, factor inferred from the same ticker's later raw-referenced days"))
        for (d, tk), (close, _o, _h, _l, fix) in sorted(fixes.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            result["repaired" if fix.note.startswith("repaired") else "flagged"] += 1
            result["plan"].append((d, tk, float(close), fix.close, fix.note.split(":")[0]))
            if apply:
                cur.execute(
                    "UPDATE price_bars SET close=%s, open=%s, high=%s, low=%s, quality_note=%s "
                    "WHERE session_date=%s AND ticker=%s",
                    (fix.close, fix.open, fix.high, fix.low, fix.note, d, tk),
                )
        cur.execute("SELECT count(*) FROM price_bars WHERE volume > 0 AND open > 0 AND low > 0 AND (open < low OR open > high)")
        result["open_nulled"] = cur.fetchone()[0]
        if apply:
            cur.execute(
                "UPDATE price_bars SET open = NULL, quality_note = COALESCE(quality_note || '; ', '') || 'open nulled: outside [low, high]' "
                "WHERE volume > 0 AND open > 0 AND low > 0 AND (open < low OR open > high)"
            )
    return result
