"""`vg backfill-wash`: the in-sample history of AKUM-NETRAL-WASH, written only before the forward-test window and only from the stored
broker scores, with the same rule `compute` uses."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from vg import __main__ as cli  # imported before any test swaps vg.config.CONFIG for a stand-in
from vg import engine

FWD = date(2026, 9, 4)
FIRST = date(2025, 9, 16)


def test_the_rule_in_one_place():
    assert engine.is_wash(0.10, 2) and engine.is_wash(0.5, 3)
    assert not engine.is_wash(0.0999, 2)            # below the bpi threshold
    assert not engine.is_wash(0.5, 1)               # only one house on both sides
    assert not engine.is_wash(None, 2) and not engine.is_wash(0.5, None)


class World:
    """A fake database: `scores` are the (session_date, ticker, bpi3, overlap3, close) rows the SELECT returns."""

    def __init__(self, scores, first=FIRST):
        self.scores, self.first = scores, first
        self.selects: list[tuple] = []
        self.written: list[tuple] = []
        self.logged: list[tuple] = []


@pytest.fixture
def world(monkeypatch):
    scores = [
        (date(2025, 10, 1), "AAAA", 0.30, 3, 1000.0),       # a hit
        (date(2025, 10, 1), "BBBB", 0.05, 3, 500.0),        # bpi too low
        (date(2025, 10, 2), "CCCC", 0.40, 1, 300.0),        # overlap too low
        (date(2026, 3, 2), "DDDD", 0.10, 2, 200.0),         # a hit exactly on the thresholds
        (date(2026, 9, 3), "EEEE", 0.20, 2, 150.0),         # the last allowed day
    ]
    w = World(scores)

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            self.sql = sql
            if "FROM signal_components sc" in sql:
                w.selects.append(params)
                self.rows = [r for r in w.scores if params[0] <= r[0] <= params[1]]
            else:
                self.rows = [(w.first,)]

        def fetchone(self):
            return self.rows[0]

        def fetchall(self):
            return self.rows

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return Cur()

    monkeypatch.setattr(engine, "connect", lambda: Conn())
    monkeypatch.setattr(engine, "upsert_many", lambda cur, table, cols, rows, **kw: (w.written.append((table, cols, list(rows), kw)), len(rows))[1])
    monkeypatch.setattr(engine, "_log", lambda cur, phase, sd, status, rows, err: w.logged.append((phase, sd, status, rows, err)))
    set_forward(monkeypatch, FWD)
    return w


def set_forward(monkeypatch, d):
    monkeypatch.setattr("vg.config.CONFIG", SimpleNamespace(forward_since=d))


def test_it_writes_only_the_hits_before_the_window(world):
    out = engine.backfill_wash()
    assert out["rows"] == 3 and out["written"] == 3 and out["days"] == 3
    assert out["start"] == FIRST and out["end"] == date(2026, 9, 3)
    assert out["by_month"] == {"2025-10": 1, "2026-03": 1, "2026-09": 1}
    table, cols, rows, kw = world.written[0]
    assert table == "signal_log" and cols == ("session_date", "ticker", "tag", "layer", "close")
    assert kw["conflict"] == ("session_date", "ticker", "tag")
    assert rows == [(date(2025, 10, 1), "AAAA", "AKUM-NETRAL-WASH", "probation", 1000.0),
                    (date(2026, 3, 2), "DDDD", "AKUM-NETRAL-WASH", "probation", 200.0),
                    (date(2026, 9, 3), "EEEE", "AKUM-NETRAL-WASH", "probation", 150.0)]
    assert world.logged and world.logged[0][0] == "backfill_wash" and "nothing at or after 2026-09-04" in world.logged[0][4]


def test_dry_run_counts_and_writes_nothing(world):
    out = engine.backfill_wash(dry_run=True)
    assert out["rows"] == 3 and out["written"] == 0
    assert world.written == [] and world.logged == []


def test_a_range_that_reaches_the_forward_window_is_refused_and_nothing_is_touched(world):
    for end in (FWD, date(2026, 11, 6)):
        with pytest.raises(engine.WashBackfillRefused, match="inside the forward-test window"):
            engine.backfill_wash(end=end)
    assert world.selects == [] and world.written == []


def test_without_a_forward_date_nothing_is_written(world, monkeypatch):
    set_forward(monkeypatch, None)
    with pytest.raises(engine.WashBackfillRefused, match="VG_FORWARD_SINCE is not set"):
        engine.backfill_wash()
    assert world.written == []


def test_the_start_is_clipped_to_the_first_stored_score_and_can_be_narrowed(world):
    assert engine.backfill_wash(date(2020, 1, 1), dry_run=True)["start"] == FIRST
    out = engine.backfill_wash(date(2026, 1, 1), date(2026, 6, 30), dry_run=True)
    assert out["rows"] == 1 and out["by_month"] == {"2026-03": 1}
    assert world.selects[-1] == (date(2026, 1, 1), date(2026, 6, 30))


def test_no_stored_scores_is_not_an_error(world):
    world.first = None
    out = engine.backfill_wash()
    assert out["start"] is None and out["rows"] == 0 and world.written == []


def test_the_command_is_registered_and_prints_the_in_sample_warning(world, monkeypatch, capsys):
    monkeypatch.setattr(engine, "backfill_outcomes", lambda: 7)
    assert cli.main(["backfill-wash"]) == 0
    out = capsys.readouterr().out
    assert "wrote 3 rows on 3 sessions" in out and "IN-SAMPLE" in out and "forward returns: 7" in out
    assert cli.main(["backfill-wash", "--to", "2026-11-06"]) == 2
    assert "refused" in capsys.readouterr().out
    assert cli.main(["backfill-wash", "--dry-run"]) == 0
    assert "would write" in capsys.readouterr().out
