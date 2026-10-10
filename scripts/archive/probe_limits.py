# /// script
# requires-python = ">=3.10"
# dependencies = ["claude-agent-sdk"]
# ///
"""Probe the defaults-file limits against a live Claude Agent SDK. Model spend is granted (CLAUDE.md); every call has caps.

Run: uv run scripts/archive/probe_limits.py [context|output|subagent|dontask|tools]...   (no argument runs all)
Writes docs/research/limits-probe.json. Each probe runs in an empty scratch directory with setting_sources=[].
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, SystemMessage, query

MODEL = "claude-sonnet-5-5"
OUT = Path(__file__).resolve().parents[2] / "docs" / "research" / "limits-probe.json"
CALL_TIMEOUT = 240


def res_row(m):
    return {"subtype": m.subtype, "is_error": m.is_error, "turns": m.num_turns, "cost": m.total_cost_usd, "stop_reason": m.stop_reason,
            "terminal_reason": getattr(m, "terminal_reason", None), "usage": m.usage, "model_usage": m.model_usage,
            "denials": m.permission_denials, "result_chars": len(m.result or ""), "errors": m.errors}


def base(cwd, **kw):
    return ClaudeAgentOptions(cwd=str(cwd), model=MODEL, setting_sources=[], permission_mode="bypassPermissions", max_turns=kw.pop("max_turns", 3),
                              max_budget_usd=kw.pop("max_budget_usd", 0.5), **kw)


async def run_query(prompt, opts):
    seen = {"results": [], "init": None, "compact": 0, "tool_uses": [], "error": None}
    try:
        await _drain(query(prompt=prompt, options=opts), seen)
    except Exception as e:      # a call that errors is a finding: keep what arrived before it
        seen["error"] = f"{type(e).__name__}: {e}"
    return seen


async def _drain(stream, seen):
    async for m in stream:
        n = type(m).__name__
        if n == "AssistantMessage":
            seen["tool_uses"] += [b.name for b in m.content if type(b).__name__ == "ToolUseBlock"]
        elif isinstance(m, SystemMessage) and m.subtype == "init":
            seen["init"] = {"tools": m.data.get("tools"), "model": m.data.get("model"), "version": m.data.get("claude_code_version")}
        elif isinstance(m, SystemMessage) and "compact" in m.subtype:
            seen["compact"] += 1
        elif n == "ResultMessage":
            seen["results"].append({**res_row(m), "origin": getattr(m, "origin", None), "text": (m.result or "")[:400]})


async def probe_context(tmp):
    rows = {}
    for label, env in (("default", {}), ("window_100000", {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000"}), ("window_50000", {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "50000"})):
        async with ClaudeSDKClient(base(tmp, env=env, max_turns=1, max_budget_usd=0.3)) as c:
            await c.query("Reply with the word ok.")
            async for m in c.receive_response():
                pass
            u = await c.get_context_usage()
            rows[label] = {k: u.get(k) for k in ("totalTokens", "maxTokens", "rawMaxTokens", "autoCompactThreshold", "isAutoCompactEnabled", "model")}
    return rows


async def probe_output(tmp):
    prompt = "Write a 1500 word essay on the history of tea. Output the essay only. Use no tools."
    rows = {}
    for label, env in (("cap_300", {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "300"}), ("cap_32000", {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "32000"})):
        rows[label] = await run_query(prompt, base(tmp, env=env, max_turns=2, max_budget_usd=0.6, tools=["Read"]))
    return rows


async def probe_subagent(tmp):
    (tmp / "a.txt").write_text("one\ntwo\nthree\n")
    prompt = ("Use the Agent tool with subagent_type general-purpose, in the foreground (run_in_background false), to count the lines in a.txt. "
              "Wait for its answer. Then reply with the number only.")
    return await run_query(prompt, base(tmp, max_turns=6, max_budget_usd=1.0, tools=["Task", "Read"]))


async def probe_dontask(tmp):
    (tmp / "note.txt").write_text("draft\n")
    opts = ClaudeAgentOptions(cwd=str(tmp), model=MODEL, setting_sources=[], permission_mode="dontAsk", max_turns=6, max_budget_usd=0.6,
                              allowed_tools=["Read", "Edit", "Write", "Grep", "Glob"], disallowed_tools=["WebFetch", "WebSearch", "Task", "Agent"])
    seen = await run_query("Edit note.txt: replace the word draft with final. Then run `echo hi` with Bash and report whether it was allowed.", opts)
    seen["file_after"] = (tmp / "note.txt").read_text()
    return seen


async def probe_tools(tmp):
    rows = {}
    for label, kw in (("tools_none", {}), ("tools_read_grep_glob", {"tools": ["Read", "Grep", "Glob"]}), ("tools_read_only", {"tools": ["Read"]})):
        seen = await run_query("Reply with the word ok.", base(tmp, max_turns=1, max_budget_usd=0.3, **kw))
        rows[label] = {"init": seen["init"], "cost": [r["cost"] for r in seen["results"]]}
    return rows


async def probe_compact(tmp):
    """Fill a 100000-token window past its 67000 auto-compact threshold and count PreCompact hook calls."""
    import random
    rnd = random.Random(7)
    words = [f"w{rnd.randrange(10**6)}" for _ in range(40000)]
    for i in range(5):
        (tmp / f"big{i}.txt").write_text(" ".join(rnd.choice(words) for _ in range(14000)) + "\n")
    fired = []

    async def on_compact(input_data, tool_use_id, context):
        fired.append(input_data.get("trigger"))
        return {}
    opts = base(tmp, env={"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000"}, max_turns=14, max_budget_usd=2.0, tools=["Read"],
                hooks={"PreCompact": [HookMatcher(hooks=[on_compact], timeout=60)]})
    seen = await run_query("Read big0.txt, big1.txt, big2.txt, big3.txt and big4.txt one at a time with the Read tool, then reply with the word done.", opts)
    seen["precompact_fired"] = fired
    return seen


PROBES = {"compact": probe_compact, "context": probe_context, "output": probe_output, "subagent": probe_subagent, "dontask": probe_dontask, "tools": probe_tools}


async def main(names):
    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    for n in names or PROBES:
        with tempfile.TemporaryDirectory() as d:
            try:
                out[n] = await asyncio.wait_for(PROBES[n](Path(d)), CALL_TIMEOUT * 3)
            except Exception as e:     # a failed probe is a finding: keep going
                out[n] = {"error": f"{type(e).__name__}: {e}"}
        OUT.write_text(json.dumps(out, indent=1, default=str) + "\n")
        print(n, "done", file=sys.stderr)


asyncio.run(main(sys.argv[1:]))
