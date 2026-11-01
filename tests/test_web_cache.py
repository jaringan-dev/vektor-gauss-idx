"""TTLCache (vg.web.cache) — pure, injected clock, no DB."""

import pytest

from vg.web.cache import TTLCache


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _cache(ttl=600):
    clock = Clock()
    return TTLCache(ttl, clock=clock, wall=lambda: 5_000_000 + clock.t), clock


def test_second_call_within_ttl_is_a_hit_and_does_not_recompute():
    cache, _ = _cache()
    calls = []
    v1, _, hit1 = cache.get_or_compute("k", lambda: calls.append(1) or "A")
    v2, _, hit2 = cache.get_or_compute("k", lambda: calls.append(1) or "B")
    assert (v1, hit1) == ("A", False)
    assert (v2, hit2) == ("A", True)
    assert len(calls) == 1


def test_expires_after_ttl():
    cache, clock = _cache(ttl=600)
    cache.get_or_compute("k", lambda: "old")
    clock.t += 601
    v, _, hit = cache.get_or_compute("k", lambda: "new")
    assert (v, hit) == ("new", False)


def test_still_fresh_just_before_ttl():
    cache, clock = _cache(ttl=600)
    cache.get_or_compute("k", lambda: "old")
    clock.t += 599
    assert cache.get_or_compute("k", lambda: "new")[2] is True


def test_version_change_invalidates_even_inside_ttl():
    """backfill-outcomes changes the version token -> numbers must be recomputed at once."""
    cache, _ = _cache()
    cache.get_or_compute("k", lambda: "v1-data", version=("ts1", 100))
    v, _, hit = cache.get_or_compute("k", lambda: "v2-data", version=("ts2", 101))
    assert (v, hit) == ("v2-data", False)


def test_fresh_flag_bypasses_cache_and_refreshes_it():
    cache, _ = _cache()
    cache.get_or_compute("k", lambda: "old")
    v, _, hit = cache.get_or_compute("k", lambda: "forced", fresh=True)
    assert (v, hit) == ("forced", False)
    assert cache.get_or_compute("k", lambda: "x")[0] == "forced"


def test_keys_are_independent():
    cache, _ = _cache()
    cache.get_or_compute(("tracker", None), lambda: "insample")
    v, _, hit = cache.get_or_compute(("tracker", "2026-09-04"), lambda: "forward")
    assert (v, hit) == ("forward", False)


def test_reports_when_value_was_computed():
    cache, clock = _cache()
    _, t_first, _ = cache.get_or_compute("k", lambda: "A")
    clock.t += 120
    _, t_again, hit = cache.get_or_compute("k", lambda: "B")
    assert hit is True
    assert t_again == t_first  # still the ORIGINAL computation time, not "now"


def test_failed_compute_raises_and_is_not_cached():
    cache, _ = _cache()

    def boom():
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        cache.get_or_compute("k", boom)
    v, _, hit = cache.get_or_compute("k", lambda: "recovered")
    assert (v, hit) == ("recovered", False)


def test_clear():
    cache, _ = _cache()
    cache.get_or_compute("k", lambda: "A")
    cache.clear()
    assert cache.get_or_compute("k", lambda: "B")[2] is False
