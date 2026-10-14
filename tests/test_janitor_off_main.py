"""A PR merged off the base never closes its ticket in silence (#285).

Two PRs were stacked on a branch; the parent squash-merged into the base and the stacked PRs then merged into the stale
branch. Their issues were closed by hand and later runs failed with "not landed on main". The janitor now retargets a
stacked PR whose parent is gone, and reopens an issue closed over a loop PR that merged off the base.

These cases drive janitor.py end to end with a stub `gh` on PATH and a temp state dir. No case touches the network.
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
N = 2040  # the issue a finished run delivered

STUB = """#!/bin/bash
a="$*"; echo "$a" >> "@CALLS@"
case "$a" in
  *"-X PATCH"*|*"--method PATCH"*) echo '{}';;
  *graphql*) printf '%s' '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}';;
  *"repos/x/y/pulls?state=open"*) cat "@OPEN@";;
  *repos/x/y/pulls/77*) echo '{"number":77,"base":{"ref":"feat/parent"},"body":"@BODY@","merged_at":"2026-10-13T09:00:00Z"}';;
  *repos/x/y/pulls/*) echo clean;;
  *search/issues*)
    case "$a" in *"-X GET"*|*"--method GET"*|*"search/issues?q="*) ;; *) echo '{"message":"Not Found"}'; exit 1;; esac
    case "$a" in
      *"base:"*"Refs #@N@"*) echo 0;;
      *"search/issues?q="*) echo '{"total_count":@HITS@,"items":[@ITEMS@]}';;
      *"Refs #@N@"*) echo @HITS@;;
      *) echo 0;; esac;;
  *projectsV2/*/fields*) echo FID4;;
  *projectsV2/*/items*) ;;
  *issues/@N@*) printf '%s\\t\\t%s\\n' @STATE@ Issue;;
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


def sandbox(tmp_path: Path, *, open_prs: str = "", state: str = "closed", hits: int = 0, body: str = f"Refs #{N}") -> Path:
    """The janitor with the door beside it, a stub `gh` and one finished run for the issue. `hits` PRs name the issue
    (one, #77, merged into feat/parent); `open_prs` is the open-PR list as the janitor's projection prints it."""
    for name in ("janitor.py", "ghcall.sh", "ghthrottle.py", "ghread.py", "issue_state.py"):
        shutil.copy(SCRIPTS / name, tmp_path / name)
    (tmp_path / "bin").mkdir()
    calls = tmp_path / "calls"
    (tmp_path / "open.txt").write_text(open_prs)
    stub = (
        STUB.replace("@N@", str(N)).replace("@CALLS@", str(calls)).replace("@OPEN@", str(tmp_path / "open.txt"))
        .replace("@HITS@", str(hits)).replace("@ITEMS@", '{"number":77}' if hits else "").replace("@STATE@", state)
        .replace("@BODY@", body)
    )
    _write(tmp_path / "bin/gh", stub)
    _write(tmp_path / "board.sh", BOARD.replace("@CALLS@", str(calls)))
    run = tmp_path / "co/runs/run-offmain"
    run.mkdir(parents=True)
    (run / "request.txt").write_text(f"CHILD — GitHub issue(s) {N}:\n")
    (run / "record.jsonl").write_text(
        '{"event":"run_start","run_id":"run-offmain","ts":"2026-10-13T08:12:00Z"}\n'
        '{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}\n'
    )
    (tmp_path / "st/auto").mkdir(parents=True)
    (tmp_path / "st/cq").mkdir()
    (tmp_path / "st/active-runs.txt").write_text("run-offmain\n")
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
            "SG_HUB_DIR": str(tmp_path / "st"),
        }
    )
    return subprocess.run(["python3", "-I", str(tmp_path / "janitor.py")], capture_output=True, text=True, env=env, check=False)


def calls(tmp_path: Path) -> list[str]:
    f = tmp_path / "calls"
    return f.read_text().splitlines() if f.exists() else []


def patches(tmp_path: Path, what: str) -> list[str]:
    return [c for c in calls(tmp_path) if "PATCH" in c and what in c]


def comments(tmp_path: Path) -> list[str]:
    return [p.read_text() for p in (tmp_path / "st/cq").glob("*.md")]


@pytest.mark.heavy
def test_an_open_pr_on_a_dead_base_is_retargeted_to_the_base_once(tmp_path):
    """PR 12 is stacked on feat/parent, which no open PR carries: the janitor retargets it. PR 13 is stacked on feat/live,
    which PR 14 carries: it is left alone."""
    sandbox(
        tmp_path,
        open_prs=(
            "12 aaa111111 feat/parent feat/12 2026-10-14T00:00:00Z\n"
            "13 bbb222222 feat/live feat/13 2026-10-14T00:00:00Z\n"
            "14 ccc333333 main feat/live 2026-10-14T00:00:00Z\n"
        ),
    )
    p = janitor(tmp_path)
    assert len(patches(tmp_path, "pulls/12")) == 1, f"PR 12 was not retargeted exactly once: {calls(tmp_path)}"
    assert "base=main" in patches(tmp_path, "pulls/12")[0], f"the PATCH does not name the base: {patches(tmp_path, 'pulls/12')}"
    assert not patches(tmp_path, "pulls/13"), f"PR 13 has a live base and must be left alone: {calls(tmp_path)}"
    log = (tmp_path / "st/cycle.log").read_text() if (tmp_path / "st/cycle.log").exists() else ""
    assert "RETARGET #12" in log, f"the pass did not say so: {log}{p.stderr}"


@pytest.mark.heavy
def test_a_pr_on_the_base_is_never_retargeted(tmp_path):
    sandbox(tmp_path, open_prs="14 ccc333333 main feat/live 2026-10-14T00:00:00Z\n")
    janitor(tmp_path)
    assert not patches(tmp_path, "pulls/"), f"a PR already on the base was patched: {calls(tmp_path)}"


@pytest.mark.heavy
def test_an_issue_closed_over_a_loop_pr_merged_off_the_base_is_reopened_once(tmp_path):
    sandbox(tmp_path, hits=1)
    janitor(tmp_path)
    assert len(patches(tmp_path, f"issues/{N}")) == 1 and "state=open" in patches(tmp_path, f"issues/{N}")[0], f"the issue was not reopened: {calls(tmp_path)}"
    cm = comments(tmp_path)
    assert len(cm) == 1 and "#77" in cm[0] and "feat/parent" in cm[0], f"one comment must name the PR and the base: {cm}"
    janitor(tmp_path)
    assert len(patches(tmp_path, f"issues/{N}")) == 1, f"a second pass reopened again: {calls(tmp_path)}"
    assert len(comments(tmp_path)) == 1, "a second pass wrote another comment"


@pytest.mark.heavy
def test_an_issue_closed_by_hand_with_no_loop_pr_stays_closed(tmp_path):
    sandbox(tmp_path, hits=0)
    janitor(tmp_path)
    assert not patches(tmp_path, f"issues/{N}"), f"an issue with no PR was reopened: {calls(tmp_path)}"
    assert not comments(tmp_path)


@pytest.mark.heavy
def test_a_pr_that_only_mentions_the_issue_does_not_reopen_it(tmp_path):
    """The merged PR names the issue in prose, not as `Refs #n` or `Closes #n`: it is no loop PR."""
    sandbox(tmp_path, hits=1, body=f"see #{N} for context")
    janitor(tmp_path)
    assert not patches(tmp_path, f"issues/{N}"), f"a mention reopened the issue: {calls(tmp_path)}"


@pytest.mark.heavy
def test_an_open_issue_is_never_reopened(tmp_path):
    sandbox(tmp_path, state="open", hits=1)
    janitor(tmp_path)
    assert not patches(tmp_path, f"issues/{N}")
