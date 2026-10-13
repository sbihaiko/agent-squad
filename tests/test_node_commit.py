"""Guards for #84: the engine commits a passing node's dirty workspace, so a branch tip always equals the run's final state."""
import subprocess
from pathlib import Path

import pytest

from squad import git, node_commit, record
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from tests.flows import agent, doc
from tests.gitrepo import make_repo, sh
from tests.test_actions import GIT_FACT, act, outs, write_file

pytestmark = pytest.mark.anyio

WS = "worktrees/w"
PASS, FAIL = {"output": {"x": 1}, "spend": 0.1}, {"output": {"status": "fail", "x": 1}, "spend": 0.1}


def flow(nodes, children):
    return doc(nodes, children, facts=[GIT_FACT])


def worker(**kw):
    return agent("a", output_key="x", workspace=WS, tools=["Read", "Edit"], instructions="Build it.\n\nWork:\nClose #73 for good", **kw)


def events(path, kind):
    return [e for e in record.read(path / "record.jsonl") if e["event"] == kind]


def engine(tmp_path, nodes, children, step, **kw):
    repo = make_repo(tmp_path / "repo")
    adapter = ScriptedAdapter({"a": {**step, "effect": write_file("new.txt", "hi\n")}})
    return repo, Engine(from_doc(flow(nodes, children)), adapter, tmp_path / "run", target=repo, run_id="r1", **kw)


async def test_a_passing_node_with_a_dirty_workspace_is_committed_and_the_tip_is_the_final_state(tmp_path):
    repo, eng = engine(tmp_path, [act("wt", "worktree_create"), worker()], ["wt", "a"], PASS)
    v = await eng.run()
    assert v.verdict == "completed"
    a = outs(tmp_path / "run", "a")[0]
    wt = Path(a["workspace"]["path"])
    assert a["outcome"] == "pass" and git.dirty(wt) == [] and git.head(wt) != git.head(repo)
    assert a["workspace"]["head"] == git.head(wt)      # the snapshot a resume compares against is the committed state
    ev = events(tmp_path / "run", "workspace_commit")
    assert [(e["node"], e["entry"], e["status"], e["head"]) for e in ev] == [("a", 1, "committed", git.head(wt))]
    assert ev[0]["workspace"] == WS and ev[0]["path"] == str(wt) and ev[0]["pos"] < a["pos"]
    msg = sh(wt, "git", "log", "-1", "--format=%B").splitlines()
    assert "a" in msg[0] and "#73" in msg[0] and msg[1] == "" and "pass" in "\n".join(msg[2:]) and msg[-1] == "Refs #73"
    assert sh(wt, "git", "log", "-1", "--format=%an") == "agent-squad"     # the engine's commit, never the model's


async def test_a_passing_node_with_a_clean_workspace_writes_no_commit(tmp_path):
    repo = make_repo(tmp_path / "repo")
    eng = Engine(from_doc(flow([act("wt", "worktree_create"), worker()], ["wt", "a"])), ScriptedAdapter({"a": PASS}), tmp_path / "run", target=repo)
    v = await eng.run()
    a = outs(tmp_path / "run", "a")[0]
    assert v.verdict == "completed" and a["workspace"]["head"] == git.head(repo)
    assert events(tmp_path / "run", "workspace_commit") == []


async def test_a_failing_node_leaves_its_tree_as_it_is(tmp_path):
    repo, eng = engine(tmp_path, [act("wt", "worktree_create"), worker(next={"fail": "exit:fail"})], ["wt", "a"], FAIL)
    v = await eng.run()
    a = outs(tmp_path / "run", "a")[0]
    wt = Path(a["workspace"]["path"])
    assert v.verdict == "failed" and a["outcome"] == "fail"
    assert git.dirty(wt) == ["?? new.txt"] and git.head(wt) == git.head(repo)
    assert events(tmp_path / "run", "workspace_commit") == []


async def test_a_node_with_no_declared_workspace_never_touches_the_owners_checkout(tmp_path):
    repo, eng = engine(tmp_path, [agent("a", output_key="x", tools=["Read", "Edit"])], ["a"], PASS)
    head = git.head(repo)
    v = await eng.run()
    assert v.verdict == "completed" and git.dirty(repo) == ["?? new.txt"] and git.head(repo) == head
    ev = events(tmp_path / "run", "workspace_commit")
    assert [(e["status"], e["reason"], e["workspace"], e["path"]) for e in ev] == [("refused", "owner_checkout", "target", str(repo.resolve()))]


# #90: the loop starts a run with `--target <issue worktree>`, so its nodes declare no workspace: the target is the workspace.
# A target that is a git worktree the run owns (the run folder lives inside it, and it is not the main checkout) is committed
# the same way a declared workspace is. The owner's checkout and a worktree outside the run are refused as an event, never touched.

TARGET_NODE = agent("a", output_key="x", tools=["Read", "Edit"], instructions="Build it.\n\nWork:\nClose #73 for good")


def issue_worktree(tmp_path):
    """The loop's shape: a worktree of the issue branch, next to the checkout, with the run folder inside it."""
    repo = make_repo(tmp_path / "repo")
    wt = tmp_path / "wt-as-73"
    sh(repo, "git", "worktree", "add", "-q", "-b", "gui/73", str(wt))
    return repo, wt


def target_engine(tmp_path, step, *, node=TARGET_NODE, run_dir=None, target=None, **kw):
    repo, wt = issue_worktree(tmp_path)
    target = target or wt
    adapter = ScriptedAdapter({"a": {**step, "effect": write_file("new.txt", "hi\n")}} if step else {})
    return repo, wt, Engine(from_doc(flow([node], ["a"])), adapter, run_dir or target / "runs" / "r1", target=target, run_id="r1", **kw)


async def test_a_passing_node_whose_target_is_a_run_owned_worktree_is_committed_and_the_branch_tip_is_the_final_state(tmp_path):
    repo, wt, eng = target_engine(tmp_path, PASS)
    base = git.head(repo)
    v = await eng.run()
    a = outs(wt / "runs" / "r1", "a")[0]
    assert v.verdict == "completed" and a["outcome"] == "pass"
    assert git.dirty(wt, exclude="runs/r1") == [] and git.head(wt) != base
    assert sh(repo, "git", "rev-parse", "gui/73") == git.head(wt)          # the branch tip is the run's final state
    assert sh(wt, "git", "ls-files", "runs") == ""                           # the run's own folder is never part of the commit
    ev = events(wt / "runs" / "r1", "workspace_commit")
    assert [(e["node"], e["entry"], e["status"], e["head"], e["workspace"], e["path"]) for e in ev] == [("a", 1, "committed", git.head(wt), "target", str(wt.resolve()))]
    assert ev[0]["pos"] < a["pos"]
    msg = sh(wt, "git", "log", "-1", "--format=%B").splitlines()
    assert "a" in msg[0] and "#73" in msg[0] and msg[1] == "" and "pass" in "\n".join(msg[2:]) and msg[-1] == "Refs #73"
    assert sh(wt, "git", "log", "-1", "--format=%an") == "agent-squad"


async def test_a_clean_target_commits_nothing(tmp_path):
    repo, wt, eng = target_engine(tmp_path, None)
    eng.adapter = ScriptedAdapter({"a": PASS})
    head = git.head(wt)
    v = await eng.run()
    assert v.verdict == "completed" and git.head(wt) == head
    assert events(wt / "runs" / "r1", "workspace_commit") == []


async def test_a_failed_node_leaves_the_target_as_it_is(tmp_path):
    repo, wt, eng = target_engine(tmp_path, FAIL, node={**TARGET_NODE, "next": {"fail": "exit:fail"}})
    head = git.head(wt)
    v = await eng.run()
    assert v.verdict == "failed" and outs(wt / "runs" / "r1", "a")[0]["outcome"] == "fail"
    assert git.dirty(wt, exclude="runs/r1") == ["?? new.txt"] and git.head(wt) == head
    assert events(wt / "runs" / "r1", "workspace_commit") == []


async def test_a_target_worktree_outside_the_run_is_refused_with_the_reason(tmp_path):
    repo, wt, eng = target_engine(tmp_path, PASS, run_dir=tmp_path / "run")   # the run folder is not inside the target
    head = git.head(wt)
    v = await eng.run()
    a = outs(tmp_path / "run", "a")[0]
    assert v.verdict == "completed" and a["outcome"] == "pass"                 # a refusal never fails the node
    assert git.dirty(wt) == ["?? new.txt"] and git.head(wt) == head
    ev = events(tmp_path / "run", "workspace_commit")
    assert [(e["status"], e["reason"], e["path"]) for e in ev] == [("refused", "outside_run_area", str(wt.resolve()))]


async def test_the_owners_checkout_is_refused_even_when_the_run_folder_is_inside_it(tmp_path):
    repo, wt, eng = target_engine(tmp_path, PASS, target=make_repo(tmp_path / "own"))
    own = eng.target
    head = git.head(own)
    v = await eng.run()
    assert v.verdict == "completed" and git.head(own) == head and "?? new.txt" in git.dirty(own)
    ev = events(own / "runs" / "r1", "workspace_commit")
    assert [(e["status"], e["reason"]) for e in ev] == [("refused", "owner_checkout")]


async def test_a_git_that_times_out_on_the_target_commits_nothing(tmp_path, monkeypatch):
    repo, wt, eng = target_engine(tmp_path, PASS)

    def slow(path, message, timeout=None, exclude=None):
        raise subprocess.TimeoutExpired("git", timeout or 0)
    monkeypatch.setattr(node_commit.actions, "commit", slow)
    head = git.head(wt)
    v = await eng.run()
    assert v.verdict == "completed" and outs(wt / "runs" / "r1", "a")[0]["outcome"] == "pass"
    assert git.dirty(wt, exclude="runs/r1") == ["?? new.txt"] and git.head(wt) == head
    ev = events(wt / "runs" / "r1", "workspace_commit")
    assert [(e["status"], e["reason"]) for e in ev] == [("refused", "timeout")]


async def test_a_dry_run_writes_nothing_to_the_target(tmp_path):
    repo, wt, eng = target_engine(tmp_path, None)
    (wt / "new.txt").write_text("hi\n")
    head, eng.dry = git.head(wt), True
    assert node_commit.commit_after(eng, node="a", entry=1, item=None, logical=None, path=wt, outcome="pass") is None
    assert git.dirty(wt) == ["?? new.txt"] and git.head(wt) == head and eng.record.events == []


async def test_a_workspace_outside_the_run_area_is_refused_with_a_record_event(tmp_path, monkeypatch):
    repo, eng = engine(tmp_path, [worker()], ["a"], PASS)
    other = make_repo(tmp_path / "other")
    monkeypatch.setattr(eng, "workspace_path", lambda logical, item: other if logical == WS else eng.target)
    head = git.head(other)
    v = await eng.run()
    a = outs(tmp_path / "run", "a")[0]
    assert v.verdict == "completed" and a["outcome"] == "pass"       # a refusal never fails the node
    assert git.dirty(other) == ["?? new.txt"] and git.head(other) == head
    ev = events(tmp_path / "run", "workspace_commit")
    assert len(ev) == 1 and ev[0]["status"] == "refused" and ev[0]["reason"] == "outside_run_area" and ev[0]["path"] == str(other.resolve())


async def test_a_git_that_times_out_commits_nothing_and_does_not_fail_the_node(tmp_path, monkeypatch):
    repo, eng = engine(tmp_path, [act("wt", "worktree_create"), worker()], ["wt", "a"], PASS)

    def slow(path, message, timeout=None):
        raise subprocess.TimeoutExpired("git", timeout or 0)
    monkeypatch.setattr(node_commit.actions, "commit", slow)
    v = await eng.run()
    a = outs(tmp_path / "run", "a")[0]
    wt = Path(a["workspace"]["path"])
    assert v.verdict == "completed" and a["outcome"] == "pass"
    assert git.dirty(wt) == ["?? new.txt"] and git.head(wt) == git.head(repo)
    ev = events(tmp_path / "run", "workspace_commit")
    assert len(ev) == 1 and ev[0]["status"] == "refused" and ev[0]["reason"] == "timeout"


async def test_a_dry_run_writes_nothing(tmp_path):
    repo = make_repo(tmp_path / "repo")
    eng = Engine(from_doc(flow([worker()], ["a"])), ScriptedAdapter(), tmp_path / "run", target=repo)
    wt = eng.workspace_path(WS, None)
    wt.parent.mkdir(parents=True)
    sh(repo, "git", "worktree", "add", "-q", str(wt))
    (wt / "new.txt").write_text("hi\n")
    head, eng.dry = git.head(wt), True
    assert node_commit.commit_after(eng, node="a", entry=1, item=None, logical=WS, path=wt, outcome="pass") is None
    assert git.dirty(wt) == ["?? new.txt"] and git.head(wt) == head and eng.record.events == []


def test_the_issue_a_run_names_is_read_from_the_request():
    assert node_commit.issue_of({"nodes": [worker()]}) == 73
    assert node_commit.issue_of({"nodes": [agent("a", instructions="Fix #12 now\n\nTask:\nno number")]}) is None
    assert node_commit.issue_of({"nodes": [agent("a")]}) is None
