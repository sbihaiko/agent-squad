#!/bin/bash
# seconds: 1.28
# Rule V reads every `auto/wave-*` marker and strips one of a few prefixes to name its run. `auto/wave-entry-<run>` is the epoch
# the first child that entered the wave wrote (wave.sh, ADR-0145 rule 16), and its name starts with `wave-`: the rule stripped
# that prefix, read `entry-<run>`, found no run by that name and deleted the entry clock of a live run (#2473). `wave-recut-<run>`
# has the same shape. The rule names the run of each known marker and drops the marker of a run no line names and no live
# process works; a listed run's markers stay, and a real `wave-<run>` of an unlisted run is still dropped (#2140).
# A stub `gh`, a stub board, a temp state dir. usage: bash test-janitor-wave-marker.sh [janitor.py]
export SG_TAKEOVER_SECONDS=1000000000000   # the run below tests rule V, not the takeover bound (ADR-0166)
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"; export PATH="$T/bin:$PATH"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" SQUAD_CAPS_DIR="$T/caps" python3 -I "$T/s/janitor.py" 2>&1; }
# a listed run with a folder of its own; its wave is open, so the reap holds its active-runs line.
mkdir -p "$T/co/runs/run-live"
printf 'CHILD — GitHub issue(s) 1001 (bug; one PR):\n' > "$T/co/runs/run-live/request.txt"
printf '%s\n' '{"event": "run_start", "run_id": "run-live", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": ["main"], "pos": 1, "ts": 1}' > "$T/co/runs/run-live/record.jsonl"
echo run-live > "$T/st/active-runs.txt"
now=$(date +%s)
echo "wave/run-live" > "$T/st/auto/wave-run-live"
echo pass > "$T/st/auto/wave-recut-run-live"
printf '%s\n' "$now" > "$T/st/auto/wave-entry-run-live"
printf '%s\n' "$(( now + 7200 ))" > "$T/st/auto/wave-ceiling-run-live"
# an unlisted run: no line names it and no folder works it, so its wave can never land.
echo "wave/run-old" > "$T/st/auto/wave-run-old"
printf '%s\n' "$(( now + 7200 ))" > "$T/st/auto/wave-ceiling-run-old"
o=$(pass)
[ -e "$T/st/auto/wave-entry-run-live" ] && ok "the entry clock of a run the listing names stays" || ko "the entry clock of a live run went: $o"
[ -e "$T/st/auto/wave-recut-run-live" ] && ok "the recut marker of a run the listing names stays" || ko "the recut marker of a live run went: $o"
[ -e "$T/st/auto/wave-run-live" ] && ok "the wave of a listed run stays" || ko "the wave of a live run went: $o"
[ -e "$T/st/auto/wave-ceiling-run-live" ] && ok "the ceiling of a run the listing names stays" || ko "the ceiling of a live run went: $o"
[ ! -e "$T/st/auto/wave-run-old" ] && ok "the wave marker of an unlisted run is dropped" || ko "the wave marker of an unlisted run stayed: $o"
[ ! -e "$T/st/auto/wave-ceiling-run-old" ] && ok "the ceiling of an unlisted run is dropped with its wave" || ko "the ceiling of an unlisted run stayed: $o"
/usr/bin/grep -q 'WAVE-DROPPED wave-run-old' "$T/st/cycle.log" && ok "the pass names the marker it dropped" || ko "the drop is unnamed: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q 'WAVE-DROPPED wave-ceiling-run-old' "$T/st/cycle.log" && ok "the pass names the ceiling it dropped" || ko "the ceiling drop is unnamed: $(cat "$T/st/cycle.log" 2>/dev/null)"
exit $fail
