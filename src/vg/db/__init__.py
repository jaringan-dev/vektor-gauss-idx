"""Postgres helpers (psycopg 3). `vg.db.validated` adds schema-validated upserts (needs pydantic)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg

from ..config import CONFIG


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Yield a connection; commit on success, rollback on error."""
    conn = psycopg.connect(CONFIG.database_url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_sql_file(path: str | Path) -> None:
    sql = Path(path).read_text(encoding="utf-8")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)


def upsert_many(
    cur: psycopg.Cursor,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    conflict: Sequence[str],
    update: Sequence[str] | None = None,
) -> int:
    """Bulk INSERT ... ON CONFLICT. Returns rows submitted (not net changed)."""
    if not rows:
        return 0
    col_list = ", ".join(columns)
    placeholders = "(" + ", ".join(["%s"] * len(columns)) + ")"
    conflict_list = ", ".join(conflict)
    if update:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update)
        action = f"DO UPDATE SET {set_clause}"
    else:
        action = "DO NOTHING"
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES {placeholders} "
        f"ON CONFLICT ({conflict_list}) {action}"
    )
    cur.executemany(sql, rows)
    return len(rows)
