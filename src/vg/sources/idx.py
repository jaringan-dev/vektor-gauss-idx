"""IDX Stock Summary source (Tier 0).

Endpoint (verified 2026-09-03 via idx.co.id devtools):
    GET {base}/primary/TradingSummary/GetStockSummary?length=9999&start=0&date=YYYYMMDD

- Omitting `date` returns the latest published trading day.
- Response: {"draw":0,"recordsTotal":N,"recordsFiltered":N,"data":[{...}]}
- ~963 rows. Each row keyed by IDX field names (see FIELD_MAP).
- The site sits behind Cloudflare. A datacenter IP may get a 403 / HTML
  challenge page — see README ("Cloudflare"). This client uses a realistic
  browser identity and low request rate only.

UNVERIFIED, flag before trusting:
- Whether ForeignBuy/ForeignSell are all-boards or regular-only.
- Exact behaviour for a non-trading day vs a trading day whose EOD data is not
  yet published — both are handled here as "date_mismatch" / "no_data".
"""

from __future__ import annotations

import json
import random
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from ..config import CONFIG
from .base import PriceBar, PriceFetchResult, PriceSource

_PATH = "/primary/TradingSummary/GetStockSummary"

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


class IdxStockSummarySource(PriceSource):
    name = "idx_stock_summary"
    tier = 0

    def __init__(self, base_url: str | None = None, *, max_retries: int = 3) -> None:
        self.base_url = (base_url or CONFIG.idx_base_url).rstrip("/")
        self.max_retries = max_retries

    # -- HTTP ---------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": CONFIG.http_user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
            "Referer": f"{self.base_url}/en/market-data/trading-summary/stock-summary",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _get(self, params: dict[str, str]) -> bytes:
        url = f"{self.base_url}{_PATH}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=CONFIG.http_timeout, headers=self._headers()) as client:
                    resp = client.get(url, params=params)
                if resp.status_code == 403:
                    raise IdxAccessBlocked(
                        f"IDX returned 403 for {resp.url}. Likely a Cloudflare block on this "
                        "IP. Try an Indonesian VPS, or the curl-cffi fallback (see README)."
                    )
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"retryable {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                body = resp.content
                ctype = resp.headers.get("content-type", "")
                if "json" not in ctype and b"<html" in body[:2048].lower():
                    raise IdxAccessBlocked(
                        f"IDX returned HTML (not JSON) for {resp.url} — Cloudflare challenge?"
                    )
                return body
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt + random.uniform(0, 1))
        raise RuntimeError(f"IDX request failed after {self.max_retries} attempts: {last_exc}")

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
