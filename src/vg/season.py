"""Day-of-week and calendar-month seasonality from price / index history.

Pure sign/mean stats over close-to-close returns. Our data starts ~2023, so a
month bucket has only ~n years of observations — read `n` before trusting the
number. Not a flow signal; a context line ("Sep has been weak for IHSG").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .db import connect

if TYPE_CHECKING:
    from datetime import date

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def _stats(rets: list[float]) -> tuple[int, float | None, float | None]:
    n = len(rets)
    if n == 0:
        return 0, None, None
    return n, sum(1 for r in rets if r > 0) / n, sum(rets) / n


def _down(rets: list[float]) -> float | None:
    """Share of observations that closed lower; the rest of `1 - pct_up - pct_down` is unchanged (common on low-priced stocks)."""
    return sum(1 for r in rets if r < 0) / len(rets) if rets else None


def day_of_week(daily: list[tuple[date, float]]) -> list[dict]:
    """daily = [(session_date, close-to-close return), ...]."""
    buckets: dict[int, list[float]] = {i: [] for i in range(5)}
    for d, r in daily:
        if d.weekday() < 5:
            buckets[d.weekday()].append(r)
    out = []
    for wd in range(5):
        n, pct_up, mean_ret = _stats(buckets[wd])
        out.append({"label": _DOW[wd], "n": n, "pct_up": pct_up, "pct_down": _down(buckets[wd]), "mean_ret": mean_ret})
    return out


def month_of_year(monthly: list[tuple[date, float]]) -> list[dict]:
    """monthly = [(any date in the month, month return), ...]."""
    buckets: dict[int, list[float]] = {i: [] for i in range(1, 13)}
    for d, r in monthly:
        buckets[d.month].append(r)
    out = []
    for mo in range(1, 13):
        n, pct_up, mean_ret = _stats(buckets[mo])
        out.append({"label": _MONTHS[mo - 1], "n": n, "pct_up": pct_up, "pct_down": _down(buckets[mo]), "mean_ret": mean_ret})
    return out


def _to_monthly(daily: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Compound daily returns within each calendar month. Drops the first and
    last (partial) months."""
    if not daily:
        return []
    months: list[tuple[date, float]] = []
    key = (daily[0][0].year, daily[0][0].month)
    acc, last = 1.0, daily[0][0]
    for d, r in daily:
        k = (d.year, d.month)
        if k != key:
            months.append((last, acc - 1))
            key, acc = k, 1.0
        acc *= 1 + r
        last = d
    months.append((last, acc - 1))
    return months[1:-1] if len(months) > 2 else []


def _load_daily(cur, ticker: str, years: int) -> list[tuple[date, float]]:
    """Daily close-to-close returns for the last `years` years of available data
    (cutoff is relative to the newest bar, not wall-clock)."""
    span = f"{int(years)} years"
    if ticker.upper() in ("COMPOSITE", "IHSG"):
        cur.execute(
            """
            SELECT session_date, close FROM index_bars
            WHERE index_code = 'COMPOSITE' AND close > 0
              AND session_date >= (SELECT max(session_date) FROM index_bars
                                   WHERE index_code = 'COMPOSITE') - %s::interval
            ORDER BY session_date
            """,
            (span,),
        )
    else:
        cur.execute(
            """
            SELECT p.session_date, p.close FROM price_bars p
            WHERE p.ticker = %s AND p.close > 0 AND COALESCE(p.volume, 0) > 0
              AND p.session_date >= (SELECT max(session_date) FROM price_bars) - %s::interval
              AND NOT EXISTS (
                  SELECT 1 FROM corp_actions ca
                  WHERE ca.ticker = p.ticker AND ca.type = 'share_count_change'
                    AND ca.ex_date BETWEEN p.session_date - 3 AND p.session_date + 3)
            ORDER BY p.session_date
            """,
            (ticker.upper(), span),
        )
    rows = cur.fetchall()
    daily: list[tuple[date, float]] = []
    for i in range(1, len(rows)):
        p0, p1 = float(rows[i - 1][1]), float(rows[i][1])
        if p0 > 0:
            daily.append((rows[i][0], p1 / p0 - 1))
    return daily


def seasonality(ticker: str, years: int = 12) -> dict:
    with connect() as conn, conn.cursor() as cur:
        daily = _load_daily(cur, ticker, years)
    monthly = _to_monthly(daily)
    return {
        "ticker": ticker.upper(),
        "years": years,
        "n_days": len(daily),
        "span": (daily[0][0].isoformat(), daily[-1][0].isoformat()) if daily else None,
        "day_of_week": day_of_week(daily),
        "month": month_of_year(monthly),
    }
