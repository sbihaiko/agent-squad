"""A command run in a workspace: one policy for the gate and the landing check.

The environment has no virtualenv. The cap is `TIMEOUT_S`, moved by `SG_AC_TIMEOUT`. A failure keeps the output
tail as evidence. The gate (`ac-verify`) and the landing check (`Dispatch.ac_survey`) both call `run`.

The default clears the check the instructions name (#1785): the fork's own `contrib/squad-goal/scripts/shell-suites.sh`
runs the 41 `test-*.sh` suites next to it and took 158.69 s real on an idle machine, so the old 120 s cap killed it,
recorded `ac_failed:timeout after 120 s` and failed a run whose code was green. A run that needs more room sets
`SG_AC_TIMEOUT`; a command is still asked to be the narrowest one that proves the done condition, so the common case
stays fast (a wide command is a choice, never the default).

Guards: `tests/test_workcmd.py`.
"""
import os
import re
import subprocess
from dataclasses import dataclass

TIMEOUT_S = 300           # the default cap for one acceptance command, in seconds (>= 300: see the module docstring)
EVIDENCE_TAIL = 1500      # the most characters of a failed command's output kept as evidence

# A line that explains a failure, even when it sits above the kept tail. `pytest -q` prints the failure block first and
# the slowest-durations block last, so a bare tail records the standings and drops the assertion (ADR-0100 rule 10).
EVIDENCE_SIGNAL = re.compile(r"^(_{5,}|E   |FAILED|ERROR)")
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
    signals = [line for line in head.splitlines() if EVIDENCE_SIGNAL.match(line)]
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


@dataclass
class CmdResult:
    ok: bool
    evidence: str = ""     # empty on success; the output tail on a failure; "timeout after N s" on a timeout
    malformed: bool = False     # the shell cannot parse the command (prose in it); the command never ran
    counts: dict = None    # the counts of the output's last pytest summary, or None: how much of the suite passed (#80)


def run(cmd, cwd, timeout=None):
    """Run a shell command in `cwd`. Exit code 0 is ok; any other exit or a timeout is a failure with evidence.
    A command the shell cannot parse is `malformed`, not a failed check: nothing ran, so nothing was proved false.
    `timeout` None takes the cap of `ac_timeout()` at call time, so `SG_AC_TIMEOUT` reaches the gate and the landing check.
    The counts of the output's last pytest summary are reported beside the exit code, on a green command and a red one
    alike (#80). A timeout and a command the shell cannot parse report none: nothing was measured."""
    timeout = ac_timeout() if timeout is None else timeout
    syntax = subprocess.run(["/bin/sh", "-n", "-c", cmd], capture_output=True, cwd=cwd)
    if syntax.returncode != 0:
        return CmdResult(False, evidence_of(syntax.stderr), malformed=True)
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, timeout=timeout, env=clean_env(os.environ))
    except subprocess.TimeoutExpired:
        return CmdResult(False, f"timeout after {timeout:g} s")
    if r.returncode == 0:
        return CmdResult(True, counts=counts_of(r.stdout + r.stderr))
    return CmdResult(False, evidence_of(r.stdout + r.stderr), counts=counts_of(r.stdout + r.stderr))
