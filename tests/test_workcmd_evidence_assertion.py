"""A failed command's evidence must carry the failing assertion, not only the tail of the output.

`pytest -q` prints the progress, then the failure blocks, then the summary lines, then the durations block. A tail
alone therefore keeps the durations and drops the `E   assert` block that explains the failure. ADR-0100 rule 10 now
keeps the failing lines found in the part being dropped, then the tail, inside the same budget.
"""
from squad import workcmd
from squad.gates import GateInput, run_check

FAILING_LINE = "E       assert 1 == 2"
SUMMARY_LINE = "FAILED tests/test_mine.py::test_the_assertion - assert 1 == 2"


def _pytest_like_output():
    """Progress, the failure block, the summary, then a long durations block: the order `pytest -q` prints."""
    durations = "\n".join(f"tests/test_mine.py::test_{i} 0.0{i % 9}s call" for i in range(200))
    return (
        "collected 2 items\n"
        "tests/test_mine.py ..F\n"
        "=== FAILURES ===\n"
        "____________________________ test_the_assertion ____________________________\n"
        "    def test_the_assertion():\n"
        ">       assert 1 == 2\n"
        f"{FAILING_LINE}\n"
        "=========================== short test summary info ============================\n"
        f"{SUMMARY_LINE}\n"
        "============================ slowest 3 durations =============================\n"
        f"{durations}\n"
        "2 failed in 1.23s\n"
    )


def _failing_script(tmp_path):
    """A command whose output is the pytest-like text. The assertion sits well above the kept tail."""
    (tmp_path / "out.txt").write_text(_pytest_like_output())
    (tmp_path / "t.sh").write_text("cat out.txt\nexit 1\n")
    return "sh t.sh"


def test_a_failed_command_keeps_the_failing_assertion_from_the_dropped_part(tmp_path):
    r = workcmd.run(_failing_script(tmp_path), str(tmp_path))
    assert not r.ok
    ev = r.evidence
    assert FAILING_LINE in ev                                  # the assertion that explains the failure
    assert SUMMARY_LINE in ev                                  # and the summary line that names the test
    assert ev.endswith("2 failed in 1.23s")                     # the end of the output is still the tail's job
    assert len(ev) <= workcmd.EVIDENCE_TAIL
    assert "elided" in ev                                       # the dropped part is named, never silent


def test_the_gate_records_the_same_evidence_as_the_command_runner(tmp_path):
    cmd = _failing_script(tmp_path)
    gate = run_check("ac-verify", {"commands_from": "spec"}, GateInput({"spec": {"ac_commands": [cmd]}}, {}, str(tmp_path)))
    assert not gate.ok and list(gate.findings) == [f"ac_failed:{cmd}"]
    ev = gate.evidence[f"ac_failed:{cmd}"]
    assert ev == workcmd.run(cmd, str(tmp_path)).evidence
    assert FAILING_LINE in ev


def test_an_output_with_no_failing_line_keeps_the_plain_tail(tmp_path):
    script = "printf 'start\\n'; for i in $(seq 1 400); do echo line$i; done; exit 1\n"
    r = workcmd.run(script, str(tmp_path))
    assert not r.ok and r.evidence.endswith("line400")
    assert len(r.evidence) <= workcmd.EVIDENCE_TAIL and "start" not in r.evidence


def test_the_failing_test_name_survives_a_long_detail_block(tmp_path):
    """A noisy failure's detail lines fill the kept budget before the summary that names the failing test."""
    target = "FAILED tests/test_hub_mobile.py::test_mux_serves_the_mobile_page - TimeoutError: timed out"
    noisy = "".join(f"E       detail line {i:03d} " + "x" * 90 + "\n" for i in range(40))
    text = (
        "collected 2 items\n=== FAILURES ===\n"
        + "_" * 40 + " test_noisy " + "_" * 40 + "\n"
        + noisy
        + "=========================== short test summary info ============================\n"
        + target + "\n"
        + "tests/x.py::test_slow 0.10s call\n" * 200
        + "2 failed in 4.2s\n"
    )
    (tmp_path / "out.txt").write_text(text)
    (tmp_path / "t.sh").write_text("cat out.txt\nexit 1\n")
    r = workcmd.run("sh t.sh", str(tmp_path))
    assert not r.ok and target in r.evidence
    assert len(r.evidence) <= workcmd.EVIDENCE_TAIL
