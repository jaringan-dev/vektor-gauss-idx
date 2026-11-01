"""EOD quality gate (ARSITEKTUR-DATA-v1 K4).

`compute` turns whatever is in the tables into signals. If the day's inputs are broken (the 14/15/25 Sep price
gap: 5 rows instead of ~962), the signals are silently wrong and — worse — get scored by `eval` later. The gate
runs between collection and compute: any BLOCK finding stops `compute` for that run and alerts; WARN findings
are reported but never stop the pipeline (secondary sources legitimately wobble).

Rules are pure functions of plain numbers (unit-tested); `load_facts` is the only DB-touching part. They reuse
the thresholds of the dashboard's freshness strip so the strip and the gate can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .web import freshness as fr

BLOCK, WARN = "BLOCK", "WARN"


@dataclass(frozen=True)
class Finding:
    level: str
    key: str
    text: str


@dataclass
class Facts:
    day: date
    price_counts: dict = field(default_factory=dict)        # date -> rows (recent window incl. `day`)
    broker_counts: dict = field(default_factory=dict)
    ohlc_violations: int = 0                                 # high<low, close outside [low,high] on traded rows
    nonpositive_close: int = 0
    negative_volume: int = 0
    open_out_of_range: int = 0
    index_rows: int = 0                                      # COMPOSITE row present for `day` (0/1)
    fallback_rows: int = 0                                   # rows on `day` from a non-primary source
    in_calendar: bool = True


def evaluate(f: Facts) -> list[Finding]:
    out: list[Finding] = []
    n = f.price_counts.get(f.day, 0)
    base = fr.baseline(f.price_counts, f.day)
    if not f.in_calendar:
        out.append(Finding(BLOCK, "calendar", f"{fr.fmt_date(f.day)} bukan hari bursa terkonfirmasi"))
    if n == 0:
        out.append(Finding(BLOCK, "price_missing", f"tidak ada baris harga untuk {fr.fmt_date(f.day)}"))
    elif base and n / base < fr.GAP_RATIO:
        out.append(Finding(BLOCK, "price_gap", f"harga bolong: {n} saham vs normal ≈ {round(base)}"))
    elif base and n / base < fr.PARTIAL_RATIO:
        out.append(Finding(WARN, "price_partial", f"harga parsial: {n} saham vs normal ≈ {round(base)}"))
    if f.ohlc_violations:
        out.append(Finding(BLOCK, "ohlc", f"{f.ohlc_violations} baris melanggar high≥low / close dalam rentang"))
    if f.nonpositive_close:
        out.append(Finding(BLOCK, "close_nonpositive", f"{f.nonpositive_close} baris close ≤ 0"))
    if f.negative_volume:
        out.append(Finding(BLOCK, "volume_negative", f"{f.negative_volume} baris volume/nilai negatif"))
    if f.open_out_of_range:
        out.append(Finding(WARN, "open_range", f"{f.open_out_of_range} baris `open` di luar [low, high]"))
    if not f.index_rows:
        out.append(Finding(WARN, "index_missing", "indeks COMPOSITE belum ada → RS vs IHSG akan kosong"))
    if f.fallback_rows:
        out.append(Finding(WARN, "fallback_source", f"{f.fallback_rows} baris dari sumber cadangan (harga mungkin disesuaikan)"))
    bn = f.broker_counts.get(f.day, 0)
    bbase = fr.baseline(f.broker_counts, f.day)
    if bbase and bn / bbase < fr.PARTIAL_RATIO:
        out.append(Finding(WARN, "broker_partial", f"broker {bn} saham vs normal ≈ {round(bbase)}"))
    return out


def blocked(findings: list[Finding]) -> bool:
    return any(x.level == BLOCK for x in findings)


def format_report(day: date, findings: list[Finding]) -> str:
    if not findings:
        return f"Gerbang kualitas {fr.fmt_date(day)}: lolos, tanpa temuan."
    head = "DIBLOKIR" if blocked(findings) else "lolos dengan peringatan"
    lines = [f"Gerbang kualitas {fr.fmt_date(day)}: {head}"]
    lines += [f"  [{x.level}] {x.key}: {x.text}" for x in findings]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------
# DB part
# ---------------------------------------------------------------------------------------

def load_facts(day: date | None = None) -> Facts:
    from datetime import timedelta

    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        if day is None:
            cur.execute("SELECT max(session_date) FROM trading_calendar")
            day = cur.fetchone()[0]
        since = day - timedelta(days=int(fr.BASELINE_DAYS * 1.6) + 7)
        cur.execute("SELECT session_date, count(*) FROM price_bars WHERE session_date BETWEEN %s AND %s GROUP BY 1", (since, day))
        price = {d: int(n) for d, n in cur.fetchall()}
        cur.execute("SELECT session_date, count(DISTINCT ticker) FROM broker_flows WHERE session_date BETWEEN %s AND %s GROUP BY 1", (since, day))
        broker = {d: int(n) for d, n in cur.fetchall()}
        cur.execute("SELECT count(*) FROM trading_calendar WHERE session_date = %s", (day,))
        in_cal = cur.fetchone()[0] > 0
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE volume > 0 AND low > 0 AND (high < low OR close < low OR close > high)),
                   count(*) FILTER (WHERE close <= 0),
                   count(*) FILTER (WHERE volume < 0 OR value < 0),
                   count(*) FILTER (WHERE volume > 0 AND open > 0 AND low > 0 AND (open < low OR open > high)),
                   count(*) FILTER (WHERE source <> 'idx_stock_summary')
            FROM price_bars WHERE session_date = %s
            """,
            (day,),
        )
        ohlc, nonpos, negv, openr, fb = (int(x or 0) for x in cur.fetchone())
        cur.execute("SELECT count(*) FROM index_bars WHERE session_date = %s AND index_code = 'COMPOSITE'", (day,))
        idx = int(cur.fetchone()[0])
    return Facts(day=day, price_counts=price, broker_counts=broker, ohlc_violations=ohlc, nonpositive_close=nonpos,
                 negative_volume=negv, open_out_of_range=openr, index_rows=idx, fallback_rows=fb, in_calendar=in_cal)
