#!/bin/bash
# seconds: 0.15
# The migration planner: a dry run lists every card move and every option removal and writes nothing; the check refuses an old
# option while a card carries it, with the counts; Open Specs lands in Backlog with the spec label (ADR-0143).
# Fixtures only: the planner reads a JSON list and never reaches the network or a board.
# usage: bash test-board-migrate.sh [board_migrate.py]
src=${1:-$(dirname "$0")/board_migrate.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
here="$(cd "$(dirname "$src")" && pwd)"; mkdir -p "$T/s"; cp "$src" "$T/s/board_migrate.py"; cp "$here/board_columns.py" "$here/board-definition.json" "$T/s/"
cat > "$T/all.json" <<'J'
[{"n":1,"st":"Open Specs","s":"OPEN","l":[]},{"n":3,"st":"Blocked","s":"OPEN","l":[],"facts":{"wave_branch":true}},
 {"n":4,"st":"Blocked","s":"OPEN","l":[]},{"n":5,"st":"In Progress","s":"OPEN","l":[]},{"n":6,"st":"Done","s":"CLOSED","l":[]},
 {"n":7,"st":"Todo","s":"OPEN","l":[]},{"n":8,"st":"Merging","s":"OPEN","l":[]},{"n":9,"st":"Batching","s":"OPEN","l":[]},
 {"n":13,"st":"Landing","s":"OPEN","l":[]}]
J
echo '[{"n":7,"st":"Todo","s":"OPEN","l":[]},{"n":8,"st":"Doing","s":"OPEN","l":[]}]' > "$T/clean.json"
before=$(ls "$T" | sort)
out=$(python3 "$T/s/board_migrate.py" --plan "$T/all.json" 2>&1); rc=$?
[ $rc = 0 ] && ok "dry run exits 0" || ko "dry run exits $rc"
[ "$(echo "$out" | grep -c '^MOVE #')" = 6 ] && ok "every move is listed" || ko "moves: $out"
for o in Blocked "In Progress" Done "Open Specs" Merging Pushing; do echo "$out" | grep -q "^REMOVE option $o" && ok "removal listed: $o" || ko "removal missing: $o"; done
echo "$out" | grep -q '^MOVE #1 Open Specs -> Backlog +spec' && ok "Open Specs -> Backlog with the spec label" || ko "open specs: $out"
echo "$out" | grep -q '^MOVE #3 Blocked -> Batching +frozen:owner' && ok "Blocked -> the column reached, owner marker" || ko "blocked: $out"
echo "$out" | grep -q '^MOVE #8 Merging -> Batching' && ok "Merging -> Batching" || ko "merging: $out"
echo "$out" | grep -q '^MOVE #9 ' && ko "batching: a card on the shipped Batching column is never moved" || ok "Batching is the shipped column, so it is no old option: its card is never moved"
echo "$out" | grep -q '^MOVE #13 \|option Landing' && ko "a renamed option is moved or removed: $out" || ok "Landing is renamed in place (#2315): its card is never moved and its option never removed"
echo "$out" | grep -q 'option Testing' && ko "Testing is a shipped column again, never an old option: $out" || ok "Testing is a shipped column, so the plan never removes it"
[ "$(ls "$T" | sort)" = "$before" ] && ok "the dry run wrote nothing" || ko "the dry run wrote a file"
err=$(python3 "$T/s/board_migrate.py" --check "$T/all.json" 2>&1 >/dev/null); rc=$?
[ $rc = 3 ] && ok "removal refused while a card carries an old option" || ko "check exit $rc"
echo "$err" | grep -q 'Blocked: 2' && echo "$err" | grep -q 'Done: 1' && ok "the refusal names the counts" || ko "counts: $err"
python3 "$T/s/board_migrate.py" --check "$T/clean.json" >/dev/null 2>&1 && ok "a clean board is not refused" || ko "clean board refused"
# Pushing is retired with no column to take its cards: the plan moves none and refuses its removal by name. A card in
# Testing is on a shipped column (#2315), so it neither moves nor holds a removal.
echo '[{"n":11,"st":"Testing","s":"OPEN","l":[]},{"n":12,"st":"Pushing","s":"OPEN","l":[]}]' > "$T/retired.json"
out=$(python3 "$T/s/board_migrate.py" --plan "$T/retired.json" 2>&1)
! echo "$out" | grep -q '^MOVE ' && ok "a card on a retired option is never moved" || ko "a retired option was moved: $out"
! echo "$out" | grep -q 'option Testing' && ok "the plan names no Testing option: Testing is a column" || ko "Testing named as an old option: $out"
echo "$out" | grep -q '^REFUSE option Pushing: 1 card(s) left' && ok "the plan refuses the Pushing removal, naming the count" || ko "Pushing refusal: $out"
err=$(python3 "$T/s/board_migrate.py" --check "$T/retired.json" 2>&1 >/dev/null); rc=$?
[ $rc = 3 ] && ! echo "$err" | grep -q 'Testing' && echo "$err" | grep -q 'Pushing: 1' && ok "the check refuses a retired option with its count" || ko "retired check exit $rc: $err"
# #2154, #2315: the in-place rename. One call carries every option with its own id, the old names come from the version 1
# table and the one alias map, and a second call over the canonical names changes nothing.
python3 - "$T/s/board_migrate.py" <<'P' && ok "the rename carries every option id and maps the version 1 and version 2 names" || ko "rename plan"
import importlib.util, os, sys
sys.path.insert(0, os.path.dirname(sys.argv[1]))
spec = importlib.util.spec_from_file_location("bm", sys.argv[1]); m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
opts = [{"id": "a", "name": "Todo", "color": "GRAY", "description": ""},
        {"id": "b", "name": "Code Review", "color": "ORANGE", "description": ""},
        {"id": "c", "name": "Code Complete", "color": "ORANGE", "description": ""}]
out, changed = m.renamed(opts)
assert m.RENAMES == {"Code Review": "Reviewing", "Code Complete": "Batching", "Review": "Reviewing",
                     "Landing": "Batching", "Gating": "Testing"}, m.RENAMES
assert [(o["id"], o["name"]) for o in out] == [("a", "Todo"), ("b", "Reviewing"), ("c", "Batching")], out
assert changed
again, changed2 = m.renamed(out)
assert changed2 is False and again == out
v2 = [{"id": "r", "name": "Review"}, {"id": "l", "name": "Landing"}, {"id": "g", "name": "Gating"}]
out, changed = m.renamed(v2)
assert changed and [(o["id"], o["name"]) for o in out] == [("r", "Reviewing"), ("l", "Batching"), ("g", "Testing")], out
assert m.clashes(v2) == [] and m.clashes(v2 + [{"id": "t", "name": "Testing"}]) == ["Testing"]
P
rm -rf "$T"; exit $fail
