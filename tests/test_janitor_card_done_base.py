"""Issue #127: a card is Done only over work the base carries.

A pull request that names the issue and is merged into a *parent* branch delivers nothing to the base: the merge lands on
that branch alone, and the change is stranded once the parent is squash-merged (#125). The pass read any merge naming the
issue as delivery, so the card left Code Complete for Done over work the base never took. The merged-PR hold is now
scoped to the base: only a merge whose base is the loop's base counts, beside the two other holds -- a landed branch and
a closed issue.

These cases drive janitor.py end to end with a stub `gh` on PATH and a temp state dir. No case touches the network, the
live loop state or the live board.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
N = 2030  # the issue a finished run delivered, whose pull request names it

# The stub gh answers the two PR searches the pass makes. The merge scoped to the base answers @SCOPED@ and the same
# merge read without the qualifier answers 1, so a case picks whether the base carried the merge.
STUB = """#!/bin/bash
a="$*"; echo "$a" >> "@CALLS@"
case "$a" in
  *graphql*) printf '%s' '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}';;
  *search/issues*)
    case "$a" in *"-X GET"*|*"--method GET"*) ;; *) echo '{"message":"Not Found"}'; exit 1;; esac
    case "$a" in
      *"base:"*"Refs #@N@"*) @SCOPED@;;
      *"is:merged"*"Refs #@N@"*) echo 1;;
      *"Refs #@N@"*) echo 1;;
      *) echo 0;; esac;;
  *projectsV2/*/fields*) echo FID4;;
  *projectsV2/*/items*) ;;
  *issues/*) echo -e 'open\\t\\tIssue';;
esac
"""

BOARD = """#!/bin/bash
case "$1" in
  --add) echo "ADD $2 $3 $4 $5" >> "@CALLS@";;
  --run-fields) :;;
  --clear-run) :;;
  *) echo "STATUS $1 $2 $3 $4" >> "@CALLS@";;
esac
"""


def _write(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def sandbox(tmp_path: Path, scoped: str) -> Path:
    """The janitor with the throttle and the reader beside it, a stub `gh` (the merge scoped to the base answers
    `scoped`), a stub board.sh, and one finished run that delivered issue #2030 by a pull request naming it."""
    for name in ("janitor.py", "ghthrottle.py", "ghread.py"):
        shutil.copy(SCRIPTS / name, tmp_path / name)
    (tmp_path / "bin").mkdir()
    calls = tmp_path / "calls"
    _write(tmp_path / "bin/gh", STUB.replace("@N@", str(N)).replace("@SCOPED@", scoped).replace("@CALLS@", str(calls)))
    _write(tmp_path / "board.sh", BOARD.replace("@CALLS@", str(calls)))
    run = tmp_path / "co/runs/run-stacked"
    run.mkdir(parents=True)
    (run / "request.txt").write_text(f"CHILD — GitHub issue(s) {N}:\n")
    (run / "record.jsonl").write_text(
        '{"event":"run_start","run_id":"run-stacked","ts":"2026-10-13T08:12:00Z"}\n'
        '{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}\n'
    )
    (tmp_path / "st/auto").mkdir(parents=True)
    (tmp_path / "st/active-runs.txt").write_text("run-stacked\n")
    return tmp_path


def janitor(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}  # a live loop's SG_* never leaks in: the pass reads only this sandbox
    env.update(
        {
            "PATH": f"{tmp_path / 'bin'}:{env.get('PATH', '')}",
            "SG_STATE": str(tmp_path / "st"),
            "SG_CHECKOUT": str(tmp_path / "co"),
            "SG_REPO": "x/y",
            "SG_OWNER": "o",
            "SG_PROJECT": "4",
            "SG_BASE": "main",
            "SG_HUB_DIR": str(tmp_path / "st"),
        }
    )
    return subprocess.run(["python3", "-I", str(tmp_path / "janitor.py")], capture_output=True, text=True, env=env, check=False)


def writes(tmp_path: Path) -> str:
    f = tmp_path / "calls"
    return f.read_text() if f.exists() else ""


@pytest.mark.heavy
def test_a_merge_into_a_parent_branch_does_not_move_the_card_to_done(tmp_path):
    """The measured defect (#127, the #125 strand): the PR names the issue but its base is a parent branch, so the base
    never carries the change. The card stops at Code Complete; a merge the base did not take is not Done."""
    sandbox(tmp_path, "echo 0")
    janitor(tmp_path)
    log = writes(tmp_path)
    assert f"ADD o 4 Code Complete {N}" in log, f"the card did not stop at Code Complete: {log}"
    assert f"Done {N}" not in log, f"the card read Done over a merge the base never took: {log}"


def test_a_merge_into_the_base_still_moves_the_card_to_done(tmp_path):
    """The control: when the same merge landed on the base, the card is Done."""
    sandbox(tmp_path, "echo 1")
    janitor(tmp_path)
    log = writes(tmp_path)
    assert f"ADD o 4 Done {N}" in log, f"a merge into the base did not move the card to Done: {log}"
