"""Freshness rules (vg.web.freshness) — pure, no DB. Includes the real 14/15/25 Sep incident."""

from datetime import date, timedelta

from vg.web import freshness as fr

EOD = date(2026, 10, 30)            # a Friday


def _days(n, end=EOD):
    """n weekdays ending at `end`, oldest first."""
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _counts(latest_n, normal=962, days=21):
    ds = _days(days)
    c = {d: normal for d in ds}
    c[ds[-1]] = latest_n
    return c


def _level(items, key):
    return next(i.level for i in items if i.key == key)


def _strip(price=962, broker=190, today=date(2026, 10, 31), ksei=date(2026, 9, 30)):
    return fr.build_strip(fr.FreshInputs(today=today, eod_date=EOD, price_counts=_counts(price),
                                         broker_counts=_counts(broker, normal=190), ksei_month_end=ksei))


# ---- the incident that motivated the strip ---------------------------------------------

def test_the_september_price_gap_is_bad():
    """14/15/25 Sep had 5 price rows instead of ~962 — must be red."""
    items = _strip(price=5)
    assert _level(items, "price") == fr.BAD
    assert "gap" in next(i.text for i in items if i.key == "price")


def test_price_below_500_rows_is_bad_checklist_a2():
    assert _level(_strip(price=480), "price") == fr.BAD      # 480/962 = 0.499 < GAP_RATIO


def test_price_partial_is_warn_and_healthy_is_ok():
    assert _level(_strip(price=800), "price") == fr.WARN     # 83%
    assert _level(_strip(price=962), "price") == fr.OK
    assert _level(_strip(price=900), "price") == fr.OK       # 93.6% >= PARTIAL_RATIO


# ---- broker coverage: visible, but never red (secondary source) ------------------------

def test_broker_coverage_drop_is_warn_not_bad():
    items = _strip(broker=60)                                 # the 16-30 Oct situation: ~60 vs ~190
    assert _level(items, "broker") == fr.WARN
    assert "gap" in next(i.text for i in items if i.key == "broker")


def test_broker_normal_is_ok():
    assert _level(_strip(broker=196), "broker") == fr.OK


# ---- baseline ---------------------------------------------------------------------------

def test_baseline_excludes_the_latest_day_and_is_robust_to_old_gaps():
    ds = _days(21)
    counts = {d: 962 for d in ds}
    counts[ds[3]] = 5                                         # an old gap inside the window
    counts[ds[4]] = 5
    counts[ds[-1]] = 5                                        # today is also broken
    assert fr.baseline(counts, ds[-1]) == 962                 # median ignores the two old gaps


def test_a_twelve_day_broker_sag_is_still_flagged_on_its_last_day():
    """The failure that motivated a long baseline: with a 20-day median, ~10 sagging days became
    'normal' and the strip went quiet while coverage was still at a third of what it should be."""
    ds = _days(70)
    broker = {d: 190 for d in ds}
    for d in ds[-12:]:
        broker[d] = 60                                        # 12 trading days of ~60 names
    assert fr.baseline(broker, ds[-1]) == 190
    items = fr.build_strip(fr.FreshInputs(today=date(2026, 10, 31), eod_date=ds[-1],
                                          price_counts={d: 962 for d in ds}, broker_counts=broker,
                                          ksei_month_end=date(2026, 9, 30)))
    b = next(i for i in items if i.key == "broker")
    assert b.level == fr.WARN and "normal ≈ 190" in b.text


def test_baseline_window_is_sixty_trading_days():
    assert fr.BASELINE_DAYS == 60
    ds = _days(100)
    counts = {d: (100 if i < 30 else 500) for i, d in enumerate(ds)}      # old regime, then a new one
    # only the 60 days before the latest count: 30 new-regime days + ... the oldest 39 are ignored
    assert fr.baseline(counts, ds[-1]) == 500


def test_no_baseline_means_no_false_alarm():
    items = fr.build_strip(fr.FreshInputs(today=date(2026, 10, 31), eod_date=EOD,
                                          price_counts={EOD: 700}, broker_counts={EOD: 50},
                                          ksei_month_end=date(2026, 9, 30)))
    assert _level(items, "price") == fr.OK                    # nothing to compare against


def test_price_date_behind_calendar_is_stale():
    ds = _days(21)
    counts = {d: 962 for d in ds[:-3]}                        # last 3 trading days missing
    items = fr.build_strip(fr.FreshInputs(today=date(2026, 10, 31), eod_date=EOD, price_counts=counts,
                                          broker_counts=_counts(190, 190), ksei_month_end=date(2026, 9, 30)))
    price = next(i for i in items if i.key == "price")
    assert price.level == fr.BAD and "3 trading days behind" in price.text


def test_no_data_at_all_is_bad_for_prices_but_only_warn_for_broker():
    items = fr.build_strip(fr.FreshInputs(today=date(2026, 10, 31), eod_date=EOD))
    assert _level(items, "price") == fr.BAD
    assert _level(items, "broker") == fr.WARN
    assert _level(items, "ksei") == fr.BAD


# ---- EOD recency ------------------------------------------------------------------------

def test_eod_saturday_after_friday_is_ok():
    assert fr._eod_item(date(2026, 10, 30), date(2026, 10, 31)).level == fr.OK


def test_eod_monday_morning_after_friday_is_ok():
    assert fr._eod_item(date(2026, 10, 30), date(2026, 11, 2)).level == fr.OK


def test_eod_tuesday_morning_missing_monday_is_warn_then_bad():
    assert fr._eod_item(date(2026, 10, 30), date(2026, 11, 3)).level == fr.WARN
    assert fr._eod_item(date(2026, 10, 30), date(2026, 11, 4)).level == fr.BAD


# ---- KSEI age ---------------------------------------------------------------------------

def test_ksei_age_thresholds():
    assert fr._ksei_item(date(2026, 9, 30), EOD, EOD).level == fr.OK          # 30 days
    assert fr._ksei_item(date(2026, 8, 31), EOD, EOD).level == fr.WARN        # 60 days
    assert fr._ksei_item(date(2026, 7, 31), EOD, EOD).level == fr.BAD         # 91 days


# ---- helpers ----------------------------------------------------------------------------

def test_weekdays_in():
    assert fr.weekdays_in(date(2026, 10, 30), date(2026, 10, 30)) == 0
    assert fr.weekdays_in(date(2026, 10, 30), date(2026, 11, 2)) == 1         # only Monday counts
    assert fr.weekdays_in(date(2026, 10, 30), date(2026, 11, 8)) == 5
    assert fr.weekdays_in(date(2026, 11, 2), date(2026, 10, 30)) == 0         # reversed -> 0


def test_worst_and_overall():
    assert fr.worst([fr.OK, fr.WARN, fr.OK]) == fr.WARN
    assert fr.worst([fr.OK, fr.BAD, fr.WARN]) == fr.BAD
    assert fr.worst([]) == fr.OK


def test_english_date_formats():
    assert fr.fmt_date(date(2026, 10, 30)) == "30 Oct 2026"
    assert fr.fmt_date(date(2026, 5, 6)) == "6 May 2026"
    assert fr.fmt_month(date(2026, 8, 31)) == "Aug 2026"
    assert fr.fmt_date(None) == "—"


def test_item_dict_carries_text_symbol_and_word_never_color_only():
    d = fr.FreshItem("price", "Prices", "5 stocks — gap", fr.BAD).as_dict()
    assert d["symbol"] == "✖" and d["word"] == "problem" and d["level"] == "bad"


# ---- broker floor: ~200 liquid names is the design, so >= BROKER_MIN_NAMES is healthy ---------

def test_broker_at_or_above_the_floor_is_ok_even_when_older_days_were_bigger():
    """3 Nov 2026: 202 names vs a median of ~262 from backfilled days used to be a permanent amber warning."""
    items = fr.build_strip(fr.FreshInputs(today=date(2026, 10, 31), eod_date=EOD, price_counts=_counts(962),
                                          broker_counts=_counts(202, normal=262), ksei_month_end=date(2026, 9, 30)))
    assert _level(items, "broker") == fr.OK
    assert "202 stocks" in next(i.text for i in items if i.key == "broker")


def test_broker_just_below_the_floor_still_warns_against_normal():
    items = fr.build_strip(fr.FreshInputs(today=date(2026, 10, 31), eod_date=EOD, price_counts=_counts(962),
                                          broker_counts=_counts(fr.BROKER_MIN_NAMES - 1, normal=262),
                                          ksei_month_end=date(2026, 9, 30)))
    assert _level(items, "broker") == fr.WARN
