#!/bin/bash
# usage: mkreq.sh <name> <body-file>   writes ~/.cache/squad-goal/req/<name>.txt = body + pre-push checklist
set -e
# The model this request names is the first coding_models entry whose `hours` window contains the current local hour (an
# absent window always matches), the same rule launch.py enforces: naming entry [0] blindly would ask for a model the hours
# gate is about to refuse, or quietly use an Anthropic model during the day.
mjson="$(cd "$(dirname "$0")" && pwd)/models.json"; hh=$((10#$(date +%H)))
inwin() { local w; w=$(jq -r --arg m "$1" '.hours.coding_models[$m] // empty' "$mjson"); [ -n "$w" ] || return 0
  local a=$((10#${w%%-*})) b=$((10#${w##*-}))
  if [ "$a" -le "$b" ]; then [ "$hh" -ge "$a" ] && [ "$hh" -lt "$b" ]; else [ "$hh" -ge "$a" ] || [ "$hh" -lt "$b" ]; fi; }
cm=""; fb=""
for m in $(jq -r '.coding_models[]' "$mjson"); do inwin "$m" || continue
  if [ -z "$cm" ]; then cm=$m; elif [ -z "$fb" ]; then fb=$m; fi; done
[ -n "$cm" ] || cm=$(jq -r '.coding_models[0] // empty' "$mjson")   # every entry out of window: name [0], launch.py refuses loudly
[ -n "$fb" ] || fb=$(jq -r --arg c "$cm" 'first(.coding_models[]|select(.!=$c)) // empty' "$mjson")
[ $# -eq 2 ] || { echo "usage: mkreq.sh <name> <body-file>" >&2; exit 2; }
here="$(cd "$(dirname "$0")" && pwd)"; out="$HOME/.cache/squad-goal/req/$1.txt"
grep -q 'IMPORTANT: your first and only action' "$2" || { echo "refusing: body lacks the 'IMPORTANT: your first and only action' line" >&2; exit 1; }
# A path a body can claim: the trees a child edits, the fork's own tree included (contrib/squad-goal/..., dashboard/...), which
# the old UI|UI.<x>|Core|scripts|docs|tests list was blind to -- a fork-only child's slice lives there and matched nothing.
SLICE_RE='(^|[^A-Za-z0-9_./-])(UI|UI\.[A-Za-z]+|Core|scripts|docs|tests|contrib/squad-goal|dashboard)/[A-Za-z0-9_./-]+'
slice_paths() { grep -oE "$SLICE_RE" "$1" 2>/dev/null | sed -E 's#^[^A-Za-z0-9_/.-]+##' | sort -u; }
# Slice guard. Two requests that name the same file both edit it, and whichever merges second conflicts with the first: the
# sibling pair #1285/#1391 shared scripts/gui_test/mesen_gui_adapter.py and UI/Logic/TestHook/TestHookProtocol.cs, and #1285
# spent a whole review plus a conflict round on it; gui/1497, gui/1498 and gui/1502 each landed a branch on
# contrib/squad-goal/scripts/hub.py and the fork merge stalled. A request already names the files it will touch, so every slice
# in flight is checkable before anything is launched. A body that names a file already in flight is REFUSED: non-zero exit,
# nothing written, a message naming the file and its holder. In flight means any of three things: an open PR that changes the
# file; a queued request of any panel that claims it -- the same-panel sibling case of ADR-0274 (#1400) is the narrower half of
# that rule, and an overlapping pair of that panel still says so; or an unmerged gui/* branch in the fork checkout, which is the
# only place a fork-only child's slice is visible at all, since it never opens a PR. SG_SLICE_FORCE=1 is the documented way past
# it: a deliberate overlap stays possible, but it has to be asked for and it is announced on stderr.
slice_guard() {
  [ -n "$(slice_paths "$2")" ] || return 0
  local paths id taken f holder rid hpanel force=0
  [ "${SG_SLICE_FORCE:-}" = 1 ] && force=1
  paths=$(slice_paths "$2")
  id=$(grep -m1 -E '^PANEL: *[^ ]+' "$2" 2>/dev/null | sed -E 's/^PANEL: *//; s/ +$//') || true; id=${id:-${SG_PANEL:-}}
  # env.sh refuses to load without a matching profile, and a launch exports the SG_* values it would have set, so take them from
  # the environment first and ask env.sh only for what is missing.
  local repo=${SG_REPO:-} fdir=${SG_FORK_DIR:-} fbase=${SG_FORK_BASE:-local/next} ev=""
  if [ -z "$repo" ] || [ -z "$fdir" ]; then
    ev=$( ( . "$(cd "$(dirname "$0")" && pwd)/env.sh" >/dev/null 2>&1; printf '%s\n%s\n%s\n' "${SG_REPO:-}" "${SG_FORK_DIR:-}" "${SG_FORK_BASE:-}" ) 2>/dev/null ) || true
    [ -n "$repo" ] || repo=$(printf '%s\n' "$ev" | sed -n 1p)
    [ -n "$fdir" ] || fdir=$(printf '%s\n' "$ev" | sed -n 2p)
    [ -n "${SG_FORK_BASE:-}" ] || fbase=$(printf '%s\n' "$ev" | sed -n 3p); fbase=${fbase:-local/next}
  fi
  # "file <TAB> holder", one line per file in flight; the first holder found for a file is the one reported.
  taken=""
  if [ -n "$repo" ] && command -v gh >/dev/null 2>&1; then
    for p in $(gh api "repos/$repo/pulls?state=open&per_page=100" -q '.[].number' 2>/dev/null || true); do
      for f in $(gh api "repos/$repo/pulls/$p/files?per_page=100" -q '.[].filename' 2>/dev/null || true); do
        taken="$taken$f	PR #$p
"; done; done
  fi
  local q
  for q in "$HOME/.cache/squad-goal/req"/*.txt; do
    [ -e "$q" ] || continue; [ "$q" = "$out" ] && continue; rid=$(basename "$q" .txt)
    for f in $(slice_paths "$q"); do taken="$taken$f	request $rid
"; done
  done
  # A fork-only child commits on gui/<N> off the fork base and opens no PR, so its slice is only visible in the fork checkout.
  local bglob=${SG_FORK_BRANCH:-gui/%n} br; bglob="refs/heads/${bglob%%/%n*}"
  if [ -n "$fdir" ] && [ -d "$fdir" ]; then
    for br in $(/usr/bin/git -C "$fdir" for-each-ref --format='%(refname:short)' "$bglob/*" 2>/dev/null || true); do
      [ -n "$(/usr/bin/git -C "$fdir" rev-list -1 "$fbase..$br" 2>/dev/null)" ] || continue
      for f in $(/usr/bin/git -C "$fdir" diff --name-only "$fbase...$br" 2>/dev/null || true); do
        taken="$taken$f	branch $br
"; done
    done
  fi
  [ -n "$taken" ] || return 0
  for f in $paths; do
    holder=$(printf '%s\n' "$taken" | /usr/bin/awk -F'\t' -v f="$f" '$1==f {print $2; exit}')
    [ -n "$holder" ] || continue
    if [ $force = 1 ]; then echo "SLICE FORCE $1: $f is already in flight ($holder); dispatching anyway on SG_SLICE_FORCE=1" >&2; continue; fi
    case "$holder" in
      "request "*)
        rid=${holder#request }; hpanel=$(grep -m1 -E '^PANEL: *[^ ]+' "$HOME/.cache/squad-goal/req/$rid.txt" 2>/dev/null | sed -E 's/^PANEL: *//; s/ +$//') || true
        echo "refusing: request $1 and request $rid$([ -n "$id" ] && [ "$hpanel" = "$id" ] && echo " (same panel $id)") both name: $f. Re-split the slices or add a '## Blocked by' edge between them (ADR-0274). Set SG_SLICE_FORCE=1 to dispatch anyway." >&2 ;;
      "PR "*)
        echo "refusing: request $1 names $f, which open $holder already changes. Set SG_SLICE_FORCE=1 to dispatch anyway." >&2 ;;
      *)
        echo "refusing: request $1 names $f, which the unmerged fork branch ${holder#branch } touches (that is where a fork-only child's slice lives). Set SG_SLICE_FORCE=1 to dispatch anyway." >&2 ;;
    esac
    return 1
  done
}
slice_guard "$1" "$2" || exit 1
# Fork-only (#1411, the rule of forkonly.sh): the builders write a `FORK-ONLY: all` line when every child is fork-only; a hand-written body
# with no CHILD blocks is fork-only when it carries the "Fork-only work" line. A fork-only request gets the fork DONE CHECK and checklist:
# nothing in it names a PR, a push or prepush.sh.
fork=0; if grep -qE '^FORK-ONLY: all' "$2"; then fork=1; elif ! grep -qE '^CHILD' "$2" && grep -q 'Fork-only work' "$2"; then fork=1; fi
if [ $fork = 1 ]; then
  mkdir -p "$(dirname "$out")"; { cat "$2"; echo; echo "DONE CHECK (the run's final gate runs these; without them the run ends failed with no_ac_commands even when the work is right): end your result with {\"status\": \"pass\", \"ac_commands\": [...]}, one string per check you ran as evidence, each as \"cd <fork worktree> && <command>\" (the gate runs in the fork worktree you committed in). Use the targeted commands (the touched test files, the doc checks when docs changed), not a full suite, and only commands that passed for you."; [ -n "$cm" ] && echo "Coding model: $cm (no [1m] suffix); fall back to $fb. This line wins over any other model named above."; cat "$here/req-checklist-fork.txt"; } > "$out"; echo "$out"; exit 0
fi
mkdir -p "$(dirname "$out")"; { cat "$2"; echo; echo "DONE CHECK (the run's final gate runs these; without them the run ends failed with no_ac_commands even when the work is right): end your result with {\"status\": \"pass\", \"ac_commands\": [...]}, one string per check you ran as evidence. Work pushed to a PR branch: wrap each as \"$here/acpr.sh <branch> -- <command>\" (the gate runs in the main checkout; acpr.sh checks out origin/<branch> in a throwaway worktree first). Work committed only in a local fork: \"cd <fork worktree> && <command>\". Use the targeted commands (the touched test classes, the doc checks when docs changed), not a full suite, and only commands that passed for you."; [ -n "$cm" ] && echo "Coding model: $cm (no [1m] suffix); fall back to $fb. This line wins over any other model named above."; cat "$here/req-checklist.txt"; } > "$out"; echo "$out"
