"""Weekly backup wiring (checklist G11). scripts/backup.sh runs from eod.sh with `|| true`, so a wrong
target fails silently every Sunday — these pin the target to the live repo."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_REPO = "jaringan-dev/vektor-gauss-idx"
DEAD_REPO = "jaringan-bot/vektor-gauss"          # account suspended: every `gh release create` is a 404


def _code(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_backup_default_repo_is_the_live_one():
    m = re.search(r'^REPO="\$\{VG_BACKUP_REPO:-([^}]+)\}"', _code(ROOT / "scripts" / "backup.sh"), re.M)
    assert m, "REPO default line not found in scripts/backup.sh"
    assert m.group(1) == LIVE_REPO


def test_env_example_does_not_override_with_the_dead_repo():
    """backup.sh sources .env, so a copied .env.example would silently re-point the backup."""
    m = re.search(r"^VG_BACKUP_REPO=(\S+)", _code(ROOT / ".env.example"), re.M)
    assert m and m.group(1) == LIVE_REPO


def test_no_script_still_targets_the_dead_repo():
    for p in (ROOT / "scripts").glob("*.sh"):
        assert DEAD_REPO not in _code(p), p.name


def test_sunday_block_also_refreshes_the_stop_target_snapshot_without_ever_failing_eod():
    text = (ROOT / "scripts" / "eod.sh").read_text(encoding="utf-8")
    sunday = text[text.index('-eq 7 ]'):text.index("vg digest || true")]
    assert "backup.sh" in sunday and "vg risk-eval --save" in sunday
    assert "vg risk-eval --save > /dev/null || true" in sunday              # best-effort: a failure must not abort EOD
    assert sunday.index("backup.sh") < sunday.index("risk-eval")            # the backup is never delayed by it
    assert "vg research --save > /dev/null || true" in sunday and sunday.index("risk-eval") < sunday.index("vg research")
