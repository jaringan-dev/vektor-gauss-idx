"""Seasonality (`/seasonality`): for one stock (or the index, `COMPOSITE`) how often each weekday and each calendar month closed up, and
the average return, over the last 5 / 10 / 12 / 15 years that the database actually holds.

It is a description of the past, not a flow signal and not a forecast; a month has one observation per year, so the table prints how
many it rests on and marks the thin ones. The maths is `vg.season` (the same as `vg season`); this module only loads and shapes it.
`load()` touches the database (SELECT only); `clean_ticker`, `clean_years` and `build_view` are pure and unit-tested.
"""

from __future__ import annotations

import re

from . import fmt

YEARS = (5, 10, 12, 15)
DEFAULT_YEARS = 12
DEFAULT_TICKER = "COMPOSITE"
THIN_N = 5                       # fewer observations than this and the cell is marked "thin"
_CODE = re.compile(r"^[A-Z0-9]{4}$")


def clean_ticker(raw: str | None) -> str:
    """A four-character stock code or the index; anything else (empty, too long, odd characters) falls back to the index."""
    t = (raw or "").strip().upper()
    if t in ("COMPOSITE", "IHSG"):
        return DEFAULT_TICKER
    return t if _CODE.match(t) else DEFAULT_TICKER


def clean_years(raw) -> int:
    try:
        y = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_YEARS
    return y if y in YEARS else DEFAULT_YEARS


def load(ticker: str, years: int) -> dict:
    from .. import season

    return season.seasonality(ticker, years)


def _cells(rows: list[dict]) -> list[dict]:
    """Display cells for one table (weekdays or months); the heat of each average return is relative to the largest in that table."""
    top = max((abs(r["mean_ret"]) for r in rows if r.get("mean_ret") is not None), default=0.0)
    out = []
    for r in rows:
        n, up, mean = r.get("n") or 0, r.get("pct_up"), r.get("mean_ret")
        down = r.get("pct_down")
        flat = None if up is None or down is None else max(0.0, 1.0 - up - down)
        out.append({
            "label": r["label"], "n": n, "thin": 0 < n < THIN_N, "empty": n == 0,
            "up": "—" if up is None else fmt.pct_plain(up, 1),
            "down": "—" if down is None else fmt.pct_plain(down, 1),
            "flat": "—" if flat is None else fmt.pct_plain(flat, 1),
            "exp": "—" if mean is None else fmt.pct(mean, 2),
            "cls": "" if mean is None else fmt.signcls(mean) if abs(mean) > 0 else "",
            "heat": 0.0 if (mean is None or not top) else round(min(1.0, abs(mean) / top), 2),
            "up_cls": "" if up is None or up == 0.5 else ("pos" if up > 0.5 else "neg"),
        })
    return out


def build_view(res: dict, ticker: str, years: int) -> dict:
    """Everything the template needs. `res` is `season.seasonality()`'s answer."""
    span = res.get("span")
    n_days = res.get("n_days") or 0
    covered = None
    if span:
        from datetime import date

        a, b = date.fromisoformat(span[0]), date.fromisoformat(span[1])
        covered = round((b - a).days / 365.25, 1)
    short = covered is not None and covered < years - 0.5
    return {
        "ticker": ticker, "years": years, "years_options": [{"y": y, "on": y == years} for y in YEARS],
        "has_data": n_days > 0, "n_days": n_days, "span": span, "covered": covered,
        "short": short, "dow": _cells(res.get("day_of_week") or []), "month": _cells(res.get("month") or []),
        "is_index": ticker == DEFAULT_TICKER,
    }
