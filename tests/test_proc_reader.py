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
@pytest.mark.heavy     # a wall-clock threshold, measured on the runner's own load (#1511)
def test_a_one_pid_read_takes_under_one_hundred_milliseconds(tmp_path):
    """Acceptance of #1511: one `$SG_PS -o pid= -p $$` read takes under 100 ms. The build is paid once, as a poll pays it,
    and the timed loop then runs in the one shell that already holds the reader, so the wall clock measures the read."""
    env = {**os.environ, "TMPDIR": str(tmp_path), "SG_PS": "", "PYTHONPATH": str(ROOT)}
    env.pop("SG_GATE_PY", None)
    script = (
        f'. "{CAPS}"; '
        'sg_ps -o pid= -p $$ >/dev/null; '                        # the build and a warm read, once
        'start=$(python3 -c "import time; print(time.time())"); '
        'for i in 1 2 3 4 5 6 7 8 9 10; do sg_ps -o pid= -p $$ >/dev/null; done; '
        'end=$(python3 -c "import time; print(time.time())"); '
        'python3 -c "print(($end - $start) / 10)"'
    )
    out = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    per_read = float(out.stdout.strip())
    assert per_read < 0.1, f"one read took {per_read * 1000:.0f} ms"


@pytest.mark.cc_bound
@pytest.mark.heavy     # builds the reader and waits out a child to hold it as a zombie (#1511)
def test_the_compiled_reader_and_squad_procs_answer_the_same_fields(tmp_path):
    """The compiled reader serves four fields of the two kernel structs the module reads (proc_bsdinfo, then kinfo_proc),
    and the ps text is what callers parse, so the two must print the same bytes -- padding included -- for the same pid:
    a live pid, a leader of its own group, a zombie and pid 1."""
    from squad import procs

    child = subprocess.Popen(["sleep", "30"], start_new_session=True)
    zombie = os.fork()
    if zombie == 0:
        os._exit(0)                                              # the forked child exits and is never reaped: a zombie
    try:
        time.sleep(0.3)
        pids = [os.getpid(), child.pid, zombie, 1]
        table = procs.snapshot(pids)
        for pid in pids:
            if pid not in table:
                pytest.skip(f"pid {pid} vanished before the read")
            out = read(tmp_path, "-o", "pid=,ppid=,pgid=,stat=", "-p", pid)
            module = subprocess.run(
                [sys.executable, "-m", "squad.procs", "ps", "-o", "pid=,ppid=,pgid=,stat=", "-p", str(pid)],
                env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True)
            assert (out.returncode, out.stdout) == (module.returncode, module.stdout), \
                f"pid {pid}: the compiled reader printed {out.stdout!r}, squad.procs answers {module.stdout!r}"
        assert table[zombie].stat == "Z"                         # the case is a zombie, or it guards nothing
    finally:
        child.kill()
        child.wait()
        try:
            os.waitpid(zombie, 0)
        except ChildProcessError:
            pass


@pytest.mark.cc_bound
def test_a_dead_pid_reads_as_gone_with_the_status_ps_uses(tmp_path):
    """A pid that is gone exits 1, as `ps -p` does, and a live one exits 0: the pollers read the status, never the text."""
    out = read(tmp_path, "-o", "stat=", "-p", 999999)
    assert out.returncode == 1 and out.stdout == "", (out.returncode, out.stdout)
    out = read(tmp_path, "-o", "stat=", "-p", os.getpid())
    assert out.returncode == 0 and out.stdout.strip() in ("S", "R"), out.stdout


@pytest.mark.cc_bound
def test_a_multi_pid_shape_is_left_to_the_module(tmp_path):
    """The pollers ask about one pid. A `-p` list can hold a live pid beside a dead one, and the module answers that shape
    with the rows it found and exit 0; the compiled reader derives no such answer, so it refuses the list and the module,
    the one source of truth, answers (#1511)."""
    env = {**os.environ, "TMPDIR": str(tmp_path), "SG_PS_FAST": "1"}
    built = subprocess.run(["bash", "-c", f'. "{CAPS}"; _sg_ps_build'], env=env, capture_output=True, text=True)
    fast = built.stdout.strip()
    assert fast, built.stderr
    live, dead = os.getpid(), 999999
    direct = subprocess.run([fast, "-o", "pid=", "-p", f"{live},{dead}"], capture_output=True, text=True)
    assert direct.returncode == 3 and direct.stdout == "", (direct.returncode, direct.stdout)
    module = subprocess.run([sys.executable, "-m", "squad.procs", "ps", "-o", "pid=", "-p", f"{live},{dead}"],
                            env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True)
    assert module.returncode == 0, module.stderr
    out = read(tmp_path, "-o", "pid=", "-p", f"{live},{dead}")
    assert (out.returncode, out.stdout) == (module.returncode, module.stdout), (out.returncode, out.stdout)


@pytest.mark.cc_bound
@pytest.mark.process_bound   # its fallback reads the process table, which a nested sandbox denies (#1617, ADR-0139)
def test_a_shape_the_compiled_reader_does_not_serve_falls_back_to_the_module(tmp_path):
    """The command line and the whole table stay the module's: a read the compiled reader refuses is still answered, in
    the ps text the callers parse."""
    out = read(tmp_path, "-o", "command=", "-p", os.getpid())
    assert out.returncode == 0, out.stderr
    assert sys.executable.split("/")[-1] in out.stdout or "python" in out.stdout.lower(), out.stdout
