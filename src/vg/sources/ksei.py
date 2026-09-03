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
import time
import zipfile
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from ..config import CONFIG
from .base import KSEI_CATEGORIES, OwnershipFetchResult, OwnershipSnapshot, OwnershipSource

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


def _candidate_dates(month: date) -> list[date]:
    """Last weekday of `month`'s month, then walking back up to _MAX_LOOKBACK_DAYS."""
    last_day = calendar.monthrange(month.year, month.month)[1]
    d = date(month.year, month.month, last_day)
    while d.weekday() >= 5:  # Sat/Sun
        d -= timedelta(days=1)
    return [d - timedelta(days=i) for i in range(_MAX_LOOKBACK_DAYS + 1)]


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
