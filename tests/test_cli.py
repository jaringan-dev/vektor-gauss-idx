from datetime import date

import pytest

from vg.__main__ import _report, main
from vg.collect import CollectOutcome

D = date(2026, 9, 1)


@pytest.mark.parametrize("argv", [["--help"], ["calc", "--help"], ["collect-disclosure", "--help"]])
def test_help_renders(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 0
    assert "usage: vg" in capsys.readouterr().out


@pytest.mark.parametrize("status", ["ok", "no_data", "date_mismatch"])
def test_report_success_statuses_exit_zero(status):
    assert _report([CollectOutcome(D, status, 0)]) == 0


@pytest.mark.parametrize("status", ["error", "blocked", "partial"])
def test_report_failure_statuses_exit_one(status, capsys):
    outs = [CollectOutcome(D, "ok", 10), CollectOutcome(D, status, 0, "boom")]
    assert _report(outs, "days with data") == 1
    captured = capsys.readouterr()
    assert "done: 1/2 days with data" in captured.out
    assert "FAILED: 1/2" in captured.err
