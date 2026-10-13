"""#1415: a real-app case starts with no emulator alive, and a launch that dies before its hook is named as such.

Only a process carrying the hook flag (`--test-hook=`) is ours to reap: the Owner's own Mesen never has it.
"""
import glob
import os
import signal
import socket
import subprocess
import time

HOOK_FLAG = "--test-hook="
SOCKET_GLOB = "/tmp/mg-*/hook.sock"


SHELLS = {"sh", "bash", "zsh", "dash", "fish", "ps", "grep", "pgrep"}


def _table():
    # `-ww`: procps cuts the command column at $COLUMNS, and readline (loaded under pytest) exports COLUMNS=80 to every child.
    # The hook flag is the last word, so a cut line hides the app.
    out = subprocess.run(["ps", "-ww", "-axo", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "command": parts[2][:240]})
    return rows


def _carries_hook_flag(command):
    """The flag is a whole argument of the process; a shell whose command text merely mentions it is not an app."""
    words = command.split()
    return bool(words) and os.path.basename(words[0]) not in SHELLS and any(w.startswith(HOOK_FLAG) for w in words[1:])


def live_apps(rows=None):
    """Every process started with the hook flag, as {"pid", "ppid", "command"}; ours or another run's."""
    return [r for r in (rows or _table()) if r["pid"] != os.getpid() and _carries_hook_flag(r["command"])]


def reapable(apps, rows=None, me=None):
    """The apps that are a leftover of this suite: a descendant of this process, or an orphan (its runner is gone).

    An app whose runner is alive and is not us belongs to another run holding its own session; it is reported, never killed.
    """
    rows, me = rows or _table(), me or os.getpid()
    parent = {r["pid"]: r["ppid"] for r in rows}

    def ours(pid):
        seen = set()
        while pid > 1 and pid not in seen:
            if pid == me:
                return True
            seen.add(pid)
            pid = parent.get(pid, 0)
        return False
    return [a for a in apps if a["ppid"] == 1 or ours(a["ppid"])]


def _answers(path):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(1)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def hook_sockets():
    """The hook sockets on disk, each marked `live` when something still accepts on it."""
    return [{"path": p, "live": _answers(p)} for p in sorted(glob.glob(SOCKET_GLOB))]


def reap(apps, timeout=10):
    """Quit, then kill, each app and wait until it is gone; a stale socket file of a dead session goes with it."""
    for sig, wait in ((signal.SIGTERM, timeout / 2), (signal.SIGKILL, timeout)):
        left = [a for a in apps if _alive(a["pid"])]
        for a in left:
            try:
                os.killpg(os.getpgid(a["pid"]), sig)
            except OSError:
                try:
                    os.kill(a["pid"], sig)
                except OSError:
                    pass
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and any(_alive(a["pid"]) for a in left):
            time.sleep(0.1)
    for s in hook_sockets():
        if not s["live"]:
            try:
                os.unlink(s["path"])
            except OSError:
                pass


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


def clean_start():
    """What was alive when the case began (the evidence), after reaping it; raises when something survives the reaping."""
    rows = _table()
    seen = {"apps": live_apps(rows), "sockets": hook_sockets()}
    mine = reapable(seen["apps"], rows)
    reap(mine)
    gone = {a["pid"] for a in mine}
    left = [a for a in live_apps() if a["pid"] in gone]
    assert not left, f"an emulator from an earlier case is still alive after the reaping: {left}"
    return seen


def launch_failures(results):
    """The batches whose launch exited before its hook came up (an adapter `Unavailable`), each named with its output."""
    found = []
    for b in results.get("batches", []):
        verdict = b.get("verdict", "")
        if verdict.startswith("failed (") and ("before opening its hook" in verdict or "before the hook came up" in verdict
                                                or "hook did not come up" in verdict):
            found.append(f"launch failure: batch {b.get('id')!r}: {verdict} {b.get('output', '')}".strip())
    return found


def assert_launched(results, seen=None):
    """Fail with the launch failure and what was alive at the start, not with the steps the dead launch left unexecuted."""
    failures = launch_failures(results)
    if failures:
        alive = [f"pid {a['pid']}: {a['command']}" for a in (seen or {}).get("apps", [])]
        socks = [s["path"] for s in (seen or {}).get("sockets", [])]
        raise AssertionError("\n".join(failures) + f"\nemulators alive at the start of the case: {alive or 'none'}"
                             f"\nhook sockets on disk at the start: {socks or 'none'}")
