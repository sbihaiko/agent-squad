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
mkdir -p "$(dirname "$out")"; { cat "$2"; echo; echo "DONE CHECK (the run's final gate runs these; without them the run ends failed with no_ac_commands even when the work is right): end your result with {\"status\": \"pass\", \"ac_commands\": [...]}, one string per check you ran as evidence. Work pushed to a PR branch: wrap each as \"$here/acpr.sh <branch> -- <command>\" (the gate runs in the main checkout; acpr.sh checks out origin/<branch> in a throwaway worktree first). Work committed only in a local fork: \"cd <fork worktree> && <command>\". Use the targeted commands (the touched test classes, the doc checks when docs changed), not a full suite, and only commands that passed for you."; [ -n "$cm" ] && echo "Coding model: $cm (no [1m] suffix); fall back to $fb. This line wins over any other model named above."; cat "$here/req-checklist.txt"; } > "$out"; echo "$out"
