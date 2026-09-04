"""Raw artifact store: every source response saved to disk with a sha256,
recorded in raw_artifacts. Lets us recompute/replay without re-fetching."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import psycopg

from .config import CONFIG


def store(
    source: str,
    logical_date: date | None,
    content: bytes,
    ext: str = "json",
    *,
    stem: str | None = None,
) -> tuple[Path, str, int]:
    """Write `content` to <artifact_dir>/<source>/<stem>.<ext> and return
    (path, sha256, bytes). `stem` defaults to the ISO date (or a hash prefix); pass it
    when a source stores several files per logical_date (e.g. one PDF per filing)."""
    sha = hashlib.sha256(content).hexdigest()
    subdir = CONFIG.artifact_dir / source
    subdir.mkdir(parents=True, exist_ok=True)
    stem = stem or (logical_date.isoformat() if logical_date else sha[:16])
    path = subdir / f"{stem}.{ext.lstrip('.')}"
    path.write_bytes(content)
    return path, sha, len(content)


def record(
    cur: psycopg.Cursor,
    source: str,
    logical_date: date | None,
    path: Path,
    sha256: str,
    n_bytes: int,
) -> None:
    cur.execute(
        """
        INSERT INTO raw_artifacts (source, logical_date, path, sha256, bytes)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (source, logical_date, sha256) DO NOTHING
        """,
        (source, logical_date, str(path), sha256, n_bytes),
    )
