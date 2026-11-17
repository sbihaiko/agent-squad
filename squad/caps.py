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
A gate takes its worker slots in one locked step (`try_take_many`): all it asks for when they are free, else the free slots when
they number two or more, so a third gate runs with fewer workers instead of giving up (#734); fewer than two free is no take, and
a gate never holds part of its slots while it waits for the rest. A tip check or a landing check waits first in the queue
(ADR-0174, #2049): its take names a priority, it writes a waiting marker with `next`, and no other holder takes a slot while
that marker's pid lives. Bash takes and releases through `python3 -m squad.caps take|release`; the try mode skips at once (exit 73), as
`SERIAL_TRY` does for serial.sh, and a wait that outlives its bound exits 75. A count above the ceiling is lowered to it.

The loop's free run slots are one count (`free_runs`, `Ceiling.run_holders`): every live lease machine-wide, and every run the
loop's active list names whose last invocation has no final verdict, each run once. A finished run frees its slot at once, and
its folder and record stay listed for the readers that close its issues; the counter is a reader and never rewrites the list.
The reading is ADR-0105's, the one the dashboard, `wave.sh` and `wtclean.sh` use: a verdict before a later `run_start` or
`resume` is history, and the run is live again. A listed run whose record names a process that is gone holds no slot, the
question the janitor and `reap.run_live` ask: a run killed with no verdict must not keep a slot until the takeover bound
reaches it. A record that names no process is no proof of death, so that run keeps its slot.
This is the dispatcher's side (ADR-0167): the engine's admission still counts raw leases, and the dispatcher never shows more
free slots than that admission holds.

Guard: `tests/test_machine_caps.py`.
"""
import contextlib
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

from squad import defaults as D
from squad import procs
from squad import project_settings as P
from squad import run_liveness as RL
from squad.options import OptionError

KINDS = ("runs", "children", "gates", "reviews")
SKIPPED, EXPIRED = 73, 75   # the exits serial.sh uses for a try-mode skip and for a wait that ran out
LAUNCH_GRACE = 300   # a listed run with no record holds its slot only through its launch window: the janitor drops it after this bound (rule J)


def final_verdict(text):
    """The record's final verdict: the last `verdict` event after the last `run_start` or `resume`, read as the dashboard,
    `wave.sh` and `wtclean.sh` read it (ADR-0105), through the one reader (`squad/run_liveness.py`). Return the event, or None
    when the last invocation has no verdict yet. A verdict before a later start is history: a resumed run is live again and
    holds its slot."""
    return RL.verdict(RL.invocation(text))


def _in_launch_window(folder, now):
    """True while a listed run with no record is still starting: its `request.txt` (the clock the janitor's rule-J bound and
    `launch.py` read) is younger than `LAUNCH_GRACE`. No request means the launch never began, so it holds no slot."""
    try:
        return now - (folder / "request.txt").stat().st_mtime <= LAUNCH_GRACE
    except OSError:
        return False


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


def _ask(pids):
    """{pid: (the wall time it started, its command line, its state)} for every pid the system answers for, in ONE call for
    all of them, or None when the read failed: an answer the system did not give is unknown, and the caller holds instead of
    reading it as "no process". The state is `Z` for a zombie, a holder that exited and that its parent has not reaped
    (#1573)."""
    want = sorted({int(p) for p in pids if isinstance(p, int) and not isinstance(p, bool) and p > 0})
    if not want:
        return {}
    try:
        table = procs.snapshot(want)
    except procs.ReadError:
        return None
    return {pid: (p.start, p.command, p.stat) for pid, p in table.items()}


def _procs(pids):
    """`_ask`, with a failed read as no process: the lease read reclaims a holder it cannot ask about, as it always has.

    The slots are counted on every poll of every wait, and a wait polls until a slot frees, so one `ps` per lease is a fork
    storm: a worker spins in a subprocess loop and holds what the rest of the suite needs, and on a two-core runner the
    wait never converges and the run never reaches a verdict (#130; the runner in #158). One call per count, whatever the
    number of leases; the answer is the same one. The command comes in that same call, so the dashboard rule below costs
    no second process (#1389).
    """
    return _ask(pids) or {}


def started_at(pids):
    """{pid: the wall time it started} for every pid the system answers for, in ONE call for all of them (`_procs`)."""
    return {pid: started for pid, (started, _cmd, _stat) in _procs(pids).items()}


# The page server the loop starts beside a run. It idles and never works the run, so a lease that names one is no live
# run: an orphaned dashboard kept a dead run's slot for hours, the janitor could not prove the run dead, and the drain
# never ended (#1389). A running engine is `-m squad.cli`, never this.
DASHBOARD = re.compile(r"(?:^|\s|/)squad\.dashboard_server\b")


def alive(pid, since=None, started=None, zombie=False):
    """True while the process `pid` lives; `since` adds the pid-reuse rule, the one rule every liveness reader uses
    (`run_liveness.same_process`): a process that started after the lease was written is a different process than the one
    it names, so the lease is dead. `started` is the answer to `started_at` when the caller already holds it, one call for
    many pids; None asks the system for this one pid. `zombie` is the process read's answer (`_procs`) for a holder that
    exited and that its parent has not reaped: a zombie answers a signal and works nothing, so it holds no lease (#1573)."""
    if zombie:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if not isinstance(since, (int, float)) or isinstance(since, bool):
        return True
    if started is None:
        started = started_at([pid])
    return RL.same_process(started.get(pid), since)


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

    def _reap(self, p, pid, why):
        """Remove a stale lease and say so on one line naming the slot, its kind and its holder: a reap is never silent,
        the rule `serial.sh` applies to its lock (#1057, #1573). A reap whose own write fails still says which."""
        try:
            p.unlink()
        except FileNotFoundError:
            return                              # another count reaped it first: one line, never two
        except OSError as e:
            print(f"caps: cannot reap stale slot {p.name} in {p.parent.name}: {e}", file=sys.stderr)
            return
        print(f"caps: reaping stale slot {p.name} in {p.parent.name}: holder pid {pid} is {why}", file=sys.stderr)

    def _live(self, sub):
        """The leases of one subfolder whose holder lives. A lease of a dead or zombie holder is removed here, with one line.

        The leases are read first and their holders asked about in one call, so a count costs one process however many
        leases stand (`started_at`): the per-lease `ps` was the fork storm that parked the runner.
        """
        leases = []
        for p in sorted((self.folder / sub).glob("*.json")):
            try:
                leases.append((p, json.loads(p.read_text())))
            except (OSError, ValueError):
                continue
        found = _procs([lease.get("pid", -1) for _, lease in leases])
        started = {pid: at for pid, (at, _cmd, _stat) in found.items()}
        out = []
        for p, lease in leases:
            pid = lease.get("pid", -1)
            if pid in found and DASHBOARD.search(found[pid][1] or ""):
                self._reap(p, pid, "a run's page server")   # a page server idles: a lease that names one holds no slot (#1389)
                continue
            if alive(pid, lease.get("since"), started, zombie=pid in found and found[pid][2] == "Z"):
                out.append(lease)
                continue
            self._reap(p, pid, "a zombie" if pid in found and found[pid][2] == "Z" else "dead")
        return out

    def occupancy(self):
        """{runs, children, gates, reviews, waiting}: live leases per kind and the live waits, each {ceiling, holder}, and
        `next` (the check that waits) for a priority waiter, so the panel names the next holder."""
        with self._lock():
            occ = {k: len(self._live(k)) for k in KINDS}
            occ["waiting"] = [{"ceiling": w["ceiling"], "holder": w["holder"], **({"next": w["next"]} if w.get("next") else {})}
                              for w in self._live("waiting")]
        return occ

    def waiting(self):
        return self.occupancy()["waiting"]

    def run_holders(self, active_runs, root=".", now=None):
        """The runs that hold a slot of `caps.runs`, each named once: every live lease machine-wide, and every run the loop's
        active list names whose last invocation has no final verdict. A run whose last verdict follows its last `run_start`
        or `resume` is over: it holds no slot, though its folder and record stay listed for the readers that close its issues
        (the janitor and childcc read the same file). A listed entry with no readable record holds its slot through its launch
        window only: past `LAUNCH_GRACE` the janitor drops a launch that never started, so it never pins a slot, and a listed
        folder that is gone holds no slot at all. A listed run with no verdict whose record names its own process holds no
        slot when that process is gone (`run_liveness.engine_process`, the pid-reuse rule the janitor reads): a run killed
        with no verdict frees its slot at the next count, and the janitor keeps its surgery. A record that names no process
        keeps its slot: a missing pid is no proof of death.
        A live lease always counts, even when its run is finished: the engine's admission counts raw leases, and a dispatcher
        that freed a lingering lease would start a run the engine refuses (ADR-0167). The engine releases its lease before it
        writes the verdict, so a finished run normally holds none.
        `root` resolves a relative entry, as autofrontier.sh and autobug.sh do (`$SG_CHECKOUT/runs/<name>`)."""
        now = time.time() if now is None else now
        try:
            rows = Path(active_runs).read_text().split()
        except OSError:
            rows = []
        listed = {}
        for r in rows:
            folder = Path(r) if r.startswith("/") else Path(root) / "runs" / r
            listed[folder.name] = folder
        over = set()   # a listed run that holds no slot
        pending = {}   # a listed run with no verdict whose own process the record names: {name: (pid, since)}
        for name, folder in listed.items():
            try:
                text = (folder / "record.jsonl").read_text()
            except OSError:
                if not _in_launch_window(folder, now):
                    over.add(name)   # no record and past the launch window: the janitor drops it, so it holds no slot
                continue
            if final_verdict(text) is not None:
                over.add(name)
                continue
            proc = RL.engine_process(RL.invocation(text))
            if proc is not None:   # a record that names no pid is no proof of death: the run keeps its slot
                pending[name] = proc
        with self._lock():   # a writer holds this lock while it writes a lease, so a lease mid-write is never read as absent
            leases = self._live("runs")
        holders = []
        for lease in leases:
            holder = lease.get("holder")
            if holder not in holders:
                holders.append(holder)
        asked = _ask([pid for pid, _since in pending.values()])
        for name, (pid, since) in pending.items():
            if name in holders or asked is None:
                continue   # a live lease already counts, and an unreadable table is unknown, never "no process"
            seen = asked.get(pid)
            if seen is None or seen[2] == "Z" or not alive(pid, since, started={pid: seen[0]}):
                over.add(name)   # the process the record names is gone: the run holds no slot
        for name in listed:
            if name not in over and name not in holders:
                holders.append(name)
        return holders

    def try_take(self, kind, holder, pid=None):
        """Take one slot of `kind` for `holder` if the ceiling has room. Return the lease path, or None when it is full."""
        with self._lock():
            if len(self._live(kind)) >= self.caps[kind] or self._first(kind, holder) is not None:
                return None
            p = self.folder / kind / _name(holder)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"holder": holder, "pid": pid or os.getpid(), "since": time.time()}))
            return p

    def holders(self, kind):
        """The holders of the live leases of `kind`, each named once, in the order of their leases."""
        with self._lock():
            return list(dict.fromkeys(lease.get("holder", "?") for lease in self._live(kind)))

    def holding(self, kind):
        """The live leases of `kind`, each named once with the facts the lease carries, in the order of its leases.

        A name alone sends a reader to the wrong process: a gate names its holder `gate-$$`, the inner shell's pid, and
        writes the lease under the lock holder's pid (#1653). So a refusal names the pid it counted and when it started."""
        with self._lock():
            out, seen = [], set()
            for lease in self._live(kind):
                holder = lease.get("holder", "?")
                if holder in seen:
                    continue
                seen.add(holder)
                since = lease.get("since")
                at = time.strftime("%H:%M:%S", time.localtime(since)) if isinstance(since, (int, float)) else "?"
                out.append(f"{holder} (lease pid {lease.get('pid', '?')}, live, since {at})")
            return out

    def _first(self, kind, holder):
        """The live priority waiter of `kind` that is not `holder`, or None. Call it under the lock."""
        return next((w for w in self._live("waiting")
                     if w.get("next") and w.get("ceiling") == f"caps.{kind}" and w.get("holder") != holder), None)

    def first_in_queue(self, kind):
        """The holder of the live priority waiter of `kind`, or None: the next holder, whom a refusal names."""
        with self._lock():
            w = self._first(kind, None)
            return w and w.get("holder")

    def try_take_many(self, kind, holder, n, pid=None, priority=None):
        """Take `n` slots of `kind` for `holder` in one locked step, or none. Return the lease paths, or None when the ceiling
        gives no slot.

        The ceiling gives all `n` when it holds them, else every free slot when they number two or more, so a third gate runs with
        the slots that are free instead of giving up (#734). Fewer than two free is no take. A take is one locked step, so a gate
        never holds part of its slots while it waits for the rest.
        A `priority` take (a tip check or a landing check, ADR-0174, #2049) that gets no slot writes a waiting marker that names
        `holder` and `next: priority`, and a take of all `n` removes it; a part take keeps it until `release_holder`. While that marker's pid lives, a take by any other holder gets
        no slot, so the freed slots wait for the priority holder; a dead waiter is reaped and never blocks."""
        with self._lock():
            if n <= 0:
                return []
            mark = self.folder / "waiting" / _name(f"{kind}-{holder}")
            free, want = self.caps[kind] - len(self._live(kind)), n
            n = n if free >= n else (free if free >= 2 else 0)
            if priority is None and self._first(kind, holder) is not None:
                n = 0
            if n == 0:
                if priority is not None:
                    mark.parent.mkdir(parents=True, exist_ok=True)
                    mark.write_text(json.dumps({"ceiling": f"caps.{kind}", "holder": holder, "pid": pid or os.getpid(),
                                                "since": time.time(), "next": priority}))
                return None
            if priority is not None and n >= want:
                mark.unlink(missing_ok=True)
            elif priority is not None:     # a part take keeps its marker until it releases, so the slots it lacks never go to a child (#2049)
                mark.parent.mkdir(parents=True, exist_ok=True)
                mark.write_text(json.dumps({"ceiling": f"caps.{kind}", "holder": holder, "pid": pid or os.getpid(),
                                            "since": time.time(), "next": priority}))
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
        """Give back every slot of `kind` that `holder` took, by one slot or by many, and its priority waiting marker."""
        stem = _name(holder)[:-len(".json")]
        (self.folder / "waiting" / _name(f"{kind}-{holder}")).unlink(missing_ok=True)
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


def free_runs(c, active_runs, root="."):
    """The free slots of `caps.runs`: the ceiling less the runs that hold one (`Ceiling.run_holders`), never below zero.
    This is the one counter the loop's dispatchers read, so a run whose last invocation ended frees its slot there and
    nowhere else. The ceiling never widens: the slot is freed, the cap stays."""
    return max(c.caps["runs"] - len(c.run_holders(active_runs, root)), 0)


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
    leases = c.try_take_many(args.kind, args.holder, n, args.pid, args.priority)
    while leases is None:
        held = ", ".join(c.holding(args.kind)) or "?"
        full = f"caps.{args.kind} is full ({cap} slots), held by {held}"
        first = None if args.priority else c.first_in_queue(args.kind)
        if first:
            full = f"caps.{args.kind} keeps its freed slots for {first}, a waiting priority check (ADR-0174); held by {held}"
        if args.try_mode:
            print(f"caps: {full}: {args.holder} skipped, not queued (try)", file=sys.stderr)
            return SKIPPED
        if not said:
            print(f"caps: {args.holder} is waiting for {n} slot(s): {full}", file=sys.stderr)
            said = True
        left = args.wait - (time.monotonic() - t0)
        if left <= 0:
            print(f"caps: {args.holder} waited {args.wait:g}s for caps.{args.kind}, held by {held}", file=sys.stderr)
            if args.priority:
                c.release_holder(args.kind, args.holder)   # a waiter that gives up leaves no marker
            return EXPIRED
        time.sleep(min(poll, left))
        leases = c.try_take_many(args.kind, args.holder, n, args.pid, args.priority)
    print(len(leases))
    return 0


def main(argv=None):
    """`python3 -m squad.caps [--target DIR]`: print the resolution with the live occupancy as JSON. A missing block exits 2 with its name.
    No `--target` reads the project file of the working directory, the resolution the loop reaches with `caps.ceiling(".")`.
    `take <kind> <holder> [--count N] [--try] [--wait S] [--pid P] [--priority CHECK]` takes slots; `release <kind> <holder>` gives them back."""
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
            s.add_argument("--priority", default=None, help="the check that waits first in the queue (tip or landing, ADR-0174)")
    args = a.parse_args(argv)
    args.target = args.target if args.target is not None else "."   # the CLI resolves the working directory, as the loop does
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
