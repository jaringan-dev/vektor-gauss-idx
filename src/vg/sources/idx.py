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

import json
import random
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

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
_REFERER = "/en/market-data/trading-summary/"

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


def _idx_get(base_url: str, path: str, params: dict[str, str], *, max_retries: int = 3) -> bytes:
    url = f"{base_url}{path}"
    headers = {
        "User-Agent": CONFIG.http_user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        "Referer": f"{base_url}{_REFERER}",
        "X-Requested-With": "XMLHttpRequest",
    }
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=CONFIG.http_timeout, headers=headers) as client:
                resp = client.get(url, params=params)
            if resp.status_code == 403:
                raise IdxAccessBlocked(
                    f"IDX returned 403 for {resp.url}. Likely a Cloudflare block on this IP. "
                    "Try an Indonesian VPS, or the curl-cffi fallback (see README)."
                )
            if resp.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError(
                    f"retryable {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            body = resp.content
            if "json" not in resp.headers.get("content-type", "") and b"<html" in body[:2048].lower():
                raise IdxAccessBlocked(
                    f"IDX returned HTML (not JSON) for {resp.url} — Cloudflare challenge?"
                )
            return body
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(2**attempt + random.uniform(0, 1))
    raise RuntimeError(f"IDX request failed after {max_retries} attempts: {last_exc}")


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
