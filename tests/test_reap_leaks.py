"""The session ends the processes it left behind, and no other (#1722)."""
import os
import subprocess
import time
from pathlib import Path

from tests import reap_leaks


def _orphan(tree, tmp_path):
    """A `serial.sh live-check` of `tree` whose parent is 1; the answer is its pid."""
    script = tree / "contrib" / "squad-goal" / "scripts" / "serial.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/bash\nsleep 30\n")
    pidfile = tmp_path / f"pid-{time.time_ns()}"
    subprocess.run(["bash", "-c", f"(bash {script} live-check >/dev/null 2>&1 & echo $! > {pidfile})"], check=True)
    pid = int(pidfile.read_text())
    for _ in range(50):
        if subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip() == "1":
            break
        time.sleep(0.02)
    return pid


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _gone(pid):
    for _ in range(100):
        if not _alive(pid):
            return True
        time.sleep(0.02)
    return False


def test_a_leftover_the_session_started_is_ended(tmp_path):
    since = time.time()
    pid = _orphan(tmp_path / "tree", tmp_path)
    assert reap_leaks.reap(tmp_path / "tree", since) == [pid]
    assert _gone(pid), "the leftover outlived the reap"


def test_a_process_of_another_tree_and_an_older_process_are_kept(tmp_path):
    other = _orphan(tmp_path / "other", tmp_path)
    old = _orphan(tmp_path / "tree", tmp_path)
    try:
        assert reap_leaks.reap(tmp_path / "tree", time.time() + 60) == [], "a process older than the session was ended"
        assert _alive(other) and _alive(old)
    finally:
        for pid in (other, old):
            os.kill(pid, 15)


def test_a_process_with_a_living_parent_is_kept(tmp_path):
    rows = [(4242, 77, 1, f"bash {tmp_path}/contrib/squad-goal/scripts/serial.sh live-check")]
    assert reap_leaks.leaks(tmp_path, 0.0, rows) == []
    rows = [(4242, 1, 1, f"bash {tmp_path}/contrib/squad-goal/scripts/serial.sh live-check")]
    assert reap_leaks.leaks(tmp_path, 0.0, rows) == [4242]


def test_the_session_reaps_when_it_ends():
    conftest = (Path(__file__).parent / "conftest.py").read_text()
    assert "def pytest_sessionfinish" in conftest and "reap_leaks.reap(" in conftest, \
        "the session calls the reap when it ends"
