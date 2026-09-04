"""IDX Trading Summary sources (Tier 0).

Endpoints (verified 2026-09-03 via idx.co.id devtools):
    GET {base}/primary/TradingSummary/GetStockSummary?length=9999&start=0&date=YYYYMMDD
    GET {base}/primary/TradingSummary/GetIndexSummary?length=9999&start=0&date=YYYYMMDD

- Omitting `date` returns the latest published trading day.
- Response: {"draw":0,"recordsTotal":N,"recordsFiltered":N,"data":[{...}]}
- Stock: ~963 rows. Index: ~45 rows ('COMPOSITE' = IHSG).
- The site sits behind Cloudflare. A datacenter IP may get a 403 / HTML
  challenge page — see README ("Cloudflare"). This client uses a realistic
  browser identity and low request rate only.

UNVERIFIED, flag before trusting:
- Whether ForeignBuy/ForeignSell are all-boards or regular-only.
- Non-trading day vs unpublished EOD — both handled as "date_mismatch" / "no_data".
"""

from __future__ import annotations

import contextlib
import json
import random
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

try:  # optional but strongly recommended — see _idx_get
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover
    curl_requests = None

from ..config import CONFIG
from .base import (
    IndexBar,
    IndexFetchResult,
    IndexSource,
    PriceBar,
    PriceFetchResult,
    PriceSource,
)

_STOCK_PATH = "/primary/TradingSummary/GetStockSummary"
_INDEX_PATH = "/primary/TradingSummary/GetIndexSummary"
_ANNOUNCE_PATH = "/primary/ListedCompany/GetAnnouncement"
_REFERER = "/en/market-data/trading-summary/"
_ANNOUNCE_REFERER = "/en/listed-companies/company-announcements"

# Cloudflare sits in front of www.idx.co.id. A plain httpx/OpenSSL request to the
# /primary/ JSON endpoint draws a "Just a moment..." challenge that never clears.
# What works, from an Indonesian *residential* IP: curl_cffi impersonating Chrome
# (matching TLS/JA3 fingerprint) — fetch the HTML page first to bank the __cf_bm
# cookie, then the JSON endpoint on the same session. A datacenter/VPS IP is
# hard-blocked (HTTP 1020) no matter the client; run the collector from home.
# Without curl_cffi installed we fall back to httpx priming (works only if the IP
# is already warm) and otherwise raise IdxAccessBlocked.
_WARMUP_PATHS = {
    _STOCK_PATH: "/en/market-data/trading-summary/stock-summary",
    _INDEX_PATH: "/en/market-data/trading-summary/index-summary",
    _ANNOUNCE_PATH: _ANNOUNCE_REFERER,
}
_IMPERSONATE = "chrome"

# IDX response field -> PriceBar attribute
FIELD_MAP: dict[str, str] = {
    "Previous": "prev",
    "OpenPrice": "open",
    "FirstTrade": "first_trade",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Change": "change",
    "Volume": "volume",
    "Value": "value",
    "Frequency": "frequency",
    "ForeignBuy": "foreign_buy_vol",
    "ForeignSell": "foreign_sell_vol",
    "NonRegularVolume": "nonreg_volume",
    "NonRegularValue": "nonreg_value",
    "NonRegularFrequency": "nonreg_frequency",
    "ListedShares": "listed_shares",
    "TradebleShares": "tradable_shares",  # sic — IDX spelling
    "WeightForIndex": "weight_for_index",
    "IndexIndividual": "index_individual",
    "Bid": "bid",
    "BidVolume": "bid_volume",
    "Offer": "offer",
    "OfferVolume": "offer_volume",
}


class IdxAccessBlocked(RuntimeError):
    """Raised when IDX/Cloudflare rejects the request (403 or challenge HTML)."""


def _to_decimal(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v).date()
    except ValueError:
        return None


def _is_json_ok(status: int, content_type: str, body: bytes) -> bool:
    """True if this is the JSON API response, not a Cloudflare challenge/block."""
    if status != 200:
        return False
    if "json" in content_type.lower():
        return True
    return b"<html" not in body[:2048].lower()


_PAGE_HEADERS = {
    "User-Agent": CONFIG.http_user_agent,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

# One curl_cffi session reused across every _idx_get in the process: warm the
# __cf_bm cookie once, not once per backfilled day (halves the load on IDX).
_cffi_session = None
_cffi_warm = False


def _fetch_curl_cffi(warmup_url: str, api_url: str, params: dict[str, str],
                     api_headers: dict[str, str], *, warm: bool) -> tuple[int, str, bytes]:
    global _cffi_session, _cffi_warm
    if _cffi_session is None:
        _cffi_session = curl_requests.Session(impersonate=_IMPERSONATE, timeout=CONFIG.http_timeout)
    if warm or not _cffi_warm:
        with contextlib.suppress(Exception):  # warmup is best-effort
            _cffi_session.get(warmup_url, headers=_PAGE_HEADERS)
        _cffi_warm = True
        time.sleep(1.0 + random.uniform(0, 0.5))
    resp = _cffi_session.get(api_url, params=params, headers=api_headers)
    return resp.status_code, resp.headers.get("content-type", ""), resp.content


def _fetch_httpx(warmup_url: str, api_url: str, params: dict[str, str],
                 api_headers: dict[str, str], *, warm: bool) -> tuple[int, str, bytes]:
    with httpx.Client(timeout=CONFIG.http_timeout, follow_redirects=True) as client:
        with contextlib.suppress(httpx.TransportError):
            client.get(warmup_url, headers=_PAGE_HEADERS)
        time.sleep(1.0 + random.uniform(0, 0.5))
        resp = client.get(api_url, params=params, headers=api_headers)
    return resp.status_code, resp.headers.get("content-type", ""), resp.content


def _idx_get(base_url: str, path: str, params: dict[str, str], *, max_retries: int = 3) -> bytes:
    global _cffi_warm
    api_url = f"{base_url}{path}"
    warmup_url = f"{base_url}{_WARMUP_PATHS.get(path, _REFERER)}"
    api_headers = {
        "User-Agent": CONFIG.http_user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        "Referer": warmup_url,
        "X-Requested-With": "XMLHttpRequest",
    }
    fetch = _fetch_curl_cffi if curl_requests is not None else _fetch_httpx
    last_detail = "no attempts made"
    for attempt in range(max_retries):
        try:
            status, content_type, body = fetch(
                warmup_url, api_url, params, api_headers, warm=attempt > 0
            )
        except Exception as exc:  # noqa: BLE001 — transport errors vary by backend
            last_detail = f"{type(exc).__name__}: {exc}"
        else:
            if _is_json_ok(status, content_type, body):
                return body
            last_detail = f"HTTP {status}, content-type {content_type!r} (Cloudflare challenge/block)"
        _cffi_warm = False  # force a re-warm on the next attempt
        if attempt < max_retries - 1:
            time.sleep(2**attempt + random.uniform(0, 1))

    hint = (
        "IDX's Cloudflare hard-blocks datacenter/VPS IPs; run from an Indonesian "
        "residential connection."
    )
    if curl_requests is None:
        hint += " Also: curl_cffi is not installed (pip install 'vektor-gauss[cffi]')."
    raise IdxAccessBlocked(f"IDX {path} unreachable after {max_retries} attempts: {last_detail}. {hint}")


def idx_fetch_file(url: str, *, max_retries: int = 3) -> bytes:
    """GET a Cloudflare-fronted static file (an announcement PDF) reusing the warmed
    curl_cffi session. Raises IdxAccessBlocked on a challenge/block."""
    global _cffi_warm
    base = url.split("/StaticData", 1)[0] if "/StaticData" in url else CONFIG.idx_base_url
    warmup_url = f"{base}{_ANNOUNCE_REFERER}"
    headers = {
        "User-Agent": CONFIG.http_user_agent,
        "Accept": "application/pdf,*/*",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        "Referer": f"{base}{_ANNOUNCE_REFERER}",
    }
    fetch = _fetch_curl_cffi if curl_requests is not None else _fetch_httpx
    last_detail = "no attempts made"
    for attempt in range(max_retries):
        try:
            status, content_type, body = fetch(warmup_url, url, {}, headers, warm=attempt > 0)
        except Exception as exc:  # noqa: BLE001 — transport errors vary by backend
            last_detail = f"{type(exc).__name__}: {exc}"
        else:
            if status == 200 and (body[:5] == b"%PDF-" or "pdf" in content_type.lower()):
                return body
            last_detail = f"HTTP {status}, content-type {content_type!r} (not a PDF)"
        _cffi_warm = False
        if attempt < max_retries - 1:
            time.sleep(2**attempt + random.uniform(0, 1))
    raise IdxAccessBlocked(f"IDX file {url} unreachable after {max_retries} attempts: {last_detail}")


class IdxStockSummarySource(PriceSource):
    name = "idx_stock_summary"
    tier = 0

    def __init__(self, base_url: str | None = None, *, max_retries: int = 3) -> None:
        self.base_url = (base_url or CONFIG.idx_base_url).rstrip("/")
        self.max_retries = max_retries

    def _get(self, params: dict[str, str]) -> bytes:
        return _idx_get(self.base_url, _STOCK_PATH, params, max_retries=self.max_retries)

    # -- public -----------------------------------------------------------

    def fetch(self, session_date: date | None = None) -> PriceFetchResult:
        """Fetch one trading day. `session_date=None` => IDX's latest published day
        (result.session_date is then set to whatever IDX reports)."""
        params = {"length": "9999", "start": "0"}
        if session_date is not None:
            params["date"] = session_date.strftime("%Y%m%d")

        placeholder = session_date or date.today()
        try:
            raw = self._get(params)
        except IdxAccessBlocked as exc:
            return PriceFetchResult(
                status="blocked", session_date=placeholder, source=self.name,
                source_tier=self.tier, detail=str(exc),
            )

        payload = json.loads(raw)
        rows = payload.get("data") or []
        if not rows:
            return PriceFetchResult(
                status="no_data", session_date=placeholder, source=self.name,
                source_tier=self.tier, raw_bytes=raw,
                detail="empty data[] — non-trading day or EOD not yet published",
            )

        returned = _parse_date(rows[0].get("Date"))
        if session_date is not None and returned != session_date:
            return PriceFetchResult(
                status="date_mismatch", session_date=session_date, source=self.name,
                source_tier=self.tier, raw_bytes=raw,
                detail=f"requested {session_date}, IDX returned {returned}",
            )
        effective_date = returned or placeholder

        bars: list[PriceBar] = []
        for row in rows:
            code = (row.get("StockCode") or "").strip()
            if not code:
                continue
            bar = PriceBar(ticker=code, name=(row.get("StockName") or "").strip() or None)
            for idx_field, attr in FIELD_MAP.items():
                setattr(bar, attr, _to_decimal(row.get(idx_field)))
            bar.remarks = row.get("Remarks") or None
            bar.delisting_date = _parse_date(row.get("DelistingDate"))
            bars.append(bar)

        return PriceFetchResult(
            status="ok", session_date=effective_date, source=self.name,
            source_tier=self.tier, raw_bytes=raw, bars=bars,
        )


# IDX Index Summary response field -> IndexBar attribute
_INDEX_FIELD_MAP: dict[str, str] = {
    "Previous": "prev",
    "Highest": "high",
    "Lowest": "low",
    "Close": "close",
    "Change": "change",
    "NumberOfStock": "num_stock",
    "Volume": "volume",
    "Value": "value",
    "Frequency": "frequency",
    "MarketCapital": "market_capital",
}


class IdxIndexSummarySource(IndexSource):
    name = "idx_index_summary"
    tier = 0

    def __init__(self, base_url: str | None = None, *, max_retries: int = 3) -> None:
        self.base_url = (base_url or CONFIG.idx_base_url).rstrip("/")
        self.max_retries = max_retries

    def _get(self, params: dict[str, str]) -> bytes:
        return _idx_get(self.base_url, _INDEX_PATH, params, max_retries=self.max_retries)

    def fetch(self, session_date: date | None = None) -> IndexFetchResult:
        params = {"length": "9999", "start": "0"}
        if session_date is not None:
            params["date"] = session_date.strftime("%Y%m%d")
        placeholder = session_date or date.today()

        try:
            raw = self._get(params)
        except IdxAccessBlocked as exc:
            return IndexFetchResult(
                status="blocked", session_date=placeholder, source=self.name,
                source_tier=self.tier, detail=str(exc),
            )

        rows = json.loads(raw).get("data") or []
        if not rows:
            return IndexFetchResult(
                status="no_data", session_date=placeholder, source=self.name,
                source_tier=self.tier, raw_bytes=raw, detail="empty data[]",
            )

        returned = _parse_date(rows[0].get("Date"))
        if session_date is not None and returned != session_date:
            return IndexFetchResult(
                status="date_mismatch", session_date=session_date, source=self.name,
                source_tier=self.tier, raw_bytes=raw,
                detail=f"requested {session_date}, IDX returned {returned}",
            )

        bars: list[IndexBar] = []
        for row in rows:
            code = (row.get("IndexCode") or "").strip()
            if not code:
                continue
            bar = IndexBar(index_code=code)
            for idx_field, attr in _INDEX_FIELD_MAP.items():
                setattr(bar, attr, _to_decimal(row.get(idx_field)))
            bars.append(bar)

        return IndexFetchResult(
            status="ok", session_date=returned or placeholder, source=self.name,
            source_tier=self.tier, raw_bytes=raw, bars=bars,
        )
