"""Ticket #41: the five-phase graph end to end on the scripted adapter."""
from pathlib import Path

import pytest

from squad import git
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.record import read
from tests.gitrepo import make_repo, sh
from tests.helpers import five

pytestmark = pytest.mark.anyio

OK = {"output": {"ok": 1}, "spend": 0.05}
TRIVIAL, MEDIUM, LARGE = ({"files": 1, "added_lines": 5}, {"files": 5, "added_lines": 100}, {"files": 30, "added_lines": 900})


def writes(name=None, text="work\n"):
    def effect(req):
        (Path(req.workspace) / (name or f"item-{req.item}.txt")).write_text(text)
    return effect


def script(size, items=1, actor=None):
    s = {"scout": {"output": {"size_estimate": size, "paths": ["src/a.py"], "ac_commands": ["true"]}, "spend": 0.05},
         "spec_draft": {"output": {"spec": {"ac_commands": ["true"]}}, "spend": 0.05},
         "decompose": {"output": {"work_items": [{"id": i} for i in range(items)]}, "spend": 0.05},
         "actor": actor or {"output": {"diff": {"files": [{"path": "a.txt"}], "added_lines": 3}}, "spend": 0.1, "effect": writes()}}
    return s


def engine(tmp_path, repo, sc, **kw):
    ad = ScriptedAdapter(sc, repeat_last=True, default=OK)
    return Engine(from_doc(five()), ad, tmp_path / "run", target=repo, **kw)


def outs(tmp_path):
    return [e for e in read(tmp_path / "run" / "record.jsonl") if e["event"] == "outcome"]


def ran(tmp_path, node):
    return [e for e in outs(tmp_path) if e["node"] == node and e["reasons"] != ["skipped"] and not any(r.startswith("skipped") for r in e["reasons"])]


@pytest.mark.heavy     # 1.1 to 1.3 s here and 2.8 s on a loaded runner, over the two-second line for an unmarked call
async def test_a_trivial_input_skips_spec_and_plan_but_the_gates_still_run(tmp_path):
    repo = make_repo(tmp_path / "repo")
    eng = engine(tmp_path, repo, script(TRIVIAL))
    v = await eng.run()
    assert v.verdict == "completed", v.message
    skipped = {e["node"] for e in outs(tmp_path) if "skipped" in e["reasons"]}
    assert {"spec", "plan"} <= skipped and not ran(tmp_path, "spec_draft") and not ran(tmp_path, "decompose")
    for gate in ("budget_gate", "scope_gate", "security_gate", "ac_verify", "learning_gate"):
        assert ran(tmp_path, gate), gate
    assert (repo / "item-1.txt").exists()      # the item landed on the target through the merge action


@pytest.mark.heavy     # 1.0 s here and 3.0 s on a loaded runner, over the two-second line for an unmarked call
async def test_a_medium_input_runs_spec_and_skips_plan(tmp_path):
    repo = make_repo(tmp_path / "repo")
    eng = engine(tmp_path, repo, script(MEDIUM))
    v = await eng.run()
    assert v.verdict == "completed", v.message
    assert ran(tmp_path, "spec_draft") and ran(tmp_path, "spec_gate") and ran(tmp_path, "spec_proxy")
    assert "skipped" in [e for e in outs(tmp_path) if e["node"] == "plan"][0]["reasons"]
    assert not ran(tmp_path, "critic")          # review_required is false for a small diff in a medium tier


@pytest.mark.heavy
async def test_a_large_input_plans_and_runs_each_item_in_its_own_worktree(tmp_path):
    repo = make_repo(tmp_path / "repo")
    eng = engine(tmp_path, repo, script(LARGE, items=3))
    v = await eng.run()
    assert v.verdict == "completed", v.message
    assert ran(tmp_path, "decompose") and ran(tmp_path, "plan_gate")
    items = sorted({e["item"] for e in ran(tmp_path, "actor")})
    assert items == [1, 2, 3]
    paths = {e["workspace"]["path"] for e in ran(tmp_path, "actor")}
    assert len(paths) == 3                      # three items, three worktrees
    assert len(ran(tmp_path, "critic")) == 3    # review_required holds for a large tier
    assert ran(tmp_path, "audit_c")             # more than one work item
    for i in (1, 2, 3):
        assert (repo / f"item-{i}.txt").exists()


def conflicting_actor():
    def effect(req):
        (Path(req.workspace) / "base.txt").write_text("from the worktree\n")
        repo = Path(git.run(["rev-parse", "--git-common-dir"], req.workspace).stdout.strip()).resolve().parent
        (repo / "base.txt").write_text("from the target\n")
        sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "target moved")
    return {"output": {"diff": {"files": [{"path": "base.txt"}], "added_lines": 1}}, "spend": 0.1, "effect": effect}


async def test_the_manual_merge_wait_stops_at_exit_10_and_a_resume_finishes_the_run(tmp_path):
    repo = make_repo(tmp_path / "repo")
    eng = engine(tmp_path, repo, script(MEDIUM, actor=conflicting_actor()))
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("needs-human", 10), v.message
    merge = [e for e in outs(tmp_path) if e["node"] == "merge"][0]
    assert merge["outcome"] == "fail" and merge["reasons"] == ["merge_conflict"]
    w = [e for e in read(tmp_path / "run" / "record.jsonl") if e["event"] == "wait"]
    assert w[0]["node"] == "manual_merge" and w[0]["owner"] == "rafael" and w[0]["item"] == 1
    assert not ran(tmp_path, "ac_verify")       # the run stopped before verify
    eng2 = engine(tmp_path, repo, script(MEDIUM, actor=conflicting_actor()))
    v2 = await eng2.resume(answer="pass")
    assert v2.verdict == "completed", v2.message
    assert ran(tmp_path, "ac_verify") and [c.node for c in eng2.adapter.calls].count("actor") == 0     # nothing before the wait runs again
    assert len([e for e in read(tmp_path / "run" / "record.jsonl") if e["event"] == "resume"]) == 1


@pytest.mark.heavy     # 1.6 s here, over the one-second line for an unmarked call
async def test_a_wait_left_unanswered_fails_by_its_declared_default_after_the_deadline(tmp_path):
    repo = make_repo(tmp_path / "repo")
    eng = engine(tmp_path, repo, script(MEDIUM, actor=conflicting_actor()), wall=lambda: 1000.0)
    assert (await eng.run()).exit_code == 10
    eng2 = engine(tmp_path, repo, script(MEDIUM, actor=conflicting_actor()), wall=lambda: 1000.0 + 86401)
    v = await eng2.resume()
    assert v.verdict == "failed"
    assert [e for e in read(tmp_path / "run" / "record.jsonl") if e["event"] == "resume"][0]["default_applied"] == ["manual_merge"]
