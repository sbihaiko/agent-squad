"""Child processes behind an adapter: stop the whole process group when the call is cancelled (ADR-0051, ADR-0102 rule 4).

A deadline cancels the calling task. The child and every process it started must be gone when the
teardown ends, so no orphan keeps spending. The teardown time is an engine constant, measured at about
5 s for the SDK's CLI child (docs/research/sdk-live-smoke.md).
"""
import json
import os
import signal
import subprocess

import anyio

from squad.adapter import AdapterError, CallResult

TEARDOWN_S = 5.0


def clean_env(environ):
    """A copy of the environment without a virtualenv.

    A gate command that calls `python3` must reach the project's python3, not the orchestrator's virtualenv. `uv run`
    puts its own venv first on PATH and sets VIRTUAL_ENV, and the target project's tools are elsewhere. Seen in the
    first live subject run (2026-09-30) and again under `uv run pytest`, where `python3 -m pytest` inside a gate found
    no pytest at all.
    """
    env = dict(environ)
    venv = env.pop("VIRTUAL_ENV", None)
    if venv and "PATH" in env:
        env["PATH"] = ":".join(p for p in env["PATH"].split(":") if not p.startswith(venv))
    return env


def _signal_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


async def stop_group(proc, teardown=TEARDOWN_S):
    """Terminate the process group, wait up to `teardown` seconds, then kill it. Shielded from cancellation."""
    with anyio.CancelScope(shield=True):
        _signal_group(proc, signal.SIGTERM)
        with anyio.move_on_after(teardown):
            await proc.wait()
        _signal_group(proc, signal.SIGKILL)
        await proc.wait()


async def run_child(argv, *, cwd=None, teardown=TEARDOWN_S, on_start=None):
    """Run a child in its own process group and return (exit code, stdout). Cancellation stops the group."""
    proc = await anyio.open_process(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    if on_start:
        on_start(proc)
    try:
        chunks = []
        async with proc.stdout:
            async for c in proc.stdout:
                chunks.append(c)
        await proc.wait()
        return proc.returncode, b"".join(chunks).decode()
    finally:
        if proc.returncode is None:
            await stop_group(proc, teardown)
        else:
            _signal_group(proc, signal.SIGKILL)   # a child that exited may leave group members behind


class CommandAdapter:
    """An adapter that runs one command per call and reads `{"output", "spend", "turns"}` JSON from its stdout."""

    def __init__(self, make_argv, teardown=TEARDOWN_S, on_start=None):
        self.make_argv, self.teardown, self.on_start = make_argv, teardown, on_start

    async def call(self, req):
        code, out = await run_child(self.make_argv(req), cwd=req.workspace, teardown=self.teardown, on_start=self.on_start)
        if code != 0:
            raise AdapterError(f"child exited {code}")
        try:
            d = json.loads(out)
        except json.JSONDecodeError as e:
            raise AdapterError(f"child printed no JSON: {e}") from e
        return CallResult(d.get("output"), d.get("spend"), d.get("turns"), d.get("stop_reason"), d.get("subagents", 0), req.session_id)
