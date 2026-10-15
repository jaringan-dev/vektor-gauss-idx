"""Gate-classification logic (vg.web.status). Pure, no DB, no web deps."""

from datetime import date, timedelta

from vg.web import status as gate


def _row(n: int, edge: float | None, span_days: int) -> dict:
    first = date(2026, 1, 1)
    return {"n": n, "edge_h5": edge, "first_date": first, "last_date": first + timedelta(days=span_days)}


def test_probation_when_too_few_signals():
    st = gate.classify(_row(10, 0.05, 90))
    assert st.code == gate.PROBATION
    assert st.enough_data is False


def test_probation_when_span_too_short():
    st = gate.classify(_row(100, 0.05, 10))
    assert st.code == gate.PROBATION


def test_ready_when_edge_beats_cost():
    st = gate.classify(_row(100, gate.ROUND_TRIP_COST + 0.01, 90))
    assert st.code == gate.READY
    assert st.edge_after_cost is not None and st.edge_after_cost > 0


def test_failing_when_edge_below_cost():
    st = gate.classify(_row(100, gate.ROUND_TRIP_COST - 0.005, 90))
    assert st.code == gate.FAILING
    assert st.enough_data is True


def test_failing_when_edge_negative():
    assert gate.classify(_row(100, -0.02, 90)).code == gate.FAILING


def test_missing_edge_is_not_ready():
    st = gate.classify(_row(100, None, 90))
    assert st.code == gate.FAILING
    assert st.edge_after_cost is None


def test_span_days_handles_missing_dates():
    assert gate.span_days(None, None) == 0
    assert gate.span_days(date(2026, 1, 1), date(2026, 3, 2)) == 60
