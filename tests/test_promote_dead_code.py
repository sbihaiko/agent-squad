"""The delta proof and the promotion bound have no caller on the dev-to-stg path (#1734)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIS = Path(__file__).resolve()

REMOVED_FILES = (
    "contrib/squad-goal/scripts/delta-pass.sh",
    "contrib/squad-goal/scripts/test-promote-delta.sh",
    "contrib/squad-goal/scripts/delta-guards.txt",
    "tests/test_promote_bound.py",
    "tests/test_delta_guards.py",
)

DEAD_NAMES = (
    "SG_PROMOTE_BOUND_SECONDS",
    "SG_PROMOTE_FALLBACK_SECONDS",
    "SG_GATE_DELTA_RESERVE_SECONDS",
    "promote-bound",
    "promote-indeterminate",
    "gate-carried",
    "gate-status-delta",
    "delta-pass",
    "delta-guards",
    "test-promote-delta",
    "test_promote_bound",
    "test_delta_guards",
)

LIVE_ROOTS = ("contrib", "squad", "tests", "config", "prompts", "docs", "dashboard", ".claude-plugin")
LIVE_FILES = ("CLAUDE.md", "AGENTS.md")


def _live_texts():
    paths = [ROOT / name for name in LIVE_FILES]
    for root in LIVE_ROOTS:
        base = ROOT / root
        if base.is_dir():
            paths += [p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    for p in paths:
        if p.resolve() != THIS:
            yield p, p.read_text(encoding="utf-8", errors="ignore")


def test_removed_files_are_absent():
    present = [f for f in REMOVED_FILES if (ROOT / f).exists()]
    assert not present, f"removed files still exist: {present}"


def test_dead_names_are_absent_from_live_code():
    hits = []
    for path, text in _live_texts():
        for name in DEAD_NAMES:
            if name in text:
                hits.append(f"{path.relative_to(ROOT)}: {name}")
    assert not hits, "dead promotion names still in live code:\n" + "\n".join(hits)
