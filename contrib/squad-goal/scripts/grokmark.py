#!/usr/bin/env python3
"""The grok-limited marker: one rule for review.sh, the hub and the chain health reader (#1417).

The marker holds its write epoch. It is fresh for SG_GROK_LIMIT_TTL seconds (default 21600) after that epoch, then
expired. An expired marker lets one review try grok: a non-blocking kernel flock on `grok-probe.lock` beside the marker,
held on a descriptor that the grok try and its children inherit. The kernel frees the lock when the last holder exits.
Every write takes that lock, after the grok try's own descriptor is closed. A 402 renews the epoch and a real answer removes the marker; nothing else writes or removes it.

usage: grokmark.py state <shared dir> [now]   prints `none`, `fresh <next probe epoch>` or `expired`
       grokmark.py lock <fd>                  takes the lock on an inherited descriptor; exit 0 won, 1 held
       grokmark.py renew|clear <shared dir>   write a new epoch, or remove the marker, under the probe lock (5 s bound; exit 1 past it)
       grokmark.py probe <shared dir>         prints `none`, `limited`, `probing` or `eligible`
       grokmark.py when <epoch>               prints the epoch as local `YYYY-MM-DD HH:MM:SS`
"""
import fcntl
import os
import pathlib
import sys
import time

MARKER, LOCK, DEFAULT_TTL = "grok-limited", "grok-probe.lock", 21600


def ttl():
    try:
        return max(0, int(os.environ.get("SG_GROK_LIMIT_TTL") or DEFAULT_TTL))
    except ValueError:
        return DEFAULT_TTL


def written(shared):
    """The marker's write epoch; None with no marker; 0 for a marker with no readable epoch (always expired)."""
    try:
        text = (pathlib.Path(shared) / MARKER).read_text().strip()
    except OSError:
        return None
    try:
        return float(text)
    except ValueError:
        return 0.0


def state(shared, now=None):
    """('none',), ('fresh', next probe epoch) or ('expired',)."""
    t = written(shared)
    if t is None:
        return ("none",)
    until = t + ttl()
    return ("fresh", int(until)) if (time.time() if now is None else now) < until else ("expired",)


def lock_held(shared):
    """True while a probe holds the lock: a non-blocking try fails. The try itself holds the lock for an instant."""
    try:
        fd = os.open(str(pathlib.Path(shared) / LOCK), os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def locked(shared, bound=None):
    """Hold the probe lock exclusively for a write: blocking, polled for at most `bound` seconds; None when the bound passes."""
    if bound is None:
        bound = float(os.environ.get("SG_GROK_LOCK_BOUND") or 5)
    fd = os.open(str(pathlib.Path(shared) / LOCK), os.O_RDWR | os.O_CREAT, 0o644)
    end = time.monotonic() + bound
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= end:
                os.close(fd)
                return None
            time.sleep(0.05)


def renew(shared, now=None):
    fd = locked(shared)
    if fd is None:
        return False
    try:
        _write(shared, now)
    finally:
        os.close(fd)
    return True


def _write(shared, now=None):
    p = pathlib.Path(shared) / MARKER
    tmp = p.with_name(f".{MARKER}.{os.getpid()}.tmp")
    tmp.write_text(f"{int(time.time() if now is None else now)}\n")
    os.replace(tmp, p)


def clear(shared):
    fd = locked(shared)
    if fd is None:
        return False
    try:
        (pathlib.Path(shared) / MARKER).unlink()
    except OSError:
        pass
    finally:
        os.close(fd)
    return True


def probe_state(shared, now=None):
    """The hub's word for a grok entry with a marker: limited, probing or eligible; None with no marker."""
    s = state(shared, now)
    if s[0] == "none":
        return None
    if s[0] == "fresh":
        return "limited"
    return "probing" if lock_held(shared) else "eligible"


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "state" and len(argv) >= 3:
        print(*state(argv[2], float(argv[3]) if len(argv) > 3 else None))
    elif cmd == "lock" and len(argv) == 3:
        fd = int(argv[2])
        try:
            os.set_inheritable(fd, True)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 1
    elif cmd == "renew" and len(argv) == 3:
        return 0 if renew(argv[2]) else 1
    elif cmd == "clear" and len(argv) == 3:
        return 0 if clear(argv[2]) else 1
    elif cmd == "probe" and len(argv) == 3:
        print(probe_state(argv[2]) or "none")
    elif cmd == "when" and len(argv) == 3:
        print(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(float(argv[2])))))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
