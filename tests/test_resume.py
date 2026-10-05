"""Tickets #33 and #35: the wait node and resume (ADR-0107)."""
from pathlib import Path

import pytest

from squad.adapter import ScriptedAdapter
from squad.engine import Engine, ResumeRefused
from squad.graph import from_doc
from squad.record import read
from tests.flows import agent, doc
from tests.gitrepo import make_repo, sh


def wait_doc(default="pass", deadline=100):
    nodes = [agent("a", output_key="x", next={"fail": "exit:fail"}),
             {"name": "ask", "kind": "wait", "owner": "rafael", "deadline": deadline, "default": default},
             agent("b", output_key="y", next={"fail": "exit:fail"})]
    return doc(nodes, ["a", "ask", "b"])


OK = {"output": {"x": 1, "y": 1}, "spend": 0.1}


def make(d, tmp_path, script=None, **kw):
    ad = ScriptedAdapter(script or {"a": OK, "b": OK}, repeat_last=True)
    return Engine(from_doc(d), ad, tmp_path, **kw), ad


def events(tmp_path, kind=None):
    return [e for e in read(tmp_path / "record.jsonl") if kind is None or e["event"] == kind]


@pytest.mark.anyio
async def test_a_wait_ends_the_process_with_needs_human_and_exit_10(tmp_path):
    eng, ad = make(wait_doc(), tmp_path)
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("needs-human", 10)
    w = events(tmp_path, "wait")[0]
    assert (w["node"], w["owner"], w["default"]) == ("ask", "rafael", "pass")
    assert [c.node for c in ad.calls] == ["a"]


@pytest.mark.anyio
async def test_a_resume_with_an_answer_continues_under_the_same_run_id(tmp_path):
    eng, _ = make(wait_doc(), tmp_path)
    first = await eng.run()
    eng2, ad2 = make(wait_doc(), tmp_path)
    v = await eng2.resume(answer="pass")
    assert v.verdict == "completed"
    assert [c.node for c in ad2.calls] == ["b"]          # the recorded node `a` is not run again
    assert {e["run_id"] for e in events(tmp_path, "run_start") + events(tmp_path, "resume")} == {eng.run_id}
    assert events(tmp_path, "resume")[0]["frontier"] == ["ask"]
    assert first.verdict == "needs-human"


@pytest.mark.anyio
async def test_a_resume_answer_fail_fails_the_run(tmp_path):
    eng, _ = make(wait_doc(), tmp_path)
    await eng.run()
    eng2, ad2 = make(wait_doc(), tmp_path)
    v = await eng2.resume(answer="fail")
    assert v.verdict == "failed" and ad2.calls == []


@pytest.mark.anyio
async def test_a_resume_before_the_deadline_with_no_answer_halts_again(tmp_path):
    eng, _ = make(wait_doc(), tmp_path, wall=lambda: 1000.0)
    await eng.run()
    eng2, ad2 = make(wait_doc(), tmp_path, wall=lambda: 1050.0)
    v = await eng2.resume()
    assert v.exit_code == 10 and ad2.calls == []


@pytest.mark.anyio
async def test_a_resume_after_the_deadline_applies_the_declared_default(tmp_path):
    eng, _ = make(wait_doc(default="pass"), tmp_path, wall=lambda: 1000.0)
    await eng.run()
    eng2, ad2 = make(wait_doc(default="pass"), tmp_path, wall=lambda: 1101.0)
    v = await eng2.resume()
    assert v.verdict == "completed" and [c.node for c in ad2.calls] == ["b"]
    assert [e["reasons"] for e in events(tmp_path, "outcome") if e["node"] == "ask"] == [["default_applied"]]
    assert events(tmp_path, "resume")[0]["default_applied"] == ["ask"]


@pytest.mark.anyio
async def test_a_default_of_fail_fails_the_run_after_the_deadline(tmp_path):
    eng, _ = make(wait_doc(default="fail"), tmp_path, wall=lambda: 1000.0)
    await eng.run()
    eng2, _ = make(wait_doc(default="fail"), tmp_path, wall=lambda: 1101.0)
    assert (await eng2.resume()).verdict == "failed"


@pytest.mark.anyio
async def test_a_resume_refuses_a_changed_graph_and_writes_no_event(tmp_path):
    eng, _ = make(wait_doc(), tmp_path)
    await eng.run()
    n = len(events(tmp_path))
    other = wait_doc(deadline=200)
    eng2, _ = make(other, tmp_path)
    with pytest.raises(ResumeRefused, match="graph hash"):
        await eng2.resume(answer="pass")
    assert len(events(tmp_path)) == n


@pytest.mark.anyio
async def test_a_resume_refuses_a_run_whose_root_has_an_exit_event(tmp_path):
    eng, _ = make(wait_doc(), tmp_path)
    await eng.run()
    eng2, _ = make(wait_doc(), tmp_path)
    await eng2.resume(answer="pass")
    eng3, _ = make(wait_doc(), tmp_path)
    with pytest.raises(ResumeRefused, match="exit event"):
        await eng3.resume(answer="pass")


@pytest.mark.anyio
async def test_a_missing_artifact_fails_the_resume_at_preflight(tmp_path):
    eng, _ = make(wait_doc(), tmp_path)
    await eng.run()
    for p in (tmp_path / "artifacts").rglob("*"):
        if p.is_file():
            p.unlink()
    eng2, ad2 = make(wait_doc(), tmp_path)
    v = await eng2.resume(answer="pass")
    assert v.verdict == "failed" and "artifact_missing" in v.reasons[0] and ad2.calls == []


@pytest.mark.anyio
async def test_from_runs_the_named_node_and_what_follows_again(tmp_path):
    eng, _ = make(wait_doc(), tmp_path)
    await eng.run()
    eng2, _ = make(wait_doc(), tmp_path)
    await eng2.resume(answer="pass")
    # the root has an exit event, so --from on a finished run is refused
    eng3, _ = make(wait_doc(), tmp_path)
    with pytest.raises(ResumeRefused):
        await eng3.resume(from_node="a")


@pytest.mark.anyio
async def test_from_forces_a_live_run_from_that_node(tmp_path):
    eng, _ = make(wait_doc(), tmp_path)
    await eng.run()
    eng2, ad2 = make(wait_doc(), tmp_path)
    v = await eng2.resume(from_node="a", answer="pass")
    assert [c.node for c in ad2.calls] == ["a", "b"]
    assert v.verdict == "completed"
    entries = [e["entry"] for e in events(tmp_path, "outcome") if e["node"] == "a"]
    assert entries == [1, 2]


@pytest.mark.anyio
async def test_from_refuses_an_unknown_node(tmp_path):
    eng, _ = make(wait_doc(), tmp_path)
    await eng.run()
    eng2, _ = make(wait_doc(), tmp_path)
    with pytest.raises(ResumeRefused):
        await eng2.resume(from_node="nope")


# ---- workspace preflight (ADR-0107 rule 5)

GIT_FACT = {"name": "git", "type": "bool", "instrument": "code: git is available in the target", "source_kind": "target"}


def ws_doc():
    nodes = [{"name": "wt", "kind": "action", "effect": "worktree_create", "workspace": "worktrees/w", "deadline": 60},
             agent("a", output_key="x", workspace="worktrees/w", next={"fail": "exit:fail"}),
             {"name": "ask", "kind": "wait", "owner": "rafael", "deadline": 100, "default": "pass"},
             agent("b", output_key="y", workspace="worktrees/w", next={"fail": "exit:fail"})]
    return doc(nodes, ["wt", "a", "ask", "b"], facts=[GIT_FACT])


async def halted_workspace(tmp_path):
    repo = make_repo(tmp_path / "repo")
    eng, _ = make(ws_doc(), tmp_path / "run", target=repo)
    await eng.run()
    return repo, next(Path(e["workspace"]["path"]) for e in events(tmp_path / "run", "outcome") if e["node"] == "a")


@pytest.mark.anyio
async def test_a_resume_with_an_untouched_workspace_continues(tmp_path):
    repo, _ = await halted_workspace(tmp_path)
    eng2, ad2 = make(ws_doc(), tmp_path / "run", target=repo)
    assert (await eng2.resume(answer="pass")).verdict == "completed"


@pytest.mark.anyio
async def test_a_resume_with_a_changed_workspace_is_a_conflict_and_runs_nothing(tmp_path):
    repo, ws = await halted_workspace(tmp_path)
    (ws / "base.txt").write_text("edited by hand\n")
    eng2, ad2 = make(ws_doc(), tmp_path / "run", target=repo)
    v = await eng2.resume(answer="pass")
    assert (v.verdict, v.exit_code) == ("conflict", 20) and ad2.calls == []
    assert events(tmp_path / "run", "resume")[-1]["preflight"]["reason"] == "workspace_changed:worktrees/w"


@pytest.mark.anyio
async def test_a_resume_with_a_moved_head_is_a_conflict(tmp_path):
    repo, ws = await halted_workspace(tmp_path)
    (ws / "n.txt").write_text("x\n")
    sh(ws, "git", "add", "-A")
    sh(ws, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "moved")
    eng2, _ = make(ws_doc(), tmp_path / "run", target=repo)
    v = await eng2.resume(answer="pass")
    assert v.verdict == "conflict"
    assert events(tmp_path / "run", "resume")[-1]["preflight"]["reason"] == "workspace_head_moved:worktrees/w"


@pytest.mark.anyio
async def test_a_resume_with_a_missing_worktree_fails(tmp_path):
    import shutil
    repo, ws = await halted_workspace(tmp_path)
    shutil.rmtree(ws)
    eng2, _ = make(ws_doc(), tmp_path / "run", target=repo)
    v = await eng2.resume(answer="pass")
    assert v.verdict == "failed" and "worktree_missing" in v.reasons[0]
