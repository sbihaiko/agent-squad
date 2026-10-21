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
# A publish refuses (exit 3, nothing written, one line naming both commits) when the source HEAD is not in the history of
# $SG_SQUAD's HEAD, the repository the live scripts import squad from (#605). SG_SQUAD unset or the source repo: no check.
# A publish refuses (exit 3, nothing written, one line naming the tree) when the source is a linked worktree (#658).
# A publish writes $dest/PUBLISHED (`<sha> <utc>`, atomic): the tested sha, the source HEAD (#700). --check prints
# `publish <sha>` while PUBLISHED is missing or names another sha. A publish with a failed copy writes no PUBLISHED. A source with no git has no sha and no PUBLISHED.
#
# The retired names (#122, #134, #361) are the routes the repository no longer ships: the fork-only route and, from the #361
# landing, the pull-request route. The fork names are split in the source so this list is not itself a call site:
# test-retire-fork-only.sh greps every shipped script for a retired name.
RETIRED="fork""close.sh
fork""-gate.sh
fork""only.sh
publish-landing.sh
sync-to-""fork.sh
refresh-from-""fork.sh
forkrescue.py
forkstrand.py
req-checklist-""fork.txt
autofix.sh
autopanel.sh
autopilot.sh
prland.sh
retryfix.sh
revci.sh
speed.py"
# A retired name takes its suites with it: the fork route's suites stayed in the live copy after its scripts were gone, and
# every one of them failed on a call to a file that is not there (#210). Some do not carry their subject in their name, so
# they are named here.
RETIRED_SUITES="test-""forkbranch-source.sh
test-sync-refresh.sh
test-autofrontier-merged.sh
test-janitor-g.sh
test-janitor-kmn.sh"
# A data file the landing deleted travels the same way, at the top of the skill, not under scripts/.
RETIRED_DATA="ci-baseline.txt"
set -uo pipefail
check=""; args=()
for a in "$@"; do case "$a" in --check) check=1;; *) args+=("$a");; esac; done
here="$(cd "$(dirname "$0")" && pwd)"
src=${args[0]:-$(cd "$here/.." && pwd)}
[ -d "$src/scripts" ] || src="${SG_SQUAD:-$here/../..}/contrib/squad-goal"   # run from the live copy: the repository is SG_SQUAD
dest=${args[1]:-${SG_LIVE_DIR:-$HOME/.claude/skills/squad-goal}}
[ -d "$src" ] || { echo "publish-live: no source tree at $src" >&2; exit 2; }
[ -d "$dest" ] || { echo "publish-live: no live skill at $dest" >&2; exit 2; }

# Only the main checkout publishes. A coding child published its unlanded branch from its own worktree (#658), so a
# publish from a linked worktree (git-dir differs from the common dir) is refused: exit 3, nothing written, one line
# naming the tree. --check writes nothing, so it still runs. A source with no git is not checked.
if [ -z "$check" ]; then
  gd=$(git -C "$src" rev-parse --path-format=absolute --git-dir 2>/dev/null)
  cd_=$(git -C "$src" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
  if [ -n "$gd" ] && [ -n "$cd_" ] && [ "$(cd "$gd" && pwd -P)" != "$(cd "$cd_" && pwd -P)" ]; then
    wtop=$(cd "$(git -C "$src" rev-parse --show-toplevel)" && pwd -P)
    echo "publish-live: refused: source $wtop is a linked worktree, not the main checkout; publish from the main checkout after landing" >&2; exit 3
  fi
fi

# The live scripts import squad from SG_SQUAD (caps.sh, childcc.py). A source tree ahead of SG_SQUAD ships scripts that call
# a squad command SG_SQUAD lacks (#605), so a publish refuses unless the source HEAD is in SG_SQUAD's history. No check
# when SG_SQUAD is unset, is the source repository itself, or either side has no git.
if [ -z "$check" ] && [ -n "${SG_SQUAD:-}" ]; then
  srctop=$(git -C "$src" rev-parse --show-toplevel 2>/dev/null); sqtop=$(git -C "$SG_SQUAD" rev-parse --show-toplevel 2>/dev/null)
  if [ -n "$srctop" ] && [ -n "$sqtop" ] && [ "$(cd "$srctop" && pwd -P)" != "$(cd "$sqtop" && pwd -P)" ]; then
    sh=$(git -C "$src" rev-parse HEAD); qh=$(git -C "$SG_SQUAD" rev-parse HEAD)
    git -C "$SG_SQUAD" merge-base --is-ancestor "$sh" "$qh" 2>/dev/null || {
      echo "publish-live: refused: source $srctop at ${sh:0:8} is not in the history of SG_SQUAD $sqtop at ${qh:0:8}; fast-forward SG_SQUAD first" >&2; exit 3; }
  fi
fi

# The files the repository ships: SKILL.md and scripts/*, minus machine config (models.json, per-repo profiles).
# scripts/profiles/example.sh is the template, so it ships; every other profile is machine config.
shipped() { { ( cd "$src" && find . -type f ! -name .DS_Store ! -name '*.pyc' ! -name '*.bak*' ! -path '*/__pycache__/*' | sed 's|^\./||' ) \
  | { grep -vxE 'scripts/models\.json' || true; } \
  | { grep -vE '^scripts/profiles/.+\.sh$' || true; }
  [ -f "$src/scripts/profiles/example.sh" ] && printf '%s\n' scripts/profiles/example.sh; } | LC_ALL=C sort -u; }

sha=$(git -C "$src" rev-parse HEAD 2>/dev/null); bad=
rc=0
for f in $(shipped); do
  if [ -f "$dest/$f" ] && cmp -s "$src/$f" "$dest/$f" && { [ ! -x "$src/$f" ] || [ -x "$dest/$f" ]; }; then continue; fi
  if [ -n "$check" ]; then echo "copy $f"; rc=1; continue; fi
  { mkdir -p "$dest/$(dirname "$f")" && cp -p "$src/$f" "$dest/$f.new.$$" && mv "$dest/$f.new.$$" "$dest/$f"; } || bad=1  # new inode: a running copy keeps reading the old one
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
for n in $RETIRED_DATA; do
  [ -e "$dest/$n" ] || continue
  if [ -n "$check" ]; then echo "drop $n"; rc=1; continue; fi
  rm -f "$dest/$n"; echo "publish-live: dropped $n"
done
if [ -n "$sha" ] && [ "$(cut -d' ' -f1 "$dest/PUBLISHED" 2>/dev/null)" != "$sha" ]; then
  if [ -n "$check" ]; then echo "publish $sha"; rc=1
  elif [ -z "$bad" ]; then printf '%s %s\n' "$sha" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$dest/PUBLISHED.new.$$" && mv "$dest/PUBLISHED.new.$$" "$dest/PUBLISHED"; fi
fi
[ -n "$check" ] && exit $rc
echo "publish-live: $(shipped | wc -l | tr -d ' ') file(s) published to $dest, retired names dropped by name"
