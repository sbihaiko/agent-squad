# /// script
# requires-python = ">=3.10"
# dependencies = ["claude-agent-sdk"]
# ///
"""Probe which slash commands the Claude Agent SDK accepts.

Run: uv run scripts/archive/probe_slash.py [prompt ...]
Default probes: "/goal", then "/autonomy just say hello".
"""
import asyncio
import sys

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

DEFAULT_PROBES = ["/goal", "/autonomy just say hello"]


async def probe(prompt: str) -> None:
    print(f"\n=== {prompt!r} ===")
    options = ClaudeAgentOptions(
        cwd=".",
        setting_sources=["user", "project"],
        permission_mode="default",
        max_turns=3,
    )
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            data = msg.data
            print("slash_commands:", sorted(data.get("slash_commands", [])))
            print("skills:", sorted(data.get("skills", [])))
        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print("assistant:", block.text)
                elif isinstance(block, ToolUseBlock):
                    print("tool_use:", block.name, block.input)
        elif isinstance(msg, UserMessage):
            print("user/injected:", str(msg.content)[:300])
        elif isinstance(msg, ResultMessage):
            print(f"result: error={msg.is_error} turns={msg.num_turns} "
                  f"cost={msg.total_cost_usd}")
            print("result text:", msg.result)


async def main() -> None:
    for prompt in sys.argv[1:] or DEFAULT_PROBES:
        await probe(prompt)


asyncio.run(main())
