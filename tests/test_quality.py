"""EOD quality gate (quality.py) — pure rules + the eod.sh wiring. Includes the real 14/15/25 Sep incident."""

import re
from datetime import date, timedelta
from pathlib import Path

from vg import quality
from vg.quality import BLOCK, WARN, Facts

ROOT = Path(__file__).resolve().parent.parent
DAY = date(2026, 10, 30)


def _days(n, end=DAY):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _facts(price=962, broker=190, **kw):
    ds = _days(70)
    p = {d: 962 for d in ds}
    p[DAY] = price
    b = {d: 190 for d in ds}
    b[DAY] = broker
    return Facts(day=DAY, price_counts=p, broker_counts=b, index_rows=1, **kw)


def _keys(findings, level):
    return {f.key for f in findings if f.level == level}


def test_healthy_day_has_no_findings():
    assert quality.evaluate(_facts()) == []


def test_september_style_price_gap_blocks():
    f = quality.evaluate(_facts(price=5))
    assert "price_gap" in _keys(f, BLOCK) and quality.blocked(f)


def test_partial_price_day_warns_but_does_not_block():
    f = quality.evaluate(_facts(price=800))
    assert _keys(f, WARN) == {"price_partial"} and not quality.blocked(f)


def test_missing_price_rows_and_non_calendar_day_block():
    assert "price_missing" in _keys(quality.evaluate(_facts(price=0)), BLOCK)
    assert "calendar" in _keys(quality.evaluate(_facts(in_calendar=False)), BLOCK)


def test_integrity_violations_block_each_on_their_own():
    for field, key in (("ohlc_violations", "ohlc"), ("nonpositive_close", "close_nonpositive"),
                       ("negative_volume", "volume_negative")):
        f = quality.evaluate(_facts(**{field: 3}))
        assert key in _keys(f, BLOCK), key


def test_secondary_issues_only_warn():
    f = quality.evaluate(_facts(broker=60, open_out_of_range=128, fallback_rows=17))
    assert not quality.blocked(f)
    assert _keys(f, WARN) == {"broker_partial", "open_range", "fallback_source"}
    assert "index_missing" in _keys(quality.evaluate(Facts(day=DAY, price_counts=_facts().price_counts)), WARN)


def test_report_text_states_the_verdict():
    assert "DIBLOKIR" in quality.format_report(DAY, quality.evaluate(_facts(price=5)))
    assert "tanpa temuan" in quality.format_report(DAY, [])


def test_eod_runs_the_gate_before_compute_and_guards_both_compute_passes():
    lines = [ln.strip() for ln in (ROOT / "scripts" / "eod.sh").read_text(encoding="utf-8").splitlines()
             if not ln.strip().startswith("#")]
    text = "\n".join(lines)
    assert "vg quality-gate || GATE_OK=0" in text
    gate = text.index("vg quality-gate")
    first = text.index('vg compute             --backfill "$BACK" "$TODAY"')
    assert gate < first
    assert '[ "$GATE_OK" -eq 1 ] && { vg compute --backfill' in text          # pass 2 is guarded too
    assert len(re.findall(r"\bvg compute(?![\w-])", text)) == 2                # no unguarded third compute (`vg compute-adj` is another command)
