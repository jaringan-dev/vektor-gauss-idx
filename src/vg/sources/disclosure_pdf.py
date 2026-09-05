"""Parse the archived KSEI ownership-disclosure PDF forms (Phase B) into
structured per-transaction rows.

Each filing is a standardized, system-generated bilingual POJK 4/2024 form:
Indonesian pages first, an identical English translation appended after. We
parse only the Indonesian pages — always present, always first, and the
legally authoritative version (KSEI/OJK forms).

Header fields (reporter name, role, shares before/after, %-voting-rights,
controller flag) are simple "Label : Value" lines — extracted by regex over
the page text, order-independent (fields have been observed to shift
between pages depending on how the letter wraps).

The transaction table is NOT reliably recoverable via pdfplumber's
`extract_table()` on this template (verified empirically against real
filings — both its "lines" and "text" strategies mis-split rows around
2-line-wrapped cells: "Saham\\nBiasa" and "30-Jul-\\n2026"). Instead we
cluster `extract_words()` by (page, y-position) into row bands, then bucket
each word into one of 11 columns by nearest x0 to a set of column-anchor
x-coordinates measured from real filings. A row-band is accepted as a
transaction row only if its "Jumlah Saham" column parses as an
Indonesian-formatted number (`1.234,00`) — this is what actually
distinguishes a data row from header/footer/boilerplate text sharing the
same page, since enumerating every possible `Jenis Transaksi` value up
front isn't reliable.

Column anchors are specific to this KSEI-generated template. If a future
form revision shifts them, rows simply fail the structural check and get
skipped (surfaced via `ParsedDisclosure.notes`), not silently misparsed.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date

try:
    import pdfplumber
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "pdfplumber is required for disclosure PDF parsing (Phase B): pip install pdfplumber"
    ) from exc

# Column anchors (x0, points) measured from real filings — Indonesian table:
# Jenis Transaksi, Repurchase Agreement, Status Kepemilikan, Nama Pemilik
# Langsung, Jumlah EBE belum dilaksanakan (skipped — warrant/option-specific,
# not needed for signals), Batas Akhir Periode Pelaksanaan (skipped, ditto),
# Jumlah Saham (Unit), Klasifikasi Saham, Harga (Rp), Tanggal Transaksi,
# Tujuan Transaksi.
_COL_ANCHORS = (47.0, 97.0, 146.0, 198.0, 240.0, 299.0, 353.0, 406.0, 450.0, 494.0, 536.0)
_COL_JENIS, _COL_REPO, _COL_STATUS, _COL_PEMILIK = 0, 1, 2, 3
_COL_QTY, _COL_KLAS, _COL_HARGA, _COL_TGL, _COL_TUJUAN = 6, 7, 8, 9, 10

_ROW_GAP_PT = 12.0                        # max top-gap within one row's wrapped sub-lines
_QTY_RE = re.compile(r"^[\d.]+,\d{2}$")   # Indonesian-formatted number, e.g. "1.234,00"

_MONTHS_ID = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "mei": 5, "jun": 6,
    "jul": 7, "agu": 8, "ags": 8, "sep": 9, "okt": 10, "nov": 11, "des": 12,
}
_YES = {"ya", "yes"}
_NO = {"tidak", "no"}
_DIRECTION = {"pembelian": "buy", "penjualan": "sell"}


def _nearest_col(x0: float) -> int:
    return min(range(len(_COL_ANCHORS)), key=lambda i: abs(_COL_ANCHORS[i] - x0))


def _id_number(s: str) -> float | None:
    """'11.170.700,00' -> 11170700.0. None if unparsable/blank/'-'."""
    s = s.strip()
    if not s or s == "-":
        return None
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _id_percent(s: str) -> float | None:
    """'0,0346%' -> 0.000346 (a fraction, e.g. use *100 to display as %)."""
    s = s.strip().rstrip("%").strip()
    if not s:
        return None
    try:
        return float(s.replace(".", "").replace(",", ".")) / 100.0
    except ValueError:
        return None


def _id_date(s: str) -> date | None:
    """'30-Jul-2026' or 'DD-Mon' + 'YYYY' squished by the form's own line-wrap
    ('30-Jul2026') -> date. Month name may be Indonesian or English abbrev
    (both 3-letter for the months that differ: Mei/May, Agu/Aug, Okt/Oct,
    Des/Dec all map via the Indonesian table since we only see Indonesian
    pages; kept lenient regardless)."""
    m = re.search(r"(\d{1,2})-?\s*([A-Za-z]{3})-?\s*(\d{4})", s)
    if not m:
        return None
    day, mon, year = m.groups()
    month = _MONTHS_ID.get(mon.lower())
    if not month:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def _id_bool(s: str) -> bool | None:
    s = s.strip().lower()
    if s in _YES:
        return True
    if s in _NO:
        return False
    return None


def _direction_of(jenis: str | None) -> str | None:
    if not jenis:
        return None
    return _DIRECTION.get(jenis.strip().lower(), "other")


@dataclass(slots=True)
class DisclosureHeader:
    reporter_name: str | None = None
    is_insider: bool | None = None             # Anggota Direksi/Dewan Komisaris
    position: str | None = None                # Jabatan
    citizenship: str | None = None
    shares_before: float | None = None
    voting_rights_before: float | None = None  # fraction, e.g. 0.000346 == 0.0346%
    shares_after: float | None = None
    voting_rights_after: float | None = None
    is_controller: bool | None = None          # Keterangan Pengendali


@dataclass(slots=True)
class DisclosureTransaction:
    transaction_type: str | None = None        # raw Indonesian label, e.g. "Pembelian"
    direction: str | None = None               # 'buy' | 'sell' | 'other'
    repurchase_agreement: bool | None = None
    ownership_status: str | None = None        # Langsung / Tidak Langsung
    direct_investor: str | None = None         # Nama Pemilik Langsung, if indirect
    shares_qty: float | None = None            # Jumlah Saham (Unit), this row
    share_class: str | None = None             # Klasifikasi Saham
    price: float | None = None                 # Harga (Rp)
    transaction_date: date | None = None
    purpose: str | None = None                 # Tujuan Transaksi
    raw_row: str = ""                          # joined row text, for debugging bad parses


@dataclass(slots=True)
class ParsedDisclosure:
    header: DisclosureHeader
    transactions: list[DisclosureTransaction] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # non-fatal parse issues


_HEADER_PATTERNS = {
    "reporter_name": r"Nama \(sesuai SID\)\s*:\s*(.+)",
    "is_insider": r"Anggota Direksi/Dewan Komisaris\s*:\s*(\S+)",
    "position": r"Jabatan\s*:\s*(.+)",
    "citizenship": r"Kewarganegaraan\s*:\s*(.+)",
    "shares_before": r"Jumlah Saham Sebelum Transaksi\s*:\s*([\d.,]+)",
    "vr_before": r"Hak Suara Sebelum Transaksi\s*:\s*([\d,]+)\s*%",
    "shares_after": r"Jumlah Saham Setelah Transaksi\s*:\s*([\d.,]+)",
    "vr_after": r"Hak Suara Setelah Transaksi\s*:\s*([\d,]+)\s*%",
    "is_controller": r"Keterangan Pengendali\s*:\s*(\S+)",
}


def _parse_header(text: str) -> DisclosureHeader:
    def find(key: str) -> str | None:
        m = re.search(_HEADER_PATTERNS[key], text)
        return m.group(1).strip() if m else None

    return DisclosureHeader(
        reporter_name=find("reporter_name"),
        is_insider=_id_bool(find("is_insider") or ""),
        position=find("position"),
        citizenship=find("citizenship"),
        shares_before=_id_number(find("shares_before") or ""),
        voting_rights_before=_id_percent(find("vr_before") or ""),
        shares_after=_id_number(find("shares_after") or ""),
        voting_rights_after=_id_percent(find("vr_after") or ""),
        is_controller=_id_bool(find("is_controller") or ""),
    )


def _row_bands(words: list[dict]) -> list[list[dict]]:
    """Cluster words into row-bands by top-gap. No attempt is made to tell
    table rows from surrounding letter/legal text here — that's the caller's
    job via the Jumlah-Saham structural check, since a row band from a
    non-table part of the page is harmless: it just never matches."""
    ws = sorted(words, key=lambda w: w["top"])
    if not ws:
        return []
    rows: list[list[dict]] = [[ws[0]]]
    for w in ws[1:]:
        if w["top"] - rows[-1][-1]["top"] > _ROW_GAP_PT:
            rows.append([])
        rows[-1].append(w)
    return rows


def _col_text(band: list[dict], col: int) -> str:
    ws = sorted((w for w in band if _nearest_col(w["x0"]) == col), key=lambda w: w["x0"])
    return " ".join(w["text"] for w in ws).strip()


def parse_disclosure_pdf(pdf_bytes: bytes) -> ParsedDisclosure:
    header_text_parts: list[str] = []
    transactions: list[DisclosureTransaction] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if re.search(r"\bREPORT OF OWNERSHIP\b", text):
                break  # English duplicate starts here — Indonesian pages done
            header_text_parts.append(text)

            for band in _row_bands(page.extract_words(use_text_flow=False, keep_blank_chars=False)):
                qty_text = _col_text(band, _COL_QTY)
                if not _QTY_RE.match(qty_text):
                    continue  # not a transaction row (header/footer/boilerplate on this page)
                jenis = _col_text(band, _COL_JENIS) or None
                pemilik = _col_text(band, _COL_PEMILIK)
                transactions.append(DisclosureTransaction(
                    transaction_type=jenis,
                    direction=_direction_of(jenis),
                    repurchase_agreement=_id_bool(_col_text(band, _COL_REPO)),
                    ownership_status=_col_text(band, _COL_STATUS) or None,
                    direct_investor=pemilik if pemilik and pemilik != "-" else None,
                    shares_qty=_id_number(qty_text),
                    share_class=_col_text(band, _COL_KLAS) or None,
                    price=_id_number(_col_text(band, _COL_HARGA)),
                    transaction_date=_id_date(_col_text(band, _COL_TGL)),
                    purpose=_col_text(band, _COL_TUJUAN) or None,
                    raw_row=" | ".join(
                        w["text"] for w in sorted(band, key=lambda w: (w["top"], w["x0"]))
                    ),
                ))

    header = _parse_header("\n".join(header_text_parts))
    notes = [] if transactions else ["no transaction rows structurally recognized"]
    return ParsedDisclosure(header=header, transactions=transactions, notes=notes)
