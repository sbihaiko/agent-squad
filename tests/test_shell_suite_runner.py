"""The shell-suite runner: one pytest test per `test-*.sh` beside the scripts, so the gate runs them (#394).

These cases drive the runner's seam (`tests/shell_suites.py`) on throwaway suites. The real suites run from
`tests/test_shell_suites.py`, never from here, so this file stays fast.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import shell_suites, slowcap
from tests.gitrepo import make_repo, sh, sh_result

pytestmark = pytest.mark.macos_bound   # the suites it collects are the loop's own, macOS-only (ADR-0139)
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


def test_a_suite_carries_its_measured_seconds_in_its_own_header(tmp_path):
    """The measure sits in the suite it measures (#2442): a suite added beside a sibling's is its own file, so no branch
    writes the line of another."""
    write_suite(tmp_path, "test-headed.sh", "# seconds: 2.50\necho ok\n")
    write_suite(tmp_path, "test-plain.sh", "echo ok\n")
    assert shell_suites.measured(tmp_path) == {"test-headed.sh": 2.5}


def test_set_seconds_writes_the_measure_into_one_suite_s_own_header(tmp_path):
    """`measure_shell_suites --write` touches only the suite it measured, so it never rewrites a sibling's line."""
    suite = write_suite(tmp_path, "test-fresh.sh", "echo ok\n")
    shell_suites.set_seconds(suite, 3.25)
    assert shell_suites.measured(tmp_path) == {"test-fresh.sh": 3.25}
    shell_suites.set_seconds(suite, 4.5)   # a second measure replaces the header, never adds a second one
    assert shell_suites.measured(tmp_path) == {"test-fresh.sh": 4.5}
    assert suite.read_text().count(shell_suites.SECONDS_HEADER) == 1


def test_two_branches_that_each_add_a_shell_suite_merge_and_the_measure_holds_both(tmp_path):
    """The expected behavior (#2442): a branch that adds a suite writes that suite and no shared list, so a sibling's
    addition merges clean, and the merged tree measures both."""
    repo = make_repo(tmp_path / "merge")
    scripts = repo / "contrib" / "squad-goal" / "scripts"
    scripts.mkdir(parents=True)
    g = lambda *a: sh(repo, "git", "-c", "user.email=t@t", "-c", "user.name=t", *a)  # noqa: E731

    def add(name: str) -> None:
        (scripts / name).write_text("#!/bin/bash\n# seconds: 1.00\necho ok\n")

    add("test-a.sh")
    g("add", "-A")
    g("commit", "-q", "-m", "base")
    g("checkout", "-q", "-b", "one")
    add("test-b.sh")
    g("add", "-A")
    g("commit", "-q", "-m", "one")
    g("checkout", "-q", "main")
    g("checkout", "-q", "-b", "two")
    add("test-c.sh")
    g("add", "-A")
    g("commit", "-q", "-m", "two")
    g("checkout", "-q", "one")
    done = sh_result(repo, "git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "--no-edit", "two")
    assert done.returncode == 0, f"the merge of two added suites conflicted: {done.stdout}{done.stderr}"
    assert shell_suites.measured(scripts) == {"test-a.sh": 1.0, "test-b.sh": 1.0, "test-c.sh": 1.0}


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


@pytest.mark.heavy
def test_the_real_runner_makes_one_test_per_suite():
    out = collect("tests/test_shell_suites.py")
    ids = [line for line in out.splitlines() if "::" in line]
    assert len(ids) == len(list(SCRIPTS.glob("test-*.sh")))


@pytest.mark.heavy
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


@pytest.mark.heavy
def test_a_slow_suite_carries_a_slow_reason_so_the_cap_does_not_fail_it():
    out = collect("tests/test_shell_suites.py", "-m", "slow")
    slow = [name for name, seconds in shell_suites.measured().items() if seconds > shell_suites.SLOW_SECONDS]
    assert all(name in out for name in slow), out


def test_every_suite_has_a_measured_line_so_none_lands_unmeasured():
    listed = shell_suites.measured()
    missing = [p.name for p in shell_suites.discover() if p.name not in listed]
    assert not missing, ("each shell suite needs its own `# seconds:` header; measure it with "
                         f"`python3 -m tests.measure_shell_suites --write {' '.join(missing)}`")


def repo_cap_seconds():
    """The cap this repository sets for one test call, read from pyproject.toml, so the lines here are anchored to it."""
    m = re.search(r'^slow_cap_seconds = "([\d.]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    assert m, "pyproject.toml sets slow_cap_seconds"
    return float(m.group(1))


def test_the_slow_line_is_the_cap_divided_by_the_slowdown_the_cap_excuses():
    """The cap excuses a call over its line only up to the slowdown `contention_factor` measures. A suite whose own measured
    time crosses the cap under that load has an outcome the load decides, so it carries a slow reason (#736)."""
    assert shell_suites.SLOW_SECONDS == repo_cap_seconds() / slowcap.MAX_CONTENTION_FACTOR
    assert shell_suites.SLOW_SECONDS * slowcap.MAX_CONTENTION_FACTOR == repo_cap_seconds()


def test_a_suites_bound_is_its_measured_time_times_the_slowdown_the_cap_excuses(monkeypatch):
    """A bound tighter than the load this machine may meet fails a suite for the machine's work, not for its own (#736)."""
    monkeypatch.setattr(shell_suites, "measured", lambda: {"test-long.sh": 250.0})
    monkeypatch.setattr(slowcap, "pressure_factor", lambda: 1.0)   # an idle machine, so the bound is the spin factor alone
    assert shell_suites.timeout_for("/nowhere/test-long.sh") == 250.0 * slowcap.MAX_CONTENTION_FACTOR


def test_a_quick_suites_bound_is_the_floor(monkeypatch):
    monkeypatch.setattr(shell_suites, "measured", lambda: {"test-quick.sh": 1.0})
    monkeypatch.setattr(slowcap, "pressure_factor", lambda: 4.0)
    assert shell_suites.timeout_for("/nowhere/test-quick.sh") == shell_suites.MIN_TIMEOUT_SECONDS


def test_a_busy_machines_load_widens_a_suites_bound(monkeypatch):
    """#1885: a suite that clones and pushes trees waits on the disk, a wait the CPU-spin factor never sees. At load 24.6
    on 8 CPUs the machine carries about three waiters per CPU, so the 106.19 s line is not held to the 424.76 s bound the
    spin factor alone allows; test-wave.sh took 485.2 s there and was stopped while green."""
    monkeypatch.setattr(shell_suites, "measured", lambda: {"test-wave.sh": 106.19})
    monkeypatch.setattr(slowcap, "load_average", lambda: 24.6)
    monkeypatch.setattr(slowcap, "cpu_count", lambda: 8)
    assert shell_suites.timeout_for("/nowhere/test-wave.sh") > 485.2


def test_a_suite_that_costs_more_than_the_widened_bound_is_still_stopped(monkeypatch):
    """The pressure widens the bound by a measured and bounded factor, never removes it: a suite whose own cost is past what
    the busiest machine can excuse keeps a finite bound."""
    monkeypatch.setattr(shell_suites, "measured", lambda: {"test-costly.sh": 250.0})
    monkeypatch.setattr(slowcap, "load_average", lambda: 1000.0)
    monkeypatch.setattr(slowcap, "cpu_count", lambda: 8)
    assert shell_suites.timeout_for("/nowhere/test-costly.sh") == (
        250.0 * shell_suites.LOAD_FACTOR * slowcap.MAX_CONTENTION_FACTOR)


def test_every_real_suite_s_bound_covers_the_load_the_cap_excuses():
    """test-review.sh measured 266.8 s quiet and crossed a 300 s hand-tuned bound at load 61 (#736). Every suite's bound is its
    own measure times the load the cap excuses, so no suite is failed for the machine's wall."""
    for name, seconds in shell_suites.measured().items():
        assert shell_suites.timeout_for(SCRIPTS / name) >= seconds * shell_suites.LOAD_FACTOR


def test_the_gate_knobs_never_reach_a_suite(tmp_path, monkeypatch):
    # #755: nightly.sh runs the gate with SERIAL_HOLD_SECONDS=10800, and a suite that reads the bound in force saw a
    # reserve it wrote as dead still alive. The runner clears the gate's SERIAL_* knobs as it clears SG_*.
    monkeypatch.setenv("SERIAL_HOLD_SECONDS", "10800")
    monkeypatch.setenv("SERIAL_TRY", "1")
    suite = write_suite(tmp_path, "test-env.sh", 'echo "hold=${SERIAL_HOLD_SECONDS:-unset} try=${SERIAL_TRY:-unset}"\n')
    assert "hold=unset try=unset" in shell_suites.run(suite).stdout


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_a_process_a_suite_leaves_in_its_temp_folder_is_ended_with_the_suite(tmp_path):
    """#1742: a suite started the real watch loop from a fixture copy and left it as an orphan that ran for hours. The
    runner gives each suite its own process group and ends every process still in it once the suite returns."""
    pid_file = tmp_path / "pid"
    suite = tmp_path / "test-leaks.sh"
    suite.write_text(
        "#!/bin/bash\n"
        "T=$(mktemp -d)\n"
        "printf '#!/bin/bash\\nwhile :; do /bin/sleep 0.1; done\\n' > \"$T/loop.sh\"\n"
        "( bash \"$T/loop.sh\" ) > /dev/null 2>&1 &\n"
        "/bin/sleep 0.2\n"
        "pgrep -f \"$T/loop.sh\" | tail -1 > \"$LEAK_PID_FILE\"\n"
        "echo 'ok   the suite is green'\n")
    done = shell_suites.run(suite, extra_env={"LEAK_PID_FILE": str(pid_file)})
    pid = int(pid_file.read_text().split()[0])
    try:
        assert done.returncode == 0
        assert not _alive(pid), f"the loop the suite started (pid {pid}) outlived the suite"
    finally:
        if _alive(pid):
            os.kill(pid, 9)



def test_a_group_with_no_process_left_is_no_error():
    assert shell_suites.end_group(2 ** 22 - 7) is False   # no such group: nothing ended, nothing raised
