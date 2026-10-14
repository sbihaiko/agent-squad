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
