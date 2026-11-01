#!/bin/bash
# The board history store: host-free (no hub, no GitHub client), one file per repository, a points fact the Monitor draws, and the hub wires it.
# Fixtures and a temp file only: never the network, never the live hub state.
# usage: bash test-board-history.sh [board_history.py]
here="$(cd "$(dirname "$0")" && pwd)"; src=${1:-$here/board_history.py}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s"; cp "$src" "$T/s/board_history.py" 2>/dev/null || { ko "board_history.py is missing"; rm -rf "$T"; exit 1; }
cp "$here/board_columns.py" "$here/board-definition.json" "$T/s/"

grep -Eq 'import hub|from hub|gh api|subprocess|urllib|requests' "$T/s/board_history.py" \
  && ko "the module imports the hub or a GitHub client" || ok "the module imports no hub and no GitHub client"

python3 -c "import sys; sys.path.insert(0, '$T/s'); import board_history; assert 'hub' not in sys.modules" 2>/dev/null \
  && ok "the module loads with no hub" || ko "the module failed to load alone"

python3 - "$T/s" "$T/hub-board.json" <<'PY' >"$T/out.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import board_history as bh
board = sys.argv[2]
a = bh.history_path(board, "sbihaiko/agent-squad")
b = bh.history_path(board, "sbihaiko/squad-board")
print("path", a.name, b.name)
bh.record_points(a, 5, 1_800_000_000)
print("fact", bh.budget_fact(a, 1_800_000_001)["text"])
print("other", len(bh.load_store(b)))
PY
grep -q '^path hub-history.sbihaiko_agent-squad.jsonl hub-history.sbihaiko_squad-board.jsonl$' "$T/out.txt" \
  && ok "one history file per repository" || ko "history names: $(cat "$T/out.txt")"
grep -q "^fact GraphQL 5/" "$T/out.txt" && ok "the Monitor fact reports the counted points" || ko "fact: $(cat "$T/out.txt")"
grep -q '^other 0$' "$T/out.txt" && ok "the second repository's file holds nothing from the first" || ko "mixed: $(cat "$T/out.txt")"

grep -q "board_history" "$here/hub.py" && ok "the hub loads the module" || ko "hub.py does not load board_history"
grep -q 'instTile("GraphQL"' "$here/hub.py" \
  && ok "the Monitor draws a GraphQL points tile" || ko "no GraphQL points tile in instTiles"

rm -rf "$T"; exit $fail
