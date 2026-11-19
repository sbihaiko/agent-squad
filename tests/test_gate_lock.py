"""Issue #392: one gate at a time. A machine-wide lock queues every gate.

The branchland local gate, the wave entry gate, the promotion gate and a hand-started gate all take the `gate` lock
`serial.sh` owns. A second gate waits for the first and then runs; it is never dropped; the wait shows on one log line.
Host-free: a throwaway SG_STATE and a throwaway scripts folder; the runners are read, the hand gate is run.
"""
import os
import re
import shutil
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
WAIT_LINE = re.compile(r"^serial: waiting for lock gate held by pid [0-9]+$", re.M)
TAKES_LOCK = re.compile(r"(?<![\w-])gate\.sh\"?\s")
RUNNERS = ("branchland.sh", "wave.sh", "promote-stg.sh")

# A held gate lock is proven held by its holder's live pid, which the test reads from the process table through /bin/ps.
# The mark is per test: a case that records the pids it starts reads no process table, so a session that denies /bin/ps runs it.
PROCESS_BOUND = pytest.mark.process_bound


GIT = "/usr/bin/git"
POLL = {"SERIAL_POLL_SECONDS": "0.05"}   # a waiter looks at the lock every 50 ms, so a short hold is enough to prove a wait


def _env(state):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    e["SG_STATE"] = str(state)
    e["SG_SQUAD"] = str(SCRIPTS.parent.parent.parent)   # a gate takes gate slots through this repository's squad.caps (#567)
    e["SQUAD_CAPS_DIR"] = str(Path(state) / "caps")     # a throwaway budget: no case draws on the machine's live slots
    return e



def _watchdog(state, lock):
    """The pid of the lock's live watchdog, as the holder recorded it in `<lock>/wd`; empty while none is written."""
    try:
        pid = (Path(state) / "locks" / lock / "wd").read_text().strip()
    except OSError:
        return ""
    return pid if pid and _alive(pid) else ""

def _alive(pid):
    """True while a process with this pid lives. A case that reads the pids it started needs no process table."""
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    return True


@contextmanager
def _grouped(cmd, **kw):
    """Start a process in its own session and end its whole group when the case leaves this block, on every exit path.

    A case leaves the block when it passes, when an assertion fails and when a wait runs out. A runner's own serial.sh
    gate waiter polls the lock for ever, so ending the runner alone leaves a process nobody owns.
    """
    proc = subprocess.Popen(cmd, start_new_session=True, **kw)
    try:
        yield proc
    finally:
        _end(proc)


def _end(proc):
    """End the process a case started and every process in its group: a waiter the process forked outlives it."""
    if proc.poll() is None:
        _signal_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    # The leader can end and leave a child behind, so the group is signalled whether or not its leader still runs.
    _signal_group(proc, signal.SIGKILL)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _signal_group(proc, sig):
    """Signal a group started by `_grouped`; a group that is already empty is nothing to end."""
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def _tree(tmp_path):
    d = tmp_path / "scripts"
    d.mkdir()
    for name in ("gate.sh", "serial.sh", "caps.sh"):
        shutil.copy(SCRIPTS / name, d / name)
    return d


def _code(path):
    return "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())


@pytest.mark.parametrize("runner", RUNNERS)
@PROCESS_BOUND
def test_each_gate_runner_takes_the_machine_wide_gate_lock(runner):
    """A runner that starts a gate calls it under `serial.sh gate`, or a second gate would run beside the first."""
    assert TAKES_LOCK.search(_code(SCRIPTS / runner)), f"{runner} runs its gate without the machine-wide gate lock"


def _git(*args, cwd):
    subprocess.run([GIT, "-c", "user.email=a@b", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True)


def _repo_with_remote(tmp_path, branches):
    """A working repository on `dev` with one commit, and a bare origin holding `dev` and every branch in `branches`."""
    co, origin = tmp_path / "co", tmp_path / "origin.git"
    _git("init", "-q", "-b", "dev", str(co), cwd=tmp_path)
    (co / "f").write_text("x\n")
    _git("add", "f", cwd=co)
    _git("commit", "-q", "-m", "init", cwd=co)
    _git("clone", "-q", "--bare", str(co), str(origin), cwd=tmp_path)
    for b in branches:
        _git("push", "-q", str(origin), f"dev:refs/heads/{b}", cwd=co)
    return co, origin


def _copy(d, names, to):
    for name in names:
        shutil.copy(d / name, to / name)


WAVE_SCRIPTS = ("wave.sh", "child_issues.py", "address.sh", "gate.sh", "serial.sh", "caps.sh", "base-battery.sh",
                "ghcall.sh", "ghread.py", "ghthrottle.py", "autohunt.sh", "basecheck.sh", "fast-gate.sh", "issue_ledger.py",
                "revformat.py", "siblings.py", "wavepause.py",
                "columnlimit.sh", "issue_state.py")   # wave.sh sources ghcall.sh, which runs the two readers (#593); columnlimit.sh and issue_state.py read the Testing card limit and the board (#2439)


@pytest.fixture
def wave_runner(tmp_path):
    """wave.sh enter in a child's worktree, with a gate that records its run and fails (so nothing is pushed)."""
    d = tmp_path / "s"
    d.mkdir()
    _copy(SCRIPTS, WAVE_SCRIPTS, d)
    co, origin = _repo_with_remote(tmp_path, ["wave/run-1"])
    st, marker = tmp_path / "st", tmp_path / "gate.ran"
    (st / "auto").mkdir(parents=True)
    (tmp_path / "board.sh").write_text("#!/bin/bash\nexit 0\n")
    (tmp_path / "board.sh").chmod(0o755)
    (d / "env.sh").write_text(f"export SG_STATE={st} SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT={co} "
                              f"SG_LAND_REMOTE={origin} BOARD={tmp_path / 'board.sh'}\n")
    child = tmp_path / "c1"
    _git("clone", "-q", str(origin), str(child), cwd=tmp_path)
    _git("checkout", "-q", "-b", "gui/1", "origin/wave/run-1", cwd=child)
    (child / "g").write_text("1\n")
    _git("add", "g", cwd=child)
    _git("commit", "-q", "-m", "work 1", cwd=child)
    sha = subprocess.run([GIT, "rev-parse", "HEAD"], cwd=child, capture_output=True, text=True).stdout.strip()
    (st / "review-gui_1.md").write_text("VERDICT: APPROVE\n")
    (st / "review-gui_1.sha").write_text(sha + "\n")
    (st / "review-gui_1.base").write_text("wave/run-1\n")
    env = _env(st) | POLL | {"SG_WAVE_GATES": f"echo gate >> {marker}; exit 1", "SG_WAVE_ENTRY_GATE": "on"}   # the entry gate is off by default (SPEC 1797)
    return (["bash", str(d / "wave.sh"), "enter", "run-1", "1"], child, env, st, f"test -e {marker}", marker)


@pytest.fixture
def branchland_runner(tmp_path):
    """One branchland pass over a work branch whose review approved it and whose tip has no check run: the local gate runs."""
    d = tmp_path / "s"
    d.mkdir()
    _copy(SCRIPTS, ("gate.sh", "serial.sh", "caps.sh", "branchland.sh", "base-battery.sh", "revformat.py", "workbranches.sh", "ffland.sh",
                    "address.sh", "ghread.py", "ghthrottle.py", "ghcall.sh"), d)
    co, origin = _repo_with_remote(tmp_path, [])
    st, marker = tmp_path / "st", tmp_path / "gate.ran"
    (st / "auto").mkdir(parents=True)
    (tmp_path / "board.sh").write_text("#!/bin/bash\nexit 0\n")
    (tmp_path / "board.sh").chmod(0o755)
    (d / "env.sh").write_text(f"export SG_STATE={st} SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT={co} "
                              f"SG_LAND_REMOTE={origin} BOARD={tmp_path / 'board.sh'}\n")
    w = tmp_path / "w"
    _git("clone", "-q", str(origin), str(w), cwd=tmp_path)
    _git("checkout", "-q", "-b", "gui/7-x", "origin/dev", cwd=w)
    (w / "g").write_text("y\n")
    _git("add", "g", cwd=w)
    _git("commit", "-q", "-m", "work", cwd=w)
    _git("push", "-q", "origin", "gui/7-x", cwd=w)
    sha = subprocess.run([GIT, "rev-parse", "HEAD"], cwd=w, capture_output=True, text=True).stdout.strip()
    (st / "review-gui_7-x.md").write_text("VERDICT: APPROVE\n")
    (st / "review-gui_7-x.sha").write_text(sha + "\n")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text(
        "#!/bin/bash\ncase \"$*\" in\n"
        "  *\"pulls?per_page\"*) echo '[]';;\n"
        "  *\"issues(states: OPEN\"*) echo '{\"data\":{\"repository\":{\"issues\":{\"pageInfo\":{\"hasNextPage\":false},"
        "\"nodes\":[{\"number\":7,\"title\":\"t\",\"body\":\"\",\"state\":\"OPEN\"}]}}}}';;\n"
        "  *\"/check-runs\"*) echo '{\"check_runs\":[]}';;\nesac\n")
    (bin_ / "gh").chmod(0o755)
    env = _env(st) | POLL | {"PATH": f"{bin_}:{os.environ['PATH']}", "SG_LOCAL_GATE_CMD": f"echo gate >> {marker}; exit 1",
                             "SG_LOCAL_GATE_RERUN_CMD": "exit 1"}
    return (["bash", str(d / "branchland.sh")], tmp_path, env, st, f"test -e {marker}", marker)


@pytest.fixture
def promote_runner(tmp_path):
    """promote-stg.sh with dev one commit ahead of stg and the local proof for the dev tip on the status line: the promotion
    merges the dev -> stg pull request (#743), and the stub GitHub moves stg the way a merge commit does."""
    d = tmp_path / "s"
    d.mkdir()
    _copy(SCRIPTS, ("promote-stg.sh", "gate.sh", "serial.sh", "caps.sh", "ghcall.sh", "ghread.py"), d)
    co, origin = _repo_with_remote(tmp_path, ["stg"])
    _git("commit", "-q", "--allow-empty", "-m", "one", cwd=co)
    _git("push", "-q", str(origin), "dev", cwd=co)
    st = tmp_path / "st"
    st.mkdir()
    dev = subprocess.run([GIT, "-C", str(co), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (st / "gate-status-full").write_text(f"{int(time.time())}\tfull\tpass\t{dev}\n")   # the promotion reads the full proof here, which a fast gate never overwrites (#472)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text(
        "#!/bin/bash\ncase \"$*\" in\n"
        "  *graphql*) echo '{\"data\":{\"repository\":{\"pullRequests\":{\"nodes\":[],\"pageInfo\":{\"hasNextPage\":false}}}}}';;\n"
        "  *\"-X POST repos/x/y/pulls\"*) echo '{\"number\":9}';;\n"
        "  *\"-X PUT repos/x/y/pulls/9/merge\"*) "
        f"\"{GIT}\" --git-dir=\"$SG_PROMOTE_REMOTE\" update-ref refs/heads/stg "
        f"\"$(\"{GIT}\" --git-dir=\"$SG_PROMOTE_REMOTE\" rev-parse refs/heads/dev)\" && echo '{{\"merged\":true}}';;\n"
        "  *) echo '{}';;\nesac\n")
    (bin_ / "gh").chmod(0o755)
    env = _env(st) | POLL | {"SG_PROMOTE_REMOTE": str(origin), "SG_REPO": "x/y", "SG_PROMOTE_PROOF": "local",
                             "PATH": f"{bin_}:{os.environ['PATH']}"}
    ran = f'test "$({GIT} -C {origin} rev-parse stg)" = "$({GIT} -C {origin} rev-parse dev)"'
    return (["bash", str(d / "promote-stg.sh")], tmp_path, env, st, ran, None)


@pytest.mark.slow(reason="each case builds a git remote and runs a whole runner script")
@pytest.mark.heavy
@pytest.mark.parametrize("fixture", ["branchland_runner", "promote_runner"])   # the wave entry holds, never waits (#575): below
@PROCESS_BOUND
def test_each_gate_runner_waits_for_a_held_gate_lock_and_then_runs(fixture, request, tmp_path):
    """A runner that names gate.sh but bypasses it would run beside a holder. Each runner is started while the `gate` lock is
    held: it says it waits, its gate has not run when the holder lets go, and it runs after that."""
    cmd, cwd, env, st, ran, _marker = request.getfixturevalue(fixture)
    log, release, err = tmp_path / "holder.log", tmp_path / "release", tmp_path / "runner.err"
    # The holder lets go when told to, never on a timer: a runner that is slow to start must still find the lock held.
    holder_cmd = f'while [ ! -e {release} ]; do sleep 0.02; done; if {ran}; then echo early >> {log}; else echo clean >> {log}; fi'
    with _grouped(["bash", str(SCRIPTS / "serial.sh"), "gate", "bash", "-c", holder_cmd], env=_env(st),
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as holder:
        pid = st / "locks" / "gate" / "pid"
        deadline = time.time() + 10
        while not (pid.exists() and pid.read_text().strip()) and time.time() < deadline:
            time.sleep(0.02)
        assert pid.exists(), "the holder never took the gate lock"
        with err.open("w") as ef, _grouped(cmd, cwd=cwd, env=env, stdout=ef, stderr=subprocess.STDOUT) as runner:
            # The waiter is the runner's own serial.sh (the copy beside the runner): the holder runs the repository's copy. A
            # runner that bypassed gate.sh never starts one, and its gate then runs beside the holder, which the log below shows.
            mine = f"{tmp_path / 's' / 'serial.sh'} gate"
            deadline = time.time() + 20
            while time.time() < deadline and runner.poll() is None:
                ps = subprocess.run(["/bin/ps", "-ww", "-o", "command=", "-ax"], capture_output=True, text=True).stdout
                if mine in ps:
                    time.sleep(0.2)   # past its first look at the lock
                    break
                time.sleep(0.02)
            release.touch()
            runner.wait(timeout=60)
        holder.wait(timeout=60)
    out = err.read_text()
    assert WAIT_LINE.search(out), f"the runner never said it waits for the held lock: {out}"
    assert log.read_text().split() == ["clean"], f"the runner's gate ran beside the holder: {log.read_text()!r}"
    assert subprocess.run(["bash", "-c", ran]).returncode == 0, f"the runner never ran its gate after the holder: {out}"


@pytest.mark.slow(reason="starts a runner that never ends and waits for its process group to end")
@pytest.mark.heavy
def test_a_case_ends_a_runner_that_never_ends_with_its_process_group(tmp_path):
    """A case that starts a runner and leaves before it ends leaks the runner and its gate waiter, which polls for ever.
    The block ends every process it started: the never-ending runner and its waiter are gone, and no process from the
    case's folder is alive after the case."""
    d = tmp_path / "s"
    d.mkdir()
    pids = tmp_path / "pids"
    waiter = d / "waiter.sh"
    # Stands in for a runner's own serial.sh gate waiter: a process the runner forked, which polls for ever. It names both
    # pids, so the case reads them without the process table a sandbox may deny (ADR-0139).
    waiter.write_text(f'#!/bin/bash\necho $$ >> {pids}\n( exec sleep 300 ) &\necho $! >> {pids}\nwait\n')
    waiter.chmod(0o755)
    with _grouped(["bash", str(waiter)], cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as runner:
        deadline = time.time() + 10
        while time.time() < deadline and (not pids.exists() or len(pids.read_text().split()) < 2):
            time.sleep(0.02)
        started = pids.read_text().split() if pids.exists() else []
        assert len(started) == 2, f"the runner never started its waiter: {started}"
        assert all(_alive(p) for p in started), f"the runner ended on its own, so the cleanup was never proven: {started}"
        assert runner.poll() is None, "the runner ended on its own, so the cleanup was never proven"
    deadline = time.time() + 10
    while time.time() < deadline and any(_alive(p) for p in started):
        time.sleep(0.05)
    left = [p for p in started if _alive(p)]
    assert not left, f"a process the case started is alive after the case: {left}"


@pytest.mark.slow(reason="builds a git remote and runs wave.sh twice")
@pytest.mark.heavy
@PROCESS_BOUND
def test_a_wave_entry_under_a_held_gate_lock_holds_the_child_and_enters_on_a_later_pass(wave_runner, tmp_path):
    """The wave entry runs its gate in try mode (#575): a held `gate` lock holds the child out with one `WAVE HOLD` line, and its
    gate never runs beside the holder. The next pass, with the lock free, runs the gate."""
    cmd, cwd, env, st, ran, _marker = wave_runner
    release = tmp_path / "release"
    with _grouped(["bash", str(SCRIPTS / "serial.sh"), "gate", "bash", "-c", f"while [ ! -e {release} ]; do sleep 0.02; done"],
                  env=_env(st), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as holder:
        try:
            pid = st / "locks" / "gate" / "pid"
            deadline = time.time() + 10
            while not (pid.exists() and pid.read_text().strip()) and time.time() < deadline:
                time.sleep(0.02)
            assert pid.exists(), "the holder never took the gate lock"
            with _grouped(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as held:
                stdout, stderr = held.communicate(timeout=60)
            out = stdout + stderr
            assert held.returncode != 0 and re.search(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d WAVE HOLD #1: the gate lock is held by pid [0-9]+", out, re.M), out
            assert subprocess.run(["bash", "-c", ran]).returncode != 0, f"the entry ran its gate beside the holder: {out}"
        finally:
            release.touch()
            holder.wait(timeout=60)
    with _grouped(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as later:
        later_out, later_err = later.communicate(timeout=60)
    assert subprocess.run(["bash", "-c", ran]).returncode == 0, f"the later pass never ran its gate: {later_out}{later_err}"


def test_the_wave_runner_copies_every_script_wave_sh_uses():
    """wave.sh reads its helpers beside it, so a copy that omits one fails with a missing file, not the case it tests."""
    used = set(re.findall(r'\$here/([\w.-]+\.(?:sh|py))', (SCRIPTS / "wave.sh").read_text()))
    missing = sorted(name for name in used if name not in WAVE_SCRIPTS and name != "env.sh")   # the fixture writes env.sh
    assert not missing, f"wave.sh uses scripts the wave runner does not copy: {missing}"


@pytest.mark.parametrize("name", ["test-gate-lock.sh", "test-serial.sh"])
@PROCESS_BOUND
def test_a_gate_lock_shell_suite_is_executable(name):
    """A suite committed without its executable bit is dropped by any runner that starts suites by path."""
    assert os.access(SCRIPTS / name, os.X_OK), f"{name} is not executable"


@pytest.mark.heavy   # two gates take the lock in turn and wait on each other's line: 2.2 s on a CI runner
@PROCESS_BOUND
def test_two_hand_gates_started_at_once_run_one_after_the_other_in_short(tmp_path):
    """The short form of the soak below. The first gate holds the lock until the second prints its wait line, so the proof
    never races the second gate's start-up (a python start, seconds under load). The lock comes first and the slots
    second (#1166), so a gate queued here takes no slot while it waits."""
    d = _tree(tmp_path)
    log = tmp_path / "log"
    release = tmp_path / "release"
    cmd = ["bash", str(d / "gate.sh"), "sh", "-c", f"echo start >> {log}; while [ ! -e {release} ]; do sleep 0.02; done; echo end >> {log}"]
    env = _env(tmp_path / "st") | POLL | {"SERIAL_WAIT_SECONDS": "20", "SERIAL_HOLD_SECONDS": "20"}
    # The gate takes its slots against the project file above its working directory, and this repository lowers caps.gates
    # to one gate's worth (docs/squad/defaults.yaml): run from the repository root, the second gate could never take its
    # slots, so the case stopped proving the lock. The throwaway tree carries no project file, so the budget is the
    # plugin's, and the two gates prove the lock as intended.
    first = subprocess.Popen(cmd, env=env, cwd=str(d), stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 20
        while not log.exists() and time.time() < deadline:
            time.sleep(0.02)
        second = subprocess.Popen(cmd, env=env, cwd=str(d), stderr=subprocess.PIPE, text=True)
        seen = []
        for line in second.stderr:   # EOF ends the read: the second gate gives up after SERIAL_WAIT_SECONDS
            seen.append(line)
            if WAIT_LINE.search(line):
                break
    finally:
        release.touch()
    err = first.communicate(timeout=60)[1] + "".join(seen) + second.communicate(timeout=60)[1]
    assert log.read_text().split() == ["start", "end", "start", "end"], "two gates overlapped, or one was dropped"
    assert len(WAIT_LINE.findall(err)) == 1, err


@pytest.mark.heavy
@PROCESS_BOUND
def test_two_hand_gates_started_at_once_run_one_after_the_other(tmp_path):
    d = _tree(tmp_path)
    log = tmp_path / "log"
    cmd = ["bash", str(d / "gate.sh"), "sh", "-c", f"echo start >> {log}; sleep 2; echo end >> {log}"]
    first = subprocess.Popen(cmd, env=_env(tmp_path / "st"), cwd=str(d), stderr=subprocess.PIPE, text=True)
    time.sleep(0.5)
    second = subprocess.Popen(cmd, env=_env(tmp_path / "st"), cwd=str(d), stderr=subprocess.PIPE, text=True)
    err = first.communicate(timeout=60)[1] + second.communicate(timeout=60)[1]
    assert log.read_text().split() == ["start", "end", "start", "end"], "two gates overlapped, or one was dropped"
    assert len(WAIT_LINE.findall(err)) == 1, err


@PROCESS_BOUND
def test_a_hand_gate_whose_holder_died_takes_the_lock(tmp_path):
    d = _tree(tmp_path)
    lock = tmp_path / "st" / "locks" / "gate"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("99999999\n")
    p = subprocess.run(["bash", str(d / "gate.sh"), "sh", "-c", "exit 7"], env=_env(tmp_path / "st"),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 7, p.stdout + p.stderr
    assert not lock.exists(), "the lock is left behind"


@PROCESS_BOUND
def test_a_cut_short_watchdog_sleep_is_not_a_deadline_in_short(tmp_path):
    """The short form of the test below: the watchdog sleep is found by its place in the holder's tree (holder, watchdog
    subshell, sleep) and cut at once, and the holder keeps its command's exit code."""
    d = _tree(tmp_path)
    p = subprocess.Popen(["bash", str(d / "gate.sh"), "sh", "-c", "sleep 0.5; exit 7"],
                         env=_env(tmp_path / "st") | {"SERIAL_HOLD_SECONDS": "4242"},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cut = False
    for _ in range(40):   # the watchdog is named by the pid the holder records, never by a sleep's argument (#1660)
        wd = _watchdog(tmp_path / "st", "gate")
        if wd:
            subprocess.run(["kill", "-TERM", wd], check=False)
            cut = True
            break
        time.sleep(0.02)
    assert p.wait(timeout=30) == 7, "a cut-short sleep failed the holder"
    assert cut, "the holder's watchdog sleep was not found"


@pytest.mark.heavy
@PROCESS_BOUND
def test_a_cut_short_watchdog_sleep_is_not_a_deadline(tmp_path):
    """The holder's cleanup TERMs its watchdog's process group when its command ends, and the sleep of that watchdog can be
    the process that dies. A sleep cut short is not a deadline: the holder keeps its command's exit code, never 75.
    SERIAL_HOLD_SECONDS=4242 puts the deadline hours away and names that one sleep, so the cut is what ends it. Finding it
    reads the process table, and a reading is not the behavior: the holder runs again when nothing was found to cut in time."""
    d = _tree(tmp_path)
    rc, cut = 1, False
    for _ in range(3):
        p = subprocess.Popen(["bash", str(d / "gate.sh"), "sh", "-c", "sleep 3; exit 7"],
                             env=_env(tmp_path / "st") | {"SERIAL_HOLD_SECONDS": "4242"},
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        watchdog_sleep = ""   # the pid the holder writes for its watchdog (#1660): its sleeps are short steps, so no argument names it
        for _ in range(30):
            watchdog_sleep = _watchdog(tmp_path / "st", "gate")
            if watchdog_sleep:
                break
            time.sleep(0.1)
        if watchdog_sleep:
            cut = True
            subprocess.run(["kill", "-TERM", watchdog_sleep], check=False)
        rc = p.wait(timeout=60)
        if cut:
            break
    assert cut, "the holder's watchdog sleep was not found"
    assert rc == 7, "a cut-short sleep failed the holder"
