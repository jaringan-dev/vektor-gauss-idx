"""IDX trading-halt (suspend/reopen) announcements (Tier 0).

Same IDX GetAnnouncement feed as disclosure.py, different keyword
("PENGHENTIAN SEMENTARA") and no title filter — disclosure.py's own filter
requires "KEPEMILIKAN" in the title, but halt-announcement titles never
contain that word, so IdxDisclosureSource can't be reused directly here
despite being keyword-parameterized.

Metadata only — unlike the ownership-disclosure Phase A/B pipeline, no PDF is
downloaded or parsed: the announcement title itself ("Penghentian Sementara
Perdagangan Efek PT X Tbk.") is enough to know a ticker was halted that day.
Verified live against the real feed 2026-09-05 (see memory
vektor-gauss-data-availability): "PENGHENTIAN SEMENTARA" returns per-ticker
halt notices same-day; "POTENSI DELISTING" returns a periodic bulk PDF with
no per-ticker code (not handled here — would need PDF parsing like Phase B,
lower value, skipped for now).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from ..config import CONFIG
from . import idx
from .base import FetchStatus

_KEYWORD = "PENGHENTIAN SEMENTARA"
_PAGE_SIZE = 200
_HALT_RE = re.compile(r"PENGHENTIAN SEMENTARA PERDAGANGAN", re.IGNORECASE)
# "Pembukaan Penghentian Sementara Perdagangan..." (reopening) and "Pencabutan
# Penghentian Sementara Perdagangan..." (lifting) both contain the halt phrase
# as a substring — verified live 2026-09-05 (real SUPR/EDGE titles) — so a
# bare substring match on _HALT_RE alone misclassifies reopenings as fresh
# halts. Either word anywhere in the title means "this is about lifting a
# halt", not declaring one.
_REOPEN_RE = re.compile(r"PEMBUKAAN|PENCABUTAN", re.IGNORECASE)


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


@dataclass(slots=True)
class TradingHaltEvent:
    ticker: str
    no_pengumuman: str
    filed_at: datetime
    filed_date: date
    title: str | None = None
    is_halt: bool = False     # True = the halt notice itself; False = a related
                               # follow-up (recovery plan, reopening statement, ...)


@dataclass(slots=True)
class TradingHaltFetchResult:
    status: FetchStatus
    session_date: date
    source: str
    source_tier: int
    rows: list[TradingHaltEvent] = field(default_factory=list)
    detail: str = ""


class IdxTradingHaltSource:
    """Trading-halt announcements for one announcement day."""

    name = "idx_trading_halt"
    tier = 0

    def __init__(self, base_url: str | None = None, *, max_retries: int = 3) -> None:
        self.base_url = (base_url or CONFIG.idx_base_url).rstrip("/")
        self.max_retries = max_retries

    def _download(self, session_date: date) -> bytes:
        """Announcement list for one day. Test injection point (monkeypatch this)."""
        ymd = session_date.strftime("%Y%m%d")
        index_from = 0
        replies: list[dict] = []
        total = None
        while True:
            params = {
                "indexFrom": str(index_from),
                "pageSize": str(_PAGE_SIZE),
                "dateFrom": ymd,
                "dateTo": ymd,
                "lang": "id",
                "keyword": _KEYWORD,
            }
            body = idx._idx_get(
                self.base_url, idx._ANNOUNCE_PATH, params, max_retries=self.max_retries
            )
            payload = json.loads(body)
            page = payload.get("Replies") or []
            replies.extend(page)
            total = payload.get("ResultCount", len(replies)) if total is None else total
            if len(replies) >= total or not page:
                break
            index_from += _PAGE_SIZE
        return json.dumps({"ResultCount": total, "Replies": replies}).encode("utf-8")

    @staticmethod
    def _parse(raw: bytes) -> list[TradingHaltEvent]:
        payload = json.loads(raw)
        out: list[TradingHaltEvent] = []
        for reply in payload.get("Replies") or []:
            p = reply.get("pengumuman") or {}
            ticker = (p.get("Kode_Emiten") or "").strip()
            if not ticker:
                continue   # market-wide notices (e.g. bulk delisting-risk letters) carry no ticker
            filed_at = _parse_dt(p.get("TglPengumuman"))
            no = (p.get("NoPengumuman") or "").strip()
            if filed_at is None or not no:
                continue
            title = p.get("JudulPengumuman") or ""
            is_halt = bool(_HALT_RE.search(title)) and not _REOPEN_RE.search(title)
            out.append(TradingHaltEvent(
                ticker=ticker, no_pengumuman=no, filed_at=filed_at, filed_date=filed_at.date(),
                title=title, is_halt=is_halt,
            ))
        return out

    def fetch(self, session_date: date) -> TradingHaltFetchResult:
        try:
            raw = self._download(session_date)
        except idx.IdxAccessBlocked as exc:
            return TradingHaltFetchResult(
                status="blocked", session_date=session_date, source=self.name,
                source_tier=self.tier, detail=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — transport/parse errors vary
            return TradingHaltFetchResult(
                status="error", session_date=session_date, source=self.name,
                source_tier=self.tier, detail=f"{type(exc).__name__}: {exc}",
            )
        rows = self._parse(raw)
        return TradingHaltFetchResult(
            status="ok" if rows else "no_data",
            session_date=session_date,
            source=self.name,
            source_tier=self.tier,
            rows=rows,
            detail="" if rows else "no trading-halt announcements that day",
        )
