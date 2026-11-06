"""The daily KSEI check: ask until last month's ownership file is stored, then stop asking."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from vg import __main__ as cli
from vg import collect
from vg.collect import CollectOutcome


class Cur:
    def __init__(self, newest):
        self.newest = newest

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        assert sql.lstrip().upper().startswith("SELECT")

    def fetchone(self):
        return (self.newest,)


class Conn:
    def __init__(self, newest):
        self.newest = newest

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return Cur(self.newest)


def holding(monkeypatch, newest):
    monkeypatch.setattr(collect, "connect", lambda: Conn(newest))


@pytest.mark.parametrize(("today", "newest", "missing"), [
    (date(2026, 11, 3), date(2026, 10, 30), False),      # October's file is in: nothing to ask for
    (date(2026, 11, 20), date(2026, 10, 30), False),     # still October's, all month long
    (date(2026, 12, 3), date(2026, 10, 30), True),       # November ended and its file is not here yet
    (date(2026, 12, 4), date(2026, 11, 30), False),      # ...and now it is
    (date(2027, 1, 2), date(2026, 11, 30), True),        # across the year boundary: December's file is due
    (date(2027, 1, 7), date(2026, 12, 31), False),
    (date(2026, 11, 3), None, True),                     # an empty table
])
def test_pending_means_last_months_file_is_not_stored_yet(monkeypatch, today, newest, missing):
    holding(monkeypatch, newest)
    assert collect.ownership_pending(today) == (missing, newest)


def test_nothing_is_downloaded_once_last_months_file_is_in(monkeypatch):
    holding(monkeypatch, date(2026, 10, 30))
    monkeypatch.setattr(collect, "collect_ownership_month", lambda m: pytest.fail("must not download"))
    out = collect.collect_ownership_pending(date(2026, 11, 9))
    assert [(o.status, o.rows) for o in out] == [("ok", 0)] and "nothing to fetch" in out[0].detail


def test_one_try_for_last_month_while_it_is_missing(monkeypatch):
    holding(monkeypatch, date(2026, 10, 30))
    asked = []
    monkeypatch.setattr(collect, "collect_ownership_month", lambda m: (asked.append(m), CollectOutcome(m, "no_data", 0, "no file"))[1])
    out = collect.collect_ownership_pending(date(2026, 12, 3))
    assert asked == [date(2026, 11, 30)] and out[0].status == "no_data"       # last month only; this month's file cannot exist yet


def test_the_command_and_the_eod_wiring(monkeypatch, capsys):
    args = cli.build_parser().parse_args(["collect-ownership", "--pending"])
    assert args.pending and not args.latest
    monkeypatch.setattr(collect, "collect_ownership_pending", lambda: [CollectOutcome(date(2026, 10, 30), "ok", 0, "already have")])
    assert cli.main(["collect-ownership", "--pending"]) == 0 and "already have" in capsys.readouterr().out
    eod = (Path(__file__).resolve().parents[1] / "scripts" / "eod.sh").read_text(encoding="utf-8")
    assert "vg collect-ownership --pending || true" in eod
    assert "vg collect-ownership --latest" not in eod                          # the old once-a-month call is gone
    monthly = eod[eod.index('-le 5 ]'):eod.index("fi", eod.index('-le 5 ]'))]
    assert "collect-corpactions" in monthly and "detect-ca" in monthly         # corporate actions stay monthly
