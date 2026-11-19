#!/bin/bash
# seconds: 1.24
# #865 (janitor DUP): two live runs accept one issue. The older run keeps it; the newer run's process tree is killed and the pass says so.
# Before #865 the pass named both and killed neither ("not killed"), so two sessions worked one issue for as long as both stood.
# A dry run kills nothing. Stubbed gh and board; a temp state dir; live runs are sleepers whose cwd sits in their run folder.
# usage: bash test-janitor-dup.sh [janitor.py]
export SG_TAKEOVER_SECONDS=1000000000000   # these runs test other rules; their fixed start stamps are past the takeover bound (ADR-0166)
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
cp "$(dirname "$src")/issue_ledger.py" "$T/s/issue_ledger.py"
printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"; export PATH="$T/bin:$PATH"
OLD=run-20261029-124510-af3898; NEW=run-20261029-124527-5f71a1
# A live run: a request for #133, a record that accepted it, and a sleeper with squad.cli in its argv and its cwd in the run folder.
live() {
  mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"
  printf 'CHILD — GitHub issue(s) #133 (bug; one PR):\n' > "$T/co/runs/$1/request.txt"
  (cd "$T/co/runs/$1" && exec sh -c 'sleep 120; :' squad.cli) </dev/null >/dev/null 2>&1 & p=$!   # no stdout: a sleeper that outlives the test must not hold its pipe
  printf '%s\n' '{"event": "run_start", "run_id": "r", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": ["main"], "pos": 1, "ts": 1}' \
    '{"event": "spawn_accepted", "text": "Fix GitHub issue 133 (Refs #133)"}' > "$T/co/runs/$1/record.jsonl"
  eval "$2=$p"
}
live "$OLD" OPID; live "$NEW" NPID; sleep 0.5
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" "$@" python3 -I "$T/s/janitor.py" 2>&1; }
state() { ps -o state= -p "$1" 2>/dev/null | tr -d ' '; }
dead() { case "$(state "$1")" in Z*|"") return 0;; *) return 1;; esac; }
o=$(pass env JANITOR_DRY=1)
dead "$NPID" && ko "a dry run killed the newer run's process" || ok "a dry run kills nothing"
echo "$o" | /usr/bin/grep -q "DUP" && ok "the pass reports the duplicate" || ko "no DUP line: $o"
o=$(pass env)
dead "$NPID" && ok "the newer run's process tree is killed" || ko "the newer run survives the DUP: $(state "$NPID")"
kill -0 "$OPID" 2>/dev/null && ok "the older run keeps running" || ko "the older run was killed"
/usr/bin/grep -q "DUP issue #133 .*keeping $OLD, killing $NEW" "$T/st/cycle.log" && ok "the pass names the run it keeps and the one it kills" || ko "no keep/kill detail: $(/usr/bin/grep DUP "$T/st/cycle.log")"
/usr/bin/grep -q "not killed" "$T/st/cycle.log" && ko "the pass still says 'not killed'" || ok "the pass no longer says 'not killed'"
/usr/bin/grep -q "kill pid=.*duplicate of run $OLD" "$T/st/cycle.log" && ok "each kill is reported with its reason" || ko "no kill line for the duplicate: $(/usr/bin/grep 'kill pid' "$T/st/cycle.log")"
for q in "$OPID" "$NPID"; do pkill -P "$q" 2>/dev/null; kill "$q" 2>/dev/null; done; wait 2>/dev/null
rm -rf "$T"; exit $fail
