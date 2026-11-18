"""A command run in a workspace: one policy for the gate and the landing check.

The environment has no virtualenv. The cap is `TIMEOUT_S`, moved by `SG_AC_TIMEOUT`. A failure keeps the output
tail as evidence. The gate (`ac-verify`) and the landing check (`Dispatch.ac_survey`) both call `run`.

The default clears the check the instructions name (#1785): the fork's own `contrib/squad-goal/scripts/shell-suites.sh`
runs the 41 `test-*.sh` suites next to it and took 158.69 s real on an idle machine, so the old 120 s cap killed it,
recorded `ac_failed:timeout after 120 s` and failed a run whose code was green. A run that needs more room sets
`SG_AC_TIMEOUT`; a command is still asked to be the narrowest one that proves the done condition, so the common case
stays fast (a wide command is a choice, never the default).

A command takes `SG_CHILD_TEST_WORKERS` (default 2) `gates` slots of the machine-wide budget before it starts (squad/caps.py,
#608), in one locked step and never part of them while it waits, and runs with the count it took in `SQUAD_GATE_WORKERS` and
`PYTEST_XDIST_AUTO_NUM_WORKERS` (#734), so a run's
children never start one pytest worker per core each. A full budget makes it wait and say so on stderr, never fail. A wait past
`SG_CHILD_SLOT_WAIT` (default 1800 s) runs it with 1 worker and says so. Inside a gate holder it takes none, as gate.sh does.

Guards: `tests/test_workcmd.py`.
"""
import contextlib
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass

TIMEOUT_S = 300           # the default cap for one acceptance command, in seconds (>= 300: see the module docstring)
EVIDENCE_TAIL = 1500      # the most characters of a failed command's output kept as evidence

# A line that explains a failure, even when it sits above the kept tail. `pytest -q` prints the failure block first and
# the slowest-durations block last, so a bare tail records the standings and drops the assertion (ADR-0100 rule 10).
EVIDENCE_SIGNAL = re.compile(r"^(_{5,}|E   |FAILED|ERROR)")
NAME_SIGNAL = re.compile(r"^(FAILED|ERROR)\b")   # the summary lines that name the failing tests: kept before the detail
MORE_ELIDED = "[... more failing lines elided]"

# The share of a suite that passes is a fact of the output, and one bit is not progress: 16 of 24 reads as work done
# while the gate is red (#80). pytest prints one summary line, whatever the spelling -- `1 failed, 24 passed in 3.1s`,
# `2100 passed, 21 skipped in 103.09s (0:01:43)`, `= 4 passed in 2.20s =`. The counts are parsed from the LAST such line
# the output carries (a wrapper that runs pytest twice ends in the state it ended in) and reported beside the exit code,
# never instead of it: the exit code stays the only pass/fail rule (ADR-0019). `no tests ran` is not a share of
# anything, so it reports nothing; a command that never ran pytest reports nothing either, and no path reports a zero
# for it -- a drawn 0% would read as "nothing passes".
SUMMARY = re.compile(r"\bin [0-9.]+s(?:\s*\([0-9:]+\))?\s*=?\s*$")
COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed|deselected|warning|warnings)\b")
COUNT_KEYS = ("passed", "failed", "errors", "skipped", "xfailed", "xpassed", "deselected")


# #862: a command that reads git history reads the tree it was written in, not the target's merge. The list is closed, and
# this is the one place it lives. `git` must start a command (after a `(`, `;`, `&`, `|`, a quote, or `then`/`do`): `echo git log` is text. A `diff` reads history only when it names a revision (a range, HEAD, or a sha).
HISTORY_VERBS = ("log", "show", "rev-list", "rev-parse", "blame", "cherry", "merge-base", "describe", "ls-tree", "cat-file",
                 "for-each-ref", "name-rev", "shortlog", "reflog")
HISTORY = re.compile(
    r"(?:^|[(;&|`\"'{]|\bthen|\bdo)\s*git\s+(?:-\S+\s+(?:\S+\s+)?)*"
    r"(?:(?:" + "|".join(HISTORY_VERBS) + r")\b|diff\b[^;&|]*(?:\.\.|\bHEAD\b|\b[0-9a-f]{7,40}\b))")


def reads_history(cmd):
    """True when the command reads git history, so it runs in the tree its child wrote (#862), never in the target."""
    return HISTORY.search(cmd) is not None


def counts_of(output):
    """The counts of the last pytest summary line in `output`, or None when the output carries none.

    `passed`, `failed` and `errors` are the tests that ran to a verdict, and their sum is `total`: that is the share
    the panel draws. `skipped`, `deselected`, `xfailed`, `xpassed` and `warnings` are reported as they stand and never
    enter `total` -- a deselected test was not run, and counting it would move the percentage up by doing less.
    """
    text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output
    line = None
    for ln in (text or "").splitlines():
        if SUMMARY.search(ln.rstrip()) and ("passed" in ln or "failed" in ln or "error" in ln or "no tests ran" in ln):
            line = ln
    if line is None:
        return None
    got = {}
    for n, what in COUNT.findall(line):
        key = "errors" if what in ("error", "errors") else ("warnings" if what == "warning" else what)
        if key in COUNT_KEYS:
            got[key] = got.get(key, 0) + int(n)
    total = sum(got.get(k, 0) for k in ("passed", "failed", "errors"))
    if total == 0:
        return None      # `no tests ran`, or a line whose only counts are skips: nothing was measured, so nothing is claimed
    return {**got, "total": total}


def evidence_of(output):
    """The evidence kept from the output of a failed command: its failing lines, then the end of the output.

    ADR-0100 rule 10. An output that fits the budget is kept whole. A longer one keeps the lines that explain a
    failure (`EVIDENCE_SIGNAL`) found in the part being dropped, then the last characters of the output, all inside
    `EVIDENCE_TAIL`; the size of the dropped part is named, so a reader never mistakes the record for the whole log.
    An output with no failing line keeps its plain tail.
    """
    text = output.decode("utf-8", "replace").strip() if isinstance(output, bytes) else output.strip()
    if len(text) <= EVIDENCE_TAIL:
        return text
    head, tail = text[: -EVIDENCE_TAIL], text[-EVIDENCE_TAIL:]
    signals = sorted((line for line in head.splitlines() if EVIDENCE_SIGNAL.match(line)), key=lambda line: not NAME_SIGNAL.match(line))
    if not signals:
        return tail
    note = f"[{len(head)} characters elided; the failing lines are kept]"
    room = EVIDENCE_TAIL - len(note)
    block = "\n".join(signals)
    if len(block) > room // 2:                     # the first failing lines are the ones that explain the failure
        cut = max(0, room // 2 - len(MORE_ELIDED) - 1)
        block = block[:cut].rsplit("\n", 1)[0] + "\n" + MORE_ELIDED
    kept = [note, block]
    end = EVIDENCE_TAIL - sum(len(p) for p in kept) - len(kept)
    return "\n".join(kept + [text[-end:]])


def ac_timeout(environ=None):
    """The cap for one acceptance command: `SG_AC_TIMEOUT`, else the default.

    Anything that is not a positive number leaves the default in place: a cap of `0` or a stray word would kill
    every command, which reads as a failed child rather than as a mistyped knob. The caller reports the value it
    actually used, so the record names the real cap.
    """
    env = os.environ if environ is None else environ
    try:
        seconds = float(env.get("SG_AC_TIMEOUT", ""))
    except (TypeError, ValueError):
        return TIMEOUT_S
    return seconds if seconds > 0 else TIMEOUT_S


def clean_env(environ):
    """A copy of the environment without a virtualenv.

    A command that calls `python3` must reach the python3 of the project, not the virtualenv of the orchestrator.
    `uv run` puts its own venv first on PATH and sets VIRTUAL_ENV. The tools of the target project are elsewhere.
    Seen in the first live subject run (2026-09-30). Seen again under `uv run pytest`:
    `python3 -m pytest` inside a gate found no pytest.
    """
    env = dict(environ)
    venv = env.pop("VIRTUAL_ENV", None)
    if venv and "PATH" in env:
        env["PATH"] = ":".join(p for p in env["PATH"].split(":") if not p.startswith(venv))
    return env


def _num(env, var, default):
    try:
        v = float(env.get(var, ""))
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def child_workers(environ=None):
    """The pytest worker count of one child: `SG_CHILD_TEST_WORKERS`, else 2."""
    return int(_num(os.environ if environ is None else environ, "SG_CHILD_TEST_WORKERS", 2))


def bounded_env(n):
    """The variables that bound a child's pytest to `n` workers: the battery reads one, pytest-xdist's `-n auto` the other."""
    return {"SQUAD_GATE_WORKERS": str(n), "PYTEST_XDIST_AUTO_NUM_WORKERS": str(n)}


@contextlib.contextmanager
def gate_slots(cwd):
    """Hold `gates` slots of the machine-wide budget while a command runs; yield (worker count, slots it holds)."""
    env = os.environ
    if "gate" in env.get("SG_SERIAL_HELD", "").split(":"):      # a gate holder took the slots already (gate.sh)
        yield int(_num(env, "SQUAD_GATE_WORKERS", child_workers(env))), 0
        return
    from squad import caps
    from squad.options import OptionError
    try:
        c = caps.ceiling(cwd)
    except OptionError as e:
        print(f"workcmd: no machine-wide budget ({e}); the command runs with {child_workers(env)} worker(s) and no slot", file=sys.stderr)
        yield child_workers(env), 0
        return
    n, holder = min(child_workers(env), c.caps["gates"]), f"child-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    bound, poll, t0, said = _num(env, "SG_CHILD_SLOT_WAIT", 1800.0), _num(env, "SQUAD_CAPS_POLL", 1.0), time.monotonic(), False
    while (leases := c.try_take_many("gates", holder, n)) is None:
        held = ", ".join(c.holders("gates")) or "?"
        if not said:
            print(f"workcmd: {holder} is waiting for {n} slot(s): caps.gates is full ({c.caps['gates']} slots), held by {held}", file=sys.stderr)
            said = True
        if time.monotonic() - t0 >= bound:
            print(f"workcmd: {holder} waited {bound:g}s for caps.gates, held by {held}; the command runs with 1 worker and no slot", file=sys.stderr)
            yield 1, 0
            return
        time.sleep(poll)
    try:
        yield min(n, len(leases)), len(leases)       # the count it took: a short take runs with the slots it holds (#734)
    finally:
        c.release_many(leases)


SLOTS_HELD = "SG_GATE_SLOTS_HELD"      # the count of gates slots the run holds for the command; child-gate.sh takes none under it (ADR-0175)
CHILD_SUITE = re.compile(r"child-gate\.sh\s+['\"]?([^\s'\";&|()<>]+)")      # the suite a command starts through child-gate.sh


@dataclass
class CmdResult:
    ok: bool
    evidence: str = ""     # empty on success; the output tail on a failure; "timeout after N s" on a timeout
    malformed: bool = False     # the shell cannot parse the command (prose in it); the command never ran
    counts: dict | None = None    # the counts of the output's last pytest summary, or None: how much of the suite passed (#80)
    unproven: bool = False     # the command ended on the gate's unproven exit with no summary line: it measured nothing (ADR-0164)


# The exits of a command the shell could not start: 126 is "found, not executable" (a bad shebang interpreter too), 127 is
# "no such command". A path that names no program, such as an absolute home path in another machine's tree, lands here (#1803).
# The exit code alone does not tell: a program that started may exit 126 or 127 by design, and a compound command may end
# on 127 after a real check failed. So the shell's own start-failure text is read with it, beside what the command starts.
CANNOT_START = (126, 127)
# The exit the fast gate gives when it could not run a battery (`contrib/squad-goal/scripts/fast-gate.sh`: 0 pass, 1 fail,
# 2 and 4 unproven). The run's gate reads it with the evidence: a command that printed no summary and named no failing test
# measured nothing, so it is unproven; a command that measured something stays a measured failure (#1825, #2337).
GATE_UNPROVEN_EXIT = 4
# The gate's own mark: every exit-4 path of fast-gate.sh prints one line that starts `fast-gate:`. The exit code is not
# exclusive to the gate, because any command may exit 4 for its own reason, so only this line scopes the exit to the gate.
# A bare exit 4 with no such line measured nothing the gate can vouch for and stays a measured failure (#2337).
GATE_PROVENANCE = re.compile(r"^fast-gate:", re.MULTILINE)


def _named_a_failure(output):
    """True when the output names a failing test or an error, whatever its exit: a red a check reported is never unproven."""
    text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output
    return any(NAME_SIGNAL.match(line) for line in (text or "").splitlines())


# The shell's report that it never started a program. A shell builtin that starts says nothing of the kind. The suites run
# under more than one shell and the words differ: bash says `No such file or directory`, the CI's dash says `not found`.
START_REFUSAL = ("command not found", "No such file or directory", "not found", "Permission denied", "is a directory")
# A file that exists and is executable can still be refused by the kernel: a shebang interpreter that is not there, a
# binary built for another machine. Only these name the file the command starts first; a `command not found` here is a
# program the command started itself, as in `sh -c missing_prog`, and that one is a measured failure.
EXEC_REFUSAL = ("bad interpreter", "exec format error", "Bad CPU type in executable")
LEADING_PROGRAM = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*([^\s;&|()<>]+)")
# A shell word always starts, so its non-zero exit is a measured failure: `exit 127` is a check, not a missing program.
SHELL_WORDS = frozenset("""! : . [ [[ break case cd command continue do done elif else esac eval exec exit export false fi
for function getopts hash if in local printf pwd read readonly return select set shift source test then times trap true
type ulimit umask unalias unset until wait while""".split())


def _leading_program(cmd):
    """The program the shell starts first, after any `VAR=value` assignments; empty when the command names none."""
    m = LEADING_PROGRAM.match(cmd)
    return m.group(1).strip("'\"") if m else ""


def _refused_shebang(path):
    """True when the file names a shebang interpreter that is not there, so the kernel refuses the file itself and nothing
    it holds ran. Read from the file, never from the shell's message: bash calls it `bad interpreter` and dash only `not
    found`, and the two must answer the same (#1803). A file with no shebang is left to the shell's own words, which is the
    only reading of a binary built for another machine."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return True
    if not head.startswith(b"#!"):
        return False
    words = head[2:].split(b"\n", 1)[0].split()
    if not words:
        return True
    interpreter = os.fsdecode(words[0])
    return not (os.path.isfile(interpreter) and os.access(interpreter, os.X_OK))


def _cannot_start(cmd, cwd, env, stderr):
    """True when the shell never started a program, so the command proves nothing (#1803).

    A shell built-in always starts, so a refusal in its wake names the program the command runs next, as in
    `cd sub && ./missing`. A word with a path separator is checked as a path, relative to the tree the command runs in; a
    bare word is looked up on the command's own `PATH`. A program that exists and started is measured at any exit, never
    called malformed: its own `command not found` belongs to something it ran, not to the shell. Only the kernel's
    refusal of that very file, a bad shebang interpreter or a binary of another machine, makes it malformed anyway.
    """
    program = _leading_program(cmd)
    if not program:
        return False
    refused = any(phrase in stderr for phrase in START_REFUSAL)
    if program in SHELL_WORDS:
        return refused
    if os.sep in program or (os.altsep and os.altsep in program):
        path = program if os.path.isabs(program) else os.path.join(cwd, program)
    else:
        path = shutil.which(program, path=env.get("PATH", ""))
    if path is None or not (os.path.isfile(path) and os.access(path, os.X_OK)):
        return True
    return _refused_shebang(path) or any(phrase in stderr for phrase in EXEC_REFUSAL)


def _tree_sha(cwd):
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _timeout_memo(cwd, cmd):
    """The marker of one (tree, command) timeout under `$SG_STATE`, or None when the tree has no sha to name it.

    The same tree and the same command timing out twice is not the machine: it is a hang the change brought, so the
    second timeout is a measured red (#1874, ADR-0164).
    """
    sha = _tree_sha(cwd)
    if not sha:
        return None
    key = hashlib.sha1(f"{sha}\0{cmd}".encode()).hexdigest()
    return pathlib.Path(os.environ.get("SG_STATE") or "/tmp") / "ac-timeouts" / key


def _suite_ran_past_cap(cwd, cmd, cap):
    """True when a suite the command names ran on this tree for at least `cap` seconds (ADR-0175).

    `child-gate.sh` writes one row per finished suite: finish, start, wait, load, load, exit, suite, tree sha. A suite of
    this tree that needed the whole cap shows the cap is too short, so a timeout of the command says nothing of the change.
    """
    named = {os.path.realpath(os.path.join(cwd, s)) for s in CHILD_SUITE.findall(cmd)}
    sha = _tree_sha(cwd)
    if not named or not sha:
        return False
    history = pathlib.Path(os.environ.get("SG_STATE") or "/tmp/sg-state") / "child-suite-history"
    try:
        rows = history.read_text().splitlines()
    except OSError:
        return False
    for row in rows:
        f = row.split("\t")
        if len(f) != 8 or f[7] != sha or os.path.realpath(f[6]) not in named:
            continue
        try:
            if int(f[0]) - int(f[1]) >= cap:
                return True
        except ValueError:
            continue
    return False


def run(cmd, cwd, timeout=None):
    """Run a shell command in `cwd`. Exit code 0 is ok; any other exit or a timeout is a failure with evidence.
    A command the shell cannot parse is `malformed`, not a failed check: nothing ran, so nothing was proved false.
    So is a command whose leading program cannot start (exit 126 or 127 with no such executable): a path that names no
    program proves nothing about the code. A program that did start is a measured failure at any exit.
    A command that ends on the gate's unproven exit (4), carries the gate's own `fast-gate:` line, printed no pytest
    summary line, and named no failing test is `unproven`: the gate measured nothing, so nothing was proved false
    (ADR-0164, #1825, #2337). The exit code alone is not the gate's, because any command may exit 4 for its own reason, so
    without that line the command is a measured failure. A command that printed a summary or named a red measured its
    answer and stays a failure.
    `timeout` None takes the cap of `ac_timeout()` at call time, so `SG_AC_TIMEOUT` reaches the gate and the landing check.
    The counts of the output's last pytest summary are reported beside the exit code, on a green command and a red one
    alike (#80). A timeout and a command the shell cannot parse report none: nothing was measured.
    A timeout is `unproven`, because a command the machine could not finish proved nothing false (ADR-0164, #1874), and a
    marker under `$SG_STATE` names the (tree, command) that timed out: the same pair timing out again is a measured red,
    so a hang the change brought cannot pass by omission."""
    timeout = ac_timeout() if timeout is None else timeout
    syntax = subprocess.run(["/bin/sh", "-n", "-c", cmd], capture_output=True, cwd=cwd)
    if syntax.returncode != 0:
        return CmdResult(False, evidence_of(syntax.stderr), malformed=True)
    with gate_slots(cwd) as (n, slots):
        env = {**clean_env(os.environ), **bounded_env(n)}
        if slots:
            env[SLOTS_HELD] = str(slots)
        else:
            env.pop(SLOTS_HELD, None)
        try:
            r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            evidence = f"timeout after {timeout:g} s"
            memo = _timeout_memo(cwd, cmd)
            if memo is not None and memo.exists():
                if _suite_ran_past_cap(cwd, cmd, timeout):
                    return CmdResult(False, f"{evidence}: a suite it names ran for the whole cap on this tree, so the cap is too short and the timeout is unproven", unproven=True)
                return CmdResult(False, f"{evidence}: the same tree and command timed out before, so this is a measured red")
            if memo is not None:
                try:
                    memo.parent.mkdir(parents=True, exist_ok=True)
                    memo.write_text(evidence)
                except OSError:
                    pass   # no record, no red: the first timeout still proved nothing
            return CmdResult(False, evidence, unproven=True)
    if r.returncode == 0:
        return CmdResult(True, counts=counts_of(r.stdout + r.stderr))
    stderr = r.stderr.decode("utf-8", "replace")
    if r.returncode in CANNOT_START and _cannot_start(cmd, cwd, env, stderr):      # nothing ran, so nothing was measured (#1803)
        return CmdResult(False, evidence_of(r.stdout + r.stderr), malformed=True)
    out, counts = evidence_of(r.stdout + r.stderr), counts_of(r.stdout + r.stderr)
    text = r.stdout + r.stderr
    if (r.returncode == GATE_UNPROVEN_EXIT and GATE_PROVENANCE.search(text.decode("utf-8", "replace"))
            and counts is None and not _named_a_failure(text)):
        return CmdResult(False, out, unproven=True)     # the gate's exit 4, no summary and no red named: nothing was measured (#2337)
    return CmdResult(False, out, counts=counts)
