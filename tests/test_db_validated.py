"""vg.db.validated: reject bad rows individually, abort on a systematic break, stay idempotent (no DB)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("pydantic")

from vg.db.validated import UpsertAborted, validated_upsert
from vg.schemas import DoneByPriceRow
from vg.sources import base as src

DAY = date(2026, 11, 4)


class _Cur:
    def __init__(self):
        self.calls = []

    def executemany(self, sql, rows):
        self.calls.append((sql, list(rows)))


def _item(price, **over):
    d = {"session_date": DAY, "ticker": "BBCA", "board": "RG", "price_num": price, "qty": Decimal(1), "source": "s", "source_tier": 3}
    d.update(over)
    return d


def test_good_rows_are_upserted_with_the_models_conflict_key_and_update_set():
    cur = _Cur()
    rep = validated_upsert(cur, DoneByPriceRow, [_item(100), _item(101)])
    assert rep.ok and rep.submitted == 2 and rep.upserted == 2 and rep.rejected == []
    sql, rows = cur.calls[0]
    assert sql.startswith("INSERT INTO done_by_price (session_date, ticker, board, price_num, qty,")
    assert "ON CONFLICT (session_date, ticker, board, price_num) DO UPDATE SET qty = EXCLUDED.qty" in sql
    assert "price_num = EXCLUDED.price_num" not in sql          # the key is never rewritten
    assert len(rows) == 2


def test_one_bad_row_is_rejected_alone_when_under_the_ratio():
    cur = _Cur()
    items = [_item(p) for p in range(100, 120)] + [_item(5, board="XX")]      # 1 of 21 = 4.8 %
    rep = validated_upsert(cur, DoneByPriceRow, items)
    assert rep.upserted == 20 and len(rep.rejected) == 1 and not rep.ok
    assert rep.rejected[0].index == 20 and rep.rejected[0].errors[0].startswith("board:")
    assert "1 rejected" in rep.summary() and "#20 board:" in rep.summary()


def test_too_many_bad_rows_abort_before_any_write():
    cur = _Cur()
    items = [_item(100)] + [_item(p, board="XX") for p in range(5)]
    with pytest.raises(UpsertAborted) as ei:
        validated_upsert(cur, DoneByPriceRow, items)
    assert cur.calls == []                                     # nothing written
    assert len(ei.value.report.rejected) == 5 and "nothing written" in str(ei.value)


def test_ratio_is_configurable():
    cur = _Cur()
    items = [_item(100), _item(1, board="XX")]
    assert validated_upsert(cur, DoneByPriceRow, items, max_reject_ratio=0.5).upserted == 1
    with pytest.raises(UpsertAborted):
        validated_upsert(cur, DoneByPriceRow, items, max_reject_ratio=0.1)


def test_duplicate_keys_in_one_batch_collapse_to_the_last():
    cur = _Cur()
    rep = validated_upsert(cur, DoneByPriceRow, [_item(100, qty=Decimal(1)), _item(100, qty=Decimal(9))])
    assert rep.duplicates == 1 and rep.upserted == 1
    (row,) = cur.calls[0][1]
    assert row[DoneByPriceRow.COLUMNS.index("qty")] == Decimal(9)


def test_running_the_same_batch_twice_sends_identical_statements():
    a, b = _Cur(), _Cur()
    items = [_item(100), _item(101)]
    validated_upsert(a, DoneByPriceRow, items)
    validated_upsert(b, DoneByPriceRow, items)
    assert a.calls == b.calls


def test_build_wraps_source_dataclasses_and_its_validation_errors_become_rejections():
    good = src.DoneByPriceRow(board="RG", price_num=100, qty=Decimal(5), value=Decimal(500), trades=1)
    bad = src.DoneByPriceRow(board="RG", price_num=0, qty=Decimal(5))             # price must be > 0
    many = [src.DoneByPriceRow(board="NG", price_num=p, qty=Decimal(1)) for p in range(200, 220)]
    cur = _Cur()
    rep = validated_upsert(
        cur, DoneByPriceRow, [good, bad, *many],
        build=lambda r: DoneByPriceRow.from_source(r, session_date=DAY, ticker="BBCA", source="s", source_tier=3))
    assert rep.upserted == 21 and [r.index for r in rep.rejected] == [1]
    assert rep.rejected[0].errors == ("price_num: Input should be greater than 0",)


def test_a_bug_in_build_is_not_swallowed():
    def boom(_):
        raise TypeError("programming error")

    with pytest.raises(TypeError):
        validated_upsert(_Cur(), DoneByPriceRow, [1], build=boom)


def test_empty_batch_is_a_no_op():
    cur = _Cur()
    rep = validated_upsert(cur, DoneByPriceRow, [])
    assert rep.submitted == 0 and rep.upserted == 0 and cur.calls == []
