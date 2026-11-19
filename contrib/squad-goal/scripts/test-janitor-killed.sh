#!/bin/bash
# seconds: 0.85
# Rule C: a zombie run (no verdict, record idle, no process inside its folder, no live call) gets the killed verdict in its own
# record (squad.killed, the one writer) and leaves active-runs.txt on the same pass that frees its leases, so the listing and the
# leases agree in one sample (#1854); the log names the removal. An open wave holds its line until the wave lands. Without the
# verdict the run read `stalled` in the hub for ever and held a footer slot.
# A dry run writes nothing. Stubbed gh and board; a temp state dir. usage: bash test-janitor-killed.sh [janitor.py]
export SG_TAKEOVER_SECONDS=1000000000000   # these runs test other rules; their fixed start stamps are past the takeover bound (ADR-0166)
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"; export PATH="$T/bin:$PATH"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s (bug; one PR):\n' "$2" > "$T/co/runs/$1/request.txt"
  printf '%s\n' '{"event": "run_start", "run_id": "r", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": ["main"], "pos": 1, "ts": 1}' > "$T/co/runs/$1/record.jsonl"
  touch -t 202001010000 "$T/co/runs/$1" "$T/co/runs/$1/record.jsonl" "$T/co/runs/$1/request.txt"; }
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" SQUAD_CAPS_DIR="$T/caps" "$@" python3 -I "$T/s/janitor.py" 2>&1; }
last() { tail -1 "$T/co/runs/$1/record.jsonl"; }
mk run-zombie 1001
o=$(pass env JANITOR_DRY=1); echo "$o" | /usr/bin/grep -q 'zombie run run-zombie' && ok "a dry run reports the zombie" || ko "dry run: $o"
last run-zombie | /usr/bin/grep -q '"event": "verdict"' && ko "a dry run wrote a verdict" || ok "a dry run writes no verdict"
o=$(pass env); /usr/bin/grep -qx 'run-zombie' "$T/st/active-runs.txt" && ko "the zombie kept its active-runs line: $o" || ok "the zombie leaves active-runs on the pass that reaped it (#1854)"
last run-zombie | /usr/bin/grep -q '"verdict": "failed".*"reasons": \["killed/no-process"\]' && ok "the zombie's record ends in the killed verdict" || ko "no killed verdict: $(last run-zombie)"
# #745: a helper the session left behind (uv, caffeinate -- any process with a cwd inside the run folder) is not a live run.
# The run's own session is; a helper is reaped with the zombie, or it keeps the run "alive" for ever.
mk run-helper 1002
( cd "$T/co/runs/run-helper" && exec sleep 60 ) & hp=$!
sleep 0.3
o=$(pass env)
last run-helper | /usr/bin/grep -q '"reasons": \["killed/no-process"\]' && ok "a leftover helper does not keep a zombie alive" || ko "a leftover helper keeps the run alive: $o"
st=$(ps -o state= -p "$hp" 2>/dev/null | tr -d ' ')   # a killed child is a zombie until the shell reaps it, so the state decides
case "$st" in Z*|"") ok "the zombie's leftover helper is killed with it";; *) ko "the zombie's leftover helper survives the pass (state $st)";; esac
wait "$hp" 2>/dev/null
# #1854: a run that dies by kill -9 (no verdict of its own, no marker) leaves active-runs.txt on the same pass that frees its
# lease, so the listing and the leases agree in one sample, and the reap names the line it removed in the cycle log.
mk run-killed 1003
mkdir -p "$T/caps/runs" "$T/caps/children"
sleep 60 & kp=$!
printf '{"event": "call_start", "pid": %s, "started_at": %s}\n' "$kp" "$(date +%s)" >> "$T/co/runs/run-killed/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-killed/record.jsonl"
printf '{"holder": "run-killed", "pid": %s, "since": 1}\n' "$kp" > "$T/caps/runs/run-killed.json"
kill -9 "$kp"; wait "$kp" 2>/dev/null
o=$(pass env)
[ ! -e "$T/caps/runs/run-killed.json" ] && ok "#1854: the killed run's lease is freed" || ko "#1854: the killed run's lease is kept: $o"
/usr/bin/grep -qx 'run-killed' "$T/st/active-runs.txt" && ko "#1854: a run gone from the leases kept its active-runs line" || ok "#1854: the listing drops the run on the pass that frees its lease"
/usr/bin/grep -q 'zombie run run-killed .*dropped from active-runs' "$T/st/cycle.log" && ok "#1854: the reap names the line it removed" || ko "#1854: the cycle log names no removal: $(cat "$T/st/cycle.log")"
# A reaped run whose wave is still open keeps its line: childcc enters its children from the listing until the wave lands.
mk run-wave 1004
: > "$T/st/auto/wave-run-wave"
o=$(pass env)
/usr/bin/grep -qx 'run-wave' "$T/st/active-runs.txt" && ok "#1854: an open wave holds the reaped run's line" || ko "#1854: an open wave lost its line: $o"
# A run the health pass's reap already ended (the killed verdict is in its record and no process is left) is gone from the
# leases too: its line leaves on the janitor's pass, and the line says so. A live lease still naming it holds the line.
mk run-reaped 1005
printf '%s\n' '{"event": "verdict", "verdict": "failed", "reasons": ["killed/no-process"], "exit_code": 1}' >> "$T/co/runs/run-reaped/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-reaped/record.jsonl"
o=$(pass env)
/usr/bin/grep -qx 'run-reaped' "$T/st/active-runs.txt" && ko "#1854: a reap-ended run kept its active-runs line" || ok "#1854: a reap-ended run leaves active-runs"
/usr/bin/grep -q 'killed run run-reaped .*dropped from active-runs' "$T/st/cycle.log" && ok "#1854: the reap-ended run's removal is named" || ko "#1854: no removal named: $(cat "$T/st/cycle.log")"
mk run-leased 1006
printf '%s\n' '{"event": "verdict", "verdict": "failed", "reasons": ["killed/no-process"], "exit_code": 1}' >> "$T/co/runs/run-leased/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-leased/record.jsonl"
printf '{"holder": "run-leased:route.router.1", "pid": %s, "since": 1}\n' "$$" > "$T/caps/children/run-leased_route.router.1.json"
o=$(pass env)
/usr/bin/grep -qx 'run-leased' "$T/st/active-runs.txt" && ok "#1854: a live lease holds a reap-ended run's line" || ko "#1854: a live lease lost its line: $o"
rm -f "$T/caps/children/run-leased_route.router.1.json"

exit $fail
