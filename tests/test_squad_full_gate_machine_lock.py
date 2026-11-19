"""Issue #81/#94: no full suite gate runs on this machine, and the one a landing needs is CI's.

The loop's land pass used to run the full gate here -- the shell suites and the pytest suite -- and a child ran the same
suites in its own worktree: eight cores take one of them, both finish later, and the owner's editor loses the machine. The
gate's pytest half therefore held the machine-wide `gate` lock `serial.sh` owns (#81), and never held it across the shell
suites (#94). The local land path retired (#115): `forkclose.sh` and the gate script it ran are gone, and the landing pass
`branchland.sh` gates the tip it lands on the check runs GitHub reports for it. So the one full gate of a landing runs in
CI, on the pushed commit, and only a landing pushes, so the run the gate reads is one the branch carries. The group and
its queue belong to the CI rule, and `tests/test_tooling.py` guards them.
The gates that still run on this machine -- the branchland local gate, the wave entry gate, the promotion gate and a hand
gate -- queue behind the `gate` lock again (#392); no other script takes it, and the shell suites still run unlocked.
No accepted ADR records the earlier rule that no script takes the lock, so no ADR is superseded here.

Host-free: the surviving scripts run in a throwaway tree with a throwaway SG_STATE, and the workflow file is read, never run.
"""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
ROOT = SCRIPTS.parent.parent.parent
WAIT_LINE = re.compile(r"^serial: waiting for lock gate held by pid [0-9]+$", re.M)
GATE_LOCK = re.compile(r"serial\.sh\"?\s+gate\b|locks/gate\b(?!/pid)|(?<![\w-])gate\.sh\"?\s")
RETIRED = ("fork-gate.sh", "forkclose.sh", "forkonly.sh", "sync-to-fork.sh", "refresh-from-fork.sh")


def _scripts():
    """Every script of the loop. The suites beside them are left out: they name the pieces they drive."""
    return sorted(p for p in (*SCRIPTS.glob("*.sh"), *SCRIPTS.glob("*.py"))
                  if not p.name.startswith(("test-", "test_")))


def _env(cwd, **over):
    """The environment a run of the surviving scripts gets. The live loop's SG_*/FG_* never reach it: its own SG_STATE
    would make the run wait behind the machine's lock instead of its own."""
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    e["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    e["SG_STATE"] = str(Path(cwd) / "state")
    e.update(over)
    return e


def _run(*cmd, cwd, env=None, timeout=60):
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True,
                          env=_env(cwd, **(env or {})), timeout=timeout)


def _tree(tmp_path, suites):
    """A scripts folder holding the real shell-suite runner and serial.sh, plus the given fake suites."""
    d = tmp_path / "contrib" / "squad-goal" / "scripts"
    d.mkdir(parents=True)
    for name in ("shell-suites.sh", "serial.sh", "caps.sh"):   # caps.sh holds the git scrub shell-suites.sh requires (#2306)
        shutil.copy(SCRIPTS / name, d / name)
    for name, body in suites.items():
        (d / name).write_text("#!/bin/bash\n" + body + "\n")
    return d


def _holder(d, cwd, log, seconds):
    """A holder of the machine-wide `gate` lock for `seconds`; returns once its pid is in the lock directory."""
    h = subprocess.Popen(["bash", str(d / "serial.sh"), "gate", "bash", "-c", f"sleep {seconds}; echo holder end >> {log}"],
                         cwd=cwd, env=_env(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid = Path(cwd) / "state" / "locks" / "gate" / "pid"
    for _ in range(100):
        if pid.exists() and pid.read_text().strip():
            return h
        time.sleep(0.05)
    raise AssertionError("the holder never took the gate lock")


def test_only_the_gate_runners_take_the_machine_wide_gate_lock():
    """#81 and #392: the gate of a landing runs in CI, and the gates that still run here -- the branchland local gate, the wave
    entry gate, the promotion pass and its gate (#459), the fast gate, a hand gate and the daily shell-suite pass -- queue behind
    one lock. No other script takes it. A comment never counts.
    The one call that names gate.sh and takes nothing is its `--history` door (#1927): it appends a history line with no lock
    and no slots, so a proof that must never hold a landing records its gate with it. It is read as no take, and the suite
    proves such a call exists, so the exemption never hides a real one."""
    TIP_GATE = SCRIPTS / "tip-gate.sh"
    assert 'gate.sh" --history' in TIP_GATE.read_text(), "the tip gate records through the lock-free history door"

    def takes(path):
        body = "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())
        return GATE_LOCK.search(body.replace('gate.sh" --history', ""))

    takers = sorted(p.name for p in _scripts() if takes(p))
    assert takers == ["branchland.sh", "daysuites.sh", "fast-gate.sh", "gate.sh", "promote-pass.sh", "promote-stg.sh", "wave.sh"], takers


def test_a_read_of_the_gate_holder_pid_is_not_a_take():
    """The loop switch reads the holder's pid to list what a stop kills; a read never takes the lock (#923)."""
    assert not GATE_LOCK.search('g=$(cat "$SG_STATE/locks/gate/pid" 2>/dev/null)')
    assert not GATE_LOCK.search("the held gate (locks/gate/pid naming a live pid)")
    assert GATE_LOCK.search('mkdir "$SG_STATE/locks/gate"') and GATE_LOCK.search('bash "$d/serial.sh" gate cmd')


def test_the_local_gate_and_the_land_path_that_ran_it_are_retired():
    """#115: the gate script and the pass that ran it go; the shell-suite runner the loop still runs by hand stays."""
    for name in RETIRED:
        assert not (SCRIPTS / name).exists(), f"{name} is back: the local land path is retired (#115)"
    assert (SCRIPTS / "shell-suites.sh").exists(), "the shell suites are the half that stays"


@pytest.mark.heavy
def test_the_shell_suites_take_no_gate_lock_and_run_beside_a_holder(tmp_path):
    """#94: whoever holds the machine-wide lock, the shell suites run at once, print no wait line, and leave it alone."""
    log = tmp_path / "log"
    d = _tree(tmp_path, {"test-a.sh": f"echo suite >> {log}"})
    holder = _holder(d, tmp_path, log, 4)
    pid = Path(tmp_path) / "state" / "locks" / "gate" / "pid"
    held = pid.read_text().strip()
    p = _run("bash", str(d / "shell-suites.sh"), cwd=tmp_path)
    assert pid.exists() and pid.read_text().strip() == held, "the shell suites took the holder's lock away"
    holder.wait(timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert log.read_text().split() == ["suite", "holder", "end"], (
        f"the shell suites waited for the gate lock, so one holder blocks them for its whole run: {log.read_text()!r}")
    assert not WAIT_LINE.findall(p.stdout + p.stderr), p.stdout + p.stderr


def test_the_gate_of_a_landing_is_the_ci_check_job():
    """#81 where the gate runs now: the CI check job runs the suite, and only a landing reaches it (#398). A work
    branch starts no run: the local fast gate proves it before it lands. The concurrency group and its queue belong to
    the CI rule, and `tests/test_tooling.py` guards that rule in one place."""
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    runs = [step["run"] for job in wf["jobs"].values() for step in job["steps"] if "run" in step]
    assert any("pytest" in r for r in runs), runs


def test_the_landing_pass_reads_the_checks_of_the_tip():
    """The gate's caller now: branchland.sh lands a tip only when its check runs are green, and the local gate
    (`fast-gate.sh`) is what a tip with no check run is proved by while Actions is off (#385)."""
    src = (SCRIPTS / "branchland.sh").read_text()
    assert "check-runs" in src and "SG_LOCAL_GATE" in src, "the landing pass no longer gates the tip on its checks"
