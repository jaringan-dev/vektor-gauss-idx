"""Schema-validated, idempotent upsert on top of `vg.db.upsert_many`.

`upsert_many` trusts its rows. `validated_upsert` puts a `vg.schemas` model in front of it:

    items --(build)--> model.model_validate --> in-batch de-dup --> upsert_many --> UpsertReport

Policy (chosen so a bad row can never poison a table, and a broken parser can never write half a day):

* A row that fails validation is **rejected on its own**; the valid rows still go in. Every rejection is kept in
  the returned `UpsertReport` (row index + which fields failed and why), never silently dropped.
* If more than `max_reject_ratio` of the batch is rejected (default 10 %), nothing is written and `UpsertAborted`
  is raised: that pattern means the feed or the parser changed shape, not that a few rows are odd. The caller's
  existing error handling (`collect._log_error` -> `run_log`) then records it like any other failed collect.
* Two rows with the same conflict key inside one batch collapse to the last one (counted in `duplicates`).
* Idempotence comes from the table's own `ON CONFLICT (...) DO UPDATE`, driven by the model's `CONFLICT` /
  `update_columns()`; running the same batch twice leaves the table unchanged.

Wiring example (`collect._persist_done_by_price`, same table, same conflict key, same columns as today):

    from ..db.validated import validated_upsert
    from ..schemas import DoneByPriceRow

    report = validated_upsert(
        cur, DoneByPriceRow, res.rows,
        build=lambda r: DoneByPriceRow.from_source(
            r, session_date=res.session_date, ticker=res.ticker,
            source=res.source, source_tier=res.source_tier),
    )
    detail = report.summary()          # "done_by_price: 212 upserted, 0 rejected"
    return report.upserted

`build` runs inside the validation guard, so a `from_source()` that raises `ValidationError` is a rejected row,
not a crashed collect. Any other exception (a bug) still propagates.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

import psycopg
from pydantic import ValidationError

from ..schemas import RowModel
from . import upsert_many

log = logging.getLogger(__name__)

M = TypeVar("M", bound=RowModel)

DEFAULT_MAX_REJECT_RATIO = 0.10


@dataclass(frozen=True, slots=True)
class RejectedRow:
    index: int                      # position in the submitted batch
    errors: tuple[str, ...]         # "field: message" - values are deliberately not echoed


@dataclass(slots=True)
class UpsertReport:
    table: str
    submitted: int = 0
    upserted: int = 0               # rows sent to INSERT .. ON CONFLICT DO UPDATE (inserted or refreshed)
    duplicates: int = 0             # same conflict key twice in the batch; the last one won
    rejected: list[RejectedRow] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected

    def summary(self, examples: int = 3) -> str:
        s = f"{self.table}: {self.upserted} upserted, {len(self.rejected)} rejected"
        if self.duplicates:
            s += f", {self.duplicates} duplicate keys"
        if self.rejected:
            shown = "; ".join(f"#{r.index} {', '.join(r.errors[:2])}" for r in self.rejected[:examples])
            more = len(self.rejected) - examples
            s += f" [{shown}{f'; +{more} more' if more > 0 else ''}]"
        return s


class UpsertAborted(RuntimeError):
    """Too many rows failed validation; nothing was written. `.report` has the details."""

    def __init__(self, report: UpsertReport, max_reject_ratio: float):
        self.report = report
        super().__init__(
            f"{len(report.rejected)}/{report.submitted} rows failed validation "
            f"(limit {max_reject_ratio:.0%}), nothing written - {report.summary()}"
        )


def _brief(exc: ValidationError) -> tuple[str, ...]:
    return tuple(f"{'.'.join(str(p) for p in e['loc']) or '<row>'}: {e['msg']}" for e in exc.errors())


def validate_batch(
    model: type[M],
    items: Iterable[Any],
    *,
    build: Callable[[Any], M | Mapping[str, Any]] | None = None,
    max_reject_ratio: float = DEFAULT_MAX_REJECT_RATIO,
) -> tuple[list[M], UpsertReport]:
    """Validation half of `validated_upsert`, for callers that keep their own INSERT (for example the
    price-history upsert, whose `WHERE source_tier >= ...` guard `upsert_many` cannot express).

    Returns the accepted rows (de-duplicated by conflict key, last one wins) and the report (`upserted` is 0 -
    the caller writes). Raises `UpsertAborted` when too many rows are rejected."""
    report = UpsertReport(table=model.TABLE)
    good: dict[tuple, M] = {}

    for i, item in enumerate(items):
        report.submitted += 1
        try:
            built = build(item) if build is not None else item
            row = built if isinstance(built, model) else model.model_validate(built)
        except ValidationError as exc:
            report.rejected.append(RejectedRow(i, _brief(exc)))
            continue
        k = row.key()
        if k in good:
            report.duplicates += 1
        good[k] = row

    if len(report.rejected) > max_reject_ratio * report.submitted:
        log.error("validated_upsert aborted: %s", report.summary())
        raise UpsertAborted(report, max_reject_ratio)
    if report.rejected:
        log.warning("validated_upsert rejected rows: %s", report.summary())
    return list(good.values()), report


def validated_upsert(
    cur: psycopg.Cursor,
    model: type[M],
    items: Iterable[Any],
    *,
    build: Callable[[Any], M | Mapping[str, Any]] | None = None,
    max_reject_ratio: float = DEFAULT_MAX_REJECT_RATIO,
) -> UpsertReport:
    """Validate `items` against `model` and upsert the good ones into `model.TABLE`.

    `items` are model instances, mappings, or - with `build` - anything `build` turns into one of those
    (typically a source dataclass and `Model.from_source`). Returns an `UpsertReport`;
    raises `UpsertAborted` (before any write) when too large a share of the batch is rejected."""
    rows, report = validate_batch(model, items, build=build, max_reject_ratio=max_reject_ratio)
    report.upserted = upsert_many(
        cur, model.TABLE, model.COLUMNS, [r.as_row() for r in rows],
        conflict=model.CONFLICT, update=model.update_columns(),
    )
    return report
