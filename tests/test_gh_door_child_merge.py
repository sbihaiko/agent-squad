"""A child session never merges a PR; the loop does (#285).

The door (`contrib/squad-goal/scripts/ghcall.sh`) refuses `gh pr merge` and `PUT .../pulls/N/merge` when the caller is a
child session (`SG_CHILD_SESSION` set, which the call environment exports). The refusal is named. Outside a child session
the door passes the same write through, so the loop's merger still merges. Every case drives a stub `gh` on PATH.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from squad import defaults

ROOT = Path(__file__).resolve().parents[1]
DOOR = ROOT / "contrib/squad-goal/scripts/ghcall.sh"
MERGE = "gh_send -X PUT repos/x/y/pulls/5/merge -f merge_method=squash"


def sh(tmp_path: Path, line: str, *, child: bool) -> subprocess.CompletedProcess[str]:
    (tmp_path / "bin").mkdir(exist_ok=True)
    stub = tmp_path / "bin/gh"
    stub.write_text(f"#!/bin/bash\necho \"$*\" >> {tmp_path}/calls\necho '{{\"merged\":true}}'\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update({"PATH": f"{tmp_path / 'bin'}:{env['PATH']}", "HOME": str(tmp_path), "SG_STATE": str(tmp_path / "st")})
    if child:
        env["SG_CHILD_SESSION"] = "1"
    (tmp_path / "st").mkdir(exist_ok=True)
    return subprocess.run(["bash", "-c", f'. "{DOOR}"; {line}'], capture_output=True, text=True, env=env, check=False)


def reached(tmp_path: Path) -> str:
    f = tmp_path / "calls"
    return f.read_text() if f.exists() else ""


def test_the_door_refuses_a_childs_api_merge_with_a_named_refusal(tmp_path):
    p = sh(tmp_path, MERGE, child=True)
    assert p.returncode == 2, f"the merge was not refused: rc={p.returncode} {p.stdout}{p.stderr}"
    assert "CHILD-MERGE-REFUSED" in p.stdout, f"the refusal has no name: {p.stdout}"
    assert "pulls/" not in reached(tmp_path), "a refused merge still reached GitHub"


def test_the_door_refuses_a_childs_pr_merge_command(tmp_path):
    p = sh(tmp_path, "gh_send pr merge 5 --squash", child=True)
    assert p.returncode == 2 and "CHILD-MERGE-REFUSED" in p.stdout, f"rc={p.returncode} {p.stdout}"
    assert not reached(tmp_path)


def test_the_loops_merger_still_merges(tmp_path):
    p = sh(tmp_path, MERGE, child=False)
    assert p.returncode == 0, f"the loop's merge was refused: {p.stdout}{p.stderr}"
    assert "pulls/5/merge" in reached(tmp_path)


def test_a_child_may_still_write_something_else(tmp_path):
    p = sh(tmp_path, "gh_send -X POST repos/x/y/issues/5/comments -f body=hi", child=True)
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"


def test_the_call_environment_marks_a_child_session():
    assert defaults.call_env(defaults.load())["SG_CHILD_SESSION"] == "1"


def test_the_child_request_says_the_loop_merges():
    text = (ROOT / "contrib/squad-goal/scripts/req-checklist.txt").read_text()
    assert "never merge" in text and "the loop merges" in text, "the child prompt must say that the loop merges"
