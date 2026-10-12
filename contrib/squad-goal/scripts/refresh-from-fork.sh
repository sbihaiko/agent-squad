#!/bin/bash
# usage: refresh-from-fork.sh [--check]   The counterpart of sync-to-fork.sh: copies the fork's main contrib/squad-goal into this live skill.
# --check changes nothing and exits 1 naming every shipped file where live and main disagree.
# Not shipped (ignored on both sides): __pycache__/, *.pyc, *.bak*.
# Out of scope: a file only live carries (the fork never shipped it, so it can never converge) is not divergence.
# Never touched: scripts/models.json, per-repo profiles (only profiles/example.sh ships) and $SG_STATE (sync-deny, fork-base).
# Refused (SG_REFRESH_FORCE=1 overrides) when live carries edits neither synced to the fork nor on main, which the copy would erase.
set -euo pipefail
live="$(cd "$(dirname "$0")/.." && pwd)"; F=${SG_SQUAD_FORK:-$HOME/VSCodeProjects/agent-squad}; br=${SG_FORK_BRANCH:-main}; st=${SG_STATE:-$HOME/.cache/squad-goal}
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git -C "$F" archive "$br" contrib/squad-goal | tar -x -C "$tmp"; fk="$tmp/contrib/squad-goal"
sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
shipped() { ( cd "$1" && find . -type f ! -name .DS_Store ! -name '*.pyc' ! -name '*.bak*' ! -path '*/__pycache__/*' | sed 's|^\./||' | { grep -vE '^scripts/models\.json$|^scripts/profiles/' || true; } ; echo scripts/profiles/example.sh ) | sort -u; }
base() { [ -f "$st/fork-base" ] && awk -v p="$1" '$2==p{print $1}' "$st/fork-base" || true; }
diverged=$( shipped "$fk" | while read -r f; do
  [ -f "$live/$f" ] && cmp -s "$live/$f" "$fk/$f" || echo "$f"; done )   # only what $br ships can be current or stale; a live-only file is neither
if [ "${1:-}" = "--check" ]; then
  [ -z "$diverged" ] && { echo "live skill matches $br"; exit 0; }
  echo "live skill and $br disagree on:"; echo "$diverged"; exit 1
fi
[ -z "$diverged" ] && { echo "already current with $br"; exit 0; }
ahead=$(echo "$diverged" | while read -r f; do
  [ -f "$live/$f" ] || continue; b=$(base "$f"); [ -n "$b" ] && [ "$b" = "$(sha "$live/$f")" ] || echo "$f"; done)
if [ -n "$ahead" ] && [ "${SG_REFRESH_FORCE:-}" != 1 ]; then
  echo "refused: live carries edits that are not in $br (run sync-to-fork.sh first, or SG_REFRESH_FORCE=1 to discard them):"; echo "$ahead"; exit 1
fi
echo "$diverged" | while read -r f; do
  [ -f "$fk/$f" ] || continue
  mkdir -p "$(dirname "$live/$f")"; cp -p "$fk/$f" "$live/$f.new.$$"; mv "$live/$f.new.$$" "$live/$f"   # new inode: a running copy of the script keeps reading the old one
done
mkdir -p "$st"; ( cd "$live" && shipped . | while read -r f; do [ -f "$f" ] && echo "$(sha "$f") $f"; done ) > "$st/fork-base"
echo "refreshed $(echo "$diverged" | wc -l | tr -d ' ') files from $br"
