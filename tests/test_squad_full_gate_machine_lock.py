"""Issue #81: two full suite gates never run at once on one machine.

The loop's land pass runs the fork gate (the shell suites and the pytest suite); a child working an issue ran the same
suites in its own worktree. Eight cores take one of them, both finish later and the owner's editor loses the machine.
The fix: the pytest half of the gate holds the machine-wide `gate` lock `serial.sh` already owns, so a second full gate
prints one wait line, its pytest half starts when the first one's ends, and the lock directory never carries two pids.
Issue #94: the lock is held around the pytest half only, never across the shell suites; one holder that held it for the
whole shell suite blocked every other gate for a quarter hour.

Host-free: the real gate scripts run in a throwaway tree with a throwaway SG_STATE, so nothing here touches the live
loop's lock, the emulator or the network.
"""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
WAIT_LINE = re.compile(r"^serial: waiting for lock gate held by pid [0-9]+$", re.M)


def _tree(tmp_path, suites):
    """A scripts folder holding the real gate scripts and the given fake shell suites (name -> shell body)."""
    d = tmp_path / "contrib" / "squad-goal" / "scripts"
    d.mkdir(parents=True)
    for name in ("shell-suites.sh", "fork-gate.sh", "serial.sh"):
        shutil.copy(SCRIPTS / name, d / name)
    for name, body in suites.items():
        (d / name).write_text("#!/bin/bash\n" + body + "\n")
    return d


def _env(cwd, **over):
    """The environment one gate runs in. The live loop's SG_*/FG_* never reach it (its own SG_STATE would make this test
    wait behind the machine's gate lock instead of its own), and PATH carries no uv, so every pytest command is pinned."""
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    e["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    e["SG_STATE"] = str(Path(cwd) / "state")
    e.update(over)
    return e


def _run(*cmd, cwd, env=None, timeout=60):
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True,
                          env=_env(cwd, **(env or {})), timeout=timeout)


def _gate(d, cwd, gui="true", **env):
    return _run("bash", str(d / "fork-gate.sh"), gui, cwd=cwd, env={"FG_SUITES": "full", **env})


def test_the_suite_phase_of_the_gate_holds_the_machine_gate_lock(tmp_path):
    """The pytest half runs inside the `gate` holder, so a child's own full gate waits for it and not the other way round."""
    d = _tree(tmp_path, {"test-a.sh": "echo ok a"})
    seen = tmp_path / "held"
    py = f"printf '%s\\n' \"held=${{SG_SERIAL_HELD:-none}}\" > {seen}; test -f \"$SG_STATE/locks/gate/pid\" && echo lock >> {seen}"
    p = _gate(d, tmp_path, SG_FORK_PY_CMD=py)
    assert p.returncode == 0, p.stdout + p.stderr
    assert seen.read_text().split() == ["held=gate", "lock"], (
        "the pytest gate ran outside the machine-wide gate lock, so a second full gate would take the machine with it")
    assert not (tmp_path / "state" / "locks" / "gate").exists(), "the gate lock outlived the gate that took it"


def test_two_full_gates_at_once_never_overlap_and_the_second_waits_once(tmp_path):
    """Both gates of a race run to green, their pytest halves never overlap, and the second says it waits once.

    The shell suites hold no lock (#94), so the two gates' shell suites may run side by side; the full pytest gate is what
    takes the machine, and one of those runs whole before any line of the other."""
    d = _tree(tmp_path, {"test-slow.sh": 'echo "$FG_TAG suite start" >> "$LOG"; sleep 2; echo "$FG_TAG suite end" >> "$LOG"'})
    log = tmp_path / "log"
    py = 'echo "$FG_TAG pytest start" >> "$LOG"; sleep 2; echo "$FG_TAG pytest end" >> "$LOG"'
    gates = [subprocess.Popen(["bash", str(d / "fork-gate.sh"), "true"], cwd=tmp_path, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,
                              env=_env(tmp_path, FG_SUITES="full", SG_FORK_PY_CMD=py, LOG=str(log), FG_TAG=tag))
             for tag in ("A", "B")]
    outs = [g.communicate(timeout=60)[0] for g in gates]
    assert [g.returncode for g in gates] == [0, 0], "".join(outs)
    lines = log.read_text().splitlines()
    assert sorted(lines) == sorted(["A suite start", "A suite end", "A pytest start", "A pytest end",
                                    "B suite start", "B suite end", "B pytest start", "B pytest end"]), lines
    pytest = [ln for ln in lines if " pytest " in ln]
    first = pytest[0].split()[0]
    assert pytest[:2] == [f"{first} pytest start", f"{first} pytest end"], f"two full pytest gates ran side by side: {lines}"
    waiting = WAIT_LINE.findall("".join(outs))
    assert len(waiting) == 1, f"the second full gate prints one wait line; got {len(waiting)}"


def _holder(d, cwd, log, seconds):
    """Another gate holding the machine-wide lock for `seconds`; returns once its pid is in the lock directory."""
    h = subprocess.Popen(["bash", str(d / "serial.sh"), "gate", "bash", "-c", f"sleep {seconds}; echo holder end >> {log}"],
                         cwd=cwd, env=_env(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid = Path(cwd) / "state" / "locks" / "gate" / "pid"
    for _ in range(100):
        if pid.exists() and pid.read_text().strip():
            return h
        time.sleep(0.05)
    raise AssertionError("the holder never took the gate lock")


def test_the_shell_suites_of_a_gate_run_while_another_gate_holds_the_lock(tmp_path):
    """Only the pytest half waits for the holder (#94): the shell suites run at once, the pytest half after the holder ends."""
    log = tmp_path / "log"
    d = _tree(tmp_path, {"test-a.sh": f"echo suite >> {log}"})
    holder = _holder(d, tmp_path, log, 4)
    p = _gate(d, tmp_path, SG_FORK_PY_CMD=f"echo pytest >> {log}")
    holder.wait(timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert log.read_text().split("\n")[:3] == ["suite", "holder end", "pytest"], (
        f"the shell suites waited for the gate lock, so one holder blocks every gate for its whole run: {log.read_text()!r}")
    assert len(WAIT_LINE.findall(p.stdout + p.stderr)) == 1, p.stdout + p.stderr


def test_the_shell_suites_alone_take_no_gate_lock(tmp_path):
    """shell-suites.sh run on its own does not take or wait for the machine-wide gate lock (#94)."""
    log = tmp_path / "log"
    d = _tree(tmp_path, {"test-a.sh": f"echo suite >> {log}"})
    holder = _holder(d, tmp_path, log, 4)
    p = _run("bash", str(d / "shell-suites.sh"), cwd=tmp_path)
    holder.wait(timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert log.read_text().split() == ["suite", "holder", "end"], log.read_text()
    assert not WAIT_LINE.findall(p.stdout + p.stderr), p.stderr


def test_an_overlapping_gate_starts_after_the_holder_ends(tmp_path):
    """The waiter starts when the first gate ends, not while it holds the lock."""
    d = _tree(tmp_path, {"test-a.sh": "echo ok a"})
    log = tmp_path / "log"
    py = f'echo py start >> {log}; sleep 3; echo py end >> {log}'
    first = subprocess.Popen(["bash", str(d / "fork-gate.sh"), "true"], cwd=tmp_path, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=_env(tmp_path, FG_SUITES="full", SG_FORK_PY_CMD=py))
    time.sleep(1)   # the first gate owns the lock by now: its pytest half sleeps 3 s
    second = _gate(d, tmp_path, SG_FORK_PY_CMD=f'echo py2 >> {log}')
    out = first.communicate(timeout=60)[0]
    assert first.returncode == 0 and second.returncode == 0, out + second.stdout
    assert log.read_text().split() == ["py", "start", "py", "end", "py2"], log.read_text()
    assert len(WAIT_LINE.findall(out + second.stdout + second.stderr)) == 1, out + second.stderr


def test_the_python_class_gate_waits_for_the_machine_lock_too(tmp_path):
    """The `python` class is a full suite run on its own: it takes the same lock."""
    d = _tree(tmp_path, {})
    log = tmp_path / "log"
    py = f'echo start >> {log}; sleep 3; echo end >> {log}'
    first = subprocess.Popen(["bash", str(d / "fork-gate.sh"), "true"], cwd=tmp_path, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=_env(tmp_path, FG_SUITES="python", SG_FORK_PY_CMD=py))
    time.sleep(1)
    second = _run("bash", str(d / "fork-gate.sh"), "true", cwd=tmp_path,
                  env={"FG_SUITES": "python", "SG_FORK_PY_CMD": f'echo second >> {log}'})
    out = first.communicate(timeout=60)[0]
    assert first.returncode == 0 and second.returncode == 0, out + second.stdout
    assert log.read_text().split() == ["start", "end", "second"], log.read_text()
    assert len(WAIT_LINE.findall(out + second.stdout + second.stderr)) == 1, out + second.stderr
