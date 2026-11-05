"""The glossary is the guarantee that no jargon reaches the screen unexplained (RENCANA-ROMBAK-UI-v3 §4)."""

import json
import re
from pathlib import Path

from vg import engine
from vg.web import glossary
from vg.web.narrative import FORBIDDEN

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "src" / "vg" / "web" / "templates"


def _emitted_tags() -> set[str]:
    src = (ROOT / "src" / "vg" / "engine" / "__init__.py").read_text(encoding="utf-8")
    body = src[src.index("def _classify"):src.index("# persistence")]
    return set(re.findall(r'tags\.append\("([A-Z0-9-]+)"\)', body))


def test_every_tag_the_engine_can_emit_is_explained_and_there_are_no_ghost_entries():
    emitted = _emitted_tags()
    assert len(emitted) >= 24                                   # the regex really found the tags
    assert emitted - set(glossary.TAGS) == set(), "tags with no glossary entry"
    assert set(glossary.TAGS) - emitted == set(), "glossary entries for tags the engine never emits"


def test_every_key_used_in_templates_exists_in_the_glossary():
    used: set[str] = set()
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"""\b(?:term|th)\(\s*['"]([a-z0-9_]+)['"]""", text))
        used |= set(re.findall(r'data-term="([a-z0-9_]+)"', text))
        used |= set(re.findall(r'href="/guide#([a-z0-9_]+)"', text))
    assert used, "no term usages found — the regex is broken"
    section_anchors = {"patterns"}                       # page sections of /guide, not glossary terms
    assert used - set(glossary.TERMS) - section_anchors == set()


def test_every_quadrant_the_engine_assigns_has_a_term():
    for engine_value, (key, word) in glossary.QUADRANTS.items():
        assert key in glossary.TERMS and word
    src = (ROOT / "src" / "vg" / "engine" / "__init__.py").read_text(encoding="utf-8")
    assigned = set(re.findall(r'\): "([A-Z]+)",', src[src.index("c.quadrant = {"):src.index("cons = c.foreign_consistency_10d")]))
    assert assigned <= set(glossary.QUADRANTS)


def test_every_term_group_exists_and_every_term_is_substantive():
    groups = {k for k, _ in glossary.GROUPS}
    for t in glossary.TERMS.values():
        assert t.group in groups, t.key
        assert len(t.short) >= 30 and t.name, t.key


def test_tag_rules_quote_the_real_engine_thresholds():
    assert engine.BROKER_FOREIGN_BAND == 0.05 and "5%" in glossary.TAGS["BROKER-ASING-AKUM"].rule
    assert "5%" in glossary.TAGS["BROKER2-ASING-AKUM"].rule
    assert engine.CONSISTENCY_ACC == 6 and "6 of the last 10" in glossary.TAGS["AKUM-ASING"].rule
    assert engine.CONSISTENCY_DIST == 3 and "at most 3" in glossary.TAGS["DISTRIBUSI-ASING"].rule
    assert engine.NONREG_HIGH == 0.20 and "20%" in glossary.TAGS["CROSSING-TINGGI"].rule
    assert engine.BANDAR_CR1_HIGH == 0.40 and "40%" in glossary.TAGS["BANDAR-CANDIDATE"].rule
    assert engine.PRICE_FLAT_BAND == 0.02 and "2%" in glossary.TAGS["MARKUP"].rule
    assert engine.KSEI_TREND_BAND == 0.005 and "0.5" in glossary.TAGS["KSEI-INST-AKUM"].rule
    assert engine.FLOAT_ACCUM_BAND == 0.01 and "1%" in glossary.TAGS["FLOAT-ASING-PADAT"].rule
    assert engine.LIQUID_VALUE_IDR == 5_000_000_000 and "Rp5 billion" in glossary.TERMS["liquid"].short


def test_no_glossary_text_gives_trading_instructions():
    texts = []
    for t in glossary.TERMS.values():
        texts += [t.short, t.how, t.caveat]
    for t in glossary.TAGS.values():
        texts += [t.what, t.rule, t.caveat, t.name]
    blob = " ".join(texts).lower()
    for phrase in FORBIDDEN:
        assert phrase not in blob, phrase


def test_popover_data_is_json_safe_and_covers_terms_and_tags():
    data = glossary.popover_data()
    json.dumps(data)
    assert "cr3" in data and "tag:AKUM-ASING" in data and data["tag:AKUM-ASING"]["code"] == "AKUM-ASING"
    assert len(data) == len(glossary.TERMS) + len(glossary.TAGS)


def test_helpers_never_lose_an_unknown_tag_or_quadrant():
    assert glossary.plain_tag("SOMETHING-NEW") == "SOMETHING-NEW"
    assert glossary.quad_key(None) is None and glossary.quad_word("???") == "—"
    assert glossary.quad_key("AKUMULASI") == "q_accumulation" and glossary.quad_word("DISTRIBUSI") == "Distribution"
    assert glossary.split_tags("A, B ,,C") == ["A", "B", "C"] and glossary.split_tags(None) == []


# ---- broker scoring (stock page panel) -----------------------------------------------------------------------------------

_SCORING_TERMS = ("bpi", "overlap3", "aggressor", "retail_div")


def test_scoring_terms_are_explained_and_state_their_limits():
    for key in _SCORING_TERMS:
        term = glossary.TERMS[key]
        assert term.short and term.group == "concepts", key
    for key in ("bpi", "overlap3", "aggressor", "retail_div"):
        assert glossary.TERMS[key].caveat, f"{key} must say what it does NOT tell you"
    assert "forward-tested" in glossary.TERMS["bpi"].caveat
    assert "N/A" in glossary.TERMS["aggressor"].caveat                     # the glossary names what the page shows when empty


def test_the_stock_template_uses_exactly_the_scoring_terms_the_glossary_defines():
    text = (TEMPLATES / "stock.html").read_text(encoding="utf-8")
    used = set(re.findall(r"""\b(?:term|th)\(\s*['"]([a-z0-9_]+)['"]""", text))
    assert set(_SCORING_TERMS) <= used
    assert used - set(glossary.TERMS) == set()


def test_the_wash_tag_rule_quotes_the_real_thresholds():
    rule = glossary.TAGS["AKUM-NETRAL-WASH"].rule
    assert engine.BPI_HIGH == 0.10 and "10 points" in rule
    assert engine.WASH_OVERLAP_MIN == 2 and "2 or more" in rule
