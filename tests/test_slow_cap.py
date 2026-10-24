import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def run_inner(tmp_path, body, ini=(), timeout=60, args=()):
    """One nested pytest run. `timeout` is the caller's own bound: a case about a test that never ends must not end the suite
    it runs in, so the nested run is cut there and the case fails on the exception instead of parking the worker."""
    (tmp_path / "test_inner.py").write_text(body)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    opts = [x for i in ini for x in ("-o", i)]
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.slowcap", "-o", "slow_cap_seconds=0.2",
         *opts, *args, "-c", "/dev/null", "--rootdir", str(tmp_path), "test_inner.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=timeout,
    )


SLEEP = "import time\n"
SPIN = "def test_spin():\n    while True:\n        pass\n"


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_test_over_the_cap_fails_and_is_named(tmp_path):
    r = run_inner(tmp_path, SLEEP + "def test_slow():\n    time.sleep(1.5)\n")
    assert r.returncode == 1
    assert "test_inner.py::test_slow took" in r.stdout


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_test_under_the_cap_passes(tmp_path):
    r = run_inner(tmp_path, "def test_fast():\n    pass\n")
    assert r.returncode == 0


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_slow_marker_with_a_reason_lets_a_test_pass(tmp_path):
    body = SLEEP + "import pytest\n@pytest.mark.slow(reason='real subprocess')\ndef test_slow():\n    time.sleep(1.5)\n"
    assert run_inner(tmp_path, body).returncode == 0


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_slow_marker_with_no_reason_refuses_the_run(tmp_path):
    body = "import pytest\n@pytest.mark.slow\ndef test_x():\n    pass\n"
    r = run_inner(tmp_path, body)
    assert r.returncode != 0 and "needs a reason" in r.stdout + r.stderr


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_test_that_never_returns_fails_by_name_at_its_deadline(tmp_path):
    """The cap is read after the call, so a test that never returns walks past it and parks the whole suite -- one xdist
    worker spinning is what hung a full gate (#130). The deadline is read while the test runs, so the suite ends, naming it."""
    r = run_inner(tmp_path, SPIN, ini=("test_deadline_seconds=1",), timeout=30)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "test_inner.py::test_spin" in r.stdout, "the spinning test has to be named"
    assert "deadline" in r.stdout, "and the deadline has to be the reason"


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_slow_marker_with_a_reason_is_exempt_from_the_deadline(tmp_path):
    """The deadline carries the cap's own exemption: a test that declares why it runs long is not cut, so no GUI or e2e suite
    that names its work is lost to it."""
    body = SLEEP + "import pytest\n@pytest.mark.slow(reason='real subprocess')\ndef test_slow():\n    time.sleep(1.5)\n"
    assert run_inner(tmp_path, body, ini=("test_deadline_seconds=1",)).returncode == 0


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
def test_an_unmarked_test_at_the_heavy_line_fails_and_is_named(tmp_path):
    r = run_inner(tmp_path, SLEEP + "def test_slow():\n    time.sleep(1.5)\n", ini=("slow_cap_seconds=5", "heavy_cap_seconds=0.3"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "test_inner.py::test_slow took" in r.stdout and "heavy" in r.stdout


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
def test_a_heavy_marker_lets_a_test_past_the_heavy_line_pass(tmp_path):
    body = SLEEP + "import pytest\n@pytest.mark.heavy\ndef test_slow():\n    time.sleep(1.5)\n"
    assert run_inner(tmp_path, body, ini=("slow_cap_seconds=5", "heavy_cap_seconds=0.3")).returncode == 0


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
def test_a_heavy_marker_does_not_lift_the_cap(tmp_path):
    body = SLEEP + "import pytest\n@pytest.mark.heavy\ndef test_slow():\n    time.sleep(1.5)\n"
    r = run_inner(tmp_path, body)
    assert r.returncode == 1 and "over the 0.2 s cap" in r.stdout, r.stdout + r.stderr


CONTENDED = (
    "import time\nimport tests.slowcap as slowcap\n"
    "def test_contended(monkeypatch):\n"
    "    monkeypatch.setattr(slowcap, 'contention_factor', lambda: 4.0, raising=False)\n"
    "    time.sleep(0.5)\n"
)


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_wall_time_that_only_contention_grew_does_not_fail_the_cap(tmp_path):
    """The cap guards the work a test does, not the runner's load. A call over the line whose excess one measured
    scheduling slowdown explains is not named; the sleeping test above, at no slowdown, still is."""
    r = run_inner(tmp_path, CONTENDED)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_wall_time_that_only_contention_grew_does_not_fail_the_heavy_line(tmp_path):
    r = run_inner(tmp_path, CONTENDED, ini=("slow_cap_seconds=5", "heavy_cap_seconds=0.3"))
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_test_far_past_the_line_fails_even_under_contention(tmp_path):
    body = CONTENDED.replace("time.sleep(0.5)", "time.sleep(2.0)")
    r = run_inner(tmp_path, body)
    assert r.returncode == 1 and "test_inner.py::test_contended took" in r.stdout, r.stdout + r.stderr


def test_the_contention_factor_is_measured_and_bounded():
    import tests.slowcap as slowcap

    assert 1.0 <= slowcap.contention_factor() <= slowcap.MAX_CONTENTION_FACTOR


def test_the_repo_default_cap_is_20_seconds_and_durations_print():
    text = (ROOT / "pyproject.toml").read_text()
    assert "--durations=15" in text and 'slow_cap_seconds = "20"' in text and 'test_deadline_seconds = "60"' in text
    assert 'heavy_cap_seconds = "2"' in text and '"heavy: ' in text


UNMARKED_SLOW = SLEEP + "def test_slow():\n    time.sleep(1.0)\n"


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
def test_under_xdist_an_unmarked_call_at_the_heavy_line_is_reported_not_failed(tmp_path):
    """Load under -n auto turns a 0.4 s suite into 2.2 s, a timing-only finding (#629). Under xdist it is named in the
    summary and deferred to the serial run on the same tip, which enforces the line; the run's exit code is untouched."""
    r = run_inner(tmp_path, UNMARKED_SLOW, ini=("slow_cap_seconds=5", "heavy_cap_seconds=0.3"), args=("-p", "xdist", "-n", "1"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SLOW-UNMARKED (deferred to the serial run)" in r.stdout, r.stdout
    assert "test_inner.py::test_slow" in r.stdout


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
def test_in_a_serial_run_an_unmarked_call_at_the_heavy_line_fails(tmp_path):
    r = run_inner(tmp_path, UNMARKED_SLOW, ini=("slow_cap_seconds=5", "heavy_cap_seconds=0.3"), args=("-p", "no:xdist"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "test_inner.py::test_slow took" in r.stdout and "heavy" in r.stdout
    assert "SLOW-UNMARKED" not in r.stdout


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.parametrize("mode", [("-p", "xdist", "-n", "1"), ("-p", "no:xdist")], ids=["xdist", "serial"])
def test_a_call_over_the_cap_fails_in_both_modes(tmp_path, mode):
    r = run_inner(tmp_path, UNMARKED_SLOW, ini=("slow_cap_seconds=0.5", "heavy_cap_seconds=0.3"), args=mode)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "over the 0.5 s cap" in r.stdout, r.stdout


@pytest.mark.slow(reason="starts a nested pytest subprocess")
@pytest.mark.heavy
def test_a_nested_run_of_a_trivial_test_is_cheap(tmp_path):
    """A nested run of one empty test does no work of its own: its wall time is interpreter start and pytest import.
    Left to load every installed plugin (deepeval alone adds seconds), it crept to the inner deadline under load (#797).
    The nested run names only the plugins it needs, so a trivial run finishes in a few seconds even on a loaded machine."""
    start = time.monotonic()
    r = run_inner(tmp_path, "def test_fast():\n    pass\n")
    elapsed = time.monotonic() - start
    assert r.returncode == 0, r.stdout + r.stderr
    assert elapsed < 3.0, f"a nested run of one empty test took {elapsed:.1f} s"


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
        "test_under_xdist_an_unmarked_call_at_the_heavy_line_is_reported_not_failed": "starts a nested pytest subprocess",
        "test_in_a_serial_run_an_unmarked_call_at_the_heavy_line_fails": "starts a nested pytest subprocess",
        "test_a_call_over_the_cap_fails_in_both_modes": "starts a nested pytest subprocess",
        "test_a_nested_run_of_a_trivial_test_is_cheap": "starts a nested pytest subprocess",
    }
