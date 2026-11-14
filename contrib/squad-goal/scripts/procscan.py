"""One process-table scan a tick, shared by every hub.py loaded into one interpreter (#1746).

hubmux loads hub.py once per profile into one interpreter, and each instance runs its own sampler thread. The process
table is the machine's, not a profile's, so a scan of it answers every profile's tick: one read serves them all, and the
whole hub costs the machine one scan a tick instead of one per profile. The store lives here, in a module every profile's
hub.py imports by name, because the module objects hubmux builds share nothing through their own globals.

A scan is kept for one tick and no longer: a caller whose clock is a full tick past the kept scan reads the table again.
The lock holds the read, so a tick with three profiles reads the table once and not three times in three threads.
"""
import threading

LOCK = threading.Lock()
_STATE = {"at": None, "snap": None}


def table(read, window, now):
    """The snapshot of this tick: `read` runs only when the kept scan is older than `window` seconds. The second caller
    of one tick gets the first caller's snapshot with the instant it was read at, so the caller's own window is measured
    from that instant, never from the moment it happened to ask."""
    with LOCK:
        kept = _STATE["at"]
        if _STATE["snap"] is None or kept is None or now - kept >= window:
            _STATE["snap"], _STATE["at"] = read(), now
        return _STATE["snap"], _STATE["at"]
