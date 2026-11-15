"""The one writer protocol of the loop's `active-runs.txt` (#1862, ADR-0030, ADR-0149).

Two processes write the list. The launcher appends the run it just started, and the janitor rewrites it when a pass drops
the runs that are over. The list is the loop's own fact of live work: the dashboard's live list, the janitor's
HELD-BY-WAVE, `squad.handhold` and the frontier's accounting read it.

The janitor read the list once at the start of a pass and wrote back the copy it had read. A launch that appended in
between lost its line: on 2026-11-15 the file read empty while four run leases were held and alive, and every reader saw
no work.

An atomic rename alone does not fix that. An append between the pass's read and its rename writes the old inode, and the
rename drops it. So both writers hold one kernel lock, `<list>.lock`, and the rewrite re-reads the list under it and keeps
every line its pass did not name. The lock is a separate file because the list itself is replaced: a lock held on the old
inode would not exclude the next writer.

`append` adds one line under the lock. `rewrite` takes the lines the pass keeps and the list its read produced, re-reads
under the lock, writes the whole list to a temporary file beside it, fsyncs and renames it into place (ADR-0030), and
leaves the destination untouched when nothing changed.

Guard: `tests/test_active_runs_write.py`.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib
import tempfile
from collections.abc import Iterator, Sequence

LOCK_SUFFIX = ".lock"


def lock_path(path: pathlib.Path | str) -> pathlib.Path:
    """The lock both writers hold: a file beside the list, never the list itself, which the rewrite replaces."""
    return pathlib.Path(str(path) + LOCK_SUFFIX)


@contextlib.contextmanager
def locked(path: pathlib.Path | str) -> Iterator[None]:
    """Hold the list's writer lock from the bytes a writer reads to the rename it writes; the kernel frees it if it dies."""
    p = lock_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def read(path: pathlib.Path | str) -> list[str]:
    """The entries of the list; a list that is not there names no run."""
    try:
        return pathlib.Path(path).read_text().split()
    except FileNotFoundError:
        return []


def append(path: pathlib.Path | str, line: str) -> None:
    """Add one entry, as the launcher does, under the lock a pass's rewrite holds."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with locked(p):
        with open(p, "a") as f:
            f.write(line.rstrip("\n") + "\n")
            f.flush()
            os.fsync(f.fileno())


def rewrite(path: pathlib.Path | str, names: Sequence[str], lines: Sequence[str]) -> list[str]:
    """Write `lines` as the list and keep every entry the pass's read (`names`) did not name.

    A line another writer added after that read is new work, never a line this pass judged: it goes after the kept ones.
    Returns the list as written."""
    p = pathlib.Path(path)
    with locked(p):
        cur = read(p)
        final = list(lines) + [x for x in cur if x not in names]
        if final == cur and p.exists():
            return final
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(final) + ("\n" if final else ""))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        d = os.open(str(p.parent), os.O_RDONLY)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    return final
