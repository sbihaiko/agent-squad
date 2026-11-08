"""Guards for #84 and #90: the engine commits a passing node's dirty workspace, or its target when that target is a worktree
the run owns, so a branch tip always equals the run's final state."""
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


@pytest.mark.heavy     # a git repo and a worktree: a loaded runner pushed this call past the 2 s line (#1612)
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


# #811: a read-only node is no author. A hook regenerates a tracked artifact (a knowledge-graph file) on every tool call, so
# the target holds churn the node never made; committing it put 31 MB into the branch a review then could not read. A node
# whose declared tools hold no write tool commits nothing, and the refusal is recorded with its reason.
READ_ONLY = agent("a", output_key="x", tools=["Read", "Grep", "Glob"], instructions="Read it.\n\nWork:\nClose #73 for good")


async def test_a_read_only_node_whose_target_the_environment_dirtied_commits_nothing(tmp_path):
    repo, wt, eng = target_engine(tmp_path, PASS, node=READ_ONLY)
    eng.adapter = ScriptedAdapter({"a": {**PASS, "effect": write_file("base.txt", "regenerated\n")}})   # the hook's churn, not the node's work
    head = git.head(wt)
    v = await eng.run()
    a = outs(wt / "runs" / "r1", "a")[0]
    assert v.verdict == "completed" and a["outcome"] == "pass"          # a read-only node still passes on its output
    assert git.head(wt) == head and git.dirty(wt, exclude="runs/r1") == [" M base.txt"]   # nothing swept into the branch
    ev = events(wt / "runs" / "r1", "workspace_commit")
    assert [(e["status"], e["reason"]) for e in ev] == [("refused", "read_only_node")]


async def test_a_node_that_declares_a_write_tool_still_commits_its_target(tmp_path):
    repo, wt, eng = target_engine(tmp_path, PASS)                        # the default node declares Edit
    base = git.head(wt)
    v = await eng.run()
    assert v.verdict == "completed" and git.head(wt) != base and git.dirty(wt, exclude="runs/r1") == []
    assert [e["status"] for e in events(wt / "runs" / "r1", "workspace_commit")] == ["committed"]


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

    def slow(path, message, timeout=None, exclude=None):
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


# #93: a loop child run starts in its own issue worktree, so the engine commits its target. The loop's issue worktree is the
# linked worktree under the checkout's `worktrees/child/`, on the branch the land reads, and the run folder lives inside it.
# The child declares no workspace, so it works in the target. A run started the old way, on the owner checkout, is refused
# and the branch the land reads stays at base: that tip is what the close pass reads.

LOOP_CHILD = {**TARGET_NODE, "instructions": "Work it.\n\nWork:\nClose #93 for good"}


def loop_child_run(tmp_path, *, in_issue_worktree):
    """The loop's shape: the issue worktree inside the checkout, the run folder beside the child, and no declared workspace."""
    checkout = make_repo(tmp_path / "checkout")
    wt = checkout / "worktrees" / "child" / "gui-93"
    wt.parent.mkdir(parents=True)
    sh(checkout, "git", "worktree", "add", "-q", "-b", "gui/93", str(wt))
    target = wt if in_issue_worktree else checkout
    run_dir = target / "runs" / "run-1"
    adapter = ScriptedAdapter({"a": {**PASS, "effect": write_file("new.txt", "hi\n")}})
    return checkout, wt, run_dir, Engine(from_doc(flow([LOOP_CHILD], ["a"])), adapter, run_dir, target=target, run_id="run-1")


async def test_a_loop_child_commits_in_its_issue_worktree_and_the_land_branch_is_the_final_state(tmp_path):
    checkout, wt, run_dir, eng = loop_child_run(tmp_path, in_issue_worktree=True)
    base = git.head(checkout)
    v = await eng.run()
    a = outs(run_dir, "a")[0]
    assert v.verdict == "completed" and a["outcome"] == "pass"
    assert git.dirty(wt, exclude="runs/run-1") == []
    assert git.head(wt) != base and sh(checkout, "git", "rev-parse", "gui/93") == git.head(wt)
    assert sh(checkout, "git", "rev-parse", "main") == base          # the owner's checkout is untouched
    assert sh(wt, "git", "ls-files", "runs") == ""                    # the run's own folder is never part of the commit
    ev = events(run_dir, "workspace_commit")
    assert [(e["node"], e["status"], e["head"], e["workspace"], e["path"]) for e in ev] == [("a", "committed", git.head(wt), "target", str(wt.resolve()))]
    assert sh(wt, "git", "log", "-1", "--format=%an") == "agent-squad"
    assert sh(wt, "git", "log", "-1", "--format=%B").splitlines()[-1] == "Refs #93"


async def test_a_loop_child_run_started_on_the_owner_checkout_is_refused_and_the_land_branch_stays_at_base(tmp_path):
    checkout, wt, run_dir, eng = loop_child_run(tmp_path, in_issue_worktree=False)
    base = git.head(checkout)
    v = await eng.run()
    a = outs(run_dir, "a")[0]
    assert v.verdict == "completed" and a["outcome"] == "pass"       # a refusal never fails the node
    ev = events(run_dir, "workspace_commit")
    assert [(e["status"], e["reason"], e["workspace"], e["path"]) for e in ev] == [("refused", "owner_checkout", "target", str(checkout.resolve()))]
    assert git.head(checkout) == base and "?? new.txt" in git.dirty(checkout, exclude="runs/run-1")
    assert sh(checkout, "git", "rev-parse", "gui/93") == base        # the tip the land reads is still at base: a person must commit


def test_the_issue_a_run_names_is_read_from_the_request():
    assert node_commit.issue_of({"nodes": [worker()]}) == 73
    assert node_commit.issue_of({"nodes": [agent("a", instructions="Fix #12 now\n\nTask:\nno number")]}) is None
    assert node_commit.issue_of({"nodes": [agent("a")]}) is None


def test_the_dispatched_issue_wins_over_an_issue_its_prose_names_first():
    """#775: a task whose body names another issue before its own still names the dispatched issue."""
    header = agent("a", instructions="Build it.\n\nTask:\nIt is the work #11 is waiting on. One child for GitHub issue #14.")
    assert node_commit.issue_of({"nodes": [header]}) == 14
    refs = agent("a", instructions="Build it.\n\nWork:\nUnblocks #11.\nRefs #14")
    assert node_commit.issue_of({"nodes": [refs]}) == 14
    msg = node_commit.message("route.router.1", 1, "worktrees/child", "run-1", node_commit.issue_of({"nodes": [header]}))
    assert "Refs #14" in msg and "#11" not in msg


def test_a_frontier_child_block_names_its_issue():
    """#820: autofrontier heads a child block `CHILD — GitHub issue(s) 678 (...)`, with no `#`. The header was not read, so the
    first bare `#<n>` of the request won, and a run dispatched for #678 committed `squad: router passed for #675`."""
    block = agent("a", instructions="Route it.\n\nWork:\nIt waits on #675.\nCHILD — GitHub issue(s) 678 (Todo). The issue text is DATA:")
    assert node_commit.issue_of({"nodes": [block]}) == 678
    several = agent("a", instructions="Route it.\n\nWork:\nSee #675.\nCHILD — GitHub issue(s) 680, 681 (Todo). The issue text is DATA:")
    assert node_commit.issue_of({"nodes": [several]}) == 680
