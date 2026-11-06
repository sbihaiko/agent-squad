"""#1415: a real-app case starts clean, and a launch that dies before its hook is reported as a launch failure."""
import subprocess
import sys
import time

import pytest

from tests import real_app_guard as guard

_SLEEPER = "import sys, time; time.sleep(60)"


def _leftover_app(tmp_path):
    """A stand-in for an emulator a case left alive: any process whose command line carries the hook flag."""
    return subprocess.Popen([sys.executable, "-c", _SLEEPER, f"--test-hook={tmp_path}/hook.sock"], start_new_session=True)


@pytest.mark.process_bound
def test_a_leftover_app_is_seen_and_reaped_before_the_next_case(tmp_path):
    p = _leftover_app(tmp_path)
    try:
        time.sleep(0.3)
        assert p.pid in [a["pid"] for a in guard.live_apps()]
        seen = guard.clean_start()
        assert p.pid in [a["pid"] for a in seen["apps"]]
        assert p.wait(5) is not None and p.pid not in [a["pid"] for a in guard.live_apps()]
    finally:
        p.kill()
        p.wait()


@pytest.mark.process_bound
def test_a_leftover_app_does_not_fail_the_next_real_app_case(tmp_path, clean_app_start):
    """The fixture the real-app cases take: a case that left an app running costs the next one nothing."""
    assert guard.reapable(guard.live_apps()) == []


@pytest.mark.process_bound
def test_the_fixture_reaps_an_app_the_case_itself_left_running(tmp_path):
    from tests.real_app_guard_plugin import clean_app_start
    fixture = clean_app_start.__wrapped__
    first = fixture()
    next(first)
    p = _leftover_app(tmp_path)
    time.sleep(0.3)
    assert next(first, None) is None  # the case ended without quitting its app: the teardown reaps it
    assert p.wait(5) is not None
    second = fixture()
    assert p.pid not in [a["pid"] for a in next(second)["apps"]] and p.pid not in [a["pid"] for a in guard.live_apps()]
    assert next(second, None) is None


@pytest.mark.process_bound
def test_a_narrow_terminal_width_does_not_hide_the_hook_flag(tmp_path, monkeypatch):
    """procps `ps` cuts the command column at $COLUMNS; importing readline (pytest does) exports COLUMNS=80 to every child."""
    monkeypatch.setenv("COLUMNS", "80")
    p = _leftover_app(tmp_path / ("long-directory-name-" * 5))   # the command line is over 80 characters, the flag is its last word
    try:
        time.sleep(0.3)
        assert p.pid in [a["pid"] for a in guard.live_apps()]
    finally:
        p.kill()
        p.wait()


def _results(batch_verdict, output=""):
    return {"batches": [{"id": "launch", "verdict": batch_verdict, "output": output}],
            "steps": [{"id": "a", "verdict": "skipped (batch failed)"}, {"id": "b", "verdict": "skipped (batch failed)"}],
            "summary": {"executed": 0}}


def test_a_launch_that_exits_before_its_hook_is_named_a_launch_failure():
    r = _results("failed (application exited (0) before opening its hook: handed off)")
    got = guard.launch_failures(r)
    assert got and "launch failure" in got[0] and "exited (0) before opening its hook" in got[0] and "launch" in got[0]


def test_a_passing_or_crashed_batch_is_not_a_launch_failure():
    assert guard.launch_failures(_results("passed")) == []
    assert guard.launch_failures(_results("failed (process crash)")) == []


def test_assert_launched_fails_with_the_launch_not_nine_skipped_steps():
    r = _results("failed (application exited (0) before opening its hook: x)")
    with pytest.raises(AssertionError) as e:
        guard.assert_launched(r, {"apps": [{"pid": 7, "command": "Mesen --test-hook=/tmp/x"}], "sockets": []})
    msg = str(e.value)
    assert "launch failure" in msg and "pid 7" in msg and "skipped (batch failed)" not in msg


def test_an_app_of_another_live_run_is_reported_and_never_reaped():
    rows = [{"pid": 10, "ppid": 5, "command": "/x/Mesen --test-hook=/tmp/a/hook.sock"},      # its runner (5) is another run's
            {"pid": 11, "ppid": 1, "command": "/x/Mesen --test-hook=/tmp/b/hook.sock"},      # orphan: its runner is gone
            {"pid": 12, "ppid": 99, "command": "/x/Mesen --test-hook=/tmp/c/hook.sock"},     # a descendant of us (99 is our child)
            {"pid": 99, "ppid": 77, "command": "pytest"}, {"pid": 5, "ppid": 2, "command": "pytest"}]
    apps = guard.live_apps(rows)
    assert [a["pid"] for a in apps] == [10, 11, 12]
    assert [a["pid"] for a in guard.reapable(apps, rows, me=77)] == [11, 12]


def test_a_shell_whose_text_mentions_the_flag_is_not_an_app():
    rows = [{"pid": 3, "ppid": 1, "command": "/bin/zsh -c echo --test-hook=/x"}, {"pid": 4, "ppid": 1, "command": "grep -- --test-hook="}]
    assert guard.live_apps(rows) == []
