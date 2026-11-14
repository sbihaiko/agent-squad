"""The hub Profiler's rule layer (#1743, ADR-0137): process-table readings in, a hot-spot tree out. No I/O and no clock.

A reading is {pid: (ppid, cpu seconds, command)}. `fold` turns two readings into what happened between them: the CPU
seconds each process used and one event for each process the second reading saw first. Each process is filed under the
loop parts its ancestry names, outermost first, so the tree reads like a call tree: a driver, the run it started, the gate
below it. A process that names no part of its own counts as its nearest named ancestor's own time.

A process that started and ended between two readings is never seen, so the event count is a floor.
"""
import collections
import functools
import re

SPAN_S = 600   # the window a profile covers: long enough to see a slow cycle, short enough that an old storm leaves it

# The loop's drivers by name. A fixture copies a driver into a temp folder, so the name alone must be enough: the two
# orphaned fixture loops that started this tab ran as `<temp folder>/watch.sh`, outside the shipped scripts folder.
DRIVERS = frozenset((
    "watchd.sh", "watch.sh", "fastloop.sh", "janitor.py", "childcc.py", "autofrontier.sh", "autobug.sh", "cqflush.sh",
    "reconcile.sh", "branchland.sh", "live-guard.sh", "promote.sh", "board.sh", "board.py", "review.sh", "caps.sh"))
GATES = frozenset(("gate.sh", "fast-gate.sh"))
_SCRIPT = re.compile(r"(?:^|[\s/'\"=])([A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:sh|py))(?=$|[\s'\";])")
_SCRIPTS_DIR = re.compile(r"squad-goal/scripts/([A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:sh|py))(?=$|[\s'\";])")
_SUITE = re.compile(r"test-[A-Za-z0-9_.-]+\.sh$")
_PYTEST = re.compile(r"(?:^|[\s/])(?:pytest|py\.test)(?=$|\s)|-m pytest\b|\bxdist\b")
_ENGINE = re.compile(r"(?:^|[\s/])squad (?:start|resume|work|task)\b|-m squad(?:\.cli)?(?=$|\s)")
_TEMP_SCRIPT = re.compile(r"(?:/T/tmp\.[^/\s]+/|/tmp/)\S*?([A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:sh|py))(?=$|[\s'\";])")
_APP = re.compile(r"/Applications/(?:[^/]+/)*?([^/]+)\.app/")
OTHER = ("other", "other")
_HEAD = 400    # a command's head names what it runs; its tail is arguments, and some run to kilobytes


@functools.lru_cache(maxsize=8192)
def label(command):
    """(name, kind) for the loop part a command runs, or None when it names none."""
    head = command[:_HEAD]
    if "_bundled/claude" in head:
        return ("child session", "run")
    if _PYTEST.search(head):
        return ("pytest", "gate")
    for name in _SCRIPT.findall(head):
        if _SUITE.match(name):
            return (name, "suite")
        if name in GATES:
            return (name, "gate")
        if name in DRIVERS:
            return (name, "driver")
    m = _SCRIPTS_DIR.search(head)
    if m:
        return (m.group(1), "driver")
    if _ENGINE.search(head):
        return ("engine", "run")
    return None


@functools.lru_cache(maxsize=8192)
def _orphan(command):
    """The orphan's node for a parentless command whose script sits in a temp folder, or None."""
    m = _TEMP_SCRIPT.search(command[:_HEAD])
    return (f"orphan {m.group(1)} (temp folder)", "orphan") if m else None


@functools.lru_cache(maxsize=8192)
def _plain(command):
    """The name `other` files a command under: the application, else the program's file name."""
    m = _APP.search(command[:_HEAD])
    if m:
        return m.group(1)
    return (command.split(None, 1) or ["?"])[0].rsplit("/", 1)[-1] or "?"


def _paths(table):
    """A function pid -> the node path of that process in `table`, memoized over one reading."""
    memo = {}

    def named(pid):
        """(path of named parts, the top ancestor's command) with no `other` fallback."""
        if pid in memo:
            return memo[pid]
        chain, base = [], None
        while pid in table and len(chain) < 64:   # the cap ends a parent cycle a torn read can show
            chain.append(pid)
            pid = table[pid][0]
            if pid <= 1 or pid == chain[-1]:   # pid 1 adopts every orphan: it is no ancestor that names a tree
                break
            if pid in memo:
                base = memo[pid]
                break
        for p in reversed(chain):
            ppid, _, command = table[p]
            if base is None:   # the top of its tree: pid 1 adopts an orphan, so a temp-folder script here is one
                own = (_orphan(command) if ppid == 1 else None) or label(command)
                base = ((own,) if own else (), command)
            else:
                own = label(command)
                if own and (not base[0] or base[0][-1] != own):
                    base = (base[0] + (own,), base[1])
            memo[p] = base
        return base

    def path(pid):
        parts, top = named(pid)
        return parts or (OTHER, (_plain(top), "other"))
    return path


def fold(prev, cur):
    """{node path: [cpu seconds, events]} for what happened between two readings.
    A pid the first reading does not hold is one event, and all its CPU time is new. A pid whose CPU time went back is
    another process on a reused pid, counted the same way."""
    out = {}
    path = _paths(cur)
    for pid, (_, cpu, _) in cur.items():
        was = prev.get(pid)
        new = was is None or cpu < was[1]
        used = cpu if new else cpu - was[1]
        if not new and used <= 0:
            continue
        cell = out.setdefault(path(pid), [0.0, 0])
        cell[0] += used
        cell[1] += 1 if new else 0
    return out


def tree(acc, root="loop"):
    """The hot-spot tree of a fold: each node's own seconds and events, its total with its children, and its share of the
    whole. The children sort by self time, the largest first, as a method profiler's hot spots do."""
    def blank(name, kind):
        return {"name": name, "kind": kind, "self_s": 0.0, "events": 0, "kids": {}}
    top = blank(root, "root")
    for parts, (cpu, events) in acc.items():
        n = top
        for name, kind in parts:
            n = n["kids"].setdefault(name, blank(name, kind))
        n["self_s"] += cpu
        n["events"] += events

    def total(n):
        n["total_s"] = n["self_s"] + sum(total(k) for k in n["kids"].values())
        n["total_events"] = n["events"] + sum(k["total_events"] for k in n["kids"].values())
        return n["total_s"]
    whole = total(top)

    def out(n):
        kids = sorted(n["kids"].values(), key=lambda k: (-k["self_s"], -k["total_s"], k["name"]))
        return {"name": n["name"], "kind": n["kind"], "self_s": round(n["self_s"], 3), "total_s": round(n["total_s"], 3),
                "share": round(n["total_s"] / whole, 4) if whole > 0 else (1 if n is top else 0),
                "events": n["events"], "total_events": n["total_events"],
                "mean_s": round(n["self_s"] / n["events"], 4) if n["events"] else 0,
                "children": [out(k) for k in kids]}
    return out(top)


class Window:
    """The readings of one switch-on, folded as they come, bounded to the last `span` seconds.
    It keeps one small delta per reading and never a reading older than the last, so its size follows the span."""

    def __init__(self, span=SPAN_S):
        self.span, self.since, self.prev = span, None, None
        self.steps = collections.deque()   # (time, delta) per reading, oldest first
        self.acc = {}

    def reset(self, now):
        self.since, self.prev, self.acc = now, None, {}
        self.steps.clear()

    def add(self, now, reading):
        """Take one reading. The first is the baseline: it holds every process and says nothing about the window."""
        if self.since is None:
            self.since = now
        if self.prev is not None:
            delta = fold(self.prev, reading)
            self.steps.append((now, delta))
            for parts, (cpu, events) in delta.items():
                cell = self.acc.setdefault(parts, [0.0, 0])
                cell[0] += cpu
                cell[1] += events
        self.prev = reading
        while self.steps and self.steps[0][0] < now - self.span:
            for parts, (cpu, events) in self.steps.popleft()[1].items():
                cell = self.acc[parts]
                cell[0] = max(0.0, cell[0] - cpu)
                cell[1] -= events
                if cell[0] < 1e-9 and cell[1] <= 0:
                    del self.acc[parts]

    def window_s(self, now):
        return 0 if self.since is None else max(0.0, min(now - self.since, self.span))

    def tree(self, root="loop"):
        return tree(self.acc, root)
