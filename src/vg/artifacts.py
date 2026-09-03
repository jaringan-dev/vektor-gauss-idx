"""Raw artifact store: every source response saved to disk with a sha256,
recorded in raw_artifacts. Lets us recompute/replay without re-fetching."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import psycopg

from .config import CONFIG


def store(source: str, logical_date: date | None, content: bytes) -> tuple[Path, str, int]:
    """Write `content` to <artifact_dir>/<source>/<logical_date>.json and return
    (path, sha256, bytes). Overwrites same-day file (content-addressed by hash in DB)."""
    sha = hashlib.sha256(content).hexdigest()
    subdir = CONFIG.artifact_dir / source
    subdir.mkdir(parents=True, exist_ok=True)
    stem = logical_date.isoformat() if logical_date else sha[:16]
    path = subdir / f"{stem}.json"
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
