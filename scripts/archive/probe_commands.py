# /// script
# requires-python = ">=3.10"
# dependencies = ["claude-agent-sdk"]
# ///
"""Dump every command surface the Claude Agent SDK exposes in this repo.

Run: uv run scripts/archive/probe_commands.py
Prints the init lists (one cheap turn) and the public client control methods.
"""
import asyncio
import inspect
import json

import claude_agent_sdk as sdk
from claude_agent_sdk import ClaudeAgentOptions, SystemMessage, query


async def main() -> None:
    opts = ClaudeAgentOptions(cwd=".", setting_sources=["user", "project"], max_turns=1)
    async for msg in query(prompt="Reply with the word ok.", options=opts):
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            d = msg.data
            out = {k: d.get(k) for k in
                   ("claude_code_version", "slash_commands", "terminal_slash_commands",
                    "skills", "tools", "agents", "plugins", "capabilities")}
            print(json.dumps(out, indent=1, default=str))
    print("CLIENT_METHODS", [n for n, _ in inspect.getmembers(sdk.ClaudeSDKClient, inspect.isfunction)
                             if not n.startswith("_")])
    print("MODULE_FUNCS", [n for n in dir(sdk) if inspect.isfunction(getattr(sdk, n))])


asyncio.run(main())
