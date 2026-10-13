"""The machine-wide ceiling: `caps.runs` live runs and `caps.children` live dispatch children, across every repository and every run.

`config/agent-defaults.yaml` holds the pair. A project settings file may lower it and never raise it. A missing block is a named failure.
This pair is not the per-run pair (`run.max_parallel`, dispatch `max_children`): the two never merge.
`resolve` is the one resolution: the engine's admission, the loop's free slots and the hub panel all read it.

A slot is a lease file in the leases folder (`SQUAD_CAPS_DIR`, else ~/.cache/agent-squad/caps), one subfolder per kind.
A lease names the pid of its holder. A lease whose pid is dead is reclaimed each time the slots are counted, the same reasoning
the janitor applies to a stranded run: no live process holds it. The holders of one count are asked about in one call, never one
process per lease: the count runs on every poll of every wait, and a `ps` per lease is the fork storm that parks a small runner.
Filling the ceiling queues: `take` waits for a slot until its bound, writes a waiting marker the panel names, then raises
CeilingWaitExpired, which names the ceiling that was full.

Guard: `tests/test_machine_caps.py`.
"""
import contextlib
import datetime
import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path

from squad import defaults as D
from squad import project_settings as P
from squad.options import OptionError

KINDS = ("runs", "children")


class CapsError(OptionError):
    """The plugin defaults hold no sound caps block."""


class CeilingWaitExpired(Exception):
    """A wait for a slot outlived its bound. `ceiling` names the full ceiling (`caps.runs` or `caps.children`)."""

    def __init__(self, kind, cap, holder):
        self.ceiling, self.cap, self.holder = f"caps.{kind}", cap, holder
        super().__init__(f"ceiling_wait_expired:{self.ceiling} ({cap} live) for {holder}")


def _count(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def resolve(target=None, defaults_path=None):
    """Return {runs, children, path, sha256, project}: the plugin pair, lowered by the project file under `target`.
    A missing block raises CapsError; a project value above the plugin value raises OptionError."""
    path = Path(defaults_path or D.PATH)
    block = (D.load(path) or {}).get("caps")
    if not isinstance(block, dict) or not all(_count(block.get(k)) for k in KINDS):
        raise CapsError(f"{path}: no caps block (caps.runs, caps.children); the machine-wide ceiling has no default")
    out = {k: block[k] for k in KINDS}
    data, prec = P.load(target) if target else (None, None)
    for k in KINDS:
        v = ((data or {}).get("caps") or {}).get(k)
        if v is None:
            continue
        if v > out[k]:
            raise OptionError(f"project settings key caps.{k}: {v} is above the machine-wide ceiling {out[k]}; a project may only lower it")
        out[k] = v
    return {**out, "path": str(path.resolve()), "sha256": D.digest(path), "project": prec}


def folder():
    return Path(os.environ.get("SQUAD_CAPS_DIR") or Path.home() / ".cache" / "agent-squad" / "caps")


def started_at(pids):
    """{pid: the wall time it started} for every pid the system answers for, in ONE call for all of them.

    The slots are counted on every poll of every wait, and a wait polls until a slot frees, so one `ps` per lease is a fork
    storm: a worker spins in a subprocess loop and holds what the rest of the suite needs, and on a two-core runner the
    wait never converges and the run never reaches a verdict (#130; the runner in #158). One call per count, whatever the
    number of leases; the answer is the same one.
    """
    want = sorted({int(p) for p in pids if isinstance(p, int) and not isinstance(p, bool) and p > 0})
    if not want:
        return {}
    try:
        out = subprocess.run(["/bin/ps", "-o", "pid=,lstart=", "-p", ",".join(str(p) for p in want)],
                             capture_output=True, text=True, check=False).stdout
    except OSError:
        return {}
    found = {}
    for line in out.splitlines():
        pid, _, lstart = line.strip().partition(" ")
        try:
            found[int(pid)] = datetime.datetime.strptime(lstart.strip(), "%a %b %d %H:%M:%S %Y").timestamp()
        except ValueError:
            continue
    return found


def alive(pid, since=None, started=None):
    """True while the process `pid` lives; `since` adds the pid-reuse rule: a process that started after the lease was
    written is a different process than the one it names, so the lease is dead. `started` is the answer to `started_at`
    when the caller already holds it, one call for many pids; None asks the system for this one pid."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if not isinstance(since, (int, float)):
        return True
    if started is None:
        started = started_at([pid])
    began = started.get(pid)
    return began is None or began <= since


def _name(holder):
    return re.sub(r"[^A-Za-z0-9._-]", "_", holder) + ".json"


class Ceiling:
    def __init__(self, caps, where=None, poll=1.0):
        self.caps, self.folder, self.poll = caps, Path(where or folder()), poll

    @contextlib.contextmanager
    def _lock(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(self.folder / ".lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _live(self, sub):
        """The leases of one subfolder whose holder lives. A lease of a dead holder is removed here.

        The leases are read first and their holders asked about in one call, so a count costs one process however many
        leases stand (`started_at`): the per-lease `ps` was the fork storm that parked the runner.
        """
        leases = []
        for p in sorted((self.folder / sub).glob("*.json")):
            try:
                leases.append((p, json.loads(p.read_text())))
            except (OSError, ValueError):
                continue
        started = started_at([lease.get("pid", -1) for _, lease in leases])
        out = []
        for p, lease in leases:
            if alive(lease.get("pid", -1), lease.get("since"), started):
                out.append(lease)
            else:
                p.unlink(missing_ok=True)
        return out

    def occupancy(self):
        """{runs, children, waiting}: live leases per kind and the live waits, each {ceiling, holder}."""
        with self._lock():
            occ = {k: len(self._live(k)) for k in KINDS}
            occ["waiting"] = [{"ceiling": w["ceiling"], "holder": w["holder"]} for w in self._live("waiting")]
        return occ

    def waiting(self):
        return self.occupancy()["waiting"]

    def try_take(self, kind, holder, pid=None):
        """Take one slot of `kind` for `holder` if the ceiling has room. Return the lease path, or None when it is full."""
        with self._lock():
            if len(self._live(kind)) >= self.caps[kind]:
                return None
            p = self.folder / kind / _name(holder)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"holder": holder, "pid": pid or os.getpid(), "since": time.time()}))
            return p

    def release(self, lease):
        if lease is not None:
            Path(lease).unlink(missing_ok=True)

    async def take(self, kind, holder, deadline_at, on_wait=None):
        """Take one slot, waiting while the ceiling is full. `deadline_at` is on the anyio clock; None waits with no bound.
        `on_wait` gets one {ceiling, cap, live, holder} when the wait begins. A bound that expires raises CeilingWaitExpired."""
        import anyio  # here, not at the top: the loop scripts read `resolve` and `occupancy` with a python3 that may lack it
        lease = self.try_take(kind, holder)
        if lease is not None:
            return lease
        mark = self.folder / "waiting" / _name(f"{kind}-{holder}")
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(json.dumps({"ceiling": f"caps.{kind}", "holder": holder, "pid": os.getpid()}))
        if on_wait:
            on_wait({"ceiling": f"caps.{kind}", "cap": self.caps[kind], "live": self.occupancy()[kind], "holder": holder})
        try:
            while (lease := self.try_take(kind, holder)) is None:
                if deadline_at is not None and anyio.current_time() >= deadline_at:
                    raise CeilingWaitExpired(kind, self.caps[kind], holder)
                left = self.poll if deadline_at is None else min(self.poll, max(0.0, deadline_at - anyio.current_time()))
                await anyio.sleep(left)
            return lease
        finally:
            mark.unlink(missing_ok=True)


def ceiling(target=None, defaults_path=None, where=None):
    """The one Ceiling: the resolution and the machine-wide leases folder."""
    return Ceiling(resolve(target, defaults_path), where)


def main(argv=None):
    """`python3 -m squad.caps [--target DIR]`: print the resolution with the live occupancy as JSON. A missing block exits 2 with its name."""
    import argparse
    import sys
    a = argparse.ArgumentParser(prog="squad.caps")
    a.add_argument("--target")
    args = a.parse_args(argv)
    try:
        c = ceiling(args.target)
    except OptionError as e:
        print(f"squad.caps: {e}", file=sys.stderr)
        return 2
    print(json.dumps({**c.caps, "occupancy": c.occupancy()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
