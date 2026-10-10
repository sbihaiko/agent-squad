#!/bin/bash
# usage: mkreq.sh <name> <body-file>   writes ~/.cache/squad-goal/req/<name>.txt = body + pre-push checklist
set -e
cm=$(jq -r ".coding_models[0] // empty" "$(cd "$(dirname "$0")" && pwd)/models.json"); fb=$(jq -r ".coding_models[1] // empty" "$(cd "$(dirname "$0")" && pwd)/models.json")
[ $# -eq 2 ] || { echo "usage: mkreq.sh <name> <body-file>" >&2; exit 2; }
here="$(cd "$(dirname "$0")" && pwd)"; out="$HOME/.cache/squad-goal/req/$1.txt"
grep -q 'IMPORTANT: your first and only action' "$2" || { echo "refusing: body lacks the 'IMPORTANT: your first and only action' line" >&2; exit 1; }
mkdir -p "$(dirname "$out")"; { cat "$2"; echo; [ -n "$cm" ] && echo "Coding model: $cm (no [1m] suffix); fall back to $fb. This line wins over any other model named above."; cat "$here/req-checklist.txt"; } > "$out"; echo "$out"
