"""Price-adjustment factors from corporate actions (ARSITEKTUR-DATA-v1 K3).

`price_bars` holds RAW closes. A cash dividend, a split or a bonus issue makes the raw series jump on the
ex-date without any change in what a holder owns, so returns that straddle an ex-date are wrong. This builds,
per (ticker, ex-date, kind), the multiplier that puts earlier prices on the post-event footing:

    split      price factor = 1 / (new shares / old shares)      from derived 'split:' share_count_change
    bonus      price factor = old / (old + new)                   KSEI stock_dividend and bonus issues
    dividend   price factor = 1 - DPS / previous close            KSEI cash_dividend (DPS in IDR per share)

Rights issues with a subscription price are NOT adjusted: the schedule gives the ratio but not the price, so the
theoretical ex-rights price cannot be computed. They are counted and reported, never guessed.

Pure functions first (unit-tested); the DB part rebuilds `adj_factor` + `price_adj` and exposes the view
`price_bars_adj` (close_adj = split/bonus-adjusted, close_tr = also dividend-adjusted = total-return basis).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

MAX_YIELD = 0.5            # a single dividend above 50% of the price is a parse error, not a dividend
# A listed_shares jump with 0.7 < ratio < 1.5 is mostly a placement / rights exercise, not a split: only 31% of
# those moved the price by ~1/ratio (vs 85-95% for ratio >= 1.5). Measured 2 Nov 2026 on the stored events.
SPLIT_AMBIGUOUS = (0.7, 1.5)
DEDUPE_DAYS = 7            # a derived 'split:' within this many days of a KSEI bonus/stock dividend is the same event

_NUM = re.compile(r"^\s*[0-9]+(?:[.,][0-9]+)?\s*$")
_BONUS = re.compile(r"bonus", re.IGNORECASE)


@dataclass(frozen=True)
class AdjEvent:
    ticker: str
    ex_date: date
    kind: str                  # 'split' | 'bonus' | 'dividend'
    factor: float              # multiply bars BEFORE ex_date by this
    detail: str


def _num(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not _NUM.match(s):
        return None
    return float(s.replace(",", "."))


def dividend_factor(proceed, exercise, prev_close) -> tuple[float | None, str]:
    """(factor, reason). reason is '' on success, else why the row was skipped."""
    dps, per = _num(proceed), _num(exercise)
    if dps is None or per is None or per <= 0:
        return None, "dps_unparseable"
    dps = dps / per
    if not prev_close or prev_close <= 0:
        return None, "no_price"
    y = dps / float(prev_close)
    if y <= 0:
        return None, "dps_nonpositive"
    if y > MAX_YIELD:
        return None, "dps_implausible"
    return 1.0 - y, ""


def bonus_factor(old, new) -> float | None:
    o, n = _num(old), _num(new)
    if o is None or n is None or o <= 0 or n <= 0:
        return None
    return o / (o + n)


def split_factor(ratio) -> float | None:
    r = float(ratio) if ratio is not None else None
    return (1.0 / r) if r and r > 0 else None


def build_events(rows, last_bar_date: date) -> tuple[list[AdjEvent], dict[str, int]]:
    """rows: (ticker, ex_date, type, ratio, exercise_ratio, proceed_ratio, notes, source, prev_close, prev_td).
    Returns (events, skipped-reason counts). Events dated after `last_bar_date` have not happened in our data
    yet and are ignored (they must never adjust history).

    KSEI ex-dates are one trading day LATE for the regular market: the price drops on the bar before the listed
    ex-date for 35% of cash dividends (vs ~9% expected by chance) and 74% (28/38) of bonus issues. So dividend
    and bonus events take the PREVIOUS trading day as their effective ex-date (last tuple element)."""
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    ksei_bonus: dict[str, list[date]] = {}
    events: list[AdjEvent] = []
    derived: list[tuple] = []
    for tk, ex, typ, ratio, exr, prc, notes, source, prev_close, prev_td in rows:
        if ex > last_bar_date:
            skip("future_ex_date")
            continue
        if typ == "cash_dividend":
            f, why = dividend_factor(prc, exr, prev_close)
            if f is None:
                skip(why)
            else:
                events.append(AdjEvent(tk, prev_td or ex, "dividend", f, f"DPS {prc}/{exr} on {prev_close}; KSEI ex {ex}"))
        elif typ == "stock_dividend" or (typ == "rights" and _BONUS.search(notes or "")):
            f = bonus_factor(exr, prc)
            if f is None:
                skip("bonus_unparseable")
            else:
                events.append(AdjEvent(tk, prev_td or ex, "bonus", f, f"{exr} old : {prc} new; KSEI ex {ex}"))
                ksei_bonus.setdefault(tk, []).append(ex)
        elif typ == "rights":
            skip("rights_price_unknown")
        elif typ == "share_count_change":
            if (notes or "").startswith("split:"):
                derived.append((tk, ex, ratio))
            else:
                skip("dilution_not_adjusted")
        else:
            skip(f"type_{typ}")
    for tk, ex, ratio in derived:                  # KSEI schedule wins over the price-derived guess
        if any(abs((ex - d).days) <= DEDUPE_DAYS for d in ksei_bonus.get(tk, ())):
            skip("split_duplicates_bonus")
            continue
        f = split_factor(ratio)
        if f is None:
            skip("split_unparseable")
        elif SPLIT_AMBIGUOUS[0] < float(ratio) < SPLIT_AMBIGUOUS[1]:
            skip("split_ambiguous_placement")
        else:
            events.append(AdjEvent(tk, ex, "split", f, f"shares x{ratio}"))
    events.sort(key=lambda e: (e.ticker, e.ex_date, e.kind))
    return events, skipped


# ---------------------------------------------------------------------------------------
# DB part
# ---------------------------------------------------------------------------------------

_EVENT_SQL = """
SELECT ca.ticker, ca.ex_date, ca.type, ca.ratio, ca.exercise_ratio, ca.proceed_ratio, ca.notes, ca.source,
       (SELECT p.close FROM price_bars p WHERE p.ticker = ca.ticker AND p.session_date < ca.ex_date AND p.close > 0
         ORDER BY p.session_date DESC LIMIT 1) AS prev_close,
       (SELECT max(c.session_date) FROM trading_calendar c WHERE c.session_date < ca.ex_date) AS prev_td
FROM corp_actions ca
WHERE ca.type IN ('cash_dividend', 'stock_dividend', 'rights', 'share_count_change')
"""


def rebuild() -> dict:
    """Recompute adj_factor and price_adj from corp_actions + price_bars. Full rebuild in one transaction."""
    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(session_date), min(session_date) FROM price_bars")
        last, first = cur.fetchone()
        cur.execute(_EVENT_SQL)
        rows = cur.fetchall()
        events, skipped = build_events(rows, last)
        events = [e for e in events if e.ex_date > first]       # nothing before our first bar to adjust
        cur.execute("DELETE FROM adj_factor")
        cur.executemany(
            "INSERT INTO adj_factor (ticker, ex_date, kind, factor, detail) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (ticker, ex_date, kind) DO UPDATE SET factor = EXCLUDED.factor, detail = EXCLUDED.detail",
            [(e.ticker, e.ex_date, e.kind, e.factor, e.detail) for e in events],
        )
        cur.execute("DELETE FROM price_adj")
        cur.execute(
            """
            INSERT INTO price_adj (session_date, ticker, split_factor, total_factor)
            SELECT p.session_date, p.ticker,
                   COALESCE(exp(sum(ln(e.factor)) FILTER (WHERE e.kind <> 'dividend')), 1),
                   exp(sum(ln(e.factor)))
            FROM price_bars p JOIN adj_factor e ON e.ticker = p.ticker AND e.ex_date > p.session_date
            WHERE p.close > 0
            GROUP BY p.session_date, p.ticker
            """
        )
        cur.execute("SELECT count(*) FROM price_adj")
        n_adj = cur.fetchone()[0]
    by_kind: dict[str, int] = {}
    for e in events:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
    return {"events": len(events), "by_kind": by_kind, "skipped": skipped, "price_adj_rows": n_adj}


# ---------------------------------------------------------------------------------------
# Validation: does the factor predict what the market actually did on the ex-date?
# ---------------------------------------------------------------------------------------

def validate(sample: int = 20) -> dict:
    """Realised ex-date price ratio (first close >= ex / last close before) vs the factor.
    Splits/bonus move the price by the factor (tight); a dividend moves it by about the DPS (noisy), so for
    dividends the check is that the average realised-minus-expected gap is ~0 (no sign or unit error)."""
    from statistics import mean, median

    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.ticker, e.ex_date, e.kind, e.factor::float,
                   (SELECT p.close FROM price_bars p WHERE p.ticker = e.ticker AND p.session_date >= e.ex_date AND p.close > 0
                     ORDER BY p.session_date LIMIT 1)::float AS ex_close,
                   (SELECT p.close FROM price_bars p WHERE p.ticker = e.ticker AND p.session_date < e.ex_date AND p.close > 0
                     ORDER BY p.session_date DESC LIMIT 1)::float AS prev_close
            FROM adj_factor e ORDER BY e.ex_date
            """
        )
        rows = [r for r in cur.fetchall() if r[4] and r[5]]
    out: dict = {"cases": []}
    for kind in ("split", "bonus", "dividend"):
        rs = [(tk, ex, f, xc / pc) for tk, ex, k, f, xc, pc in rows if k == kind]
        if not rs:
            out[kind] = None
            continue
        gaps = [real - f for _, _, f, real in rs]
        rel = [abs(real / f - 1) for _, _, f, real in rs]
        out[kind] = {
            "n": len(rs), "mean_gap": mean(gaps), "median_gap": median(gaps),
            "within_5pct": sum(1 for x in rel if x <= 0.05) / len(rs),
            "median_abs_rel_err": median(rel),
        }
        step = max(1, len(rs) // max(1, sample // 3))
        out["cases"] += [(kind, *r) for r in rs[::step][: max(1, sample // 3)]]
    return out


def h5_effect() -> list[tuple]:
    """How much do raw 5-bar returns differ from split-adjusted and total-return ones, by year."""
    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH x AS (
              SELECT session_date, ticker, close, close_adj, close_tr,
                     lead(close, 5)     OVER w AS c5,
                     lead(close_adj, 5) OVER w AS a5,
                     lead(close_tr, 5)  OVER w AS t5
              FROM price_bars_adj WHERE close > 0
              WINDOW w AS (PARTITION BY ticker ORDER BY session_date)
            ), r AS (
              SELECT extract(year FROM session_date)::int AS y,
                     c5 / close - 1 AS raw, a5 / close_adj - 1 AS adj, t5 / close_tr - 1 AS tr
              FROM x WHERE c5 IS NOT NULL
            )
            SELECT y, count(*),
                   round(1e4 * avg(adj - raw), 2)  AS split_bp,
                   round(1e4 * avg(tr - adj), 2)   AS dividend_bp,
                   round(100.0 * avg((abs(adj - raw) > 0.005)::int), 3) AS pct_rows_split_moved_gt50bp,
                   round(100.0 * avg((abs(tr - adj) > 0.005)::int), 3)  AS pct_rows_div_moved_gt50bp
            FROM r GROUP BY y ORDER BY y
            """
        )
        return cur.fetchall()
