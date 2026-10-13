"""Ticket #55: a fork fix request names a workspace the graph allows, and a refusal quotes what it refused.

The router read `git worktree add ~/wt-fr-34 gui/34` in the request text and asked for the workspace 'worktrees/child/gui-34'.
The graph allows only 'worktrees/child', so the envelope rejected the request whole and the run ended no_dispatch.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.envelope import validate_request
from squad.graph import from_doc
from tests.gitrepo import sibling_repo
from tests.helpers import node, router
from tests.test_dispatch import CHILD, ev, req, routed

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
GIT = ["/usr/bin/git", "-c", "user.email=a@b", "-c", "user.name=t"]


def disp():
    return node(router(), "route")


def conflict_request_body(tmp_path):
    """The body startability.sh writes for a fork branch that no longer merges into the base (the real script, a throwaway fork)."""
    fork, st, co, s = (tmp_path / d for d in ("fork", "st", "co", "s"))
    for d in (fork, st / "auto", st / "cq", st / "tests", st / "req", co / "runs" / ".hub", s, tmp_path / "bin"):
        d.mkdir(parents=True)
    for f in ("startability.sh", "startability.sh", "gate-suites.sh", "ghread.py", "ghthrottle.py", "ghcall.sh", "address.sh"):
        (s / f).write_bytes((SCRIPTS / f).read_bytes())
    (s / "startability.sh").write_text('#!/bin/bash\neval "$1"\n')
    (s / "test.sh").write_text("#!/bin/bash\nexit 1\n")
    (s / "mkreq.sh").write_text(f'#!/bin/bash\ncp "{st}/req/$1.body" "{tmp_path}/$1.body"; echo "{tmp_path}/$1.txt"\n')
    (s / "launch.py").write_text("import sys\n")
    (s / "env.sh").write_text(f"export SG_STATE={st} SG_FORK_DIR={fork} SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%n SG_REPO=x/y "
                              f"SG_CHECKOUT={co} SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n")
    (co / "runs" / ".hub" / "hub-board.json").write_text('{"items":[{"n":34,"st":"Code Complete","s":"open"}]}')
    (tmp_path / "bin" / "gh").write_text('#!/bin/bash\ncase "$*" in *"pulls?state=open"*) ;; *"-q .state"*) echo open;; *graphql*) echo "[]";; '
                                         '*"--include"*) echo "{\\"state\\":\\"open\\"}";; esac\n')
    for f in (s / "test.sh", s / "mkreq.sh", tmp_path / "bin" / "gh"):
        f.chmod(0o755)

    def g(*a):
        subprocess.run([*GIT, "-C", str(fork), *a], check=True, capture_output=True)
    g("init", "-q", "-b", "local/next")
    (fork / "f").write_text("0\n")
    g("add", "f"), g("commit", "-q", "-m", "init"), g("checkout", "-q", "-b", "gui/34")
    (fork / "f").write_text("branch\n")
    g("commit", "-qam", "work 34"), g("checkout", "-q", "local/next")
    (fork / "f").write_text("base\n")
    g("commit", "-qam", "base moved f")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env["PATH"] = f"{tmp_path / 'bin'}:{env['PATH']}"
    out = subprocess.run(["bash", str(s / "startability.sh")], env=env, capture_output=True, text=True, timeout=60).stdout
    bodies = list(tmp_path.glob("forkrebase-34-*.body"))
    assert bodies, out
    return bodies[0].read_text()


def router_reads(text):
    """What a router does with the request text: the workspace it is told to use, else the worktree path the text names."""
    m = re.search(r"spawn `workspace` field[^`]*`([^`]+)`", text)
    if m:
        return m.group(1)
    m = re.search(r"worktree add ~/wt-fr-(\d+) (gui/\d+)", text)
    return f"worktrees/child/{m.group(2).replace('/', '-')}"


@pytest.mark.anyio
async def test_a_fork_conflict_fix_request_dispatches_a_child(tmp_path):
    body = conflict_request_body(tmp_path / "fx")
    run = tmp_path / "run"
    run.mkdir()
    d = router()
    d["run"]["deadline"] = 7200
    eng = Engine(from_doc(d), ScriptedAdapter({"router": routed(req(workspace=router_reads(body))), "route.router.1": CHILD}), run,
                 target=sibling_repo(tmp_path))
    v = await eng.run()
    assert [e["reason"] for e in ev(run, "spawn_rejected")] == []
    assert [e["child"] for e in ev(run, "spawn_accepted")] == ["route.router.1"] and v.verdict == "completed"


def test_a_refused_workspace_is_quoted_with_the_allowed_list():
    """A workspace outside the allowed envelope is refused, and the refusal names what was asked and what is allowed. A
    path below an allowed one is inside it (#55), so the refused case names a directory beside it."""
    _, errs = validate_request(disp(), {"prompt": "go", "skill": "tdd", "workspace": "worktrees/other"}, 1, None, 10.0)
    assert ("workspace_outside_allow:requested 'worktrees/other', allowed ['worktrees/child']") in errs


@pytest.mark.anyio
async def test_the_rejection_event_carries_the_quoted_workspace(tmp_path):
    eng = Engine(from_doc({**router(), "run": {**router()["run"], "deadline": 7200}}),
                 ScriptedAdapter({"router": routed(req(workspace="worktrees/other"))}), tmp_path, target=sibling_repo(tmp_path))
    v = await eng.run()
    assert [e["reason"] for e in ev(tmp_path, "spawn_rejected")] == ["workspace_outside_allow:requested 'worktrees/other', allowed ['worktrees/child']"]
    assert v.verdict == "failed"
