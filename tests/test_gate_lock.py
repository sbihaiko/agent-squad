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
