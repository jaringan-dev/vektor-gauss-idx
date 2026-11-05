"""Broker summary via the the configured data feed REST API (Tier 3).

    GET {base}/api/broker-summary/{TICKER}      header: X-API-Key: <key>

Response: {"stock_code": "BBCA", "brokers": [
    {"broker_code":"YU","broker_name":"CGS ...","bval":..,"bvol":..,"bfrq":..,
     "sfrq":..,"sval":..,"svol":..,"nval":..,"nvol":..}, ...]}

IMPORTANT properties, verified live 2026-09-27:
  * FORWARD-ONLY. The `?date=` param is silently ignored — the endpoint always
    returns the latest published trading day. There is no historical backfill;
    the collector stamps rows with the latest confirmed trading day and can only
    accumulate going forward. This is why there is no `--backfill` here.
  * NO origin/cohort field. The feed gives per-broker value/volume/frequency
    only. Foreign-vs-domestic (`side_flag`) is assigned from FOREIGN_BROKERS
    below — a static, hand-maintained map, which is exactly the analytical layer
    NeoBDM/Sectors sell. Keep it auditable (PRINSIP-DATA.md).
  * Tier 3: a free, almost-certainly-scraped feed from a solo operator. Fine as a
    Phase-1 personal cross-check; never a production/redistribution source. Cross-
    validate anything surprising against IDX-direct fields before trusting it.

Unlike the IDX sources this is a plain REST endpoint (no Cloudflare), so it uses
httpx directly and needs no curl_cffi / residential IP.
"""

from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from datetime import date as _date

from ..config import CONFIG
from .base import (
    BrokerFlowRow,
    BrokerInventoryResult,
    BrokerInventoryRow,
    BrokerLevelRow,
    BrokerSummaryFetchResult,
    BrokerSummarySource,
    DoneByPriceResult,
    DoneByPriceRow,
    PriceBar,
    PriceHistoryResult,
)

# ---------------------------------------------------------------------------
# STATIC broker -> origin map. Codes NOT listed here are treated as domestic.
#
# This is the one piece the feed cannot give you and the place your domain
# knowledge shapes the signal (net-foreign, foreign concentration). The default
# set below = foreign INSTITUTIONAL houses conventionally read as "asing" /
# smart-money in IDX bandarmology. Audit each code against the `broker_name` that
# appears in your own feed and adjust — codes can be reassigned over time.
#
# NOT included on purpose — "foreign-owned but RETAIL-flow" brokers, whose parent
# is foreign but whose order flow is overwhelmingly domestic retail. Tagging them
# 'F' would pollute the net-foreign signal. Decide per your own method before
# moving any of these up:
#     YP Mirae Asset (KR) · AG Kiwoom (KR) · XA/XC KB Valbury (KR) ·
#     SS Samuel (part KR) — the classic Korean-owned retail houses.
FOREIGN_BROKERS: frozenset[str] = frozenset({
    "AK",   # UBS Sekuritas Indonesia
    "KZ",   # CLSA Sekuritas Indonesia
    "YU",   # CGS International Sekuritas Indonesia (ex CGS-CIMB)
    "ML",   # Merrill Lynch (BofA) Sekuritas Indonesia
    "DB",   # Deutsche Sekuritas Indonesia
    "RX",   # Macquarie Sekuritas Indonesia
    "ZP",   # Maybank Sekuritas Indonesia (ex Maybank Kim Eng)
    "BK",   # J.P. Morgan Sekuritas Indonesia
    "BB",   # Verdhana Sekuritas Indonesia (Nomura, JP)
    "DR",   # RHB Sekuritas Indonesia (MY)
    "MS",   # Morgan Stanley Sekuritas Indonesia
    "CG",   # Citigroup Sekuritas Indonesia
    "CS",   # Credit Suisse Sekuritas Indonesia (merged into UBS 2023; historical)
    "AI",   # UOB Kay Hian Sekuritas (SG) — confirmed "KAY HIAN SEKURITAS" in feed
    "DP",   # DBS Vickers Sekuritas Indonesia (SG)
})
# ---------------------------------------------------------------------------

# STATIC broker -> cohort map. Codes here are treated as RETAIL-dominated order
# flow (retail-app / mass-retail brokers). Everything else = institution-leaning.
# This is a HEURISTIC (a broker code aggregates many clients — one broker serves
# both retail and institutions), so the participant classifier built on it is an
# INFERENCE, forward-tested, never ground truth. EXTEND with codes you judge retail.
# Seeded conservatively with the clearest retail-app / mass-retail houses:
RETAIL_BROKERS: frozenset[str] = frozenset({
    "YP",   # Mirae Asset Sekuritas (largest retail broker)
    "XL",   # Stockbit Sekuritas Digital
    "XC",   # Ajaib Sekuritas Asia
    "PD",   # Indo Premier Sekuritas (IPOT)
    "AG",   # Kiwoom Sekuritas Indonesia
    "CP",   # KB Valbury Sekuritas
})
# Fuzzy (bank-backed, serve BOTH retail & institution — left OUT on purpose; add
# if your read says retail): NI BNI · SQ BCA · EP MNC · GR Panin · DH Sinarmas.


def is_retail(broker_code: str) -> bool:
    return broker_code.upper() in RETAIL_BROKERS


_PATH = "/api/broker-summary/{ticker}"

# Bump when `_parse`/`_parse_levels` change what they extract; stored next to every parsed row so a
# bad parser version can be found and the raw bytes re-parsed (ARSITEKTUR-DATA-v1 K1).
#   /1  top-20 NET buyers only (`brokers`)            — the sell side was silently dropped
#   /2  + `broker_levels` (25 gross-buy / gross-sell rungs)
PARSER_VERSION = "vg-broker/2"


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _div(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or not den:
        return None
    return num / den


def _origin(broker_code: str) -> str:
    return "F" if broker_code in FOREIGN_BROKERS else "D"


class VgBrokerSummarySource(BrokerSummarySource):
    """Latest-day broker summary for one ticker from VG_FEED_BASE_URL."""

    name = "vektorgauss"
    tier = 3

    @staticmethod
    def _parse(raw: bytes) -> list[BrokerFlowRow]:
        doc = json.loads(raw)
        rows: list[BrokerFlowRow] = []
        for b in doc.get("brokers", []):
            code = (b.get("broker_code") or "").strip().upper()
            if not code:
                continue
            buy_val, buy_vol = _dec(b.get("bval")), _dec(b.get("bvol"))
            sell_val, sell_vol = _dec(b.get("sval")), _dec(b.get("svol"))
            rows.append(BrokerFlowRow(
                broker_code=code,
                broker_name=b.get("broker_name"),
                buy_val=buy_val,
                buy_vol=buy_vol,
                buy_freq=_dec(b.get("bfrq")),
                sell_val=sell_val,
                sell_vol=sell_vol,
                sell_freq=_dec(b.get("sfrq")),
                net_val=_dec(b.get("nval")),
                net_vol=_dec(b.get("nvol")),
                buy_avg=_div(buy_val, buy_vol),
                sell_avg=_div(sell_val, sell_vol),
                side_flag=_origin(code),
                board="ALL",
            ))
        return rows

    @staticmethod
    def _parse_levels(raw: bytes) -> list[BrokerLevelRow]:
        """The ranked gross buy / gross sell ladder. A rung may lack either side (fewer than 25
        active brokers); a missing side is simply skipped, never zero-filled."""
        doc = json.loads(raw)
        out: list[BrokerLevelRow] = []
        for rank, lv in enumerate(doc.get("broker_levels") or [], start=1):
            for side, key, vkey, qkey, fkey, akey in (
                ("B", "buy", "bval", "bvol", "bfrq", "bavg"),
                ("S", "sell", "sval", "svol", "sfrq", "savg"),
            ):
                b = (lv or {}).get(key) or {}
                code = (b.get("broker_code") or "").strip().upper()
                if not code:
                    continue
                out.append(BrokerLevelRow(
                    side=side, rank=rank, broker_code=code, broker_name=b.get("broker_name"),
                    val=_dec(b.get(vkey)), vol=_dec(b.get(qkey)), freq=_dec(b.get(fkey)),
                    avg=_dec(b.get(akey)),
                ))
        return out

    @staticmethod
    def _served_date(raw: bytes) -> date | None:
        """The day the response says it covers (`broker_end_date`), or None when it carries no date."""
        end = json.loads(raw).get("broker_end_date")
        return _date.fromisoformat(end) if end else None

    _retry_waits: tuple[float, ...] = (2.0, 6.0)  # seconds before the 2nd and 3rd try

    def _download(self, ticker: str, on_date: date | None = None) -> bytes:
        if not CONFIG.feed_base_url or not CONFIG.feed_api_key:
            raise RuntimeError(
                "VG_FEED_BASE_URL / VG_FEED_API_KEY not set — see .env"
            )
        url = CONFIG.feed_base_url + _PATH.format(ticker=ticker.upper())
        params: dict = {}
        if on_date is not None:
            # The feed now honours a date range (verified live 2026-10-16: different
            # dates return genuinely different broker data, back to 2020). start==end
            # pins ONE trading day -> enables historical backfill. Without it the
            # endpoint returns only the latest published day.
            d = on_date.isoformat()
            params["start_date"] = params["end_date"] = d
        # A 5xx or a dropped connection is the feed or the network blinking (5 Nov: ten 502s in a row ended the night's backfill,
        # and the same names answered 200 an hour later; 4 Nov: three minutes of DNS failures). Wait and ask again a couple of
        # times. 4xx (bad key, quota, ban) is NOT retried: asking harder would only make that worse.
        for wait in (*self._retry_waits, None):
            try:
                resp = httpx.get(
                    url,
                    headers={"accept": "application/json", "X-API-Key": CONFIG.feed_api_key},
                    params=params or None,
                    timeout=CONFIG.http_timeout,
                )
                if resp.status_code < 500:
                    break
                failure: Exception | None = None
            except httpx.TransportError as exc:
                resp, failure = None, exc
            if wait is None:
                if failure is not None:
                    raise failure
                break  # out of tries: raise_for_status below reports the 5xx
            time.sleep(wait)
        resp.raise_for_status()
        return resp.content

    def fetch(
        self, session_date: date, ticker: str, *, use_date: bool = False
    ) -> BrokerSummaryFetchResult:
        ticker = ticker.upper()
        try:
            # Latest-day path keeps the original one-argument call (back-compat with
            # subclasses/stubs that override _download(ticker)); only a dated pull passes on_date.
            raw = self._download(ticker, on_date=session_date) if use_date else self._download(ticker)
        except Exception as exc:
            return BrokerSummaryFetchResult(
                "error", session_date, ticker, self.name, self.tier,
                detail=f"{type(exc).__name__}: {exc}",
            )
        try:
            rows = self._parse(raw)
            levels = self._parse_levels(raw)
            served = self._served_date(raw)
        except Exception as exc:
            return BrokerSummaryFetchResult(
                "error", session_date, ticker, self.name, self.tier, raw_bytes=raw,
                detail=f"parse: {type(exc).__name__}: {exc}",
            )
        if served is not None and served != session_date:
            # The feed answers "the latest day" with whatever it has published so far. At 19:19 on 3 Nov 2026 that was still
            # 2 Nov, and the whole table was stored under 3 Nov (every row an exact copy). Its own date says which day it is.
            return BrokerSummaryFetchResult(
                "no_data", session_date, ticker, self.name, self.tier, raw_bytes=raw,
                detail=f"stale: the feed served {served}, not {session_date}",
            )
        status = "ok" if rows else "no_data"
        return BrokerSummaryFetchResult(
            status, session_date, ticker, self.name, self.tier,
            raw_bytes=raw, rows=rows, levels=levels,
        )


_HISTORY_PATH = "/api/history/{ticker}"


class VgHistoryPriceSource:
    """Per-ticker daily OHLCV + foreign flow from VG_FEED_BASE_URL /api/history.

    A Cloudflare-free fallback for IdxStockSummarySource (which gets 403'd on
    non-residential IPs). One call returns a fixed ~120-trading-day rolling window
    (NO deeper history — every paging/date param is ignored by the feed). Tier 3.

    Units verified live 2026-09-27: value = IDR; volume / f_buy / f_sell / n_foreign
    = SHARES; n_foreign = f_buy - f_sell (net foreign shares); avg = value/volume;
    prev = close - change. Fields the endpoint does NOT carry (nonreg board split,
    listed/tradable shares, bid/offer) stay None -> the float/crossing/order-book
    signals simply don't fire on history-sourced days; the transaction-analysis
    core (net foreign flow, avg-ticket, foreign consistency, RS, ATR) does.
    """

    name = "vektorgauss_history"
    tier = 3

    @staticmethod
    def _parse(raw: bytes) -> list[PriceBar]:
        doc = json.loads(raw)
        code = (doc.get("stock_code") or "").strip().upper()
        bars: list[PriceBar] = []
        for r in doc.get("rows", []):
            d = r.get("date")
            if not d:
                continue
            close, change = _dec(r.get("close")), _dec(r.get("change"))
            bar = PriceBar(ticker=code, name=None)
            bar.bar_date = _date.fromisoformat(d)
            bar.close = close
            bar.open = _dec(r.get("open"))
            bar.high = _dec(r.get("high"))
            bar.low = _dec(r.get("low"))
            bar.change = change
            bar.prev = (close - change) if (close is not None and change is not None) else None
            bar.volume = _dec(r.get("volume"))
            bar.value = _dec(r.get("value"))
            bar.frequency = _dec(r.get("freq"))
            bar.foreign_buy_vol = _dec(r.get("f_buy"))
            bar.foreign_sell_vol = _dec(r.get("f_sell"))
            bars.append(bar)
        return bars

    def _download(self, ticker: str) -> bytes:
        if not CONFIG.feed_base_url or not CONFIG.feed_api_key:
            raise RuntimeError("VG_FEED_BASE_URL / VG_FEED_API_KEY not set — see .env")
        url = CONFIG.feed_base_url + _HISTORY_PATH.format(ticker=ticker.upper())
        resp = httpx.get(
            url,
            headers={"accept": "application/json", "X-API-Key": CONFIG.feed_api_key},
            timeout=CONFIG.http_timeout,
        )
        resp.raise_for_status()
        return resp.content

    def fetch(self, ticker: str) -> PriceHistoryResult:
        ticker = ticker.upper()
        try:
            raw = self._download(ticker)
        except Exception as exc:
            return PriceHistoryResult("error", ticker, self.name, self.tier,
                                      detail=f"{type(exc).__name__}: {exc}")
        try:
            bars = self._parse(raw)
        except Exception as exc:
            return PriceHistoryResult("error", ticker, self.name, self.tier, raw_bytes=raw,
                                      detail=f"parse: {type(exc).__name__}: {exc}")
        status = "ok" if bars else "no_data"
        return PriceHistoryResult(status, ticker, self.name, self.tier, raw_bytes=raw, bars=bars)


_ACCUM_PATH = "/api/broker-accumulation/{ticker}"


class VgBrokerInventorySource:
    """Per-broker daily net + running inventory (cum_net_val) over a trailing
    window (~80 td, top brokers per ticker) from the VG feed's broker-accumulation
    endpoint. HISTORICAL — unlike the latest-day-only broker summary. Tier 3.

    Response: {stock_code, start_date, end_date, series:[{broker_code, broker_name,
    points:[{date, nval, nvol, cum_nval, bavg, savg}]}], top_buyers, top_sellers}.
    """

    name = "vektorgauss_inventory"
    tier = 3

    @staticmethod
    def _parse(raw: bytes) -> list[BrokerInventoryRow]:
        doc = json.loads(raw)
        rows: list[BrokerInventoryRow] = []
        for s in doc.get("series", []):
            code = (s.get("broker_code") or "").strip().upper()
            if not code:
                continue
            bname = s.get("broker_name")
            for p in s.get("points", []):
                d = p.get("date")
                if not d:
                    continue
                rows.append(BrokerInventoryRow(
                    bar_date=_date.fromisoformat(d),
                    broker_code=code,
                    broker_name=bname,
                    net_val=_dec(p.get("nval")),
                    net_vol=_dec(p.get("nvol")),
                    cum_net_val=_dec(p.get("cum_nval")),
                    buy_avg=_dec(p.get("bavg")),
                    sell_avg=_dec(p.get("savg")),
                ))
        return rows

    def _download(self, ticker: str) -> bytes:
        if not CONFIG.feed_base_url or not CONFIG.feed_api_key:
            raise RuntimeError("VG_FEED_BASE_URL / VG_FEED_API_KEY not set — see .env")
        url = CONFIG.feed_base_url + _ACCUM_PATH.format(ticker=ticker.upper())
        resp = httpx.get(
            url,
            headers={"accept": "application/json", "X-API-Key": CONFIG.feed_api_key},
            timeout=CONFIG.http_timeout,
        )
        resp.raise_for_status()
        return resp.content

    def fetch(self, ticker: str) -> BrokerInventoryResult:
        ticker = ticker.upper()
        try:
            raw = self._download(ticker)
        except Exception as exc:
            return BrokerInventoryResult("error", ticker, self.name, self.tier,
                                         detail=f"{type(exc).__name__}: {exc}")
        try:
            rows = self._parse(raw)
        except Exception as exc:
            return BrokerInventoryResult("error", ticker, self.name, self.tier, raw_bytes=raw,
                                         detail=f"parse: {type(exc).__name__}: {exc}")
        status = "ok" if rows else "no_data"
        return BrokerInventoryResult(status, ticker, self.name, self.tier, raw_bytes=raw, rows=rows)


_DONE_PATH = "/api/done-details"
_DONE_PER_PAGE = 100
_DONE_MAX_PAGES = 200        # hard ceiling; a liquid stock/day is ~130 pages


def _agg_done_ticks(ticks: list[dict], agg: dict) -> None:
    """Fold a page of done ticks into agg keyed by (board, price_num). In-place, so
    12k ticks never all live in memory — we keep only the compact price-level map."""
    for t in ticks:
        board = (t.get("market_board") or "").strip().upper() or "RG"
        price = t.get("price_num")
        if price is None:
            continue
        qty = t.get("qty_num") or 0
        val = t.get("value_raw") or 0
        key = (board, int(price))
        a = agg.get(key)
        if a is None:
            a = agg[key] = {"qty": 0, "value": 0, "trades": 0, "ba": 0, "sa": 0, "fb": 0, "fs": 0}
        a["qty"] += qty
        a["value"] += val
        a["trades"] += 1
        if (t.get("action") or "").upper() == "BUY":
            a["ba"] += qty          # buy aggressor (offer lifted)
        elif (t.get("action") or "").upper() == "SELL":
            a["sa"] += qty          # sell aggressor (bid hit)
        if (t.get("buyer_type") or "").upper() == "F":
            a["fb"] += qty
        if (t.get("seller_type") or "").upper() == "F":
            a["fs"] += qty


class VgDoneDetailsSource:
    """Done-details (tick order-flow) for one ticker+day, aggregated LOCALLY into
    a compact per-price-level map. The endpoint is paginated (per_page=100) and a
    liquid name is ~130 pages/day — expensive — so this is an on-demand,
    page-capped harvest that is paid ONCE (freshness guard in collect prevents
    re-fetching). Tier 3. Each tick: {market_board, price_num, qty_num(lots),
    value_raw, buyer, seller, buyer_type/seller_type (F/D), action (BUY/SELL aggressor)}.
    """

    name = "vektorgauss_done"
    tier = 3

    def _get(self, ticker: str, session_date, page: int) -> dict:
        params = {"code": ticker.upper(), "page": page, "per_page": _DONE_PER_PAGE}
        if session_date is not None:
            params["date"] = session_date.isoformat()
        resp = httpx.get(
            CONFIG.feed_base_url + _DONE_PATH,
            headers={"accept": "application/json", "X-API-Key": CONFIG.feed_api_key},
            params=params, timeout=CONFIG.http_timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def fetch(self, ticker: str, session_date, max_pages: int = _DONE_MAX_PAGES) -> DoneByPriceResult:
        ticker = ticker.upper()
        if not CONFIG.feed_base_url or not CONFIG.feed_api_key:
            return DoneByPriceResult("error", ticker, session_date, self.name, self.tier,
                                     detail="VG_FEED_BASE_URL / VG_FEED_API_KEY not set")
        agg: dict = {}
        got = 0
        total_pages = 0
        resolved_date = session_date
        try:
            first = self._get(ticker, session_date, 1)
            total_pages = int(first.get("total_pages") or 0)
            if first.get("date") and resolved_date is None:
                resolved_date = _date.fromisoformat(first["date"])
            _agg_done_ticks(first.get("data", []), agg)
            got = 1
            last = min(total_pages, max_pages)
            for pg in range(2, last + 1):
                _agg_done_ticks(self._get(ticker, resolved_date, pg).get("data", []), agg)
                got += 1
        except Exception as exc:
            if not agg:
                return DoneByPriceResult("error", ticker, resolved_date, self.name, self.tier,
                                         detail=f"{type(exc).__name__}: {exc}")
            # partial: keep what we aggregated, flag it
        rows = [
            DoneByPriceRow(
                board=b, price_num=p,
                qty=_dec(a["qty"]), value=_dec(a["value"]), trades=a["trades"],
                buy_aggressor_qty=_dec(a["ba"]), sell_aggressor_qty=_dec(a["sa"]),
                foreign_buy_qty=_dec(a["fb"]), foreign_sell_qty=_dec(a["fs"]),
            )
            for (b, p), a in agg.items()
        ]
        status = "ok" if rows else "no_data"
        detail = "" if got >= total_pages else f"partial: {got}/{total_pages} pages (cap {max_pages})"
        return DoneByPriceResult(status, ticker, resolved_date, self.name, self.tier,
                                 rows=rows, pages_fetched=got, total_pages=total_pages, detail=detail)
