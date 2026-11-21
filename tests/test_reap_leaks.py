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


def test_a_session_a_test_started_ends_nothing(tmp_path):
    """#2680: a pytest session a test started ends nothing. Its reap sees the detached live check the outer session still
    owns as a leftover, because a nohup child's parent is 1, and it ends it; that killed a live check eight-tenths of a
    second after another worker's case started it. The outer session's own end still ends its leftover."""
    since = time.time()
    pid = _orphan(tmp_path / "tree", tmp_path)   # a real detached `serial.sh live-check` of this tree
    try:
        rows = reap_leaks._table() + [
            (424242, 424241, 1, "python3 -m pytest -q -o addopts= tests/test_git_env_scrub.py"),   # the nested session
            (424241, 424240, 3, "bash -e /home/runner/work/_temp/step.sh"),                          # the test that started it
            (424240, 1, 30, "python3 -m pytest -n auto --rootdir . tests/"),                         # the outer controller
        ]
        assert reap_leaks.session_reap(tmp_path / "tree", since, rows=rows, pid=424242) == [], \
            "a session a test started ended the live check of the outer session"
        assert _alive(pid), "the live check the outer session still owns was ended"
        assert reap_leaks.session_reap(tmp_path / "tree", since, rows=rows, pid=999999) == [pid], \
            "the outer session's own end does not end its leftover"
        assert _gone(pid), "the leftover outlived the outer session's reap"
    finally:
        if _alive(pid):
            os.kill(pid, 15)


def test_the_session_reaps_when_it_ends():
    conftest = (Path(__file__).parent / "conftest.py").read_text()
    assert "def pytest_sessionfinish" in conftest and "reap_leaks.session_reap(" in conftest, \
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


def test_the_table_is_read_without_the_process_table_binary(monkeypatch):
    """A session inside the child's sandbox-exec wall cannot exec /bin/ps (#1508), so a read that ends the session on it
    records a red the machine caused as the tree's own (#1691). The table comes from squad.procs, the reader built for
    that wall."""
    def denied(*_a, **_k):
        raise PermissionError(1, "Operation not permitted", "ps")

    monkeypatch.setattr(reap_leaks.subprocess, "run", denied)
    rows = reap_leaks._table()
    assert any(pid == os.getpid() for pid, _, _, _ in rows), "the read fell back to the refused binary"


def test_a_table_no_reader_can_read_is_no_rows(monkeypatch):
    """A read the machine refuses is no rows, never an exception: the reap ends nothing it cannot see, which is the safe
    half of the choice."""
    monkeypatch.setattr(reap_leaks, "_table_procs", lambda: None)

    def denied(*_a, **_k):
        raise PermissionError(1, "Operation not permitted", "ps")

    monkeypatch.setattr(reap_leaks.subprocess, "run", denied)
    assert reap_leaks._table() == []


def test_a_zsh_wrapper_in_a_dead_run_folder_does_not_keep_the_run_from_the_reap(tmp_path):
    """#1993: a `zsh -c` tool shell whose body names a session command is no session, so a dead run is still reaped."""
    import json
    import os
    import time

    from squad import reap

    folder = tmp_path / "runs" / "run-dead"
    folder.mkdir(parents=True)
    rec = folder / "record.jsonl"
    rec.write_text("".join(json.dumps(e) + "\n" for e in [
        {"event": "run_start", "run_id": "run-dead", "pos": 1},
        {"event": "call_start", "pid": 999999, "started_at": 1.0, "pos": 2},
    ]))
    now = time.time()
    os.utime(rec, (now - 3600, now - 3600))
    active = tmp_path / "active-runs.txt"
    active.write_text(f"{folder}\n")

    class NoLeases:
        def occupancy(self):
            return {"waiting": []}

        def holders(self, kind):
            return []

    shell = [(9001, str(folder), "zsh -c sleep 540; python3 -m squad start x")]
    done = reap.reap(str(active), str(tmp_path), ceiling=NoLeases(), procs=shell, dry=True, now=now, say=lambda s: None)
    assert done == ["run-dead"]
