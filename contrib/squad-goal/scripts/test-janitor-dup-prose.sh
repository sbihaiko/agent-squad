#!/bin/bash
# seconds: 2.5
# #3042 (janitor DUP): a run holds an issue only when its request's CHILD header names it. A child prompt that cites another
# issue in prose ("A issue #4 (relatório manual) is open") made rule DUP read two runs on #4 and kill the newer one, and the
# card pass moved #4 to Doing. Three cases: two runs that cite #4 in prose are no duplicate and hold no #4 card; two runs
# whose headers both name #9 are still a duplicate; an accept line with no Refs keeps its header issue and drops the prose one.
# Stubbed gh and board; a temp state dir; live runs are sleepers whose cwd sits in their run folder.
# usage: bash test-janitor-dup-prose.sh [janitor.py]
export SG_TAKEOVER_SECONDS=1000000000000   # fixed start stamps sit past the takeover bound (ADR-0166)
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
pids=()
setup() {
  T=$(mktemp -d)
  mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"
  for x in issue_state.py board_columns.py board-definition.json issue_ledger.py; do cp "$(dirname "$src")/$x" "$T/s/"; done
  printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"
}
# live <run> <header issue> <accept text>: a request whose CHILD header names one issue, a record whose accept line carries the text.
live() {
  mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"
  printf 'Context: issue #4 (manual spike report) is open.\nCHILD — GitHub issue(s) #%s (one branch):\n' "$2" > "$T/co/runs/$1/request.txt"
  (cd "$T/co/runs/$1" && exec sh -c 'sleep 120; :' squad.cli) </dev/null >/dev/null 2>&1 & pids+=($!)
  python3 - "$T/co/runs/$1/record.jsonl" "$3" <<'EOF'
import json, sys
open(sys.argv[1], "w").write("\n".join(json.dumps(x) for x in [
    {"event": "run_start", "run_id": "r", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": ["main"], "pos": 1, "ts": 1},
    {"event": "spawn_accepted", "child": "route.router.1", "definition": sys.argv[2], "pos": 2, "ts": 2}]) + "\n")
EOF
}
pass() { PATH="$T/bin:$PATH" SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" JANITOR_DRY=1 python3 -I "$T/s/janitor.py" 2>&1; }
done_case() { for q in "${pids[@]}"; do pkill -P "$q" 2>/dev/null; kill "$q" 2>/dev/null; done; wait 2>/dev/null; pids=(); rm -rf "$T"; }
prompt() { printf 'Implemente os critérios de aceite da issue #%s. A issue #4 (relatório manual) está aberta; leia o spec issue #1.\nA última linha é exatamente Refs #%s (nunca Closes).' "$1" "$1"; }

# 1. Two runs, different headers, both cite #4 and #1 in prose: no duplicate, and no #4 or #1 held as live work.
setup; live run-20261127-151404-b16c26 8 "$(prompt 8)"; live run-20261127-151410-f5aa0a 9 "$(prompt 9)"; sleep 0.5
o=$(pass)
echo "$o" | /usr/bin/grep -q "DUP issue #4" && ko "a prose #4 reads as a duplicate: $(echo "$o" | /usr/bin/grep DUP)" || ok "a prose #4 is no duplicate"
echo "$o" | /usr/bin/grep -q "DUP issue #1\b" && ko "a prose spec #1 reads as a duplicate" || ok "a prose spec #1 is no duplicate"
echo "$o" | /usr/bin/grep -qE "#(4|1)\b.*is live|is live.*#(4|1)\b" && ko "the card pass holds a prose issue as live work: $(echo "$o" | /usr/bin/grep -E 'is live')" || ok "the card pass holds no prose issue"
done_case

# 2. Two runs whose headers both name #9: still a duplicate.
setup; live run-20261127-151404-b16c26 9 "$(prompt 9)"; live run-20261127-151410-f5aa0a 9 "$(prompt 9)"; sleep 0.5
o=$(pass)
echo "$o" | /usr/bin/grep -q "DUP issue #9 .*keeping run-20261127-151404-b16c26, killing run-20261127-151410-f5aa0a" && ok "two runs on #9 are still a duplicate" || ko "no DUP for two runs on #9: $o"
done_case

# 3. Accept lines with no Refs: the router form `GitHub issue 11` holds #11, the prose #4 holds nothing.
setup; live run-20261127-151404-b16c26 11 "Fix GitHub issue 11. A issue #4 is open."; live run-20261127-151410-f5aa0a 12 "Fix GitHub issue 12. A issue #4 is open."; sleep 0.5
o=$(pass)
echo "$o" | /usr/bin/grep -q "DUP issue #4" && ko "a prose #4 with no Refs reads as a duplicate" || ok "a prose #4 with no Refs is no duplicate"
done_case
exit $fail
