"""The one process-read seam (squad/procs.py, #1508).

A child session runs under sandbox-exec, where macOS refuses to exec the setuid /bin/ps. The read must not exec anything:
libproc and sysctl answer inside the signal profile. The ratchet below keeps /bin/ps out of the code until its callers move.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from squad import procs

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = "/usr/bin/sandbox-exec"
PS_ENV = {**os.environ, "LC_ALL": "C"}


@pytest.fixture
def child():
    p = subprocess.Popen(["/bin/sleep", "30"])
    try:
        yield p
    finally:
        p.kill()
        p.wait()


def test_1508_own_process_is_read_without_exec(child, monkeypatch):
    def no_exec(*args, **kwargs):
        raise OSError(1, "no exec in this read", str(args[0]) if args else "")

    monkeypatch.setattr(subprocess, "Popen", no_exec)
    monkeypatch.setattr(subprocess, "run", no_exec)
    snap = procs.snapshot()
    me = snap[os.getpid()]
    assert me.ppid == os.getppid()
    assert me.start is not None and me.start <= time.time()   # pytest started before this test, not within five seconds
    kid = snap[child.pid]
    assert kid.ppid == os.getpid()
    assert kid.start is not None and abs(time.time() - kid.start) < 5
    assert kid.command == "/bin/sleep 30"


@pytest.mark.sandbox_bound
def test_1508_a_process_read_works_under_the_signal_profile(child):
    from squad.sandbox import SIGNAL_PROFILE

    assert os.path.exists(SANDBOX)
    cmd = [SANDBOX, "-p", SIGNAL_PROFILE, sys.executable, "-m", "squad.procs", "ps",
           "-o", "pid=,lstart=,command=", "-p", str(child.pid)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=60, env=PS_ENV)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[0] == str(child.pid)
    stamp = re.search(r"\w{3} \w{3}\s+\d{1,2} \d\d:\d\d:\d\d \d{4}", r.stdout)
    assert stamp is not None, r.stdout
    time.strptime(stamp.group(), "%a %b %d %H:%M:%S %Y")
    assert r.stdout.rstrip().endswith("/bin/sleep 30")


@pytest.mark.process_bound
def test_1508_snapshot_matches_ps(child):
    r = subprocess.run(["/bin/ps", "-axwwo", "pid=,ppid=,command="], capture_output=True, text=True,
                       timeout=30, env=PS_ENV)
    assert r.returncode == 0, r.stderr
    table = {}
    for line in r.stdout.splitlines():
        f = line.split(None, 2)
        table[int(f[0])] = (int(f[1]), f[2] if len(f) > 2 else "")
    pids = [os.getpid(), child.pid]
    snap = procs.snapshot(pids)
    for pid in pids:
        assert (snap[pid].pid, snap[pid].ppid, snap[pid].command) == (pid, table[pid][0], table[pid][1])


def test_1508_no_file_names_bin_ps():
    hits = []
    for base in (ROOT / "squad", ROOT / "contrib" / "squad-goal" / "scripts"):
        for path in base.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.name.startswith("test-") and path.suffix == ".sh":
                continue
            if "/bin/ps" in path.read_text(errors="ignore"):
                hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_1508_shell_reader_survives_a_space_in_the_checkout_and_the_interpreter(tmp_path):
    """A shell caller expands `$SG_PS` unquoted. A checkout or an interpreter whose path holds a space must still read the table."""
    repo = tmp_path / "My Project"
    scripts = repo / "contrib" / "squad-goal" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "caps.sh").write_text((ROOT / "contrib/squad-goal/scripts/caps.sh").read_text())
    (repo / "squad").mkdir()
    for name in ("__init__.py", "procs.py"):
        (repo / "squad" / name).write_text((ROOT / "squad" / name).read_text())
    pydir = tmp_path / "py dir"
    pydir.mkdir()
    (pydir / "python").symlink_to(sys.executable)
    env = {**PS_ENV, "SG_GATE_PY": str(pydir / "python")}
    env.pop("SG_PS", None)
    out = subprocess.run(["bash", "-c", '. "$1"; $SG_PS -o pid= -p $$', "_", str(scripts / "caps.sh")],
                         capture_output=True, text=True, env=env, cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().isdigit(), out.stdout


def test_1508_every_shell_reader_sources_the_seam_at_top_level():
    """`SG_PS` is a shell function, never exported, so a script that expands it must source caps.sh itself, at top level:
    a source inside one branch leaves another branch, or a fresh `bash script --run` child, with no reader at all."""
    scripts = ROOT / "contrib/squad-goal/scripts"
    missing = []
    for f in sorted(scripts.glob("*.sh")):
        if f.name.startswith("test-") or f.name == "caps.sh":
            continue
        text = f.read_text()
        if "$SG_PS" not in text and "${SG_PS" not in text:
            continue
        if not re.search(r'^(?![\s#])[^\n]*?\.\s+"[^\n]*/caps\.sh"', text, re.M):
            missing.append(f.name)
    assert not missing, f"expand $SG_PS without a top-level source of caps.sh: {missing}"
