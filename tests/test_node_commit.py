"""Guards for #84: the engine commits a passing node's declared workspace, so a branch tip is the run's final state.

Measured: two dispatch children ended with a tip equal to an ancestor of main while their worktrees held the work,
so forkclose refused the branch as UNLANDED. One writer, the engine; the run record stays the single truth.
"""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from squad import actions, git, node_commit, record
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.runners.types import Scope
from tests.flows import agent, doc
from tests.gitrepo import make_repo, sh, sibling_repo
from tests.helpers import router

pytestmark = pytest.mark.anyio

WS = "worktrees/w"
PASS = {"output": {"x": 1}, "spend": 0.1}
FAIL = {"output": {"status": "fail", "x": 1}, "spend": 0.1}


def req(skill="tdd", **kw):
    """One dispatch request: the shape the router's envelope admits."""
    return {"prompt": f"Run {skill} on the task.", "skill": skill, "model": "sonnet", "effort": "medium", "tools": ["Read", "Edit"],
            "stalls": 4, "spend": 1.0, "deadline": 100, "workspace": "worktrees/child", **kw}


def act(name, effect, ws=WS, **kw):
    n = {"name": name, "kind": "action", "effect": effect, "workspace": ws, "deadline": 60}
    n.update(kw)
    return n


def write_file(name, text):
    def effect(req):
        (Path(req.workspace) / name).write_text(text)
    return effect


def outs(run_dir, node=None):
    return [e for e in record.read(run_dir / "record.jsonl")
            if e["event"] == "outcome" and (node is None or e["node"] == node)]


def events(run_dir, kind):
    return [e for e in record.read(run_dir / "record.jsonl") if e["event"] == kind]


def engine(tmp_path, nodes, children, step=(), **kw):
    """A repo, a run folder, and one agent node named `a` that writes `new.txt` in its workspace."""
    repo = make_repo(tmp_path / "repo")
    ad = ScriptedAdapter({"a": {"effect": write_file("new.txt", "hi\n"), **PASS, **dict(step)}})
    return repo, Engine(from_doc(doc(nodes, children)), ad, tmp_path / "run", target=repo, run_id="r1", **kw)


async def test_a_passing_node_with_a_dirty_declared_workspace_leaves_a_commit(tmp_path):
    repo, eng = engine(tmp_path, [act("wt", "worktree_create"), agent("a", output_key="x", workspace=WS, tools=["Read", "Edit"])], ["wt", "a"])
    v = await eng.run()
    run, a = tmp_path / "run", outs(tmp_path / "run", "a")[0]
    wt = Path(a["workspace"]["path"])
    assert v.verdict == "completed" and a["outcome"] == "pass"
    assert git.dirty(wt) == [] and git.head(wt) != git.head(repo)
    assert a["workspace"]["head"] == git.head(wt)      # the snapshot a resume compares against is the committed state
    ev = events(run, "workspace_commit")
    assert [(e["node"], e["entry"], e["status"], e["head"]) for e in ev] == [("a", 1, "committed", git.head(wt))]
    assert ev[0]["workspace"] == WS and ev[0]["path"] == str(wt) and ev[0]["pos"] < a["pos"]
    assert sh(wt, "git", "log", "-1", "--format=%s") == "squad: a passed (r1)"       # the engine's own line
    assert sh(wt, "git", "log", "-1", "--format=%an") == "agent-squad"               # the engine's commit, never the model's
    assert sh(repo, "git", "rev-parse", "squad/r1/worktrees-w") == git.head(wt)      # the branch tip is the run's final state


async def test_a_passing_node_with_a_clean_workspace_writes_no_commit(tmp_path):
    repo, eng = engine(tmp_path, [act("wt", "worktree_create"), agent("a", output_key="x", workspace=WS, tools=["Edit"])], ["wt", "a"], step={"effect": None})
    v = await eng.run()
    a = outs(tmp_path / "run", "a")[0]
    assert v.verdict == "completed" and git.dirty(Path(a["workspace"]["path"])) == []
    assert events(tmp_path / "run", "workspace_commit") == []
    assert git.head(Path(a["workspace"]["path"])) == git.head(repo)


async def test_a_failed_node_leaves_its_tree_untouched(tmp_path):
    nodes = [act("wt", "worktree_create"), agent("a", output_key="x", workspace=WS, tools=["Read", "Edit"], next={"fail": "exit:fail"})]
    repo, eng = engine(tmp_path, nodes, ["wt", "a"], step=FAIL)
    v = await eng.run()
    a = outs(tmp_path / "run", "a")[0]
    wt = Path(a["workspace"]["path"])
    assert (v.verdict, a["outcome"]) == ("failed", "fail")
    assert (wt / "new.txt").exists() and git.head(wt) == git.head(repo)       # a person reads the tree it left
    assert events(tmp_path / "run", "workspace_commit") == []


async def test_a_node_with_no_declared_workspace_is_untouched(tmp_path):
    repo, eng = engine(tmp_path, [agent("a", output_key="x", tools=["Read", "Edit"])], ["a"])
    v = await eng.run()
    assert v.verdict == "completed" and (repo / "new.txt").exists() and git.dirty(repo)   # the target is never committed
    assert events(tmp_path / "run", "workspace_commit") == []


async def test_a_workspace_outside_the_run_area_is_refused_with_one_event_naming_the_reason(tmp_path):
    """The path builder keeps a graph's workspace inside the run folder, so the refusal is exercised at the seam
    the two callers reach: a node's path and a dispatch child's own worktree path."""
    repo, eng = engine(tmp_path, [agent("a", output_key="x", workspace=WS, tools=["Edit"])], ["a"])
    (repo / "new.txt").write_text("hi\n")
    ev = node_commit.commit_after(eng, node="a", entry=1, item=None, logical=WS, path=repo, outcome="pass")
    assert (ev["event"], ev["status"], ev["reason"]) == ("workspace_commit", "refused", "outside_run_area")
    assert ev["path"] == str(repo.resolve()) and ev["workspace"] == WS
    assert git.dirty(repo) and len(events(tmp_path / "run", "workspace_commit")) == 1   # nothing was committed, one event


async def test_the_git_call_keeps_a_deadline_and_a_timeout_commits_nothing_and_never_fails_the_node(tmp_path, monkeypatch):
    seen = {}

    def slow(path, message, timeout=None):
        seen.update(path=str(path), message=message, timeout=timeout)
        raise subprocess.TimeoutExpired("git commit", timeout)
    monkeypatch.setattr(actions, "commit", slow)
    nodes = [act("wt", "worktree_create"), agent("a", output_key="x", workspace=WS, tools=["Read", "Edit"])]
    repo, eng = engine(tmp_path, nodes, ["wt", "a"])
    v = await eng.run()
    a = outs(tmp_path / "run", "a")[0]
    wt = Path(a["workspace"]["path"])
    assert seen["timeout"] == node_commit.COMMIT_DEADLINE_S and seen["timeout"]                       # a deadline, not a bare call
    assert (v.verdict, a["outcome"]) == ("completed", "pass")                                          # the node never fails on it
    assert git.dirty(wt) and git.head(wt) == git.head(repo)                                            # nothing was committed
    assert [(e["status"], e["reason"]) for e in events(tmp_path / "run", "workspace_commit")] == [("refused", "timeout")]


async def test_a_passing_dispatch_childs_own_worktree_is_committed_too(tmp_path):
    """The measured case: a child ended with a tip equal to an ancestor of main while its worktree held the work."""
    repo = sibling_repo(tmp_path)
    d = router()
    d["run"]["deadline"] = 7200
    script = {"router": {"output": {"spawns": [req()]}, "spend": 0.1},
              "route.router.1": {"output": {"diff": {"files": [{"path": "made.txt"}]}, "ac_commands": ["true"]}, "spend": 0.2, "turns": 3,
                                 "effect": write_file("made.txt", "work\n")}}
    eng = Engine(from_doc(d), ScriptedAdapter(script), tmp_path / "run", target=repo, run_id="r1")
    eng.dispatch.prune = lambda: None      # the test reads the child's worktree after the run
    v = await eng.run()
    run, out = tmp_path / "run", [e for e in record.read(tmp_path / "run" / "record.jsonl")
                                 if e["event"] == "outcome" and e["node"] == "route.router.1"][0]
    wt = Path(out["workspace"]["path"])
    assert v.verdict == "completed" and out["outcome"] == "pass"
    assert git.dirty(wt) == [] and git.head(wt) != git.head(repo) and out["workspace"]["head"] == git.head(wt)
    assert [(e["node"], e["entry"], e["status"], e["workspace"]) for e in events(run, "workspace_commit")] == \
           [("route.router.1", 1, "committed", "worktrees/child")]
    assert sh(wt, "git", "log", "-1", "--format=%s") == "squad: route.router.1 passed (r1)"


async def test_a_dry_run_writes_nothing(tmp_path):
    """The frontier walk of a resume runs before any node: it commits nothing."""
    repo, eng = engine(tmp_path, [act("wt", "worktree_create"), agent("a", output_key="x", workspace=WS, tools=["Edit"])], ["wt", "a"])
    wt = Path(eng.workspace_path(WS, None))
    actions.worktree_create(repo, wt, "squad/r1/worktrees-w", 30)
    write_file("new.txt", "hi\n")(SimpleNamespace(workspace=str(wt)))
    eng.dry = True
    assert eng.ctx.commit_workspace(eng.nodes["a"], 1, Scope(None, 0), "pass") is None
    assert events(tmp_path / "run", "workspace_commit") == []
    assert git.dirty(wt) and git.head(wt) == git.head(repo)
