"""Ticket #107: pause and resume a launched run through a control file."""
import pytest

from squad.adapter import ScriptedAdapter
from squad.control import Control
from squad.engine import Engine
from squad.graph import from_doc
from squad.record import read
from tests.flows import agent, doc

OK = {"output": {"x": 1, "y": 1, "z": 1}, "spend": 0.1}


def chain():
    nodes = [agent(n, output_key=k, next={"fail": "exit:fail"}) for n, k in (("a", "x"), ("b", "y"), ("c", "z"))]
    return doc(nodes, ["a", "b", "c"])


def make(tmp_path):
    ad = ScriptedAdapter({"a": OK, "b": OK, "c": OK}, repeat_last=True)
    return Engine(from_doc(chain()), ad, tmp_path), ad


def events(tmp_path, kind):
    return [e for e in read(tmp_path / "record.jsonl") if e["event"] == kind]


def test_a_control_file_folds_to_the_nodes_it_holds(tmp_path):
    c = Control(tmp_path)
    assert not c.held("a")
    c.append("pause", nodes=["a"])
    assert c.held("a") and not c.held("b")
    c.append("resume", nodes=["a"])
    assert not c.held("a")
    c.append("pause")
    assert c.held("a") and c.held("b")
    c.append("resume", nodes=["b"])
    assert c.held("a") and not c.held("b")      # a released node stays released under a whole-run pause
    c.append("resume")
    assert not c.held("a") and not c.held("b")


def test_a_damaged_control_line_is_skipped(tmp_path):
    (tmp_path / "control.jsonl").write_text('not json\n{"action":"pause","nodes":["a"]}\n{"action":"fly"}\n[1]\n')
    assert Control(tmp_path).held("a")


@pytest.mark.anyio
async def test_a_held_node_halts_the_run_before_it_enters_and_records_where_it_stands(tmp_path):
    eng, ad = make(tmp_path)
    Control(tmp_path).append("pause", nodes=["b"])
    v = await eng.run()
    assert [c.node for c in ad.calls] == ["a"]
    assert (v.verdict, v.exit_code) == ("stopped", 30) and "paused:b" in v.reasons
    p = events(tmp_path, "paused")[0]
    assert (p["node"], p["held"], p["scope"]) == ("b", ["b"], "nodes") and p["completed"] == ["a"]


@pytest.mark.anyio
async def test_a_whole_run_pause_holds_the_first_node(tmp_path):
    eng, ad = make(tmp_path)
    Control(tmp_path).append("pause")
    v = await eng.run()
    assert ad.calls == [] and v.exit_code == 30
    assert events(tmp_path, "paused")[0]["scope"] == "run"


@pytest.mark.anyio
async def test_a_resume_request_then_a_resume_continues_the_run_under_the_same_id(tmp_path):
    eng, _ = make(tmp_path)
    Control(tmp_path).append("pause", nodes=["b"])
    await eng.run()
    Control(tmp_path).append("resume", nodes=["b"])
    eng2, ad2 = make(tmp_path)
    v = await eng2.resume()
    assert v.verdict == "completed" and [c.node for c in ad2.calls] == ["b", "c"]
    assert {e["run_id"] for e in events(tmp_path, "run_start") + events(tmp_path, "resume")} == {eng.run_id}


@pytest.mark.anyio
async def test_the_resume_command_releases_a_hold_it_finds(tmp_path):
    eng, _ = make(tmp_path)
    Control(tmp_path).append("pause")
    await eng.run()
    eng2, ad2 = make(tmp_path)
    v = await eng2.resume(release=True)
    assert v.verdict == "completed" and [c.node for c in ad2.calls] == ["a", "b", "c"]


@pytest.mark.anyio
async def test_a_resume_with_the_hold_still_in_force_pauses_again_and_runs_nothing(tmp_path):
    eng, _ = make(tmp_path)
    Control(tmp_path).append("pause", nodes=["b"])
    await eng.run()
    eng2, ad2 = make(tmp_path)
    v = await eng2.resume()
    assert ad2.calls == [] and v.exit_code == 30
    assert len(events(tmp_path, "paused")) == 2


@pytest.mark.anyio
async def test_a_run_with_no_control_file_is_unchanged(tmp_path):
    eng, ad = make(tmp_path)
    v = await eng.run()
    assert v.verdict == "completed" and [c.node for c in ad.calls] == ["a", "b", "c"]
    assert not (tmp_path / "control.jsonl").exists() and events(tmp_path, "paused") == []


# --- dispatch children ---

def _dispatch_make(tmp_path):
    from tests.test_dispatch import CHILD, make, req, routed
    ran = []

    def child(r):
        ran.append(r.node)
        return CHILD

    script = {"router": routed(req(), req(), req("codebase-design", tools=["Read"], after=[0])),
              "route.router.1": child, "route.router.2": child, "route.router.3": child}
    return make(tmp_path, script), ran


@pytest.mark.anyio
async def test_a_held_child_does_not_run_its_sibling_does_and_a_child_after_it_is_held_too(tmp_path):
    eng, ran = _dispatch_make(tmp_path)
    Control(tmp_path).append("pause", nodes=["route.router.1"])
    v = await eng.run()
    assert ran == ["route.router.2"]
    assert (v.verdict, v.exit_code) == ("stopped", 30)
    p = events(tmp_path, "paused")[0]
    assert sorted(p["held"]) == ["route.router.1", "route.router.3"]
    assert not any(e["node"] in ("route.router.1", "route.router.3") for e in events(tmp_path, "outcome"))


@pytest.mark.anyio
async def test_a_resume_runs_the_held_children_and_not_the_one_that_ran(tmp_path):
    eng, _ = _dispatch_make(tmp_path)
    Control(tmp_path).append("pause", nodes=["route.router.1"])
    await eng.run()
    eng2, ran2 = _dispatch_make(tmp_path)
    v = await eng2.resume(release=True)
    assert v.verdict == "completed" and sorted(ran2) == ["route.router.1", "route.router.3"]
