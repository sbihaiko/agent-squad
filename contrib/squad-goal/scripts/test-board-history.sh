#!/bin/bash
# The board history store: host-free (no hub, no GitHub client), one file per repository, a points fact the Monitor draws, and the hub wires it.
# Fixtures and a temp file only: never the network, never the live hub state.
# usage: bash test-board-history.sh [board_history.py]
here="$(cd "$(dirname "$0")" && pwd)"; py=python3; [ -n "${SG_CHECKOUT:-}" ] && [ -x "$SG_CHECKOUT/.venv/bin/python" ] && py="$SG_CHECKOUT/.venv/bin/python"; src=${1:-$here/board_history.py}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s"; cp "$src" "$T/s/board_history.py" 2>/dev/null || { ko "board_history.py is missing"; rm -rf "$T"; exit 1; }
cp "$here/board_columns.py" "$here/board-definition.json" "$T/s/"

grep -Eq 'import hub|from hub|gh api|subprocess|urllib|requests' "$T/s/board_history.py" \
  && ko "the module imports the hub or a GitHub client" || ok "the module imports no hub and no GitHub client"

"$py" -c "import sys; sys.path.insert(0, '$T/s'); import board_history; assert 'hub' not in sys.modules" 2>/dev/null \
  && ok "the module loads with no hub" || ko "the module failed to load alone"

"$py" - "$T/s" "$T/hub-board.json" <<'PY' >"$T/out.txt" 2>&1
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
"$py" - "$here/hubmux.py" <<'PY' && ok "the live server starts the history writer" || ko "the live server starts no history writer (hubmux read threads)"
import ast, sys
for n in ast.walk(ast.parse(open(sys.argv[1]).read())):
    if isinstance(n, ast.For) and isinstance(n.iter, ast.Tuple):
        names = [e.value for e in n.iter.elts if isinstance(e, ast.Constant)]
        if "refresh_board" in names:
            sys.exit(0 if "refresh_history" in names else 1)
sys.exit(1)
PY
grep -q 'budget_fact(_hist.history_path' "$here/hub.py" \
  && ok "the Monitor facts carry the GraphQL points" || ko "the Monitor facts do not carry the GraphQL points"
! grep -q 'instTile("GraphQL"' "$here/hub.py" \
  && ok "the card draws no GraphQL tile (removed by the owner)" || ko "the card draws a GraphQL tile the owner removed"

# (#1394) A record that closed before the card's last moves is re-read: the Status clock can lag the move it stamps, so a
# complete record whose last known Status is not the card's current one is stale even when both clocks agree.
"$py" - "$T/s" <<'PY' >"$T/out2.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import board_history as bh
ev = [{"t": "2026-11-05T01:28:14Z", "type": "status", "status": "Code Complete"}]
stored = {1393: {"issue_updated": "2026-11-05T01:28:14Z", "status_updated": "2026-11-05T01:28:14Z",
                 "events": ev, "complete": True, "cursor": None}}
items = [{"n": 1393, "issue_updated": "2026-11-05T01:28:14Z", "status_updated": "2026-11-05T01:28:14Z",
          "status": "Closed"}]
print("due", bh.due_reads(items, stored))
stored[1393]["events"] = ev + [{"t": "2026-11-05T01:28:16Z", "type": "status", "status": "Gating"},
                               {"t": "2026-11-05T01:28:41Z", "type": "status", "status": "Closed"}]
print("settled", bh.due_reads(items, stored))
PY
grep -q '^due \[1393\]$' "$T/out2.txt" \
  && ok "a complete record behind the card's current Status is re-read" || ko "a stale complete record is never re-read: $(cat "$T/out2.txt")"
grep -q '^settled \[\]$' "$T/out2.txt" \
  && ok "the re-read record is settled, so the pass does not loop" || ko "the settled record is still due: $(cat "$T/out2.txt")"

# (#1419) A close that moves no card is never read: the issue closes through the API and its card keeps its Status, so no
# clock moves. A complete record that ends in Backlog is due while the card is closed, and once read it ends closed and
# plots its lead row. An edit that changes no state is still no reason to read.
"$py" - "$T/s" <<'PY' >"$T/out3.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import board_history as bh
ev = [{"t": "2026-11-01T00:00:00Z", "type": "added"},
      {"t": "2026-11-01T00:00:00Z", "type": "status", "status": "Backlog"}]
stored = {1420: {"issue_updated": "2026-11-01T00:00:00Z", "status_updated": "2026-11-01T00:00:00Z",
                 "events": ev, "complete": True, "cursor": None}}
closed = {"n": 1420, "issue_updated": "2026-11-04T00:00:00Z", "status_updated": "2026-11-01T00:00:00Z",
          "status": "Backlog", "closed": True}
open_ = dict(closed, closed=False)
print("due", bh.due_reads([closed], stored))
print("edited", bh.due_reads([open_], stored))
stored[1420]["events"] = ev + [{"t": "2026-11-02T00:00:00Z", "type": "closed"}]
print("settled", bh.due_reads([closed], stored))
row = bh.board_time(bh.intervals(stored[1420]["events"]))
print("row", row is not None, round(row["flowing"], 3), sorted(row["columns"].items()), row["censored"])
PY
grep -q '^due \[1420\]$' "$T/out3.txt" \
  && ok "a card closed while its complete record ends in Backlog is re-read" || ko "a close that moves no card is never read: $(cat "$T/out3.txt")"
grep -q '^edited \[\]$' "$T/out3.txt" \
  && ok "an edit that changes no closed state is no reason to read" || ko "an unrelated edit is a reason to read: $(cat "$T/out3.txt")"
grep -q '^settled \[\]$' "$T/out3.txt" \
  && ok "the re-read record ends closed and is settled" || ko "the closed record is still due: $(cat "$T/out3.txt")"
grep -qxF "row True 24.0 [('Backlog', 24.0)] False" "$T/out3.txt" \
  && ok "the re-read record plots its lead row from the board entry" || ko "lead row: $(cat "$T/out3.txt")"

"$py" - "$T/s" <<'PY' >"$T/boardtime.txt" 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import board_history as bh
# #1354: the lead-time chart measures a closed spec from its first time on the board, the parked Backlog wait included.
events = [{"t": "2026-11-01T00:00:00Z", "type": "added"}, {"t": "2026-11-01T00:00:00Z", "type": "status", "status": "Backlog"},
          {"t": "2026-11-02T00:00:00Z", "type": "status", "status": "Closed"},
          {"t": "2026-11-02T00:00:00Z", "type": "closed"}]
wait = bh.board_time(bh.intervals(events))
print("wait", round(wait["flowing"], 3), sorted(wait["columns"].items()), round(sum(wait["columns"].values()), 3))
moved = [{"t": "2026-11-01T00:00:00Z", "type": "added"}, {"t": "2026-11-01T00:00:00Z", "type": "status", "status": "Backlog"},
         {"t": "2026-11-01T06:00:00Z", "type": "status", "status": "Doing"},
         {"t": "2026-11-02T00:00:00Z", "type": "status", "status": "Closed"},
         {"t": "2026-11-02T00:00:00Z", "type": "closed"}]
span = bh.board_time(bh.intervals(moved))
print("moved", round(span["flowing"], 3), sorted(span["columns"].items()), round(sum(span["columns"].values()), 3))
print("off", bh.board_time(bh.intervals([{"t": "2026-11-01T00:00:00Z", "type": "status", "status": "Closed"},
                                          {"t": "2026-11-01T00:00:00Z", "type": "closed"}])))
print("end", bh.board_time(bh.intervals(moved), end="Code Review"))   # the reader's end column, never replaced (#1354)
PY
grep -qxF "wait 24.0 [('Backlog', 24.0)] 24.0" "$T/boardtime.txt" \
  && ok "a closed spec's board time counts the Backlog wait" || ko "board time: $(cat "$T/boardtime.txt")"
grep -qxF "moved 24.0 [('Backlog', 6.0), ('Doing', 18.0)] 24.0" "$T/boardtime.txt" \
  && ok "the board-time stack sums the columns the card wore" || ko "board-time stack: $(cat "$T/boardtime.txt")"
grep -q "^off None$" "$T/boardtime.txt" \
  && ok "a card never on the board has no board time" || ko "off-board: $(cat "$T/boardtime.txt")"
grep -q "^end None$" "$T/boardtime.txt" \
  && ok "a card with no entry into the chosen end column has no board time" || ko "end column: $(cat "$T/boardtime.txt")"

rm -rf "$T"; exit $fail
