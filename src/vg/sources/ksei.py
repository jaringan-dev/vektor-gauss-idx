"""KSEI monthly ownership composition source (Tier 0).

File (verified 2026-09-03):
    https://web.ksei.co.id/Download/BalanceposEfek<YYYYMMDD>.zip
    -> single member  Balancepos<YYYYMMDD>.txt  (pipe-delimited, header row)

<YYYYMMDD> is the last IDX trading day of the month. Usually the last weekday,
but shifts for year-end / public holidays (e.g. Dec 2024 -> 20241230, not 31).
So fetch() tries the last weekday and walks back up to 6 days.

Columns (25, positional — 'Total' appears twice so parse by index):
  0 Date  1 Code  2 Type  3 Sec.Num  4 Price
  5-13  Local IS CP PF IB ID MF SC FD OT      14 Local Total
  15-23 Foreign IS CP PF IB ID MF SC FD OT    24 Foreign Total

Values are SHARE COUNTS. `holders_count` is NOT in this file (left NULL; comes
from Sectors `shareholders-composition` or another KSEI dataset later).

Data available from ~2018 onwards at predictable URLs.
"""

from __future__ import annotations

import calendar
import io
import random
import re
import time
import zipfile
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

import httpx

from ..config import CONFIG
from .base import (
    KSEI_CATEGORIES,
    CaFetchResult,
    CorpAction,
    CorpActionSource,
    OwnershipFetchResult,
    OwnershipSnapshot,
    OwnershipSource,
)

_BASE = "https://web.ksei.co.id/Download"
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_LOCAL_START = 5   # first "Local IS" column index
_FOREIGN_START = 15
_LOCAL_TOTAL = 14
_FOREIGN_TOTAL = 24
_MAX_LOOKBACK_DAYS = 9  # covers a week-long Eid/year-end holiday + weekend at month-end


def _to_decimal(v: str) -> Decimal | None:
    v = v.strip()
    if not v:
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def _parse_ksei_date(v: str) -> date | None:
    # "31-AUG-2026"
    try:
        d, mon, y = v.strip().upper().split("-")
        return date(int(y), _MONTHS[mon], int(d))
    except (ValueError, KeyError):
        return None


# -- Corporate-action schedule -------------------------------------------------

_CA_BASE = "https://web.ksei.co.id/Download"

# normalized type -> matched if the raw 'Type of CA' contains this substring
_CA_TYPE_MAP: list[tuple[str, str]] = [
    ("reverse_split", "REVERSE"),
    ("stock_split", "SPLIT"),
    ("stock_dividend", "STOCK DIVIDEND"),
    ("bonus", "BONUS"),
    ("rights", "RIGHT"),
    ("warrant_exercise", "WARRANT"),
    ("merger", "MERGER"),
    ("tender_offer", "TENDER"),
    ("cash_dividend", "CASH DIVIDEND"),
    ("cash_dividend", "DIVIDEND"),
]
# Types that change price / share count (or matter for equities). Bond noise
# (INTEREST, REDEMPTION, MANDATORY CONVERSION, PROXY VOTING) is dropped.
_CA_KEEP = {
    "reverse_split", "stock_split", "stock_dividend", "bonus", "rights",
    "warrant_exercise", "merger", "tender_offer", "cash_dividend",
}


def _normalize_ca_type(raw: str) -> str:
    up = raw.strip().upper()
    for norm, needle in _CA_TYPE_MAP:
        if needle in up:
            return norm
    return re.sub(r"[^a-z0-9]+", "_", up.lower()).strip("_")


class _KseiCaTableParser(HTMLParser):
    """Extracts <tr id=isi> rows from KSEI's Excel-flavoured HTML .xls files."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_row = False
        self._in_cell = False
        self._cell: list[str] = []
        self._row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs):  # noqa: ANN001
        if tag == "tr" and dict(attrs).get("id") == "isi":
            self._in_row, self._row = True, []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell, self._cell = True, []

    def handle_endtag(self, tag: str):
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._row.append(" ".join("".join(self._cell).split()))
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._row:
                self.rows.append(self._row)

    def handle_data(self, data: str):
        if self._in_cell:
            self._cell.append(data)


class KseiCaScheduleSource(CorpActionSource):
    """KSEI schedule_ca_keseluruhan_<year>.xls — the master CA list (HTML table).

    Columns (15): No | Issuer | Security Code | Security Name | Type of CA |
      Cum Date | Recording Date | Distribution Date | Exercise Ratio |
      Exercise Intrument | Proceed Ratio | Proceed Instrument | Description |
      Refference No | Letter Date

    NOTE: this file does NOT contain stock splits (KSEI omits them). Splits come
    from the derived_from_price_bars detector — see vg.collect.
    """

    name = "ksei_ca_schedule"
    tier = 0

    def __init__(self, *, max_retries: int = 3) -> None:
        self.max_retries = max_retries

    def _download(self, year: int) -> bytes | None:
        url = f"{_CA_BASE}/schedule_ca_keseluruhan_{year}.xls"
        headers = {
            "User-Agent": CONFIG.http_user_agent,
            "Accept": "text/html, application/vnd.ms-excel, */*",
            "Referer": "https://web.ksei.co.id/data/download-data-and-user-guide",
        }
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=CONFIG.http_timeout, headers=headers) as c:
                    resp = c.get(url, follow_redirects=True)
                if resp.status_code == 404:
                    return None
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("5xx", request=resp.request, response=resp)
                resp.raise_for_status()
                return resp.content
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt + random.uniform(0, 1))
        raise RuntimeError(f"KSEI CA download failed for {url}: {last_exc}")

    @staticmethod
    def _parse(html_bytes: bytes) -> list[CorpAction]:
        parser = _KseiCaTableParser()
        parser.feed(html_bytes.decode("latin-1", errors="replace"))
        out: list[CorpAction] = []
        for r in parser.rows:
            if len(r) < 8:
                continue
            raw_type = r[4]
            ca_type = _normalize_ca_type(raw_type)
            if ca_type not in _CA_KEEP:
                continue
            cum = _parse_ksei_date(r[5])
            recording = _parse_ksei_date(r[6])
            ex = recording or cum
            code = r[2].strip()
            if not code or ex is None:
                continue
            out.append(
                CorpAction(
                    ticker=code,
                    ca_type=ca_type,
                    raw_type=raw_type,
                    ex_date=ex,
                    cum_date=cum,
                    distribution_date=_parse_ksei_date(r[7]),
                    exercise_ratio=(r[8].strip() or None) if len(r) > 8 else None,
                    proceed_ratio=(r[10].strip() or None) if len(r) > 10 else None,
                    issuer=r[1].strip() or None,
                    notes=(r[12].strip() or None) if len(r) > 12 else None,
                )
            )
        return out

    def fetch(self, year: int) -> CaFetchResult:
        try:
            raw = self._download(year)
        except Exception as exc:
            return CaFetchResult(
                status="error", year=year, source=self.name, source_tier=self.tier,
                detail=f"{type(exc).__name__}: {exc}",
            )
        if raw is None:
            return CaFetchResult(
                status="no_data", year=year, source=self.name, source_tier=self.tier,
                detail=f"no schedule_ca_keseluruhan_{year}.xls",
            )
        rows = self._parse(raw)
        return CaFetchResult(
            status="ok" if rows else "no_data", year=year, source=self.name,
            source_tier=self.tier, raw_bytes=raw, rows=rows,
            detail="" if rows else "file downloaded but no equity CA rows parsed",
        )


def _candidate_dates(month: date, today: date | None = None) -> list[date]:
    """Last weekday of `month`'s month, then walking back up to _MAX_LOOKBACK_DAYS.
    Dates after `today` are dropped: a file for a day that has not happened cannot exist, and asking
    KSEI for one has drawn a 5xx (4 Nov, probing November), which was logged as a collector error."""
    last_day = calendar.monthrange(month.year, month.month)[1]
    d = date(month.year, month.month, last_day)
    while d.weekday() >= 5:  # Sat/Sun
        d -= timedelta(days=1)
    cutoff = today or date.today()
    return [c for c in (d - timedelta(days=i) for i in range(_MAX_LOOKBACK_DAYS + 1)) if c <= cutoff]


class KseiOwnershipSource(OwnershipSource):
    name = "ksei_balancepos"
    tier = 0

    def __init__(self, *, max_retries: int = 3) -> None:
        self.max_retries = max_retries

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": CONFIG.http_user_agent,
            "Accept": "application/zip, application/octet-stream, */*",
            "Referer": "https://web.ksei.co.id/archive_download/holding_composition",
        }

    def _try_download(self, d: date) -> bytes | None:
        """GET one candidate URL. Returns bytes on 200, None on 404, raises on 5xx
        after retries (so the caller can decide to move on)."""
        url = f"{_BASE}/BalanceposEfek{d:%Y%m%d}.zip"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=CONFIG.http_timeout, headers=self._headers()) as c:
                    resp = c.get(url, follow_redirects=True)
                if resp.status_code == 404:
                    return None
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("5xx", request=resp.request, response=resp)
                resp.raise_for_status()
                if not resp.content.startswith(b"PK"):
                    return None  # got an HTML error page, not a zip
                return resp.content
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt + random.uniform(0, 1))
        raise RuntimeError(f"KSEI download failed for {url}: {last_exc}")

    @staticmethod
    def _parse(zip_bytes: bytes) -> tuple[date | None, list[OwnershipSnapshot]]:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
            text = zf.read(name).decode("utf-8", errors="replace")

        rows: list[OwnershipSnapshot] = []
        snap_date: date | None = None
        for line in text.splitlines():
            if not line or line.startswith("Date|"):
                continue
            f = line.split("|")
            if len(f) < 25 or f[2].strip() != "EQUITY":
                continue
            if snap_date is None:
                snap_date = _parse_ksei_date(f[0])
            local = {
                cat: _to_decimal(f[_LOCAL_START + i]) for i, cat in enumerate(KSEI_CATEGORIES)
            }
            local["TOTAL"] = _to_decimal(f[_LOCAL_TOTAL])
            foreign = {
                cat: _to_decimal(f[_FOREIGN_START + i]) for i, cat in enumerate(KSEI_CATEGORIES)
            }
            foreign["TOTAL"] = _to_decimal(f[_FOREIGN_TOTAL])
            rows.append(
                OwnershipSnapshot(
                    ticker=f[1].strip(),
                    price=_to_decimal(f[4]),
                    sec_num=_to_decimal(f[3]),
                    local=local,
                    foreign=foreign,
                )
            )
        return snap_date, rows

    def fetch(self, month: date) -> OwnershipFetchResult:
        try:
            raw: bytes | None = None
            for d in _candidate_dates(month):
                raw = self._try_download(d)
                if raw is not None:
                    break
        except Exception as exc:  # 5xx exhausted etc.
            return OwnershipFetchResult(
                status="error", month_end=None, source=self.name, source_tier=self.tier,
                detail=f"{type(exc).__name__}: {exc}",
            )

        if raw is None:
            return OwnershipFetchResult(
                status="no_data", month_end=None, source=self.name, source_tier=self.tier,
                detail=f"no BalanceposEfek file for {month:%Y-%m} (tried last {_MAX_LOOKBACK_DAYS + 1} days)",
            )

        snap_date, rows = self._parse(raw)
        if not rows or snap_date is None:
            return OwnershipFetchResult(
                status="error", month_end=snap_date, source=self.name, source_tier=self.tier,
                raw_bytes=raw,
                detail="no EQUITY rows parsed" if not rows else "could not parse snapshot date",
            )
        return OwnershipFetchResult(
            status="ok", month_end=snap_date, source=self.name, source_tier=self.tier,
            raw_bytes=raw, rows=rows,
        )
