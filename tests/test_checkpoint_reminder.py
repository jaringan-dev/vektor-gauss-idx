"""Daily checkpoint reminder (vg.checkpoint_reminder): CHECKPOINT.md parsing and the message, no database, no network."""

from datetime import date
from pathlib import Path

from vg import checkpoint_reminder as C

ROOT = Path(__file__).resolve().parent.parent

SAMPLE = """# CHECKPOINT

intro

---

## 2026-11-09 (Senin)

### Langkah
- [x] 1. done thing
- [ ] 2. check `trade_lots_daily` **today**
- [ ] 3. pick thresholds <now>

### Menunggu keputusan user
- `is_confirmed()` rule
- quota plan

### Sudah selesai
- [ ] not a step of this day? (still in the section, counted)

## 2026-11-07 (Sabtu)
- [ ] old unticked item must NOT appear
"""


def test_only_the_newest_day_is_read():
    day, steps, waiting = C.open_items(SAMPLE)
    assert day == "2026-11-09 (Senin)"
    assert steps[:2] == ["2. check `trade_lots_daily` **today**", "3. pick thresholds <now>"]
    assert all("old unticked" not in s for s in steps)
    assert waiting == ["`is_confirmed()` rule", "quota plan"]


def test_no_day_section_means_nothing_open():
    assert C.open_items("# empty\n") == (None, [], [])


def test_the_message_converts_markdown_and_escapes_the_rest():
    day, steps, waiting = C.open_items(SAMPLE)
    msg = C.build_message(date(2026, 11, 9), [(True, "Broker data: 374 stocks"), (False, "v2 tags fired: 0")], day, steps, waiting)
    assert "✅ Broker data: 374 stocks" in msg and "⚠️ v2 tags fired: 0" in msg
    assert "<code>trade_lots_daily</code>" in msg and "<b>today</b>" in msg
    assert "&lt;now&gt;" in msg                                  # a stray < cannot break Telegram's HTML parser
    assert "Menunggu keputusan Anda" in msg


def test_a_long_list_is_cut_and_says_how_many_more():
    steps = [f"step {i}" for i in range(C.MAX_ITEMS + 5)]
    msg = C.build_message(date(2026, 11, 9), [], None, steps, [])
    assert msg.count("• ") == C.MAX_ITEMS and "… dan 5 lagi" in msg


def test_the_real_checkpoint_file_parses():
    day, _steps, _waiting = C.open_items((ROOT / "CHECKPOINT.md").read_text(encoding="utf-8"))
    assert day is not None


def test_eod_calls_it_last_and_never_lets_it_fail_the_run():
    eod = (ROOT / "scripts" / "eod.sh").read_text(encoding="utf-8")
    assert "vg remind-checkpoint || true" in eod
    assert eod.index("vg remind-checkpoint") > eod.index("vg status --telegram")
