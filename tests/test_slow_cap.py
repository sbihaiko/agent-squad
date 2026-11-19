import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def run_inner(body, ini=(), timeout=60, args=(), keep=False):
    """One nested pytest run. `timeout` is the caller's own bound: a case about a test that never ends must not end the suite
    it runs in, so the nested run is cut there and the case fails on the exception instead of parking the worker.

    The nested run is cheap by design (#797): its own directory under /tmp, a fresh one per run, with no conftest above it
    and no plugin that is not named. pytest walks every ancestor of a collected path, and the macOS TMPDIR above
    the test holds over 100,000 entries, so a run there took 25 s alone. `keep` leaves the work dir for the caller to
    inspect and remove; the caller owns that removal."""
    work = Path(tempfile.mkdtemp(prefix="slowcap-", dir="/tmp")).resolve()
    try:
        (work / "test_inner.py").write_text(body)
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        opts = [x for i in ini for x in ("-o", i)]
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:deepeval", "-p", "no:cacheprovider", "-p", "tests.slowcap",
             "-o", "slow_cap_seconds=0.2", *opts, *args, "--confcutdir", str(work), "-c", "/dev/null", "--rootdir", str(work), "test_inner.py"],
            cwd=work, env=env, capture_output=True, text=True, timeout=timeout,
        )
        result.workdir = str(work)
        return result
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


SLEEP = "import time\n"
SPIN = "def test_spin():\n    while True:\n        pass\n"


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_test_over_the_cap_fails_and_is_named():
    r = run_inner(SLEEP + "def test_slow():\n    time.sleep(1.5)\n")
    assert r.returncode == 1
    assert "test_inner.py::test_slow took" in r.stdout


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_test_under_the_cap_passes():
    r = run_inner("def test_fast():\n    pass\n")
    assert r.returncode == 0


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_slow_marker_with_a_reason_lets_a_test_pass():
    body = SLEEP + "import pytest\n@pytest.mark.slow(reason='real subprocess')\ndef test_slow():\n    time.sleep(1.5)\n"
    assert run_inner(body).returncode == 0


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_slow_marker_with_no_reason_refuses_the_run():
    body = "import pytest\n@pytest.mark.slow\ndef test_x():\n    pass\n"
    r = run_inner(body)
    assert r.returncode != 0 and "needs a reason" in r.stdout + r.stderr


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_test_that_never_returns_fails_by_name_at_its_deadline():
    """The cap is read after the call, so a test that never returns walks past it and parks the whole suite -- one xdist
    worker spinning is what hung a full gate (#130). The deadline is read while the test runs, so the suite ends, naming it."""
    r = run_inner(SPIN, ini=("test_deadline_seconds=1",), timeout=30)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "test_inner.py::test_spin" in r.stdout, "the spinning test has to be named"
    assert "deadline" in r.stdout, "and the deadline has to be the reason"


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_slow_marker_with_a_reason_is_exempt_from_the_deadline():
    """The deadline carries the cap's own exemption: a test that declares why it runs long is not cut, so no GUI or e2e suite
    that names its work is lost to it."""
    body = SLEEP + "import pytest\n@pytest.mark.slow(reason='real subprocess')\ndef test_slow():\n    time.sleep(1.5)\n"
    assert run_inner(body, ini=("test_deadline_seconds=1",)).returncode == 0


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
def test_an_unmarked_test_at_the_heavy_line_fails_and_is_named():
    r = run_inner(SLEEP + "def test_slow():\n    time.sleep(1.5)\n", ini=("slow_cap_seconds=5", "heavy_cap_seconds=0.3"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "test_inner.py::test_slow took" in r.stdout and "heavy" in r.stdout


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
def test_a_heavy_marker_lets_a_test_past_the_heavy_line_pass():
    body = SLEEP + "import pytest\n@pytest.mark.heavy\ndef test_slow():\n    time.sleep(1.5)\n"
    assert run_inner(body, ini=("slow_cap_seconds=5", "heavy_cap_seconds=0.3")).returncode == 0


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
def test_a_heavy_marker_does_not_lift_the_cap():
    body = SLEEP + "import pytest\n@pytest.mark.heavy\ndef test_slow():\n    time.sleep(1.5)\n"
    r = run_inner(body)
    assert r.returncode == 1 and "over the 0.2 s cap" in r.stdout, r.stdout + r.stderr


CONTENDED = (
    "import time\nimport tests.slowcap as slowcap\n"
    "def test_contended(monkeypatch):\n"
    "    monkeypatch.setattr(slowcap, 'contention_factor', lambda: 4.0, raising=False)\n"
    "    time.sleep(0.5)\n"
)


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_wall_time_that_only_contention_grew_does_not_fail_the_cap():
    """The cap guards the work a test does, not the runner's load. A call over the line whose excess one measured
    scheduling slowdown explains is not named; the sleeping test above, at no slowdown, still is."""
    r = run_inner(CONTENDED)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_wall_time_that_only_contention_grew_does_not_fail_the_heavy_line():
    r = run_inner(CONTENDED, ini=("slow_cap_seconds=5", "heavy_cap_seconds=0.3"))
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_test_far_past_the_line_fails_even_under_contention():
    body = CONTENDED.replace("time.sleep(0.5)", "time.sleep(2.0)")
    r = run_inner(body)
    assert r.returncode == 1 and "test_inner.py::test_contended took" in r.stdout, r.stdout + r.stderr


PRESSURED = (
    "import time\nimport tests.slowcap as slowcap\n"
    "def test_pressured(monkeypatch):\n"
    "    monkeypatch.setattr(slowcap, 'contention_factor', lambda: 1.0, raising=False)\n"
    "    monkeypatch.setattr(slowcap, 'pressure_factor', lambda *a, **k: 4.0, raising=False)\n"
    "    time.sleep(0.5)\n"
)


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_wall_time_that_only_the_machine_s_load_explains_does_not_fail_the_cap():
    """A call that waits on the disk or on a subprocess spins for none of it, so a CPU spin reads 1.0 while the machine's
    load average per CPU reads 4.0 (#1885). The cap reads the same measured load as the shell suite runner's own bound, so
    a real script suite is not named for the runner's wait. Its own excess past the bound still fails below."""
    r = run_inner(PRESSURED)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_test_far_past_the_line_fails_even_under_the_machine_s_load():
    body = PRESSURED.replace("time.sleep(0.5)", "time.sleep(2.0)")
    r = run_inner(body)
    assert r.returncode == 1 and "test_inner.py::test_pressured took" in r.stdout, r.stdout + r.stderr


def test_the_contention_factor_is_measured_and_bounded():
    import tests.slowcap as slowcap

    assert 1.0 <= slowcap.contention_factor() <= slowcap.MAX_CONTENTION_FACTOR


def test_the_pressure_factor_is_the_load_per_cpu_and_is_bounded():
    """#1885: the load average counts a process blocked on a disk, which a CPU spin cannot see. The factor is one waiter per
    CPU at the idle line, that ratio above it, and never past the bound the cap excuses."""
    import tests.slowcap as slowcap

    assert slowcap.pressure_factor(4.0, 8) == 1.0
    assert slowcap.pressure_factor(24.6, 8) == 24.6 / 8
    assert slowcap.pressure_factor(1000.0, 8) == slowcap.MAX_CONTENTION_FACTOR
    assert 1.0 <= slowcap.pressure_factor() <= slowcap.MAX_CONTENTION_FACTOR


def test_the_repo_default_cap_is_20_seconds_and_durations_print():
    text = (ROOT / "pyproject.toml").read_text()
    assert "--durations=15" in text and 'slow_cap_seconds = "20"' in text and 'test_deadline_seconds = "60"' in text
    assert 'heavy_cap_seconds = "2"' in text and '"heavy: ' in text


UNMARKED_SLOW = SLEEP + "def test_slow():\n    time.sleep(1.0)\n"


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
def test_under_xdist_an_unmarked_call_at_the_heavy_line_is_reported_not_failed():
    """Load under -n auto turns a 0.4 s suite into 2.2 s, a timing-only finding (#629). Under xdist it is named in the
    summary and deferred to the serial run on the same tip, which enforces the line; the run's exit code is untouched."""
    r = run_inner(UNMARKED_SLOW, ini=("slow_cap_seconds=5", "heavy_cap_seconds=0.3"), args=("-p", "xdist", "-n", "1"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SLOW-UNMARKED (deferred to the serial run)" in r.stdout, r.stdout
    assert "test_inner.py::test_slow" in r.stdout


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
def test_in_a_serial_run_an_unmarked_call_at_the_heavy_line_fails():
    r = run_inner(UNMARKED_SLOW, ini=("slow_cap_seconds=5", "heavy_cap_seconds=0.3"), args=("-p", "no:xdist"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "test_inner.py::test_slow took" in r.stdout and "heavy" in r.stdout
    assert "SLOW-UNMARKED" not in r.stdout


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.parametrize("mode", [("-p", "xdist", "-n", "1"), ("-p", "no:xdist")], ids=["xdist", "serial"])
def test_a_call_over_the_cap_fails_in_both_modes(mode):
    # A 1.0 s call under the worst contention (factor 4) still reads 0.25 s, so a 0.2 s cap always names it.
    r = run_inner(UNMARKED_SLOW, ini=("slow_cap_seconds=0.2", "heavy_cap_seconds=0.1"), args=mode)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "over the 0.2 s cap" in r.stdout, r.stdout


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_nested_run_of_a_trivial_test_is_cheap():
    """A nested run of one empty test is set up for speed, not timed: its work dir is under /tmp, never TMPDIR (#797),
    and its loaded plugins are named, with no deepeval. The plugin list is read from the run's own header; no header means no third-party plugin.
    No .pytest_cache is left in its work dir, which is kept until this check ends (#797)."""
    r = run_inner("def test_fast():\n    pass\n", keep=True)
    try:
        assert r.returncode == 0, r.stdout + r.stderr
        assert not os.path.exists(os.path.join(r.workdir, ".pytest_cache")), os.listdir(r.workdir)
        _check_cheap_header(r)
    finally:
        shutil.rmtree(r.workdir, ignore_errors=True)


def _check_cheap_header(r):
    rootdir = next((ln.split(":", 1)[1].strip() for ln in r.stdout.splitlines() if ln.startswith("rootdir:")), None)
    assert rootdir is not None, r.stdout
    assert os.path.realpath(rootdir).startswith(os.path.realpath("/tmp") + os.sep), rootdir
    # pytest prints no "plugins:" header when no third-party plugin loaded; that is an empty list, not a failure.
    plugins = next((ln.split(":", 1)[1] for ln in r.stdout.splitlines() if ln.startswith("plugins:")), "")
    assert "deepeval" not in plugins, plugins


NESTED_CHECKS = (
    test_a_test_over_the_cap_fails_and_is_named,
    test_a_test_under_the_cap_passes,
    test_a_slow_marker_with_a_reason_lets_a_test_pass,
    test_a_slow_marker_with_no_reason_refuses_the_run,
    test_a_test_that_never_returns_fails_by_name_at_its_deadline,
    test_a_slow_marker_with_a_reason_is_exempt_from_the_deadline,
    test_an_unmarked_test_at_the_heavy_line_fails_and_is_named,
    test_a_heavy_marker_lets_a_test_past_the_heavy_line_pass,
    test_a_heavy_marker_does_not_lift_the_cap,
    test_wall_time_that_only_contention_grew_does_not_fail_the_cap,
    test_wall_time_that_only_contention_grew_does_not_fail_the_heavy_line,
    test_a_test_far_past_the_line_fails_even_under_contention,
    test_wall_time_that_only_the_machine_s_load_explains_does_not_fail_the_cap,
    test_a_test_far_past_the_line_fails_even_under_the_machine_s_load,
    test_under_xdist_an_unmarked_call_at_the_heavy_line_is_reported_not_failed,
    test_in_a_serial_run_an_unmarked_call_at_the_heavy_line_fails,
    test_a_call_over_the_cap_fails_in_both_modes,
    test_a_nested_run_of_a_trivial_test_is_cheap,
)


def _slow_reason(test):
    for mark in getattr(test, "pytestmark", ()):
        if mark.name == "slow":
            return mark.kwargs.get("reason")
    return None


def _heavy_marked(test):
    return any(mark.name == "heavy" for mark in getattr(test, "pytestmark", ()))


def test_nested_pytest_checks_carry_the_heavy_marker():
    """Every check here starts a nested pytest, a call at or over the 1 s line the fast battery deselects. A call that sits
    just over that line is named by the guard once the machine is loaded, so the mark is what keeps a loaded gate green. One
    check was left unmarked and failed a loaded gate at 3.1 s."""
    missing = [_test.__name__ for _test in NESTED_CHECKS if not _heavy_marked(_test)]
    assert missing == [], f"these checks start a nested pytest and carry no heavy mark: {missing}"


def test_a_dispatch_test_that_builds_two_child_worktrees_carries_the_heavy_mark():
    """One call that builds two child worktrees in a real repository does git worktree adds and a repository copy, and a
    loaded runner pushed it to 4.6 s, past the 2 s line an unmarked call is held to (#1721)."""
    from tests import test_dispatch as dispatch

    builders = [
        dispatch.test_a_request_never_picks_its_workspace_and_each_child_worktree_starts_from_the_target_head,
        dispatch.test_parallel_writing_children_run_in_distinct_worktrees_and_a_reader_shares_the_workspace,
        dispatch.test_the_checks_gates_run_after_every_child_and_a_failed_check_fails_the_child,
        dispatch.test_the_dispatch_artifact_feeds_the_final_gate_with_the_child_commands,
    ]
    missing = [_test.__name__ for _test in builders if not _heavy_marked(_test)]
    assert missing == [], f"these tests build two child worktrees and carry no heavy mark: {missing}"


def test_a_no_progress_window_wait_carries_the_heavy_mark():
    """Each entry in this test waits out the no-progress window, and a loaded runner pushed the call to 2.2 s (#1694)."""
    from tests.test_entry_no_progress import test_the_second_entry_gets_what_is_left_of_the_node_deadline as waits

    assert _heavy_marked(waits), "this test waits out a no-progress window and carries no heavy mark"


def test_nested_pytest_checks_explain_their_slow_marker():
    checks = NESTED_CHECKS
    assert {_test.__name__: _slow_reason(_test) for _test in checks} == {
        "test_a_test_over_the_cap_fails_and_is_named": "starts a nested pytest subprocess",
        "test_a_test_under_the_cap_passes": "starts a nested pytest subprocess",
        "test_a_slow_marker_with_a_reason_lets_a_test_pass": "starts a nested pytest subprocess",
        "test_a_slow_marker_with_no_reason_refuses_the_run": "starts a nested pytest subprocess",
        "test_a_test_that_never_returns_fails_by_name_at_its_deadline": "starts a nested pytest subprocess",
        "test_a_slow_marker_with_a_reason_is_exempt_from_the_deadline": "starts a nested pytest subprocess",
        "test_an_unmarked_test_at_the_heavy_line_fails_and_is_named": "starts a nested pytest subprocess",
        "test_a_heavy_marker_lets_a_test_past_the_heavy_line_pass": "starts a nested pytest subprocess",
        "test_a_heavy_marker_does_not_lift_the_cap": "starts a nested pytest subprocess",
        "test_wall_time_that_only_contention_grew_does_not_fail_the_cap": "starts a nested pytest subprocess",
        "test_wall_time_that_only_contention_grew_does_not_fail_the_heavy_line": "starts a nested pytest subprocess",
        "test_a_test_far_past_the_line_fails_even_under_contention": "starts a nested pytest subprocess",
        "test_wall_time_that_only_the_machine_s_load_explains_does_not_fail_the_cap": "starts a nested pytest subprocess",
        "test_a_test_far_past_the_line_fails_even_under_the_machine_s_load": "starts a nested pytest subprocess",
        "test_under_xdist_an_unmarked_call_at_the_heavy_line_is_reported_not_failed": "starts a nested pytest subprocess",
        "test_in_a_serial_run_an_unmarked_call_at_the_heavy_line_fails": "starts a nested pytest subprocess",
        "test_a_call_over_the_cap_fails_in_both_modes": "starts a nested pytest subprocess",
        "test_a_nested_run_of_a_trivial_test_is_cheap": "starts a nested pytest subprocess",
    }
