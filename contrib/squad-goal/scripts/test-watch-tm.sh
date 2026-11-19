#!/usr/bin/env bash
# seconds: 0.11
# tm logs a timed step by the step it runs: `bash single.sh <name>` by <name>, `bash <script>` and `<script>` by the script name (#1745).
S="$(cd "$(dirname "$0")" && pwd)"
fail=0
ok() { echo "ok   $1"; }
ko() { echo "FAIL $1"; fail=1; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export SG_STATE=$T/state CLK=$T/clock; mkdir -p "$SG_STATE" "$T/bin"
echo 1000 > "$CLK"
# the fake clock: date +%s reads it, and each stub step advances it by its own duration
date() { if [ "$1" = +%s ]; then cat "$CLK"; else command date "$@"; fi; }
orphaned() { false; }
. "$S/steprec.sh"   # the timer writes the step's dated record as it runs (#1750), so its fixture loads the writer
eval "$(/usr/bin/grep '^tm() ' "$S/watch.sh")"
step() { printf '#!/bin/bash\necho $(( $(cat "%s") + %s )) > "%s"\nexit %s\n' "$CLK" "$2" "$CLK" "${3:-0}" > "$1"; chmod +x "$1"; }
step "$T/bin/single.sh" 7
step "$T/bin/janitor.sh" 7
step "$T/bin/short.sh" 3
step "$T/bin/held.sh" 7 76

last() { tail -n 1 "$SG_STATE/cycle.log" 2>/dev/null; }
reset() { : > "$SG_STATE/cycle.log"; echo 1000 > "$CLK"; }

reset; tm bash "$T/bin/single.sh" promote; rc=$?
[[ "$(last)" =~ ^[0-9]{2}:[0-9]{2}:[0-9]{2}\ promote\ 7s$ ]] \
  && ok "1 bash single.sh <name> is logged by <name>" || ko "1 bash single.sh logged as: '$(last)'"

reset; tm bash "$T/bin/janitor.sh"
[[ "$(last)" =~ ^[0-9]{2}:[0-9]{2}:[0-9]{2}\ janitor\.sh\ 7s$ ]] \
  && ok "2 bash <script> is logged by the script name" || ko "2 bash <script> logged as: '$(last)'"

reset; tm "$T/bin/janitor.sh"
[[ "$(last)" =~ ^[0-9]{2}:[0-9]{2}:[0-9]{2}\ janitor\.sh\ 7s$ ]] \
  && ok "3 bare <script> is logged by the script name" || ko "3 bare <script> logged as: '$(last)'"

reset; tm bash "$T/bin/short.sh"
[ ! -s "$SG_STATE/cycle.log" ] && ok "4 a step under 5 s writes no line" || ko "4 a short step wrote: '$(last)'"

reset; tm bash "$T/bin/held.sh"; rc=$?
[ "$rc" = 76 ] && [[ "$(last)" =~ ^[0-9]{2}:[0-9]{2}:[0-9]{2}\ held\.sh\ 7s$ ]] \
  && ok "5 the step's exit code comes back unchanged and its line names held.sh" || ko "5 rc=$rc, logged as: '$(last)'"

exit $fail
