"""Data-freshness assessment for the global FreshnessStrip (checklist A2, RANCANGAN-UI-UX-v2 §2.2).

Why it exists: the 14/15/25 Sep price gap (5 rows instead of 962) and the drop of daily broker
coverage went unnoticed until found by accident. This turns "is today's data healthy?" into a
strip shown on every screen: EOD date · prices · broker · KSEI, each ok / warn / bad.

Pure and DB-free (`assess`/`build_strip`) so the rules are unit-tested; `load_inputs()` is the only
part that touches Postgres and imports its driver lazily.

Rules (all relative to what is NORMAL for that source, never absolute):
  prices : count on the latest day vs the median of the previous 60 days -> ok / partial / gap
  broker : at/above BROKER_MIN_NAMES stocks it is healthy; below it, relative to normal like prices, capped at WARN
           (secondary source; coverage legitimately varies, ~200 liquid names is the design)
  EOD    : weekdays elapsed since the newest trading day (holidays can cause one false WARN)
  KSEI   : monthly file; age of the newest month-end vs the latest EOD
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

OK, WARN, BAD = "ok", "warn", "bad"
_RANK = {OK: 0, WARN: 1, BAD: 2}
_SYMBOL = {OK: "✓", WARN: "⚠", BAD: "✖"}
_WORD = {OK: "normal", WARN: "attention", BAD: "problem"}

PARTIAL_RATIO = 0.9     # below this share of the normal count  -> "partial"
GAP_RATIO = 0.5         # below this share                      -> "gap"
BASELINE_DAYS = 60      # previous trading days that define "normal". Deliberately long: with 20 a
                        # sag lasting ~10 days BECAME the median and the warning vanished silently.
KSEI_WARN_DAYS = 45     # age of the newest KSEI month-end vs the latest EOD
KSEI_BAD_DAYS = 60
BROKER_MIN_NAMES = 150  # broker feed covers ~200 liquid names by design: at/above this it is healthy, whatever the older days looked like

_BULAN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt_date(d: date | None) -> str:
    return f"{d.day} {_BULAN[d.month - 1]} {d.year}" if d else "—"


def fmt_month(d: date | None) -> str:
    return f"{_BULAN[d.month - 1]} {d.year}" if d else "—"


def weekdays_in(after: date, upto: date) -> int:
    """Number of weekdays d with after < d <= upto (0 when upto <= after)."""
    n, d = 0, after + timedelta(days=1)
    while d <= upto:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def worst(levels) -> str:
    return max(levels, key=_RANK.__getitem__, default=OK)


@dataclass(frozen=True)
class FreshItem:
    key: str
    label: str
    text: str
    level: str

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "text": self.text, "level": self.level,
                "symbol": _SYMBOL[self.level], "word": _WORD[self.level]}


@dataclass
class FreshInputs:
    today: date
    eod_date: date | None                       # newest day in trading_calendar
    price_counts: dict = field(default_factory=dict)    # date -> rows in price_bars
    broker_counts: dict = field(default_factory=dict)   # date -> distinct tickers in broker_flows
    ksei_month_end: date | None = None


def baseline(counts: dict, latest: date) -> float | None:
    """Median of the previous BASELINE_DAYS days' counts (the latest day itself excluded)."""
    prev = [counts[d] for d in sorted(counts) if d < latest][-BASELINE_DAYS:]
    return float(median(prev)) if prev else None


def _count_item(key: str, label: str, counts: dict, eod: date | None, *, cap: str = BAD, floor: int | None = None) -> FreshItem:
    def capped(level: str) -> str:
        return level if _RANK[level] <= _RANK[cap] else cap

    if not counts:
        return FreshItem(key, label, "no data yet", capped(BAD))
    latest = max(counts)
    n = counts[latest]
    if eod and latest < eod:
        lag = weekdays_in(latest, eod)
        return FreshItem(key, label, f"up to {fmt_date(latest)} — {lag} trading days behind",
                         capped(WARN if lag <= 2 else BAD))
    if floor is not None and n >= floor:
        return FreshItem(key, label, f"{n} stocks", OK)
    base = baseline(counts, latest)
    if base:
        ratio = n / base
        if ratio < GAP_RATIO:
            return FreshItem(key, label, f"{n} stocks — gap (normal ≈ {round(base)})", capped(BAD))
        if ratio < PARTIAL_RATIO:
            return FreshItem(key, label, f"{n} stocks — partial (normal ≈ {round(base)})", capped(WARN))
    return FreshItem(key, label, f"{n} stocks", OK)


def _eod_item(eod: date | None, today: date) -> FreshItem:
    if eod is None:
        return FreshItem("eod", "EOD", "no data yet", BAD)
    # weekdays that have fully passed since the newest trading day (today is not yet due)
    lag = weekdays_in(eod, today - timedelta(days=1))
    if lag <= 0:
        return FreshItem("eod", "EOD", fmt_date(eod), OK)
    if lag == 1:
        return FreshItem("eod", "EOD", f"{fmt_date(eod)} — 1 trading day behind (holiday?)", WARN)
    return FreshItem("eod", "EOD", f"{fmt_date(eod)} — {lag} trading days behind", BAD)


def _ksei_item(month_end: date | None, eod: date | None, today: date) -> FreshItem:
    if month_end is None:
        return FreshItem("ksei", "KSEI", "no data yet", BAD)
    age = ((eod or today) - month_end).days
    if age <= KSEI_WARN_DAYS:
        return FreshItem("ksei", "KSEI", fmt_month(month_end), OK)
    if age <= KSEI_BAD_DAYS:
        return FreshItem("ksei", "KSEI", f"{fmt_month(month_end)} — {age} days", WARN)
    return FreshItem("ksei", "KSEI", f"{fmt_month(month_end)} — {age} days, stale", BAD)


def build_strip(inp: FreshInputs) -> list[FreshItem]:
    return [
        _eod_item(inp.eod_date, inp.today),
        _count_item("price", "Prices", inp.price_counts, inp.eod_date),
        _count_item("broker", "Broker", inp.broker_counts, inp.eod_date, cap=WARN, floor=BROKER_MIN_NAMES),
        _ksei_item(inp.ksei_month_end, inp.eod_date, inp.today),
    ]


def load_inputs(today: date | None = None) -> FreshInputs:
    """The only DB-touching part: four small, indexed queries (~tens of ms)."""
    from ..db import connect  # lazy: keeps this module importable without the Postgres driver

    today = today or date.today()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(session_date) FROM trading_calendar")
        eod = cur.fetchone()[0]
        # BASELINE_DAYS trading days ~ 1.45x as many calendar days, plus a margin for holidays
        since = (eod or today) - timedelta(days=int(BASELINE_DAYS * 1.6) + 7)
        cur.execute("SELECT session_date, count(*) FROM price_bars WHERE session_date >= %s GROUP BY 1", (since,))
        price = {d: int(n) for d, n in cur.fetchall()}
        cur.execute(
            "SELECT session_date, count(DISTINCT ticker) FROM broker_flows WHERE session_date >= %s GROUP BY 1",
            (since,),
        )
        broker = {d: int(n) for d, n in cur.fetchall()}
        cur.execute("SELECT max(month_end) FROM ownership_monthly")
        ksei = cur.fetchone()[0]
    return FreshInputs(today=today, eod_date=eod, price_counts=price, broker_counts=broker, ksei_month_end=ksei)
