"""The machine-wide ceiling: `caps.runs` live runs, `caps.children` live dispatch children, `caps.gates` gate worker slots and
`caps.reviews` live reviews, across every repository and every run.

`config/agent-defaults.yaml` holds the four. A project settings file may lower it and never raise it. A missing block is a named failure.
This pair is not the per-run pair (`run.max_parallel`, dispatch `max_children`): the two never merge.
`resolve` is the one resolution: the engine's admission, the loop's free slots and the hub panel all read it.

A slot is a lease file in the leases folder (`SQUAD_CAPS_DIR`, else ~/.cache/agent-squad/caps), one subfolder per kind.
A lease names the pid of its holder. A lease whose pid is dead is reclaimed each time the slots are counted, the same reasoning
the janitor applies to a stranded run: no live process holds it. The holders of one count are asked about in one call, never one
process per lease: the count runs on every poll of every wait, and a `ps` per lease is the fork storm that parks a small runner.
Filling the ceiling queues: `take` waits for a slot until its bound, writes a waiting marker the panel names, then raises
CeilingWaitExpired, which names the ceiling that was full.
A gate takes its worker slots all at once or none (`try_take_many`): a partial hold across two gates is a deadlock that lasts until
the wait bound. Bash takes and releases through `python3 -m squad.caps take|release`; the try mode skips at once (exit 73), as
`SERIAL_TRY` does for serial.sh, and a wait that outlives its bound exits 75. A count above the ceiling is lowered to it.

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

KINDS = ("runs", "children", "gates", "reviews")
SKIPPED, EXPIRED = 73, 75   # the exits serial.sh uses for a try-mode skip and for a wait that ran out


class CapsError(OptionError):
    """The plugin defaults hold no sound caps block."""


class CeilingWaitExpired(Exception):
    """A wait for a slot outlived its bound. `ceiling` names the full ceiling (`caps.<kind>`)."""

    def __init__(self, kind, cap, holder):
        self.ceiling, self.cap, self.holder = f"caps.{kind}", cap, holder
        super().__init__(f"ceiling_wait_expired:{self.ceiling} ({cap} live) for {holder}")


def _count(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def resolve(target=None, defaults_path=None):
    """Return {runs, children, gates, reviews, path, sha256, project}: the plugin values, lowered by the project file under `target`.
    A missing block raises CapsError; a project value above the plugin value raises OptionError."""
    path = Path(defaults_path or D.PATH)
    block = (D.load(path) or {}).get("caps")
    if not isinstance(block, dict) or not all(_count(block.get(k)) for k in KINDS):
        raise CapsError(f"{path}: no caps block (caps.runs, caps.children, caps.gates, caps.reviews); the machine-wide ceiling has no default")
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
        """{runs, children, gates, reviews, waiting}: live leases per kind and the live waits, each {ceiling, holder}."""
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

    def holders(self, kind):
        """The holders of the live leases of `kind`, each named once, in the order of their leases."""
        with self._lock():
            return list(dict.fromkeys(lease.get("holder", "?") for lease in self._live(kind)))

    def try_take_many(self, kind, holder, n, pid=None):
        """Take `n` slots of `kind` for `holder` all at once, or none. Return the lease paths, or None when the ceiling has no room
        for all of them: a gate never holds part of its slots while it waits for the rest."""
        with self._lock():
            if len(self._live(kind)) + n > self.caps[kind]:
                return None
            out = []
            for i in range(n):
                p = self.folder / kind / _name(f"{holder}.slot{i}")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps({"holder": holder, "pid": pid or os.getpid(), "since": time.time()}))
                out.append(p)
            return out

    def release_many(self, leases):
        for lease in leases or []:
            self.release(lease)

    def release_holder(self, kind, holder):
        """Give back every slot of `kind` that `holder` took, by one slot or by many."""
        stem = _name(holder)[:-len(".json")]
        for p in [self.folder / kind / f"{stem}.json", *(self.folder / kind).glob(f"{stem}.slot*.json")]:
            p.unlink(missing_ok=True)

    def release(self, lease):
        if lease is not None:
            Path(lease).unlink(missing_ok=True)

    async def take(self, kind, holder, deadline_at, on_wait=None, on_tick=None, tick=60.0):
        """Take one slot, waiting while the ceiling is full. `deadline_at` is on the anyio clock; None waits with no bound.
        `on_wait` gets one {ceiling, cap, live, holder, held_by} when the wait begins. `on_tick` gets the same dict every `tick`
        seconds while the wait lasts, so a run that waits keeps writing to its record and the heartbeat finds it moving, never
        wedged. A bound that expires raises CeilingWaitExpired."""
        import anyio  # here, not at the top: the loop scripts read `resolve` and `occupancy` with a python3 that may lack it
        lease = self.try_take(kind, holder)
        if lease is not None:
            return lease
        mark = self.folder / "waiting" / _name(f"{kind}-{holder}")
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(json.dumps({"ceiling": f"caps.{kind}", "holder": holder, "pid": os.getpid()}))
        info = {"ceiling": f"caps.{kind}", "cap": self.caps[kind], "live": self.occupancy()[kind], "holder": holder, "held_by": self.holders(kind)}
        if on_wait:
            on_wait(info)
        ticked = anyio.current_time()
        try:
            while (lease := self.try_take(kind, holder)) is None:
                if on_tick and anyio.current_time() - ticked >= tick:
                    ticked = anyio.current_time()
                    on_tick({**info, "live": self.occupancy()[kind], "held_by": self.holders(kind)})
                if deadline_at is not None and anyio.current_time() >= deadline_at:
                    raise CeilingWaitExpired(kind, self.caps[kind], holder)
                left = self.poll if deadline_at is None else min(self.poll, max(0.0, deadline_at - anyio.current_time()))
                left = min(left, tick) if on_tick else left
                await anyio.sleep(left)
            return lease
        finally:
            mark.unlink(missing_ok=True)


def ceiling(target=None, defaults_path=None, where=None):
    """The one Ceiling: the resolution and the machine-wide leases folder."""
    return Ceiling(resolve(target, defaults_path), where)


def _take(args):
    """`take <kind> <holder>`: print the count of slots taken. Try mode skips at once (73); a wait past `--wait` seconds exits 75."""
    import sys
    c = ceiling(args.target)
    cap, n = c.caps[args.kind], args.count
    if n > cap:
        print(f"caps: caps.{args.kind} holds {cap}: {args.holder} takes {cap} of {n}, never waits for slots that cannot exist", file=sys.stderr)
        n = cap
    poll = float(os.environ.get("SQUAD_CAPS_POLL") or 1.0)
    t0, said = time.monotonic(), False
    while c.try_take_many(args.kind, args.holder, n, args.pid) is None:
        held = ", ".join(c.holders(args.kind)) or "?"
        full = f"caps.{args.kind} is full ({cap} slots), held by {held}"
        if args.try_mode:
            print(f"caps: {full}: {args.holder} skipped, not queued (try)", file=sys.stderr)
            return SKIPPED
        if not said:
            print(f"caps: {args.holder} is waiting for {n} slot(s): {full}", file=sys.stderr)
            said = True
        left = args.wait - (time.monotonic() - t0)
        if left <= 0:
            print(f"caps: {args.holder} waited {args.wait:g}s for caps.{args.kind}, held by {held}", file=sys.stderr)
            return EXPIRED
        time.sleep(min(poll, left))
    print(n)
    return 0


def main(argv=None):
    """`python3 -m squad.caps [--target DIR]`: print the resolution with the live occupancy as JSON. A missing block exits 2 with its name.
    `take <kind> <holder> [--count N] [--try] [--wait S] [--pid P]` takes slots; `release <kind> <holder>` gives them back."""
    import argparse
    import sys
    argv = sys.argv[1:] if argv is None else argv
    a = argparse.ArgumentParser(prog="squad.caps")
    a.add_argument("--target")
    sub = a.add_subparsers(dest="cmd")
    for name in ("take", "release"):
        s = sub.add_parser(name)
        s.add_argument("kind", choices=KINDS)
        s.add_argument("holder")
        s.add_argument("--target")
        if name == "take":
            s.add_argument("--count", type=int, default=1)
            s.add_argument("--pid", type=int, default=os.getppid())
            s.add_argument("--wait", type=float, default=0.0)
            s.add_argument("--try", dest="try_mode", action="store_true")
    args = a.parse_args(argv)
    try:
        c = ceiling(args.target)
        if args.cmd == "take":
            return _take(args)
    except OptionError as e:
        print(f"squad.caps: {e}", file=sys.stderr)
        return 2
    if args.cmd == "release":
        c.release_holder(args.kind, args.holder)
        return 0
    print(json.dumps({**c.caps, "occupancy": c.occupancy()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
