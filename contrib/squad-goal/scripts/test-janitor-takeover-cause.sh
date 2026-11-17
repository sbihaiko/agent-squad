#!/bin/bash
# A run the janitor takes over (ADR-0166) ends with the killed verdict that names its cause, `killed/taken-over`, in its own
# record. The hub reads that cause as an intervention; a run with no cause reads as a plain failure (ADR-0029).
# Stubbed gh and board; a temp state dir. usage: bash test-janitor-takeover-cause.sh [janitor.py]
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/wip_snapshot.py" "$T/s/wip_snapshot.py"; printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/notify"; export PATH="$T/bin:$PATH"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s (bug; one PR):\n' "$2" > "$T/co/runs/$1/request.txt"
  printf '%s\n' '{"event": "run_start", "run_id": "r", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": ["main"], "pos": 1, "ts": 1}' > "$T/co/runs/$1/record.jsonl"
  touch -t 202001010000 "$T/co/runs/$1" "$T/co/runs/$1/record.jsonl" "$T/co/runs/$1/request.txt"; }
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" SQUAD_CAPS_DIR="$T/caps" "$@" python3 -I "$T/s/janitor.py" 2>&1; }
last() { tail -1 "$T/co/runs/$1/record.jsonl"; }
hubstate() { SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T/co" SG_HUB_DIR="$T/hub" SG_STATE="$T/st" HUB_SRC="$d/hub.py" python3 - "$1" <<'PY'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("hub", os.environ["HUB_SRC"]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
print(hub.state_of(hub.scan(sys.argv[1]), 0))
PY
}
mkdir -p "$T/hub"
mk run-stuck 1001
mk run-red 1002
printf '%s\n' '{"event": "gate_result", "ts": 2, "pos": 2, "node": "n", "entry": 1, "item": null, "sha": "s", "dirt": false, "suite": "fast", "outcome": "fail", "source": "gate"}' >> "$T/co/runs/run-red/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-red/record.jsonl"
o=$(pass env)
last run-stuck | /usr/bin/grep -q '"verdict": "failed".*"reasons": \["killed/taken-over"\]' && ok "a takeover writes the taken-over cause into the record" || ko "no takeover cause in the record: $o / $(last run-stuck)"
[ "$(hubstate "$T/co/runs/run-stuck/record.jsonl")" = "taken-over" ] && ok "the hub reads a taken-over run as taken-over" || ko "the hub did not read the taken-over run: $(hubstate "$T/co/runs/run-stuck/record.jsonl")"
last run-red | /usr/bin/grep -q '"verdict": "failed".*"reasons": \["killed/taken-over"\]' && ok "a takeover of a measured red writes its cause too" || ko "no cause on the red run: $(last run-red)"
[ "$(hubstate "$T/co/runs/run-red/record.jsonl")" = "failed" ] && ok "the hub keeps the measured red of a taken-over run as failed" || ko "the hub hid the measured red: $(hubstate "$T/co/runs/run-red/record.jsonl")"
exit $fail
