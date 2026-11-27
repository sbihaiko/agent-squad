"""Ticket #42: the authored flow and the dynamic flow run on one engine with the same guards."""
import re
from pathlib import Path

import pytest

from squad import record, verdict
from squad.engine import Engine
from tests.gitrepo import make_repo
from tests.test_dispatch import CHILD, crash_after_first_child, ev, make, req, routed, two_children
from tests.test_five_phases import MEDIUM
from tests.test_five_phases import engine as five_engine
from tests.test_five_phases import script as five_script

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.heavy  # two git child runs; CI load pushed it to 3.1 s
@pytest.mark.anyio
async def test_the_router_picks_a_different_skill_child_for_two_inputs(tmp_path):
    a = make(tmp_path / "a", {"router": routed(req("tdd")), "route.router.1": CHILD})
    b = make(tmp_path / "b", {"router": routed(req("diagnosing-bugs", tools=["Read"])), "route.router.1": CHILD})
    va, vb = await a.run(), await b.run()
    assert va.verdict == vb.verdict == "completed"
    def skill(d):
        return ev(d, "spawn_accepted")[0]["definition"]["skill"]["name"]
    assert (skill(tmp_path / "a"), skill(tmp_path / "b")) == ("tdd", "diagnosing-bugs")
    prompts = {c.prompt.split()[0] for eng in (a, b) for c in eng.adapter.calls if c.node == "route.router.1"}
    assert prompts == {"/tdd", "/diagnosing-bugs"}


@pytest.mark.anyio
@pytest.mark.heavy
async def test_an_over_limit_spawn_is_rejected_and_a_crash_resumes_from_the_frontier(tmp_path):
    await two_children(tmp_path / "a")
    over = make(tmp_path / "over", {"router": routed(req(model="gpt-x"))})
    await over.run()
    # a request that names a model is rejected whole (ADR-0113 rule 3, SPEC 750): the envelope holds no model list to be
    # outside of, and the child's model comes from the chain of its kind
    assert [e["reason"] for e in ev(tmp_path / "over", "spawn_rejected")] == ["model_supplied"]
    crash_after_first_child(tmp_path / "a", tmp_path / "b")
    eng = make(tmp_path / "b", {"route.router.2": CHILD})
    v = await eng.resume()
    assert v.verdict == "completed" and ev(tmp_path / "b", "resume")[0]["frontier"] == ["route.router.2"]


@pytest.mark.anyio
@pytest.mark.heavy
async def test_both_flows_use_one_engine_one_verdict_module_and_the_same_event_kinds(tmp_path):
    repo = make_repo(tmp_path / "repo")
    authored = five_engine(tmp_path, repo, five_script(MEDIUM))
    dynamic = make(tmp_path / "dyn", {"router": routed(req()), "route.router.1": CHILD})
    va, vd = await authored.run(), await dynamic.run()
    assert type(authored) is type(dynamic) is Engine
    assert va.verdict == vd.verdict == "completed" and isinstance(va, type(vd)) and isinstance(va, verdict.Verdict)
    ka = {e["event"] for e in record.read(tmp_path / "run" / "record.jsonl")}
    kd = {e["event"] for e in record.read(tmp_path / "dyn" / "record.jsonl")}
    assert {"run_start", "outcome", "verdict"} <= ka & kd
    assert (ka | kd) <= set(record.REQUIRED)
    for d in (tmp_path / "run", tmp_path / "dyn"):
        last = record.read(d / "record.jsonl")[-1]
        assert last["event"] == "verdict" and last["exit_code"] == 0


def test_the_engine_holds_no_code_for_one_flow():
    src = (ROOT / "squad" / "engine.py").read_text()
    assert not re.search(r"five-phases|router\.graph|\"scout\"|\"spec_draft\"|\"router\"", src)
