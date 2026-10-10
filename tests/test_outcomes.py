"""Guards for issue #147: one module records an outcome. No Engine is built here."""
import pytest

from squad.engine_errors import Halt
from squad.facts import FactStore
from squad.outcomes import Outcomes
from squad.record import Record


def make(tmp_path, stop_after=None):
    rec = Record(tmp_path / "record.jsonl")
    facts = FactStore([], ["a", "main"], "r1", clock=lambda: 1.0)
    seen = []
    out = Outcomes(rec, facts, refresh=lambda item: seen.append(item), stop_after=stop_after)
    return out, rec, facts, seen


F = dict(reasons=[], artifact=None, spend=None, turns=None, cap=None, elapsed=None)


def test_a_node_outcome_lands_in_the_record_the_maps_its_fact_and_the_refresh(tmp_path):
    out, rec, facts, seen = make(tmp_path)
    ev = out.node("a", 1, 3, "pass", **F)
    assert rec.events[-1] is ev and ev["item"] == 3
    assert out.done[("a", 1, 3)] is ev and out.last[("a", 3)] == "pass"
    assert facts.read("outcome:a", 3) == "pass"
    assert seen == [3]


def test_stop_after_halts_once_the_outcome_is_recorded(tmp_path):
    out, rec, _, _ = make(tmp_path, stop_after="a")
    with pytest.raises(Halt):
        out.node("a", 1, None, "pass", **F)
    assert out.last[("a", None)] == "pass" and rec.events[-1]["event"] == "outcome"


def test_a_child_outcome_is_recorded_with_its_parent_and_makes_no_fact(tmp_path):
    # A child is not a graph node, so the fact registry refuses `outcome:<child>`.
    # Its outcome reaches readers through `done` and `last`, never through a fact. No refresh runs.
    out, rec, facts, seen = make(tmp_path)
    ev = out.child("kid", "fail", ["x"], None, {"spend": 0.5}, parent="main", depth=1)
    assert ev["parent"] == "main" and ev["depth"] == 1 and ev["entry"] == 1 and ev["spend"] == 0.5 and ev["turns"] is None
    assert out.done[("kid", 1, None)] is ev and out.last[("kid", None)] == "fail"
    assert not facts.registered("outcome:kid") and seen == []


def test_absorb_rebuilds_state_from_a_recorded_event_and_writes_nothing(tmp_path):
    out, rec, facts, seen = make(tmp_path)
    ev = dict(event="outcome", pos=7, node="a", entry=2, outcome="fail", item=None, **F)
    out.absorb(ev)
    assert out.done[("a", 2, None)] is ev and out.last[("a", None)] == "fail"
    assert facts.read("outcome:a") == "fail" and seen == [None] and rec.events == []


def test_the_defect_fallback_takes_the_next_entry_of_the_root_in_run_and_resume(tmp_path):
    out, rec, _, _ = make(tmp_path)
    assert out.defect("main", "boom")["entry"] == 1          # a run with no recorded root entry
    ev = out.defect("main", "again")                         # a resume: the same rule, after the recorded entry
    assert ev["entry"] == 2 and ev["outcome"] == "fail" and ev["reasons"] == ["defect:again"]


def test_the_outcomes_module_returns_the_recorded_event_or_none(tmp_path):
    out, _, _, _ = make(tmp_path)
    assert out.event("kid") is None
    ev = out.child("kid", "fail", [], None, {}, parent="main", depth=1)
    assert out.event("kid") is ev and out.event("kid", 2) is None


def test_nothing_but_the_outcomes_module_writes_done_or_last():
    import re
    from pathlib import Path
    pat = re.compile(r"\.(done|last)\[[^\]]*\]\s*=[^=]|\.(done|last)\s*=(?!=|\s*self\.outcomes)")
    # `last` in sdk_adapter.py and dashboard_server.py is a timestamp of the last progress or request, not the outcome map.
    bad = [str(p) for p in Path("squad").rglob("*.py") if p.name not in ("outcomes.py", "sdk_adapter.py", "dashboard_server.py") and pat.search(p.read_text())]
    assert bad == [], f"write through squad.outcomes.Outcomes: {bad}"


# ---- issue #148: replay reads the record through the outcome module

def _facts(eng):
    return {k: f.value for k, f in eng.facts.values.items()}


@pytest.mark.anyio
@pytest.mark.parametrize("stop", ["a", "b"])
async def test_a_resumed_run_rebuilds_the_facts_and_the_outcomes_of_the_live_run(tmp_path, stop):
    """Property: absorbing a recorded run gives the facts, `done` and `last` the live run left."""
    from squad.adapter import ScriptedAdapter
    from squad.engine import Engine
    from squad.graph import from_doc
    from tests.flows import agent, doc
    d = doc([agent(n, output_key=k, next={"fail": "exit:fail"}) for n, k in (("a", "x"), ("b", "y"), ("c", "z"))], ["a", "b", "c"])
    script = {n: {"output": {k: 1}, "spend": 0.1} for n, k in (("a", "x"), ("b", "y"), ("c", "z"))}
    live = Engine(from_doc(d), ScriptedAdapter(script, repeat_last=True), tmp_path, options={"stop_after": stop})
    await live.run()
    again = Engine(from_doc(d), ScriptedAdapter(script, repeat_last=True), tmp_path)
    again.run_id = again.facts.run_id = live.run_id
    assert again.replay.absorb_all() == []
    again._seed_facts()
    assert _facts(again) == _facts(live)
    assert again.outcomes.last == live.outcomes.last
    assert {k: v["outcome"] for k, v in again.outcomes.done.items()} == {k: v["outcome"] for k, v in live.outcomes.done.items()}


def test_the_outcomes_module_answers_whether_an_outcome_is_recorded(tmp_path):
    out, _, _, _ = make(tmp_path)
    assert not out.recorded("kid")
    out.child("kid", "fail", [], None, {}, parent="main", depth=1)
    assert out.recorded("kid") and not out.recorded("kid", entry=2)


def test_replay_never_touches_done_last_or_the_private_refresh():
    from pathlib import Path
    src = (Path(__file__).parent.parent / "squad" / "replay.py").read_text()
    for banned in (".done", ".last", "_refresh"):
        assert banned not in src, banned
