#!/bin/bash
# usage: acscan-run.sh <issue...>   Builds the brief (acscan.sh) and has haiku judge it; output tee'd to <brief>.out
d="$(dirname "$0")"; brief=$("$d/acscan.sh" "$@") || exit $?
# --strict-mcp-config: the acceptance scan judges one brief and needs no MCP server; without it the machine's server
# set loads into the call and pays tens of thousands of input tokens of tool schemas before the judgment.
if command -v claude >/dev/null 2>&1; then
  claude -p --strict-mcp-config --model haiku --permission-mode default < "$brief" 2>/dev/null | tee "${brief%.md}.out"
else
  echo "claude CLI not found; brief at $brief — run it via the Agent tool with model haiku"
fi
