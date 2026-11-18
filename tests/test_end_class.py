"""The end-reason class of a failed child: a structured field, read by the session and folded into the verdict (#2217)."""

import json
from pathlib import Path
from types import SimpleNamespace

from squad import session
from squad import verdict as V
from squad.outcomes import Outcomes
from squad.record import Record
from squad.runners.children import Children
from tests.test_verdict import events, out


def test_a_fail_artifact_names_its_class_only_by_the_structured_field():
    assert session.end_class({"status": "fail", "end_class": "owner-only", "reasons": ["gate red"]}) == "owner-only"
    assert session.end_class({"status": "fail", "end_class": "does-not-reproduce"}) == "does-not-reproduce"
    assert session.end_class({"status": "fail", "reasons": ["owner-only: criterion (1) needs a credential"]}) is None
    assert session.end_class({"status": "fail", "end_class": "gate"}) is None
    assert session.end_class({"status": "pass", "end_class": "owner-only"}) is None


def test_a_failed_child_with_an_owner_only_class_puts_it_in_the_verdict_record():
    evs = events(dict(out("a", "fail", ["owner-only: criterion (1) needs a credential"]), end_class="owner-only"), out("main", "fail", ["owner-only: criterion (1) needs a credential"]))
    v = V.fold(evs)
    assert v.verdict == "failed"
    assert v.end_class == "owner-only"
    assert v.event_fields()["end_class"] == "owner-only"


def test_a_gate_failure_carries_no_end_class_in_the_verdict_record():
    evs = events(out("a", "fail", ["ac-verify:no_ac_commands"]), out("main", "fail", ["ac-verify:no_ac_commands"]))
    v = V.fold(evs)
    assert v.verdict == "failed"
    assert v.end_class is None
    assert "end_class" not in v.event_fields()


class Engine:
    """The engine fields `Children.settle` reads, over a real `Record`: the wiring is the one a run uses, and the run
    truth is the record file. A child that may not write needs no worktree, so no git runs here."""

    def __init__(self, path):
        self.run_id = "run-1"
        self.has_git = False   # tools are readers, so `isolated` is False and no worktree is made
        self.limits = {}
        self.record = Record(path)
        self.artifacts = SimpleNamespace(put=lambda *a: "artifact", view=lambda item: {})
        self.facts = SimpleNamespace(values={})
        self.dispatch = SimpleNamespace(children={}, held={})
        self.outcomes = Outcomes(self.record, self.facts, refresh=lambda item: None)
        self.target = Path(path).parent
        self._ws_snapshot = lambda d, scope: None

    def child(self, name):
        self.dispatch.children[name] = {"parent": "main", "depth": 1, "definition": {}, "pos": len(self.dispatch.children)}
        return Children(self)


def settle(path, name, output, engine=None):
    """One child's call settled through `Children.settle`, its outcome on the run's record. Returns the engine."""
    e = engine or Engine(path)
    if engine is None:
        e.record.append("run_start", run_id="run-1", graph_name="g", graph_hash="h", engine_options={},
                        root="main", plan=["main"])
    children = e.child(name)
    res = SimpleNamespace(output=output, stop_reason=None, ended=None, usage={}, spend_source=None, spend=0.0, turns=1,
                          session_id="s1")
    call = SimpleNamespace(result=res, res=SimpleNamespace(amount=1.0), elapsed=1.0)
    d = {"tools": [], "workspace": ".", "effort": "low", "model": "m", "stalls": 1, "skill": {"name": "x"}}
    children.settle(SimpleNamespace(node={"checks": []}), name, d, "/nonexistent", call, "m")
    return e


def verdict_event(path):
    """Fold the record the run wrote and append its verdict event, as the engine does at the end of a run."""
    path = Path(path)
    e = SimpleNamespace(record=Record(path))
    v = V.fold(e.record.events)
    e.record.append("verdict", **v.event_fields())
    line = [json.loads(x) for x in path.read_text().splitlines() if json.loads(x)["event"] == "verdict"][-1]
    return v, line


def test_a_fail_artifact_that_names_its_class_reaches_the_verdict_record(tmp_path):
    """#2217 review debt: the wiring is driven, not rebuilt by hand: a child's fail artifact with `end_class` goes through
    `Children.settle`, and the verdict event on record.jsonl carries the class."""
    path = tmp_path / "record.jsonl"
    settle(path, "a", {"status": "fail", "end_class": "owner-only", "reasons": ["owner-only: needs a credential"]})
    v, line = verdict_event(path)
    assert (v.verdict, v.end_class) == ("failed", "owner-only")
    assert line["end_class"] == "owner-only"


def test_two_children_that_disagree_on_the_class_give_no_class(tmp_path):
    """The run names one class or none: two failing children with different classes are no signal to freeze the issue."""
    path = tmp_path / "record.jsonl"
    e = settle(path, "a", {"status": "fail", "end_class": "owner-only", "reasons": ["owner-only: needs a credential"]})
    settle(path, "b", {"status": "fail", "end_class": "does-not-reproduce", "reasons": ["does not reproduce"]}, e)
    v, line = verdict_event(path)
    assert v.verdict == "failed"
    assert v.end_class is None
    assert "end_class" not in line
