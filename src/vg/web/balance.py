"""Balance position (`/balance`): who holds a stock, month-end by month-end, from the KSEI monthly report - the same panel the stock page
shows, on a page of its own with a stock picker and a choice of how many months to draw.

`load()` is the only part that touches the database (SELECT only); the picture itself is `stock.ownership_view`.
"""

from __future__ import annotations

import re

from . import stock

MONTHS = (12, 24, 36, 60)
DEFAULT_MONTHS = 36
DEFAULT_TICKER = "BBCA"
_CODE = re.compile(r"^[A-Z0-9]{4}$")


def clean_ticker(raw: str | None) -> str:
    t = (raw or "").strip().upper()
    return t if _CODE.match(t) else DEFAULT_TICKER


def clean_months(raw) -> int:
    try:
        m = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MONTHS
    return m if m in MONTHS else DEFAULT_MONTHS


def load(ticker: str, months: int) -> list[tuple]:
    from ..db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT month_end, detail FROM ownership_monthly WHERE ticker = %s AND detail IS NOT NULL "
                    "ORDER BY month_end DESC LIMIT %s", (ticker, int(months)))
        return cur.fetchall()[::-1]                                   # oldest first, as the chart wants


def build_view(rows: list[tuple], ticker: str, months: int) -> dict:
    return {"ticker": ticker, "months": months, "months_options": [{"m": m, "on": m == months} for m in MONTHS],
            "own_chart": stock.ownership_view(rows)}
