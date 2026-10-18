"""Issue #392: one gate at a time. A machine-wide lock queues every gate.

The branchland local gate, the wave entry gate, the promotion gate and a hand-started gate all take the `gate` lock
`serial.sh` owns. A second gate waits for the first and then runs; it is never dropped; the wait shows on one log line.
Host-free: a throwaway SG_STATE and a throwaway scripts folder; the runners are read, the hand gate is run.
"""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
WAIT_LINE = re.compile(r"^serial: waiting for lock gate held by pid [0-9]+$", re.M)
TAKES_LOCK = re.compile(r"(?<![\w-])gate\.sh\"?\s")
RUNNERS = ("branchland.sh", "wave.sh", "promote-stg.sh")


GIT = "/usr/bin/git"
POLL = {"SERIAL_POLL_SECONDS": "0.05"}   # a waiter looks at the lock every 50 ms, so a short hold is enough to prove a wait


def _env(state):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    e["SG_STATE"] = str(state)
    return e


def _tree(tmp_path):
    d = tmp_path / "scripts"
    d.mkdir()
    for name in ("gate.sh", "serial.sh"):
        shutil.copy(SCRIPTS / name, d / name)
    return d


def _code(path):
    return "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())


@pytest.mark.parametrize("runner", RUNNERS)
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


@pytest.fixture
def wave_runner(tmp_path):
    """wave.sh enter in a child's worktree, with a gate that records its run and fails (so nothing is pushed)."""
    d = tmp_path / "s"
    d.mkdir()
    _copy(SCRIPTS, ("wave.sh", "address.sh", "gate.sh", "serial.sh"), d)
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
    env = _env(st) | POLL | {"SG_WAVE_GATES": f"echo gate >> {marker}; exit 1"}
    return (["bash", str(d / "wave.sh"), "enter", "run-1", "1"], child, env, st, f"test -e {marker}", marker)


@pytest.fixture
def branchland_runner(tmp_path):
    """One branchland pass over a work branch whose review approved it and whose tip has no check run: the local gate runs."""
    d = tmp_path / "s"
    d.mkdir()
    _copy(SCRIPTS, ("gate.sh", "serial.sh", "branchland.sh", "revformat.py", "workbranches.sh", "ffland.sh", "address.sh",
                    "ghread.py", "ghthrottle.py", "ghcall.sh"), d)
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
    """promote-stg.sh with dev one commit ahead of stg and the local proof for the dev tip on the status line: the promotion pushes stg."""
    d = tmp_path / "s"
    d.mkdir()
    _copy(SCRIPTS, ("promote-stg.sh", "gate.sh", "serial.sh"), d)
    co, origin = _repo_with_remote(tmp_path, ["stg"])
    _git("commit", "-q", "--allow-empty", "-m", "one", cwd=co)
    _git("push", "-q", str(origin), "dev", cwd=co)
    st = tmp_path / "st"
    st.mkdir()
    dev = subprocess.run([GIT, "-C", str(co), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (st / "gate-status-full").write_text(f"{int(time.time())}\tfull\tpass\t{dev}\n")   # the promotion reads the full proof here, which a fast gate never overwrites (#472)
    env = _env(st) | POLL | {"SG_PROMOTE_REMOTE": str(origin), "SG_REPO": "x/y", "SG_PROMOTE_PROOF": "local"}
    ran = f'test "$({GIT} -C {origin} rev-parse stg)" = "$({GIT} -C {origin} rev-parse dev)"'
    return (["bash", str(d / "promote-stg.sh")], tmp_path, env, st, ran, None)


@pytest.mark.slow(reason="each case builds a git remote and runs a whole runner script")
@pytest.mark.heavy
@pytest.mark.parametrize("fixture", ["wave_runner", "branchland_runner", "promote_runner"])
def test_each_gate_runner_waits_for_a_held_gate_lock_and_then_runs(fixture, request, tmp_path):
    """A runner that names gate.sh but bypasses it would run beside a holder. Each runner is started while the `gate` lock is
    held: it says it waits, its gate has not run when the holder lets go, and it runs after that."""
    cmd, cwd, env, st, ran, _marker = request.getfixturevalue(fixture)
    log, release, err = tmp_path / "holder.log", tmp_path / "release", tmp_path / "runner.err"
    # The holder lets go when told to, never on a timer: a runner that is slow to start must still find the lock held.
    holder_cmd = f'while [ ! -e {release} ]; do sleep 0.02; done; if {ran}; then echo early >> {log}; else echo clean >> {log}; fi'
    holder = subprocess.Popen(["bash", str(SCRIPTS / "serial.sh"), "gate", "bash", "-c", holder_cmd], env=_env(st),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid = st / "locks" / "gate" / "pid"
    deadline = time.time() + 10
    while not (pid.exists() and pid.read_text().strip()) and time.time() < deadline:
        time.sleep(0.02)
    assert pid.exists(), "the holder never took the gate lock"
    with err.open("w") as ef:
        runner = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=ef, stderr=subprocess.STDOUT)
        # The waiter is the runner's own serial.sh (the copy beside the runner): the holder runs the repository's copy. A runner
        # that bypassed gate.sh never starts one, and its gate then runs beside the holder, which the log below shows.
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


@pytest.mark.parametrize("name", ["test-gate-lock.sh", "test-serial.sh"])
def test_a_gate_lock_shell_suite_is_executable(name):
    """A suite committed without its executable bit is dropped by any runner that starts suites by path."""
    assert os.access(SCRIPTS / name, os.X_OK), f"{name} is not executable"


def test_two_hand_gates_started_at_once_run_one_after_the_other_in_short(tmp_path):
    """The short form of the soak below: sub-second holds and a 50 ms poll, so the fast battery proves it."""
    d = _tree(tmp_path)
    log = tmp_path / "log"
    cmd = ["bash", str(d / "gate.sh"), "sh", "-c", f"echo start >> {log}; sleep 0.25; echo end >> {log}"]
    env = _env(tmp_path / "st") | POLL | {"SERIAL_WAIT_SECONDS": "20", "SERIAL_HOLD_SECONDS": "20"}
    first = subprocess.Popen(cmd, env=env, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 5
    while not log.exists() and time.time() < deadline:
        time.sleep(0.02)
    second = subprocess.Popen(cmd, env=env, stderr=subprocess.PIPE, text=True)
    err = first.communicate(timeout=30)[1] + second.communicate(timeout=30)[1]
    assert log.read_text().split() == ["start", "end", "start", "end"], "two gates overlapped, or one was dropped"
    assert len(WAIT_LINE.findall(err)) == 1, err


@pytest.mark.heavy
def test_two_hand_gates_started_at_once_run_one_after_the_other(tmp_path):
    d = _tree(tmp_path)
    log = tmp_path / "log"
    cmd = ["bash", str(d / "gate.sh"), "sh", "-c", f"echo start >> {log}; sleep 2; echo end >> {log}"]
    first = subprocess.Popen(cmd, env=_env(tmp_path / "st"), stderr=subprocess.PIPE, text=True)
    time.sleep(0.5)
    second = subprocess.Popen(cmd, env=_env(tmp_path / "st"), stderr=subprocess.PIPE, text=True)
    err = first.communicate(timeout=60)[1] + second.communicate(timeout=60)[1]
    assert log.read_text().split() == ["start", "end", "start", "end"], "two gates overlapped, or one was dropped"
    assert len(WAIT_LINE.findall(err)) == 1, err


def test_a_hand_gate_whose_holder_died_takes_the_lock(tmp_path):
    d = _tree(tmp_path)
    lock = tmp_path / "st" / "locks" / "gate"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("99999999\n")
    p = subprocess.run(["bash", str(d / "gate.sh"), "sh", "-c", "exit 7"], env=_env(tmp_path / "st"),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 7, p.stdout + p.stderr
    assert not lock.exists(), "the lock is left behind"


def test_a_cut_short_watchdog_sleep_is_not_a_deadline_in_short(tmp_path):
    """The short form of the test below: the watchdog sleep is found by its place in the holder's tree (holder, watchdog
    subshell, sleep) and cut at once, and the holder keeps its command's exit code."""
    d = _tree(tmp_path)
    p = subprocess.Popen(["bash", str(d / "gate.sh"), "sh", "-c", "sleep 0.5; exit 7"],
                         env=_env(tmp_path / "st") | {"SERIAL_HOLD_SECONDS": "4242"},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cut = False
    for _ in range(40):
        rows = [r.split(None, 2) for r in subprocess.run(["/bin/ps", "-ww", "-o", "pid=,ppid=,command=", "-ax"],
                                                         capture_output=True, text=True).stdout.splitlines()]
        parent = {r[0]: r[1] for r in rows if len(r) > 2}
        sleeps = [r[0] for r in rows if len(r) > 2 and r[2].split()[0].endswith("sleep") and r[2].split()[-1] == "4242"
                  and parent.get(parent.get(r[0], ""), "") == str(p.pid)]
        if sleeps:
            subprocess.run(["kill", "-TERM", sleeps[0]], check=False)
            cut = True
            break
        time.sleep(0.02)
    assert p.wait(timeout=30) == 7, "a cut-short sleep failed the holder"
    assert cut, "the holder's watchdog sleep was not found"


@pytest.mark.heavy
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
        watchdog_sleep = ""
        for _ in range(30):
            ps = subprocess.run(["/bin/ps", "-o", "pid=,command=", "-ax"], capture_output=True, text=True).stdout.splitlines()
            watchdog_sleep = next((w[0] for w in map(str.split, ps) if len(w) > 1 and w[1] == "sleep" and w[-1] == "4242"), "")
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
