"""The shell-suite runner's seam: find each `test-*.sh` beside the scripts, run one, and say why it failed.

`contrib/squad-goal/scripts/shell-suites.sh` runs the same files. Each suite runs the way it does there: every inherited
`SG_*` variable cleared and `HOME` at its own temp dir, so the live loop's state never reaches an assertion.

The measured run time of each suite sits beside the runner, in `tests/shell_suites_seconds.txt` (`name seconds`, `#` starts
a comment). A suite of 1 s or more is heavy, so the fast battery (`-m "not heavy"`) leaves it out before it runs. One over
the slow line carries a `slow` reason, so the 20 s cap does not fail a real script suite. A new suite needs no edit here: it
is found, and it counts as fast until the list says otherwise. The heavy line in `tests/slowcap.py` names one that is not.
"""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES_DIR = ROOT / "contrib" / "squad-goal" / "scripts"
SECONDS_LIST = Path(__file__).resolve().parent / "shell_suites_seconds.txt"
HEAVY_SECONDS = 1.0
SLOW_SECONDS = 15.0
SUITE_TIMEOUT_SECONDS = 300.0
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


def _run(suite, timeout, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    if extra_env:
        env.update(extra_env)   # a variable a caller sets on purpose, after the clear: the suite must survive it
    with tempfile.TemporaryDirectory() as home:
        env["HOME"] = home
        return subprocess.run(["bash", str(suite)], cwd=Path(suite).parent, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)


def run(suite, timeout=SUITE_TIMEOUT_SECONDS, extra_env=None):
    """Run one suite the way `problem` does -- every inherited `SG_*` cleared and `HOME` in its own temp dir -- and hand back
    its result, so a module reads the run by this one seam instead of spawning the suite with the ambient environment. `extra_env`
    names a variable to set on purpose (a lock the gate would hand down), so a caller can prove the suite survives it."""
    return _run(Path(suite), timeout, extra_env)


def problem(suite, timeout=SUITE_TIMEOUT_SECONDS):
    """None when the suite is green; else a message that shows its FAIL lines, or the end of its output."""
    name = Path(suite).name
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
