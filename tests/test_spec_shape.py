"""Ticket #53: the spec-shape gate. A bad spec shape fails here and repeats the spec, not ac-verify (ADR-0100 rule 2, ADR-0018).

Guards: a spec with no ac_commands fails spec-shape and the spec node runs again; ac_commands beside spec gets a finding that
names the misplacement; a good spec passes and a subject run still completes; the check id is in the registry and the fixture.
"""
import pytest

from squad import record, subject
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.gates import CHECKS, GATE_REGISTRY, GateInput, run_check
from squad.graph import load
from tests.helpers import five
from tests.test_subject_run import fake_subject, write_calc

pytestmark = pytest.mark.anyio

GOOD = "def add(a, b):\n    return a + b\n"


def check(arts):
    return run_check("spec-shape", {}, GateInput(arts, {}))


def test_a_good_spec_passes():
    assert check({"spec": {"summary": "s", "ac_commands": ["true"]}}).ok


@pytest.mark.parametrize("spec,finding", [
    ({"summary": "s"}, "ac_commands_missing"),
    ({"summary": "s", "ac_commands": []}, "ac_commands_empty"),
    ({"summary": "s", "ac_commands": "true"}, "ac_commands_not_a_list_of_strings"),
    ({"summary": "s", "ac_commands": ["true", 3]}, "ac_commands_not_a_list_of_strings"),
    ({"summary": "s", "ac_commands": ["true", ""]}, "ac_commands_not_a_list_of_strings"),
    ("text", "spec_not_an_object"),
    (None, "spec_missing"),
])
def test_a_bad_shape_fails_with_a_named_finding(spec, finding):
    r = check({"spec": spec} if spec is not None else {})
    assert not r.ok and r.findings == [finding]


def test_ac_commands_beside_spec_is_named_as_misplaced():
    arts = {"spec_draft": {"status": "pass", "spec": {"summary": "s"}, "ac_commands": ["true"]}, "spec": {"summary": "s"}}
    r = check(arts)
    assert not r.ok and r.findings == ["ac_commands_beside_spec"]


def test_the_check_id_is_registered_and_listed_in_the_fixture():
    assert GATE_REGISTRY["spec-shape"] == [] and "spec-shape" in CHECKS
    assert {"id": "spec-shape", "params": []} in five()["gates"]


def engine(tmp_path, first=None):
    repo, bench = fake_subject(tmp_path)
    prep = subject.prepare(1, repo, bench, tmp_path / "out")
    good = {"output": {"spec": {"ac_commands": prep.ac_commands}}, "spend": 0.1, "turns": 2}
    sc = {"spec_draft": ([first] if first else []) + [good],
          "actor": {"output": {"diff": {"files": [{"path": "billing/calc.py"}]}}, "spend": 0.2, "turns": 5, "effect": write_calc(GOOD)}}
    return prep, Engine(load(prep.graph_path), ScriptedAdapter(sc), prep.run_dir, target=prep.target, graph_path=prep.graph_path)


def outcomes(prep, node):
    return [e for e in record.read(prep.run_dir / "record.jsonl") if e["event"] == "outcome" and e["node"] == node]


async def test_a_good_spec_still_completes_a_subject_run(tmp_path):
    prep, eng = engine(tmp_path)
    v = await eng.run()
    assert v.verdict == "completed", v.message
    assert [e["outcome"] for e in outcomes(prep, "spec_gate")] == ["pass"]


async def test_a_spec_with_no_ac_commands_fails_the_gate_not_ac_verify_and_the_spec_runs_again(tmp_path):
    prep, eng = engine(tmp_path, {"output": {"spec": {"summary": "s"}}, "spend": 0.1, "turns": 2})
    v = await eng.run()
    assert v.verdict == "completed", v.message
    gate = outcomes(prep, "spec_gate")
    assert [e["outcome"] for e in gate] == ["fail", "pass"] and gate[0]["reasons"] == ["spec-shape:ac_commands_missing"]
    assert len(outcomes(prep, "spec_draft")) == 2 and [e["outcome"] for e in outcomes(prep, "ac_verify")] == ["pass"]


async def test_ac_commands_beside_spec_fails_with_a_finding_that_names_it(tmp_path):
    prep, eng = engine(tmp_path, {"output": {"status": "pass", "spec": {"summary": "s"}, "ac_commands": ["true"]}, "spend": 0.1, "turns": 2})
    v = await eng.run()
    assert v.verdict == "completed", v.message
    assert outcomes(prep, "spec_gate")[0]["reasons"] == ["spec-shape:ac_commands_beside_spec"]
