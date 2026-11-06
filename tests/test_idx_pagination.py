"""IDX GetAnnouncement pagination (audit 2026-11-07, B15): `indexFrom` is a PAGE NUMBER, not a row offset.

Observed live on 7-Oct-2026 ('Registrasi Pemegang Efek'): ResultCount=222; indexFrom=0 -> 200 rows, indexFrom=1 -> 22 rows,
indexFrom=200 -> 0 rows. The old loop stepped indexFrom by the page size and so kept only the first page."""

import json

import pytest

from vg.sources import disclosure, idx, trading_halt


def _fake_idx(total: int, page_size: int):
    calls = []

    def get(base, path, params, max_retries=3):
        page = int(params["indexFrom"])
        calls.append(page)
        lo = page * page_size
        rows = [{"pengumuman": {"Kode_Emiten": f"T{i:03d}", "NoPengumuman": f"N{i}"}} for i in range(lo, min(lo + page_size, total))]
        return json.dumps({"ResultCount": total, "Replies": rows}).encode()

    return get, calls


@pytest.mark.parametrize("module,make", [
    (disclosure, lambda: disclosure.IdxDisclosureSource(base_url="http://x", keyword="Registrasi Pemegang Efek")),
    (trading_halt, lambda: trading_halt.IdxTradingHaltSource(base_url="http://x")),
])
def test_every_page_is_fetched_by_page_number(monkeypatch, module, make):
    get, calls = _fake_idx(222, module._PAGE_SIZE)
    monkeypatch.setattr(idx, "_idx_get", get)
    from datetime import date

    out = json.loads(make()._download(date(2026, 10, 7)))
    assert calls == [0, 1] and len(out["Replies"]) == 222 and out["ResultCount"] == 222


def test_a_server_that_never_stops_is_capped(monkeypatch):
    def endless(base, path, params, max_retries=3):
        return json.dumps({"ResultCount": 10**9, "Replies": [{"pengumuman": {"Kode_Emiten": "X"}}]}).encode()

    monkeypatch.setattr(idx, "_idx_get", endless)
    from datetime import date

    out = json.loads(disclosure.IdxDisclosureSource(base_url="http://x")._download(date(2026, 10, 7)))
    assert len(out["Replies"]) == disclosure._MAX_PAGES + 1
