"""Guards for the verdict fold, the stopped verdict and the five exit codes."""
import pytest

from squad import verdict as V

PLAN = ["main", "a", "b", "c"]


def events(*rest, options=None, plan=PLAN):
    evs = [{"event": "run_start", "run_id": "r1", "graph_name": "g", "graph_hash": "h", "engine_options": options or {}, "root": "main", "plan": plan}]
    evs += list(rest)
    for i, e in enumerate(evs, 1):
        e["pos"] = i
    return evs


def out(node, outcome="pass", reasons=None, routed=None, item=None, entry=1):
    e = {"event": "outcome", "node": node, "entry": entry, "outcome": outcome, "reasons": reasons or [], "artifact": None,
         "spend": None, "turns": None, "cap": None, "elapsed": None}
    if routed:
        e["routed"] = routed
    if item is not None:
        e["item"] = item
    return e


@pytest.mark.parametrize("rows,obs,verdict,code,reasons", [
    ([out("a"), out("b"), out("c"), out("main")], {}, "completed", 0, []),
    ([out("a", "fail", ["node_stall_cap"]), out("main", "fail", ["loop_max_exhausted"])], {}, "failed", 1, ["loop_max_exhausted", "node_stall_cap"]),
    ([out("a", "deadline", ["deadline"]), out("main", "deadline", ["deadline"])], {}, "failed", 1, ["deadline"]),
    ([out("a", "deadline"), out("main", "deadline", ["deadline"])], {"effect_observed": True}, "completed", 0, []),
    ([out("a", "needs-human"), out("main", "needs-human", ["question"])], {}, "needs-human", 10, ["question"]),
    ([out("a"), out("main")], {"conflict": ["unmerged:src/x.py"]}, "conflict", 20, ["unmerged:src/x.py"]),
    ([out("a", "fail"), out("main", "fail")], {"conflict": ["dirty_base"]}, "failed", 1, ["a:fail"]),
])
def test_each_row_of_the_fold_gives_its_verdict_and_code(rows, obs, verdict, code, reasons):
    v = V.fold(events(*rows), obs)
    assert (v.verdict, v.exit_code) == (verdict, code)
    assert v.reasons == reasons or set(reasons) <= set(v.reasons)


def test_exit_codes_are_a_pure_function_of_the_verdict():
    assert V.EXIT == {"completed": 0, "needs-human": 10, "conflict": 20, "failed": 1, "stopped": 30, "no-repro": 40}
    for name, code in V.EXIT.items():
        assert V.exit_code(name) == code
    with pytest.raises(KeyError):
        V.exit_code("partial")


def test_only_the_verdict_module_defines_the_exit_codes():
    import pathlib
    src = pathlib.Path(V.__file__).parent
    for p in src.glob("*.py"):
        if p.name != "verdict.py":
            assert '"needs-human": 10' not in p.read_text(), p.name


def test_false_positive_a_fail_then_a_passing_recheck_is_completed():
    rows = [out("a"), out("gate", "fail", ["sensitive_path:auth"], routed="repeat"), out("a", entry=2), out("gate", entry=2), out("main")]
    v = V.fold(events(*rows, plan=["main", "a", "gate"]))
    assert v.verdict == "completed" and v.remains == []


def test_a_truncated_run_with_every_node_passed_is_stopped_exit_30():
    v = V.fold(events(out("a"), out("b"), options={"stop_after": "b"}), run_id="r1")
    assert (v.verdict, v.exit_code, v.reasons) == ("stopped", 30, ["stopped_after:b"])
    assert v.remains == ["main", "c"] and "squad resume r1" in v.message


def test_a_truncated_run_with_a_failed_node_is_failed_not_stopped():
    v = V.fold(events(out("a"), out("b", "fail", ["gate_fail"]), options={"stop_after": "b"}))
    assert (v.verdict, v.exit_code) == ("failed", 1) and "gate_fail" in v.reasons


def test_a_truncated_run_with_a_conflict_is_conflict_and_with_a_wait_is_needs_human():
    base = [out("a"), out("b")]
    assert V.fold(events(*base, options={"stop_after": "b"}), {"conflict": ["dirty"]}).verdict == "conflict"
    wait = {"event": "wait", "node": "c", "owner": "rafael", "deadline_at": 9e9, "default": "fail"}
    assert V.fold(events(*base, wait, options={"stop_after": "b"})).verdict == "needs-human"


def test_a_failed_node_takes_precedence_over_a_conflict_and_a_wait():
    wait = {"event": "wait", "node": "c", "owner": "r", "deadline_at": 9e9, "default": "fail"}
    v = V.fold(events(out("a", "fail"), wait, options={"stop_after": "a"}), {"conflict": ["dirty"]})
    assert v.verdict == "failed"


def test_a_fail_that_a_continue_drops_does_not_fail_a_stopped_run():
    v = V.fold(events(out("a", "fail", routed="continue"), out("b"), options={"stop_after": "b"}))
    assert v.verdict == "stopped"


def test_a_wait_holds_the_run_as_needs_human_until_its_outcome_is_recorded():
    wait = {"event": "wait", "node": "b", "owner": "rafael", "deadline_at": 9e9, "default": "fail"}
    assert V.fold(events(out("a"), wait)).verdict == "needs-human"
    assert V.fold(events(out("a"), wait, out("b"), out("main"))).verdict == "completed"


def test_the_last_verdict_of_a_resume_uses_the_latest_invocation_options():
    resume = {"event": "resume", "run_id": "r1", "graph_hash": "h", "frontier": ["c"], "invoker": "cli", "engine_options": {}, "preflight": {"status": "ok"}}
    v = V.fold(events(out("a"), out("b"), resume, out("c"), out("main"), options={"stop_after": "b"}))
    assert v.verdict == "completed"


def test_a_preflight_failure_and_conflict_on_resume_fold_to_failed_and_conflict():
    def mk(pre):
        return {"event": "resume", "run_id": "r1", "graph_hash": "h", "frontier": [], "invoker": "cli", "engine_options": {}, "preflight": pre}
    assert V.fold(events(out("a"), mk({"status": "failed", "reason": "worktree_missing:w"}))).verdict == "failed"
    c = V.fold(events(out("a"), mk({"status": "conflict", "reason": "workspace_dirty:w"})))
    assert (c.verdict, c.exit_code, c.reasons) == ("conflict", 20, ["workspace_dirty:w"])
    ok = V.fold(events(out("a"), mk({"status": "failed", "reason": "x"}), mk({"status": "ok"}), out("main")))
    assert ok.verdict == "completed"


def test_a_dispatch_entry_with_no_accepted_child_folds_to_fail_no_dispatch():
    assert V.fold_dispatch([], "all", accepted=0) == ("fail", ["no_dispatch"])
    assert V.fold_dispatch(["pass", "pass"], "all", accepted=2) == ("pass", [])
    rows = [out("router"), out("route", "fail", ["no_dispatch"]), out("main", "fail", ["no_dispatch"])]
    v = V.fold(events(*rows, plan=["main", "route", "router"]))
    assert v.verdict == "failed" and v.reasons[0] == "no_dispatch"


def test_join_order_is_fail_then_deadline_then_needs_human_then_pass():
    assert V.join(["pass", "needs-human", "deadline", "fail"]) == "fail"
    assert V.join(["pass", "needs-human", "deadline"]) == "deadline"
    assert V.join(["pass", "needs-human"]) == "needs-human"
    assert V.join(["pass", "pass"]) == "pass"
    assert V.join(["pass", "deadline", "fail"], "first_failure") == "deadline"


def test_a_run_with_no_root_outcome_no_wait_and_no_stop_is_failed_not_completed():
    v = V.fold(events(out("a")))
    assert v.verdict == "failed" and v.reasons == ["no_root_outcome"]


def test_remains_names_steps_that_never_ran_and_a_completed_run_has_none():
    v = V.fold(events(out("a", "fail"), out("main", "fail")))
    assert v.remains == ["b", "c"]
    assert V.fold(events(out("a"), out("main"))).remains == []


def commit(node, head="ab12cd34ef56"):
    return {"event": "workspace_commit", "node": node, "entry": 1, "workspace": "target", "path": "/ws/target",
            "status": "committed", "head": head}


def test_a_deadline_after_a_committed_node_keeps_the_branch_and_names_its_unproven_proof():
    rows = [commit("code"), out("code"), out("final_gate", "deadline", ["deadline"]), out("main", "deadline", ["deadline"])]
    v = V.fold(events(*rows, plan=["main", "code", "final_gate"]))
    assert (v.verdict, v.exit_code) == ("completed", 0), v.reasons
    assert v.reasons == ["unproven:final_gate", "kept:target@ab12cd34e"]


def test_a_deadline_before_any_commit_is_still_failed_with_the_deadline_reason():
    rows = [out("code", "deadline", ["deadline"]), out("main", "deadline", ["deadline"])]
    v = V.fold(events(*rows, plan=["main", "code"]))
    assert (v.verdict, v.exit_code) == ("failed", 1)
    assert v.reasons == ["deadline"]


def test_a_run_whose_only_failures_are_unproven_completes_and_names_the_node():    # #1825
    """ADR-0164: an unfinished proof never holds delivery. When every failing outcome is unproven, the run is
    completed and the reasons name the node whose proof did not finish."""
    rows = [out("code"), out("final_gate", "unproven", ["ac-verify:unproven:bash contrib/squad-goal/scripts/fast-gate.sh"]), out("main")]
    v = V.fold(events(*rows, plan=["main", "code", "final_gate"]))
    assert (v.verdict, v.exit_code) == ("completed", 0), v.reasons
    assert v.reasons == ["unproven:final_gate"]


def test_an_unproven_outcome_routed_to_continue_is_no_failing_outcome():    # #1825
    """Only an unproven outcome that exits its step is a failing outcome. A node that answered unproven but was routed
    to continue did not fail the run, so the fold never counts it: a completed run is not relabelled by it (ADR-0164)."""
    rows = [out("a", "unproven", routed="continue"), out("main")]
    v = V.fold(events(*rows))
    assert (v.verdict, v.exit_code) == ("completed", 0), v.reasons
    assert not any(r.startswith("unproven:") for r in v.reasons)


def test_a_needs_human_sibling_outranks_an_unproven_one_in_the_join():    # #1825
    """A person's decision is never turned into a completed run by an unfinished proof beside it: needs-human is the
    worse join, so the run surfaces the hold."""
    assert V.join(["unproven", "needs-human"], "all") == "needs-human"


def test_a_measured_red_beside_an_unproven_outcome_still_fails():    # #1825
    """A measured red wins: the unproven sibling never releases it (ADR-0164)."""
    rows = [out("final_gate", "unproven"), out("main", "fail", ["ac-verify:ac_failed:exit 3"])]
    v = V.fold(events(*rows, plan=["main", "final_gate"]))
    assert (v.verdict, v.exit_code) == ("failed", 1)
    assert "ac-verify:ac_failed:exit 3" in v.reasons


def test_a_commit_written_after_the_deadline_keeps_nothing():
    rows = [out("code", "deadline", ["deadline"]), commit("code"), out("main", "deadline", ["deadline"])]
    v = V.fold(events(*rows, plan=["main", "code"]))
    assert v.verdict == "failed"
    assert "kept:" not in " ".join(v.reasons)
