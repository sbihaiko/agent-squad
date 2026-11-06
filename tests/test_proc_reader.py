"""The shell process read (#1511): a poll of one pid costs milliseconds, never an interpreter start.

`sg_ps` (contrib/squad-goal/scripts/caps.sh) reads the process table through `squad.procs`, which is a Python module, so
every read paid for a new interpreter: about 0.7 s on a loaded loop box against 10 ms for the reader it replaced. The
callers that poll ask one question about one pid — is it alive, is it a zombie, which group does it lead — and a small
compiled reader answers that shape. The module stays the one reader of everything else, and the two agree on the fields
they share.

Guard: this file (a read with no interpreter, the read's cost, and the two readers held to one answer), plus the `heavy`
mark on the two suites the issue names.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CAPS = ROOT / "contrib" / "squad-goal" / "scripts" / "caps.sh"


def read(tmp_path, *args, python="", pid=None):
    """One `$SG_PS` read in a fresh shell, with its own build folder and, when `python` is given, that interpreter as the
    battery's. A fresh shell per call is how a poll reads the table: the fast reader is found and built there."""
    env = {**os.environ, "TMPDIR": str(tmp_path), "SG_PS": "", "PYTHONPATH": str(ROOT)}
    env.pop("SG_GATE_PY", None)
    if python:
        env["SG_GATE_PY"] = python
    words = " ".join(str(a) for a in args)
    return subprocess.run(["bash", "-c", f'. "{CAPS}"; $SG_PS {words}'], env=env, capture_output=True, text=True)


@pytest.mark.cc_bound
def test_a_one_pid_read_answers_with_no_interpreter(tmp_path):
    """The poll shape is served by the compiled reader: the read answers even when the interpreter the shell would start is
    gone, so a poll pays no interpreter start (#1511)."""
    out = read(tmp_path, "-o", "pid=", "-p", os.getpid(), python=str(tmp_path / "no-such-python"))
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [str(os.getpid())], out.stdout


@pytest.mark.cc_bound
def test_a_one_pid_read_takes_under_one_hundred_milliseconds(tmp_path):
    """Acceptance of #1511: one `$SG_PS -o pid= -p $$` read takes under 100 ms. The first read in a shell pays the build,
    so this times a warm read, as a poll pays it."""
    read(tmp_path, "-o", "pid=", "-p", os.getpid())             # the build, once
    t0 = time.monotonic()
    for _ in range(20):
        out = read(tmp_path, "-o", "pid=", "-p", os.getpid())
        assert out.returncode == 0, out.stderr
    per_read = (time.monotonic() - t0) / 20
    assert per_read < 0.1, f"one read took {per_read * 1000:.0f} ms"


@pytest.mark.cc_bound
def test_the_compiled_reader_and_squad_procs_answer_the_same_fields(tmp_path):
    """The compiled reader serves four fields of the two kernel structs the module reads (proc_bsdinfo, then kinfo_proc),
    so the two must answer the same pid, ppid, pgid and stat: a live pid, a leader of its own group, a zombie and pid 1."""
    from squad import procs

    child = subprocess.Popen(["sleep", "30"], start_new_session=True)
    zombie = subprocess.Popen(["true"])
    try:
        time.sleep(0.3)                                          # the child exits and is left unreaped: a zombie
        pids = [os.getpid(), child.pid, zombie.pid, 1]
        table = procs.snapshot(pids)
        for pid in pids:
            out = read(tmp_path, "-o", "pid=,ppid=,pgid=,stat=", "-p", pid)
            assert out.returncode == 0, out.stderr
            printed = out.stdout.split()
            assert [int(printed[0]), int(printed[1]), int(printed[2]), printed[3]] == \
                [table[pid].pid, table[pid].ppid, table[pid].pgid, table[pid].stat], \
                f"pid {pid}: the compiled reader printed {out.stdout!r}, squad.procs answers {table[pid]}"
        assert table[zombie.pid].stat == "Z"                     # the case is a zombie, or it guards nothing
    finally:
        child.kill()
        child.wait()
        zombie.wait()


@pytest.mark.cc_bound
def test_a_dead_pid_reads_as_gone_with_the_status_ps_uses(tmp_path):
    """A pid that is gone exits 1, as `ps -p` does, and a live one exits 0: the pollers read the status, never the text."""
    out = read(tmp_path, "-o", "stat=", "-p", 999999)
    assert out.returncode == 1 and out.stdout == "", (out.returncode, out.stdout)
    out = read(tmp_path, "-o", "stat=", "-p", os.getpid())
    assert out.returncode == 0 and out.stdout.strip() in ("S", "R"), out.stdout


@pytest.mark.cc_bound
def test_a_shape_the_compiled_reader_does_not_serve_falls_back_to_the_module(tmp_path):
    """The command line and the whole table stay the module's: a read the compiled reader refuses is still answered, in
    the ps text the callers parse."""
    out = read(tmp_path, "-o", "command=", "-p", os.getpid())
    assert out.returncode == 0, out.stderr
    assert sys.executable.split("/")[-1] in out.stdout or "python" in out.stdout.lower(), out.stdout
