#!/bin/bash
# usage: sync-to-fork.sh   Copies this skill into the agent-squad fork as contrib/squad-goal (no commit).
# Ships the scripts, SKILL.md and models/plans config; per-repo profiles stay local (only profiles/example.sh ships),
# and the copy is refused if a name from the local deny list ($SG_STATE/sync-deny, one regex, never shipped) or a home path is left in it.
# It is also refused when the fork's copy holds work not in this live skill (landed on local/next, or edited since the last sync/refresh):
# the names are listed and nothing is removed. Bring it over with refresh-from-fork.sh, or SG_SYNC_FORCE=1 to discard it.
set -euo pipefail
src="$(cd "$(dirname "$0")/.." && pwd)"; F=${SG_SQUAD_FORK:-$HOME/VSCodeProjects/agent-squad}; dst="$F/contrib/squad-goal"
deny=$(cat "${SG_STATE:-$HOME/.cache/squad-goal}/sync-deny")   # required: the names that must never ship
st=${SG_STATE:-$HOME/.cache/squad-goal}
sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
if [ -d "$dst" ] && [ "${SG_SYNC_FORCE:-}" != 1 ]; then   # fork-ahead = a fork file that differs from live and is not what the last sync/refresh left there
  ahead=$(cd "$dst" && find . -type f ! -name .DS_Store | sed 's|^\./||' | { grep -v '^scripts/models\.json$' || true; } | sort | while read -r f; do
    [ -f "$src/$f" ] && cmp -s "$src/$f" "$f" && continue
    b=$([ -f "$st/fork-base" ] && awk -v p="$f" '$2==p{print $1}' "$st/fork-base" || true); [ -n "$b" ] && [ "$b" = "$(sha "$f")" ] || echo "$f"; done)
  [ -z "$ahead" ] || { echo "refused: $dst holds work the live skill does not have (run refresh-from-fork.sh, or SG_SYNC_FORCE=1 to discard it):"; echo "$ahead"; exit 1; }
fi
rm -rf "$dst"; mkdir -p "$dst/scripts/profiles"
cp "$src/SKILL.md" "$dst/"
for f in "$src"/scripts/*; do [ -f "$f" ] && cp "$f" "$dst/scripts/"; done
cp "$src/scripts/profiles/example.sh" "$dst/scripts/profiles/"
if hits=$( { /usr/bin/grep -rniE "$deny" "$dst"; /usr/bin/grep -rnE "/U[s]ers/|/h[o]me/[a-z]" "$dst"; } ); then echo "refused: names left in the copy:"; echo "$hits" | cut -c1-160; rm -rf "$dst"; exit 1; fi
mkdir -p "$st"; ( cd "$dst" && find . -type f ! -name .DS_Store | sed 's|^\./||' | sort | while read -r f; do echo "$(sha "$f") $f"; done ) > "$st/fork-base"
echo "synced $(find "$dst" -type f | wc -l | tr -d ' ') files -> $dst"
