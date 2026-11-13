#!/bin/bash
# usage: rollback-agents.sh   Returns the deployment checkout to the previous proven commit and restarts both agents.
# The history of pins lives in the deployment checkout's .git, so the state directory is never read or written here.
# With no previous commit it refuses and changes nothing.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
DEPLOY=${SG_DEPLOY:-$HOME/.local/share/agent-squad/deploy}
PINS="$DEPLOY/.git/squad-pins"
prev=$(tail -n 1 "$PINS" 2>/dev/null || true)
[ -n "$prev" ] || { echo "rollback-agents: no previous proven commit in $PINS; nothing changed" >&2; exit 1; }
"$here/install-agents.sh" --restore "$prev"
tmp="$PINS.tmp"
sed '$d' "$PINS" > "$tmp"
mv "$tmp" "$PINS"
echo "rolled back to $(git -C "$DEPLOY" rev-parse --short HEAD)"
