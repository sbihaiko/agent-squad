#!/bin/bash
# usage: acscan-run.sh <issue...>   Builds the brief (acscan.sh) and has haiku judge it; output tee'd to <brief>.out
d="$(dirname "$0")"; brief=$("$d/acscan.sh" "$@") || exit $?
if command -v claude >/dev/null 2>&1; then
  claude -p --model haiku --permission-mode default < "$brief" 2>/dev/null | tee "${brief%.md}.out"
else
  echo "claude CLI not found; brief at $brief — run it via the Agent tool with model haiku"
fi
