#!/bin/bash
# usage: publish-live.sh [--check] [<src>] [<dest>]   Publishes this repository's contrib/squad-goal into the live skill
# (~/.claude/skills/squad-goal) as an explicit FILE LIST, never a mirror: the live copy holds machine config (models.json,
# per-repo profiles under scripts/profiles/, and $SG_STATE), so the step copies the files the repository ships and drops
# the names it retired, and removes nothing else. It never removes the destination and never touches a file it did not name.
#
# src  the repository tree to publish (default: the contrib/squad-goal this script sits in, else $SG_SQUAD/contrib/squad-goal).
# dest the live skill (default: $SG_LIVE_DIR, else $HOME/.claude/skills/squad-goal).
# --check changes nothing, prints one `copy <file>` / `drop <scripts/name>` line per difference and exits 1; with no
# difference it prints nothing and exits 0. A publish prints each retired name it drops and a one-line count.
#
# The retired names (#122, #134) are the fork-only route the repository no longer ships. They are split in the source so
# this list is not itself a call site: test-retire-fork-only.sh greps every shipped script for a retired name.
RETIRED="fork""close.sh
fork""-gate.sh
fork""only.sh
publish-landing.sh
sync-to-""fork.sh
refresh-from-""fork.sh
forkrescue.py
forkstrand.py
req-checklist-""fork.txt"
# A retired name takes its suites with it: the fork route's suites stayed in the live copy after its scripts were gone, and
# every one of them failed on a call to a file that is not there (#210). Two of them do not carry their subject in their
# name, so they are named here.
RETIRED_SUITES="test-""forkbranch-source.sh
test-sync-refresh.sh"
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

rc=0
for f in $(shipped); do
  if [ -f "$dest/$f" ] && cmp -s "$src/$f" "$dest/$f" && { [ ! -x "$src/$f" ] || [ -x "$dest/$f" ]; }; then continue; fi
  if [ -n "$check" ]; then echo "copy $f"; rc=1; continue; fi
  mkdir -p "$dest/$(dirname "$f")"
  cp -p "$src/$f" "$dest/$f.new.$$" && mv "$dest/$f.new.$$" "$dest/$f"    # new inode: a running copy keeps reading the old one
done
for n in $RETIRED; do
  stem=${n%.*}
  for f in "$dest/scripts/test-$stem"*.sh "$dest/scripts/$n"; do
    [ -e "$f" ] || continue
    b=$(basename "$f")
    if [ -n "$check" ]; then echo "drop scripts/$b"; rc=1; continue; fi
    rm -f "$f"; echo "publish-live: dropped scripts/$b"
  done
done
for s in $RETIRED_SUITES; do
  [ -e "$dest/scripts/$s" ] || continue
  if [ -n "$check" ]; then echo "drop scripts/$s"; rc=1; continue; fi
  rm -f "$dest/scripts/$s"; echo "publish-live: dropped scripts/$s"
done
[ -n "$check" ] && exit $rc
echo "publish-live: $(shipped | wc -l | tr -d ' ') file(s) published to $dest, retired names dropped by name"
