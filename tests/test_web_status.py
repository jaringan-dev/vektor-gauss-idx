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


def test_is_small_n_boundary():
    assert gate.is_small_n(gate.MIN_SIGNALS - 1) is True
    assert gate.is_small_n(gate.MIN_SIGNALS) is False
    assert gate.is_small_n(None) is True
    assert gate.is_small_n(0) is True


def test_order_rows_puts_small_n_last_even_with_a_spectacular_edge():
    rows = [
        {"tag": "FLOAT-ASING-PADAT", "n": 34, "edge_h5": 0.0529},   # tiny sample, huge edge
        {"tag": "BROKER-ASING-AKUM", "n": 1320, "edge_h5": -0.0062},
        {"tag": "KSEI-KONFIRM", "n": 96, "edge_h5": -0.012},
        {"tag": "INSIDER-AKUM", "n": 49, "edge_h5": 0.0009},
    ]
    assert [r["tag"] for r in gate.order_rows(rows)] == [
        "BROKER-ASING-AKUM", "KSEI-KONFIRM",      # well-sampled, n descending
        "INSIDER-AKUM", "FLOAT-ASING-PADAT",       # small n, n descending, always below
    ]


def test_order_rows_does_not_mutate_input_and_is_stable_for_ties():
    rows = [{"tag": "A", "n": 100}, {"tag": "B", "n": 100}, {"tag": "C", "n": 200}]
    out = gate.order_rows(rows)
    assert [r["tag"] for r in out] == ["C", "A", "B"]
    assert [r["tag"] for r in rows] == ["A", "B", "C"]


def test_span_days_handles_missing_dates():
    assert gate.span_days(None, None) == 0
    assert gate.span_days(date(2026, 1, 1), date(2026, 3, 2)) == 60
