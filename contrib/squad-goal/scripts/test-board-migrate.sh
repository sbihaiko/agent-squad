#!/bin/bash
# The migration planner: a dry run lists every card move and every option removal and writes nothing; the check refuses an old
# option while a card carries it, with the counts; Open Specs lands in Backlog with the spec label (ADR-0143).
# Fixtures only: the planner reads a JSON list and never reaches the network or a board.
# usage: bash test-board-migrate.sh [board_migrate.py]
src=${1:-$(dirname "$0")/board_migrate.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
here="$(cd "$(dirname "$src")" && pwd)"; mkdir -p "$T/s"; cp "$src" "$T/s/board_migrate.py"; cp "$here/board_columns.py" "$here/board-definition.json" "$T/s/"
cat > "$T/all.json" <<'J'
[{"n":1,"st":"Open Specs","s":"OPEN","l":[]},{"n":3,"st":"Blocked","s":"OPEN","l":[],"facts":{"wave_branch":true}},
 {"n":4,"st":"Blocked","s":"OPEN","l":[]},{"n":5,"st":"In Progress","s":"OPEN","l":[]},{"n":6,"st":"Done","s":"CLOSED","l":[]},
 {"n":7,"st":"Todo","s":"OPEN","l":[]},{"n":8,"st":"Merging","s":"OPEN","l":[]},{"n":9,"st":"Landing","s":"OPEN","l":[]}]
J
echo '[{"n":7,"st":"Todo","s":"OPEN","l":[]},{"n":8,"st":"Doing","s":"OPEN","l":[]}]' > "$T/clean.json"
before=$(ls "$T" | sort)
out=$(python3 "$T/s/board_migrate.py" --plan "$T/all.json" 2>&1); rc=$?
[ $rc = 0 ] && ok "dry run exits 0" || ko "dry run exits $rc"
[ "$(echo "$out" | grep -c '^MOVE #')" = 6 ] && ok "every move is listed" || ko "moves: $out"
for o in Blocked "In Progress" Done "Open Specs" Merging Testing Pushing; do echo "$out" | grep -q "^REMOVE option $o" && ok "removal listed: $o" || ko "removal missing: $o"; done
echo "$out" | grep -q '^MOVE #1 Open Specs -> Backlog +spec' && ok "Open Specs -> Backlog with the spec label" || ko "open specs: $out"
echo "$out" | grep -q '^MOVE #3 Blocked -> Landing +frozen:owner' && ok "Blocked -> the column reached, owner marker" || ko "blocked: $out"
echo "$out" | grep -q '^MOVE #8 Merging -> Landing' && ok "Merging -> Landing" || ko "merging: $out"
echo "$out" | grep -q '^MOVE #9 ' && ko "landing: a card on the shipped Landing column is never moved" || ok "Landing is the shipped column, so it is no old option: its card is never moved"
[ "$(ls "$T" | sort)" = "$before" ] && ok "the dry run wrote nothing" || ko "the dry run wrote a file"
err=$(python3 "$T/s/board_migrate.py" --check "$T/all.json" 2>&1 >/dev/null); rc=$?
[ $rc = 3 ] && ok "removal refused while a card carries an old option" || ko "check exit $rc"
echo "$err" | grep -q 'Blocked: 2' && echo "$err" | grep -q 'Done: 1' && ok "the refusal names the counts" || ko "counts: $err"
python3 "$T/s/board_migrate.py" --check "$T/clean.json" >/dev/null 2>&1 && ok "a clean board is not refused" || ko "clean board refused"
# Testing and Pushing are retired with no column to take their cards: the plan moves none and refuses their removal by name.
echo '[{"n":11,"st":"Testing","s":"OPEN","l":[]},{"n":12,"st":"Pushing","s":"OPEN","l":[]}]' > "$T/retired.json"
out=$(python3 "$T/s/board_migrate.py" --plan "$T/retired.json" 2>&1)
! echo "$out" | grep -q '^MOVE ' && ok "a card on a retired option is never moved" || ko "a retired option was moved: $out"
echo "$out" | grep -q '^REFUSE option Testing: 1 card(s) left' && ok "the plan refuses the Testing removal, naming the count" || ko "Testing refusal: $out"
echo "$out" | grep -q '^REFUSE option Pushing: 1 card(s) left' && ok "the plan refuses the Pushing removal, naming the count" || ko "Pushing refusal: $out"
err=$(python3 "$T/s/board_migrate.py" --check "$T/retired.json" 2>&1 >/dev/null); rc=$?
[ $rc = 3 ] && echo "$err" | grep -q 'Testing: 1' && echo "$err" | grep -q 'Pushing: 1' && ok "the check refuses a retired option with its count" || ko "retired check exit $rc: $err"
rm -rf "$T"; exit $fail
