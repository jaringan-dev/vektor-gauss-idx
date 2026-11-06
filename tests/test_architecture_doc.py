"""ARCHITECTURE.md is the system map. This keeps it honest: every `vg` command, every database table and every web route must be named
in it (in backticks), so adding one without documenting it fails here.

To fix a failure, add the missing name to the right section of ARCHITECTURE.md (section 7 there says which)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
_NOT_ROUTES = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def named(name: str) -> bool:
    """`name` appears inside a backtick span as a whole word (so `vg init-db` and `price_bars, index_bars` both count)."""
    word = re.compile(r"(?<![\w/.-])" + re.escape(name) + r"(?![\w-])")
    # findall pairs the backticks left to right, so the text BETWEEN two spans is never mistaken for a span
    return any(word.search(span) for span in re.findall(r"`([^`\n]+)`", DOC))


def commands() -> list[str]:
    import argparse

    from vg import __main__ as cli

    parser = cli.build_parser()
    for action in parser._actions:  # noqa: SLF001 - argparse has no public list of sub-commands
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return sorted(action.choices)
    return []


def tables() -> list[str]:
    schema = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", schema)))


def routes() -> list[str]:
    pytest.importorskip("fastapi")
    from vg.web import app as webapp

    return sorted({r.path for r in webapp.app.routes if getattr(r, "methods", None)} - _NOT_ROUTES)


def test_the_helper_matches_whole_words_inside_backticks_only():
    global DOC
    saved = DOC
    try:
        DOC = "text `vg collect-price` and `price_bars, index_bars` plus plain compute and `compute-adj`"
        assert named("collect-price") and named("price_bars") and named("index_bars") and named("compute-adj")
        assert not named("compute")                       # plain text, and only a prefix of `compute-adj`
        assert not named("collect")                       # a prefix of a longer name
    finally:
        DOC = saved


def test_every_command_is_in_the_architecture_doc():
    missing = [c for c in commands() if not named(c)]
    assert commands(), "no sub-commands found: the parser changed shape"
    assert not missing, f"add these `vg` commands to ARCHITECTURE.md (section 2, 3 or 4): {missing}"


def test_every_table_is_in_the_architecture_doc():
    missing = [t for t in tables() if not named(t)]
    assert tables(), "no tables found in db/schema.sql"
    assert not missing, f"add these tables to ARCHITECTURE.md (section 5): {missing}"


def test_every_web_route_is_in_the_architecture_doc():
    missing = [r for r in routes() if not named(r)]
    assert routes(), "no routes found"
    assert not missing, f"add these routes to ARCHITECTURE.md (section 6): {missing}"


def test_the_mermaid_diagrams_are_closed_and_the_doc_is_linked_from_the_readme():
    assert DOC.count("```mermaid") == 3
    assert DOC.count("```") % 2 == 0                      # every fence is closed
    assert "ARCHITECTURE.md" in (ROOT / "README.md").read_text(encoding="utf-8")
