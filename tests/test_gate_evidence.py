"""Ticket #91: a failed gate outcome carries the tail of the output that explains it (ADR-0100 amendment).

The record keeps the evidence. No agent prompt reads it: the gate artifact and the ledger stay small (ADR-0113 rule 3).
"""
import pytest

from squad.gates import EVIDENCE_TAIL, GateInput, run_check
from squad.record import read
from tests.test_dispatch import CHILD, make, req, routed


def _ac(cmds, cwd="."):
    return run_check("ac-verify", {"commands_from": "spec"}, GateInput({"spec": {"ac_commands": cmds}}, {}, cwd))


def test_a_failed_acceptance_command_keeps_the_tail_of_its_output(tmp_path):
    (tmp_path / "t.sh").write_text("echo start; for i in $(seq 1 400); do echo line$i; done; echo 'AttributeError: no restock' >&2; exit 1\n")
    r = _ac(["sh t.sh"], str(tmp_path))
    assert not r.ok and list(r.evidence) == ["ac_failed:sh t.sh"]
    tail = r.evidence["ac_failed:sh t.sh"]
    assert tail.endswith("AttributeError: no restock") and len(tail) <= EVIDENCE_TAIL and "start" not in tail


def test_a_passing_command_keeps_no_evidence():
    assert _ac(["true"]).evidence == {}


@pytest.mark.anyio
async def test_the_final_gate_outcome_records_the_evidence_and_the_artifact_stays_small(tmp_path):
    bad = {"output": {"ac_commands": ["echo 'assert 1 == 2' >&2; exit 3"]}, "spend": 0.1}
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": bad})
    assert (await eng.run()).verdict == "failed"
    out = next(e for e in read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "final_gate")
    (key, text), = out["evidence"].items()
    assert key.startswith("ac-verify:ac_failed:") and text == "assert 1 == 2"
    art = (tmp_path / "artifacts" / "final_gate.1.json").read_text()
    assert "assert 1 == 2" not in art.replace("echo 'assert 1 == 2'", "")     # the command text is in the findings; the output is not


@pytest.mark.anyio
async def test_a_passing_gate_outcome_has_no_evidence_field(tmp_path):
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": CHILD})
    assert (await eng.run()).verdict == "completed"
    out = next(e for e in read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "final_gate")
    assert "evidence" not in out
