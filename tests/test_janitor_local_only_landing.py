"""A run whose landing reached no remote branch delivered nothing: its card returns to Todo and its dispatch goes.

A run's own `land` event names a head only in its own worktree. When that head is on no remote branch and not in the
landing branch, no pass can land it: the work is reachable by nobody, so the run delivered nothing (ADR-0143's one
backward move, the no-landing return to Todo). The pass read every completed run's land event as delivery, whatever the
head's reach, so such a card stayed in Code Complete and its dispatch marker held it out of the frontier for good.

The same run whose head does stand on a remote branch is delivered work in the making: the card keeps Code Complete and
its marker stays for the landing pass.

These cases drive janitor.py end to end: a stub `gh` on PATH, a temp state dir, and a real checkout with a bare origin.
No case touches the network, the live loop state or the live board.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

from tests.gitrepo import make_repo, sh

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
N = 2101  # the issue the finished run worked

STUB = """#!/bin/bash
a="$*"; echo "$a" >> "@CALLS@"
case "$a" in
  *graphql*) printf '%s' '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}';;
  *search/issues*)
    case "$a" in *"-X GET"*|*"--method GET"*) ;; *) echo '{"message":"Not Found"}'; exit 1;; esac
    echo 0;;
  *projectsV2/*/fields*) echo FID4;;
  *projectsV2/*/items*) printf '%s\\t%s\\thttps://api.github.com/repos/x/y\\n' @N@ 'Code Complete';;
  *issues/*"-q .state"*) echo open;;
  *issues/*) echo -e 'open\\t\\tIssue';;
esac
"""

BOARD = """#!/bin/bash
case "$1" in
  --add) echo "ADD $2 $3 $4 $5" >> "@CALLS@";;
  --run-fields|--clear-run) :;;
  *) echo "STATUS $1 $2 $3 $4" >> "@CALLS@";;
esac
"""


def _write(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def sandbox(tmp_path: Path, *, pushed: bool, known: bool = True) -> Path:
    """The janitor beside its modules, a stub `gh` and board, a checkout with a bare origin that holds `main`, and one
    finished run for issue #2101 whose child made one commit. `pushed` sends that commit to a remote branch; without it
    the commit stands on a local branch only. `known=False` makes the record name a head the checkout cannot read at
    all, the case where no reach can be proved."""
    for name in ("janitor.py", "ghcall.sh", "ghthrottle.py", "ghread.py", "issue_state.py", "board_columns.py",
                 "board-definition.json", "board_facts.py", "spec_fold.py", "issue_ledger.py", "wip_snapshot.py"):
        shutil.copy(SCRIPTS / name, tmp_path / name)
    (tmp_path / "bin").mkdir()
    calls = tmp_path / "calls"
    _write(tmp_path / "bin/gh", STUB.replace("@N@", str(N)).replace("@CALLS@", str(calls)))
    _write(tmp_path / "board.sh", BOARD.replace("@CALLS@", str(calls)))
    co = make_repo(tmp_path / "co")
    origin = tmp_path / "origin.git"
    sh(tmp_path, "git", "init", "-q", "--bare", str(origin))
    sh(co, "git", "remote", "add", "origin", str(origin))
    sh(co, "git", "push", "-q", "origin", "main")
    run = co / "runs/run-local"
    run.mkdir(parents=True)
    (run / "request.txt").write_text(f"CHILD — GitHub issue(s) {N}:\n")
    child = run / "ws/child"
    sh(co, "git", "worktree", "add", "-q", "-b", f"gui/{N}", str(child))
    (child / "work.txt").write_text("the run's own change\n")
    sh(child, "git", "add", "work.txt")
    sh(child, "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", f"the run's change (#{N})")
    head = sh(child, "git", "rev-parse", "HEAD")
    if not known:
        head = "0" * 40   # a commit the checkout does not hold: its reach cannot be read at all
    if pushed:
        sh(child, "git", "push", "-q", "origin", f"HEAD:refs/heads/gui/{N}")
    before = sh(co, "git", "rev-parse", "HEAD")
    (run / "record.jsonl").write_text(
        '{"event":"run_start","run_id":"run-local","ts":"2026-10-13T08:12:00Z"}\n'
        '{"event":"land","node":"route.router.1","parent":"route","entry":1,"outcome":"pass","reasons":[],'
        f'"effect":{{"name":"merge_land","before":"{before}","head":"{head}","held_out":"pass"}}}}\n'
        '{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}\n'
    )
    # The fixture must drive the janitor's land-event path, never a default: a malformed line would be skipped and every
    # assertion would pass for the wrong reason. Each line has to parse.
    for line in (run / "record.jsonl").read_text().splitlines():
        assert json.loads(line)["event"] in ("run_start", "land", "verdict"), f"a record line drifted: {line}"
    auto = tmp_path / "st/auto"
    auto.mkdir(parents=True)
    (tmp_path / "st/active-runs.txt").write_text("run-local\n")
    (auto / f"disp-{N}").write_text("run-local\n")
    old = time.time() - 3600   # the run ended an hour ago: past every settle window
    for p in [*run.rglob("*"), run, *auto.iterdir()]:
        os.utime(p, (old, old))
    return tmp_path


def janitor(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    env.update(
        {
            "PATH": f"{tmp_path / 'bin'}:{env.get('PATH', '')}",
            "SG_STATE": str(tmp_path / "st"),
            "SG_CHECKOUT": str(tmp_path / "co"),
            "SG_REPO": "x/y",
            "SG_OWNER": "o",
            "SG_PROJECT": "4",
            "SG_BASE": "main",
            "SG_LAND_BRANCH": "main",
            "SG_HUB_DIR": str(tmp_path / "st"),
            "SG_SQUAD": str(ROOT),   # the janitor reads squad/handhold.py from the repository
        }
    )
    return subprocess.run(["python3", "-I", str(tmp_path / "janitor.py")], capture_output=True, text=True, env=env,
                          check=False)


def writes(tmp_path: Path) -> str:
    f = tmp_path / "calls"
    return f.read_text() if f.exists() else ""


def decided(tmp_path: Path) -> str:
    """The janitor's own decision log: the rule names the path it took, so a case never passes on another path's write."""
    f = tmp_path / "st/cycle.log"
    return f.read_text() if f.exists() else ""


@pytest.mark.heavy
def test_a_landing_on_no_remote_branch_returns_the_card_to_todo(tmp_path):
    """The stranded card (#1958): the run's head stands on a local branch only, so nothing is delivered and the card
    leaves Code Complete by the one backward move."""
    sandbox(tmp_path, pushed=False)
    janitor(tmp_path)
    log = writes(tmp_path)
    assert f"DISPATCH-FAILED #{N}" in decided(tmp_path), "the no-delivery rule did not decide this run"
    assert f"STATUS o 4 Todo {N}" in log, f"the card did not return to Todo: {log}"


@pytest.mark.heavy
def test_a_landing_on_no_remote_branch_releases_the_dispatch_and_names_the_salvage(tmp_path):
    """The same run releases its issue: the marker goes, so the frontier dispatches the issue again, and the commit on
    no remote branch is named for salvage."""
    sandbox(tmp_path, pushed=False)
    janitor(tmp_path)
    assert f"DISPATCH-FAILED #{N}" in decided(tmp_path), "the no-delivery rule did not decide this run"
    assert not (tmp_path / f"st/auto/disp-{N}").exists(), "the dispatch marker held an issue nothing can land"
    assert (tmp_path / f"st/auto/salvage-{N}").exists(), "the local-only commit was not named for salvage"


@pytest.mark.heavy
def test_a_head_the_checkout_cannot_read_keeps_the_old_reading(tmp_path):
    """A record naming a head this checkout does not hold proves no reach, so the run is not read as no delivery: the
    old reading stands and the card keeps its column. Only a head the checkout can see and that no remote holds is the
    stranded case."""
    sandbox(tmp_path, pushed=False, known=False)
    janitor(tmp_path)
    assert f"DISPATCH-FAILED #{N}" not in decided(tmp_path), "a head the checkout cannot see was read as no delivery"
    assert (tmp_path / f"st/auto/disp-{N}").exists(), "the marker went on a head the checkout cannot read"


@pytest.mark.heavy
def test_a_landing_that_stands_on_a_remote_branch_keeps_the_card_and_the_marker(tmp_path):
    """The other side: the head is on a remote work branch, so the landing pass can take it. The card keeps Code Complete
    and the marker stays."""
    sandbox(tmp_path, pushed=True)
    janitor(tmp_path)
    log = writes(tmp_path)
    assert f"DISPATCH-FAILED #{N}" not in decided(tmp_path), "a reachable landing was read as no delivery"
    assert f"Todo {N}" not in log, f"a reachable landing was read as no delivery: {log}"
    assert (tmp_path / f"st/auto/disp-{N}").exists(), "the marker went while a remote branch could still land"
