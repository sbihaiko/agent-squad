#!/bin/bash
# The live board is an instance of the board definition (ADR-0141). `board_define.py --definition <board.json> <owner> <project>`
# reads the project's fields once and names, by name, each difference between the board and the definition: an option the
# definition names that the board lacks, an option the board carries that the definition does not name, and a field the
# board lacks. Expand mode adds every missing option beside today's options and creates every missing field; it removes
# nothing, keeps the id of every option already there, so no card loses its value, and sends no item write, so no card moves.
# With no --apply the run is a dry run: one read and no write. --check exits non-zero when the board drifts.
# Stubbed gh that serves a fixture board snapshot and logs every call; a temp state dir, so the live board is never read or written.
# usage: bash test-board-define.sh [board_define.py]
src=${1:-$(dirname "$0")/board_define.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/board_define.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"; for f in board_migrate.py board_columns.py board-definition.json; do cp "$(dirname "$src")/$f" "$T/$f"; done
cat > "$T/board.json" <<'J'
{"fieldNames": {"statuses": "Status", "kinds": "Kind", "freeze": "Freeze", "outcomes": "Outcome"},
 "statuses": [{"name": "Backlog", "color": "GRAY", "fact": "Open, not in the schedule"},
              {"name": "Todo", "color": "BLUE", "fact": "Startable"},
              {"name": "Doing", "color": "YELLOW", "fact": "A live run holds it"},
              {"name": "Closed", "color": "GREEN", "fact": "Merged and closed"}],
 "kinds": [{"name": "Bug", "color": "RED", "fact": "A defect"}, {"name": "Task", "color": "BLUE", "fact": "Work"}],
 "freeze": [{"name": "Owner", "color": "ORANGE", "fact": "frozen:owner"}],
 "outcomes": [{"name": "Merged", "color": "GREEN", "fact": "In main"}, {"name": "Reverted", "color": "ORANGE", "fact": "Taken back"}]}
J
# the board today: a Status field with two options the definition does not name, and a Freeze field missing one option it does not need
cat > "$T/fields.json" <<'J'
[{"id": 1, "node_id": "F_TITLE", "name": "Title", "data_type": "title"},
 {"id": 2, "node_id": "F_STATUS", "name": "Status", "data_type": "single_select", "options": [
   {"id": "o_block", "name": {"raw": "Blocked"}, "color": "RED", "description": {"raw": ""}},
   {"id": "o_todo", "name": {"raw": "Todo"}, "color": "GRAY", "description": {"raw": "today"}},
   {"id": "o_done", "name": {"raw": "Done"}, "color": "GREEN", "description": {"raw": ""}}]},
 {"id": 3, "node_id": "F_FREEZE", "name": "Freeze", "data_type": "single_select", "options": [
   {"id": "o_owner", "name": {"raw": "Owner"}, "color": "ORANGE", "description": {"raw": "frozen:owner"}}]},
 {"id": 4, "node_id": "F_RUN", "name": "Run", "data_type": "text"}]
J
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in *"--input -"*) cat >> "TMPDIR/graphql.in"; echo >> "TMPDIR/graphql.in";; esac
case "$a" in
  *"api graphql"*) echo '{"data":{}}';;
  *users/o) echo '{"type":"User"}';;
  *projectsV2/4/fields*) cat "TMPDIR/fields.json";;
  *projectsV2/4) echo '{"node_id":"P_1"}';;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
sed -i '' "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { SG_STATE="$T/st" SG_BOARD_READ_BACKOFF=0 python3 "$T/board_define.py" --definition "$T/board.json" "$@"; }

# the dry run: one read of the fields, the drift by name, the plan, and no write
o=$(run o 4 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "the dry run exits 0" || ko "dry run exit $rc: $o"
[ "$(grep -c 'projectsV2/4/fields' "$T/gh.calls")" -eq 1 ] && ok "the dry run reads the fields once" || ko "field reads: $(cat "$T/gh.calls")"
[ ! -e "$T/graphql.in" ] && ! grep -qE 'graphql|--method|-X ' "$T/gh.calls" && ok "the dry run sends no mutation" || ko "the dry run wrote: $(cat "$T/gh.calls")"
for line in "drift Status: missing Backlog" "drift Status: missing Doing" "drift Status: missing Closed" \
            "drift Status: not in the definition Blocked" "drift Status: not in the definition Done" \
            "drift Kind: field missing" "drift Outcome: field missing" \
            "plan Status: add Backlog, Doing, Closed" "plan Kind: create Bug, Task" "plan Outcome: create Merged, Reverted" \
            "dry run: nothing written"; do
  grep -qxF "$line" <<<"$o" && ok "names: $line" || ko "missing line '$line' in: $o"
done
grep -q "Freeze" <<<"$o" && ko "a field that matches the definition is reported: $o" || ok "a field that matches the definition is not reported"
grep -q "remove\|drop" <<<"$o" && ko "expand plans a removal: $o" || ok "expand plans no removal"

# --check: the same report, and a board that drifts exits non-zero
: > "$T/gh.calls"; o=$(run --check o 4 2>&1); rc=$?
[ "$rc" -ne 0 ] && grep -qxF "drift Kind: field missing" <<<"$o" && ok "--check exits non-zero on drift, naming it" || ko "--check exit $rc: $o"
[ ! -e "$T/graphql.in" ] && ok "--check sends no mutation" || ko "--check wrote"

# --apply: the missing options join today's options, every id is kept, nothing is removed, no card moves
: > "$T/gh.calls"; o=$(run --apply o 4 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "--apply exits 0" || ko "--apply exit $rc: $o"
python3 - "$T/graphql.in" <<'P' && ok "Status keeps Blocked, Todo and Done with their ids, then adds Backlog, Doing and Closed" || { ko "Status payload: $(cat "$T/graphql.in")"; }
import json, sys
calls = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
upd = [c["variables"]["input"] for c in calls if "updateProjectV2Field" in c["query"]]
assert len(upd) == 1 and upd[0]["fieldId"] == "F_STATUS", upd
opts = upd[0]["singleSelectOptions"]
assert [(o.get("id"), o["name"]) for o in opts] == [("o_block", "Blocked"), ("o_todo", "Todo"), ("o_done", "Done"), (None, "Backlog"), (None, "Doing"), (None, "Closed")], opts
assert opts[1] == {"id": "o_todo", "name": "Todo", "color": "GRAY", "description": "today"}, opts[1]
assert opts[3] == {"name": "Backlog", "color": "GRAY", "description": "Open, not in the schedule"}, opts[3]
P
python3 - "$T/graphql.in" <<'P' && ok "--apply creates Kind and Outcome with the definition's options, and touches Freeze not at all" || ko "create payload: $(cat "$T/graphql.in")"
import json, sys
calls = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
made = {c["variables"]["input"]["name"]: c["variables"]["input"] for c in calls if "createProjectV2Field" in c["query"]}
assert sorted(made) == ["Kind", "Outcome"], made
assert [o["name"] for o in made["Kind"]["singleSelectOptions"]] == ["Bug", "Task"]
assert made["Outcome"]["dataType"] == "SINGLE_SELECT"
assert len(calls) == 3, calls
assert not any("F_FREEZE" in json.dumps(c) for c in calls)
P
grep -qE 'ItemFieldValue|items' "$T/gh.calls" "$T/graphql.in" && ko "--apply writes an item: $(cat "$T/gh.calls")" || ok "--apply moves no card"
grep -qiE 'delete' "$T/graphql.in" && ko "--apply deletes" || ok "--apply deletes nothing"

# a board that is already the definition's instance: no drift, no write
python3 - "$T/fields.json" <<'P'
import json, sys
fs = [{"id": 2, "node_id": "F_STATUS", "name": "Status", "data_type": "single_select", "options": [{"id": "s%d" % i, "name": {"raw": n}, "color": "GRAY", "description": {"raw": ""}} for i, n in enumerate(["Backlog", "Todo", "Doing", "Closed"])]}]
for i, (f, names) in enumerate([("Kind", ["Bug", "Task"]), ("Freeze", ["Owner"]), ("Outcome", ["Merged", "Reverted"])]):
    fs.append({"id": 10 + i, "node_id": "F_" + f, "name": f, "data_type": "single_select", "options": [{"id": f + n, "name": {"raw": n}, "color": "GRAY", "description": {"raw": ""}} for n in names]})
json.dump(fs, open(sys.argv[1], "w"))
P
rm -f "$T/graphql.in"; o=$(run --check --apply o 4 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ ! -e "$T/graphql.in" ] && grep -qxF "no drift: the board carries every option the definition names, and no other" <<<"$o" && ok "an applied board reports no drift and sends no write" || ko "applied board exit $rc: $o"

rm -rf "$T"; [ "$fail" -eq 0 ] && echo "TDD GREEN: board_define" || exit 1
