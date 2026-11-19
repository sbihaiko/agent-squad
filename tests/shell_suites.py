"""The shell-suite runner's seam: find each `test-*.sh` beside the scripts, run one, and say why it failed.

`contrib/squad-goal/scripts/shell-suites.sh` runs the same files. Each suite runs the way it does there: every inherited
`SG_*` and `SERIAL_*` variable cleared and `HOME` at its own temp dir, so the live loop's state never reaches an assertion.

The measured run time of each suite sits beside the runner, in `tests/shell_suites_seconds.txt` (`name seconds`, `#` starts
a comment); `python3 -m tests.measure_shell_suites` writes it. A suite of 0.5 s or more is heavy, so the fast battery
(`-m "not heavy"`) leaves it out before it runs: the line is half the 1 s rule, since a full run's load makes a 1 s suite take
2.5 s (#629). One over the slow line carries a `slow` reason, so neither the cap nor the call deadline decides a real script
suite under that load. A new suite is found with no edit here, and a test fails until the list measures it.

Both lines follow the machine's own measure, never a wall second guessed on a quiet one (#736). The cap excuses a call over
its line only up to the slowdown `tests/slowcap.py` measures and bounds (`MAX_CONTENTION_FACTOR`), so the slow line is the cap
divided by that factor: a suite at or over it can cross the cap under load. One run of a suite is bounded by its measured time
times the same factor and times the pressure the machine carries at call time (`slowcap.pressure_factor`, the load average
per CPU), never below a floor, so a bound tighter than the load it may meet never fails a suite for the machine's work rather
than its own. A spin measure alone misses a suite that clones and pushes trees: it waits on the disk and spins for none of it,
so it took 485.2 s under load 24.6 against a 424.76 s bound and was stopped while green (#1885). The pressure factor is
measured and bounded too, so a suite that really costs more keeps a finite bound.
"""
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from tests import slowcap

ROOT = Path(__file__).resolve().parent.parent
SUITES_DIR = ROOT / "contrib" / "squad-goal" / "scripts"
SECONDS_LIST = Path(__file__).resolve().parent / "shell_suites_seconds.txt"
HEAVY_SECONDS = 0.5
LOAD_FACTOR = slowcap.MAX_CONTENTION_FACTOR
SLOW_SECONDS = slowcap.DEFAULT_CAP_SECONDS / LOAD_FACTOR   # 5 s: at this line the cap can decide a suite under load (#736)
MIN_TIMEOUT_SECONDS = 300.0
TAIL_LINES = 20


def discover(directory=SUITES_DIR):
    return sorted(Path(directory).glob("test-*.sh"))


def measured():
    """Name to measured seconds, from the list beside the runner."""
    seconds = {}
    for line in SECONDS_LIST.read_text().splitlines():
        fields = line.split("#", 1)[0].split()
        if fields:
            seconds[fields[0]] = float(fields[1])
    return seconds


def heavy_names():
    return [name for name, seconds in measured().items() if seconds >= HEAVY_SECONDS]


def is_heavy(suite):
    return Path(suite).name in heavy_names()


def is_slow(suite):
    return measured().get(Path(suite).name, 0.0) > SLOW_SECONDS


def timeout_for(suite):
    """The wall bound of one run of `suite`: its own measured time times the slowdown the cap excuses times the pressure the
    machine carries now, never below the floor a short suite gets. The pressure is the load average per CPU, so a suite that
    only waited on a busy disk is not failed for the machine's wall, while a suite whose cost is past the bounded widening
    still is (#736, #1885)."""
    return max(MIN_TIMEOUT_SECONDS,
               measured().get(Path(suite).name, 0.0) * LOAD_FACTOR * slowcap.pressure_factor())


def end_group(pgid):
    """End what a suite left in its own process group: TERM, then KILL for a process that traps it. True when a process
    was still there. A suite started the real watch loop from a fixture copy and left it as an orphan that ran a cycle
    every few seconds for hours (#1742); the group is the one fact such a leftover keeps, whatever its parent became.
    A signal the system refuses (a sandboxed session) ends nothing and is no error: the suite's verdict stands."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        return False
    time.sleep(0.2)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass
    return True


# (#2306) A git hook or a gate exports these, and a fixture's `git init --bare` or `git config` in its own folder then writes
# the caller's checkout instead. caps.sh (sg_git_env_scrub) keeps the same list for the shell gates.
GIT_LOCATION = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")


def _run(suite, timeout=None, extra_env=None):
    timeout = timeout_for(suite) if timeout is None else timeout
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SG_", "SERIAL_")) and k not in GIT_LOCATION}   # the gate exports its lock bounds (#755)
    if extra_env:
        env.update(extra_env)   # a variable a caller sets on purpose, after the clear: the suite must survive it
    with tempfile.TemporaryDirectory() as home:
        env["HOME"] = home
        # Its own session (#1742): the suite leads a process group no other process is in, so what it leaves behind is
        # found by that group and ended below, whatever the suite's own cleanup did.
        proc = subprocess.Popen(["bash", str(suite)], cwd=Path(suite).parent, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            end_group(proc.pid)   # the group first: a child that holds the pipes would keep the read below waiting
            proc.kill()
            proc.communicate()
            raise
        finally:
            end_group(proc.pid)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)

def run(suite, timeout=None, extra_env=None):
    """Run one suite the way `problem` does -- every inherited `SG_*` and `SERIAL_*` cleared and `HOME` in its own temp dir -- and hand back
    its result, so a module reads the run by this one seam instead of spawning the suite with the ambient environment. `extra_env`
    names a variable to set on purpose (a lock the gate would hand down), so a caller can prove the suite survives it. A caller
    that names no `timeout` gets `timeout_for(suite)`: the suite's own measure, never a hand-tuned wall bound (#736)."""
    return _run(Path(suite), timeout, extra_env)


def problem(suite, timeout=None):
    """None when the suite is green; else a message that shows its FAIL lines, or the end of its output."""
    name = Path(suite).name
    timeout = timeout_for(suite) if timeout is None else timeout
    try:
        done = _run(suite, timeout)
    except subprocess.TimeoutExpired:
        return f"{name} did not end within {timeout:g} s and was stopped"
    lines = (done.stdout + done.stderr).splitlines()
    fails = [line for line in lines if line.startswith("FAIL")]
    if not fails and done.returncode == 0:
        return None
    shown = fails or lines[-TAIL_LINES:]
    return f"{name} failed (exit {done.returncode}):\n" + "\n".join(shown)
