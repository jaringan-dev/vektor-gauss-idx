"""vg.flags: the single SQL definition of the data-quality flags (no DB here: the live counts are checked by running it)."""

from vg import flags
from vg.engine import KSEI_PUBLISH_LAG_DAYS, SCRIPLESS_JUMP


def test_every_flag_documented_in_the_module_is_produced_by_the_sql():
    for name in ("ksei_scripless_jump", "ksei_unpublished", "corp_action_window", "broker_copy", "fallback_bar"):
        assert f"'{name}'" in flags.FLAGS_SQL and name in flags.__doc__


def test_the_thresholds_are_the_engines_own_constants():
    assert f">= {SCRIPLESS_JUMP}" in flags.FLAGS_SQL and f"+ {KSEI_PUBLISH_LAG_DAYS} >" in flags.FLAGS_SQL
    assert "source_tier = 0" in flags.FLAGS_SQL and "source_tier > 0" in flags.FLAGS_SQL   # IDX bars give factors; fallback bars are flagged


def test_only_tags_that_read_the_broken_input_are_flagged_for_it():
    assert set(flags.KSEI_TAGS) == {"KSEI-INST-AKUM", "KSEI-RITEL-EXIT", "KSEI-KONFIRM", "KSEI-DIVERGEN"}   # v1 only: KSEI2 already guards
    assert "FLOAT-ASING-PADAT" in flags.FLAGS_SQL and "FLOAT2-ASING-PADAT" not in flags.FLAGS_SQL         # FLOAT2 refuses those days itself
    assert all(t not in flags.BROKER_TAGS for t in ("MARKUP", "KSEI-INST-AKUM"))
