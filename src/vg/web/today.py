"""The "Today" page: what happened in the market, and which stocks are worth a look (RENCANA-ROMBAK-UI-v3 / UX review 4 Nov 2026).

The first screen used to be a statistics table. A person choosing stocks wants three things first: how the market did, how
THEIR stocks did, and which liquid stocks had the most unusual flow. Every shortlist here is built by a rule written on the page
(liquid stocks only, ranked by one named measure); none of it is a recommendation, and the page says that no pattern has been
proven yet. `load()` is the only DB part; `build_view()` is pure.
"""

from __future__ import annotations

from datetime import date

from . import fmt
from .glossary import plain_tag, quad_key, quad_word

LIQUID_VALUE_IDR = 5_000_000_000
LIST_SIZE = 8


def _f(v):
    return None if v is None else float(v)


def load(session_date: date, watch: list[str]) -> dict:
    from ..db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT close, prev FROM index_bars WHERE index_code = 'COMPOSITE' AND session_date = %s", (session_date,))
        ihsg = cur.fetchone()
        cur.execute(
            "SELECT count(*) FILTER (WHERE close > prev), count(*) FILTER (WHERE close < prev), count(*) FILTER (WHERE close = prev), "
            "sum(value) FROM price_bars WHERE session_date = %s AND prev > 0 AND close > 0", (session_date,))
        breadth = cur.fetchone()
        cur.execute(
            "SELECT sum(sc.nff_d0), sum(sc.nff_d0) FILTER (WHERE pb.value >= %s) FROM signal_components sc "
            "JOIN price_bars pb ON pb.session_date = sc.session_date AND pb.ticker = sc.ticker "
            "WHERE sc.session_date = %s AND sc.nff_d0 IS NOT NULL", (LIQUID_VALUE_IDR, session_date))
        foreign = cur.fetchone()

        base = """
            SELECT pb.ticker, pb.close, pb.prev, pb.value, sc.nff_d0, sc.nff_w1, sc.quadrant, sc.foreign_consistency_10d,
                   sc.bkr_foreign_net_lo, sc.bkr_foreign_net_hi,
                   (SELECT count(*) FROM signal_log sl WHERE sl.session_date = pb.session_date AND sl.ticker = pb.ticker) AS n_tags
            FROM price_bars pb LEFT JOIN signal_components sc ON sc.session_date = pb.session_date AND sc.ticker = pb.ticker
            WHERE pb.session_date = %s
        """
        watch_rows = []
        if watch:
            cur.execute(base + " AND pb.ticker = ANY(%s)", (session_date, watch))
            by = {r[0]: r for r in cur.fetchall()}
            watch_rows = [by[t] for t in watch if t in by]
        liquid = base + " AND pb.value >= %s AND sc.nff_w1 IS NOT NULL ORDER BY sc.nff_w1 {order} LIMIT %s"
        cur.execute(liquid.format(order="DESC"), (session_date, LIQUID_VALUE_IDR, LIST_SIZE))
        inflow = cur.fetchall()
        cur.execute(liquid.format(order="ASC"), (session_date, LIQUID_VALUE_IDR, LIST_SIZE))
        outflow = cur.fetchall()
        certain = {}
        for tag in ("BROKER2-ASING-AKUM", "BROKER2-ASING-DIST"):
            cur.execute(base + " AND pb.ticker IN (SELECT ticker FROM signal_log WHERE session_date = %s AND tag = %s) "
                        "ORDER BY pb.value DESC NULLS LAST LIMIT %s", (session_date, session_date, tag, LIST_SIZE))
            certain[tag] = cur.fetchall()
            cur.execute("SELECT count(*) FROM signal_log WHERE session_date = %s AND tag = %s", (session_date, tag))
            certain[tag + ":n"] = cur.fetchone()[0]
    return {"session_date": session_date, "ihsg": ihsg, "breadth": breadth, "foreign": foreign, "watch": watch_rows,
            "inflow": inflow, "outflow": outflow, "certain": certain}


def _row(r) -> dict:
    tk, close, prev, value, d0, w1, quad, cons, lo, hi, n_tags = r
    close, prev, value = _f(close), _f(prev), _f(value)
    chg = (close / prev - 1) if (close and prev) else None
    bounds = None
    if lo is not None and hi is not None and value:
        bounds = f"{fmt.pct(_f(lo) / value, 1)} to {fmt.pct(_f(hi) / value, 1)}"
    return {"ticker": tk, "price": fmt.price(close), "chg": fmt.cell(chg, 1), "value": fmt.idr(value).lstrip("+"),
            "d0": fmt.idr(_f(d0)), "d0_c": fmt.signcls(_f(d0)), "w1": fmt.idr(_f(w1)), "w1_c": fmt.signcls(_f(w1)),
            "quad": quad, "quad_key": quad_key(quad), "quad_word": quad_word(quad),
            "cons": f"{int(cons)}/10" if cons is not None else "—", "n_tags": n_tags or 0, "bounds": bounds}


def market_sentences(raw: dict) -> list[str]:
    out = []
    ihsg = raw["ihsg"]
    if ihsg and ihsg[0] and ihsg[1]:
        c, p = float(ihsg[0]), float(ihsg[1])
        out.append(f"The Jakarta Composite closed at {c:,.0f}, {'up' if c >= p else 'down'} {abs(c / p - 1) * 100:.2f}%.")
    b = raw["breadth"]
    if b and b[0] is not None:
        out.append(f"{b[0]:,} stocks rose, {b[1]:,} fell and {b[2]:,} were unchanged; total traded value was {fmt.money_words(_f(b[3]))}.")
    fr = raw["foreign"]
    if fr and fr[0] is not None:
        side = "net buyers" if float(fr[0]) >= 0 else "net sellers"
        liquid = ""
        if fr[1] is not None:
            liquid = f" (in liquid stocks alone: {'net buyers' if float(fr[1]) >= 0 else 'net sellers'} of {fmt.money_words(_f(fr[1]))})"
        out.append(f"Foreign investors were {side} of {fmt.money_words(_f(fr[0]))} across the market{liquid}.")
    return out


def build_view(raw: dict, track: dict | None = None) -> dict:
    """`track`: {"n": patterns tracked, "passed": ..., "probation": ..., "failing": ...} from the forward-test rows, or None."""
    return {
        "session_date": raw["session_date"].isoformat(), "market": market_sentences(raw),
        "watch": [_row(r) for r in raw["watch"]], "inflow": [_row(r) for r in raw["inflow"]], "outflow": [_row(r) for r in raw["outflow"]],
        "certain": {"buy": [_row(r) for r in raw["certain"].get("BROKER2-ASING-AKUM", [])], "buy_n": raw["certain"].get("BROKER2-ASING-AKUM:n", 0),
                    "sell": [_row(r) for r in raw["certain"].get("BROKER2-ASING-DIST", [])], "sell_n": raw["certain"].get("BROKER2-ASING-DIST:n", 0)},
        "track": track, "liquid_bn": LIQUID_VALUE_IDR // 1_000_000_000,
        "tag_name": {t: plain_tag(t) for t in ("BROKER2-ASING-AKUM", "BROKER2-ASING-DIST")},
    }
