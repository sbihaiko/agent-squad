"""A command run in a workspace: one policy for the gate and the landing check.

The environment has no virtualenv. The timeout is 120 s. A failure keeps the output tail as evidence.
The gate (`ac-verify`) and the landing check (`Dispatch.ac_survey`) both call `run`.

Guards: `tests/test_workcmd.py`.
"""
import os
import subprocess
from dataclasses import dataclass

TIMEOUT_S = 120
EVIDENCE_TAIL = 1500      # characters kept from the end of a failed command's output


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


def run(cmd, cwd, timeout=TIMEOUT_S):
    """Run a shell command in `cwd`. Exit code 0 is ok; any other exit or a timeout is a failure with evidence.
    A command the shell cannot parse is `malformed`, not a failed check: nothing ran, so nothing was proved false."""
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
