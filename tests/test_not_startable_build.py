"""A SQUAD-NOT-STARTABLE marker names the build that wrote it (owner answer B2, ADR-0134): the same build keeps the stop,
another build re-arms it, and a marker that names none, or a build the reader cannot know, keeps the stop because neither
proves anything. Both readers hold the rule: the list read in ghread.py and the per-issue read in startability.sh."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
GHREAD = SCRIPTS / "ghread.py"


@pytest.fixture(scope="module")
def ghread():
    os.environ.setdefault("SG_REPO", "x/y")
    spec = importlib.util.spec_from_file_location("ghread_not_startable", GHREAD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod    # a dataclass resolves its own module by name while it is defined
    spec.loader.exec_module(mod)
    return mod


def comments(*bodies):
    return {"nodes": [{"body": b} for b in bodies]}


def test_a_marker_of_this_build_keeps_the_stop(ghread, monkeypatch):
    monkeypatch.setattr(ghread, "loop_build", lambda: "abc123def456")
    assert ghread._not_startable(comments("SQUAD-NOT-STARTABLE: build=abc123def456 3 runs failed")) is True


def test_a_marker_of_another_build_rearms(ghread, monkeypatch):
    monkeypatch.setattr(ghread, "loop_build", lambda: "abc123def456")
    assert ghread._not_startable(comments("SQUAD-NOT-STARTABLE: build=000000000000 3 runs failed")) is False


def test_a_marker_that_names_no_build_keeps_the_stop(ghread, monkeypatch):
    monkeypatch.setattr(ghread, "loop_build", lambda: "abc123def456")
    assert ghread._not_startable(comments("SQUAD-NOT-STARTABLE: failed 3 times")) is True


def test_a_marker_of_another_build_does_not_mask_the_one_of_this_build(ghread, monkeypatch):
    monkeypatch.setattr(ghread, "loop_build", lambda: "abc123def456")
    assert ghread._not_startable(comments("SQUAD-NOT-STARTABLE: build=000000000000 3 runs failed",
                                          "SQUAD-NOT-STARTABLE: build=abc123def456 3 runs failed")) is True


def test_an_unknown_build_keeps_every_stop(ghread, monkeypatch):
    monkeypatch.setattr(ghread, "loop_build", lambda: "")
    assert ghread._not_startable(comments("SQUAD-NOT-STARTABLE: build=000000000000 3 runs failed")) is True


def test_a_note_that_is_not_the_marker_is_startable(ghread, monkeypatch):
    monkeypatch.setattr(ghread, "loop_build", lambda: "abc123def456")
    assert ghread._not_startable(comments("a note")) is False


SHELL = r'''
. "$SCRIPTS/startability.sh" >/dev/null 2>&1   # it loads ghcall.sh, which defines gh_api
gh_api() { printf '%s\n' "$STUB_BODY"; }       # then the per-issue comments read is stubbed
sg_not_startable 61
'''


def shell_says(marker: str) -> bool:
    env = {**os.environ, "SG_REPO": "o/r", "SG_STATE": "/tmp", "SG_CHECKOUT": str(ROOT),
           "STUB_BODY": marker, "SCRIPTS": str(SCRIPTS)}
    r = subprocess.run(["bash", "-c", SHELL], env=env, capture_output=True, text=True, timeout=60)
    return r.returncode == 0


def test_the_shell_reader_rearms_a_marker_of_another_build():
    assert shell_says("SQUAD-NOT-STARTABLE: build=000000000000 3 runs failed") is False


def test_the_shell_reader_keeps_a_marker_of_this_build():
    cur = subprocess.run(["/usr/bin/git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    assert shell_says(f"SQUAD-NOT-STARTABLE: build={cur} 3 runs failed") is True


def test_adr_rule_four_names_the_label_the_owner_removes():
    """ADR-0159 rule 4 names the frozen:not-startable label: the label holds an issue whose marker names no build, so the
    owner removes the comment and the label, or runs the re-arm command. Removing the comment alone does not re-arm it."""
    adr = (ROOT / "docs/adr/0159-a-not-startable-marker-names-its-build.md").read_text(encoding="utf-8")
    rule4 = adr.split("\n4. ", 1)[1].split("\n5. ", 1)[0]
    assert "frozen:not-startable" in rule4
