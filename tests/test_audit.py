"""vg.audit: the data-integrity findings are pure functions of the facts (no database needed)."""

from datetime import date

from vg import audit
from vg.status import BAD, OK, WARN

# The live numbers of 2026-11-07, when the audit was written.
LIVE = {
    "since": "2025-01-01", "ref_days": 62, "ref_days_unknown": 39, "ref_days_shares_lag": 32,
    "splits": 136, "splits_not_priced": 64, "bars_latest": 963, "tradable_eq_listed": 925,
    "scripless_month": "2026-10-30", "scripless_tickers": 1005, "scripless_jumps": 12,
    "broker_days_30d": 5633, "broker_copies_30d": 41,
    "adj_computed": "2026-11-02", "adj_last_div": "2026-10-29", "ca_last_div": "2026-11-06",
    "ret_rows_120d": 40000, "ret_extreme_120d": 42, "bars_30d": 21186, "bars_bad_30d": 0,
    "akum_90d": 5410, "dist_90d": 19,
}
CLEAN = {**LIVE, "ref_days_unknown": 0, "ref_days_shares_lag": 0, "splits_not_priced": 0, "tradable_eq_listed": 100,
         "scripless_jumps": 0, "broker_copies_30d": 0, "adj_computed": "2026-11-06", "adj_last_div": "2026-11-06",
         "ret_extreme_120d": 0, "akum_90d": 300, "dist_90d": 200}
TODAY = date(2026, 11, 7)


def _by_name(findings):
    return {c.name: c for c in findings}


def test_the_live_state_of_2026_11_07_is_bad_for_the_numbers_that_feed_returns():
    f = _by_name(audit.build_findings(LIVE, today=TODAY))
    for name in ("Corporate-action days", "Splits not priced by IDX", "Broker table copies", "Extreme 5-day returns"):
        assert f[name].level == BAD, name
    for name in ("Share count lag", "Free float column", "KSEI scripless base", "Adjustment factors", "One-sided broker measure"):
        assert f[name].level == WARN, name
    assert f["Price bar sanity"].level == OK
    assert "39 have no split/bonus factor" in f["Corporate-action days"].text
    assert "96%" in f["Free float column"].text                                   # 925 / 963
    assert "5 d ago" in f["Adjustment factors"].text and "2026-11-06 announced" in f["Adjustment factors"].text


def test_clean_facts_give_no_warning_and_no_failure():
    assert {c.level for c in audit.build_findings(CLEAN, today=TODAY)} == {OK}


def test_stale_factors_warn_even_when_no_dividend_is_missing():
    old = {**CLEAN, "adj_computed": "2026-10-20"}
    assert _by_name(audit.build_findings(old, today=TODAY))["Adjustment factors"].level == WARN


def test_an_audit_that_cannot_run_is_a_failure_not_silence():
    findings = audit.build_findings({}, ["OperationalError: connection refused"])
    assert len(findings) == 1 and findings[0].level == BAD and "connection refused" in findings[0].text


def test_missing_facts_are_skipped_not_guessed():
    findings = _by_name(audit.build_findings({"since": "2025-01-01", "bars_30d": 10, "bars_bad_30d": 0}, today=TODAY))
    assert set(findings) == {"Price bar sanity"}
