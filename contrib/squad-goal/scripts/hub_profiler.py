"""The hub Loop tab's rule layer (#1743, ADR-0137): process-table readings in, a hot-spot tree out. No I/O and no clock.

A reading is {(pid, start time): (ppid, cpu seconds, command)}: a pid alone does not name a process, the system gives it
again. `fold` turns two readings into what happened between them: the CPU seconds each process used and one event for each
process the second reading saw first.

A `Scope` names the loop of one profile: the supervisor's pid and the folders only that profile's work names. The loop is
the supervisor and its descendants, each process whose command names one of those folders, with its descendants, and each
orphan from a temp folder. Each is filed under the loop parts its ancestry names, outermost first, so the tree reads like
a call tree: a driver, the run it started, the gate below it. A process that names no part of its own counts as its
nearest named ancestor's own time. Every other process counts in one context row, apart from the tree.

A command is named by the program it runs, never by an argument: `vim watch.sh` runs vim. The whole line is read, with
no head cap: a script past a long argument list still names its process.

A process that started and ended between two readings is never seen, so each node's event count, and the starts a second
it answers, is a lower bound.
"""
import collections
import functools
import re
import shlex

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
FOREIGN = "another loop"         # a supervisor that is not this profile's, with its descendants
CONTEXT = "outside the loop"
_IN, _OUT, _OTHER_LOOP = "in", "out", "foreign"

Scope = collections.namedtuple("Scope", "supervisor marks")   # the supervisor's pid or None; the folders of the profile


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
    process table churns it: a reading parses each line once or twice."""
    return _run(_words(command), raw=command)


def label(command):
    """(name, kind) for the loop part a command runs, or None when its program is none: an argument never names it."""
    return _parse(command)[0]


def _orphan(command, marks):
    """The orphan's node for a parentless command whose script sits in a temp folder, or None. A script in a folder of
    the profile is no orphan, wherever that folder is."""
    script = _parse(command)[1]
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


def _paths(rows, scope):
    """A function pid -> the node path of that process in `rows` ({pid: (ppid, cpu, command)}), memoized over one reading."""
    memo = {}
    marks = tuple(scope.marks)
    held = label(rows[scope.supervisor][2]) if scope.supervisor in rows else None
    above = rows[scope.supervisor][0] if held and held[0] == SUPERVISOR else None   # the pid file names the loop the supervisor runs; the supervisor is its parent

    def place(pid):
        """(in the loop, in another loop, or outside; the named parts; the top ancestor's command)."""
        if pid in memo:
            return memo[pid]
        chain, base = [], None
        while pid in rows and len(chain) < 64:   # the cap ends a parent cycle a torn read can show
            chain.append(pid)
            pid = rows[pid][0]
            if pid <= 1 or pid == chain[-1]:   # pid 1 adopts every orphan: it is no ancestor that names a tree
                break
            if pid in memo:
                base = memo[pid]
                break
        for p in reversed(chain):
            ppid, _, command = rows[p]
            own = label(command)
            state, parts, top = base or (_OUT, (), command)
            if (p == scope.supervisor or p == above) and own and own[0] == SUPERVISOR:   # a stale pid file can name another program
                state, parts = _IN, (own,)
            elif state == _IN:
                if own and (not parts or parts[-1] != own):
                    parts = parts + (own,)
            elif state == _OUT:
                orphan = _orphan(command, marks) if ppid == 1 else None
                if orphan:
                    state, parts = _IN, (orphan,)
                elif own and own[0] == SUPERVISOR:   # the same scripts run another profile's loop: its pid tells it apart
                    state = _OTHER_LOOP
                elif _names(command, marks):
                    state, parts = _IN, ((own,) if own else ())
            base = memo[p] = (state, parts, top)
        return base

    def path(pid):
        state, parts, top = place(pid)
        if state == _IN:
            return parts or (UNKNOWN,)
        return (OTHER, (FOREIGN if state == _OTHER_LOOP else _plain(top), "other"))
    return path


def fold(prev, cur, scope):
    """{node path: [cpu seconds, events]} for what happened between two readings.
    A (pid, start time) the first reading does not hold is one event, and all its CPU time is new. So is one whose CPU
    time went back, where the system gives no start time."""
    out = {}
    path = _paths({pid: row for (pid, _), row in cur.items()}, scope)
    for key, (_, cpu, _) in cur.items():
        was = prev.get(key)
        new = was is None or cpu < was[1]
        used = cpu if new else cpu - was[1]
        if not new and used <= 0:
            continue
        cell = out.setdefault(path(key[0]), [0.0, 0])
        cell[0] += used
        cell[1] += 1 if new else 0
    return out


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
    """The one context row of a fold: the CPU seconds of every process outside the loop, by application, largest first."""
    apps = {}
    for parts, (cpu, events) in acc.items():
        if parts[0] == OTHER:
            cell = apps.setdefault(parts[1][0], [0.0, 0])
            cell[0] += cpu
            cell[1] += events
    rows = sorted(apps.items(), key=lambda kv: (-kv[1][0], kv[0]))
    return {"name": CONTEXT, "total_s": round(sum(c for c, _ in apps.values()), 3), "events": sum(e for _, e in apps.values()),
            "children": [{"name": name, "total_s": round(cpu, 3), "events": events} for name, (cpu, events) in rows]}


class Window:
    """The readings of one switch-on, folded as they come, bounded to the last `span` seconds.
    It keeps one small delta per reading and never a reading older than the last, so its size follows the span."""

    def __init__(self, span=SPAN_S):
        self.span, self.since, self.prev = span, None, None
        self.steps = collections.deque()   # (time, delta) per reading, oldest first
        self.acc = {}
        self.last, self.interval, self.failed = None, None, 0

    def reset(self, now):
        self.since, self.prev, self.acc = now, None, {}
        self.last, self.interval, self.failed = None, None, 0
        self.steps.clear()

    def fail(self):
        """Count a reading that failed. It is not folded: the next good one is measured from the last good one."""
        self.failed += 1

    def stats(self):
        """The time of the last good reading, the measured interval between the last two, and the failed readings."""
        return {"last_reading": self.last, "interval_s": self.interval, "failed_readings": self.failed}

    def add(self, now, reading, scope):
        """Take one reading. The first is the baseline: it holds every process and says nothing about the window."""
        if self.since is None:
            self.since = now
        if self.last is not None:
            self.interval = round(now - self.last, 3)
        self.last = now
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

    def window_s(self, now):
        return 0 if self.since is None or now is None else max(0.0, min(now - self.since, self.span))

    def tree(self, root="loop", now=None):
        """The tree of the window, its starts a second measured over the window's own end: the last good reading."""
        return tree(self.acc, root, self.window_s(self.last if now is None else now))

    def context(self):
        return context(self.acc)
