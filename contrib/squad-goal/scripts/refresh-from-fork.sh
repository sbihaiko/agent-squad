#!/bin/bash
# usage: refresh-from-fork.sh [--check]   The counterpart of sync-to-fork.sh: copies the fork's main contrib/squad-goal into this live skill.
# --check changes nothing and exits 1 naming every shipped file where live and main disagree.
# Not shipped (ignored on both sides): __pycache__/, *.pyc, *.bak*.
# Out of scope: a file only live carries (the fork never shipped it, so it can never converge) is not divergence.
# Never touched: scripts/models.json, per-repo profiles (only profiles/example.sh ships) and $SG_STATE (sync-deny, fork-base).
# --check also reads the fork's env.sh declaration of what a profile must set (SG_REQUIRED_SETTINGS) against every live
# profile: a required setting a profile lacks, or a retired setting it still names, is a warning line, never a divergence.
# Refused (SG_REFRESH_FORCE=1 overrides) when live carries edits neither synced to the fork nor on main, which the copy would erase.
set -euo pipefail
[ -n "${SG_STATE:-}" ] || . "$(dirname "$0")/env.sh"   # the repository's own directory comes from the seam, never a shared default
live="$(cd "$(dirname "$0")/.." && pwd)"; F=${SG_SQUAD_FORK:-$HOME/VSCodeProjects/agent-squad}; br=${SG_FORK_BRANCH:-main}; st=$SG_STATE
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git -C "$F" archive "$br" contrib/squad-goal | tar -x -C "$tmp"; fk="$tmp/contrib/squad-goal"
sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
shipped() { ( cd "$1" && find . -type f ! -name .DS_Store ! -name '*.pyc' ! -name '*.bak*' ! -path '*/__pycache__/*' | sed 's|^\./||' | { grep -vE '^scripts/models\.json$|^scripts/profiles/' || true; } ; echo scripts/profiles/example.sh ) | sort -u; }
base() { [ -f "$st/fork-base" ] && awk -v p="$1" '$2==p{print $1}' "$st/fork-base" || true; }
# A file diverges on its bytes OR on its executable bit: `cp -p` carries the mode, but a live file whose bytes already match
# is never copied, so a script the loop runs by name stayed 644 while the fork had it 755 -- and every pass it runs printed
# `Permission denied` into the log while the cycle kept going. The mode is part of what the fork ships.
diverged=$( shipped "$fk" | while read -r f; do
  [ -f "$live/$f" ] || { echo "$f"; continue; }   # only what $br ships can be current or stale; a live-only file is neither
  cmp -s "$live/$f" "$fk/$f" || { echo "$f"; continue; }
  if [ -x "$fk/$f" ] && [ ! -x "$live/$f" ]; then echo "$f"; fi; done )
# The profile is read alone, with no inherited setting: is <setting> named after the profile loads?
named() { env -i HOME="$HOME" PATH="$PATH" bash -c '. "$1" >/dev/null 2>&1; [ -n "${!2:-}" ]' _ "$1" "$2"; }
# Entry form: <setting>:<the setting that engages it>, or <setting>:<engaging setting>:retired:<replacement>. A retired
# setting is named as retired while a profile still has it; a migrated profile is quiet.
settings_report() {
  local decl e s eng kind repl p
  decl=$(sed -n 's/^SG_REQUIRED_SETTINGS="\(.*\)"$/\1/p' "$fk/scripts/env.sh" 2>/dev/null || true)
  for p in "$live"/scripts/profiles/*.sh; do
    [ -f "$p" ] && [ "$(basename "$p")" != example.sh ] || continue
    for e in $decl; do IFS=: read -r s eng kind repl <<< "$e"
      if [ "$kind" = retired ]; then
        ! named "$p" "$s" || echo "profile $(basename "$p" .sh): $s is retired, use $repl"
      elif named "$p" "$eng" && ! named "$p" "$s"; then
        echo "profile $(basename "$p" .sh): $eng is set but $s is not, and the shipped code refuses without it"
      fi
    done
  done
}
if [ "${1:-}" = "--check" ]; then
  report=$(settings_report)
  [ -z "$diverged" ] && { echo "live skill matches $br"; [ -z "$report" ] || echo "$report"; exit 0; }
  echo "live skill and $br disagree on:"; echo "$diverged"; [ -z "$report" ] || echo "$report"; exit 1
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
