"""Ticket #90: a dispatch lands passing write children in request order, a wave ledger feeds the router, a stop word ends the waves."""
import json
import shutil
from pathlib import Path

import pytest

from squad import git
from squad.record import read
from tests.gitrepo import sh, sibling_repo
from tests.helpers import node
from tests.test_dispatch import CHILD, child_outs, ev, make, req, routed


def writer(name, text="x\n", cmds=("true",), **out):
    """A child step that writes one file in its worktree and reports it."""
    def effect(r):
        (Path(r.workspace) / name).write_text(text)
    return {"output": {"diff": {"files": [{"path": name}]}, "ac_commands": list(cmds), **out}, "spend": 0.2, "turns": 3, "effect": effect}


def wiper(name, remove, cmds=("true",), **out):
    """A child step that writes one file and deletes another in its worktree, so its merge can break a landed child."""
    step = writer(name, "y\n", cmds, **out)
    inner = step["effect"]

    def effect(r):
        inner(r)
        (Path(r.workspace) / remove).unlink()
    return {**step, "effect": effect}


def waves(n, fail="continue", stall=None, task=False):
    """The wave graph: an inner loop re-enters the router while a wave passes; the final gate runs once after the stop word.
    `stall` opts the loop into #93's stall exit: {"count": k} ends the run `waves_stalled` after k waves land nothing,
    well before `n` (`max`) is ever reached. `task` opts into #98's per-task ceiling: `max` (`n`) caps one task's
    attempts, not the entries."""
    def tweak(d):
        w = {"name": "waves", "kind": "loop", "max": {"value": n, "min": 1}, "children": ["route"]}
        if stall is not None:
            w["stall_after"] = {"count": stall["count"], "by": "land"}
        if task:
            w["max_by"] = "task"
        d["nodes"].append(w)
        node(d, "main")["children"] = ["waves", "final_gate"]
        node(d, "route")["next"] = {"pass": "repeat", **({"fail": "repeat"} if fail == "repeat" else {})}
    return tweak


def seen(call):
    """The inputs a router was shown: its prompt is the skill prefix and one JSON object."""
    return json.loads(call.prompt.split(" ", 1)[1])["inputs"]


def lands(tmp_path):
    return ev(tmp_path, "land")


@pytest.mark.anyio
async def test_a_passing_write_child_is_landed_in_the_target_and_recorded(tmp_path):
    repo = sibling_repo(tmp_path)
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": writer("a.txt")}, target=repo)
    assert (await eng.run()).verdict == "completed"
    assert (repo / "a.txt").read_text() == "x\n"
    [land] = lands(tmp_path)
    assert land["node"] == "route.router.1" and land["parent"] == "route" and land["outcome"] == "pass"
    assert land["effect"]["name"] == "merge_land" and land["effect"]["head"] == git.head(repo)


@pytest.mark.anyio
async def test_two_write_children_land_in_request_order_and_the_final_gate_sees_both(tmp_path):
    repo = sibling_repo(tmp_path)
    cmd = "test -f a.txt && test -f b.txt"
    script = {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": writer("a.txt", cmds=[cmd]),
              "route.router.2": writer("b.txt", cmds=[cmd])}
    eng = make(tmp_path, script, target=repo)
    assert (await eng.run()).verdict == "completed"
    assert [e["node"] for e in lands(tmp_path)] == ["route.router.1", "route.router.2"]
    assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
    merges = sh(repo, "git", "log", "--merges", "--format=%s").splitlines()
    assert len(merges) == 2


@pytest.mark.anyio
async def test_a_conflicting_second_writer_fails_with_merge_conflict_and_the_first_stays_landed(tmp_path):
    repo = sibling_repo(tmp_path)
    script = {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": writer("same.txt", "one\n"),
              "route.router.2": writer("same.txt", "two\n")}
    eng = make(tmp_path, script, target=repo)
    v = await eng.run()
    first, second = lands(tmp_path)
    assert (first["outcome"], second["outcome"]) == ("pass", "fail") and second["reasons"] == ["merge_conflict"]
    assert (repo / "same.txt").read_text() == "one\n" and not git.dirty(repo)
    disp = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"][0]
    assert disp["outcome"] == "fail" and "merge_conflict" in disp["reasons"] and v.verdict == "failed"


@pytest.mark.anyio
async def test_a_landing_that_breaks_an_earlier_childs_held_out_command_is_undone(tmp_path):
    """The land of issue 06 turned issue 13's held-out command red,
    and the final gate was the first thing to notice, fifteen lands later. The child that broke it passed its own.

    Both worktrees are cut from the target before either lands, so the second child deletes a file the base commit
    already held. A file the first child created would not be in the second child's worktree at all.
    """
    repo = sibling_repo(tmp_path)
    script = {"router": routed(req(), req("diagnosing-bugs")),
              "route.router.1": writer("a.txt", cmds=["test -f a.txt && test -f base.txt"]),
              "route.router.2": wiper("b.txt", "base.txt", cmds=["test -f b.txt"])}
    v = await make(tmp_path, script, target=repo).run()
    first, second = lands(tmp_path)
    assert (first["outcome"], second["outcome"]) == ("pass", "fail")
    assert second["reasons"] == ["ac_regression:route.router.1"]   # route.router.2 passed its own command
    assert (repo / "base.txt").exists() and not (repo / "b.txt").exists()   # the merge was undone
    assert not git.dirty(repo)
    assert len(sh(repo, "git", "log", "--merges", "--format=%s").splitlines()) == 1
    assert v.verdict == "failed"


@pytest.mark.anyio
async def test_a_landing_that_leaves_every_command_where_it_was_stays_landed(tmp_path):
    """No false positive. The first child's command only passes once the second has landed, so it reads red at the
    baseline and green after: red to green is not a regression, and neither landing is undone."""
    repo = sibling_repo(tmp_path)
    cmd = "test -f a.txt && test -f b.txt"
    script = {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": writer("a.txt", cmds=[cmd]),
              "route.router.2": writer("b.txt", cmds=[cmd])}
    assert (await make(tmp_path, script, target=repo).run()).verdict == "completed"
    assert [e["outcome"] for e in lands(tmp_path)] == ["pass", "pass"]


@pytest.mark.anyio
async def test_a_read_only_child_and_a_failed_child_are_not_landed(tmp_path):
    repo = sibling_repo(tmp_path)
    bad = writer("bad.txt", diff=None)
    bad["output"]["status"] = "fail"
    script = {"router": routed(req(), req("codebase-design", tools=["Read"]), req("diagnosing-bugs")), "route.router.1": bad,
              "route.router.2": CHILD, "route.router.3": writer("ok.txt")}
    eng = make(tmp_path, script, target=repo)
    await eng.run()
    assert [e["node"] for e in lands(tmp_path)] == ["route.router.3"]
    assert not (repo / "bad.txt").exists()


@pytest.mark.anyio
async def test_the_router_reads_only_the_ledger_and_the_stop_word_ends_the_waves(tmp_path):
    repo = sibling_repo(tmp_path)
    noise = "N" * 4000
    script = {"router": [routed(req()), routed(req("diagnosing-bugs")), routed(done=True)],
              "route.router.1": writer("a.txt", noise=noise), "route.router.2": writer("b.txt", noise=noise, cmds=["test -f a.txt && test -f b.txt"])}
    eng = make(tmp_path, script, tweak=waves(4), target=repo)
    v = await eng.run()
    assert v.verdict == "completed"
    calls = [c for c in eng.adapter.calls if c.node == "router"]
    assert len(calls) == 3
    assert seen(calls[0]) == {"ledger": []}
    second = seen(calls[1])
    assert set(second) == {"ledger"} and noise not in calls[1].prompt
    [row] = second["ledger"]
    assert row["child"] == "route.router.1" and row["outcome"] == "pass" and row["files"] == ["a.txt"]
    assert row["landed_head"] and row["task"].startswith("Run tdd")
    third = seen(calls[2])["ledger"]
    assert [r["child"] for r in third] == ["route.router.1", "route.router.2"]
    routes = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"]
    assert [e["entry"] for e in routes] == [1, 2, 3] and routes[2]["reasons"] == ["done"] and routes[2]["routed"] == "exit:pass"
    assert (repo / "a.txt").exists() and (repo / "b.txt").exists()


@pytest.mark.anyio
async def test_zero_spawns_without_the_stop_word_still_fail_no_dispatch(tmp_path):
    eng = make(tmp_path, {"router": routed()}, target=sibling_repo(tmp_path))
    v = await eng.run()
    assert v.verdict == "failed"
    assert [e for e in ev(tmp_path, "outcome") if e["node"] == "route"][0]["reasons"] == ["no_dispatch"]


@pytest.mark.anyio
async def test_the_stop_word_is_ignored_when_the_router_also_asks_for_children(tmp_path):
    repo = sibling_repo(tmp_path)
    eng = make(tmp_path, {"router": [routed(req(), done=True), routed(done=True)], "route.router.1": writer("a.txt")},
               tweak=waves(3), target=repo)
    assert (await eng.run()).verdict == "completed"
    assert [e["entry"] for e in ev(tmp_path, "outcome") if e["node"] == "route"] == [1, 2]


@pytest.mark.anyio
async def test_a_resume_does_not_land_a_child_again(tmp_path):
    repo = sibling_repo(tmp_path / "a")
    eng = make(tmp_path / "a", {"router": routed(req()), "route.router.1": writer("a.txt")}, target=repo)
    await eng.run()
    shutil.copytree(tmp_path / "a", tmp_path / "b")
    events = read(tmp_path / "b" / "record.jsonl")
    cut = next(e["pos"] for e in events if e["event"] == "land")
    kept = [e for e in events if e["pos"] <= cut]            # the crash came right after the landing
    (tmp_path / "b" / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in kept))
    head = git.head(repo)
    eng2 = make(tmp_path / "b", {}, target=repo)
    v = await eng2.resume()
    assert v.verdict == "completed" and eng2.adapter.calls == []
    assert len(lands(tmp_path / "b")) == 1 and git.head(repo) == head


@pytest.mark.anyio
async def test_the_ledger_shows_a_conflict_and_keeps_the_conflict_text_out_of_the_router(tmp_path):
    repo = sibling_repo(tmp_path)
    script = {"router": [routed(req(), req("diagnosing-bugs")), routed(done=True)], "route.router.1": writer("same.txt", "one\n"),
              "route.router.2": writer("same.txt", "two\n")}

    eng = make(tmp_path, script, tweak=waves(3, fail="repeat"), target=repo)
    await eng.run()
    second = seen([c for c in eng.adapter.calls if c.node == "router"][1])["ledger"]
    assert [(r["child"], r["outcome"]) for r in second] == [("route.router.1", "pass"), ("route.router.2", "fail")]
    assert second[1]["reasons"] == ["merge_conflict"] and "CONFLICT" not in json.dumps(second)
    assert "stderr" in lands(tmp_path)[1]["effect"]      # the detail stays in the record, not in an artifact


@pytest.mark.anyio
async def test_a_dispatch_child_outcome_carries_its_model_effort_and_tokens_when_known(tmp_path):
    known = {**writer("a.txt"), "usage": {"tokens_in": 1200, "tokens_out": 340}}
    unknown = writer("b.txt")
    eng = make(tmp_path, {"router": routed(req(), req("diagnosing-bugs")), "route.router.1": known, "route.router.2": unknown}, target=sibling_repo(tmp_path))
    await eng.run()
    one, two = child_outs(tmp_path)["route.router.1"], child_outs(tmp_path)["route.router.2"]
    assert (one["model"], one["effort"], one["tokens_in"], one["tokens_out"]) == ("claude-gpt-5.5", "medium", 1200, 340)
    assert (two["model"], two["effort"]) == ("claude-gpt-5.5", "medium") and "tokens_in" not in two      # unknown stays absent, never zero


@pytest.mark.anyio
async def test_a_passed_writer_whose_worktree_is_gone_fails_its_landing_by_name(tmp_path):
    repo = sibling_repo(tmp_path)
    step = writer("a.txt")
    inner = step["effect"]

    def effect(r):
        inner(r)
        shutil.rmtree(r.workspace)       # the worktree vanishes before the landing
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": {**step, "effect": effect}}, target=repo)
    await eng.run()
    [land] = lands(tmp_path)
    assert land["outcome"] == "fail" and land["reasons"] == ["land_workspace_missing"] and not (repo / "a.txt").exists()


@pytest.mark.anyio
async def test_waves_that_land_nothing_exit_stalled_well_before_the_loop_max(tmp_path):
    """#93: a wave that asks for a read-only child lands nothing. Three such waves in a row end the run with
    `waves_stalled`, not `loop_max_exhausted`, long before `max` (set to 10 here) would ever bind."""
    repo = sibling_repo(tmp_path)
    reader = req("diagnosing-bugs", tools=["Read"])
    script = {"router": [routed(reader)] * 3}
    eng = make(tmp_path, script, tweak=waves(10, stall={"count": 3}), target=repo, adapter={"default": CHILD})
    v = await eng.run()
    assert v.verdict == "failed" and v.reasons == ["waves_stalled"]
    routes = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"]
    waves_out = [e for e in ev(tmp_path, "outcome") if e["node"] == "waves"][0]
    assert len(routes) == 3 and waves_out["reasons"][0] == "waves_stalled"      # stopped at the 3rd, not the 10th
    assert lands(tmp_path) == []        # a read-only child is never landable


@pytest.mark.anyio
async def test_waves_that_keep_landing_never_trip_the_stall_exit(tmp_path):
    """#93: a wave that lands something resets the stall streak, so a run making real progress can run
    past the old hand-tuned default of 6 waves without an early `waves_stalled` cutoff."""
    repo = sibling_repo(tmp_path)
    writes = [routed(req()) for _ in range(7)]
    script = {"router": [*writes, routed(done=True)], **{f"route.router.{i}": writer(f"f{i}.txt") for i in range(1, 8)}}
    eng = make(tmp_path, script, tweak=waves(10, stall={"count": 3}), target=repo)
    v = await eng.run()
    assert v.verdict == "completed"
    routes = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"]
    assert len(routes) == 8 and len(lands(tmp_path)) == 7       # past the old 6-wave default, landing every time
    assert all((repo / f"f{i}.txt").exists() for i in range(1, 8))


@pytest.mark.anyio
async def test_a_task_loop_that_names_a_new_task_each_entry_runs_past_a_small_max(tmp_path):
    """#98: with `max_by: "task"`, `max` caps one task's attempts, not the loop's entries. A loop that advances to a
    new task every entry spends none of the ceiling, so it runs past `max` (2 here) and finishes on the stop word,
    never `loop_max_exhausted`."""
    outs = [routed(req("tdd", task=str(i), tools=["Read"])) for i in (1, 2, 3)]
    script = {"router": [*outs, routed(done=True)], **{f"route.router.{i}": CHILD for i in (1, 2, 3)}}
    eng = make(tmp_path, script, tweak=waves(2, task=True), target=sibling_repo(tmp_path))
    v = await eng.run()
    routes = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"]
    assert v.verdict == "completed" and [e["entry"] for e in routes] == [1, 2, 3, 4]   # 4 entries, past max=2
    assert all("task_max_exhausted" not in (e.get("reasons") or []) for e in ev(tmp_path, "outcome"))


@pytest.mark.anyio
async def test_a_task_that_reaches_max_is_dropped_and_the_loop_goes_on_to_the_other_tasks(tmp_path):
    """#168 (run router-all24: issue 7 spent its attempts and nine issues were never asked for): the task reaches `max` (2), a diagnostic
    records it, the next wave asks task 15, and the run still fails at the end, naming the dropped task."""
    ask14, ask15 = routed(req("tdd", task="14", tools=["Read"])), routed(req("tdd", task="15", tools=["Read"]))
    eng = make(tmp_path, {"router": [ask14, ask14, ask15, routed(done=True)]},
               tweak=waves(2, task=True), target=sibling_repo(tmp_path), adapter={"default": CHILD})
    v = await eng.run()
    routes = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"]
    dropped = [e["payload"] for e in ev(tmp_path, "diagnostic") if e["source"] == "loop"]
    assert [e["entry"] for e in routes] == [1, 2, 3, 4]                      # the loop went past the dropped task
    assert [d["task_dropped"] for d in dropped] == ["14"] and dropped[0]["attempts"] == 2
    assert v.verdict == "failed" and v.reasons == ["task_exhausted:14"]      # the dropped task is never hidden


@pytest.mark.anyio
async def test_a_router_that_asks_a_dropped_task_again_ends_the_loop_task_max_exhausted(tmp_path):
    """#98, #168: asking a dropped task once more ends the loop `fail` with `task_max_exhausted`, distinct from `loop_max_exhausted`
    and `waves_stalled`, and the verdict still names the task."""
    eng = make(tmp_path, {"router": [routed(req("tdd", task="14", tools=["Read"]))] * 3},
               tweak=waves(2, task=True), target=sibling_repo(tmp_path), adapter={"default": CHILD})
    v = await eng.run()
    routes = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"]
    waves_out = [e for e in ev(tmp_path, "outcome") if e["node"] == "waves"][0]
    assert v.verdict == "failed" and v.reasons == ["task_max_exhausted", "task_exhausted:14"]
    assert [e["entry"] for e in routes] == [1, 2, 3]
    assert waves_out["outcome"] == "fail" and waves_out["reasons"][0] == "task_max_exhausted"


@pytest.mark.anyio
async def test_a_task_loop_that_names_no_task_still_terminates(tmp_path):
    """#98: an entry that names no task key counts as one unnamed attempt, so a loop that names nothing cannot run forever."""
    eng = make(tmp_path, {"router": [routed(req("tdd", tools=["Read"]))] * 2},
               tweak=waves(2, task=True), target=sibling_repo(tmp_path), adapter={"default": CHILD})
    v = await eng.run()
    routes = [e for e in ev(tmp_path, "outcome") if e["node"] == "route"]
    assert v.verdict == "failed" and v.reasons == ["task_max_exhausted"]
    assert [e["entry"] for e in routes] == [1, 2] and lands(tmp_path) == []   # two unnamed attempts, then the ceiling


@pytest.mark.anyio
async def test_the_ledger_tells_the_router_when_a_landed_childs_own_held_out_command_is_red(tmp_path):
    """Run 20261004-004606: issue 04 landed with 6 of its 27 held-out tests red, the router saw only "landed", and the
    run ended with no wave left to repair it. The landing stays (the repair builds on it); the ledger carries the verdict."""
    repo = sibling_repo(tmp_path)
    script = {"router": [routed(req(), req("diagnosing-bugs")), routed(done=True)],
              "route.router.1": writer("a.txt", cmds=["false"]), "route.router.2": writer("b.txt", cmds=["true"])}
    eng = make(tmp_path, script, tweak=waves(3, fail="repeat"), target=repo)
    await eng.run()
    ledger = seen([c for c in eng.adapter.calls if c.node == "router"][1])["ledger"]
    assert [(r["child"], r["outcome"], r["held_out"]) for r in ledger] == [("route.router.1", "pass", "fail"), ("route.router.2", "pass", "pass")]
    assert [e["outcome"] for e in lands(tmp_path)] == ["pass", "pass"]   # a red own command does not undo the landing
