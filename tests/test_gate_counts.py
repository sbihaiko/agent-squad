"""Issue #80: a gate records how much of the suite passed, beside whether it passed.

One bit is not progress. A gate command that ends `2 failed, 16 passed in 8.0s` is red, and 16 of 18 is the number that
shows the work moved. The record therefore keeps the counts of each command that printed a pytest summary, and the
verdict is untouched: the exit code stays the only pass/fail rule (ADR-0019), and the counts are evidence a reader
consults, never a threshold any gate reads.
"""
import pytest

from squad.gates import GateInput, run_check
from squad.record import read
from tests.test_dispatch import CHILD, make, req, routed


def _ac(cmds, cwd="."):
    return run_check("ac-verify", {"commands_from": "spec"}, GateInput({"spec": {"ac_commands": cmds}}, {}, cwd))


def test_each_counting_command_reports_its_own_share():
    cmd = 'echo "2 failed, 16 passed in 8.0s"; exit 1'
    r = _ac([cmd])
    assert r.counts == {cmd: {"passed": 16, "failed": 2, "total": 18}}
    assert not r.ok, "the counts never soften the verdict"


def test_a_gate_whose_commands_never_ran_pytest_reports_no_counts():
    """A gate over a shell command, or over a suite that printed nothing countable, reports no share: an empty map keeps
    the panel from drawing a zero over work it never measured."""
    assert _ac(["true", "echo done"]).counts == {}


def test_only_the_commands_that_printed_a_summary_appear():
    counting = 'echo "4 passed in 0.2s"'
    r = _ac([counting, "echo nothing here"])
    assert list(r.counts) == [counting] and r.counts[counting] == {"passed": 4, "total": 4}


@pytest.mark.anyio
async def test_the_final_gate_outcome_records_the_counts_and_the_artifact_stays_small(tmp_path):
    bad = {"output": {"ac_commands": ['echo "2 failed, 16 passed in 8.0s"; exit 1']}, "spend": 0.1}
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": bad})
    assert (await eng.run()).verdict == "failed"
    out = next(e for e in read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "final_gate")
    (cmd, counts), = out["counts"].items()
    assert counts == {"passed": 16, "failed": 2, "total": 18} and cmd.startswith("echo")
    art = (tmp_path / "artifacts" / "final_gate.1.json").read_text()
    assert '"counts"' not in art          # the artifact the ledger reads stays small; the record holds the counts


@pytest.mark.anyio
async def test_a_green_gate_outcome_carries_no_counts_field(tmp_path):
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": CHILD})
    assert (await eng.run()).verdict == "completed"
    out = next(e for e in read(tmp_path / "record.jsonl") if e["event"] == "outcome" and e["node"] == "final_gate")
    assert "counts" not in out
