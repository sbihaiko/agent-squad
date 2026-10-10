"""Tickets #38 to #40: the dispatch node."""
import json
import shutil
from pathlib import Path

import anyio
import pytest

from squad import skills
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.record import read
from tests.gitrepo import make_repo, sh, sibling_repo
from tests.helpers import node, router


def req(skill="tdd", **kw):
    return {"prompt": f"Run {skill} on the task.", "skill": skill, "model": "sonnet", "effort": "medium", "tools": ["Read", "Edit"],
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
    eng = Engine(from_doc(d), ScriptedAdapter(script, **kw.pop("adapter", {})), tmp_path, **kw)
    return eng


def ev(tmp_path, kind):
    return [e for e in read(tmp_path / "record.jsonl") if e["event"] == kind]


def child_outs(tmp_path):
    return {e["node"]: e for e in ev(tmp_path, "outcome") if e["node"].startswith("route.")}


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
    (dict(model="gpt"), "model_outside_allow:gpt"), (dict(effort="max"), "effort_outside_allow"),
    (dict(tools=["Read", "WebFetch"]), "tool_outside_allow:WebFetch"), (dict(skill="evil"), "skill_outside_allow"),
    (dict(stalls=999), "stalls_outside_budget"), (dict(stalls=1), "stalls_outside_budget"), (dict(spend=50.0), "spend_outside_budget"),
    (dict(deadline=99999), "deadline_over_dispatch"), (dict(workspace="elsewhere"), "workspace_outside_allow"),
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
async def test_zero_accepted_children_fold_to_fail_no_dispatch(tmp_path):
    eng = make(tmp_path, {"router": routed()})
    v = await eng.run()
    d = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"][0]
    assert d["outcome"] == "fail" and d["reasons"] == ["no_dispatch"] and v.verdict == "failed"


@pytest.mark.anyio
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
async def test_the_checks_gates_run_after_every_child_and_a_failed_check_fails_the_child(tmp_path):
    bad = {"output": {"diff": {"files": [{"path": "src/auth/login.py"}]}, "ac_commands": ["true"]}, "spend": 0.2}
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": bad, "route.router.2": CHILD})
    v = await eng.run()
    c = child_outs(tmp_path)
    assert c["route.router.1"]["outcome"] == "fail" and c["route.router.1"]["checks"]["security_gate"] == "fail"
    assert any(r.startswith("check:security_gate") for r in c["route.router.1"]["reasons"])
    assert c["route.router.2"]["outcome"] == "pass" and v.verdict == "failed"


@pytest.mark.anyio
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
async def test_max_children_per_entry_is_enforced(tmp_path):
    reqs = [req(f) for f in ("tdd", "diagnosing-bugs", "codebase-design", "tdd", "tdd")]
    eng = make(tmp_path, {"router": routed(*reqs), "route.router.1": CHILD}, adapter={"repeat_last": True, "default": CHILD})
    await eng.run()
    assert len(ev(tmp_path, "spawn_accepted")) == 4
    assert [r["reason"] for r in ev(tmp_path, "spawn_rejected")] == ["max_children_over"]


@pytest.mark.anyio
async def test_children_run_in_parallel_up_to_max_parallel(tmp_path):
    slow = dict(CHILD, delay=0.6)
    ro = {"tools": ["Read"]}    # read-only children get no worktree, so the timing shows the fan-out alone
    eng = make(tmp_path, {"router": routed(req(**ro), req("diagnosing-bugs", **ro), req("codebase-design", **ro)), "route.router.1": slow, "route.router.2": slow, "route.router.3": slow})
    t0 = anyio.current_time()
    v = await eng.run()
    took = anyio.current_time() - t0
    assert v.verdict == "completed" and 1.0 < took < 1.7      # three 0.6 s children, two at a time (serial would take 1.8 s or more)


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
async def test_a_resume_checks_a_child_worktree_by_its_own_path(tmp_path):
    await two_children(tmp_path / "a")
    crash_after_first_child(tmp_path / "a", tmp_path / "b")
    (tmp_path / "b" / "ws" / "worktrees_child-route.router.1" / "base.txt").write_text("changed by hand\n")
    eng = make(tmp_path / "b", {"route.router.2": CHILD})
    v = await eng.resume()
    assert v.verdict == "conflict" and eng.adapter.calls == []
    assert ev(tmp_path / "b", "resume")[0]["preflight"]["reason"] == "workspace_changed:worktrees/child"


@pytest.mark.anyio
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
async def test_a_dispatch_with_no_accepted_child_names_why_each_request_was_refused(tmp_path):
    wrong = req(name="P.12", skills=["tdd"], budget={"stalls": 5}, acceptance_commands=["true"])      # the shape a model invented for a repo it did not know
    del wrong["skill"]
    eng = make(tmp_path, {"router": routed(wrong)})
    v = await eng.run()
    d = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"][0]
    assert d["reasons"][0] == "no_dispatch" and v.verdict == "failed"
    assert {"spawn_rejected:model_supplied_identity:name", "spawn_rejected:unknown_field:skills", "spawn_rejected:unknown_field:budget",
            "spawn_rejected:unknown_field:acceptance_commands", "spawn_rejected:missing_skill"} <= set(d["reasons"])
    assert v.reasons[0] == "no_dispatch" and "spawn_rejected:unknown_field:budget" in v.reasons       # the verdict line carries them too


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
    assert set(limits) >= {"skills", "models", "efforts", "tools", "workspaces", "stalls", "spend", "deadline", "max_children"}
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
    assert r["budget"] == {"stalls": {"min": 2, "max": 5}, "spend": {"min": 0.05, "max": 8}} and r["deadline"] == 1200


def test_the_dynamic_seed_allows_only_skills_the_plugin_ships():
    from squad.workflows import DYNAMIC_SEED
    doc = json.loads(DYNAMIC_SEED.read_text())
    allowed = {s["name"] for n in doc["nodes"] if n["kind"] == "dispatch" for s in n["allow"]["skills"]}
    assert allowed and allowed <= set(skills.load()), f"{sorted(allowed - set(skills.load()))} are allowed but not vendored: a child cannot load them"


def test_the_router_prompt_names_the_three_answers_and_warns_that_a_handoff_may_be_stale():    # #159, #161
    text = (Path(__file__).resolve().parent.parent / "prompts" / "router.md").read_text()
    assert '"status": "needs-human"' in text and '"spawns": []' in text and "`children`" in text
    assert "stale" in text and "ask a child" in text


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
