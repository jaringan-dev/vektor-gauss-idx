"""Recorder vs done-details (vg.flow_check): pure, no database."""

from datetime import date

from vg import flow_check as F

D = date(2026, 11, 10)


def test_matching_shares_on_a_complete_day_agree():
    # 1,000,000 shares regular volume = 10,000 lots; both sources see it all; 60% bought on the offer
    r = F.compare(D, "ADRO", 1_000_000, 0, (10_000, 6_000, 4_000), (6_050, 3_950))
    assert r.status == "agree" and r.judged and abs(r.done_cov - 1.0) < 1e-9


def test_a_mirrored_label_reads_as_inverted():
    r = F.compare(D, "ADRO", 1_000_000, 0, (10_000, 7_000, 3_000), (3_000, 7_000))
    assert r.status == "inverted"


def test_a_capped_done_details_day_is_listed_but_not_judged():
    """BBCA 6 Nov 2026: done-details stopped at 200 pages (20,000 trades)."""
    r = F.compare(D, "BBCA", 1_000_000, 0, (5_000, 3_000, 2_000), (6_000, 4_000))
    assert r.status.startswith("partial") and "done-details 50%" in r.status and not r.judged


def test_a_late_recorder_start_is_partial():
    """6 Nov 2026: the recorder ran from mid-afternoon only (ADRO ~31% of the day)."""
    r = F.compare(D, "ADRO", 38_395_000, 9_498, (383_950, 170_972, 212_978), (56_934, 63_217))
    assert r.status.startswith("partial") and "recorder 31%" in r.status


def test_full_volume_but_missing_trades_is_still_partial():
    """BBCA 6 Nov 2026 (RG board): 97% of the regular volume, but only 19,984 of ~32,000 trades - the 200-page cap."""
    r = F.compare(D, "BBCA", 117_547_621, 0, (1_138_240, 784_119, 354_121), (700_000, 470_000), freq=(31_947, 19_984, 31_900))
    assert r.status.startswith("partial") and "done-details 63%" in r.status


def test_negotiated_volume_is_taken_out_of_the_base():
    r = F.compare(D, "BBCA", 1_400_000, 400_000, (10_000, 6_000, 4_000), (6_000, 4_000))
    assert abs(r.reg_volume - 1_000_000) < 1e-9 and r.status == "agree"


def test_missing_sources_are_named():
    assert F.compare(D, "X", 1e6, 0, None, (1, 1)).status == "missing: done-details"
    assert F.compare(D, "X", 1e6, 0, None, None).status == "missing: done-details and recorder"


def test_verdict_needs_enough_complete_days_then_reads_the_majority():
    agree = [F.compare(D, f"T{i}", 1e6, 0, (10_000, 6_000, 4_000), (6_000, 4_000)) for i in range(F.MIN_JUDGED)]
    partial = [F.compare(D, "P", 1e6, 0, (1_000, 600, 400), (6_000, 4_000))]
    assert F.verdict(agree[:3] + partial)[0] == "not enough data"
    v, ev = F.verdict(agree + partial)
    assert v == "recorder can replace done-details" and f"of {F.MIN_JUDGED} complete" in ev
    inverted = [F.compare(D, f"T{i}", 1e6, 0, (10_000, 7_000, 3_000), (3_000, 7_000)) for i in range(F.MIN_JUDGED)]
    assert F.verdict(inverted)[0].startswith("recorder side is INVERTED")
