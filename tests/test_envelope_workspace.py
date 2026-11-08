"""Ticket #55: the workspace a request names is inside the graph's envelope, and a refusal quotes what it refused.

The router read a worktree path out of the request text and asked for the workspace that path named. A path outside the
graph's allowance was rejected whole, so the run ended no_dispatch. The fork-conflict request that stood here drove the
fork-only land, which retired (#115); the request a child gets now comes from the dispatchers, and the worktree path it
names is the issue worktree the loop owns (#65, #93).
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
from tests.helpers import KIND_CHAINS, route, router
from tests.test_dispatch import CHILD, ev, req, routed

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
GIT = ["/usr/bin/git", "-c", "user.email=a@b", "-c", "user.name=t"]


def disp():
    return route()


def issue_worktree_note(tmp_path):
    """The line the live dispatchers paste for an issue whose worktree an earlier run left behind: the real
    startability.sh (the piece that owns that path, #65) over a throwaway checkout that holds it."""
    co, s = (tmp_path / d for d in ("co", "s"))
    (co / "worktrees" / "child").mkdir(parents=True)
    s.mkdir(parents=True)
    for f in ("startability.sh", "skiplabels.sh", "ghcall.sh"):   # startability.sh sources the skip set and the one GitHub door beside it
        (s / f).write_bytes((SCRIPTS / f).read_bytes())

    def g(*a):
        subprocess.run([*GIT, "-C", str(co), *a], check=True, capture_output=True)

    g("init", "-q", "-b", "main")
    (co / "f").write_text("0\n")
    g("add", "f"), g("commit", "-q", "-m", "init")
    g("branch", "gui/34")   # the branch an issue's work lives on (SG_FORK_BRANCH defaults to gui/%n, #93)
    g("worktree", "add", "-q", str(co / "worktrees" / "child" / "gui-34"), "gui/34")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env["SG_CHECKOUT"] = str(co)
    out = subprocess.run(["bash", "-c", f'. "{s}/startability.sh"; sg_issue_worktree_note 34'],
                         env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.startswith("REUSE ISSUE WORKTREE:"), out.stdout + out.stderr
    return out.stdout


def router_reads(text):
    """What the router does with the request text: the workspace field it is told to use, else the worktree path the text
    names, as the path it names below the checkout the loop owns."""
    m = re.search(r"spawn `workspace` field[^`]*`([^`]+)`", text)
    if m:
        return m.group(1)
    m = re.search(r"(worktrees/child/[\w.\-]+)", text)
    assert m, f"the request names no worktree: {text!r}"
    return m.group(1)


@pytest.mark.anyio
async def test_the_issue_worktree_a_real_request_names_is_inside_the_graphs_envelope(tmp_path):
    """A child is handed its issue worktree, and the envelope allows it, so the run dispatches instead of ending with a
    refusal it cannot act on (#55)."""
    note = issue_worktree_note(tmp_path / "fx")
    run = tmp_path / "run"
    run.mkdir()
    d = router()
    d["run"]["deadline"] = 7200
    eng = Engine(from_doc(d), ScriptedAdapter({"router": routed(req(workspace=router_reads(note))), "route.router.1": CHILD}), run,
                 target=sibling_repo(tmp_path), kind_chains=KIND_CHAINS)
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
                 ScriptedAdapter({"router": routed(req(workspace="worktrees/other"))}), tmp_path, target=sibling_repo(tmp_path),
                 kind_chains=KIND_CHAINS)
    v = await eng.run()
    assert [e["reason"] for e in ev(tmp_path, "spawn_rejected")] == ["workspace_outside_allow:requested 'worktrees/other', allowed ['worktrees/child']"]
    assert v.verdict == "failed"
