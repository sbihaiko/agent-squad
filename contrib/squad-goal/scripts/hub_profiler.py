"""The hub Loop tab's rule layer (#1743, ADR-0137): process-table readings in, a hot-spot tree out. No I/O and no clock.

A reading is {(pid, start time): (ppid, cpu seconds, command)}: a pid alone does not name a process, the system gives it
again. `fold` turns two readings into what happened between them: the CPU seconds each process used and one event for each
process the second reading saw first.

A `Scope` names the loop of one profile: the supervisor's pid and the folders only that profile's work names, plus the
other profiles of the hub, each with its own supervisor pid and run folders. The loop is the supervisor and its
descendants, each process whose command names one of those folders, with its descendants, and each orphan from a temp
folder. Each is filed under the loop parts its ancestry names, outermost first, so the tree reads like a call tree: a
driver, the run it started, the gate below it. A process that names no part of its own counts as its nearest named
ancestor's own time. Every process outside the loop counts in the context row, apart from the tree: one row for each other
profile's loop, and one row per application for the rest.

A reading answers each process's CPU seconds, or None where the system refused the read. A refused process is seen and not
read: it lowers the coverage the window answers and adds no row, since a zero there would be a guess.

A command is named by the program it runs, never by an argument: `vim watch.sh` runs vim. The whole line is read, with
no head cap: a script past a long argument list still names its process.

A process that started and ended between two readings is never seen, so each node's event count, and the starts a second
it answers, is a lower bound.
"""
import collections
import functools
import importlib.util
import math
import pathlib
import re
import shlex

# The board definition is the one order of its columns (ADR-0170): the trip reads it from the vocabulary module beside
# this script, so the panel never keeps a list that could disagree with the board (#2199).
_columns_spec = importlib.util.spec_from_file_location("board_columns", pathlib.Path(__file__).resolve().parent / "board_columns.py")
_board_columns = importlib.util.module_from_spec(_columns_spec); _columns_spec.loader.exec_module(_board_columns)

SPAN_S = 600   # the window a profile covers: long enough to see a slow cycle, short enough that an old storm leaves it

# The loop's drivers by name. A fixture copies a driver into a temp folder, so the name alone must be enough: the two
# orphaned fixture loops that started this tab ran as `<temp folder>/watch.sh`, outside the shipped scripts folder.
DRIVERS = frozenset((
    "watchd.sh", "watch.sh", "fastloop.sh", "janitor.py", "childcc.py", "autofrontier.sh", "autobug.sh", "cqflush.sh",
    "reconcile.sh", "branchland.sh", "live-guard.sh", "promote.sh", "board.sh", "board.py", "review.sh", "caps.sh"))
GATES = frozenset(("gate.sh", "fast-gate.sh"))
SUPERVISOR = "watchd.sh"
_SCRIPT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:sh|py)$")
_SUITE = re.compile(r"test-[A-Za-z0-9_.-]+\.sh$")
_TEMP = re.compile(r"/T/tmp\.[^/]+/|/tmp/")
_APP = re.compile(r"/Applications/(?:[^/]+/)*?([^/]+)\.app/")
_PYTHON = re.compile(r"[Pp]ython[\d.]*$")
_SHELLS = frozenset(("bash", "sh", "zsh", "dash"))
_SHELL_CODE = re.compile(r"-[A-Za-z]*c[A-Za-z]*$")
_WORD = re.compile(r"""(?:[^\s'"\\]|\\.|'[^']*'|"(?:\\.|[^"\\])*"|['"])+""", re.S)   # an unclosed quote is one character
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_BREAK = " \t\n;&|(){}<>"   # what ends a word in shell code; all but the blanks end a command too
_OPERATOR = re.compile(r"\d*[;&|()<>]")   # a shell operator after a word; the digits are a redirect's file descriptor
_PYTESTS = frozenset(("pytest", "py.test"))
_ENGINE_VERBS = frozenset(("start", "resume", "work", "task"))
_ENGINE_MODULES = frozenset(("squad", "squad.cli"))
# A wrapper runs another program: (the options that take a value, the words that may follow the options). `timeout` is
# apart: one duration follows its options.
_WRAPPERS = {
    "env": (frozenset(("-u", "-C", "-P", "-S")), ()),
    "uv": (frozenset(("--with", "--python", "-p", "--project", "--directory", "--extra", "--group", "--env-file",
                      "--with-requirements", "--index")), ("run",)),
    "timeout": (frozenset(("-s", "-k", "--signal", "--kill-after")), ()),
    "gtimeout": (frozenset(("-s", "-k", "--signal", "--kill-after")), ()),
    "nohup": (frozenset(), ()),
    "sandbox-exec": (frozenset(("-f", "-p", "-D", "-n")), ()),
    "rtk": (frozenset(), ("proxy",)),
}
OTHER = ("other", "other")       # the head of every path outside the loop: the context row
UNKNOWN = ("unknown", "unknown")  # a process of the loop whose program cannot be told, and no ancestor names one
FOREIGN = "another loop"         # a supervisor no other profile names, with its descendants
CONTEXT = "outside the loop"
_IN, _OUT, _OTHER_LOOP = "in", "out", "foreign"

# The supervisor's pid or None; the folders of the profile; and the other profiles' loops (name, supervisor pid or None,
# the run folders only that profile's work names). The hub names every other profile it serves; a fold with no `others`
# keeps the one row for every foreign loop.
Scope = collections.namedtuple("Scope", "supervisor marks others", defaults=((),))


def _base(path):
    return path.rsplit("/", 1)[-1]


def _options(t, i, takes, assigns=False):
    """The index after the options at t[i:]: an option in `takes` carries one value."""
    while i < len(t):
        if t[i] in takes:
            i += 2
        elif t[i].startswith("-") or (assigns and _ASSIGN.match(t[i])):
            i += 1
        else:
            break
    return i


def _unwrap(t):
    """The tokens from the program on, with every leading wrapper and shell assignment taken off."""
    i = _options(t, 0, (), assigns=True)
    while i < len(t) and _base(t[i]) in _WRAPPERS:
        name = _base(t[i])
        takes, words = _WRAPPERS[name]
        i = _options(t, i + 1, takes, assigns=name == "env")
        if name in ("timeout", "gtimeout"):
            i += 1
        while i < len(t) and t[i] in words:
            i = _options(t, i + 1, takes)
    return t[i:]


def _classify(script):
    """(name, kind) for a script the loop knows, by its file name or by the scripts folder it sits in."""
    name = _base(script)
    if not _SCRIPT.match(name):
        return None
    if _SUITE.match(name):
        return (name, "suite")
    if name in GATES:
        return (name, "gate")
    if name in DRIVERS or "squad-goal/scripts/" in script:
        return (name, "driver")
    return None


class _Word(str):
    """A word of a command line: its value, the offsets its text starts and ends at, and whether its text opens with a
    quote."""


def _words(command):
    """The words of a command line, with their offsets kept: a line's shape is read from the text, never guessed."""
    out = []
    for m in _WORD.finditer(command):
        text = m.group()
        try: value = "".join(shlex.split(text))
        except ValueError: value = text   # an unclosed quote is one word, read as it is
        w = _Word(value)
        w.start, w.end, w.quoted = m.start(), m.end(), text[0] in "'\""
        out.append(w)
    return out


def _typed(word, raw):
    """True when a shell's code is one quoted word, the shell-typed shape: its quotes were read, and what follows is only
    parameters. A code followed by any shell operator, a redirection included, is the start of a longer line, not a typed
    code: the shell reads that line as the process table prints it, so a redirected script still names its process."""
    if not word.quoted: return False
    after = raw[word.end:].lstrip()
    return not after or not _OPERATOR.match(after)


def _shell_code(t, i, raw, depth):
    """The code of a shell's `-c` at t[i], found at its own word: a shell-typed code reads its one quoted word, and a
    process table line reads the rest of the line after the flag, as ps prints it (arguments joined, no outer quotes)."""
    rest = t[i + 1:]
    if not rest: return None, None
    if raw is None or _typed(rest[0], raw):
        return _code(str(rest[0]), depth)   # the shell-typed code: the words after it are its parameters
    return _code(raw[t[i].end:].lstrip(), depth)   # the process table's line: the code runs from after the flag


def _run(t, depth=0, raw=None):
    """((name, kind) or None, the script the tokens run or None), read from the program position alone."""
    t = _unwrap(t)
    if not t:
        return None, None
    prog, name = t[0], _base(t[0])
    if prog.endswith("_bundled/claude"):
        return ("child session", "run"), None
    if name in _PYTESTS:
        return ("pytest", "gate"), None
    if name == "squad":
        return (("engine", "run") if len(t) > 1 and t[1] in _ENGINE_VERBS else None), None
    if name in _SHELLS:
        i = 1
        while i < len(t) and t[i].startswith("-"):
            if _SHELL_CODE.match(t[i]):
                return _shell_code(t, i, raw, depth)
            i += 2 if t[i] in ("-o", "+o") else 1
        return (_classify(t[i]), t[i]) if i < len(t) else (None, None)
    if _PYTHON.match(name):
        i = 1
        while i < len(t) and t[i].startswith("-"):
            if t[i] == "-m":
                module = t[i + 1] if i + 1 < len(t) else ""
                return (("pytest", "gate") if module in _PYTESTS else ("engine", "run") if module in _ENGINE_MODULES else None), None
            if t[i] == "-c":
                return None, None
            i += 2 if t[i] in ("-W", "-X") else 1
        if i >= len(t):
            return None, None
        return _run(t[i:], depth) if _base(t[i]) in _PYTESTS or _base(t[i]) == "squad" else (_classify(t[i]), t[i])
    return (_classify(prog), prog) if _SCRIPT.match(name) else (None, None)


def _commands(code):
    """The commands of a shell's code, each a list of words: a quoted word is one word, and a separator inside it splits nothing."""
    commands, cur, word, quote, open_word, i = [], [], [], None, False, 0
    while i < len(code):
        c = code[i]
        if quote:
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"' and i + 1 < len(code):
                i += 1
                word.append(code[i])
            else:
                word.append(c)
        elif c in "'\"":
            quote, open_word = c, True
        elif c == "\\" and i + 1 < len(code):
            i += 1
            word.append(code[i])
        elif c in _BREAK:
            if word or open_word:
                cur.append("".join(word))
                word, open_word = [], False
            if c not in " \t":
                commands.append(cur)
                cur = []
        else:
            word.append(c)
        i += 1
    if word or open_word:   # a quote the cut left open ends its word here
        cur.append("".join(word))
    commands.append(cur)
    return commands


def _code(code, depth):
    """A shell's code string, command by command: the first command that runs a loop part names the process."""
    if depth >= 2:
        return None, None
    for t in _commands(code):
        while t and t[0] in ("exec", "time", "if", "then", "else", "do", "while", "!"):
            t = t[1:]
        found = _run(t, depth + 1)   # no raw line: these words kept their quotes
        if found[0]:
            return found
    return None, None


def _parse(command):
    """Every shape reads the whole line: there is no head cap, so a script past a long argument list still names its
    process. The line is not cached either, since a cache would hold the whole argument list of every process and a
    process table churns it: the label memo keys a reading's row by a length and a hash, never by the line."""
    return _run(_words(command), raw=command)


def label(command):
    """(name, kind) for the loop part a command runs, or None when its program is none: an argument never names it."""
    return _parse(command)[0]


def _orphan(script, marks):
    """The orphan's node for a parentless command whose script sits in a temp folder, or None. A script in a folder of
    the profile is no orphan, wherever that folder is."""
    if not script or not _TEMP.search(script) or any(m in script for m in marks):
        return None
    return (f"orphan {_base(script)} (temp folder)", "orphan")


@functools.lru_cache(maxsize=8192)
def _plain(command):
    """The name the context row files a command under: the application, else the program's file name."""
    m = _APP.search(command)
    if m:
        return m.group(1)
    return (command.split(None, 1) or ["?"])[0].rsplit("/", 1)[-1] or "?"


@functools.lru_cache(maxsize=8192)
def _names(command, marks):
    return any(m in command for m in marks)


# The labels of one reading, keyed by (pid, start, the argv text's length, its hash) -> (label, script). It holds no
# command line: a length and a hash name the same argv without keeping it, so the rule that a line is never cached holds.
# An exec keeps the pid and the start time and changes the hash, so it names a new label. `fold` drops every process the
# reading no longer holds, and the memo never outgrows one reading of the table. One hub process serves several profiles
# on their own sampler threads, so a fold's prune can drop another profile's entry: that costs a reparse, never a wrong
# label, because the key names the whole process.
_LABELS = {}


def _labeller(rows, starts, memo):
    """pid -> (label, script) over the rows of one reading, from `memo` when it holds that argv, else parsed and kept.
    With no memo every call parses: the plain path the memo is measured against."""
    def label_of(pid):
        command = rows[pid][2]
        if memo is None:
            return _parse(command)
        key = (pid, starts.get(pid), len(command), hash(command))
        cell = memo.get(key)
        if cell is None:
            memo[key] = cell = _parse(command)
        return cell
    return label_of


def _paths(rows, scope, starts=None, memo=None):
    """A function pid -> the node path of that process in `rows` ({pid: (ppid, cpu, command)}), memoized over one reading.
    `starts` is each pid's start time and `memo` the label memo, so a process is labelled once per argv."""
    seen = {}
    marks = tuple(scope.marks)
    others = tuple(scope.others or ())
    other_pids = {sup: name for name, sup, _ in others if sup is not None}
    parsed = _labeller(rows, starts or {}, memo)
    held = parsed(scope.supervisor)[0] if scope.supervisor in rows else None
    above = rows[scope.supervisor][0] if held and held[0] == SUPERVISOR else None   # the pid file names the loop the supervisor runs; the supervisor is its parent

    def other_of(pid, command):
        """The other profile whose loop this process names: its supervisor's pid first, then the run folders it names. None
        when no other profile names it, so the loop stays the one row for the rest. A process naming this profile's own
        folders is read as this profile's first, whoever else might name it: the hub calls this after that test."""
        if pid in other_pids:
            return other_pids[pid]
        for name, _, omarks in others:
            if omarks and _names(command, omarks):
                return name
        return None

    def place(pid):
        """(in the loop, in another loop named by `parts`, or outside; the named parts; the top ancestor's command)."""
        if pid in seen:
            return seen[pid]
        chain, base = [], None
        while pid in rows and len(chain) < 64:   # the cap ends a parent cycle a torn read can show
            chain.append(pid)
            pid = rows[pid][0]
            if pid <= 1 or pid == chain[-1]:   # pid 1 adopts every orphan: it is no ancestor that names a tree
                break
            if pid in seen:
                base = seen[pid]
                break
        for p in reversed(chain):
            ppid, _, command = rows[p]
            own, script = parsed(p)
            state, parts, top = base or (_OUT, (), command)
            if (p == scope.supervisor or p == above) and own and own[0] == SUPERVISOR:   # a stale pid file can name another program
                state, parts = _IN, (own,)
            elif state == _OTHER_LOOP:
                pass   # a descendant stays in the loop its ancestry entered
            elif state == _IN:
                if own and (not parts or parts[-1] != own):
                    parts = parts + (own,)
            elif state == _OUT:
                orphan = _orphan(script, marks) if ppid == 1 else None
                if orphan:
                    state, parts = _IN, (orphan,)
                elif own and own[0] == SUPERVISOR:   # the same scripts run another profile's loop: its pid or run folders tell it
                    state, parts = _OTHER_LOOP, (other_of(p, command) or FOREIGN,)
                elif _names(command, marks):
                    state, parts = _IN, ((own,) if own else ())
                else:
                    who = other_of(p, command)
                    if who:
                        state, parts = _OTHER_LOOP, (who,)
            base = seen[p] = (state, parts, top)
        return base

    def path(pid):
        state, parts, top = place(pid)
        if state == _IN:
            return parts or (UNKNOWN,)
        if state == _OTHER_LOOP:   # a named other profile's loop; an unnamed foreign one keeps the one row for the rest
            return (OTHER, (parts[0] if parts[0] == FOREIGN else f"loop {parts[0]}", "loop"))
        return (OTHER, (_plain(top), "other"))
    return path


def fold(prev, cur, scope, memo=_LABELS):
    """{node path: [cpu seconds, events]} for what happened between two readings.
    A (pid, start time) the first reading does not hold is one event, and all its CPU time is new. So is one whose CPU
    time went back, where the system gives no start time. A process whose CPU read the system refused carries `None` in
    place of its seconds: it is seen and not read, so it is no row here, and it counts nothing anywhere. `memo` keeps
    each process's label while the reading holds it, so the next reading reuses it instead of parsing the line again; a
    process the reading no longer holds leaves the memo, and with no memo every line is parsed."""
    if memo is not None:
        live = set(cur)
        for key in list(memo):   # a snapshot: another profile's sampler can fold on its own thread in the same hub
            if (key[0], key[1]) not in live:
                memo.pop(key, None)
    out = {}
    rows, starts = {}, {}
    for (pid, start), row in cur.items():
        rows[pid], starts[pid] = row, start
    path = _paths(rows, scope, starts, memo)
    for key, (_, cpu, _) in cur.items():
        if cpu is None:
            continue
        was = prev.get(key)
        new = was is None or was[1] is None or cpu < was[1]
        used = cpu if new else cpu - was[1]
        if not new and used <= 0:
            continue
        cell = out.setdefault(path(key[0]), [0.0, 0])
        cell[0] += used
        cell[1] += 1 if new else 0
    return out


def coverage(reading):
    """(processes read with their CPU, processes seen) in one reading: a refused CPU read is seen and not read."""
    seen = len(reading)
    return sum(1 for (_, cpu, _) in reading.values() if cpu is not None), seen


def tree(acc, root="loop", window_s=None):
    """The hot-spot tree of the loop in a fold: each node's own seconds and events, its total with its children, and its
    share of the loop's total. With a window, each node also answers the starts observed under it a second: a lower
    bound, because a process that lives between two readings is never seen. The children sort by inclusive cost, the
    largest first: an idle driver whose children are costly stands above a cheap leaf. The context row is not in it."""
    def blank(name, kind):
        return {"name": name, "kind": kind, "self_s": 0.0, "events": 0, "kids": {}}

    def rate(n):
        return round(n / window_s, 3) if window_s and window_s > 0 else 0.0
    top = blank(root, "root")
    for parts, (cpu, events) in acc.items():
        if parts[0] == OTHER:
            continue
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
        kids = sorted(n["kids"].values(), key=lambda k: (-k["total_s"], -k["self_s"], k["name"]))
        return {"name": n["name"], "kind": n["kind"], "self_s": round(n["self_s"], 3), "total_s": round(n["total_s"], 3),
                "share": round(n["total_s"] / whole, 4) if whole > 0 else (1 if n is top else 0),
                "events": n["events"], "total_events": n["total_events"],
                "starts_s": rate(n["events"]), "total_starts_s": rate(n["total_events"]),
                "children": [out(k) for k in kids]}
    return out(top)


def context(acc):
    """The context row of a fold: the CPU seconds of every process outside the loop, largest first. A row per other
    profile's loop, told apart by that profile's supervisor pid and run folders, then one row for each application of the
    rest. A process of an other loop counts in that loop's row, never in the rest."""
    cells = {}
    for parts, (cpu, events) in acc.items():
        if parts[0] == OTHER:
            name, kind = parts[1]
            cell = cells.setdefault((name, kind), [0.0, 0])
            cell[0] += cpu
            cell[1] += events
    rows = sorted(cells.items(), key=lambda kv: (-kv[1][0], kv[0][0]))
    return {"name": CONTEXT, "total_s": round(sum(c for c, _ in cells.values()), 3),
            "events": sum(e for _, e in cells.values()),
            "children": [{"name": name, "kind": kind, "total_s": round(cpu, 3), "events": events}
                         for (name, kind), (cpu, events) in rows]}


class Window:
    """The readings of one switch-on, folded as they come, bounded to the last `span` seconds.
    It keeps one small delta per reading and never a reading older than the last, so its size follows the span."""

    def __init__(self, span=SPAN_S):
        self.span, self.since, self.prev = span, None, None
        self.steps = collections.deque()   # (time, delta) per reading, oldest first
        self.cov = collections.deque()     # (time, read, seen) per reading, oldest first
        self.acc = {}
        self.last, self.interval, self.failed = None, None, 0

    def reset(self, now):
        self.since, self.prev, self.acc = now, None, {}
        self.last, self.interval, self.failed = None, None, 0
        self.steps.clear()
        self.cov.clear()

    def fail(self):
        """Count a reading that failed. It is not folded: the next good one is measured from the last good one."""
        self.failed += 1

    def stats(self):
        """The time of the last good reading, the measured interval between the last two, the failed readings, and the
        coverage of the window: the processes read with their CPU over every process seen. A refused read lowers it."""
        cov = list(self.cov)   # one copy: the sampler thread prunes the window while a GET reads it (#1956's CI red)
        read = sum(r for _, r, _ in cov)
        seen = sum(s for _, _, s in cov)
        return {"last_reading": self.last, "interval_s": self.interval, "failed_readings": self.failed,
                "processes_read": read, "processes_seen": seen,
                "coverage": round(read / seen, 3) if seen else None}

    def add(self, now, reading, scope):
        """Take one reading. The first is the baseline: it holds every process and says nothing about the window. A
        process whose CPU read was refused counts in the coverage and in no row."""
        if self.since is None:
            self.since = now
        if self.last is not None:
            self.interval = round(now - self.last, 3)
        self.last = now
        read, seen = coverage(reading)
        self.cov.append((now, read, seen))
        if self.prev is not None:
            delta = fold(self.prev, reading, scope)
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
        while self.cov and self.cov[0][0] < now - self.span:
            self.cov.popleft()

    def window_s(self, now):
        return 0 if self.since is None or now is None else max(0.0, min(now - self.since, self.span))

    def tree(self, root="loop", now=None):
        """The tree of the window, its starts a second measured over the window's own end: the last good reading."""
        return tree(self.acc, root, self.window_s(self.last if now is None else now))

    def context(self):
        return context(self.acc)


# ---------------------------------------------------------------- time in failed runs (#1751)
# The third view of the Loop tab, and the only one that reads no process table: the runs of a window by verdict and, for
# a failed run, by the reasons its verdict names, with the run seconds each row covers. A failure does not prove that
# every second was lost, so the view names time, never waste. The rows come from the hub's run fold, the one reader of a
# record, so this fold reads no file and no clock of its own.
RUN_WINDOWS = (3600, 86400, 172800)   # the windows the page offers: 1 hour, 24 hours and 48 hours
RUN_WINDOW_DEFAULT = 86400
RUN_WINDOW_LABELS = {3600: "1 hour", 86400: "24 hours", 172800: "48 hours"}
RUN_TABLE_LABEL = "Time in failed runs"


def run_window(w):
    """The window a page asked for, in seconds: one of RUN_WINDOWS, else the 24 hour default. A choice the page cannot
    read is that default, never an error."""
    try: w = int(w)
    except (TypeError, ValueError): return RUN_WINDOW_DEFAULT
    return w if w in RUN_WINDOWS else RUN_WINDOW_DEFAULT


def run_seconds(r, now):
    """The seconds a folded run covers: its first event to its verdict, and up to `now` while it has no verdict."""
    t0 = r.get("t0")
    if not t0: return 0.0
    end = r.get("t1") if r.get("t1") is not None else now
    return max(0.0, min(end, now) - t0)


def failed_run_table(rows, window_s, now):
    """The runs of the window by verdict and, for a failed run, by reason: count, run seconds and share, sorted by seconds.

    `rows` is what the hub's run fold answers, one per record: `t0`, `t1`, `verdict` and `reasons`. A run is in the window
    when it ends in it, and a run with no verdict ends at `now`. A failed run counts in the row of every reason it names,
    so the reason rows of one run can add past its own seconds; the share is over every run second of the window."""
    since = now - window_s
    inside = [r for r in rows if r.get("t0") and (r.get("t1") if r.get("t1") is not None else now) >= since]
    acc, total = {}, 0.0
    for r in inside:
        secs = run_seconds(r, now); total += secs
        ver = r.get("verdict") or "running"
        reasons = [str(x) for x in (r.get("reasons") or [])] if ver == "failed" else [""]
        for reason in dict.fromkeys(reasons or [""]):
            cell = acc.setdefault((ver, reason), [0, 0.0])
            cell[0] += 1; cell[1] += secs
    out = [{"verdict": v, "reason": reason, "count": n, "seconds": round(s, 3), "share": round(s / total, 3) if total else None}
           for (v, reason), (n, s) in acc.items()]
    out.sort(key=lambda x: (-x["seconds"], x["verdict"] != "failed", x["verdict"], x["reason"]))   # a failed row wins a tie
    failed = sum(run_seconds(r, now) for r in inside if r.get("verdict") == "failed")
    return {"window_s": window_s, "runs": len(inside), "total_seconds": round(total, 3), "failed_seconds": round(failed, 3),
            "rows": out, "label": RUN_TABLE_LABEL}


# ---------------------------------------------------------------- the trip of one issue (#1769, #2152, #2199)
# One stacked bar of the trip of an issue, folded into the board's delivery columns, in the definition's order. A column
# holds the stages the loop spends its time in there: Todo holds the queue before the run and the wait for a dispatch, Doing
# holds coding, the gap between two children and the run's own gates, and Review, Landing and Gating hold the review, the
# landing and the wave's gate (#1894). A column answers its larger stage's median and p90, with the other stage beside it,
# and its width is its time per issue over the issues standing in it at once (#2256). A delivery is a run whose
# record holds both a passed land and a verdict; a run that landed nothing is no delivery and is not folded. The bars and
# tables read the percentiles on the `(n - 1)` weight, the rule `docs/loop-sequence.md` names, so a number taken here and
# the trip table agree.
#
# The run's own gates run inside the run, while the card stands in Doing, so they fold there. Gating is the complete tier
# that runs after the landing (ADR-0170), read from the gate history alone; a record's gate is never a Gating time. Closed
# draws no column (#2199): the time after the verdict is not a column the card waits in, and it stays measured under
# `record_stages`. A board column named here with None is a column a delivery never stands in.
TRIP_COLUMN_STAGES = {"Backlog": None, "Todo": ("queue", "wait"), "Doing": ("coding", "between", "gates"),
                      "Review": ("review",), "Landing": ("landing",), "Gating": ("gate",), "Closed": None,
                      "Abandoned": None}


def trip_columns(text=None):
    """The trip's columns in the board definition's order: one `(key, label, stages)` per board column a delivery stands
    in, and no other. The order and the names come from the definition (ADR-0170), never a list kept here, so the panel
    and the board name one column with one word in one order (#2199).

    A definition column this table does not name raises: a new board column is never dropped from the panel in silence,
    and the table says by hand whether a delivery stands in it. `text` is one definition to read, the pinned copy when
    None."""
    columns = _board_columns.shipped_columns(text)
    unknown = [s["name"] for s in columns if s["name"] not in TRIP_COLUMN_STAGES]
    if unknown:
        raise ValueError("the board definition names " + ", ".join(unknown)
                         + ", which the trip folds no stage for; name the column in TRIP_COLUMN_STAGES")
    return tuple((s["name"].lower(), s["name"], TRIP_COLUMN_STAGES[s["name"]])
                 for s in columns if TRIP_COLUMN_STAGES[s["name"]])


# The record's own stage keys with their captions, in the order `trip_spans` names them. `TRIP_STAGES` is the flat tuple
# of stage keys the fold reads; `TRIP_STAGES_LABELLED` is the `(key, caption)` pairs. Only this module imports either; the
# payload's `record_stages` list keeps the pair shape, while `stages` stays the board-column rows of the older wire.
TRIP_STAGES_LABELLED = (("queue", "Queued before the run"), ("wait", "Wait for a dispatch"), ("coding", "Coding"),
                        ("between", "Between two children"), ("review", "Review"), ("gates", "Gates in the run"),
                        ("landing", "Landing"), ("after", "After the verdict"))
GATING_GATE = "gate"   # the wave's gate, read from the gate history: Gating is the tier after the landing (#2199)
GATE_HISTORY_STAGES = ((GATING_GATE, "Gates"),)   # the stages the gate history adds; a record never holds one
TRIP_STAGES = tuple(key for key, _ in TRIP_STAGES_LABELLED)
TRIP_STAGE_LABELS = dict(TRIP_STAGES_LABELLED + GATE_HISTORY_STAGES)
TRIP_FOLD_KEYS = TRIP_STAGES + tuple(key for key, _ in GATE_HISTORY_STAGES)   # every stage the fold reads
TRIP_MIN_DELIVERIES = 3   # fewer than three deliveries say so and draw no bar: a median of two is no profile
TRIP_LABEL = "The trip of an issue"
_START = frozenset(("run_start", "resume"))   # an event that opens an invocation: the fold reads the last one only


def percentile(values, p):
    """The p-th percentile of `values`, by linear interpolation on the `(n - 1)` weight; None on no value. The rule is
    named here and in `docs/loop-sequence.md`, so a bar and the trip table are compared on one definition."""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * p / 100.0
    lo, hi = int(math.floor(rank)), int(math.ceil(rank))
    return xs[lo] + (xs[hi] - xs[lo]) * (rank - lo)


def _ts(e):
    """The event's time, or None when it carries none: a missing time is no zero."""
    t = e.get("ts")
    return t if isinstance(t, (int, float)) else None


def _invocation(events):
    """The last invocation's events: a resumed run's earlier attempt is no part of the trip it ended on."""
    idx = max((i for i, e in enumerate(events) if e.get("event") in _START), default=-1)
    return events[idx:] if idx >= 0 else []


def trip_spans(events, card=None):
    """The stages of one run record in seconds, or None when the record is no delivery.

    - `wait`: the run's start to its first call, the wait for a slot the record holds (`ceiling_wait` moves the start of
      that call, so no frontier beat before the run exists is read here).
    - `coding`: each spawned child's own span, its first call to its last answer. A failed entry that is retried lies
      inside that span, so a retry is coding time, never a gap.
    - `review`: each review round's wait, from the last child answer to the round.
    - `gates`: each gate's start to its result, as the record holds both.
    - `landing`: the last child answer or review to the merge.
    - `between`: the uncovered time between two child spans, the wait a wave entry or a gate slot takes inside the run.
    - `queue`: the card's Todo move to the run's start, when `card` names that move.
    - `after`: the verdict to the card's Closed move, when `card` names that move.
    `card` is `{"todo": epoch, "closed": epoch}`, read from the board's transitions by the caller. A wait the caller
    measures is 0 when it did not happen, never a missing stage; `queue` and `after` are None when no card is read, and
    `missing` names each card move the caller did not read, `todo` or `closed`.
    A stage the record does not hold is None, never a zero. `end` is the verdict time; a run with no passed land or no
    verdict is no delivery.
    """
    evs = _invocation(events)
    verdict = next((e for e in reversed(evs) if e.get("event") == "verdict" and _ts(e) is not None), None)
    land = next((e for e in reversed(evs) if e.get("event") == "land" and e.get("outcome") == "pass" and _ts(e) is not None), None)
    if verdict is None or land is None or not evs:
        return None
    start = _ts(evs[0])
    kids = {e.get("child") for e in evs if e.get("event") == "spawn_accepted" and e.get("child")}
    calls = [t for t in (_ts(e) for e in evs if e.get("event") == "call_start") if t is not None]
    coding, review, gates = None, None, None
    starts, answers, gstart, first_call, last_answer = {}, [], {}, {}, {}
    for e in evs:
        kind, t = e.get("event"), _ts(e)
        if kind == "call_start" and t is not None:
            node = e.get("node")
            starts[(node, e.get("entry"))] = t
            if node in kids: first_call.setdefault(node, t)
        elif kind == "outcome" and e.get("node") in kids and t is not None:
            last_answer[e["node"]] = t
            answers.append(t)
        elif kind == "review_round" and t is not None:
            if answers and t >= answers[-1]:
                review = (review or 0.0) + (t - answers[-1])
            answers.append(t)
        elif kind == "gate_start" and t is not None:
            gstart[(e.get("node"), e.get("entry"), e.get("item"))] = t
        elif kind == "gate_result" and t is not None:
            s = gstart.get((e.get("node"), e.get("entry"), e.get("item")))
            if s is not None and t >= s:
                gates = (gates or 0.0) + (t - s)
    spans = []
    for node, first in first_call.items():
        last = last_answer.get(node)
        if last is not None and last >= first:
            coding = (coding or 0.0) + (last - first)
            spans.append((first, last))
    between, covered = 0.0, None
    for first, last in sorted(spans):
        if covered is not None and first > covered:
            between += first - covered
        covered = last if covered is None else max(covered, last)
    card = card or {}
    todo, closed = card.get("todo"), card.get("closed")
    end = _ts(verdict)
    before = [t for t in answers if t <= _ts(land)]
    return {"queue": start - todo if todo is not None and start is not None and start >= todo else None,
            "wait": calls[0] - start if start is not None and calls and calls[0] >= start else None,
            "coding": coding, "between": between, "review": review, "gates": gates,
            "landing": _ts(land) - max(before) if before else None,
            "after": closed - end if closed is not None and end is not None and closed >= end else None,
            "missing": [name for name, moved in (("todo", todo), ("closed", closed)) if moved is None],
            "end": end}


def gate_span(gate, since):
    """The seconds one gate-history row ran, or None when the row is malformed or finished before the window."""
    try:
        start, finish = float(gate["start"]), float(gate["finish"])
    except (KeyError, TypeError, ValueError):
        return None
    if finish < since or finish < start:
        return None
    return finish - start


def _spread(values):
    """One stage's readings: its min, median, p85 and max, and its count (#2280). No value makes every reading None and
    the count zero, so a column the window never observed prints a dash and never a zero."""
    if not values:
        return {"min_s": None, "median_s": None, "p85_s": None, "max_s": None, "observations": 0}
    return {"min_s": min(values), "median_s": percentile(values, 50), "p85_s": percentile(values, 85),
            "max_s": max(values), "observations": len(values)}


def _rounded(v):
    """One reading as the wire prints it, and None where there is none."""
    return round(v, 3) if v is not None else None


def _row_stage(keys, readings):
    """The stage whose median a column answers: the larger median, and a known reading over an unknown one, because zero
    seconds is a reading and None is none. Equal medians keep the column's own order (`queue` before `wait`, `coding`
    before `between`), never a dict's order."""
    return max(keys, key=lambda k: (readings[k]["median_s"] is not None,
                                    readings[k]["median_s"] if readings[k]["median_s"] is not None else 0.0))


def column_parallelism(runs):
    """The mean count of issues standing in one column at the same time, over the time at least one stands: the sum of
    the runs' lengths over the length of their union (#2256). Two issues that share one span read 2.0; two that follow
    each other read 1.0. None on no run, and never below 1, because a run is one issue standing."""
    iv = sorted((r.get("start"), r.get("end")) for r in runs or ()
                if _num(r.get("start")) and _num(r.get("end")) and r["end"] > r["start"])
    if not iv:
        return None
    total = sum(b - a for a, b in iv)
    union, cur_a, cur_b = 0.0, iv[0][0], iv[0][1]
    for a, b in iv[1:]:
        if a > cur_b:
            union += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    union += cur_b - cur_a
    return total / union if union > 0 else None


# What a column consumes (#2227): three lines beside the bar, Tokens, CPU and RAM. A sample goes to the column the card
# stood in at the sample's time, from the card's column runs: a run covers `[start, end)`, and the sample's position is
# `(t - start) / (end - start)`. Each column is cut into TRIP_SERIES_POINTS bins, and a bin's point is the mean of what it
# holds, so two deliveries of different lengths fold by position. Tokens are the record's call outcomes and the review
# token ledger, counted at the call's end time. CPU and RAM are the minute record's readings of the WHOLE machine, so
# they say what the machine did while the card waited, never the cost of one issue alone. A line with no sample in a
# column holds no point and no mean, never a zero; a column with no model call spends a true zero, and its
# `tokens_per_issue` says so.
TRIP_SERIES_POINTS = 8
TRIP_LINES = ("tokens", "cpu", "ram")
# A load average is the runnable work on a machine of `cores` cores. A reading over ten times the core count is a burst
# of runnable threads, not a reading of saturation, so the fold reports it as invalid and never as the peak (#2253).
LOAD_SANE_PER_CORE = 10.0


def trip_calls(events):
    """Each model call of the last invocation as `{at, tokens}`, `at` the call's END: an outcome's own time, never its
    `call_start`, so a call that began in one column counts in the one it ended in. `tokens` is the outcome's input and
    output plus its cache reads; a field the record does not hold adds nothing. An outcome with no time is no sample.
    """
    out = []
    for e in _invocation(events):
        if e.get("event") != "outcome" or _ts(e) is None:
            continue
        out.append({"at": _ts(e), "tokens": sum(v for v in (e.get("tokens_in"), e.get("tokens_out"), e.get("cache_read"))
                                                if _num(v))})
    return out


def _num(v):
    """A reading the fold can use: a finite number, never a bool and never a string."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _run_at(runs, t, column=None):
    """`(column, bin)` of the first run that holds the time `t`, or None. A run covers `[start, end)`, so a sample on the
    boundary belongs to the run it opens. A run of no width holds no instant."""
    for r in runs or ():
        k = str(r.get("column") or "").lower()
        if column is not None and k != column:
            continue
        a, b = r.get("start"), r.get("end")
        if not (_num(a) and _num(b)) or b <= a or not (a <= t < b):
            continue
        p = min(1.0, max(0.0, (t - a) / (b - a)))
        return k, min(int(p * TRIP_SERIES_POINTS), TRIP_SERIES_POINTS - 1)
    return None


def _series(bins, mean):
    """One line of a column: a point at the centre of each bin that holds a sample, and one mean over the column."""
    pts = [[round((j + 0.5) / TRIP_SERIES_POINTS, 4), round(v, 4)] for j, v in enumerate(bins) if v is not None]
    return {"points": pts, "mean": round(mean, 4) if mean is not None else None}


def _tokens_top(v):
    """The Tokens lane's top tick: the first round 1, 2, 2.5 or 5 times a power of ten above `v`, so the lane's top
    stands above every point and a point is never clamped to the edge (#2228)."""
    if not _num(v) or v <= 0:
        return 0.0
    step = 10.0 ** int(math.floor(math.log10(v)))
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if v < m * step:
            return round(m * step, 4)
    return round(10.0 * step, 4)


def trip_consumption(spans, since, cards=None, calls=None, review_calls=None, loads=None, now=None,
                     cores=None, mem_gb=None):
    """Each drawn column's Tokens, CPU and RAM line, and the machine facts the page prints beside them.

    `spans` is one `trip_spans` answer per run record; `cards[i]` is record i's column runs
    (`{"column", "start", "end"}`), `calls[i]` its `trip_calls` answer. A delivery is a record whose verdict is at or
    after `since`, the same rule the bar folds by, so the bar and the lines cover one set of deliveries. `review_calls`
    is the review token ledger as `{at, tokens}` rows, each placed on the narrowest Review run that holds its time, so
    one ledger line is never counted twice and two parallel reviews never lose the line to whichever run the fold met
    first. `loads` is one `load_row` answer per minute record line; None is a record the caller may not read, and then
    no minute is counted as silent.

    The Tokens line is tokens per minute of the column: a bin's tokens over the minutes the deliveries spend in that
    bin; its mean is the column's tokens over all its minutes. `tokens_per_issue` is the column's tokens over its
    visits, a visit being one delivery's run in the column, so a card that re-enters a column adds a visit and is never
    charged as one issue twice over; a column with no model call reads a true 0. CPU and RAM are shares from 0 to 1, the
    minute record's percent divided by 100. `peak_load` is the window's highest sane reading, `cores` the machine's
    core count, `mem_gb` its memory, and `no_record_minutes` the minutes of the window with no record. A reading over
    `load_bound` is a burst of runnable threads, not a saturation reading: the fold reports it in `invalid_loads` and
    never as the peak (#2253). `ram_minutes` counts the minutes that carry a memory value, so the page can name why a
    RAM cell reads a dash.
    """
    keys = [key for key, _, _ in trip_columns()]
    N = TRIP_SERIES_POINTS
    tok = {k: {"bin": [0.0] * N, "min": [0.0] * N, "n": [0] * N, "total": 0.0, "visits": 0} for k in keys}
    lines = {k: {name: ([[] for _ in range(N)], []) for name in ("cpu", "ram")} for k in keys}
    now = since if now is None else now
    live = []
    for i, s in enumerate(spans or ()):
        if s is None or not _num(s.get("end")) or s["end"] < since:
            continue
        runs = cards[i] if cards and i < len(cards) and cards[i] else ()
        live.append(runs)
        for r in runs:
            k = str(r.get("column") or "").lower()
            a, b = r.get("start"), r.get("end")
            if k not in tok or not (_num(a) and _num(b)) or b <= a:
                continue
            # One visit per run: a card back in the same column is a new visit, so its tokens divide by the runs that
            # spent them, never by the one issue that made them.
            tok[k]["visits"] += 1
            for j in range(N):
                tok[k]["min"][j] += (b - a) / N / 60.0
        for c in (calls[i] if calls and i < len(calls) and calls[i] else ()):
            t, n = c.get("at"), c.get("tokens")
            if not _num(t) or not _num(n):
                continue
            loc = _run_at(runs, t)
            if loc is None or loc[0] not in tok:
                continue
            k, j = loc
            tok[k]["bin"][j] += n
            tok[k]["n"][j] += 1
            tok[k]["total"] += n
    for c in review_calls or ():
        t, n = c.get("at"), c.get("tokens")
        if "review" not in tok or not (_num(t) and _num(n)):
            continue
        # The ledger names no delivery, so the line goes to the narrowest Review run that holds it: two parallel reviews
        # both hold the call's time, and the narrow one is the closest match. A tie keeps the earliest delivery met.
        best = None
        for runs in live:
            for r in runs or ():
                if str(r.get("column") or "").lower() != "review":
                    continue
                a, b = r.get("start"), r.get("end")
                if not (_num(a) and _num(b)) or b <= a or not (a <= t < b):
                    continue
                width = b - a
                if best is not None and width >= best[0]:
                    continue
                p = min(1.0, max(0.0, (t - a) / width))
                best = (width, min(int(p * N), N - 1))
        if best is None:
            continue
        j = best[1]
        tok["review"]["bin"][j] += n
        tok["review"]["n"][j] += 1
        tok["review"]["total"] += n
    for row in loads or ():
        t = row.get("at")
        if not _num(t) or not (since <= t <= now):
            continue
        for runs in live:
            loc = _run_at(runs, t)
            if loc is None:
                continue
            k, j = loc
            if k not in lines:
                continue
            for name in ("cpu", "ram"):
                v = row.get(name)
                if _num(v):
                    lines[k][name][0][j].append(v / 100.0)
                    lines[k][name][1].append(v / 100.0)
            break
    out = {}
    for k in keys:
        bins = [tok[k]["bin"][j] / tok[k]["min"][j] if tok[k]["n"][j] and tok[k]["min"][j] > 0 else None
                for j in range(N)]
        mins = sum(tok[k]["min"])
        # The mean is the column's rate: a delivery with no call reads no mean, and a call with no tokens reads a 0.
        mean = tok[k]["total"] / mins if mins > 0 and sum(tok[k]["n"]) else None
        line = {"tokens": _series(bins, mean),
                "tokens_per_issue": round(tok[k]["total"] / tok[k]["visits"], 4) if tok[k]["visits"] else 0.0}
        for name in ("cpu", "ram"):
            per_bin, every = lines[k][name]
            mean = sum(every) / len(every) if every else None
            line[name] = _series([sum(v) / len(v) if v else None for v in per_bin], mean)
        out[k] = line
    cores_n = max(int(cores or 1), 1)
    bound = LOAD_SANE_PER_CORE * cores_n
    readings = [row["load"] for row in loads or () if _num(row.get("at")) and _num(row.get("load"))
                and since <= row["at"] <= now]
    # A reading over the sane bound is a burst of runnable threads, never the machine's saturation: it is counted as
    # invalid and kept out of the peak, so the page never names an impossible number as the machine's load (#2253).
    sane = [v for v in readings if v <= bound]
    invalid = [v for v in readings if v > bound]
    covered = {row["at"] for row in loads or () if _num(row.get("at")) and _num(row.get("load"))
               and since <= row["at"] <= now}
    # The RAM line has a source only from the day the minute record gained its memory field (#2222): a row with no
    # value is no reading, and the page states why a column draws no RAM line where the window holds none.
    ram_minutes = None if loads is None else sum(
        1 for row in loads or () if _num(row.get("at")) and _num(row.get("ram")) and since <= row["at"] <= now)
    minutes = int((now - since) // LOAD_CADENCE_S) if _num(now) and now > since else 0
    # The page draws the three ticks of each lane (#2228), so the fold names them: the Tokens lane covers the highest
    # rate any column holds, and the Machine lane a fixed 0 to 100% of the machine. The top tick is a round number
    # strictly above every point, so no point sits on the lane's edge and none is clamped there.
    rates = [v for k in keys
             for _, v in ((out[k].get("tokens") or {}).get("points") or ())
             if _num(v)]
    top = _tokens_top(max(rates)) if rates else 0.0
    # A record the caller may not read is no reading at all: it asserts neither a peak nor a count of silent minutes,
    # never the whole window as a count it never made.
    return {"lines": out, "peak_load": max(sane) if sane else None,
            "cores": cores_n, "mem_gb": mem_gb, "load_bound": bound,
            "invalid_loads": len(invalid), "invalid_peak": max(invalid) if invalid else None,
            "no_record_minutes": None if loads is None else max(0, minutes - len(covered)),
            "ram_minutes": ram_minutes, "window_minutes": minutes,
            "tokens_ticks": [0, round(top / 2, 4), top], "machine_ticks": [0, 50, 100]}


def trip(spans, gates, since, cards=None, calls=None, review_calls=None, loads=None, now=None,
         cores=None, mem_gb=None):
    """The trip of one issue as one stacked bar of the board's columns: each column's median and p85 over the deliveries
    of a window, in the definition's order, `Todo`, `Doing`, `Review`, `Landing` and `Gating`. `Closed` draws no column
    (#2199), and its 10 s after the verdict stays under `record_stages`.

    `spans` is one `trip_spans` answer per run record, None for a record that is no delivery; `gates` is one dict per
    gate-history row of the window, `start` and `finish` in seconds. A delivery is a run whose verdict is at or after
    `since`; each gate row after `since` adds one observation to the wave's gate, which stands in Gating. The run's own
    `gates` stage stands in Doing, where the card is while it runs (#2199); the record's gate is never a Gating time.
    A column that holds two stages answers the larger of the two medians as its `median_s`, and its `min_s`, `p85_s` and
    `max_s` come from that same stage, never from the other one (#2280); the other stage stands beside it in `beside`,
    with its own caption and its own four readings. A stage with no reading leaves `beside` None, so a run that names no
    card reads no column the card alone fills. `sum_s` is the sum of the column's stage medians. `parallelism` is the
    mean count of issues standing in the column at once, over the card's own runs, and `effective_s` is the column's
    time per issue, `median_s`, over its parallelism (#2256); `share` is the effective time over the total, the width
    its segment takes in the bar. A row's
    `stage` is the stage its median belongs to. The payload also keeps `stages`: the drawn board-column rows, in board
    order, each
    with `column`, `label`, `observations`, `min_s`, `median_s`, `p85_s` and `max_s` of the column's larger stage, so a
    reader of the older wire still reads its shape. The record's own per-stage rows live under `record_stages`, one row
    per entry of `TRIP_STAGES_LABELLED` with its own four readings and share. Fewer than `TRIP_MIN_DELIVERIES`
    deliveries answer `bar` False, and the page says so and draws no bar. Each column also carries what it consumes (see
    `trip_consumption`), and the payload the machine's peak load, core count, memory and minutes with no record.
    """
    per = {key: [] for key in TRIP_FOLD_KEYS}
    deliveries = 0
    for s in spans:
        if s is None or s["end"] < since:
            continue
        deliveries += 1
        for key in TRIP_STAGES:
            if s.get(key) is not None:
                per[key].append(s[key])
    for gate in gates:
        d = gate_span(gate, since)
        if d is not None:
            per[GATING_GATE].append(d)
    readings = {key: _spread(v) for key, v in per.items()}
    columns = trip_columns()
    held = {col: [k for k in keys if readings[k]["median_s"] is not None] for col, _, keys in columns}
    sums = {col: sum(readings[k]["median_s"] for k in ks) for col, ks in held.items()}
    mains = {col: _row_stage(keys, readings) for col, _, keys in columns}
    # #2256: a column's width is its effective time per delivery, its time per issue divided by its parallelism. The
    # runs are the card's own column runs, the spans the chart already folds, so the fold adds no reader of GitHub. A
    # column the board recorded no run for, or one where no two issues overlap, runs one at a time.
    runs_of = {col: [] for col, _, _ in columns}
    for i, s in enumerate(spans):
        if s is None or s["end"] < since:
            continue
        for r in (cards[i] if cards and i < len(cards) and cards[i] else ()):
            k = str(r.get("column") or "").lower()
            if k in runs_of:
                runs_of[k].append(r)
    parallel = {col: max(1.0, p) if (p := column_parallelism(runs_of[col])) is not None else 1.0
                for col, _, _ in columns}
    effective = {col: readings[mains[col]]["median_s"] / parallel[col]
                 if readings[mains[col]]["median_s"] is not None else None
                 for col, _, _ in columns}
    effective_total = sum(v for v in effective.values() if v)
    consumed = trip_consumption(spans, since, cards=cards, calls=calls, review_calls=review_calls, loads=loads,
                                now=now, cores=cores, mem_gb=mem_gb)
    rows, stages = [], []
    for col, label, keys in columns:
        main = mains[col]
        r = readings[main]
        beside, others = None, [k for k in keys if k != main]
        # A stage the record never held is None, never a zero, so it stands nowhere: when the main reading is a known
        # zero, the unknown stage adds no second reading to the cell, and the column reads its zero alone.
        if others and readings[others[0]]["median_s"] is not None:
            o = readings[others[0]]
            beside = {"stage": others[0], "label": TRIP_STAGE_LABELS[others[0]], "observations": o["observations"],
                      "min_s": _rounded(o["min_s"]), "median_s": _rounded(o["median_s"]),
                      "p85_s": _rounded(o["p85_s"]), "max_s": _rounded(o["max_s"])}
        rows.append({"column": col, "label": label, "stage": main, "observations": r["observations"],
                     "min_s": _rounded(r["min_s"]), "median_s": _rounded(r["median_s"]),
                     "p85_s": _rounded(r["p85_s"]), "max_s": _rounded(r["max_s"]),
                     "sum_s": round(sums[col], 3) if held[col] else None,
                     "parallelism": round(parallel[col], 3) if effective[col] is not None else None,
                     "effective_s": round(effective[col], 3) if effective[col] is not None else None,
                     "share": round(effective[col] / effective_total, 4) if effective_total and effective[col] else 0.0,
                     "beside": beside, **consumed["lines"][col]})
        stages.append({"column": col, "label": label, "observations": r["observations"],
                       "min_s": _rounded(r["min_s"]), "median_s": _rounded(r["median_s"]),
                       "p85_s": _rounded(r["p85_s"]), "max_s": _rounded(r["max_s"])})
    record_stages, stage_total = [], sum(m for m in (readings[k]["median_s"] for k in TRIP_STAGES) if m)
    for key, label in TRIP_STAGES_LABELLED:
        r = readings[key]
        record_stages.append({"stage": key, "label": label, "observations": r["observations"],
                              "min_s": _rounded(r["min_s"]), "median_s": _rounded(r["median_s"]),
                              "p85_s": _rounded(r["p85_s"]), "max_s": _rounded(r["max_s"]),
                              "share": round(r["median_s"] / stage_total, 4)
                              if stage_total and r["median_s"] is not None else 0.0})
    return {"deliveries": deliveries, "bar": deliveries >= TRIP_MIN_DELIVERIES, "columns": rows, "stages": stages,
            "record_stages": record_stages, "label": TRIP_LABEL,
            "peak_load": consumed["peak_load"], "cores": consumed["cores"], "mem_gb": consumed["mem_gb"],
            "load_bound": consumed["load_bound"], "invalid_loads": consumed["invalid_loads"],
            "invalid_peak": consumed["invalid_peak"], "ram_minutes": consumed["ram_minutes"],
            "window_minutes": consumed["window_minutes"], "no_record_minutes": consumed["no_record_minutes"],
            "tokens_ticks": consumed["tokens_ticks"], "machine_ticks": consumed["machine_ticks"]}


# The loop state strip (#1765): one strip across the top of the Loop tab, from the two records the loop already writes. The
# switch audit (`loop-audit.jsonl`) gives active, draining and off: each line is one change, with its writer, origin and the
# issue a loop drain names. The dated step records (`steps.tsv`, #1750) give the heartbeat: a stretch where the switch says
# on and no step started for longer than DOWN_GAP_S is `down`, never active. A window that starts between two audit lines
# takes the state the earlier line left. A stretch with no source data is `gap`, with its reason, never a state.
# The fold is pure: it reads the lists a caller folded from the files, never a file, a clock or GitHub.
LOOP_STATES = ("active", "draining", "off", "down")
LOOP_STATE_LABELS = {"active": "Active", "draining": "Draining", "off": "Off", "down": "Down"}
GAP = "gap"
STATE_OF_ACTION = {"start": "active", "drain": "draining", "stop-now": "off"}
_SWITCH_ON = frozenset(("active", "draining"))   # the two audit states that still say the switch is on
STRIP_WINDOWS = (4 * 3600, 8 * 3600, 24 * 3600)
STRIP_WINDOW_LABELS = {STRIP_WINDOWS[0]: "4 hours", STRIP_WINDOWS[1]: "8 hours", STRIP_WINDOWS[2]: "24 hours"}
STRIP_WINDOW_DEFAULT = STRIP_WINDOWS[2]
# The slow cycle starts a step every cycle and the fast lane every SG_FAST_INTERVAL (120 s by default), so ten silent
# minutes with the switch on is far past any pause the loop takes by itself: it is a loop that died, not a quiet one.
DOWN_GAP_S = 600


def strip_window(w):
    """The window a page asked for, in seconds: one of STRIP_WINDOWS, else the 24 hour default."""
    try:
        w = int(w)
    except (TypeError, ValueError):
        return STRIP_WINDOW_DEFAULT
    return w if w in STRIP_WINDOWS else STRIP_WINDOW_DEFAULT


def _audit_state(e):
    return STATE_OF_ACTION.get(str(e.get("action") or ""))


def _row(a, b, state, reason=None):
    row = {"start": a, "end": b, "state": state}
    if reason is not None:
        row["reason"] = reason
    return row


def _active_rows(a, b, starts, down_gap, live="active"):
    """One stretch the switch says on, over the step starts: `live` while the loop steps, `down` past the bound, a gap
    where the records hold no step at all before the first one they do hold.

    "The switch says on" covers both `active` and `draining`: a drain that stops stepping is a loop that died, so its
    live state is `draining` and its silent tail is `down`, never draining to the end. `starts` is None when no step record
    exists, which is no source data and so a gap; an empty record is a steps source that holds no step, and a loop that
    never stepped under a switch that says on is down.

    Every row stays inside `[a, b)`: a step before the window only says the loop was live then, and the bound it sets is
    clamped to the stretch, so a long silence before the window is down from the window's own start, never a row that
    reaches back before it.
    """
    if starts is None:
        return [_row(a, b, GAP, "no step record")]
    if not starts:
        return [_row(a, b, "down")]
    history = [t for t in starts if t <= a]
    inside = [t for t in starts if a < t < b]
    out, cur = [], a
    if history:
        last = history[-1]
    else:
        if not inside:
            return [_row(a, b, GAP, "no step record in this stretch")]
        first = inside[0]
        if first - a > down_gap:
            out.append(_row(a, first, GAP, "no step record before the first one in this stretch"))
        else:
            out.append(_row(a, first, live))
        cur = last = first
        inside = inside[1:]
    for t in inside:
        live_to = min(last + down_gap, b)
        if t > live_to:
            if live_to > cur:
                out.append(_row(cur, live_to, live))
            out.append(_row(max(cur, live_to), t, "down"))
            cur = t
        last = max(last, t)
    live_to = min(last + down_gap, b)
    if live_to > cur:
        out.append(_row(cur, live_to, live))
    if b > live_to:
        out.append(_row(max(cur, live_to), b, "down"))
    return [r for r in out if r["end"] > r["start"]]


def _merged(rows):
    out = []
    for r in rows:
        if r["end"] <= r["start"]:
            continue
        if out and out[-1]["state"] == r["state"] and out[-1].get("reason") == r.get("reason"):
            out[-1]["end"] = r["end"]
        else:
            out.append(dict(r))
    return out


def strip(audit, steps, landings, promotions, window_s, now, down_gap=DOWN_GAP_S):
    """`(audit, steps, landings, promotions, window, now)` -> the strip's states, its four shares and its marks.

    `audit` is one dict per switch-audit line (`at` epoch, `action`, `who`, `from`, `issue`), `steps` one dict per folded
    step record (`start`, `end`, `name`, `state`) or None when no step record exists, `landings` one row per landing
    (`at`, `branch`, `sha`, `issue`) and `promotions` one mark per promotion (`at`, `branch`, `sha`). The rows cover the
    whole window, in order: a `gap` row
    names the source it lacks. `shares` is the seconds of each of the four states; `gap_s` is the part no source covers,
    so the shares and the gap add up to the window.
    """
    w = strip_window(window_s)
    start, end = now - w, now
    events = sorted((e for e in audit if _audit_state(e) and start <= e["at"] < end), key=lambda e: e["at"])
    before = [e for e in audit if _audit_state(e) and e["at"] <= start]
    rows, segs = [], []
    if before:
        anchor = max(before, key=lambda e: e["at"])
        segs.append((start, events[0]["at"] if events else end, _audit_state(anchor)))
    elif events:
        rows.append(_row(start, events[0]["at"], GAP, "no switch audit line at or before this window"))
    else:
        rows.append(_row(start, end, GAP, "no switch audit line at or before this window"))
    for i, e in enumerate(events):
        segs.append((e["at"], events[i + 1]["at"] if i + 1 < len(events) else end, _audit_state(e)))
    starts = None if steps is None else sorted(s["start"] for s in steps if isinstance(s.get("start"), (int, float)))
    for a, b, state in segs:
        if b <= a:
            continue
        if state in _SWITCH_ON:
            rows.extend(_active_rows(a, b, starts, down_gap, live=state))
        else:
            rows.append(_row(a, b, state))
    rows = _merged(rows)
    shares = {s: 0.0 for s in LOOP_STATES}
    gap_s = 0.0
    for r in rows:
        if r["state"] == GAP:
            gap_s += r["end"] - r["start"]
        else:
            shares[r["state"]] += r["end"] - r["start"]
    return {"window_s": w, "start": start, "end": end, "labels": dict(LOOP_STATE_LABELS),
            "windows": [{"s": s, "label": STRIP_WINDOW_LABELS[s]} for s in STRIP_WINDOWS],
            "rows": rows, "shares": {k: round(v, 3) for k, v in shares.items()}, "gap_s": round(gap_s, 3),
            "marks": _marks(events, landings, promotions, start, end)}


def strip_marks(audit, landings, promotions, start, end):
    """The marks of the strip, in time order: each switch change (writer, origin, issue), each landing, each promotion."""
    return _marks([e for e in audit if _audit_state(e) and start <= e["at"] < end], landings, promotions, start, end)


# The gates under the strip (#1767): one mark for each finished gate of the strip's window, folded from the gate-history
# lines `gate.sh` writes. The mark sits at the finish, on the strip's own axis, and carries the kind, the scope, the
# seconds, the reason of a fail or unproven outcome and the load at the start and at the finish. The fold is pure: it
# reads the lines a caller folded from the file, never a file, a clock or GitHub.
GATE_OUTCOMES = ("pass", "fail", "unproven")
GATE_HISTORY_FIELDS = ("finish", "kind", "result", "sha", "start", "selection", "scope", "outcome", "reason", "ran",
                       "not_run", "slots", "red", "caller", "load_start", "load_finish", "mode",
                       # (#1856) What tells a gate stage's queue time from its work: the tree the gate read, the seconds
                       # it waited for the lock and its slots, its wall time, the CPU its command spent, and whether a
                       # green verdict answered instead of a battery (ADR-0168).
                       "tree", "wait", "wall", "cpu", "reused",
                       # (#1927) Which proof produced the verdict (ci or local), and the CI run id a `ci` proof names.
                       "runner", "run_id")
# The entry mode (#1798): how the gate came to its verdict, the seventeenth field. A row from before the field ends with its
# load reading, a number or a dash, never one of these words, so the tail is counted by the word and an older row reads its
# own columns. A row that carries the queue fields (#1856) ends with its reuse word, five fields after its mode; a row that
# carries the runner fields (#1927) ends with its CI run id, two fields after its reuse word.
ENTRY_MODES = ("gated", "reused", "skipped")
NO_BATTERY_MODES = ("reused", "skipped")
REUSE_WORDS = ("yes", "no")
RUNNERS = ("ci", "local")


def gate_row(line):
    """One gate-history line as named fields, or None when it is too short to hold a finish and a start. A reason that
    holds a tab is rejoined, so the columns after it keep their place; an older row answers '' for its later fields. A row
    that ends with the queue fields keeps thirteen tail columns, a row whose last field is the entry mode keeps eight, and a
    row from before the mode keeps seven, and a row that carries the runner fields keeps fifteen."""
    parts = line.split("\t")
    if len(parts) < 5:
        return None
    # The reason is the ninth field and the only free text, so the words at the row's end say how many fields follow it:
    # the runner words first, then the reuse word, then the entry mode.
    if len(parts) >= len(GATE_HISTORY_FIELDS) and parts[-2] in RUNNERS and parts[-3] in REUSE_WORDS and parts[-8] in ENTRY_MODES:
        tail = len(GATE_HISTORY_FIELDS) - 9
    elif len(parts) >= 22 and parts[-1] in REUSE_WORDS and parts[-6] in ENTRY_MODES:
        tail = 13
    elif len(parts) >= 17 and parts[-1] in ENTRY_MODES:
        tail = 8
    else:
        tail = 7
    if len(parts) >= 9 + tail:
        parts = parts[:8] + ["\t".join(parts[8:-tail])] + parts[-tail:]
    parts = parts + [""] * (len(GATE_HISTORY_FIELDS) - len(parts))
    return dict(zip(GATE_HISTORY_FIELDS, parts))


def _load(v):
    """A load reading as a number, None for `-` and for a value the record cannot hold."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def gate_marks(lines, start, end):
    """The gate-history lines of `[start, end)` -> one mark per finished gate, in time order, and one count per outcome.

    A mark answers `at` (its finish), `kind`, `scope`, `seconds`, `outcome`, `reason` (the fail's or the unproven's,
    None on a pass) and the load at its start and its finish, None where the row has no reading. A line the fold cannot
    read -- a short line, a finish or a start that is no number, a finish before its start, an outcome the chart cannot
    place -- is no mark and no count: a point drawn on a guess is worse than no point.
    """
    marks = []
    counts = {o: 0 for o in GATE_OUTCOMES}
    for line in lines:
        row = gate_row(line)
        if row is None:
            continue
        try:
            finish, began = float(row["finish"]), float(row["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if finish < began or not start <= finish < end:
            continue
        outcome = row.get("outcome") or ""
        if outcome not in GATE_OUTCOMES:
            continue
        reason = (row.get("reason") or "").strip()
        marks.append({"at": finish, "kind": row.get("kind") or "", "scope": row.get("scope") or "",
                      "outcome": outcome, "seconds": round(finish - began, 3),
                      "reason": reason if outcome != "pass" and reason and reason != "-" else None,
                      "load_start": _load(row.get("load_start")), "load_finish": _load(row.get("load_finish"))})
        counts[outcome] += 1
    marks.sort(key=lambda m: m["at"])
    return {"marks": marks, "counts": counts}


def _marks(events, landings, promotions, start, end):
    """The marks of the already-filtered switch changes, the landings and the promotions of the window."""
    out = []
    for e in events:
        out.append({"at": e["at"], "kind": "switch", "action": e.get("action"), "state": _audit_state(e),
                    "who": e.get("who") or "unknown author", "from": e.get("from") or "unknown origin",
                    "issue": e.get("issue")})
    for l in landings:
        if start <= l["at"] < end:
            out.append({"at": l["at"], "kind": "landing", "branch": l.get("branch"), "sha": l.get("sha"),
                        "issue": l.get("issue")})
    for p in promotions:
        if start <= p["at"] < end:
            out.append({"at": p["at"], "kind": "promotion", "branch": p.get("branch"), "sha": p.get("sha")})
    out.sort(key=lambda m: m["at"])
    return out


# ---------------------------------------------------------------- the wall time of the steps and the gates (#1748)
# The Loop tab's wall-time view, folded from the dated step records (`steps.tsv`, #1750) and the gate history: each step of
# the slow and the fast lane is a row of count, total, median and p90 wall time, and a step still in flight is a running row
# with its age. The slow cycle's time is the gap between two starts of its heartbeat. Each gate row carries the mean load at
# its start and at its finish. The fold is pure: a caller hands it the steps and the history lines it read.
WALL_WINDOWS = (600, 3600, 86400)
WALL_WINDOW_LABELS = {600: "10 minutes", 3600: "1 hour", 86400: "24 hours"}
WALL_WINDOW_DEFAULT = 3600
WALL_LANES = ("slow", "fast")
FAST_LANE_STEPS = frozenset(("childcc", "autobug", "autofrontier"))   # the names fastloop.sh writes; the slow lane's are "*.sh"
CYCLE_STEP = "heartbeat.sh"   # the one step of the slow cycle that starts on every pass


def wall_window(w):
    """The window the page chose in seconds, or the hour when it chose none the page offers."""
    try:
        s = int(w)
    except (TypeError, ValueError):
        return WALL_WINDOW_DEFAULT
    return s if s in WALL_WINDOWS else WALL_WINDOW_DEFAULT


def _stats(secs):
    return {"count": len(secs), "total_s": round(sum(secs), 3), "median_s": round(percentile(secs, 50), 3),
            "p90_s": round(percentile(secs, 90), 3)}


def _mean(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def step_wall(steps, since, now):
    """The steps `steprec.fold` made -> the finished steps of the window per lane, sorted by total, and the steps still in
    flight with their age. A skipped step ran for no time and joins no row; a step that started before `since` is no row."""
    done, running = {}, []
    for s in steps:
        if s["start"] < since:
            continue
        lane = "fast" if s["name"] in FAST_LANE_STEPS else "slow"
        if s["state"] == "running":
            running.append({"lane": lane, "name": s["name"], "age_s": round(now - s["start"], 3)})
        elif s["state"] == "done" and s["end"] is not None:
            done.setdefault((lane, s["name"]), []).append(float(s["end"] - s["start"]))
    out = {lane: [] for lane in WALL_LANES}
    for (lane, name), secs in done.items():
        out[lane].append({"lane": lane, "name": name, **_stats(secs)})
    for lane in WALL_LANES:
        out[lane].sort(key=lambda r: r["total_s"], reverse=True)
    out["running"] = running
    return out


def cycle_time(steps, since):
    """The slow cycle's time: the median and p90 of the gaps between consecutive heartbeat starts from `since` on. One start
    gives no gap, and no gap is no cycle time."""
    starts = sorted(s["start"] for s in steps if s["name"] == CYCLE_STEP and s["start"] >= since)
    gaps = [float(b - a) for a, b in zip(starts, starts[1:])]
    if not gaps:
        return {"n": 0, "median_s": None, "p90_s": None}
    return {"n": len(gaps), "median_s": round(percentile(gaps, 50), 3), "p90_s": round(percentile(gaps, 90), 3)}


def gate_rows(lines):
    """The gate-history lines -> one dict per whole row with a finish, a start, a kind and an outcome the page can place.
    A line that is no whole row is no row, and a missing load is None."""
    out = []
    for line in lines:
        row = gate_row(line)
        if row is None:
            continue
        try:
            finish, start = float(row["finish"]), float(row["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if row.get("outcome") not in GATE_OUTCOMES:
            continue
        out.append({"start": start, "finish": finish, "kind": row.get("kind") or "", "outcome": row["outcome"],
                    "load_start": _load(row.get("load_start")), "load_finish": _load(row.get("load_finish"))})
    return out


def gate_wall(gates, since):
    """The gate runs of the window by kind and outcome, sorted by total, with the mean load at their start and their finish,
    each over the rows that hold a reading. A gate that finished before `since` is no row."""
    groups = {}
    for g in gates:
        if g["finish"] < since or g["finish"] < g["start"]:
            continue
        groups.setdefault((g["kind"], g["outcome"]), []).append(g)
    rows = []
    for (kind, outcome), gs in groups.items():
        rows.append({"kind": kind, "outcome": outcome, **_stats([g["finish"] - g["start"] for g in gs]),
                     "load_start": _mean([g["load_start"] for g in gs]), "load_finish": _mean([g["load_finish"] for g in gs])})
    rows.sort(key=lambda r: r["total_s"], reverse=True)
    return rows


def _seconds(v):
    """A whole-second field as a number, None for `-`, '' and for anything the record cannot hold as a run."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def gate_queue(lines, start, end):
    """The gate queue of the window `[start, end)`, by finish (#1856): the share of the gate stage that is lock wait and
    the number of distinct trees that paid a gate.

    A row answers from its own fields: `wait` is the seconds before the gate held the lock and its slots, `wall` its run
    time, `tree` the tree it read and `reused` whether a green verdict answered instead of a battery (ADR-0168). A row
    from before those fields names no wait and no tree; it is counted in `rows`, `wait_unknown` and `trees_unnamed`, and
    its wall is its finish minus its start, which is what the field holds. A window no gate finished in answers no share,
    never a zero: a saved gate is one more row, so it is counted too.
    """
    rows = wait_unknown = trees_unnamed = reused = 0
    wait_s = wall_s = 0.0
    trees = set()
    for line in lines:
        row = gate_row(line)
        if row is None:
            continue
        try:
            finish, began = float(row["finish"]), float(row["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if finish < began or not start <= finish < end:
            continue
        rows += 1
        wait = _seconds(row.get("wait"))
        if wait is None:
            wait_unknown += 1
        else:
            wait_s += wait
        wall = _seconds(row.get("wall"))
        wall_s += (finish - began) if wall is None else wall
        tree = (row.get("tree") or "").strip()
        if tree and tree != "-":
            trees.add(tree)
        else:
            trees_unnamed += 1
        if (row.get("reused") or "").strip() == "yes":
            reused += 1
    stage = wait_s + wall_s
    return {"rows": rows, "trees": len(trees), "trees_unnamed": trees_unnamed, "wait_unknown": wait_unknown,
            "wait_s": round(wait_s, 3), "wall_s": round(wall_s, 3), "stage_s": round(stage, 3),
            "wait_share": round(wait_s / stage, 4) if stage > 0 else None, "reused": reused}


def wall(steps, gate_lines, window_s, now):
    """The Loop tab's wall-time view of the window `window_s` that ends at `now`: the step rows of each lane, the running steps,
    the slow cycle's time, the gate rows and the gate queue, all from one `since`."""
    since = now - window_s
    return {"window_s": window_s, "since": since, "cycle": cycle_time(steps, since), **step_wall(steps, since, now),
            "gates": gate_wall(gate_rows(gate_lines), since), "queue": gate_queue(gate_lines, since, now)}


# ---------------------------------------------------------------- the machine load (#1768)
# Under the strip and on the strip's own window, the machine's load average as one line, from the minute record the hub's
# load monitor appends (time, load average, CPU share). Each record covers the minute that ends at its own time, so a
# stretch no record covers is a `gap` with its reason, never a zero: an idle machine and an unread minute are different
# facts. The core count is drawn as the reference line, because a load above it is more work than the machine has cores.
# The fold is pure: rows in, no file, no clock and no process table.
LOAD_LABEL = "Machine load"
LOAD_CADENCE_S = 60   # one record line a minute, the monitor's own cadence
LOAD_GAP_NO_RECORD = "no load record"
LOAD_GAP_BEFORE = "no load record before the first one in this stretch"


def load_row(raw):
    """One record line as the chart reads it: `{at, load, cpu, ram}`, where a field the writer could not read is None, and
    an old-shape line with no memory field reads `ram` as None. None for a line that names no time: a malformed line is
    not a point and draws no load of its own."""
    fields = str(raw).split("\t")
    try:
        at = float(fields[0])
    except (IndexError, TypeError, ValueError):
        return None

    def num(i):
        try:
            v = float(fields[i])
        except (IndexError, TypeError, ValueError):
            return None
        return v
    return {"at": at, "load": num(1), "cpu": num(2), "ram": num(3)}


def load_chart(rows, start, end, cores, cadence=LOAD_CADENCE_S):
    """`(rows, start, end, cores)` -> the machine load of the window: the line's `segments`, the `gaps` no minute record
    covers, the `peak` and the `cores` the page draws as its reference line.

    `rows` is one `load_row` answer per record line, or None when the source cannot be read. A row whose load is None is
    no point: its minute falls to a gap. A gap is a stretch no record covers: before the first record, between two
    records further apart than the cadence, and after the last record once the tail is longer than the cadence (the
    minute in progress is not a gap yet). A line covers the minute before its own time, so two records exactly one
    cadence apart leave no hole, and the minute between two records two cadences apart is a gap, never a zero.
    """
    start, end = float(start), float(end)
    cad = max(float(cadence), 1.0)
    pts = sorted((r for r in (rows or [])
                  if isinstance(r, dict) and isinstance(r.get("at"), (int, float)) and not isinstance(r.get("at"), bool)
                  and isinstance(r.get("load"), (int, float)) and not isinstance(r.get("load"), bool)
                  and start <= r["at"] <= end), key=lambda r: r["at"])
    segments, gaps = [], []
    if not pts:
        gaps = [{"start": start, "end": end, "reason": LOAD_GAP_NO_RECORD}]
    else:
        head = pts[0]["at"] - cad
        if head > start:
            gaps.append({"start": start, "end": min(head, end), "reason": LOAD_GAP_BEFORE})
        cur = [pts[0]]
        for prev, p in zip(pts, pts[1:], strict=False):
            if p["at"] - prev["at"] > cad:
                segments.append(cur)
                gaps.append({"start": prev["at"], "end": p["at"] - cad, "reason": LOAD_GAP_NO_RECORD})
                cur = [p]
            else:
                cur.append(p)
        segments.append(cur)
        if end - pts[-1]["at"] > cad:
            gaps.append({"start": pts[-1]["at"], "end": end, "reason": LOAD_GAP_NO_RECORD})
    return {"label": LOAD_LABEL, "start": start, "end": end, "window_s": max(0.0, end - start),
            "cores": max(int(cores or 1), 1), "cadence": cad, "segments": segments, "gaps": gaps,
            "peak": max((p["load"] for p in pts), default=0.0),
            "gap_s": round(sum(max(0.0, g["end"] - g["start"]) for g in gaps), 3)}


# The run slots in use (#1766): the step line of the Loop tab, on the strip's own time axis and window. A run
# holds a slot from its start to its verdict; a verdict before a later start is history, so a resumed run is a second
# invocation (ADR-0167). The cap is a step too: each `run_start` records the caps in force at its own start, so a change
# shows at the time a record names it, and the cap in force now heads a window no earlier record speaks for. A file's write
# time is no cap change. The line draws the raw slots in use, so a run queued at the cap stays visible, and the cap is
# drawn apart as its own step line. A stretch no readable record covers is a gap with its reason, never a line of zeros
# that claims a silent machine; a gap runs to the window end only where no readable record covers that tail. The fold is
# pure: it reads the lists a caller folded from the files.
SLOTS_LABEL = "Run slots in use"


def _cap_marks(caps, cap, start, end):
    """The cap as one row per value in force, covering `[start, end)`, half-open as the rows are.

    The head is the last mark at or before the start, else `cap`, the value in force now; with neither, no row is drawn
    before the first in-window mark. Each in-window mark starts its own row at its timestamp.
    """
    marks = sorted(((m["at"], m["runs"]) for m in (caps or ())
                    if isinstance(m, dict) and isinstance(m.get("at"), (int, float)) and not isinstance(m.get("at"), bool)
                    and isinstance(m.get("runs"), int) and not isinstance(m.get("runs"), bool)),
                   key=lambda m: m[0])
    before = [v for at, v in marks if at <= start]
    head = before[-1] if before else (cap if isinstance(cap, int) and not isinstance(cap, bool) else None)
    cur, cur_at, rows = head, start, []
    for at, v in marks:
        if not (start < at < end): continue
        if cur is None:
            cur, cur_at = v, at
        elif v != cur:
            if at > cur_at: rows.append({"start": cur_at, "end": at, "cap": cur})
            cur, cur_at = v, at
    if cur is not None and end > cur_at: rows.append({"start": cur_at, "end": end, "cap": cur})
    return rows


def _gap_pieces(a, end, live):
    """The stretch `[a, end)` the readable invocations do not cover: a gap is only where no readable record speaks, so a
    readable run's own stretch is never hidden under one."""
    pieces = [(a, end)]
    for x, y in live:
        out = []
        for p, q in pieces:
            if y <= p or x >= q: out.append((p, q)); continue
            if x > p: out.append((p, x))
            if y < q: out.append((y, q))
        pieces = out
    return [(p, q) for p, q in pieces if q > p]


def run_slots(invocations, caps, window_s, now, cap=None, unreadable=()):
    """`(invocations, caps, window, now)` -> the step line of the run slots in use, its ceiling and its gaps.

    `invocations` holds one dict per invocation (`start`, `end`; `end` None while the run is live), or None when no run
    record exists at all: that is a gap over the whole window, never a line that says the machine was idle. `caps` holds
    one dict per cap observation (`at`, `runs`), and `cap` the run cap in force now, which anchors the ceiling where no
    mark speaks at all. `unreadable` holds one dict per run record the caller could not read (`start` of the stretch it covers, or
    None when unknown; `reason`): its stretch is a gap too, because a record that cannot be read is no evidence of an idle
    slot, and the rest of the window keeps its line. A gap stops where readable evidence starts again, and runs to the
    window's end only where no readable record covers that tail. The rows hold the raw slots in use, never a count
    lowered to the cap, so a run queued at the cap stays visible; `ceilings` is the cap as its own step line, and `top`
    is the scale the page draws, which holds the highest ceiling and the peak.
    """
    w = strip_window(window_s)
    start, end = now - w, now
    live = []
    for x in invocations or ():
        if not isinstance(x, dict): continue
        a, b = x.get("start"), x.get("end")
        if not isinstance(a, (int, float)) or isinstance(a, bool): continue
        a = max(a, start)
        b = end if not isinstance(b, (int, float)) else min(b, end)
        if b > a: live.append((a, b))
    live.sort()
    gaps = []
    if invocations is None:
        why = ["no run record"] + [str(u.get("reason") or "no readable record") for u in unreadable or () if isinstance(u, dict)]
        gaps.append({"start": start, "end": end, "reason": "; ".join(why)})
    else:
        for u in unreadable or ():
            if not isinstance(u, dict): continue
            a = u.get("start")
            a = max(a, start) if isinstance(a, (int, float)) and not isinstance(a, bool) else start
            for p, q in _gap_pieces(a, end, live):
                gaps.append({"start": p, "end": q, "reason": u.get("reason") or "no readable record"})
    gaps.sort(key=lambda g: g["start"])
    ceilings = _cap_marks(caps, cap, start, end)
    rows = []
    if invocations is not None:
        pts = {start, end}
        for a, b in live: pts.update((a, b))
        for r in ceilings: pts.update((r["start"], r["end"]))
        pts = sorted(p for p in pts if start <= p <= end)
        for a, b in zip(pts, pts[1:]):
            if b <= a: continue
            mid = (a + b) / 2.0
            n = sum(1 for x, y in live if x <= mid < y)
            if rows and rows[-1]["slots"] == n: rows[-1]["end"] = b
            else: rows.append({"start": a, "end": b, "slots": n})
    peak = max((r["slots"] for r in rows), default=0)
    vals = [peak] + [r["cap"] for r in ceilings]
    return {"window_s": w, "start": start, "end": end, "label": SLOTS_LABEL,
            "cap": ceilings[-1]["cap"] if ceilings else (cap if isinstance(cap, int) and not isinstance(cap, bool) else None),
            "rows": rows, "ceilings": ceilings, "gaps": gaps, "peak": peak, "top": max(max(vals), 1)}
