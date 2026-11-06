"""IDX ownership-disclosure filings (Tier 0).

>5% substantial-shareholder + director/commissioner ("insider") ownership changes,
filed via KSEI AKSes under POJK 4/2024 and published on the IDX announcement feed:

    GET {base}/primary/ListedCompany/GetAnnouncement
        ?indexFrom=0&pageSize=200&dateFrom=YYYYMMDD&dateTo=YYYYMMDD&lang=id&keyword=LAPORAN KEPEMILIKAN

Response: {"ResultCount": N, "Replies": [{"pengumuman": {...}, "attachments": [{...}]}]}
Each `pengumuman` carries the ticker (`Kode_Emiten`), filing datetime (`TglPengumuman`),
a unique number (`NoPengumuman`), and a KSEI-generated PDF form as its attachment.

Same Cloudflare front as the trading-summary endpoints — reuses `idx._idx_get` /
`idx.idx_fetch_file` (warmed curl_cffi). Residential IP only; a datacenter IP is
1020-blocked and surfaces here as status="blocked".

Phase A collects filing metadata + archives the PDFs. Phase B parses the archived
forms into structured holding-change rows (see collect.parse_disclosure_pdfs).
"""

from __future__ import annotations

import json
from datetime import date, datetime

from ..config import CONFIG
from . import idx
from .base import DisclosureFetchResult, DisclosureFiling, DisclosureSource

_KEYWORD = "LAPORAN KEPEMILIKAN"
_PAGE_SIZE = 200
_MAX_PAGES = 50       # safety cap: 50 pages x page size, far above any real day


def _parse_party(perihal: str | None) -> str | None:
    """Best-effort reporting-party name out of PerihalPengumuman, e.g.
    "LAPORAN ... SAHAM PERUSAHAAN TERBUKA - SAMUEL SEKURITAS INDONESIA - Group:-"
    -> "SAMUEL SEKURITAS INDONESIA". Heuristic; the raw subject is always stored."""
    if not perihal:
        return None
    parts = [p.strip() for p in perihal.split(" - ")]
    parts = [p for p in parts if p and not p.lower().startswith("group:")]
    if len(parts) < 2:
        return None
    return parts[1] or None


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


class IdxDisclosureSource(DisclosureSource):
    """Ownership-disclosure filings for one announcement day."""

    name = "idx_disclosure"
    tier = 0

    def __init__(
        self, base_url: str | None = None, *, max_retries: int = 3, keyword: str = _KEYWORD
    ) -> None:
        self.base_url = (base_url or CONFIG.idx_base_url).rstrip("/")
        self.max_retries = max_retries
        self.keyword = keyword

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
                "keyword": self.keyword,
            }
            body = idx._idx_get(
                self.base_url, idx._ANNOUNCE_PATH, params, max_retries=self.max_retries
            )
            payload = json.loads(body)
            page = payload.get("Replies") or []
            replies.extend(page)
            total = payload.get("ResultCount", len(replies)) if total is None else total
            if len(replies) >= total or not page or index_from >= _MAX_PAGES:
                break
            # IDX's indexFrom is a PAGE NUMBER (0, 1, 2 ...), not a row offset: 'indexFrom=200' returns nothing, so a
            # day with more than one page silently lost every row after the first page (audit 2026-11-07, B15).
            index_from += 1
        return json.dumps({"ResultCount": total, "Replies": replies}).encode("utf-8")

    def fetch_pdf(self, url: str) -> bytes:
        return idx.idx_fetch_file(url, max_retries=self.max_retries)

    @staticmethod
    def _parse(raw: bytes) -> list[DisclosureFiling]:
        payload = json.loads(raw)
        out: list[DisclosureFiling] = []
        for reply in payload.get("Replies") or []:
            p = reply.get("pengumuman") or {}
            ticker = (p.get("Kode_Emiten") or "").strip()
            if not ticker:
                continue
            if "KEPEMILIKAN" not in (p.get("JudulPengumuman") or "").upper():
                continue
            filed_at = _parse_dt(p.get("TglPengumuman"))
            if filed_at is None:
                continue
            no = (p.get("NoPengumuman") or "").strip()
            if not no:
                continue
            attachments = [
                {
                    "original_filename": a.get("OriginalFilename"),
                    "full_save_path": a.get("FullSavePath"),
                }
                for a in (reply.get("attachments") or [])
                if a.get("FullSavePath")
            ]
            out.append(
                DisclosureFiling(
                    ticker=ticker,
                    no_pengumuman=no,
                    filed_at=filed_at,
                    filed_date=filed_at.date(),
                    title=p.get("JudulPengumuman"),
                    subject=p.get("PerihalPengumuman"),
                    party=_parse_party(p.get("PerihalPengumuman")),
                    jenis=p.get("JenisPengumuman"),
                    attachments=attachments,
                    raw={"pengumuman": p, "attachments": reply.get("attachments") or []},
                )
            )
        return out

    def fetch(self, session_date: date) -> DisclosureFetchResult:
        try:
            raw = self._download(session_date)
        except idx.IdxAccessBlocked as exc:
            return DisclosureFetchResult(
                status="blocked", session_date=session_date, source=self.name,
                source_tier=self.tier, detail=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — transport/parse errors vary
            return DisclosureFetchResult(
                status="error", session_date=session_date, source=self.name,
                source_tier=self.tier, detail=f"{type(exc).__name__}: {exc}",
            )
        rows = self._parse(raw)
        return DisclosureFetchResult(
            status="ok" if rows else "no_data",
            session_date=session_date,
            source=self.name,
            source_tier=self.tier,
            raw_bytes=raw,
            rows=rows,
            detail="" if rows else "no ownership-disclosure filings that day",
        )
