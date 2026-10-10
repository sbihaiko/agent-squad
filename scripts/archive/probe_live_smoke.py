# /// script
# requires-python = ">=3.10"
# dependencies = ["claude-agent-sdk==0.2.161", "anyio"]
# ///
"""Live smoke probe for ticket #43: measured spend, and what a deadline-killed call reveals.

Run: uv run scripts/archive/probe_live_smoke.py <mode>
Modes:
  spend     one tiny call to completion; print the ResultMessage cost fields
  deadline  a call killed by anyio.fail_after; print whether any cost was visible and whether the CLI child is gone
Owner grant in chat: OAuth runs use usage limits, so Sonnet 5.5 at medium effort; the $2 cap per call is a guard only. Auth: the CLI login (OAuth).
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import anyio
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

MODEL, CAP = "claude-sonnet-5-5", 2.0


def descendants(root):
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    rows = [l.split(None, 2) for l in out.splitlines()]
    rows = [(int(a), int(b), c) for a, b, c in (r for r in rows if len(r) == 3 and r[0].isdigit() and r[1].isdigit())]
    found, frontier = [], {root}
    while frontier:
        nxt = {p for p, pp, _ in rows if pp in frontier and p not in {f for f, _ in found}}
        found += [(p, c) for p, _, c in rows if p in nxt]
        frontier = nxt
    return found


def opts():
    return ClaudeAgentOptions(cwd=tempfile.mkdtemp(prefix="smoke-"), model=MODEL, effort="medium", setting_sources=[],
                              permission_mode="bypassPermissions", max_turns=3, max_budget_usd=CAP)


async def run(prompt, deadline):
    seen, msgs = [], 0
    t0, timed_out = time.time(), False
    try:
        with anyio.fail_after(deadline):
            async for m in query(prompt=prompt, options=opts()):
                msgs += 1
                if isinstance(m, ResultMessage):
                    seen.append({"total_cost_usd": m.total_cost_usd, "num_turns": m.num_turns, "duration_ms": m.duration_ms,
                                 "is_error": m.is_error, "subtype": m.subtype, "usage": m.usage, "result": (m.result or "")[:80]})
    except TimeoutError:
        timed_out = True
    elapsed = round(time.time() - t0, 1)
    time.sleep(5)
    alive = descendants(os.getpid())
    for pid, _ in alive:
        try: os.kill(pid, 9)
        except ProcessLookupError: pass
    return {"timed_out": timed_out, "elapsed_s": elapsed, "messages": msgs, "results": seen,
            "descendants_alive_5s_after": [(p, c[:90]) for p, c in alive]}


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "spend":
        out = anyio.run(run, "Reply with exactly: ok", 60)
    else:
        out = anyio.run(run, "Run this exact bash command in the foreground and do nothing else: python3 -c \"import time; time.sleep(90)\"", 12)
    print(json.dumps(out, indent=2, default=str))
