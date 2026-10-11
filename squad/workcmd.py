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
import subprocess
from dataclasses import dataclass

TIMEOUT_S = 300           # the default cap for one acceptance command, in seconds (>= 300: see the module docstring)
EVIDENCE_TAIL = 1500      # characters kept from the end of a failed command's output


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


def run(cmd, cwd, timeout=None):
    """Run a shell command in `cwd`. Exit code 0 is ok; any other exit or a timeout is a failure with evidence.
    A command the shell cannot parse is `malformed`, not a failed check: nothing ran, so nothing was proved false.
    `timeout` None takes the cap of `ac_timeout()` at call time, so `SG_AC_TIMEOUT` reaches the gate and the landing check."""
    timeout = ac_timeout() if timeout is None else timeout
    syntax = subprocess.run(["/bin/sh", "-n", "-c", cmd], capture_output=True, cwd=cwd)
    if syntax.returncode != 0:
        return CmdResult(False, syntax.stderr.decode("utf-8", "replace").strip()[-EVIDENCE_TAIL:], malformed=True)
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, timeout=timeout, env=clean_env(os.environ))
    except subprocess.TimeoutExpired:
        return CmdResult(False, f"timeout after {timeout:g} s")
    if r.returncode == 0:
        return CmdResult(True)
    return CmdResult(False, (r.stdout + r.stderr).decode("utf-8", "replace").strip()[-EVIDENCE_TAIL:])
