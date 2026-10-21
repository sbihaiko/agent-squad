#!/bin/bash
# Rule C: a zombie run (no verdict, record idle, no process inside its folder, no live call) is dropped from active-runs.txt, and it gets the killed
# verdict in its own record (squad.killed, the one writer). Without it the run read `stalled` in the hub for ever and held a footer slot.
# A dry run writes nothing. Stubbed gh and board; a temp state dir. usage: bash test-janitor-killed.sh [janitor.py]
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s (bug; one PR):\n' "$2" > "$T/co/runs/$1/request.txt"
  printf '%s\n' '{"event": "run_start", "run_id": "r", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": ["main"], "pos": 1, "ts": 1}' > "$T/co/runs/$1/record.jsonl"
  touch -t 202001010000 "$T/co/runs/$1" "$T/co/runs/$1/record.jsonl" "$T/co/runs/$1/request.txt"; }
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" "$@" python3 -I "$T/s/janitor.py" 2>&1; }
last() { tail -1 "$T/co/runs/$1/record.jsonl"; }
mk run-zombie 1001
o=$(pass env JANITOR_DRY=1); echo "$o" | /usr/bin/grep -q 'zombie run run-zombie' && ok "a dry run reports the zombie" || ko "dry run: $o"
last run-zombie | /usr/bin/grep -q '"event": "verdict"' && ko "a dry run wrote a verdict" || ok "a dry run writes no verdict"
o=$(pass env); /usr/bin/grep -q 'run-zombie' "$T/st/active-runs.txt" && ko "the zombie stays in active-runs: $o" || ok "the zombie is dropped from active-runs"
last run-zombie | /usr/bin/grep -q '"verdict": "failed".*"reasons": \["killed/no-process"\]' && ok "the zombie's record ends in the killed verdict" || ko "no killed verdict: $(last run-zombie)"
# #745: a helper the session left behind (uv, caffeinate -- any process with a cwd inside the run folder) is not a live run.
# The run's own session is; a helper is reaped with the zombie, or it keeps the run "alive" for ever.
mk run-helper 1002
( cd "$T/co/runs/run-helper" && exec sleep 60 ) & hp=$!
sleep 0.3
o=$(pass env)
/usr/bin/grep -q 'run-helper' "$T/st/active-runs.txt" && ko "a leftover helper keeps the run alive: $o" || ok "a leftover helper does not keep a zombie alive"
st=$(ps -o state= -p "$hp" 2>/dev/null | tr -d ' ')   # a killed child is a zombie until the shell reaps it, so the state decides
case "$st" in Z*|"") ok "the zombie's leftover helper is killed with it";; *) ko "the zombie's leftover helper survives the pass (state $st)";; esac
wait "$hp" 2>/dev/null

exit $fail
