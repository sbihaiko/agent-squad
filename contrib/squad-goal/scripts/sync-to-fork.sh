#!/bin/bash
# usage: sync-to-fork.sh   Copies this skill into the agent-squad fork as contrib/squad-goal (no commit).
# Ships the scripts, SKILL.md and models/plans config; per-repo profiles stay local (only profiles/example.sh ships),
# and the copy is refused if a name from the local deny list ($SG_STATE/sync-deny, one regex, never shipped) or a home path is left in it.
set -euo pipefail
src="$(cd "$(dirname "$0")/.." && pwd)"; F=${SG_SQUAD_FORK:-$HOME/VSCodeProjects/agent-squad}; dst="$F/contrib/squad-goal"
deny=$(cat "${SG_STATE:-$HOME/.cache/squad-goal}/sync-deny")   # required: the names that must never ship
rm -rf "$dst"; mkdir -p "$dst/scripts/profiles"
cp "$src/SKILL.md" "$dst/"
for f in "$src"/scripts/*; do [ -f "$f" ] && cp "$f" "$dst/scripts/"; done
cp "$src/scripts/profiles/example.sh" "$dst/scripts/profiles/"
if hits=$( { /usr/bin/grep -rniE "$deny" "$dst"; /usr/bin/grep -rnE "/U[s]ers/|/h[o]me/[a-z]" "$dst"; } ); then echo "refused: names left in the copy:"; echo "$hits" | cut -c1-160; rm -rf "$dst"; exit 1; fi
echo "synced $(find "$dst" -type f | wc -l | tr -d ' ') files -> $dst"
