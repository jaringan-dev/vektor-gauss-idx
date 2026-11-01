"""Tiny thread-safe TTL cache for slow, rarely-changing read queries (checklist B1).

Why: the tracker's in-sample table runs `engine.eval_tags()` over the whole history
(~2.9s) on every page load, yet the answer only changes after `vg compute` /
`vg backfill-outcomes`. We cache it, invalidate on a cheap `version` token, and always
report WHEN the value was computed so the page never hides that it is cached
(RENCANA-UI-UX §2: honest numbers).

Pure stdlib, no DB — unit-testable with an injected clock.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Hashable


@dataclass
class _Entry:
    mono: float       # monotonic time it was computed (for TTL)
    wall: float       # wall-clock epoch it was computed (for display)
    version: Any
    value: Any


class TTLCache:
    def __init__(
        self,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ):
        self.ttl = ttl_seconds
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._entries: dict[Hashable, _Entry] = {}

    def get_or_compute(
        self,
        key: Hashable,
        compute: Callable[[], Any],
        *,
        version: Any = None,
        fresh: bool = False,
    ) -> tuple[Any, float, bool]:
        """Return (value, computed_at_epoch, was_cache_hit).

        A stored value is reused only if it is younger than the TTL AND its `version`
        token still matches the caller's current one AND `fresh` is not requested.
        `compute` runs outside the lock so one slow query never blocks other keys; two
        simultaneous cold requests may both compute — harmless for read-only queries.
        """
        now = self._clock()
        with self._lock:
            e = self._entries.get(key)
            if e is not None and not fresh and e.version == version and now - e.mono < self.ttl:
                return e.value, e.wall, True
        value = compute()
        entry = _Entry(self._clock(), self._wall(), version, value)
        with self._lock:
            self._entries[key] = entry
        return value, entry.wall, False

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
