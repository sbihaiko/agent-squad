"""Child processes behind an adapter: stop the whole process group when the call is cancelled.

A deadline cancels the calling task. The child and every process it started must be gone when the
teardown ends, so no orphan keeps spending. The teardown time is an engine constant, measured at about
5 s for the SDK's CLI child (docs/research/sdk-live-smoke.md).
"""
from __future__ import annotations

import ctypes
import dataclasses
import functools
import json
import os
import re
import signal
import struct
import subprocess
import sys
import time

# anyio and squad.adapter load inside the async functions below: the process read runs under any python3 (the hub's, the
# janitor's, sessions.py's), and those need no async stack.

TEARDOWN_S = 5.0


class ReadError(Exception):
    """A read that could not be made. The answer is unknown: the caller holds, and never reads it as absence."""


def _signal_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass   # the group is ours: macOS answers EPERM, not ESRCH, once every member is a zombie, so the group is gone


async def stop_group(proc, teardown=TEARDOWN_S):
    """Terminate the process group, wait up to `teardown` seconds, then kill it. Shielded from cancellation."""
    import anyio
    with anyio.CancelScope(shield=True):
        _signal_group(proc, signal.SIGTERM)
        with anyio.move_on_after(teardown):
            await proc.wait()
        _signal_group(proc, signal.SIGKILL)
        await proc.wait()


async def run_child(argv, *, cwd=None, teardown=TEARDOWN_S, on_start=None):
    """Run a child in its own process group and return (exit code, stdout). Cancellation stops the group."""
    import anyio
    proc = await anyio.open_process(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    if on_start:
        on_start(proc)
    try:
        chunks = []
        async with proc.stdout:
            async for c in proc.stdout:
                chunks.append(c)
        await proc.wait()
        return proc.returncode, b"".join(chunks).decode()
    finally:
        if proc.returncode is None:
            await stop_group(proc, teardown)
        else:
            _signal_group(proc, signal.SIGKILL)   # a child that exited may leave group members behind


class CommandAdapter:
    """An adapter that runs one command per call and reads `{"output", "spend", "turns"}` JSON from its stdout."""

    def __init__(self, make_argv, teardown=TEARDOWN_S, on_start=None):
        self.make_argv, self.teardown, self.on_start = make_argv, teardown, on_start

    async def call(self, req):
        from squad.adapter import AdapterError, CallResult
        code, out = await run_child(self.make_argv(req), cwd=req.workspace, teardown=self.teardown, on_start=self.on_start)
        if code != 0:
            raise AdapterError(f"child exited {code}")
        try:
            d = json.loads(out)
        except json.JSONDecodeError as e:
            raise AdapterError(f"child printed no JSON: {e}") from e
        return CallResult(d.get("output"), d.get("spend"), d.get("turns"), d.get("stop_reason"), d.get("subagents", 0), req.session_id)


# ---------------------------------------------------------------- the process read: no exec (#1508)
# A child session runs under sandbox-exec, where macOS refuses to exec the setuid process table binary. libproc and sysctl
# answer inside the signal profile, so the read is made in-process. Linux reads /proc.

PROC_PIDTBSDINFO = 3
PROC_PIDTASKINFO = 4   # struct proc_taskinfo: total user and system time at byte 16 and 24, in Mach time units
PROC_PIDPATHINFO_MAXSIZE = 4096
CTL_KERN, KERN_PROC, KERN_PROC_PID, KERN_PROCARGS2 = 1, 14, 1, 49
KINFO_PROC_SIZE = 648
LSTART_FMT = "%a %b %e %H:%M:%S %Y"
LSTART_WIDTH = 28   # the start column is padded to this width, as the system's ps pads it
PS_FIELDS = ("pid", "ppid", "pgid", "lstart", "command", "stat", "time")
SZOMB = 5   # pbi_status and p_stat of a zombie


@dataclasses.dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    pgid: int
    start: float | None   # epoch seconds, or None where the system gives none
    command: str
    cpu: float | None = 0.0   # user plus system CPU seconds, as ps `time=` reports them; None where the read was refused
    stat: str = "S"       # the first letter ps `stat=` prints: Z for a zombie, S for any other state
    cpu_known: bool = True   # False where the CPU read was refused: the process was seen, and its seconds are unknown, never zero


class _BsdInfo(ctypes.Structure):
    """struct proc_bsdinfo from sys/proc_info.h; 136 bytes on macOS (checked when the library loads)."""
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32), ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32), ("pbi_ppid", ctypes.c_uint32), ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32), ("pbi_ruid", ctypes.c_uint32), ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32), ("pbi_svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32), ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32), ("pbi_pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64), ("pbi_start_tvusec", ctypes.c_uint64),
    ]


@functools.lru_cache(maxsize=1)
def _libproc():
    try:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError as e:
        raise ReadError(f"cannot load libproc: {e}") from e
    lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.proc_listallpids.restype = ctypes.c_int
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.restype = ctypes.c_int
    lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    lib.proc_pidpath.restype = ctypes.c_int
    if ctypes.sizeof(_BsdInfo) != 136:
        raise ReadError("proc_bsdinfo layout does not match the system header")
    return lib


@functools.lru_cache(maxsize=1)
def _libc():
    try:
        return ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    except OSError as e:
        raise ReadError(f"cannot load libSystem: {e}") from e


@functools.lru_cache(maxsize=1)
def _mach_ratio():
    """(numer, denom): Mach time units to nanoseconds. Apple silicon's tick is not a nanosecond."""
    tb = (ctypes.c_uint32 * 2)()
    _libc().mach_timebase_info(tb)
    return tb[0] or 1, tb[1] or 1


def _darwin_cpu(pid: int) -> float | None:
    """User plus system CPU seconds from proc_pidinfo PROC_PIDTASKINFO; None when the system refuses the read, so a
    refused process is never read as one that used no CPU."""
    buf = ctypes.create_string_buffer(96)
    n = _libproc().proc_pidinfo(pid, PROC_PIDTASKINFO, 0, buf, 96)
    if n < 32:
        return None
    user, system = struct.unpack_from("QQ", buf.raw, 16)
    numer, denom = _mach_ratio()
    return (user + system) * numer / denom / 1e9


_NON_PRINTABLE = re.compile(r"[^\x20-\x7e]")   # the bytes a command's text escapes


def _vis(raw: bytes) -> str:
    """The listing prints a command with each byte outside printable ASCII as an octal escape (a newline shows as \\012).
    Latin-1 maps each byte to the code point of its own value, so one decode and one substitution answer what a per-byte
    loop answered (#2582)."""
    return _NON_PRINTABLE.sub(lambda m: "\\{:03o}".format(ord(m.group())), raw.decode("latin-1"))


def _parse_procargs2(raw: bytes) -> str | None:
    """KERN_PROCARGS2: argc (int), the exec path, NUL padding, then argc NUL-terminated arguments, then the environment."""
    if len(raw) < 4:
        return None
    (argc,) = struct.unpack_from("i", raw, 0)
    i = raw.find(b"\0", 4)
    if i < 0:
        return None
    i += 1
    while i < len(raw) and raw[i] == 0:
        i += 1
    args = []
    for _ in range(argc):
        j = raw.find(b"\0", i)
        if j < 0:
            j = len(raw)
        args.append(_vis(raw[i:j]))
        i = j + 1
    return " ".join(args)


def _darwin_argv(pid: int) -> str | None:
    libc = _libc()
    mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or size.value == 0:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    return _parse_procargs2(buf.raw[:size.value])


def _darwin_path(pid: int) -> str | None:
    buf = ctypes.create_string_buffer(PROC_PIDPATHINFO_MAXSIZE)
    n = _libproc().proc_pidpath(pid, buf, PROC_PIDPATHINFO_MAXSIZE)
    return _vis(buf.value) if n > 0 else None


ARGV_SETTLE_S = 0.02   # how long a read waits for the argv of a process this user owns that is still exec'ing
ARGV_SETTLE_STEP_S = 0.002


def _darwin_command(pid: int, ours: bool) -> str:
    """The command line, as ps prints it: the exec path and its arguments. A process of this user caught mid-exec answers no
    argv for a moment, and the read waits for it, bounded by ARGV_SETTLE_S: taken as the truth, the exec path alone read as
    a command with no argument, so a fresh `squad start` was no squad run and the hub's stop-now missed it (#1635). Only this
    user's process is waited for: another user's argv is refused for good, and a wait on it would stall every snapshot."""
    command = _darwin_argv(pid)
    if command is None and ours:
        deadline = time.monotonic() + ARGV_SETTLE_S
        while command is None and time.monotonic() < deadline:
            time.sleep(ARGV_SETTLE_STEP_S)
            command = _darwin_argv(pid)
    return command if command is not None else (_darwin_path(pid) or "")


def _darwin_listing() -> list[int]:
    lib = _libproc()
    n = lib.proc_listallpids(None, 0)
    if n <= 0:
        raise ReadError(f"cannot list the processes: proc_listallpids returned {n}")
    size = (n + 64) * 4   # slack for processes that start between the two calls
    buf = (ctypes.c_int32 * (size // 4))()
    got = lib.proc_listallpids(buf, size)
    if got <= 0:
        raise ReadError(f"cannot list the processes: proc_listallpids returned {got}")
    return [int(p) for p in buf[:got] if p > 0]


def _darwin_kinfo(pid: int) -> tuple[float | None, int, int, str] | None:
    """(start, ppid, pgid) from kern.proc.pid. The system answers it for another user's process, which proc_pidinfo refuses.
    Offsets in struct kinfo_proc: p_starttime at 0, e_ppid at 560, e_pgid at 564; the size check guards the layout."""
    mib = (ctypes.c_int * 4)(CTL_KERN, KERN_PROC, KERN_PROC_PID, pid)
    size = ctypes.c_size_t(KINFO_PROC_SIZE)
    buf = ctypes.create_string_buffer(KINFO_PROC_SIZE)
    if _libc().sysctl(mib, 4, buf, ctypes.byref(size), None, 0) != 0 or size.value != KINFO_PROC_SIZE:
        return None   # gone, or the layout is not this one
    sec, usec = struct.unpack_from("qq", buf.raw, 0)
    ppid, pgid = struct.unpack_from("ii", buf.raw, 560)
    stat = "Z" if buf.raw[36] == SZOMB else "S"   # p_stat at byte 36
    return (sec + usec / 1e6 if sec else None), ppid, pgid, stat


def _darwin_proc(pid: int) -> Proc | None:
    info = _BsdInfo()
    ours = False
    if _libproc().proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info)) == ctypes.sizeof(info):
        start = info.pbi_start_tvsec + info.pbi_start_tvusec / 1e6 if info.pbi_start_tvsec else None
        ppid, pgid = info.pbi_ppid, info.pbi_pgid
        cpu = _darwin_cpu(pid)   # None when the task read is refused for another user's process
        stat = "Z" if info.pbi_status == SZOMB else "S"
        ours = info.pbi_uid == os.getuid()
    else:
        known = _darwin_kinfo(pid)   # refused for another user's process: the sysctl still names it
        if known is None:
            return None   # gone
        start, ppid, pgid, stat = known
        cpu = None   # the task read is refused for another user's process
    return Proc(pid, ppid, pgid, start, _darwin_command(pid, ours), cpu, stat, cpu is not None)   # a refused read stays None, never a zero cost


def _linux_listing() -> list[int]:
    try:
        names = os.listdir("/proc")
    except OSError as e:
        raise ReadError(f"cannot list the processes: {e}") from e
    return [int(n) for n in names if n.isdigit()]


def _linux_boot_time() -> int:
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime "):
                    return int(line.split()[1])
    except (OSError, ValueError) as e:
        raise ReadError(f"cannot read the boot time: {e}") from e
    raise ReadError("cannot read the boot time: /proc/stat has no btime")


def _linux_proc(pid: int, boot: int) -> Proc | None:
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read()
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read()
        head, _, rest = stat.rpartition(")")
        fields = rest.split()
        # after the ")": state is field 3, so ppid, pgid and starttime (field 22) sit at indexes 1, 2 and 19
        ppid, pgid, starttime = int(fields[1]), int(fields[2]), int(fields[19])
        cpu = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")   # utime, stime: fields 14 and 15
        comm = head.partition("(")[2]
        stat = fields[0][:1]
    except (OSError, ValueError, IndexError):
        return None   # the pid vanished mid-read
    start = boot + starttime / os.sysconf("SC_CLK_TCK")
    command = _vis(cmdline.rstrip(b"\0").replace(b"\0", b" ")) or f"[{comm}]"
    return Proc(pid, ppid, pgid, start, command, cpu, stat)


def snapshot(pids=None) -> dict[int, Proc]:
    """The processes now, keyed by pid. `pids` limits the read; a pid that is gone is left out. A failed listing raises
    ReadError, so an empty answer is never a read of absence."""
    if sys.platform == "darwin":
        read, listing = _darwin_proc, _darwin_listing
    elif sys.platform.startswith("linux"):
        boot = _linux_boot_time()
        read, listing = (lambda pid: _linux_proc(pid, boot)), _linux_listing
    else:
        raise ReadError(f"cannot read the processes on {sys.platform}")
    wanted = listing() if pids is None else list(pids)
    out: dict[int, Proc] = {}
    for pid in wanted:
        p = read(pid)
        if p is not None:
            out[pid] = p
    return out


# ---------------------------------------------------------------- the ps text: `python -m squad.procs ps`
def _ps_args(argv: list[str]) -> tuple[list[str], list[int] | None]:
    """The flags this seam serves: -a, -x, -w (accepted, no effect), -o FIELDS and -p PIDS, both comma-joined and repeatable."""
    fields: list[str] = []
    pids: list[int] | None = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if not a.startswith("-") or len(a) < 2:
            raise ValueError(f"unexpected argument {a}")
        letters, j = a[1:], 0
        while j < len(letters):
            c = letters[j]
            if c in "axw":
                j += 1
                continue
            if c not in "op":
                raise ValueError(f"unknown flag -{c}")
            if j + 1 < len(letters):
                value = letters[j + 1:]
            else:
                i += 1
                if i >= len(argv):
                    raise ValueError(f"-{c} needs a value")
                value = argv[i]
            j = len(letters)
            items = [x.strip() for x in value.split(",") if x.strip()]
            if c == "o":
                fields += items
            else:
                pids = (pids or []) + [int(x) for x in items]
        i += 1
    return fields, pids


def format_lstart(start: float | None) -> str:
    return "" if start is None else time.strftime(LSTART_FMT, time.localtime(start))


def format_cputime(cpu: float | None) -> str:
    """ps `time=`: [dd-][hh:]mm:ss.cc. A refused CPU read prints `-`: no zero stands for a time the system did not give."""
    if cpu is None:
        return "-"
    cs = int(cpu * 100)
    s, cc = divmod(cs, 100)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return (f"{d}-{h:02d}:" if d else f"{h}:" if h else "") + (f"{m:02d}" if (d or h) else str(m)) + f":{s:02d}.{cc:02d}"


def _ps_line(p: Proc, fields: list[str]) -> str:
    cells = []
    for f in fields:
        if f == "pid":
            cells.append(str(p.pid).rjust(5))
        elif f == "ppid":
            cells.append(str(p.ppid).rjust(5))
        elif f == "pgid":
            cells.append(str(p.pgid).rjust(5))
        elif f == "stat":
            cells.append(p.stat)
        elif f == "time":
            cells.append(format_cputime(p.cpu).rjust(8))
        elif f == "lstart":
            cells.append(format_lstart(p.start).ljust(LSTART_WIDTH))
        else:
            cells.append(p.command)
    return " ".join(cells)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != "ps":
        print("usage: python -m squad.procs ps [-axw] -o pid=,ppid=,pgid=,lstart=,command= [-p PIDS]", file=sys.stderr)
        return 2
    try:
        fields, pids = _ps_args(argv[1:])
        if not fields:
            raise ValueError("no -o fields")
        names = []
        for f in fields:
            if not f.endswith("=") or f[:-1] not in PS_FIELDS:
                raise ValueError(f"unknown field {f}")
            names.append(f[:-1])
    except ValueError as e:
        print(f"ps: {e}", file=sys.stderr)
        return 2
    try:
        table = snapshot(pids)
    except ReadError as e:
        print(f"ps: {e}", file=sys.stderr)
        return 1
    rows = [table[pid] for pid in sorted(table)]
    if pids is not None and not rows:
        return 1   # ps exits 1 when no -p pid is live
    try:
        for p in rows:
            print(_ps_line(p, names))
        sys.stdout.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())   # a reader that quit early, as `head` does
    return 0


if __name__ == "__main__":
    sys.exit(main())
