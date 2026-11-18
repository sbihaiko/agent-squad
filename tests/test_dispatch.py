"""Tickets #38 to #40: the dispatch node."""
import json
import shlex
import shutil
import subprocess
from pathlib import Path

import anyio
import pytest

from squad import skills
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.record import read
from tests.gitrepo import make_repo, sh, sibling_repo
from tests.helpers import DESIGN, node, router

# The four chains a run takes its models from (SPEC 750). The coding chain keeps the order the dispatch envelope used
# to name, so a fallback test reads the same sequence it read before; the router answers on its own chain.
CHAINS = {
    "router": ["claude-opus-5-5", "claude-sonnet-5-5"],
    "coding": ["claude-grok-4.7", "claude-deepseek-flash[1m]", "claude-opus-5-5", "claude-gpt-5.6-terra",
               "claude-sonnet-5-5", "claude-sonnet-4-6", "claude-haiku-5-5"],
    "review": ["claude-opus-5-5"],
    "vision": ["claude-sonnet-5-5"],
}
MODEL = CHAINS["coding"][0]


def req(skill="tdd", **kw):
    return {"prompt": f"Run {skill} on the task.", "skill": skill, "kind": "coding", "effort": "medium", "tools": ["Read", "Edit"],
            "stalls": 4, "spend": 1.0, "deadline": 100, "workspace": "worktrees/child", **kw}


def routed(*spawns, **out):
    return {"output": {"spawns": list(spawns), **out}, "spend": 0.1}


CHILD = {"output": {"diff": {"files": [{"path": "a.py"}]}, "ac_commands": ["true"]}, "spend": 0.2, "turns": 3}


def make(tmp_path, script, tweak=None, **kw):
    d = router()
    d["run"]["deadline"] = 7200
    if tweak:
        tweak(d)
    kw.setdefault("target", sibling_repo(tmp_path))
    kw.setdefault("kind_chains", CHAINS)
    eng = Engine(from_doc(d), ScriptedAdapter(script, **kw.pop("adapter", {})), tmp_path, **kw)
    return eng


def ev(tmp_path, kind):
    return [e for e in read(tmp_path / "record.jsonl") if e["event"] == kind]


def child_outs(tmp_path):
    return {e["node"]: e for e in ev(tmp_path, "outcome") if e["node"].startswith("route.")}


@pytest.mark.anyio
async def test_a_child_falls_back_only_within_the_router_overlay(tmp_path):
    """SPEC 505: a child's chain reads the narrowed envelope, never a model the router overlay closed."""
    keep = "claude-sonnet-5-5"
    closed = [m for m in CHAINS["coding"] if m != keep]
    assert closed
    err = {"error": "API Error: 400 the model is not supported", "served": False}
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": [err] * 8},
               router_overlay={"route": [keep]})
    await eng.run()
    calls = [c.model for c in eng.adapter.calls if c.node == "route.router.1"]
    assert calls and set(calls) == {keep}
    refused = [e["model"] for e in ev(tmp_path, "model_refused") if e["node"] == "route.router.1"]
    assert refused == [keep] and not set(refused) & set(closed)


@pytest.mark.anyio
async def test_a_refused_chain_entry_is_recorded_and_its_grant_returned(tmp_path):
    """#494: the router's first model is refused before it serves a message; the next entry answers, the record names
    the refusal, and the run still funds the child (a kept grant of the router's max starved it before)."""
    eng = make(tmp_path, {"router": [{"error": "API Error: 400 The model is not supported", "served": False}, routed(req())],
                          "route.router.1": CHILD})
    v = await eng.run()
    assert v.verdict == "completed"
    refused = ev(tmp_path, "model_refused")
    assert len(refused) == 1 and refused[0]["node"] == "router" and refused[0]["model"] == node(router(), "router")["model"]
    assert "400" in refused[0]["error"]
    assert eng.budget.unmeasured == pytest.approx(0.0)
    assert [c.node for c in eng.adapter.calls] == ["router", "router", "route.router.1"]


@pytest.mark.anyio
async def test_one_child_runs_as_its_skill_and_writes_the_three_events_and_the_artifact(tmp_path):
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": CHILD})
    v = await eng.run()
    assert v.verdict == "completed"
    r, a = ev(tmp_path, "spawn_requested")[0], ev(tmp_path, "spawn_accepted")[0]
    assert a["request_key"] == r["pos"] and a["child"] == "route.router.1" and a["parent"] == "router" and a["depth"] == 1
    assert a["definition"]["skill"] == {"name": "tdd", "version": skills.load()["tdd"]["hash"]} and a["definition"]["stalls"] == 4 and a["reservation"] == 1.0
    call = [c for c in eng.adapter.calls if c.node == "route.router.1"][0]
    assert call.prompt == "/tdd Run tdd on the task." and call.skill == "tdd" and call.max_spend == 1.0
    out = child_outs(tmp_path)["route.router.1"]
    assert out["checks"] == {"scope_gate": "pass", "security_gate": "pass"} and out["parent"] == "router"
    disp = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"][0]
    assert disp["outcome"] == "pass"
    assert (tmp_path / disp["artifact"]).exists()
    assert disp["elapsed"] is not None and disp["elapsed"] >= 0   # the dispatch node's own wall time, not only its children's


CLEAN_END = {"subtype": "success", "is_error": False, "last": "I read the spec and stopped before the file was written"}


@pytest.mark.anyio
async def test_a_child_that_ends_early_names_its_end_on_the_record(tmp_path):    # #1066
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": {"output": None, "spend": 0.2, "turns": 120, "ended": CLEAN_END}})
    v = await eng.run()
    out = child_outs(tmp_path)["route.router.1"]
    assert out["outcome"] == "fail" and out["reasons"] == ["child_ended_early"]
    assert out["diagnostic"]["ended"]["last"] == CLEAN_END["last"] and out["turns"] == 120
    assert "child_ended_early" in v.reasons and "artifact_missing" not in v.reasons


@pytest.mark.anyio
async def test_a_child_that_spends_its_whole_grant_and_ends_clean_is_artifact_missing(tmp_path):    # #1066
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": {"output": None, "spend": 1.0, "turns": 120, "ended": CLEAN_END}})
    v = await eng.run()
    assert child_outs(tmp_path)["route.router.1"]["reasons"] == ["artifact_missing"]
    assert "artifact_missing" in v.reasons


@pytest.mark.anyio
async def test_a_child_that_errors_out_names_the_error_on_the_record(tmp_path):    # #1066
    failed = {"subtype": "error_during_execution", "is_error": True, "last": "the tool call crashed"}
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": {"output": None, "spend": 0.2, "ended": failed}})
    await eng.run()
    assert child_outs(tmp_path)["route.router.1"]["reasons"] == ["session_error:error_during_execution"]


@pytest.mark.anyio
async def test_a_router_that_ends_clean_with_no_spawns_list_gets_one_repair_try(tmp_path):    # #2336
    """A dispatch entry that ends clean with budget left and writes no artifact answers once more, and its prompt carries
    the reason: the answer was outside the shape. A sound answer on the repair turn ends the run `completed`."""
    eng = make(tmp_path, {"router": [{"output": None, "spend": 0.1, "ended": CLEAN_END}, routed(req())],
                          "route.router.1": CHILD})
    v = await eng.run()
    assert v.verdict == "completed"
    calls = [c for c in eng.adapter.calls if c.node == "router"]
    assert len(calls) == 2 and calls[1].session_id != calls[0].session_id
    assert "outside the shape" in calls[1].prompt and "spawns" in calls[1].prompt
    repair = [e for e in ev(tmp_path, "answer_repair") if e["node"] == "router"]
    assert len(repair) == 1 and repair[0]["shape"] == "none"


@pytest.mark.anyio
async def test_a_second_answer_with_no_spawns_list_fails_the_run_and_names_the_shape(tmp_path):    # #2336
    """One repair try only. A second clean end with no artifact ends the run `failed`, and the reason names the shape
    it read (`none`) so the record holds why no child ran."""
    eng = make(tmp_path, {"router": [{"output": None, "spend": 0.1, "ended": CLEAN_END},
                                     {"output": None, "spend": 0.1, "ended": CLEAN_END}]})
    v = await eng.run()
    assert v.verdict == "failed"
    assert len([c for c in eng.adapter.calls if c.node == "router"]) == 2
    out = [e for e in ev(tmp_path, "outcome") if e["node"] == "router"][0]
    assert out["outcome"] == "fail"
    assert out["reasons"] == ["child_ended_early", "spawns_shape:none"]
    assert "spawns_shape:none" in v.reasons


@pytest.mark.anyio
async def test_a_spawns_value_outside_the_list_is_repaired_once(tmp_path):    # #2336
    """The list shape is the contract, so a first answer whose `spawns` value is not a list is also outside the shape:
    it gets the repair turn, and a sound answer there ends the run."""
    eng = make(tmp_path, {"router": [{"output": {"spawns": "tdd"}, "spend": 0.1}, routed(req())],
                          "route.router.1": CHILD})
    v = await eng.run()
    assert v.verdict == "completed"
    repair = [e for e in ev(tmp_path, "answer_repair") if e["node"] == "router"]
    assert len(repair) == 1 and repair[0]["shape"] == "string"


@pytest.mark.anyio
async def test_a_second_spawns_value_outside_the_list_fails_and_names_its_shape(tmp_path):    # #2336
    eng = make(tmp_path, {"router": [{"output": {"spawns": {"prompt": "tdd"}}, "spend": 0.1},
                                     {"output": {"spawns": {"prompt": "tdd"}}, "spend": 0.1}]})
    v = await eng.run()
    assert v.verdict == "failed"
    out = [e for e in ev(tmp_path, "outcome") if e["node"] == "router"][0]
    assert out["reasons"] == ["spawns_shape:object"]
    assert "spawns_shape:object" in v.reasons


@pytest.mark.anyio
async def test_a_valid_empty_spawns_list_is_still_no_dispatch_and_no_repair(tmp_path):    # #2336, ADR-0113 rule 18
    """A well-formed empty list is the entry's own answer, not a shape fault: zero children without `done` fails
    `no_dispatch` and gets no repair turn."""
    eng = make(tmp_path, {"router": routed()})
    v = await eng.run()
    assert v.verdict == "failed"
    assert len([c for c in eng.adapter.calls if c.node == "router"]) == 1
    assert not [e for e in ev(tmp_path, "answer_repair") if e["node"] == "router"]
    assert "no_dispatch" in v.reasons


@pytest.mark.anyio
async def test_a_final_gate_that_could_not_run_completes_the_run_and_names_the_node(tmp_path):    # #1825
    """The child listed its own gate in ac_commands and that gate answered exit 4 with no pytest summary: nothing is
    red and nothing was proved, so the run is completed with `unproven:final_gate`, never failed and left to a person
    (ADR-0164)."""
    line = ("fast-gate: inconclusive: the machine's gate budget is full, so no battery ran and nothing is red; "
            "unproven (exit 4): commit and report done, the entry gate and the landing own the proof (ADR-0164)")
    child = {"output": {"diff": {"files": [{"path": "a.py"}]}, "ac_commands": [f"echo {shlex.quote(line)}; exit 4"]}, "spend": 0.2, "turns": 3}
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": child})
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("completed", 0), v.reasons
    assert v.reasons == ["unproven:final_gate"]


@pytest.mark.anyio
async def test_a_child_that_could_not_measure_is_no_failing_child(tmp_path):    # #1825
    """A child that answers `unproven` proves nothing false: the run completes and names the child's proof as
    unproven, and no node fails for it (ADR-0164)."""
    gate = ("fast-gate: inconclusive: the machine's gate budget is full, so no battery ran and nothing is red; "
            "unproven (exit 4): commit and report done, the entry gate and the landing own the proof (ADR-0164)")
    child = {"output": {"diff": {"files": [{"path": "a.py"}]}, "status": "unproven", "reasons": [gate]}, "spend": 0.2, "turns": 3}
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": child})
    v = await eng.run()
    assert (v.verdict, v.exit_code) == ("completed", 0), v.reasons
    assert "unproven:route.router.1" in v.reasons
    assert child_outs(tmp_path)["route.router.1"]["outcome"] == "unproven"


@pytest.mark.anyio
async def test_a_landing_command_the_gate_could_not_run_records_its_obligation(tmp_path):    # #1825
    """A held-out command that ends on the gate's own unproven line measured nothing, so it is no regression of the landing
    (ADR-0164). The obligation it leaves rides the landing result, never dropped, and a measured red never does."""
    from types import SimpleNamespace

    from squad.runners.dispatch import Dispatch
    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    line = ("fast-gate: inconclusive: the machine's gate budget is full, so no battery ran and nothing is red; "
            "unproven (exit 4): commit and report done, the entry gate and the landing own the proof (ADR-0164)")
    cmd = f"echo {shlex.quote(line)}; exit 4"
    d = Dispatch(SimpleNamespace(target=str(target), fix_run=False))
    d.landed_commands = lambda extra=None: [("kid", [cmd])]
    d.child_tree = lambda name: None
    d.ac_before = {}
    got, evidence = await d.ac_survey()
    assert got["kid"] is True and evidence["kid"] == {f"unproven:{cmd}": line}
    r = await d.landing_check("kid", None)
    assert r.ok and r.extra["unproven"] == [cmd]


@pytest.mark.anyio
async def test_a_child_that_writes_its_artifact_carries_no_artifact_missing(tmp_path):    # #1066
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": CHILD})
    v = await eng.run()
    assert child_outs(tmp_path)["route.router.1"]["reasons"] == []
    assert "artifact_missing" not in v.reasons


@pytest.mark.anyio
async def test_code_assigns_the_request_key_and_child_name_and_a_model_id_is_refused(tmp_path):
    eng = make(tmp_path, {"router": routed(req(id="child-99"), req(key=5, name="mine"), req("diagnosing-bugs")), "route.router.1": CHILD})
    await eng.run()
    rej = ev(tmp_path, "spawn_rejected")
    assert [r["reason"] for r in rej] == ["model_supplied_identity:id", "model_supplied_identity:key"]
    assert [e["child"] for e in ev(tmp_path, "spawn_accepted")] == ["route.router.1"]
    keys = {e["request_key"] for e in ev(tmp_path, "spawn_accepted") + rej}
    assert keys == {e["pos"] for e in ev(tmp_path, "spawn_requested")}


BAD = [
    (dict(model="gpt"), "model_supplied"),
    (dict(tools=["Read", "WebFetch"]), "tool_outside_allow:WebFetch"), (dict(skill="evil"), "skill_outside_allow"),
    (dict(stalls=999), "stalls_outside_budget"), (dict(stalls=1), "stalls_outside_budget"), (dict(spend=50.0), "spend_outside_budget"),
    (dict(deadline=99999), "deadline_over_dispatch"), (dict(workspace="elsewhere"), "workspace_outside_allow:requested 'elsewhere', allowed ['worktrees/child']"),
]


@pytest.mark.parametrize("patch,reason", BAD)
@pytest.mark.anyio
async def test_a_request_over_any_limit_is_rejected_whole_with_one_event(tmp_path, patch, reason):
    eng = make(tmp_path, {"router": routed(req(**patch))})
    v = await eng.run()
    assert [e["reason"] for e in ev(tmp_path, "spawn_rejected")] == [reason]
    assert not ev(tmp_path, "spawn_accepted") and v.verdict == "failed"
    assert [e["node"] for e in ev(tmp_path, "outcome") if e["node"] == "router"] == ["router"]   # a rejection changes no node outcome


@pytest.mark.anyio
async def test_a_request_naming_an_effort_over_the_allow_is_accepted_at_the_allowed_level(tmp_path):
    """#960: the router named `max` under an envelope of low and medium and two runs died `no_dispatch`. The request is
    accepted, and the child runs at the envelope's highest allowed depth."""
    eng = make(tmp_path, {"router": routed(req(effort="max")), "route.router.1": CHILD})
    await eng.run()
    acc = ev(tmp_path, "spawn_accepted")
    assert [e["definition"]["effort"] for e in acc] == ["medium"] and not ev(tmp_path, "spawn_rejected")


@pytest.mark.anyio
async def test_a_child_that_names_no_effort_runs_at_its_chain_entrys_effort(tmp_path):
    """The panel draws one effort per row (SPEC 750): a request that names no effort runs at its own chain entry's
    effort, never at the engine's default, so a chain row the owner set to low runs the child at low."""
    r = req()
    r.pop("effort")
    eng = make(tmp_path, {"router": routed(r), "route.router.1": CHILD}, kind_efforts={"coding": {MODEL: "low"}})
    await eng.run()
    assert [e["definition"]["effort"] for e in ev(tmp_path, "spawn_accepted")] == ["low"]


@pytest.mark.anyio
async def test_a_named_effort_still_wins_over_the_chain_entrys(tmp_path):
    """The chain's effort is a default, never a cap: a request that names one keeps it."""
    eng = make(tmp_path, {"router": routed(req(effort="medium")), "route.router.1": CHILD},
               kind_efforts={"coding": {MODEL: "low"}})
    await eng.run()
    assert [e["definition"]["effort"] for e in ev(tmp_path, "spawn_accepted")] == ["medium"]


@pytest.mark.anyio
async def test_zero_accepted_children_fold_to_fail_no_dispatch(tmp_path):
    eng = make(tmp_path, {"router": routed()})
    v = await eng.run()
    d = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"][0]
    assert d["outcome"] == "fail" and d["reasons"] == ["no_dispatch"] and v.verdict == "failed"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_spend_is_reserved_at_acceptance_before_any_launch(tmp_path):
    seen = {}
    eng = make(tmp_path, {})

    def child(r):
        seen["reserved"] = eng.budget.reserved
        return CHILD
    eng.adapter.script = {"router": [routed(req(spend=1.0), req("diagnosing-bugs", spend=2.0))], "route.router.1": [child], "route.router.2": [CHILD]}
    await eng.run()
    assert seen["reserved"] == pytest.approx(3.0)       # both grants held while the first child runs
    assert eng.budget.reserved == pytest.approx(0) and eng.budget.run_spend == pytest.approx(0.1 + 0.2 + 0.2)


@pytest.mark.anyio
async def test_a_request_the_run_cannot_fund_is_rejected(tmp_path):
    def cap(d):
        d["run"]["spend_cap"] = 3.0
    eng = make(tmp_path, {"router": routed(req(spend=1.5), req("diagnosing-bugs", spend=1.5)), "route.router.1": CHILD}, tweak=cap)
    await eng.run()
    assert [e["reason"] for e in ev(tmp_path, "spawn_rejected")] == ["spend_over_remainder"]


@pytest.mark.anyio
@pytest.mark.heavy   # two child worktrees in a real repository: 1.1 s here and a loaded runner pushes it past the line (#1721)
async def test_the_checks_gates_run_after_every_child_and_a_failed_check_fails_the_child(tmp_path):
    bad = {"output": {"diff": {"files": [{"path": "src/auth/login.py"}]}, "ac_commands": ["true"]}, "spend": 0.2}
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": bad, "route.router.2": CHILD})
    v = await eng.run()
    c = child_outs(tmp_path)
    assert c["route.router.1"]["outcome"] == "fail" and c["route.router.1"]["checks"]["security_gate"] == "fail"
    assert any(r.startswith("check:security_gate") for r in c["route.router.1"]["reasons"])
    assert c["route.router.2"]["outcome"] == "pass" and v.verdict == "failed"


@pytest.mark.anyio
@pytest.mark.heavy   # two child worktrees in a real repository, one per engine: 1.9 s here (#1721)
async def test_the_dispatch_artifact_feeds_the_final_gate_with_the_child_commands(tmp_path):
    ok = {"output": {"ac_commands": ["true"]}, "spend": 0.1}
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": ok})
    assert (await eng.run()).verdict == "completed"
    failing = {"output": {"ac_commands": ["false"]}, "spend": 0.1}
    eng2 = make(tmp_path / "b", {"router": routed(req()), "route.router.1": failing})
    v = await eng2.run()
    assert v.verdict == "failed" and any("ac_failed:false" in r for r in [e for e in read(tmp_path / "b" / "record.jsonl") if e["event"] == "outcome" and e["node"] == "final_gate"][0]["reasons"])


# ---- #39: nested and parallel children
@pytest.mark.anyio
async def test_a_child_may_spawn_a_child_and_depth_and_fan_out_are_enforced(tmp_path):
    grand = req("diagnosing-bugs", tools=["Read"], spend=0.5, deadline=50)
    child1 = {"output": {"ac_commands": ["true"], "spawns": [grand]}, "spend": 0.2}
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": child1, "route.router.2": {"output": {"spawns": [req("codebase-design", tools=["Read"], spend=0.2, deadline=30)]}, "spend": 0.1},
                          "route.router.3": CHILD})
    await eng.run()
    acc = {e["child"]: e for e in ev(tmp_path, "spawn_accepted")}
    assert acc["route.router.2"]["parent"] == "route.router.1" and acc["route.router.2"]["depth"] == 2
    rej = ev(tmp_path, "spawn_rejected")
    assert [r["reason"] for r in rej] == ["depth_over_max"] and "route.router.3" not in acc


@pytest.mark.anyio
async def test_a_child_gets_the_intersection_of_its_parents_tools(tmp_path):
    child1 = {"output": {"spawns": [req("diagnosing-bugs", tools=["Read", "Bash"], deadline=50)]}, "spend": 0.2}
    eng = make(tmp_path, {"router": routed(req(tools=["Read", "Edit"])), "route.router.1": child1})
    await eng.run()
    assert [r["reason"] for r in ev(tmp_path, "spawn_rejected")] == ["tool_outside_parent:Bash"]


@pytest.mark.anyio
async def test_a_grandchild_gets_no_more_than_its_parents_remaining_spend(tmp_path):
    child1 = {"output": {"spawns": [req("diagnosing-bugs", tools=["Read"], spend=1.0, deadline=50)]}, "spend": 0.6}     # the grant was 1.0: 0.4 is left
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": child1})
    await eng.run()
    assert [r["reason"] for r in ev(tmp_path, "spawn_rejected")] == ["spend_over_remainder"]


@pytest.mark.anyio
async def test_a_child_that_names_no_spend_gets_the_projects_ceiling_never_its_envelope_max(tmp_path):
    """#960: the project set max_budget_usd 4 and the envelope allowed 12; the router named no spend, and the child it
    spawned took 12 while the router itself was held at 4. A grant left to the envelope is the smallest of that max and
    the project's per-call ceiling."""
    r = req(); r.pop("spend")    # the router names no spend, as it did in the run that died
    def tweak(d):            # the envelope's own max, above the project ceiling the test sets
        node(d, "route")["budget"]["spend"]["max"] = 12
    eng = make(tmp_path, {"router": routed(r), "route.router.1": {"output": {}, "spend": 0.1}}, tweak=tweak,
               project={"limits": {"max_budget_usd": 4}})
    await eng.run()
    assert [e["reservation"] for e in ev(tmp_path, "spawn_accepted")] == [4.0]


@pytest.mark.anyio
async def test_a_child_that_asks_above_the_projects_ceiling_is_refused_by_name(tmp_path):
    """A request that names a spend over the project's ceiling is refused by that fact, not by the run's remainder."""
    child1 = {"output": {"spawns": [req("diagnosing-bugs", tools=["Read"], spend=8.0, deadline=50)]}, "spend": 0.1}
    def tweak(d):            # the envelope's own max, above the project ceiling the test sets
        node(d, "route")["budget"]["spend"]["max"] = 12
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": child1}, tweak=tweak,
               project={"limits": {"max_budget_usd": 4}})
    await eng.run()
    assert [r["reason"] for r in ev(tmp_path, "spawn_rejected")] == ["spend_over_ceiling"]


@pytest.mark.anyio
@pytest.mark.heavy
async def test_max_children_per_entry_is_enforced(tmp_path):
    reqs = [req(f) for f in ("tdd", "diagnosing-bugs", "codebase-design", "tdd", "tdd")]
    eng = make(tmp_path, {"router": routed(*reqs), "route.router.1": CHILD}, adapter={"repeat_last": True, "default": CHILD})
    await eng.run()
    assert len(ev(tmp_path, "spawn_accepted")) == 4
    assert [r["reason"] for r in ev(tmp_path, "spawn_rejected")] == ["max_children_over"]


@pytest.mark.anyio
@pytest.mark.heavy
async def test_one_request_for_three_children_records_three_accepted_children_and_no_rejection(tmp_path):   # #33 acceptance 1: one run carries the issues
    reqs = [req(f) for f in ("tdd", "diagnosing-bugs", "codebase-design")]
    eng = make(tmp_path, {"router": routed(*reqs), "route.router.1": CHILD}, adapter={"repeat_last": True, "default": CHILD})
    await eng.run()
    assert [e["child"] for e in ev(tmp_path, "spawn_accepted")] == ["route.router.1", "route.router.2", "route.router.3"]
    assert ev(tmp_path, "spawn_rejected") == []


@pytest.mark.anyio
@pytest.mark.heavy
async def test_children_run_in_parallel_up_to_max_parallel(tmp_path):
    slow = dict(CHILD, delay=0.6)
    ro = {"tools": ["Read"]}    # read-only children get no worktree, so the record shows the fan-out alone
    eng = make(tmp_path, {"router": routed(req(**ro), req("diagnosing-bugs", **ro), req("codebase-design", **ro)), "route.router.1": slow, "route.router.2": slow, "route.router.3": slow})
    v = await eng.run()
    # The fan-out is proved by the record's order, never by the wall clock: a loaded runner stretches the same three
    # 0.6 s children past any total-time bound while the run keeps the cap (ADR-0018). A child is in flight from its
    # first call to its outcome; the peak of that count is the concurrency the cap allowed.
    first = {}
    for e in ev(tmp_path, "call_start"):
        if e["node"].startswith("route.") and e["node"] != "route":
            first.setdefault(e["node"], e["pos"])
    ends = {n: e["pos"] for n, e in child_outs(tmp_path).items()}
    assert len(first) == 3 and set(first) <= set(ends), f"three children ran: {sorted(first)}"
    marks = sorted([(pos, 1) for n, pos in first.items() if n in ends] + [(pos, -1) for pos in ends.values()])
    peak = inflight = 0
    for _, delta in marks:
        inflight += delta
        peak = max(peak, inflight)
    assert v.verdict == "completed"
    assert peak == 2, f"two children run at once: the record's peak was {peak}"


@pytest.mark.anyio
async def test_the_dispatch_folds_under_its_join_rule(tmp_path):
    def first_failure(d):
        node(d, "route")["join"] = "first_failure"
    failing = {"output": {"status": "fail", "reasons": ["bad"]}, "spend": 0.1}
    slow = dict(CHILD, delay=5)
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": failing, "route.router.2": slow}, tweak=first_failure)
    with anyio.fail_after(3):
        v = await eng.run()
    c = child_outs(tmp_path)
    assert c["route.router.1"]["outcome"] == "fail" and c["route.router.2"]["reasons"] == ["cancelled"]
    assert v.verdict == "failed"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_deadline_child_folds_the_dispatch_as_deadline_not_a_new_exit_code(tmp_path):
    def short(d):
        node(d, "route")["deadline"] = 10
    slow = dict(CHILD, delay=100)   # a 1 s scaled deadline: 0.2 s let a loaded host time out the worktree setup before the child began
    eng = make(tmp_path, {"router": routed(req(deadline=10)), "route.router.1": slow}, tweak=short, time_scale=0.1)
    v = await eng.run()
    assert child_outs(tmp_path)["route.router.1"]["outcome"] == "deadline" and v.exit_code == 1


@pytest.mark.anyio
async def test_a_grandchild_deadline_is_bounded_by_its_parents_remaining_time(tmp_path):
    child1 = {"output": {"spawns": [req("diagnosing-bugs", tools=["Read"], spend=0.5, deadline=100)]}, "spend": 0.2}
    eng = make(tmp_path, {"router": routed(req(deadline=60)), "route.router.1": child1})
    await eng.run()
    assert [r["reason"] for r in ev(tmp_path, "spawn_rejected")] == ["deadline_over_parent"]


# ---- #40: the frontier and the resume


def crash_after_first_child(src, dst):
    """Copy a finished two-child run and cut the record after the first child's exit event: the second child has `spawn_accepted` and no entry."""
    shutil.copytree(src, dst)
    events = read(dst / "record.jsonl")
    cut = next(e["pos"] for e in events if e["event"] == "outcome" and e["node"] == "route.router.1")
    ran = {e["node"] for e in events[:cut] if e["event"] == "outcome"}
    kept = [e for e in events[:cut] if not (e["event"] == "call_start" and e["node"] not in ran)]   # the second child never launched
    kept = [{**e, "pos": i} for i, e in enumerate(kept, 1)]
    (dst / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in kept))
    for d in (dst / "ws").glob("*-route.*"):      # a child that never launched has no worktree yet
        if d.name.split("-", 1)[1] not in ran:
            shutil.rmtree(d)
    copy_target(src, dst, ran)
    return kept


def copy_target(src, dst, ran):
    """Copy the first run's target repository for the copied run: a landing writes to it, so the copy needs its own coherent branches and worktrees."""
    ta, tb = Path(str(src) + "-target"), Path(str(dst) + "-target")
    if not ta.exists() or tb.exists():
        return
    shutil.copytree(ta, tb)
    entries, cur = [], {}
    for line in sh(tb, "git", "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if cur:
                entries.append(cur)
            cur = {}
        else:
            k, _, v = line.partition(" ")
            cur[k] = v
    for e in entries:
        name = Path(e["worktree"]).name
        if "-route." not in name:
            continue
        child = name.split("-", 1)[1]
        if child in ran:
            sh(tb, "git", "worktree", "repair", str(dst / "ws" / name))
        else:
            shutil.rmtree(tb / ".git" / "worktrees" / name, ignore_errors=True)
            sh(tb, "git", "branch", "-D", e["branch"].removeprefix("refs/heads/"))


async def two_children(tmp_path, **kw):
    slow = dict(CHILD, delay=0.3)
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": CHILD, "route.router.2": slow}, **kw)
    eng.dispatch.prune = lambda: None      # these fixtures cut a finished run into a crashed one: they need the worktrees that a completed run prunes
    await eng.run()
    return eng


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_resume_runs_only_the_frontier_and_never_a_passed_sibling(tmp_path):
    await two_children(tmp_path / "a")
    kept = crash_after_first_child(tmp_path / "a", tmp_path / "b")
    assert not any(e["event"] == "outcome" and e["node"] == "route.router.2" for e in kept)
    eng = make(tmp_path / "b", {"route.router.2": CHILD})
    v = await eng.resume()
    assert v.verdict == "completed" and [c.node for c in eng.adapter.calls] == ["route.router.2"]
    r = ev(tmp_path / "b", "resume")
    assert len(r) == 1 and r[0]["frontier"] == ["route.router.2"]
    assert [e["node"] for e in ev(tmp_path / "b", "outcome")].count("route.router.1") == 1


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_child_with_accept_and_no_entry_is_funded_again_and_unknown_spend_is_not_zero(tmp_path):
    await two_children(tmp_path / "a")
    crash_after_first_child(tmp_path / "a", tmp_path / "b")

    # measured 0.3 so far; a lowered cap of 1.0 no longer fits the child's grant of 1.0
    eng = make(tmp_path / "b", {"route.router.2": CHILD}, options={"spend_cap": 1.0})
    v = await eng.resume()
    out = child_outs(tmp_path / "b")["route.router.2"]
    assert out["outcome"] == "fail" and out["reasons"] == ["run_spend_cap"] and eng.adapter.calls == []
    assert out["spend"] is None and v.verdict == "failed"
    assert eng.budget.run_spend == pytest.approx(0.3)      # measured spend only


@pytest.mark.anyio
@pytest.mark.heavy
async def test_an_unmeasured_child_keeps_its_grant_out_of_measured_spend_on_resume(tmp_path):
    def short(d):
        node(d, "route")["deadline"] = 10
    slow = dict(CHILD, delay=100)   # a 1 s scaled deadline, as in the deadline-child test above: 0.2 s let a loaded host time out the worktree setup
    eng = make(tmp_path / "a", {"router": routed(req(deadline=10)), "route.router.1": slow}, tweak=short, time_scale=0.1)
    await eng.run()
    eng2 = make(tmp_path / "a", {}, tweak=short)
    assert eng2.replay.absorb_all() == []
    assert eng2.budget.run_spend == pytest.approx(0.1) and eng2.budget.unmeasured == pytest.approx(1.0)


@pytest.mark.anyio
@pytest.mark.heavy
async def test_the_workspace_preflight_applies_to_a_frontier_child(tmp_path):
    repo = make_repo(tmp_path / "repo")

    def with_worktree(d):
        d["nodes"].append({"name": "wt", "kind": "action", "effect": "worktree_create", "workspace": "worktrees/child", "deadline": 60})
        node(d, "main")["children"].insert(0, "wt")
    slow = dict(CHILD, delay=0.3)
    script = {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": CHILD, "route.router.2": slow}
    eng = make(tmp_path / "a", script, tweak=with_worktree, target=repo)
    assert (await eng.run()).verdict == "completed"
    crash_after_first_child(tmp_path / "a", tmp_path / "b")
    ws = tmp_path / "b" / "ws" / "worktrees_child"
    (ws / "base.txt").write_text("changed by hand\n")
    eng2 = make(tmp_path / "b", {"route.router.2": CHILD}, tweak=with_worktree, target=repo)
    v = await eng2.resume()
    r = ev(tmp_path / "b", "resume")[0]
    assert v.verdict == "conflict" and eng2.adapter.calls == [] and r["frontier"] == ["route.router.2"]
    assert r["preflight"]["reason"] == "workspace_changed:worktrees/child"


REPO_GRAPHS = (DESIGN / "router.graph.json", DESIGN.parent / "workflows" / "spec-to-build.graph.json",
               DESIGN / "five-phases.graph.json")


def _model_names(doc):
    """Every model a graph names: a node model, the dispatch allow list and an adaptation's set. Strings only."""
    names = []
    for n in doc.get("nodes") or []:
        if isinstance(n.get("model"), str):
            names.append(n["model"])
        names += (n.get("allow") or {}).get("models") or []
        names += (n.get("adapt") or {}).get("models") or []
    return names


def test_repo_graphs_name_a_spelling_their_gateway_route_serves():
    """SPEC 505 ticket 17 (#586): a registry landing changes no graph.

    A repo graph keeps the spelling it carried, and that exact string is in the gateway route's served list, so a run
    that does not opt into resolution sends what it sent before. `allow.models` stays a list of strings.
    """
    from squad import modelreg
    reg = modelreg.default_registry()
    for path in REPO_GRAPHS:
        doc = json.loads(path.read_text())
        for raw in _model_names(doc):
            ref = modelreg.normalize(raw, reg)
            assert ref.known, f"{path.name}: {raw!r} is not in the registry"
            gateway = reg.entries[ref.id].routes.get("gateway") or {}
            served = gateway.get("served") or []
            assert raw in served, f"{path.name}: the gateway route for {ref.id!r} does not serve {raw!r}: {served!r}"


def test_the_shipped_router_record_names_spellings_the_registry_knows():
    """SPEC 505 ticket 16: the record's spawn requests name spellings the registry resolves.

    The record is a fixture of a run that happened; its spellings are the ones that run sent, not a restamped id.
    """
    from squad import modelreg
    reg = modelreg.default_registry()
    events = [json.loads(line) for line in (DESIGN / "router.record.jsonl").read_text().splitlines() if line.strip()]
    models = [e["requested"]["model"] for e in events if e["event"] == "spawn_requested"]
    assert models
    for m in models:
        assert modelreg.normalize(m, reg).known, f"the record names {m!r}, a spelling the registry does not know"


def test_the_shipped_router_record_is_stamped_to_the_graph_it_ships_beside():
    # #1817/#1830: the record's run_start graph_hash is the contract check_record.py enforces against
    # docs/design/router.graph.json, so a graph edit and the restamp of the record are one change. Named here so a
    # drifted pair fails by its own name instead of only inside the frontier test below.
    from squad.graph import canon_hash
    doc = router()
    events = [json.loads(line) for line in (DESIGN / "router.record.jsonl").read_text().splitlines() if line.strip()]
    assert events[0]["event"] == "run_start" and events[0]["graph_hash"] == canon_hash(doc)


@pytest.mark.anyio
async def test_the_router_fixture_record_yields_the_frontier_of_one_child_and_one_resume_event(tmp_path):
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "docs" / "design" / "router.record.jsonl"
    events = [json.loads(line) for line in src.read_text().splitlines() if line.strip()]
    kept = [e for e in events if e["pos"] < next(x["pos"] for x in events if x["event"] == "resume")]
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in kept))
    asked = [e["requested"] for e in kept if e["event"] == "spawn_requested" and e["parent"] == "router"]
    for e in kept:
        if e["event"] == "outcome" and e["artifact"]:
            (tmp_path / e["artifact"]).write_text(json.dumps({"spawns": asked} if e["artifact"] == "spawns" else {}))
    d = router()
    repo = sibling_repo(tmp_path)
    from squad import actions
    for e in kept:       # the fixture predates landing: a passed writer needs the worktree a real run leaves behind
        if e["event"] == "outcome" and e["node"].startswith("route.router."):
            p = tmp_path / "ws" / f"worktrees_child-{e['node']}"
            assert actions.worktree_create(repo, p, actions.branch_name(events[0]["run_id"], p.name, None)).ok
    eng = Engine(from_doc(d), ScriptedAdapter({"route.router.3": CHILD}), tmp_path, target=repo)
    assert eng.g.hash == events[0]["graph_hash"]
    v = await eng.resume()
    r = [e for e in read(tmp_path / "record.jsonl") if e["event"] == "resume"]
    assert len(r) == 1 and r[0]["frontier"] == ["route.router.3"] and v.verdict == "completed"
    assert [c.node for c in eng.adapter.calls] == ["route.router.3"]


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_crash_before_any_request_is_decided_replays_the_entry_artifact(tmp_path):
    await two_children(tmp_path / "a")
    shutil.copytree(tmp_path / "a", tmp_path / "b")
    for d in (tmp_path / "b" / "ws").glob("*-route.*"):     # no child had launched, so none has a worktree
        shutil.rmtree(d)
    events = read(tmp_path / "b" / "record.jsonl")
    cut = next(e["pos"] for e in events if e["event"] == "outcome" and e["node"] == "router")
    (tmp_path / "b" / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events[:cut]))
    eng = make(tmp_path / "b", {"route.router.1": CHILD, "route.router.2": CHILD})
    v = await eng.resume()
    assert v.verdict == "completed" and [c.node for c in eng.adapter.calls] == ["route.router.1", "route.router.2"]
    assert [e["child"] for e in ev(tmp_path / "b", "spawn_accepted")] == ["route.router.1", "route.router.2"]


@pytest.mark.anyio
@pytest.mark.heavy   # two writing children, so two child worktrees in a real repository: 1.4 s here (#1721)
async def test_parallel_writing_children_run_in_distinct_worktrees_and_a_reader_shares_the_workspace(tmp_path):
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs"), req("codebase-design", tools=["Read"])), **{f"route.router.{i}": CHILD for i in (1, 2, 3)}})
    assert (await eng.run()).verdict == "completed"
    ws = {c.node: c.workspace for c in eng.adapter.calls if c.node.startswith("route.")}
    assert ws["route.router.1"] != ws["route.router.2"]
    assert all(str(tmp_path) in ws[n] and "worktrees_child-" in ws[n] for n in ("route.router.1", "route.router.2"))
    assert "-route.router" not in ws["route.router.3"]      # a read-only child keeps the named workspace
    outs = child_outs(tmp_path)
    assert outs["route.router.1"]["workspace"]["child"] == "route.router.1" and outs["route.router.1"]["workspace"]["path"] == ws["route.router.1"]
    assert "workspace" not in outs["route.router.3"] or "child" not in outs["route.router.3"]["workspace"]


@pytest.mark.anyio
@pytest.mark.heavy   # two child worktrees in a real repository: 4.6 s on a loaded CI runner (#1721)
async def test_a_request_never_picks_its_workspace_and_each_child_worktree_starts_from_the_target_head(tmp_path):
    repo = sibling_repo(tmp_path)
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": CHILD, "route.router.2": CHILD})
    eng.dispatch.prune = lambda: None      # the test reads the child worktrees after the run
    await eng.run()
    from squad import git
    heads = {git.head(c.workspace) for c in eng.adapter.calls if c.node.startswith("route.")}
    assert heads == {git.head(repo)}
    assert len({c.workspace for c in eng.adapter.calls if c.node.startswith("route.")}) == 2


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_resume_checks_a_child_worktree_by_its_own_path(tmp_path):
    await two_children(tmp_path / "a")
    crash_after_first_child(tmp_path / "a", tmp_path / "b")
    (tmp_path / "b" / "ws" / "worktrees_child-route.router.1" / "base.txt").write_text("changed by hand\n")
    eng = make(tmp_path / "b", {"route.router.2": CHILD})
    v = await eng.resume()
    assert v.verdict == "conflict" and eng.adapter.calls == []
    assert ev(tmp_path / "b", "resume")[0]["preflight"]["reason"] == "workspace_changed:worktrees/child"


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_child_whose_worktree_path_exists_is_refused_not_reused(tmp_path):
    await two_children(tmp_path / "a")
    shutil.copytree(tmp_path / "a", tmp_path / "b")
    events = read(tmp_path / "b" / "record.jsonl")
    cut = next(e["pos"] for e in events if e["event"] == "outcome" and e["node"] == "route.router.1")
    (tmp_path / "b" / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events[:cut]))
    eng = make(tmp_path / "b", {"route.router.2": CHILD})
    await eng.resume()
    out = child_outs(tmp_path / "b")["route.router.2"]
    assert out["outcome"] == "fail" and out["reasons"][:2] == ["child_workspace_failed", "workspace_exists"] and eng.adapter.calls == []


@pytest.mark.anyio
@pytest.mark.heavy   # one node start and a resume on the engine: 2.4 s on a CI runner
async def test_a_child_that_crashed_after_launch_is_not_run_again_and_its_grant_stays_unmeasured(tmp_path):
    await two_children(tmp_path / "a")
    shutil.copytree(tmp_path / "a", tmp_path / "b")
    events = read(tmp_path / "b" / "record.jsonl")
    c2 = next(e["pos"] for e in events if e["event"] == "call_start" and e["node"] == "route.router.2")
    cut = next(e["pos"] for e in events if e["event"] == "outcome" and e["node"] == "route.router.1")
    keep = [e for e in events[:max(cut, c2)] if not (e["event"] == "outcome" and e["node"] == "route.router.2")]
    (tmp_path / "b" / "record.jsonl").write_text("".join(json.dumps({**e, "pos": i}) + "\n" for i, e in enumerate(keep, 1)))
    shutil.rmtree(tmp_path / "b" / "ws" / "worktrees_child-route.router.2", ignore_errors=True)
    eng = make(tmp_path / "b", {"route.router.2": CHILD})
    v = await eng.resume()
    out = child_outs(tmp_path / "b")["route.router.2"]
    assert eng.adapter.calls == [] and out["outcome"] == "fail" and out["reasons"] == ["crashed", "launched_no_outcome"]
    assert out["spend"] is None and out["cap"] == 1.0
    assert eng.budget.unmeasured == pytest.approx(1.0) and eng.budget.available() <= eng.budget.cap - 1.0
    assert v.verdict == "failed" and "crashed" in v.reasons


@pytest.mark.anyio
@pytest.mark.heavy   # one node start on a named workspace: 2.5 s on a CI runner
async def test_a_read_only_child_starts_in_a_directory_that_exists_when_its_named_workspace_was_never_made(tmp_path):
    """#87: the real adapter refuses a missing working directory, so the fake adapter must check it too."""
    seen = {}

    def child(r):
        seen["ws"], seen["is_dir"] = r.workspace, Path(r.workspace).is_dir()
        return CHILD

    eng = make(tmp_path, {"router": routed(req("codebase-design", tools=["Read", "Grep", "Glob"])), "route.router.1": child})
    v = await eng.run()
    assert v.verdict == "completed" and seen["is_dir"], seen
    assert Path(seen["ws"]) == eng.target


@pytest.mark.anyio
@pytest.mark.heavy     # 3.2 s here and 2.1 s on a loaded CI runner, over the two-second line for an unmarked call
async def test_a_child_with_after_starts_only_when_the_children_it_names_have_passed(tmp_path):
    """`after` holds indexes of earlier requests; code turns them into child names."""
    order = []

    def impl(r):
        order.append("impl")
        return {**CHILD, "delay": 0.2}

    def review(r):
        order.append("review")
        return CHILD

    eng = make(tmp_path, {"router": routed(req(), req("codebase-design", tools=["Read"], after=[0])), "route.router.1": impl, "route.router.2": review})
    v = await eng.run()
    assert v.verdict == "completed" and order == ["impl", "review"]
    acc = ev(tmp_path, "spawn_accepted")
    assert acc[1]["definition"]["after"] == ["route.router.1"] and acc[0]["definition"]["after"] == []
    outs = child_outs(tmp_path)
    started = {e["node"]: e["pos"] for e in ev(tmp_path, "call_start")}
    assert started["route.router.2"] > outs["route.router.1"]["pos"]     # the reviewer starts after the implementer's outcome


@pytest.mark.anyio
@pytest.mark.heavy     # a loaded runner pushed this call past the 2 s line at 0.3 s of work (#1612)
async def test_a_child_whose_after_child_failed_fails_unrun_with_the_reason(tmp_path):
    bad = {"output": {"diff": {"files": []}, "ac_commands": ["false"]}, "spend": 0.2}
    eng = make(tmp_path, {"router": routed(req(), req("codebase-design", tools=["Read"], after=[0])), "route.router.1": {**bad, "output": {"ac_commands": ["true"]}, "error": "boom"},
                          "route.router.2": CHILD})
    await eng.run()
    out = child_outs(tmp_path)["route.router.2"]
    assert out["outcome"] == "fail" and out["reasons"] == ["after_failed", "route.router.1"]
    assert [c.node for c in eng.adapter.calls if c.node == "route.router.2"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("after,reason", [([1], "after_unmet:1"), ([0], "after_unmet:0"), ([5], "after_unmet:5"), (["x"], "after_malformed"), ([-1], "after_malformed"), (0, "after_malformed")])
async def test_an_after_that_names_no_earlier_accepted_request_rejects_the_request_whole(tmp_path, after, reason):
    eng = make(tmp_path, {"router": routed(req(after=after)), "route.router.1": CHILD})
    await eng.run()
    rej = ev(tmp_path, "spawn_rejected")
    assert rej and rej[0]["reason"] == reason


@pytest.mark.anyio
async def test_a_read_only_child_that_runs_after_a_writer_starts_in_the_writers_worktree(tmp_path):
    """#88: a reviewer must see the work it reviews. Without this it read the target and found nothing."""
    seen = {}

    def impl(r):
        (Path(r.workspace) / "made.txt").write_text("work\n")
        seen["impl"] = r.workspace
        return CHILD

    def review(r):
        seen["review"], seen["sees"] = r.workspace, (Path(r.workspace) / "made.txt").exists()
        return CHILD

    eng = make(tmp_path, {"router": routed(req(), req("codebase-design", tools=["Read"], after=[0])), "route.router.1": impl, "route.router.2": review})
    v = await eng.run()
    assert v.verdict == "completed" and seen["review"] == seen["impl"] and seen["sees"], seen


@pytest.mark.anyio
async def test_a_capped_child_with_a_valid_artifact_passes_and_its_cap_rides_as_a_diagnostic(tmp_path):
    """The spend rule holds for a dispatch child as for an agent node."""
    capped = {**CHILD, "stop_reason": "stall_cap"}
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": capped})
    await eng.run()
    out = child_outs(tmp_path)["route.router.1"]
    assert out["outcome"] == "pass" and out["reasons"] == [] and out["diagnostic"] == {"cap": "node_stall_cap"}


@pytest.mark.anyio
@pytest.mark.heavy
async def test_a_dispatch_with_no_accepted_child_names_why_each_request_was_refused(tmp_path):
    wrong = req(name="P.12", skills=["tdd"], budget={"stalls": 5}, acceptance_commands=["true"])      # the shape a model invented for a repo it did not know
    del wrong["skill"]
    eng = make(tmp_path, {"router": routed(wrong)})
    v = await eng.run()
    d = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"][0]
    assert d["reasons"][0] == "no_dispatch" and v.verdict == "failed"
    assert "spawn_rejected:model_supplied_identity:name" in d["reasons"]
    assert "spawn_rejected:missing_skill" not in d["reasons"]   # an omitted skill falls back to the envelope (#1607)
    assert not [r for r in d["reasons"] if "unknown_field" in r]
    assert v.reasons[0] == "no_dispatch" and "spawn_rejected:model_supplied_identity:name" in v.reasons       # the verdict line carries them too


@pytest.mark.anyio
async def test_a_request_with_no_skill_dispatches_and_the_record_names_the_envelope_skill(tmp_path):
    """#1607: a router that left `skill` out cost the run its whole dispatch and delivered nothing. The envelope answers
    with its first allowed skill, the child runs, and the record names the skill the envelope supplied."""
    no_skill = req()
    del no_skill["skill"]
    eng = make(tmp_path, {"router": routed(no_skill), "route.router.1": CHILD})
    v = await eng.run()
    acc = ev(tmp_path, "spawn_accepted")
    assert v.verdict == "completed" and acc and acc[0]["definition"]["skill"]["name"] == "tdd", acc
    assert not ev(tmp_path, "spawn_rejected")


def test_the_router_prompt_documents_every_request_field_and_one_example():
    from squad.envelope import REQUEST_FIELDS
    text = (Path(__file__).resolve().parent.parent / "prompts" / "router.md").read_text()
    block = json.loads(text.split("```json\n")[1].split("\n```")[0])
    assert set(block) <= REQUEST_FIELDS and {"prompt", "skill"} <= set(block) and isinstance(block["skill"], str)


@pytest.mark.anyio
async def test_the_router_is_given_the_limits_it_may_ask_within(tmp_path):
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": CHILD})
    await eng.run()
    call = next(c for c in eng.adapter.calls if c.node == "router")
    limits = json.loads(call.prompt.split(" ", 1)[1] if call.prompt.startswith("/") else call.prompt)["limits"]
    assert "tdd" in limits["skills"] and "evil" not in limits["skills"]
    assert set(limits) >= {"skills", "kinds", "efforts", "tools", "workspaces", "stalls", "spend", "deadline", "max_children"}
    assert not [c for c in eng.adapter.calls if c.node == "route.router.1" and "limits" in c.prompt]       # a child sees no limits, only its own task


def _seed_router():
    from squad.workflows import DYNAMIC_SEED
    return next(n for n in json.loads(DYNAMIC_SEED.read_text())["nodes"] if n["name"] == "router")


def test_the_router_role_names_a_stopping_rule():          # #150 acceptance 3
    root = Path(__file__).resolve().parent.parent
    for text in ((root / "prompts" / "router.md").read_text(), _seed_router().get("instructions", "")):
        assert "## Stopping rule" in text and "about fifteen turns" in text and "needs-human" in text
        assert text.index("## Stopping rule") > text.index("Return these fields")      # after the role text


def test_the_dynamic_seed_router_carries_its_role_text_and_the_raised_budget():     # #150
    root = Path(__file__).resolve().parent.parent
    r = _seed_router()
    assert r.get("instructions") == (root / "prompts" / "router.md").read_text().strip()
    assert r["budget"] == {"stalls": {"min": 2, "max": 5}, "spend": {"min": 0.05, "max": 3}} and r["deadline"] == 1200


def test_the_dynamic_seed_allows_only_skills_the_plugin_ships():
    from squad.workflows import DYNAMIC_SEED
    doc = json.loads(DYNAMIC_SEED.read_text())
    allowed = {s["name"] for n in doc["nodes"] if n["kind"] == "dispatch" for s in n["allow"]["skills"]}
    assert allowed and allowed <= set(skills.load()), f"{sorted(allowed - set(skills.load()))} are allowed but not vendored: a child cannot load them"


def test_the_router_prompt_names_the_three_answers_and_warns_that_a_handoff_may_be_stale():    # #159, #161
    text = (Path(__file__).resolve().parent.parent / "prompts" / "router.md").read_text()
    assert '"status": "needs-human"' in text and '"spawns": []' in text and "`children`" in text
    assert "stale" in text and "ask a child" in text


PATH_RULE = ("A command names a repo-relative path and the repository's own interpreter, never an absolute home path "
             "or a `$(pwd)` expansion: write `python3 -m pytest tests/test_workcmd.py -q` or "
             "`.venv/bin/python -m pytest tests/test_workcmd.py -q` from the repo root.")


SHELL_RULE = ("The gate runs each command under `/bin/sh`, so a command stays inside POSIX shell syntax: "
              "no bash-only forms such as `[[ ]]`, arrays, process substitution or `function`.")


def test_the_router_prompt_asks_for_repo_relative_paths_and_the_repositorys_interpreter():    # #1803
    root = Path(__file__).resolve().parent.parent
    for text in ((root / "prompts" / "router.md").read_text(), _seed_router().get("instructions", "")):
        para = next(p for p in text.split("\n\n") if "`ac_commands`" in p and "NARROWEST" in p)
        assert PATH_RULE in para, "the ac_commands paragraph must carry the concrete path and interpreter rule"
        assert SHELL_RULE in para, "the ac_commands paragraph must name the shell dialect a command runs under"


def test_a_child_with_no_artifact_records_how_its_session_ended():
    from squad.adapter import CallResult
    from squad.runners import children  # noqa: F401  the runner reads `ended` from the result
    r = CallResult(None, 1.0, 5, None, ended={"subtype": "success", "is_error": False, "last": "I ran out of room"})
    assert r.ended["last"] == "I ran out of room"
    from squad.sdk_adapter import ended_of
    class R:
        result = "x" * 900
        is_error = False
    assert ended_of(R(), "success") == {"subtype": "success", "is_error": False, "last": "x" * 500}


@pytest.mark.anyio
@pytest.mark.heavy   # a loaded serial runner pushes the scripted run over the 2 s line (3.4 s in CI, 0.65 s here)
async def test_a_request_naming_a_field_outside_the_envelope_still_dispatches(tmp_path):
    eng = make(tmp_path, {"router": routed(req(turns=3)), "route.router.1": CHILD})
    await eng.run()
    d = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"][0]
    assert "no_dispatch" not in d["reasons"] and not [r for r in d["reasons"] if r.startswith("spawn_rejected")]
    assert "route.router.1" in child_outs(tmp_path)


# ---- #731: a gate the dispatch runs is a graph node, and the record says it ran
def declared_gates():
    """The gate nodes the shipped router graph declares for its one dispatch, read from the document itself."""
    d = node(router(), "route")
    return [*d["checks"], *([d["review"]] if d.get("review") else [])]


def gate_events(p, gates):
    """The start event and the outcome event of every declared gate, by name, for one run."""
    starts = {e["node"]: e for e in ev(p, "gate_start") if e["node"] in gates}
    outs = {e["node"]: e for e in ev(p, "outcome") if e["node"] in gates}
    return starts, outs


@pytest.mark.anyio
async def test_the_gates_a_dispatch_runs_are_recorded_as_gate_nodes(tmp_path):
    """#731: `route` names `scope_gate` and `security_gate` as its checks and `review_gate` as its review, and code ran
    all three after every child. The record held no `gate_start` and no outcome for any of them, so the dashboard read
    the two checks as the child's own and the review gate as never run. A gate the graph declares and the engine runs is
    recorded as a node: a start, then one outcome for the entry (ADR-0100, ADR-0113 rule 2)."""
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": CHILD})
    v = await eng.run()
    assert v.verdict == "completed"
    starts = {e["node"]: e for e in ev(tmp_path, "gate_start")}
    outs = {e["node"]: e for e in ev(tmp_path, "outcome") if e["node"] in ("scope_gate", "security_gate", "review_gate")}
    for g in ("scope_gate", "security_gate", "review_gate"):
        assert g in starts and g in outs, (g, sorted(starts), sorted(outs))
        assert starts[g]["pos"] < outs[g]["pos"] and outs[g]["outcome"] == "pass"


@pytest.mark.anyio
async def test_a_check_that_fails_a_child_fails_its_gate_node_and_names_the_child(tmp_path):
    bad = {"output": {"diff": {"files": [{"path": "src/auth/login.py"}]}, "ac_commands": ["true"]}, "spend": 0.2}
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": bad})
    await eng.run()
    out = [e for e in ev(tmp_path, "outcome") if e["node"] == "security_gate"]
    assert out and out[-1]["outcome"] == "fail" and any("route.router.1" in r for r in out[-1]["reasons"])
    scope = [e for e in ev(tmp_path, "outcome") if e["node"] == "scope_gate"]
    assert scope and scope[-1]["outcome"] == "pass"


@pytest.mark.anyio
async def test_a_dispatch_entry_with_no_accepted_child_still_records_every_gate_it_declares(tmp_path):
    """#731 review: zero accepted children is a completed dispatch entry, and the graph still declares `scope_gate`,
    `security_gate` and `review_gate` for it. Nothing had a child to check, so the gate's outcome says that in its
    reasons. Silence left the dashboard reading a declared gate as one nothing ran (ADR-0100, ADR-0113 rule 26)."""
    eng = make(tmp_path, {"router": routed()})
    v = await eng.run()
    assert v.verdict == "failed"                         # `no_dispatch`: the empty run still fails
    gates = declared_gates()
    starts, outs = gate_events(tmp_path, gates)
    assert sorted(outs) == sorted(gates), sorted(outs)
    for g in gates:
        assert starts[g]["pos"] < outs[g]["pos"]
    assert outs["scope_gate"]["outcome"] == "pass" and outs["scope_gate"]["reasons"] == ["no_child_checked"]
    assert outs["security_gate"]["outcome"] == "pass" and outs["security_gate"]["reasons"] == ["no_child_checked"]
    assert outs["review_gate"]["outcome"] == "pass" and outs["review_gate"]["reasons"] == ["no_child_reviewed"]


@pytest.mark.anyio
async def test_a_dispatch_entry_that_fails_before_dispatch_still_records_every_gate_it_declares(tmp_path):
    """#731 review: the router's own entry can fail before any request is lifted. The dispatch entry is still complete,
    and the gates it declares are still recorded: a start and an outcome that names the empty set they checked.
    The run fails on the entry's reasons, never on a silent gate (ADR-0100, ADR-0113 rules 2 and 26)."""
    entry = {"output": {"status": "fail", "reasons": ["bad"]}, "spend": 0.1}
    eng = make(tmp_path, {"router": entry})
    v = await eng.run()
    assert v.verdict == "failed"
    gates = declared_gates()
    starts, outs = gate_events(tmp_path, gates)
    assert sorted(outs) == sorted(gates), sorted(outs)
    for g in gates:
        assert starts[g]["pos"] < outs[g]["pos"]
    assert outs["review_gate"]["reasons"] == ["no_child_reviewed"]
    assert [e for e in ev(tmp_path, "outcome") if e["node"] == "route"][0]["reasons"] == ["bad"]
