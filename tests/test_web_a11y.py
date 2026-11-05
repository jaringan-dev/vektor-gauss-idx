"""Accessibility regressions (WCAG 2.2 AA) that can be checked without a browser: names, landmarks, target size, and the colour
palette's contrast ratios in BOTH themes. The runtime audit that found these (scripts/a11y_audit.js) covers the rest."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "src" / "vg" / "web" / "templates"
BASE = (TEMPLATES / "base.html").read_text(encoding="utf-8")


def _templates():
    return sorted(TEMPLATES.glob("*.html"))


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_every_table_has_an_accessible_name(path):
    s = path.read_text(encoding="utf-8")
    for m in re.finditer(r"<table\b[^>]*>", s):
        assert "aria-label=" in m.group(0) or "<caption" in s[m.end():m.end() + 200], f"{path.name}: unnamed table at char {m.start()}"


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_every_navigation_landmark_is_labelled(path):
    s = path.read_text(encoding="utf-8")
    for m in re.finditer(r"<nav\b[^>]*>", s):
        assert "aria-label" in m.group(0), f"{path.name}: unlabelled <nav>"


def test_the_page_never_disables_zoom_or_removes_the_focus_outline():
    assert "user-scalable" not in BASE and "maximum-scale" not in BASE
    assert not re.search(r"outline\s*:\s*(none|0)\b", BASE)


def test_interactive_chips_and_badges_meet_the_24px_target_size():
    for sel in (r"\.chip \{[^}]*min-height:24px", r"\.badge \{[^}]*min-height:24px", r"table\.cov td a \{[^}]*min-height:24px"):
        assert re.search(sel, BASE), sel


def test_plain_links_use_a_theme_aware_colour_instead_of_the_browser_default():
    assert "a:not([class]) { color:var(--link)" in BASE
    assert BASE.count("--link:") == 3                                   # light, system dark, and an explicitly picked dark


def test_every_input_has_a_label_or_aria_label():
    for path in _templates():
        s = path.read_text(encoding="utf-8")
        for m in re.finditer(r"<input\b[^>]*>", s):
            tag = m.group(0)
            if 'type="hidden"' in tag:
                continue
            idm = re.search(r'\bid="([^"]+)"', tag)
            labelled = "aria-label=" in tag or (idm and re.search(r'<label[^>]*for="%s"' % re.escape(idm.group(1)), s)) \
                or re.search(r"<label\b[^>]*>(?:(?!</label>).)*" + re.escape(tag[:30]), s, re.S)
            assert labelled, f"{path.name}: unlabelled input {tag[:60]}"


# ---- contrast of the palette, both themes ---------------------------------------------------------

def _lum(hexcolor):
    h = hexcolor.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


_PARSE = lambda block: dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{3,6})", block))  # noqa: E731
_LIGHT = re.search(r":root \{(.*?)\}", BASE, re.S).group(1)
_DARK_SYSTEM = re.search(r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(.*?)\}', BASE, re.S).group(1)
_DARK_PICKED = re.search(r':root\[data-theme="dark"\] \{(.*?)\}', BASE, re.S).group(1)


def _tokens():
    light = _PARSE(_LIGHT)
    return {"light": light, "dark": {**light, **_PARSE(_DARK_SYSTEM)}}


def test_the_two_copies_of_the_dark_palette_are_identical():
    """System dark and an explicit 'Dark' pick must look the same, or the toggle would change colours unexpectedly."""
    assert _PARSE(_DARK_SYSTEM) == _PARSE(_DARK_PICKED) and len(_PARSE(_DARK_PICKED)) >= 14


def test_theme_choice_is_applied_before_first_paint_and_survives_blocked_storage():
    head = BASE[:BASE.index("<style>")]
    assert 'localStorage.getItem("vg-theme")' in head and 'setAttribute("data-theme"' in head and "catch (e)" in head
    assert 'color-scheme: light dark' in BASE and ':root[data-theme="light"] { color-scheme: light; }' in BASE


def test_theme_button_is_a_labelled_button_that_cycles_auto_light_dark():
    assert 'id="theme-btn"' in BASE and 'type="button"' in BASE and 'aria-label="Colour theme: Auto. Press to change."' in BASE
    assert 'order = ["auto", "light", "dark"]' in BASE and 'localStorage.setItem("vg-theme"' in BASE and 'localStorage.removeItem("vg-theme")' in BASE
    assert 'btn.setAttribute("aria-label", "Colour theme: "' in BASE                      # the name always states the current choice


PAIRS = [("fg", "bg"), ("fg", "panel"), ("muted", "bg"), ("muted", "panel"), ("link", "panel"), ("link", "bg"), ("pos", "panel"), ("neg", "panel"),
         ("ready-fg", "ready-bg"), ("fail-fg", "fail-bg"), ("prob-fg", "prob-bg"), ("warn-fg", "warn-bg"), ("fg", "prob-bg"), ("muted", "prob-bg"),
         ("fg", "side"), ("muted", "side"), ("wash-fg", "wash-bg"), ("wash-fg", "panel"), ("wash-fg", "bg")]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fg,bg", PAIRS)
def test_palette_pairs_meet_aa_contrast(theme, fg, bg):
    t = _tokens()[theme]
    assert fg in t and bg in t, (fg, bg)
    assert _ratio(t[fg], t[bg]) >= 4.5, f"{theme}: {fg} on {bg} = {_ratio(t[fg], t[bg]):.2f}"


def test_the_popover_moves_focus_into_the_card_and_escape_returns_it():
    assert "card.focus(" in BASE and 'e.key === "Escape"' in BASE and "a.focus()" in BASE
