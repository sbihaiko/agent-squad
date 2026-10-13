"""Issue #80: the share of a suite that passes is a fact of the command's output.

A gate command that ends `24 failed, 2100 passed, 21 skipped in 103.09s` leaves one bit in the record, and one bit is
what the owner cannot read progress from: 16 of 24 is progress while the gate is red. `workcmd` therefore reports the
counts of the last pytest summary line of the output beside the exit code.

The exit code stays the only pass/fail rule (ADR-0019). The counts are evidence: a red command with counts is still
red, and a command whose output carries no summary reports none -- never a zero, which would read as "nothing passes".
"""
import subprocess

import pytest

from squad import workcmd


@pytest.mark.parametrize("line, want", [
    ("1 failed, 24 passed in 3.1s", {"passed": 24, "failed": 1, "total": 25}),
    ("2100 passed, 21 skipped in 103.09s (0:01:43)", {"passed": 2100, "skipped": 21, "total": 2100}),
    ("3 errors, 1 failed in 0.5s", {"errors": 3, "failed": 1, "total": 4}),
    ("1 error in 0.5s", {"errors": 1, "total": 1}),
    ("5 passed, 1 xfailed, 2 deselected, 3 warnings in 1.2s", {"passed": 5, "xfailed": 1, "deselected": 2, "total": 5}),
    ("= 4 passed, 1 skipped in 2.20s =", {"passed": 4, "skipped": 1, "total": 4}),
])
def test_counts_are_read_from_the_summary_however_pytest_spells_it(line, want):
    assert workcmd.counts_of(line) == want


def test_a_run_with_no_tests_and_a_command_that_is_not_pytest_report_no_counts():
    """`no tests ran` is not a share of anything, and a command that never ran pytest has no counts at all. Reporting a
    zero here would draw 0% on the panel for a suite that has nothing to do with pytest."""
    assert workcmd.counts_of("no tests ran in 0.01s") is None
    assert workcmd.counts_of("GO111MODULE=on go test ./...") is None
    assert workcmd.counts_of("") is None
    assert workcmd.counts_of("FAILED tests/test_a.py::test_b - AssertionError: assert 1 == 2") is None


def test_the_last_summary_of_an_output_wins():
    """A suite that prints several (a wrapper running pytest twice) reports the state it ended in."""
    out = "1 failed in 0.1s\n\n1 passed in 0.2s\n"
    assert workcmd.counts_of(out) == {"passed": 1, "total": 1}


def test_the_evidence_block_of_a_failed_command_keeps_its_counts():
    """The kept tail is the end of the output, which is where pytest's summary sits, so the failure that made the gate
    red still carries the number that shows how far the work got."""
    out = "FAILED tests/test_x.py::test_y - assert 1 == 2\n" + "x" * (workcmd.EVIDENCE_TAIL * 2) + "\n23 failed, 16 passed in 8.0s\n"
    got = workcmd.counts_of(workcmd.evidence_of(out))
    assert got == {"passed": 16, "failed": 23, "total": 39}


def test_run_reports_the_counts_of_a_green_command(tmp_path):
    r = workcmd.run('echo "16 passed in 0.4s"', str(tmp_path))
    assert r.ok and r.counts == {"passed": 16, "total": 16}


def test_run_reports_the_counts_of_a_red_command_beside_its_reason(tmp_path):
    r = workcmd.run('echo "2 failed, 16 passed in 8.0s"; exit 1', str(tmp_path))
    assert not r.ok and r.counts == {"passed": 16, "failed": 2, "total": 18}
    assert r.evidence  # the exit code keeps its evidence; the counts are added beside it, never instead of it


def test_run_reports_no_counts_for_a_command_that_is_not_pytest(tmp_path):
    r = workcmd.run('echo "all good"', str(tmp_path))
    assert r.ok and r.counts is None


def test_a_timeout_and_a_malformed_command_claim_no_counts(tmp_path):
    assert workcmd.run("true |", str(tmp_path)).counts is None            # unparsable: nothing ran
    r = workcmd.run('echo "1 passed in 0.1s"; sleep 5', str(tmp_path), timeout=0.5)
    assert not r.ok and r.counts is None and "timeout" in r.evidence       # nothing was measured, so nothing is claimed


def test_the_summary_line_is_read_from_bytes_too():
    """`run` hands `evidence_of` the raw stdout+stderr, which arrives as bytes."""
    assert workcmd.counts_of(subprocess.run(["/bin/sh", "-c", 'echo "7 passed in 0.1s"'], capture_output=True).stdout) == {"passed": 7, "total": 7}
