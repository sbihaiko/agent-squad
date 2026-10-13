#!/bin/bash
# usage: publish-live.sh [--check] [<src>] [<dest>]   Publishes this repository's contrib/squad-goal into the live skill
# (~/.claude/skills/squad-goal) as an explicit FILE LIST, never a mirror: the live copy holds machine config (models.json,
# per-repo profiles under scripts/profiles/, and $SG_STATE), so the step copies the files the repository ships, drops the
# names it retired together with the suites they took with them, and removes nothing else: the destination, the machine
# config and every other file no name here covers are left alone.
#
# src  the repository tree to publish (default: the contrib/squad-goal this script sits in, else $SG_SQUAD/contrib/squad-goal).
# dest the live skill (default: $SG_LIVE_DIR, else $HOME/.claude/skills/squad-goal).
# --check changes nothing, prints one `copy <file>` / `drop <scripts/name>` line per difference and exits 1; with no
# difference it prints nothing and exits 0. A publish prints each retired name and each retired script's suite it drops,
# and a one-line count.
#
# The retired names (#122, #134) are the fork-only route the repository no longer ships. They are split in the source so
# this list is not itself a call site: test-retire-fork-only.sh greps every shipped script for a retired name.
# A retired script takes its suite with it (#210): the repository ships the suites of the scripts it ships, so a live
# scripts/test-*.sh the source tree does not ship is the suite of a script the repository no longer ships.
RETIRED="fork""close.sh
fork""-gate.sh
fork""only.sh
publish-landing.sh
sync-to-""fork.sh
refresh-from-""fork.sh
forkrescue.py
forkstrand.py
req-checklist-""fork.txt"
set -uo pipefail
check=""; args=()
for a in "$@"; do case "$a" in --check) check=1;; *) args+=("$a");; esac; done
here="$(cd "$(dirname "$0")" && pwd)"
src=${args[0]:-$(cd "$here/.." && pwd)}
[ -d "$src/scripts" ] || src="${SG_SQUAD:-$here/../..}/contrib/squad-goal"   # run from the live copy: the repository is SG_SQUAD
dest=${args[1]:-${SG_LIVE_DIR:-$HOME/.claude/skills/squad-goal}}
[ -d "$src" ] || { echo "publish-live: no source tree at $src" >&2; exit 2; }
[ -d "$dest" ] || { echo "publish-live: no live skill at $dest" >&2; exit 2; }

# The files the repository ships: SKILL.md and scripts/*, minus machine config (models.json, per-repo profiles).
# scripts/profiles/example.sh is the template, so it ships; every other profile is machine config.
shipped() { { ( cd "$src" && find . -type f ! -name .DS_Store ! -name '*.pyc' ! -name '*.bak*' ! -path '*/__pycache__/*' | sed 's|^\./||' ) \
  | { grep -vxE 'scripts/models\.json' || true; } \
  | { grep -vE '^scripts/profiles/.+\.sh$' || true; }
  [ -f "$src/scripts/profiles/example.sh" ] && printf '%s\n' scripts/profiles/example.sh; } | LC_ALL=C sort -u; }

files=$(shipped)

rc=0
for f in $files; do
  if [ -f "$dest/$f" ] && cmp -s "$src/$f" "$dest/$f" && { [ ! -x "$src/$f" ] || [ -x "$dest/$f" ]; }; then continue; fi
  if [ -n "$check" ]; then echo "copy $f"; rc=1; continue; fi
  mkdir -p "$dest/$(dirname "$f")"
  cp -p "$src/$f" "$dest/$f.new.$$" && mv "$dest/$f.new.$$" "$dest/$f"    # new inode: a running copy keeps reading the old one
done
for n in $RETIRED; do
  [ -e "$dest/scripts/$n" ] || continue
  if [ -n "$check" ]; then echo "drop scripts/$n"; rc=1; continue; fi
  rm -f "$dest/scripts/$n"; echo "publish-live: dropped scripts/$n"
done
for s in "$dest"/scripts/test-*.sh; do
  [ -f "$s" ] || continue
  b="scripts/$(basename "$s")"
  printf '%s\n' $files | grep -qxF "$b" && continue        # the repository ships this suite: it stays, and the copy above is its content
  if [ -n "$check" ]; then echo "drop $b"; rc=1; continue; fi
  rm -f "$s"; echo "publish-live: dropped $b"
done
[ -n "$check" ] && exit $rc
echo "publish-live: $(printf '%s\n' $files | grep -c .) file(s) published to $dest, retired names and their suites dropped by name"
