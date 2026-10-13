"""Issue #81: two full suite gates never run at once on one machine.

The loop's land pass runs the fork gate (the shell suites and the pytest suite); a child working an issue ran the same
suites in its own worktree. Eight cores take one of them, both finish later and the owner's editor loses the machine.
The fix: the suite phase of the gate holds the machine-wide `gate` lock `serial.sh` already owns, so a second full gate
prints one wait line, starts when the first ends, and the lock directory never carries two pids.

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
    """Both gates of a race run to green, each one's work sits inside its own phase, and the second says it waits once."""
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
    # The whole of one gate runs before any of the other: a full gate that started inside another one would interleave here.
    first, second = ("A", "B") if lines[0].startswith("A ") else ("B", "A")
    assert max(i for i, l in enumerate(lines) if l.startswith(f"{first} ")) < \
        min(i for i, l in enumerate(lines) if l.startswith(f"{second} ")), f"{first} ran beside {second}: {lines}"
    waiting = WAIT_LINE.findall("".join(outs))
    assert len(waiting) == 1, f"the second full gate prints one wait line; got {len(waiting)}"


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
