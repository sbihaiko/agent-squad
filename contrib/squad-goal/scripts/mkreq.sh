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
# Slice guard. Two children of one panel that name the same file both edit it, and whichever merges second conflicts with the
# first: the sibling pair #1285/#1391 shared scripts/gui_test/mesen_gui_adapter.py and UI/Logic/TestHook/TestHookProtocol.cs,
# and #1285 spent a whole review plus a conflict round on it. A request already names the files it will touch, so a panel's
# slices are checkable before anything is launched. Warn (never block) when this body names a file an open PR is already
# changing, or that another queued request already claims.
slice_guard() {
  local paths taken hit r
  paths=$(grep -oE '(^|[^A-Za-z0-9_./-])(UI|UI\.[A-Za-z]+|Core|scripts|docs|tests)/[A-Za-z0-9_./-]+' "$2" 2>/dev/null | sed -E 's#^[^A-Za-z0-9_/.-]+##' | sort -u) || true
  [ -n "$paths" ] || return 0
  # env.sh refuses to load without a matching profile, so it runs in a subshell: no profile just means "no open-PR half"
  taken=$( ( . "$(cd "$(dirname "$0")" && pwd)/env.sh" 2>/dev/null
    for p in $(gh api "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[].number' 2>/dev/null); do
      gh api "repos/$SG_REPO/pulls/$p/files?per_page=100" -q '.[].filename' 2>/dev/null; done ) 2>/dev/null ) || true
  for r in "$HOME/.cache/squad-goal/req"/*.txt; do
    [ -e "$r" ] || continue; [ "$r" = "$out" ] && continue
    taken="$taken$(grep -oE '(^|[^A-Za-z0-9_./-])(UI|UI\.[A-Za-z]+|Core|scripts|docs|tests)/[A-Za-z0-9_./-]+' "$r" 2>/dev/null | sed -E 's#^[^A-Za-z0-9_/.-]+##' | sort -u)
"
  done
  hit=$(comm -12 <(printf '%s\n' "$paths") <(printf '%s\n' "$taken" | grep . | sort -u) | grep .) || true
  if [ -n "$hit" ]; then
    { echo; echo "SLICE NOTE (mkreq.sh): an open PR or another queued request already names $(printf '%s ' $hit). A panel's children are meant to hold disjoint slices, so if this request is one of them, change only what its own slice needs -- a file two children both edit is a conflict for whoever merges second (#1285 against #1391)."; } >> "$out"
    echo "SLICE WARNING $1: $(printf '%s ' $hit) is also named by an open PR or a queued request" >&2
  fi
}
mkdir -p "$(dirname "$out")"; { cat "$2"; echo; echo "DONE CHECK (the run's final gate runs these; without them the run ends failed with no_ac_commands even when the work is right): end your result with {\"status\": \"pass\", \"ac_commands\": [...]}, one string per check you ran as evidence. Work pushed to a PR branch: wrap each as \"$here/acpr.sh <branch> -- <command>\" (the gate runs in the main checkout; acpr.sh checks out origin/<branch> in a throwaway worktree first). Work committed only in a local fork: \"cd <fork worktree> && <command>\". Use the targeted commands (the touched test classes, the doc checks when docs changed), not a full suite, and only commands that passed for you."; [ -n "$cm" ] && echo "Coding model: $cm (no [1m] suffix); fall back to $fb. This line wins over any other model named above."; cat "$here/req-checklist.txt"; } > "$out"; slice_guard "$1" "$2"; echo "$out"
