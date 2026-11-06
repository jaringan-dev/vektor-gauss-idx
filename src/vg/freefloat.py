"""Real free float from the issuers' monthly registry reports (LBRE), for FLOAT2-ASING-PADAT (audit 2026-11-07, B3).

Why: `price_bars.tradable_shares` from the IDX stock summary equals `listed_shares` for ~96% of stocks (BBCA, BBRI, TLKM,
ASII all 100%), so it is not a free-float figure, and FLOAT-ASING-PADAT ("foreigners took a slice of a thin float") was in
fact measured against all listed shares.

Source: every listed company files a "Laporan Bulanan Registrasi Pemegang Efek" (LBRE) in the first days of each month, as an
IDX announcement (GetAnnouncement, keyword 'Registrasi Pemegang Efek'; ~850 issuers, mostly 5-12 days after month end). The
PDF follows one IDX template with a table "Informasi Saham Free Float": the free-float share count computed under listing rule
I-A (controller, its affiliates, treasury, lock-up etc. excluded), the listed share count at month end, and the free-float %,
each for the previous and the current month; plus the number of SID holders. BBCA, Sep-2026: 51,820,646,928 free-float shares
of 122,042,299,500 = 42.46%.

The report for month M is published in M+1, so a session may use it only once it was announced (`announced_at`, the IDX
announcement time) - the FLOAT2 rule enforces that, and also refuses to measure across a corporate action after the month end
(the share count changed since). `parse_lbre` is pure and unit-tested; `collect` touches IDX and the database.
"""

from __future__ import annotations

import io
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

KEYWORD = "Registrasi Pemegang Efek"
PCT_TOLERANCE = 0.15         # the stated % may differ from shares/listed by rounding only (percentage points)
REQUEST_DELAY = 0.3          # seconds between PDF downloads (polite; IDX is the same host the EOD uses daily)

_NUM = r"(-|\d{1,3}(?:\.\d{3})*|\d+)"
_PCT = r"(-|\d{1,3}(?:,\d+)?)"
_RE_FF = re.compile(r"Jumlah\s+saham\s+Free\s+Float\s+" + _NUM + r"\s+" + _NUM, re.IGNORECASE)
_RE_LISTED = re.compile(r"Jumlah\s+saham\s+tercatat\s+di\s+Bursa\s+per\s+akhir\s+bulan\s+" + _NUM + r"\s+" + _NUM, re.IGNORECASE)
_RE_PCT = re.compile(r"%\s*Saham\s+Free\s+Float\s+" + _PCT + r"\s+" + _PCT, re.IGNORECASE)
_RE_SID = re.compile(r"Pemilik\s+SID\s+Bulan\s+Sebelumnya\s+Bulan\s+Sekarang\s+Perubahan\s+" + _NUM + r"\s+" + _NUM, re.IGNORECASE)


@dataclass(frozen=True)
class LbreFigures:
    free_float_shares: float
    listed_shares: float
    free_float_pct: float          # as stated in the report (percent, e.g. 42.46)
    prev_free_float_shares: float | None
    prev_listed_shares: float | None
    holders_sid: int | None
    consistent: bool               # stated % agrees with shares / listed within PCT_TOLERANCE


def _num(s: str) -> float | None:
    return None if s == "-" else float(s.replace(".", ""))


def _pct(s: str) -> float | None:
    return None if s == "-" else float(s.replace(",", "."))


def parse_lbre(text: str) -> LbreFigures | None:
    """The current-month free-float figures from an LBRE's text, or None when the table is not there."""
    t = re.sub(r"\s+", " ", text)
    ff, listed, pct = _RE_FF.search(t), _RE_LISTED.search(t), _RE_PCT.search(t)
    if not (ff and listed and pct):
        return None
    ffs, lst, pc = _num(ff.group(2)), _num(listed.group(2)), _pct(pct.group(2))
    if not ffs or not lst or pc is None or lst <= 0:
        return None
    sid = _RE_SID.search(t)
    consistent = abs(ffs / lst * 100 - pc) <= PCT_TOLERANCE and ffs <= lst
    return LbreFigures(ffs, lst, pc, _num(ff.group(1)), _num(listed.group(1)),
                       int(_num(sid.group(2))) if sid and _num(sid.group(2)) is not None else None, consistent)


def report_month(announced: datetime) -> date:
    """Fallback for the month an LBRE describes: the month BEFORE it was announced (it is due in the first days of the next
    month). Returned as that month's last calendar day. `stated_month` (from the PDF) wins when present: a correction filed
    weeks later still describes the original month."""
    first = announced.date().replace(day=1)
    return first - timedelta(days=1)


_MONTHS = {m: i for i, m in enumerate(("januari", "februari", "maret", "april", "mei", "juni", "juli", "agustus", "september",
                                        "oktober", "november", "desember"), start=1)}
_RE_MONTH = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(20\d\d)\b", re.IGNORECASE)


def stated_month(text: str) -> date | None:
    """The report month named in the LBRE text (the most frequent 'Bulan YYYY'), as its last calendar day; None if absent."""
    from collections import Counter

    hits = Counter((int(y), _MONTHS[m.lower()]) for m, y in _RE_MONTH.findall(text))
    if not hits:
        return None
    y, m = hits.most_common(1)[0][0]
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return nxt - timedelta(days=1)


# ---- IDX + database ------------------------------------------------------------------------------------------------

def _announcements(src, day: date) -> list[dict]:
    """LBRE announcements of one day: ticker, number, time, attachment URLs, correction flag."""
    out = []
    for r in json.loads(src._download(day)).get("Replies") or []:
        p = r.get("pengumuman") or {}
        title = (p.get("JudulPengumuman") or "").strip()
        ticker, no = (p.get("Kode_Emiten") or "").strip().upper(), (p.get("NoPengumuman") or "").strip()
        if KEYWORD.lower() not in title.lower() or not ticker or not no:
            continue
        try:
            at = datetime.fromisoformat((p.get("TglPengumuman") or "").strip())
        except ValueError:
            continue
        urls = [a.get("FullSavePath") for a in (r.get("attachments") or []) if a.get("FullSavePath")]
        out.append({"ticker": ticker, "no": no, "at": at, "urls": urls, "correction": "KOREKSI" in title.upper()})
    return out


def _pdf_text(blob: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(blob)) as doc:
        return "\n".join((pg.extract_text() or "") for pg in doc.pages)


def collect(start: date, end: date, *, refetch: bool = False) -> dict:
    """Read every LBRE announced between `start` and `end` (inclusive) and store the free-float figures. An announcement
    already stored (same ticker + number) is skipped unless `refetch`. A correction (KOREKSI) replaces the month's figures
    but keeps the first announcement time, so a session never sees a figure before the month was first published."""
    from .db import connect
    from .engine import _log
    from .sources.disclosure import IdxDisclosureSource

    src = IdxDisclosureSource(keyword=KEYWORD)
    stats = {"days": 0, "announcements": 0, "stored": 0, "skipped": 0, "no_table": 0, "inconsistent": 0, "errors": 0}
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, no_pengumuman FROM free_float_monthly")
        have = {(t, n) for t, n in cur.fetchall()}
        day = start
        while day <= end:
            if day.weekday() < 5:
                stats["days"] += 1
                try:
                    items = _announcements(src, day)
                except Exception:  # noqa: BLE001 - one bad day must not stop a backfill; counted and logged
                    stats["errors"] += 1
                    items = []
                for a in items:
                    stats["announcements"] += 1
                    if (a["ticker"], a["no"]) in have and not refetch:
                        stats["skipped"] += 1
                        continue
                    fig, url, month = None, None, None
                    for u in a["urls"]:
                        try:
                            text = _pdf_text(src.fetch_pdf(u))
                        except Exception:  # noqa: BLE001 - a truncated/non-PDF attachment: try the next one, count it
                            stats["errors"] += 1
                            continue
                        finally:
                            time.sleep(REQUEST_DELAY)
                        fig = parse_lbre(text)
                        if fig is not None:
                            url, month = u, stated_month(text)
                            break
                    if fig is None:
                        stats["no_table"] += 1
                        continue
                    stats["inconsistent"] += not fig.consistent
                    cur.execute(
                        """
                        INSERT INTO free_float_monthly (ticker, month_end, free_float_shares, listed_shares, free_float_pct,
                            prev_free_float_shares, prev_listed_shares, holders_sid, consistent, is_correction, no_pengumuman,
                            announced_at, first_announced_at, pdf_url)
                        VALUES (%(t)s, %(m)s, %(ff)s, %(ls)s, %(pc)s, %(pff)s, %(pls)s, %(sid)s, %(ok)s, %(corr)s, %(no)s, %(at)s, %(at)s, %(url)s)
                        ON CONFLICT (ticker, month_end) DO UPDATE SET
                            free_float_shares = EXCLUDED.free_float_shares, listed_shares = EXCLUDED.listed_shares,
                            free_float_pct = EXCLUDED.free_float_pct, prev_free_float_shares = EXCLUDED.prev_free_float_shares,
                            prev_listed_shares = EXCLUDED.prev_listed_shares, holders_sid = EXCLUDED.holders_sid,
                            consistent = EXCLUDED.consistent, is_correction = EXCLUDED.is_correction,
                            no_pengumuman = EXCLUDED.no_pengumuman, announced_at = EXCLUDED.announced_at, pdf_url = EXCLUDED.pdf_url,
                            fetched_at = now(),
                            first_announced_at = LEAST(free_float_monthly.first_announced_at, EXCLUDED.first_announced_at)
                        WHERE free_float_monthly.announced_at <= EXCLUDED.announced_at
                        """,
                        {"t": a["ticker"], "m": month or report_month(a["at"]), "ff": fig.free_float_shares, "ls": fig.listed_shares,
                         "pc": fig.free_float_pct, "pff": fig.prev_free_float_shares, "pls": fig.prev_listed_shares,
                         "sid": fig.holders_sid, "ok": fig.consistent, "corr": a["correction"], "no": a["no"],
                         "at": a["at"], "url": url},
                    )
                    # The month was public from its FIRST announcement, whichever version holds the figures.
                    cur.execute(
                        "UPDATE free_float_monthly SET first_announced_at = LEAST(first_announced_at, %(at)s) "
                        "WHERE ticker = %(t)s AND month_end = %(m)s",
                        {"at": a["at"], "t": a["ticker"], "m": month or report_month(a["at"])},
                    )
                    have.add((a["ticker"], a["no"]))
                    stats["stored"] += 1
                conn.commit()                       # one day at a time: an interrupted backfill keeps what it stored
            day += timedelta(days=1)
        _log(cur, "collect_free_float", end, "ok" if not stats["errors"] else "partial", stats["stored"],
             None if not stats["errors"] else f"{stats['errors']} fetch/parse errors")
    return stats
