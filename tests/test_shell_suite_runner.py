"""The shell-suite runner: one pytest test per `test-*.sh` beside the scripts, so the gate runs them (#394).

These cases drive the runner's seam (`tests/shell_suites.py`) on throwaway suites. The real suites run from
`tests/test_shell_suites.py`, never from here, so this file stays fast.
"""
import os
import subprocess
import sys
from pathlib import Path

from tests import shell_suites

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"


def write_suite(directory, name, body):
    path = directory / name
    path.write_text("#!/bin/bash\n" + body)
    return path


def test_a_new_suite_is_picked_up_with_no_edit_to_the_runner(tmp_path):
    write_suite(tmp_path, "test-one.sh", "echo ok one\n")
    assert [p.name for p in shell_suites.discover(tmp_path)] == ["test-one.sh"]
    write_suite(tmp_path, "test-two.sh", "echo ok two\n")
    (tmp_path / "helper.sh").write_text("echo not a suite\n")
    assert [p.name for p in shell_suites.discover(tmp_path)] == ["test-one.sh", "test-two.sh"]


def test_the_runner_finds_the_suites_shell_suites_sh_runs():
    expected = sorted(p.name for p in SCRIPTS.glob("test-*.sh"))
    assert expected, "the scripts directory holds suites"
    assert [p.name for p in shell_suites.discover()] == expected


def test_a_green_suite_passes(tmp_path):
    suite = write_suite(tmp_path, "test-green.sh", "echo 'ok   it works'\n")
    assert shell_suites.problem(suite) is None


def test_a_suite_with_a_fail_line_fails_and_shows_the_fail_lines(tmp_path):
    suite = write_suite(tmp_path, "test-red.sh", "echo 'ok   fine'\necho 'FAIL first thing broke'\necho 'FAIL second thing broke'\n")
    message = shell_suites.problem(suite)
    assert message is not None
    assert "FAIL first thing broke" in message and "FAIL second thing broke" in message
    assert "ok   fine" not in message


def test_a_suite_that_exits_non_zero_fails_and_names_the_exit_code(tmp_path):
    suite = write_suite(tmp_path, "test-boom.sh", "echo 'ok   fine'\nexit 3\n")
    message = shell_suites.problem(suite)
    assert message is not None and "exit 3" in message


def test_a_non_zero_exit_shows_the_end_of_the_output_when_no_line_says_fail(tmp_path):
    suite = write_suite(tmp_path, "test-quiet.sh", "echo 'the last words'\nexit 1\n")
    assert "the last words" in (shell_suites.problem(suite) or "")


def test_a_suite_runs_with_no_inherited_sg_variable_and_its_own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_LIVE_STATE", "/live/state")
    suite = write_suite(tmp_path, "test-env.sh", '[ -z "${SG_LIVE_STATE:-}" ] || { echo "FAIL leaked $SG_LIVE_STATE"; exit 1; }\n'
                        '[ "$HOME" != "' + os.environ.get("HOME", "") + '" ] || { echo "FAIL real HOME"; exit 1; }\n')
    assert shell_suites.problem(suite) is None


def test_a_suite_that_never_ends_fails_at_its_own_bound(tmp_path):
    suite = write_suite(tmp_path, "test-hang.sh", "sleep 30\n")
    message = shell_suites.problem(suite, timeout=0.5)
    assert message is not None and "test-hang.sh" in message and "0.5" in message


def collect(*args):
    return subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "--collect-only", "-q", "-p", "no:deepeval", *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=120).stdout


def test_the_real_runner_makes_one_test_per_suite():
    out = collect("tests/test_shell_suites.py")
    ids = [line for line in out.splitlines() if "::" in line]
    assert len(ids) == len(list(SCRIPTS.glob("test-*.sh")))


def test_a_suite_on_the_heavy_list_is_deselected_by_the_fast_battery():
    heavy = shell_suites.heavy_names()
    assert heavy, "the duration list names at least one suite"
    fast = collect("tests/test_shell_suites.py", "-m", "not heavy")
    full = collect("tests/test_shell_suites.py")
    for name in heavy:
        assert name in full, f"{name} is a suite in the full run"
        assert name not in fast, f"{name} is heavy, so the fast battery leaves it out"


def test_every_heavy_list_entry_names_a_suite_that_exists():
    present = {p.name for p in shell_suites.discover()}
    assert set(shell_suites.heavy_names()) <= present


def test_a_listed_suite_is_heavy_and_an_unlisted_one_is_not(tmp_path):
    name = sorted(shell_suites.heavy_names())[0]
    assert shell_suites.is_heavy(SCRIPTS / name)
    assert not shell_suites.is_heavy(write_suite(tmp_path, "test-fast.sh", "true\n"))


def test_a_suite_over_the_slow_line_is_slow_and_a_quick_one_is_not(tmp_path):
    slow = [name for name, seconds in shell_suites.measured().items() if seconds > shell_suites.SLOW_SECONDS]
    assert slow, "the duration list names a suite over the slow line"
    assert shell_suites.is_slow(SCRIPTS / slow[0])
    assert not shell_suites.is_slow(write_suite(tmp_path, "test-fast.sh", "true\n"))


def test_a_slow_suite_carries_a_slow_reason_so_the_cap_does_not_fail_it():
    out = collect("tests/test_shell_suites.py", "-m", "slow")
    slow = [name for name, seconds in shell_suites.measured().items() if seconds > shell_suites.SLOW_SECONDS]
    assert all(name in out for name in slow), out


def test_every_suite_has_a_measured_line_so_none_lands_unmeasured():
    listed = shell_suites.measured()
    missing = [p.name for p in shell_suites.discover() if p.name not in listed]
    assert not missing, ("each shell suite needs a line in tests/shell_suites_seconds.txt; measure it with "
                         f"`python3 -m tests.measure_shell_suites --write {' '.join(missing)}`")
