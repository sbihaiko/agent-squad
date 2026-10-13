"""Guards for ticket #31: action nodes and worktrees."""
import subprocess
from pathlib import Path

import pytest

from squad import git, record
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc, validate
from tests.flows import agent, doc
from tests.gitrepo import make_repo, sh

pytestmark = pytest.mark.anyio

GIT_FACT = {"name": "git", "type": "bool", "instrument": "code: git is available in the target", "source_kind": "target"}


def act(name, effect, ws="worktrees/w", **kw):
    n = {"name": name, "kind": "action", "effect": effect, "workspace": ws, "deadline": 60}
    n.update(kw)
    return n


def outs(path, node=None):
    return [e for e in record.read(path / "record.jsonl") if e["event"] == "outcome" and (node is None or e["node"] == node)]


def write_file(name, text):
    def effect(req):
        (Path(req.workspace) / name).write_text(text)
    return effect


def flow(nodes, children, **kw):
    return doc(nodes, children, facts=[GIT_FACT], **kw)


async def test_worktree_create_makes_a_worktree_and_records_its_head(tmp_path):
    repo = make_repo(tmp_path / "repo")
    d = flow([act("wt", "worktree_create")], ["wt"])
    eng = Engine(from_doc(d), ScriptedAdapter(), tmp_path / "run", target=repo, run_id="r1")
    v = await eng.run()
    e = outs(tmp_path / "run", "wt")[0]
    wt = Path(e["workspace"]["path"])
    assert v.verdict == "completed" and (wt / "base.txt").exists()
    assert e["workspace"]["head"] == git.head(repo) and e["effect"]["name"] == "worktree_create"
    assert "squad/r1/worktrees-w" in sh(repo, "git", "branch", "--list")


async def test_commit_records_a_new_head_and_nothing_to_commit_passes(tmp_path):
    """#84: a passing node's declared workspace is already committed by the engine, so this action commits what a
    node that failed left behind; the second commit finds nothing."""
    repo = make_repo(tmp_path / "repo")
    # A failed agent leaves its tree dirty (#84: the engine commits only a passing node), so the commit action has work to commit.
    d = flow([act("wt", "worktree_create"), agent("a", output_key="x", workspace="worktrees/w", tools=["Read", "Edit"], next={"fail": "continue"}),
              act("c", "commit"), act("c2", "commit")], ["wt", "a", "c", "c2"])
    adapter = ScriptedAdapter({"a": {"output": {"status": "fail", "x": 1}, "spend": 0.1, "effect": write_file("new.txt", "hi\n")}})
    eng = Engine(from_doc(d), adapter, tmp_path / "run", target=repo)
    v = await eng.run()
    c, c2 = outs(tmp_path / "run", "c")[0], outs(tmp_path / "run", "c2")[0]
    assert v.verdict == "completed" and c["effect"]["head"] != git.head(repo) and c["reasons"] == []
    assert c2["reasons"] == ["nothing_to_commit"] and c2["effect"]["head"] == c["effect"]["head"]


async def test_worktree_revert_discards_the_work_back_to_the_recorded_head(tmp_path):
    repo = make_repo(tmp_path / "repo")
    # A failed agent leaves its junk in the tree (#84); the revert the author declared discards it.
    d = flow([act("wt", "worktree_create"), agent("a", output_key="x", workspace="worktrees/w", tools=["Read", "Edit"], next={"fail": "continue"}),
              act("rv", "worktree_revert")], ["wt", "a", "rv"])
    adapter = ScriptedAdapter({"a": {"output": {"status": "fail", "x": 1}, "spend": 0.1, "effect": write_file("junk.txt", "x")}})
    eng = Engine(from_doc(d), adapter, tmp_path / "run", target=repo)
    await eng.run()
    wt = Path(outs(tmp_path / "run", "wt")[0]["workspace"]["path"])
    assert not (wt / "junk.txt").exists() and git.dirty(wt) == []


async def test_merge_land_merges_the_worktree_branch_into_the_target(tmp_path):
    repo = make_repo(tmp_path / "repo")
    d = flow([act("wt", "worktree_create"), agent("a", output_key="x", workspace="worktrees/w", tools=["Read", "Edit"]), act("m", "merge_land")],
             ["wt", "a", "m"])
    adapter = ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.1, "effect": write_file("feature.txt", "f\n")}})
    v = await Engine(from_doc(d), adapter, tmp_path / "run", target=repo).run()
    assert v.verdict == "completed" and (repo / "feature.txt").read_text() == "f\n"
    assert git.dirty(repo) == [] and "squad:" in sh(repo, "git", "log", "-1", "--format=%s")


async def test_a_merge_conflict_fails_the_node_and_leaves_the_target_clean(tmp_path):
    repo = make_repo(tmp_path / "repo")

    def conflicting(req):
        (Path(req.workspace) / "base.txt").write_text("from the worktree\n")

    d = flow([act("wt", "worktree_create"), agent("a", output_key="x", workspace="worktrees/w", tools=["Read", "Edit"]), act("m", "merge_land")],
             ["wt", "a", "m"])
    adapter = ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.1, "effect": conflicting}})
    eng = Engine(from_doc(d), adapter, tmp_path / "run", target=repo)

    orig = eng.runners["action"]

    class WithTargetChange:
        async def run(self, ctx, node, entry, sc):
            if node["name"] == "m":
                (repo / "base.txt").write_text("from the target\n")
                sh(repo, "git", "commit", "-qam", "target change")
            return await orig.run(ctx, node, entry, sc)
    eng.runners["action"] = WithTargetChange()
    v = await eng.run()
    m = outs(tmp_path / "run", "m")[0]
    assert m["outcome"] == "fail" and m["reasons"] == ["merge_conflict"]
    assert git.unmerged(repo) == [] and git.dirty(repo) == [] and v.verdict == "failed"


async def test_a_missing_git_is_a_preflight_defect_and_never_a_swap(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    monkeypatch.setattr(git, "on_path", lambda: False)
    adapter = ScriptedAdapter({"a": {"output": {"x": 1}}})
    d = flow([act("wt", "worktree_create"), agent("a", output_key="x")], ["wt", "a"])
    v = await Engine(from_doc(d), adapter, tmp_path / "run", target=repo).run()
    assert (v.verdict, v.exit_code, v.reasons) == ("failed", 1, ["git_missing"])
    assert adapter.calls == [] and outs(tmp_path / "run") == []          # no node ran: nothing was swapped in
    assert not (tmp_path / "run" / "ws").exists()


async def test_a_graph_with_no_action_needs_no_git(tmp_path, monkeypatch):
    monkeypatch.setattr(git, "on_path", lambda: False)
    d = flow([agent("a", output_key="x")], ["a"])
    v = await Engine(from_doc(d), ScriptedAdapter({"a": {"output": {"x": 1}, "spend": 0.1}}), tmp_path / "run", target=tmp_path).run()
    assert v.verdict == "completed"


async def test_a_dirty_base_is_a_conflict_before_any_node_runs(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "base.txt").write_text("edited by a person\n")
    d = flow([act("wt", "worktree_create"), act("m", "merge_land")], ["wt", "m"])
    v = await Engine(from_doc(d), ScriptedAdapter(), tmp_path / "run", target=repo).run()
    assert (v.verdict, v.exit_code) == ("conflict", 20) and v.reasons[0].startswith("dirty_base")
    assert (repo / "base.txt").read_text() == "edited by a person\n"       # the engine never touches it


async def test_an_action_may_carry_a_when_and_a_skip_is_recorded(tmp_path):
    repo = make_repo(tmp_path / "repo")
    d = flow([act("c", "commit", ws=".", when={"fact": "git", "op": "eq", "value": False})], ["c"])
    v = await Engine(from_doc(d), ScriptedAdapter(), tmp_path / "run", target=repo).run()
    assert outs(tmp_path / "run", "c")[0]["reasons"] == ["skipped"] and v.verdict == "completed"


async def test_an_action_carries_a_deadline_and_a_timeout_is_the_deadline_outcome(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    real = git.run

    def slow(args, cwd, check=False, timeout=None):
        if args[:2] == ["worktree", "add"]:
            raise subprocess.TimeoutExpired(args, timeout)
        return real(args, cwd, check, timeout)
    monkeypatch.setattr(git, "run", slow)
    d = flow([act("wt", "worktree_create")], ["wt"])
    v = await Engine(from_doc(d), ScriptedAdapter(), tmp_path / "run", target=repo).run()
    assert outs(tmp_path / "run", "wt")[0]["outcome"] == "deadline" and v.verdict == "failed"


def test_the_validator_gives_an_action_a_deadline_and_a_when_field():
    d = flow([act("c", "commit", ws=".", when={"fact": "git", "op": "eq", "value": True})], ["c"])
    assert validate(d) == []
