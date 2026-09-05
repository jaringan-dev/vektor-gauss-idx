"""Offline tests for the IDX trading-halt announcement source."""

from __future__ import annotations

from datetime import date

from vg.sources import idx
from vg.sources.trading_halt import IdxTradingHaltSource

_HALT_JSON = (
    b'{"Replies":[{"pengumuman":{"Kode_Emiten":"EDGE","NoPengumuman":"Peng-SPT-00002",'
    b'"TglPengumuman":"2026-09-04T18:49:11",'
    b'"JudulPengumuman":"Penghentian Sementara Perdagangan Efek PT Indointernet Tbk."}}]}'
)
_FOLLOWUP_JSON = (
    b'{"Replies":[{"pengumuman":{"Kode_Emiten":"BAJA","NoPengumuman":"114/SCB/OJK/IDX",'
    b'"TglPengumuman":"2026-08-20T08:51:15",'
    b'"JudulPengumuman":"PERNYATAAN PERSEROAN SEHUBUNGAN DENGAN PEMBUKAAN SUSPENSI '
    b'PERDAGANGAN SAHAM"}}]}'
)


def test_parse_halt_event():
    rows = IdxTradingHaltSource._parse(_HALT_JSON)
    assert len(rows) == 1
    r = rows[0]
    assert r.ticker == "EDGE"
    assert r.no_pengumuman == "Peng-SPT-00002"
    assert r.filed_date == date(2026, 9, 4)
    assert r.is_halt is True


def test_parse_reopening_followup_is_not_a_halt():
    rows = IdxTradingHaltSource._parse(_FOLLOWUP_JSON)
    assert len(rows) == 1
    assert rows[0].ticker == "BAJA"
    assert rows[0].is_halt is False   # a recovery-plan/reopening statement, not the halt itself


def test_parse_pembukaan_and_pencabutan_titles_are_not_fresh_halts():
    # Real titles seen live 2026-09-05: both contain "Penghentian Sementara
    # Perdagangan" as a substring despite being about LIFTING a halt, not
    # declaring one — a bare _HALT_RE match would misclassify these.
    reopening = (
        b'{"Replies":[{"pengumuman":{"Kode_Emiten":"SUPR","NoPengumuman":"A",'
        b'"TglPengumuman":"2026-09-04T10:00:00","JudulPengumuman":'
        b'"Pembukaan Penghentian Sementara Perdagangan Efek PT Solusi Tunas Pratama Tbk"}}]}'
    )
    revocation = (
        b'{"Replies":[{"pengumuman":{"Kode_Emiten":"EDGE","NoPengumuman":"B",'
        b'"TglPengumuman":"2026-09-04T10:00:00","JudulPengumuman":'
        b'"Pencabutan Penghentian Sementara Perdagangan Efek Hanya di Pasar Negosiasi"}}]}'
    )
    assert IdxTradingHaltSource._parse(reopening)[0].is_halt is False
    assert IdxTradingHaltSource._parse(revocation)[0].is_halt is False


def test_parse_skips_untickered():
    raw = (
        b'{"Replies":[{"pengumuman":{"Kode_Emiten":"","NoPengumuman":"X",'
        b'"TglPengumuman":"2026-09-03T10:00:00","JudulPengumuman":"Potensi Delisting"}}]}'
    )
    assert IdxTradingHaltSource._parse(raw) == []


def test_fetch_injected():
    src = IdxTradingHaltSource()
    src._download = lambda sd: _HALT_JSON
    res = src.fetch(date(2026, 9, 4))
    assert res.status == "ok"
    assert len(res.rows) == 1


def test_fetch_no_data():
    src = IdxTradingHaltSource()
    src._download = lambda sd: b'{"ResultCount":0,"Replies":[]}'
    res = src.fetch(date(2026, 9, 4))
    assert res.status == "no_data"
    assert res.rows == []


def test_fetch_blocked():
    src = IdxTradingHaltSource()

    def boom(sd):
        raise idx.IdxAccessBlocked("1020")

    src._download = boom
    res = src.fetch(date(2026, 9, 4))
    assert res.status == "blocked"


def test_halt_columns_align():
    from vg.collect import _HALT_COLS, _halt_row

    rows = IdxTradingHaltSource._parse(_HALT_JSON)
    row = _halt_row(rows[0], "idx_trading_halt", 0)
    assert len(row) == len(_HALT_COLS)
    m = dict(zip(_HALT_COLS, row))
    assert m["ticker"] == "EDGE"
    assert m["is_halt"] is True
    assert m["source_tier"] == 0
