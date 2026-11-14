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
    pid = int(pidfile.read_text())   # the subshell that started it is gone, so no process of the tree owns it
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
    seen = [r for r in reap_leaks._table() if r[0] == pid or "serial.sh" in r[3]]
    assert reap_leaks.reap(tmp_path / "tree", since) == [pid], f"the table rows of the leftover: {seen}"
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
    leftover = f"bash {tmp_path}/contrib/squad-goal/scripts/serial.sh live-check"
    suite = (77, 60, 5, f"bash {tmp_path}/contrib/squad-goal/scripts/test-live-check.sh")
    assert reap_leaks.leaks(tmp_path, 0.0, [(4242, 77, 1, leftover), suite]) == [], "a suite of the tree owns it"
    session = (77, 60, 5, "python3 -m pytest -n auto")
    assert reap_leaks.leaks(tmp_path, 0.0, [(4242, 78, 1, leftover), (78, 77, 2, "bash -c x"), session]) == []
    assert reap_leaks.leaks(tmp_path, 0.0, [(4242, 1, 1, leftover)]) == [4242]
    # Linux hands an orphan to a subreaper, never to 1: a parent that is no part of the tree owns nothing.
    assert reap_leaks.leaks(tmp_path, 0.0, [(4242, 900, 1, leftover), (900, 1, 99, "systemd --user")]) == [4242]


def test_the_session_reaps_when_it_ends():
    conftest = (Path(__file__).parent / "conftest.py").read_text()
    assert "def pytest_sessionfinish" in conftest and "reap_leaks.reap(" in conftest, \
        "the session calls the reap when it ends"


def test_the_process_table_is_read_on_this_system():
    rows = reap_leaks._table()
    assert any(pid == os.getpid() for pid, _, _, _ in rows), "the table holds no row for this process: the ps columns were not read"
    long = "x" * 300
    child = subprocess.Popen(["bash", "-c", f"sleep 5; : {long}"])
    try:
        time.sleep(0.2)
        assert any(pid == child.pid and long in cmd for pid, _, _, cmd in reap_leaks._table()), "ps cut a long command line"
    finally:
        child.kill()
