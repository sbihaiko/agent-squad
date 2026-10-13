#!/bin/bash
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# usage: publish-landing.sh   A green landing that changed what the loop runs publishes it in one step (#31): the live skill follows the
# fork (refresh-from-fork.sh), and the view restarts (hubctl.sh --restart, which prints its URL; an open page reloads on the new
# /api/version) only when a file it serves changed: scripts/hub.py, hubmux.py, panes.js, address.sh. Run by watch.sh after forkclose.sh.
# Once per landing and idempotent: the work is what refresh-from-fork.sh --check names, so a second run finds nothing and prints nothing.
# Waits (exit 0, nothing changed, said once per head): a landing pass is mid-flight (the step holds forkclose's own lock while it
# runs, so no pass starts under it), or the fork head is not recorded green (a $SG_STATE/auto/basetest-<sha> whose tree is the head's).
# Refuses (exit 1, nothing changed, named every run) when the live skill holds a file the fork does not -- SKILL.md or a scripts/ file
# outside models.json and the generated/backup names -- or edits the fork lacks (refresh-from-fork.sh refuses those): that drift is what
# separated the running loop from the repository, and a refresh must never paper over it. Bring the work over with sync-to-fork.sh.
# A view restart that failed stays pending ($SG_STATE/auto/publish-view-pending) and is retried by the next run.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"; live="$(cd "$here/.." && pwd)"
F=${SG_SQUAD_FORK:-$HOME/VSCodeProjects/agent-squad}; br=${SG_FORK_BRANCH:-main}; st=${SG_STATE:-$SG_STATE}; A="$st/auto"
G="/usr/bin/git -C $F"; refresh="$here/refresh-from-fork.sh"; pending="$A/publish-view-pending"
VIEW="scripts/hub.py scripts/hubmux.py scripts/panes.js scripts/address.sh"
head=$($G rev-parse --verify -q "$br^{commit}") || { echo "PUBLISH REFUSED: $br is not a branch of $F"; exit 1; }; h9=${head:0:9}
once() { local m="$A/publish-wait-$h9-$1"; [ -e "$m" ] || { touch "$m"; echo "PUBLISH WAIT $h9: $2"; }; }   # a wait is said once per head, a refusal every run
mkdir -p "$A"
out=$(bash "$refresh" --check 2>&1); rc=$?
case $rc in 0) diverged="";; 1) diverged=$(printf '%s\n' "$out" | sed 1d);; *) echo "PUBLISH REFUSED: refresh-from-fork.sh --check failed: $out"; exit 1;; esac
[ -z "$diverged" ] && [ ! -e "$pending" ] && exit 0   # the live skill already follows the fork and the view is current: nothing landed

L="$A/forkclose.lock"   # the landing pass's lock: held by a live pid, a pass is mid-flight; held by the step, no pass can start
if ! mkdir "$L" 2>/dev/null; then
  p=$(cat "$L/pid" 2>/dev/null)
  if { [ -n "$p" ] && kill -0 "$p" 2>/dev/null; } || { [ -z "$p" ] && [ $(( $(date +%s) - $(mtime "$L") )) -le 10 ]; }; then
    once midflight "a landing pass is mid-flight (pid ${p:-unknown}); nothing is published until it ends"; exit 0; fi
  rm -rf "$L"; mkdir "$L" 2>/dev/null || { once midflight "a landing pass started; nothing is published until it ends"; exit 0; }
fi
echo $$ > "$L/pid"; trap 'rm -rf "$L"' EXIT

if [ -n "$diverged" ]; then
  tree=$($G rev-parse "$head^{tree}"); green=
  for f in "$A"/basetest-*; do [ -e "$f" ] || continue; s=${f##*/basetest-}; case "$s" in *[!0-9a-f]*|"") continue;; esac
    [ "$($G rev-parse --verify -q "$s^{tree}" 2>/dev/null)" = "$tree" ] && { green=1; break; }; done
  [ -n "$green" ] || { once green "$br is not recorded green (no basetest-<sha> with its tree under $A); the live skill stays as it is"; exit 0; }
  tracked=$($G ls-tree -r --name-only "$head" contrib/squad-goal | sed 's|^contrib/squad-goal/||')
  liveonly=$(cd "$live" && { [ -f SKILL.md ] && echo SKILL.md; find scripts -maxdepth 1 -type f ! -name .DS_Store ! -name '*.pyc' ! -name '*.bak*' ! -name models.json | sort; } \
    | while read -r f; do printf '%s\n' "$tracked" | grep -qxF "$f" || echo "$f"; done)
  if [ -n "$liveonly" ]; then
    echo "PUBLISH REFUSED $h9: the live skill holds files $br does not; nothing was changed (bring them over with sync-to-fork.sh, or remove them):"; echo "$liveonly"; exit 1; fi
  r=$(bash "$refresh" 2>&1) || { echo "PUBLISH REFUSED $h9: $r"; exit 1; }
  r=$(bash "$refresh" --check 2>&1) || { echo "PUBLISH FAILED $h9: the live skill still differs from $br after the refresh:"; echo "$r"; exit 1; }
  for f in $VIEW; do printf '%s\n' "$diverged" | grep -qxF "$f" && { touch "$pending"; break; }; done
  echo "PUBLISH $h9: the live skill follows $br ($(printf '%s\n' "$diverged" | wc -l | tr -d ' ') files)"
  mkdir -p "$st"; echo "$head" > "$st/published-head"
fi
if [ -e "$pending" ]; then
  r=$(bash "$here/hubctl.sh" --restart ${SG_HUB_PORT:-} 2>&1) && { rm -f "$pending"; echo "PUBLISH VIEW restarted: $r"; } \
    || { echo "PUBLISH VIEW restart failed (retried next run): $r"; exit 1; }
fi
