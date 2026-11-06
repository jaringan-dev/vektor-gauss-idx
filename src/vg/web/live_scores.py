"""Broker scores computed on the fly, for sessions whose scores are not stored yet.

`signal_components` holds the Bandar Power Index and friends only for sessions the engine has scored. The 44 sessions of the
forward-test window (4 Sep - 5 Nov 2026) were deliberately left unscored: scoring them means running `vg compute` over them,
which also rewrites their tags and would add `AKUM-NETRAL-WASH` signals after the fact, into a record whose whole value is that
nothing in it is written in hindsight. The numbers themselves need none of that: they are a pure function of rows that are
already in the database (`broker_levels` ladders and `price_bars.value`).

So the dashboard computes them here, with the engine's own pure functions (`bandar_power`, `score_ticker`), and shows them
marked "live". Nothing is written: no INSERT, no UPDATE, no tag. The wash flag stays a stored tag and is never drawn from a live
number; only the shared-houses count (which a live number does give) is shown.

`load_ladders` / `live_rows` are the database parts (SELECT only); `fill_rows` is pure.
"""

from __future__ import annotations

from datetime import date

from ..engine.scoring import SCORING_COLUMNS, bandar_power, score_ticker


def _f(v):
    return None if v is None else float(v)


def load_ladders(cur, session_date: date) -> dict[str, tuple[list, list]]:
    """{ticker: (buy ladder, sell ladder)} for one session, each a list of (broker_code, gross value). One query for the whole market."""
    cur.execute("SELECT ticker, side, broker_code, val FROM broker_levels WHERE session_date = %s", (session_date,))
    out: dict[str, tuple[list, list]] = {}
    for ticker, side, code, val in cur.fetchall():
        buys, sells = out.setdefault(ticker, ([], []))
        (buys if side == "B" else sells).append((code, _f(val)))
    return out


def fill_rows(rows: list[dict], ladders: dict[str, tuple[list, list]]) -> int:
    """For every row without a stored BPI, compute it from the ladders and the day's traded value. Sets `bpi3`, `bpi5`, `bpi_overlap3`
    and `bpi_live=True` on the row; a row without a usable ladder is left as it is (N/A). Returns how many rows were filled."""
    n = 0
    for r in rows:
        if r.get("bpi3") is not None or r["ticker"] not in ladders:
            continue
        b = bandar_power(*ladders[r["ticker"]], r.get("value"))
        if b is None:
            continue
        r["bpi3"], r["bpi5"], r["bpi_overlap3"], r["bpi_live"] = b.bpi3, b.bpi5, b.overlap3, True
        n += 1
    return n


def score_values(day: date, ticker: str, cur) -> tuple | None:
    """One ticker-day as a row shaped like the stored ones: (session_date, *SCORING_COLUMNS). None when no measure could be computed."""
    s = score_ticker(cur, ticker, day)
    vals = {c: None for c in SCORING_COLUMNS}
    if s.bandar is not None:
        vals.update(bpi3=s.bandar.bpi3, bpi5=s.bandar.bpi5, bpi_overlap3=s.bandar.overlap3)
    if s.aggressor is not None:
        vals.update(aggr_haka_share=s.aggressor.haka_share, aggr_ratio=s.aggressor.ratio, aggr_coverage=s.aggressor.coverage)
    if s.divergence is not None:
        vals.update(retail_net_lo=s.divergence.retail[0], retail_net_hi=s.divergence.retail[1],
                    finst_net_lo=s.divergence.foreign_institutions[0], finst_net_hi=s.divergence.foreign_institutions[1],
                    retail_div_state=s.divergence.state)
    row = tuple(vals[c] for c in SCORING_COLUMNS)
    return (day, *row) if any(v is not None for v in row) else None


def live_rows(cur, ticker: str, days: list[date]) -> list[tuple]:
    """Computed rows for the sessions in `days` that the engine has not stored, newest first. SELECT only."""
    out = []
    for d in sorted(days, reverse=True):
        row = score_values(d, ticker, cur)
        if row is not None:
            out.append(row)
    return out
