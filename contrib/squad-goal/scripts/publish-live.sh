#!/bin/bash
# usage: publish-live.sh [--check] [--no-compare] [<src>] [<dest>]   Publishes this repository's contrib/squad-goal into the live skill
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
# A publish refuses (exit 3, nothing written, one line naming both paths and the recovery command) when the canonical
# source path equals the destination's -- the live copy itself or a symlink alias of it; --check refuses too (#1377).
# A publish prints one `publish-live: chain <name> differs` line per chain whose head or order differs between the
# repository and the live scripts/models.json, naming both chains, and never writes the live file (#1261, ADR-0140).
# --no-compare is the explicit mode live-guard's per-cycle pass uses: it compares nothing. models-apply.sh is the one writer.
# A publish writes $dest/PUBLISHED (`<sha> <utc>`, atomic): the tested sha, the source HEAD (#700). --check prints
# `publish <sha>` while PUBLISHED is missing or names another sha. A publish reads the destination back after the copy and
# the drop: a shipped file that is missing, holds other bytes or lost its executable bit, or a retired name still
# present, is a failed publish (exit 1, one line naming each mismatch, no success count, no PUBLISHED). A source with no git has no sha and no PUBLISHED.
# A publish closes every issue named by a `Closes #n` trailer in the commits it makes live -- the previously published sha
# (exclusive) to the source HEAD -- with one comment naming the sha and the published time (#732). The loop's loop-bug stop
# reads the issue state alone (ADR-0149), so it lifts by itself; a TDD RED commit names its issue and closes nothing
# (ADR-0136). --check closes nothing, and a close that fails is named, never failing the publish.
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
speed.py
board_burndown.py"
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
check=""; nocompare=""; args=()
for a in "$@"; do case "$a" in --check) check=1;; --no-compare) nocompare=1;; *) args+=("$a");; esac; done
here="$(cd "$(dirname "$0")" && pwd)"
src=${args[0]:-$(cd "$here/.." && pwd)}
[ -d "$src/scripts" ] || src="${SG_SQUAD:-$here/../..}/contrib/squad-goal"   # run from the live copy: the repository is SG_SQUAD
dest=${args[1]:-${SG_LIVE_DIR:-$HOME/.claude/skills/squad-goal}}
[ -d "$src" ] || { echo "publish-live: no source tree at $src" >&2; exit 2; }
[ -d "$dest" ] || { echo "publish-live: no live skill at $dest" >&2; exit 2; }

# The live skill copy is never the source. A publish run from the live copy copies the tree onto itself
# and reports a successful publish (#1377). When the canonical source path equals the destination's --
# a symlink alias resolves to the same tree -- the publish is refused before any copy, retirement or
# tracker call: exit 3, nothing written, one line naming both paths and the recovery command. --check
# refuses too.
srcc=$(cd "$src" && pwd -P); destc=$(cd "$dest" && pwd -P)
if [ "$srcc" = "$destc" ]; then
  echo "publish-live: refused: source $srcc is the live skill destination; publish from the repository tree: publish-live.sh <repo>/contrib/squad-goal $destc" >&2; exit 3
fi

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
  elif [ -n "$srctop" ] && [ -z "$sqtop" ]; then
    # SG_SQUAD is the plugin cache, no git repo (#1422): it holds the commit the plugin was installed at, and a squad/ or
    # pyproject.toml change since then means the live scripts may call a symbol the cache lacks.
    reinstall='claude plugin uninstall agent-squad@agent-squad && claude plugin install agent-squad@agent-squad'
    sh=$(git -C "$srctop" rev-parse HEAD)
    isha=$(jq -r --arg p "${SG_SQUAD%/}" '[.. | objects | select((.installPath? // "" | rtrimstr("/")) == $p)][0].gitCommitSha // empty' \
      "${HOME}/.claude/plugins/installed_plugins.json" 2>/dev/null)
    git -C "$srctop" diff --quiet "${isha:-none}" HEAD -- squad pyproject.toml >/dev/null 2>&1; drc=$?
    if [ "$drc" != 0 ]; then
      echo "publish-live: refused: SG_SQUAD $SG_SQUAD was installed at ${isha:+${isha:0:8}}${isha:-unreadable}, source $srctop is at ${sh:0:8} with a squad change or an unreadable install sha; reinstall: $reinstall" >&2; exit 3
    fi
  fi
fi

# The files the repository ships: SKILL.md and scripts/*, minus machine config (models.json, per-repo profiles).
# scripts/profiles/example.sh is the template, so it ships; every other profile is machine config.
shipped() { { ( cd "$src" && find . -type f ! -name .DS_Store ! -name '*.pyc' ! -name '*.bak*' ! -path '*/__pycache__/*' | sed 's|^\./||' ) \
  | { grep -vxE 'scripts/models\.json' || true; } \
  | { grep -vE '^scripts/profiles/.+\.sh$' || true; }
  [ -f "$src/scripts/profiles/example.sh" ] && printf '%s\n' scripts/profiles/example.sh; } | LC_ALL=C sort -u; }

# The issues a publish closes (#732). The loop-bug stop reads the issue state and nothing else (ADR-0149, one writer per
# fact), so the step that makes a fix live is the step that closes its issue: a published commit carrying a `Closes #n`
# trailer closes #n, with one comment naming the sha and the published time, and the stop lifts on the next pass. A
# TDD RED commit names its issue in the subject and carries no closing trailer, so it closes nothing (ADR-0136). The
# commits are the range the publish makes live: the previously published sha (exclusive) to the source HEAD, or the HEAD
# alone when PUBLISHED is missing or names a commit the source does not hold. --check closes nothing. The repository is
# SG_REPO when the profile sets it, else the source's origin when that is a github.com URL; a source with neither closes
# nothing. Every reach goes through the door (ADR-0277): gh_send is its write half, and it returns 0 only when GitHub
# accepted the write. A close that fails is named and never fails the publish: the live copy already moved.
. "$here/ghcall.sh"
publish_repo() {
  local u
  case "${SG_REPO:-}" in ?*/?*) printf '%s\n' "$SG_REPO"; return 0;; esac
  u=$(git -C "$src" remote get-url origin 2>/dev/null) || return 1
  case "$u" in
    git@github.com:*) u=${u#git@github.com:};;
    ssh://git@github.com/*) u=${u#ssh://git@github.com/};;
    https://github.com/*|http://github.com/*) u=${u#*github.com/};;
    *) return 1;;
  esac
  printf '%s\n' "${u%.git}"
}
closed_issues() { # closed_issues FROM TO: the issue numbers the published commits close, sorted once
  { if [ -n "$1" ] && git -C "$src" cat-file -e "$1^{commit}" 2>/dev/null \
       && git -C "$src" merge-base --is-ancestor "$1" "$2" 2>/dev/null
    then git -C "$src" log --format=%B "$1..$2" 2>/dev/null
    else git -C "$src" log -1 --format=%B "$2" 2>/dev/null; fi; } \
  | /usr/bin/grep -oiE '^[[:space:]]*(close[sd]?|fix(e[sd])?|resolve[sd]?):?[[:space:]]+#[0-9]+([[:space:],]+#[0-9]+)*[[:space:].]*$' \
  | /usr/bin/grep -oE '#[0-9]+' | /usr/bin/grep -oE '[0-9]+' | sort -n -u
}
close_named() { # close_named FROM TO UTC
  local repo n out
  repo=$(publish_repo) || return 0
  for n in $(closed_issues "$1" "$2"); do
    out=$(gh_send -X POST "repos/$repo/issues/$n/comments" -f body="Closed by the publish of $2 at $3." 2>&1) \
      || echo "publish-live: #$n did not take the closing comment: $(printf '%s' "$out" | head -1)"
    if out=$(gh_send -X PATCH "repos/$repo/issues/$n" -f state=closed -f state_reason=completed 2>&1); then
      echo "publish-live: closed #$n in $repo"
    else
      echo "publish-live: #$n did not close: $(printf '%s' "$out" | head -1)"
    fi
  done
}

# The retired files present at the destination, relative to it: the one list the drop and the check both read.
retired() {
  local n s f
  for n in $RETIRED; do
    for f in "$dest/scripts/test-${n%.*}"*.sh "$dest/scripts/$n"; do
      if [ -e "$f" ]; then printf '%s\n' "${f#"$dest"/}"; fi
    done
  done
  for s in $RETIRED_SUITES; do
    if [ -e "$dest/scripts/$s" ]; then printf '%s\n' "scripts/$s"; fi
  done
  for n in $RETIRED_DATA; do
    if [ -e "$dest/$n" ]; then printf '%s\n' "$n"; fi
  done
}
# The publish claims only what arrived: every shipped file reads back with the same bytes and, when the source is
# executable, the executable bit; no retired file is left. A mismatch is a failed publish (#1379).
verify() {
  local f rc=0
  for f in $(shipped); do
    if ! cmp -s "$src/$f" "$dest/$f"; then echo "publish-live: mismatch $f: missing or other bytes at $dest"; rc=1
    elif [ -x "$src/$f" ] && [ ! -x "$dest/$f" ]; then echo "publish-live: mismatch $f: executable bit missing at $dest"; rc=1; fi
  done
  for f in $(retired); do echo "publish-live: mismatch $f: retired file still at $dest"; rc=1; done
  return $rc
}

# An open loop-bug:publish holds every sha but the reviewed sha its issue names (#1980). The hold clears when the publish below
# closes that issue, which happens only after the copy verifies. An unreadable issue list refuses, since no read proves no hold.
# No known repository means no issue to read, as no close can name one.
if [ -z "$check" ]; then
  hold_repo=$(publish_repo 2>/dev/null) || hold_repo=""
  if [ -n "$hold_repo" ]; then
    hold_sha=$(git -C "$src" rev-parse HEAD 2>/dev/null)
    hold_issues=$(gh_aggregate "repos/$hold_repo/issues?state=open") || { echo "publish-live: refused: the open issues of $hold_repo could not be read, so no publish hold is ruled out" >&2; exit 3; }
    hold_n=$(printf '%s' "$hold_issues" | python3 "$here/drainpolicy.py" publish "$hold_sha" 2>/dev/null); hold_rc=$?
    case $hold_rc in
      0) echo "publish-live: refused: #$hold_n holds the publish and names no reviewed sha $hold_sha" >&2; exit 3;;
      1) ;;
      *) echo "publish-live: refused: the open issues of $hold_repo could not be read, so no publish hold is ruled out" >&2; exit 3;;
    esac
  fi
fi
sha=$(git -C "$src" rev-parse HEAD 2>/dev/null)
rc=0
for f in $(shipped); do
  if [ -f "$dest/$f" ] && cmp -s "$src/$f" "$dest/$f" && { [ ! -x "$src/$f" ] || [ -x "$dest/$f" ]; }; then continue; fi
  if [ -n "$check" ]; then echo "copy $f"; rc=1; continue; fi
  mkdir -p "$dest/$(dirname "$f")" && cp -p "$src/$f" "$dest/$f.new.$$" && mv "$dest/$f.new.$$" "$dest/$f" || rm -f "$dest/$f.new.$$"   # new inode: a running copy keeps reading the old one; a failed chain leaves no temp behind
done
for f in $(retired); do
  if [ -n "$check" ]; then echo "drop $f"; rc=1; continue; fi
  rm -f "$dest/$f"; echo "publish-live: dropped $f"
done
[ -n "$check" ] || verify || { echo "publish-live: the destination does not hold the shipped files; the publish failed and nothing is recorded" >&2; exit 1; }
prev=$(cut -d' ' -f1 "$dest/PUBLISHED" 2>/dev/null)
if [ -n "$sha" ] && [ "$prev" != "$sha" ]; then
  if [ -n "$check" ]; then echo "publish $sha"; rc=1
  else
    utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    close_named "$prev" "$sha" "$utc"
    printf '%s %s\n' "$sha" "$utc" > "$dest/PUBLISHED.new.$$" && mv "$dest/PUBLISHED.new.$$" "$dest/PUBLISHED"
  fi
fi
[ -n "$check" ] && exit $rc
if [ -z "$nocompare" ] && [ -f "$here/models_chains.py" ]; then   # report only: the live models.json is never written here
  python3 "$here/models_chains.py" diff "$src/scripts/models.json" "$dest/scripts/models.json" 2>/dev/null | sed 's/^/publish-live: /'
fi
echo "publish-live: $(shipped | wc -l | tr -d ' ') file(s) published to $dest, retired names dropped by name"
