"""Coverage heatmap logic (vg.web.coverage) — pure, no DB. Uses the REAL incident's numbers."""

from datetime import date, timedelta

import pytest

from vg.web import coverage as cov

INCIDENT_DAYS = (date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 25))   # 5 price rows instead of 962


def _weekdays(n, end=date(2026, 10, 30)):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _incident(n=60):
    days = _weekdays(n)
    price = {d: 962 for d in days}
    signal = {d: 830 for d in days}
    for g in INCIDENT_DAYS:
        price[g], signal[g] = 5, 5
    return days, {"price": price, "signal": signal, "index": {d: 57 for d in days}, "broker": {}}


# ---- the incident that motivated the page ----------------------------------------------

def test_the_14_15_25_september_incident_is_red_and_nothing_else_is():
    days, counts = _incident()
    grid = cov.build_grid(days, counts)
    bad = {r["date"] for r in grid["rows"] if r["cells"]["price"].level == cov.BAD}
    assert bad == set(INCIDENT_DAYS)
    assert grid["summary"]["price"]["bad"] == 3 and grid["summary"]["price"]["warn"] == 0
    assert grid["summary"]["signal"]["bad"] == 3
    assert grid["summary"]["index"]["bad"] == 0


def test_incident_cell_reports_count_symbol_word_and_percentage():
    days, counts = _incident()
    row = next(r for r in cov.build_grid(days, counts)["rows"] if r["date"] == date(2026, 9, 15))
    c = row["cells"]["price"].as_dict()
    assert c == {"level": "bad", "count": 5, "symbol": "✖", "word": "bolong", "pct": 1}


def test_rows_are_newest_first():
    days, counts = _incident()
    dates = [r["date"] for r in cov.build_grid(days, counts)["rows"]]
    assert dates == sorted(dates, reverse=True) and dates[0] == date(2026, 10, 30)


def test_baseline_is_the_median_so_a_few_gaps_do_not_move_it():
    days, counts = _incident()
    assert cov.build_grid(days, counts)["summary"]["price"]["baseline"] == 962


# ---- sources that only began recently --------------------------------------------------

def test_days_before_a_source_existed_are_na_not_red():
    days = _weekdays(120)                                     # reaches back before 3 Jun
    start = date(2026, 6, 3)
    broker = {d: 190 for d in days if d >= start}
    grid = cov.build_grid(days, {"price": {d: 962 for d in days}, "broker": broker})
    assert grid["summary"]["broker"]["start"] == start
    for r in grid["rows"]:
        lvl = r["cells"]["broker"].level
        assert (lvl == cov.NA) if r["date"] < start else (lvl == cov.OK)
    assert grid["summary"]["broker"]["bad"] == 0


def test_a_zero_AFTER_the_source_started_is_a_real_gap():
    days = _weekdays(30)
    broker = {d: 190 for d in days}
    broker[days[20]] = 0
    grid = cov.build_grid(days, {"price": {d: 962 for d in days}, "broker": broker})
    assert next(r for r in grid["rows"] if r["date"] == days[20])["cells"]["broker"].level == cov.BAD


def test_a_source_with_no_data_at_all_is_all_na():
    days = _weekdays(10)
    grid = cov.build_grid(days, {"price": {d: 962 for d in days}})
    assert grid["summary"]["index"] == {"start": None, "baseline": None, "bad": 0, "warn": 0}
    assert all(r["cells"]["index"].level == cov.NA for r in grid["rows"])


def test_the_october_broker_coverage_drop_shows_as_bad():
    """16-30 Oct sat at ~60 names against a normal ~190 (the G3 problem)."""
    days = _weekdays(40)
    broker = {d: (60 if d >= date(2026, 10, 16) else 190) for d in days}
    grid = cov.build_grid(days, {"price": {d: 962 for d in days}, "broker": broker})
    low = {r["date"] for r in grid["rows"] if r["cells"]["broker"].level == cov.BAD}
    assert low == {d for d in days if d >= date(2026, 10, 16)}


# ---- classify boundaries ----------------------------------------------------------------

@pytest.mark.parametrize("count,expected", [
    (962, cov.OK), (866, cov.OK),          # 90.0%
    (865, cov.WARN), (481, cov.WARN),      # 89.9% .. 50.0%
    (480, cov.BAD), (5, cov.BAD), (0, cov.BAD),
])
def test_classify_thresholds_against_a_962_baseline(count, expected):
    assert cov.classify(count, 962) == expected


def test_classify_without_a_baseline_only_distinguishes_zero():
    assert cov.classify(10, None) == cov.OK and cov.classify(0, None) == cov.BAD
    assert cov.classify(10, 0) == cov.OK


# ---- missing names / reference day ------------------------------------------------------

def test_missing_names_is_sorted_set_difference():
    assert cov.missing_names({"BBCA", "ASII", "TLKM"}, {"ASII"}) == ["BBCA", "TLKM"]
    assert cov.missing_names({"A"}, {"A", "B"}) == []


def test_nearest_healthy_day_skips_other_gaps_and_prefers_earlier_on_a_tie():
    days, counts = _incident()
    price = counts["price"]
    # 14 and 15 Sep are both gaps; the nearest healthy neighbours of the 15th: 11 Sep (4 days
    # back) vs 16 Sep (1 day forward) -> 16 Sep.
    assert cov.nearest_healthy_day(date(2026, 9, 15), days, price, 962) == date(2026, 9, 16)
    # tie: equidistant healthy days -> the earlier one
    flat = {date(2026, 9, 1): 100, date(2026, 9, 3): 100, date(2026, 9, 2): 5}
    assert cov.nearest_healthy_day(date(2026, 9, 2), sorted(flat), flat, 100) == date(2026, 9, 1)


def test_an_over_covered_one_off_day_is_never_the_reference():
    """15 Oct had broker for the WHOLE market (952) vs a normal ~190. Healthy, yes — but not normal;
    using it as the reference would list ~760 names as 'missing' on an ordinary day."""
    days = _weekdays(30)
    broker = {d: 190 for d in days}
    spike, target = date(2026, 10, 15), date(2026, 10, 16)
    broker[spike] = 952
    broker[target] = 60                                         # the sagging day we are asking about
    ref = cov.nearest_healthy_day(target, days, broker, 190)
    assert ref is not None and ref != spike
    assert ref == date(2026, 10, 14)                            # nearest day that is normal, not merely healthy


def test_a_modestly_larger_day_is_still_a_valid_reference():
    """250 vs a baseline of 190 is 1.32x — ordinary variation, so it may serve as the reference."""
    days = _weekdays(10)
    counts = {days[3]: 250, days[4]: 5}
    assert cov.nearest_healthy_day(days[4], [days[3], days[4]], counts, 190) == days[3]


def test_the_reference_size_limit_is_one_and_a_half_times_normal():
    days = _weekdays(3)
    target = days[2]
    just_over = {days[1]: 286, target: 5}                       # 286/190 = 1.505x -> anomalous
    just_under = {days[1]: 285, target: 5}                      # 285/190 = 1.5x   -> still fine
    assert cov.nearest_healthy_day(target, days, just_over, 190) is None
    assert cov.nearest_healthy_day(target, days, just_under, 190) == days[1]


def test_nearest_healthy_day_is_none_when_nothing_is_healthy_and_never_returns_the_target():
    days = _weekdays(5)
    assert cov.nearest_healthy_day(days[2], days, {d: 0 for d in days}, 962) is None
    assert cov.nearest_healthy_day(days[2], days, {d: 962 for d in days}, 962) != days[2]


# ---- repair commands --------------------------------------------------------------------

def test_repair_commands_name_the_day_and_the_right_tool():
    d = date(2026, 9, 15)
    assert cov.repair_commands("price", d)[0] == "vg collect-price --date 2026-09-15"
    assert any("collect-price-history --all" in c for c in cov.repair_commands("price", d))
    assert cov.repair_commands("index", d) == ["vg collect-index --date 2026-09-15"]
    assert cov.repair_commands("broker", d)[0] == "vg collect-broker-summary --full --date 2026-09-15"
    assert cov.repair_commands("signal", d) == ["vg compute --date 2026-09-15"]
    assert all(c.startswith("vg ") for s in cov.SOURCE_KEYS for c in cov.repair_commands(s, d))


def test_repair_commands_reject_an_unknown_source():
    with pytest.raises(KeyError):
        cov.repair_commands("nope", date(2026, 9, 15))


def test_sources_are_stable():
    assert cov.SOURCE_KEYS == ("price", "index", "broker", "signal")
    assert cov.source_label("price") == "Harga"
