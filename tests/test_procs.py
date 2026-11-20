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
SCRIPTS = ROOT / "contrib/squad-goal/scripts"


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
    missing = []
    for f in sorted(SCRIPTS.glob("*.sh")):
        if f.name.startswith("test-") or f.name == "caps.sh":
            continue
        text = f.read_text()
        if "$SG_PS" not in text and "${SG_PS" not in text:
            continue
        if not re.search(r'^(?![\s#])[^\n]*?\.\s+"[^\n]*/caps\.sh"', text, re.M):
            missing.append(f.name)
    assert not missing, f"expand $SG_PS without a top-level source of caps.sh: {missing}"


def test_1508_a_script_reads_its_own_tree_before_a_foreign_sg_squad(tmp_path, monkeypatch):
    """SG_SQUAD may name another checkout, behind this change, whose procs.py has no snapshot. The script's own tree answers
    first, as caps.sh reads it, so the foreign module is never loaded."""
    import importlib.util
    foreign = tmp_path / "old" / "squad"
    foreign.mkdir(parents=True)
    (foreign / "procs.py").write_text("STALE = True\n")
    monkeypatch.setenv("SG_SQUAD", str(foreign.parent))
    monkeypatch.setenv("SG_CHECKOUT", str(foreign.parent))
    monkeypatch.delitem(sys.modules, "squad_procs", raising=False)
    monkeypatch.syspath_prepend(str(ROOT / "contrib/squad-goal/scripts"))
    spec = importlib.util.spec_from_file_location("sessions_under_test", ROOT / "contrib/squad-goal/scripts/sessions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        assert hasattr(mod._procs(), "snapshot")
    finally:
        sys.modules.pop("squad_procs", None)


# (#1635) A process caught mid-exec answers no argv for a moment, and `sysctl` says nothing instead. A read that took that
# moment as the truth reported the executable path alone as the command, so a fresh `squad start` read as no squad run and
# a stop-now missed it. The read waits for the argv a moment, only for a process this user owns, and only for a bound.
@pytest.mark.skipif(sys.platform != "darwin", reason="the exec-transition argv read is the darwin seam")
def test_1635_a_process_caught_mid_exec_still_reads_its_arguments(child, monkeypatch):
    real = procs._darwin_argv
    calls = []

    def first_read_misses(pid):
        calls.append(pid)
        return None if len(calls) == 1 else real(pid)

    monkeypatch.setattr(procs, "_darwin_argv", first_read_misses)
    snap = procs.snapshot([child.pid])
    assert snap[child.pid].command == "/bin/sleep 30"


def test_1635_another_users_process_is_not_waited_for(monkeypatch):
    """Another user's argv is refused for good, so its read answers from the path at once: a snapshot never waits on every
    process the system holds."""
    calls = []
    monkeypatch.setattr(procs, "_darwin_argv", lambda pid: calls.append(pid))
    monkeypatch.setattr(procs, "_darwin_path", lambda pid: "/usr/bin/login")
    assert procs._darwin_command(4321, ours=False) == "/usr/bin/login"
    assert calls == [4321]


def test_1635_the_wait_for_an_argv_is_bounded(monkeypatch):
    monkeypatch.setattr(procs, "_darwin_argv", lambda pid: None)
    monkeypatch.setattr(procs, "_darwin_path", lambda pid: "/usr/bin/python3")
    t = time.monotonic()
    assert procs._darwin_command(4321, ours=True) == "/usr/bin/python3"
    assert time.monotonic() - t < 1


# (#1753) The profiler's coverage counts a process seen and not read. Another user's process answers no task info, so its
# CPU seconds are unknown: the read keeps None, never a zero the fold would draw as a zero-cost row.
@pytest.mark.skipif(sys.platform != "darwin", reason="the task-info read is the darwin seam")
def test_1753_a_refused_task_info_read_keeps_none_as_the_cpu(monkeypatch):
    import ctypes

    def proc_pidinfo(pid, flavor, arg, buf, size):
        info = ctypes.cast(buf, ctypes.POINTER(procs._BsdInfo)).contents
        info.pbi_start_tvsec, info.pbi_start_tvusec = 1700000000, 0
        info.pbi_ppid, info.pbi_pgid, info.pbi_uid, info.pbi_status = 1, pid, 4321, 2
        return size

    monkeypatch.setattr(procs, "_libproc", lambda: type("L", (), {"proc_pidinfo": staticmethod(proc_pidinfo)})())
    monkeypatch.setattr(procs, "_darwin_cpu", lambda pid: None)   # the task read is refused
    monkeypatch.setattr(procs, "_darwin_command", lambda pid, ours: "/usr/bin/login")
    p = procs._darwin_proc(4321)
    assert p.cpu is None and p.cpu_known is False
    assert (p.pid, p.ppid, p.pgid, p.stat) == (4321, 1, 4321, "S")


def test_2591_a_just_started_linux_process_does_not_read_a_second_old(monkeypatch):
    """#2591: /proc/stat `btime` is a whole second, so a start built on it reads up to 1 s early and the session's reap
    takes a fresh leftover for an old process. /proc/uptime carries hundredths, so the boot time comes from there."""
    now, uptime = 1_700_000_000.0, 1234.01   # the wall clock and the uptime at one instant
    boot = now - uptime
    hz = os.sysconf("SC_CLK_TCK")
    starttime = int((uptime - 0.07) * hz)   # this process started 0.07 s ago
    fields = ["0"] * 20
    fields[0], fields[1], fields[2], fields[19] = "S", "1", "4242", str(starttime)
    files = {
        "/proc/uptime": f"{uptime:.2f} 99999.99\n",
        "/proc/stat": f"cpu  0 0 0 0\nbtime {int(boot)}\n",   # whole seconds: up to 1 s behind the true boot
        "/proc/4242/stat": f"4242 (sleep) {' '.join(fields)}\n",
        "/proc/4242/cmdline": b"sleep\x0030\x00",
    }

    class _File:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def __iter__(self):
            return iter(self.data.splitlines(True))

        def read(self):
            return self.data

    monkeypatch.setattr(procs, "open", lambda path, *a, **k: _File(files[path]), raising=False)
    monkeypatch.setattr(procs.time, "time", lambda: now)
    p = procs._linux_proc(4242, procs._linux_boot_time())
    assert p is not None
    age = now - p.start
    assert age < 0.1, f"a process started 0.07 s ago read as {age:.3f} s old"


def test_2582_vis_prints_every_byte_as_the_listing_does():
    """AC E of #2582: the reader's own escaping answers every byte exactly as before, so the faster fold changes no text."""
    def reference(raw: bytes) -> str:
        return "".join(chr(c) if 0x20 <= c < 0x7f else f"\\{c:03o}" for c in raw)
    assert procs._vis(bytes(range(256))) == reference(bytes(range(256)))
    for c in range(256):
        assert procs._vis(bytes([c])) == reference(bytes([c])), c
    assert procs._vis(b"") == ""
    assert procs._vis(b"squad start \xc3\xa9\n\t") == r"squad start \303\251\012\011"
