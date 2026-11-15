"""One process-table scan a tick, shared by every hub.py loaded into one interpreter (#1746).

hubmux loads hub.py once per profile into one interpreter, and each instance runs its own sampler thread. The process
table is the machine's, not a profile's, so a scan of it answers every profile's tick: one read serves them all, and the
whole hub costs the machine one scan a tick instead of one per profile. The store lives here, in a module every profile's
hub.py imports by name, because the module objects hubmux builds share nothing through their own globals.

A scan is kept for one tick and no longer: a caller whose clock is a full tick past the kept scan reads the table again.
The lock holds the read, so a tick with three profiles reads the table once and not three times in three threads.

The kept scan is the machine's raw process table, one format, and each reader keeps its own cell. Readers with different
formats derive their own view of the raw scan (`view`), never a second read: two readers of one tick cost one read, and
each format keeps its own state instead of one cell that one format overwrote for the other (#1848). The format that
reads the raw table is `hub.process_table`.
"""
import threading

LOCK = threading.RLock()   # a view's read may be a reader that calls this module (the profiler reads through process_table)
_CELLS = {}                            # reader name -> its raw cell {at, snap}
_VIEWS = {}                            # reader format -> (the raw snapshot it was built from, its view)


class _Cell(dict):
    """The unnamed reader's cell, and the reset handle. A caller that clears the cache by the old single-cell shape
    (sets `snap` or `at` to None) clears every reader's cell and every view, so a reset still means a reset."""

    def __setitem__(self, key, value):
        if value is None and key in ("snap", "at"):
            _CELLS.clear(); _VIEWS.clear()
        super().__setitem__(key, value)


_STATE = _Cell(at=None, snap=None)


def _cell(read):
    """The raw cell of one reader. A reader is named by its function, so a reader another caller injected (a test's fake)
    keeps its own cell and can never answer for the machine's real read. A function carries one name in every profile's
    module, so those profiles still share one cell and one read a tick."""
    name = getattr(read, "__qualname__", None)
    if not name: return _STATE
    cell = _CELLS.get(name)
    if cell is None: cell = _CELLS[name] = {"at": None, "snap": None}
    return cell


def table(read, window, now):
    """The raw snapshot of this tick from the raw reader `read`: it runs only when the kept scan of that same reader is
    older than `window` seconds. The second caller of one tick gets the first caller's snapshot with the instant it was
    read at, so the caller's own window is measured from that instant, never from the moment it happened to ask."""
    with LOCK:
        cell = _cell(read)
        kept = cell["at"]
        if cell["snap"] is None or kept is None or now - kept >= window:
            cell["snap"], cell["at"] = read(), now
        return cell["snap"], cell["at"]


def view(key, build, window, now, read):
    """One reader format's view of the tick's one raw snapshot. `read` is the raw-table reader (the format `table`
    keeps), and `build(raw)` makes this format's value. The raw read runs at most once a tick for every key, and `build`
    at most once a tick per `key`, so N readers in a tick cost one read. A view is rebuilt when the raw snapshot under it
    is a new object, which is the read a later tick made."""
    raw, at = table(read, window, now)
    with LOCK:
        got = _VIEWS.get(key)
        if got is None or got[0] is not raw:
            got = _VIEWS[key] = (raw, build(raw))
        return got[1], at
