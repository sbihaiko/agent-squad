# /// script
# requires-python = ">=3.10"
# dependencies = ["claude-agent-sdk==0.2.161", "anyio"]
# ///
"""Probe two open facts about one graph node run as one query() call.

Run: uv run scripts/archive/probe_node_deadline.py <mode>
Modes:
  deadline-asyncio  wait_for around query(); does the CLI child survive?
  deadline-anyio    anyio.fail_after around query(); does the CLI child survive?
  bg-result         background subagent; does a second ResultMessage carry origin?
Auth: the CLI login (OAuth). No API key is read or set.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time

import anyio
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

MODEL = "haiku"
DEADLINE_S = 15


def descendants(root: int) -> list[tuple[int, str]]:
    """Return (pid, command) for every live descendant of root."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="],
                         capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    found, frontier = [], {root}
    while frontier:
        nxt = {p for p, ppid, _ in rows if ppid in frontier and p not in {f for f, _ in found}}
        found += [(p, c) for p, _, c in rows if p in nxt]
        frontier = nxt
    return found


def kill_all(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


def options(**kw) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=tempfile.mkdtemp(prefix="probe-"), model=MODEL, setting_sources=[],
        permission_mode="bypassPermissions", max_turns=6, max_budget_usd=0.5, **kw)


async def consume(prompt: str, opts: ClaudeAgentOptions, results: list) -> None:
    async for msg in query(prompt=prompt, options=opts):
        if isinstance(msg, ResultMessage):
            results.append(msg)


async def deadline(kind: str) -> dict:
    prompt = ("Run this exact bash command in the foreground and do nothing else: "
              "python3 -c \"import time; time.sleep(90)\"")
    me = os.getpid()
    results: list = []
    timed_out = False
    t0 = time.time()
    try:
        if kind == "asyncio":
            await asyncio.wait_for(consume(prompt, options(), results), DEADLINE_S)
        else:
            with anyio.fail_after(DEADLINE_S):
                await consume(prompt, options(), results)
    except (asyncio.TimeoutError, TimeoutError):
        timed_out = True
    elapsed = round(time.time() - t0, 1)
    await asyncio.sleep(5)
    alive = descendants(me)
    kill_all([p for p, _ in alive])
    return {"mode": f"deadline-{kind}", "timed_out": timed_out, "elapsed_s": elapsed,
            "results_seen": len(results),
            "result_text": [(r.result or "")[:160] for r in results],
            "num_turns": [r.num_turns for r in results],
            "descendants_alive_5s_after": [(p, c[:100]) for p, c in alive]}


async def bg_result() -> dict:
    prompt = ("Use the Agent tool with run_in_background set to true to start a "
              "general-purpose subagent whose only job is to reply 'bg done'. "
              "After it starts, reply 'main done' and stop.")
    results: list = []
    with anyio.fail_after(240):
        await consume(prompt, options(), results)
    return {"mode": "bg-result", "result_count": len(results),
            "results": [{"subtype": r.subtype, "is_error": r.is_error,
                         "origin": getattr(r, "origin", "MISSING"),
                         "result": (r.result or "")[:80]} for r in results]}


async def main(mode: str) -> None:
    if mode == "deadline-asyncio":
        out = await deadline("asyncio")
    elif mode == "deadline-anyio":
        out = await deadline("anyio")
    elif mode == "bg-result":
        out = await bg_result()
    else:
        sys.exit(__doc__)
    print(json.dumps(out, indent=1, default=str))


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))
