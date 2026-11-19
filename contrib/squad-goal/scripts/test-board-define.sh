#!/bin/bash
# seconds: 0.79
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
{"fieldNames": {"statuses": "Status", "kinds": "Kind", "freeze": "Freeze", "outcomes": "Outcome", "gateTries": "Gate Tries"},
 "statuses": [{"name": "Backlog", "color": "GRAY", "fact": "Open, not in the schedule"},
              {"name": "Todo", "color": "BLUE", "fact": "Startable"},
              {"name": "Doing", "color": "YELLOW", "fact": "A live run holds it"},
              {"name": "Closed", "color": "GREEN", "fact": "Merged and closed"}],
 "kinds": [{"name": "Bug", "color": "RED", "fact": "A defect"}, {"name": "Task", "color": "BLUE", "fact": "Work"}],
 "freeze": [{"name": "Owner", "color": "ORANGE", "fact": "frozen:owner"}],
 "outcomes": [{"name": "Merged", "color": "GREEN", "fact": "In main"}, {"name": "Reverted", "color": "ORANGE", "fact": "Taken back"}],
 "gateTries": {"type": "number"}}
J
# the board today: a Status field with two options the definition does not name, a Freeze field missing one option it does
# not need, and the Run text field without its Run started twin: both are the binding's holder fields, which the
# definition does not name (#487)
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
  *"api graphql"*)
    if [ -s "TMPDIR/graphql.in" ]; then python3 - "TMPDIR/graphql.in" "TMPDIR/fields.json" <<'P'
import json, sys
body = json.loads([l for l in open(sys.argv[1]) if l.strip()][-1])
if "updateProjectV2Field" not in body.get("query", ""): raise SystemExit(0)
fields = json.load(open(sys.argv[2]))
s = next(f for f in fields if f.get("name") == "Status")
by = {o["id"]: o["name"] for o in body["variables"]["input"]["singleSelectOptions"]}
for o in s["options"]:
    if o["id"] in by: o["name"] = {"raw": by[o["id"]]}
json.dump(fields, open(sys.argv[2], "w"))
P
    fi
    echo '{"data":{}}';;
  *users/o) echo '{"type":"User"}';;
  *projectsV2/4/fields*) cat "TMPDIR/fields.json";;
  *items?per_page=100*)
    python3 - "TMPDIR/fields.json" "TMPDIR/cards.tsv" <<'P'
import json, sys
fields = json.load(open(sys.argv[1]))
s = next(f for f in fields if f.get("name") == "Status")
names = {o["id"]: (o["name"]["raw"] if isinstance(o["name"], dict) else o["name"]) for o in s["options"]}
for line in open(sys.argv[2]):
    if not line.strip(): continue
    iid, num, repo, oid = line.rstrip("\n").split("\t")
    print(json.dumps({"iid": iid, "number": int(num), "repo": repo, "s": "open", "l": [], "cr": "", "st": names[oid]}))
P
    ;;
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
            "drift Gate Tries: field missing" "plan Gate Tries: create number field" \
            "drift Run started: field missing" "plan Run started: create text field" \
            "dry run: nothing written"; do
  grep -qxF "$line" <<<"$o" && ok "names: $line" || ko "missing line '$line' in: $o"
done
grep -qE "^(drift|plan) Run:" <<<"$o" && ko "a text field the board carries is reported: $o" || ok "a text field the board carries is not reported"
grep -q "Freeze" <<<"$o" && ko "a field that matches the definition is reported: $o" || ok "a field that matches the definition is not reported"
grep -q "remove\|drop" <<<"$o" && ko "expand plans a removal: $o" || ok "expand plans no removal"

# --check: the same report, and a board that drifts exits non-zero
: > "$T/gh.calls"; o=$(run --check o 4 2>&1); rc=$?
[ "$rc" -ne 0 ] && grep -qxF "drift Kind: field missing" <<<"$o" && ok "--check exits non-zero on drift, naming it" || ko "--check exit $rc: $o"
[ ! -e "$T/graphql.in" ] && ok "--check sends no mutation" || ko "--check wrote"

# --apply: the missing options join today's options, every id is kept, nothing is removed, no card moves
: > "$T/gh.calls"; o=$(run --apply o 4 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "--apply exits 0" || ko "--apply exit $rc: $o"
python3 - "$T/graphql.in" <<'P' && ok "Status is sent in the definition's order, each existing option with its id, then the options the definition does not name" || { ko "Status payload: $(cat "$T/graphql.in")"; }
import json, sys
calls = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
upd = [c["variables"]["input"] for c in calls if "updateProjectV2Field" in c["query"]]
assert len(upd) == 1 and upd[0]["fieldId"] == "F_STATUS", upd
opts = upd[0]["singleSelectOptions"]
assert [(o.get("id"), o["name"]) for o in opts] == [(None, "Backlog"), ("o_todo", "Todo"), (None, "Doing"), (None, "Closed"), ("o_block", "Blocked"), ("o_done", "Done")], opts
assert opts[1] == {"id": "o_todo", "name": "Todo", "color": "GRAY", "description": "today"}, opts[1]
assert opts[0] == {"name": "Backlog", "color": "GRAY", "description": "Open, not in the schedule"}, opts[0]
assert opts[4] == {"id": "o_block", "name": "Blocked", "color": "RED", "description": ""}, opts[4]
P
python3 - "$T/graphql.in" <<'P' && ok "--apply creates Kind and Outcome with the definition's options, Run started as text and Gate Tries as a number, and touches Freeze and Run not at all" || ko "create payload: $(cat "$T/graphql.in")"
import json, sys
calls = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
made = {c["variables"]["input"]["name"]: c["variables"]["input"] for c in calls if "createProjectV2Field" in c["query"]}
assert sorted(made) == ["Gate Tries", "Kind", "Outcome", "Run started"], made
assert [o["name"] for o in made["Kind"]["singleSelectOptions"]] == ["Bug", "Task"]
assert made["Outcome"]["dataType"] == "SINGLE_SELECT"
assert made["Gate Tries"] == {"projectId": "P_1", "dataType": "NUMBER", "name": "Gate Tries"}, made["Gate Tries"]
assert made["Run started"] == {"projectId": "P_1", "dataType": "TEXT", "name": "Run started"}, made["Run started"]
assert len(calls) == 5, calls
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
fs += [{"id": 20 + i, "node_id": "F_T%d" % i, "name": n, "data_type": "text"} for i, n in enumerate(["Run", "Run started"])]
fs.append({"id": 30, "node_id": "F_GT", "name": "Gate Tries", "data_type": "number"})
json.dump(fs, open(sys.argv[1], "w"))
P
rm -f "$T/graphql.in"; o=$(run --check --apply o 4 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ ! -e "$T/graphql.in" ] && grep -qxF "no drift: the board carries every option the definition names, and no other" <<<"$o" && ok "an applied board reports no drift and sends no write" || ko "applied board exit $rc: $o"

# #2154: the in-place rename. One field call carries every option with its own id, so each card keeps its column; one items
# read proves every card moved with its option and the count is the same; a second call changes nothing.
cat > "$T/fields.json" <<'J'
[{"id": 2, "node_id": "F_STATUS", "name": "Status", "data_type": "single_select", "options": [
  {"id": "o_backlog", "name": {"raw": "Backlog"}, "color": "GRAY", "description": {"raw": ""}},
  {"id": "o_review", "name": {"raw": "Code Review"}, "color": "ORANGE", "description": {"raw": ""}},
  {"id": "o_landing", "name": {"raw": "Code Complete"}, "color": "ORANGE", "description": {"raw": ""}},
  {"id": "o_closed", "name": {"raw": "Closed"}, "color": "GREEN", "description": {"raw": ""}}]}]
J
printf 'PVT_1\t5\ta/x\to_review\nPVT_2\t6\ta/x\to_landing\nPVT_3\t7\tb/y\to_closed\n' > "$T/cards.tsv"
: > "$T/graphql.in"; : > "$T/gh.calls"
o=$(run --rename --apply o 4 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "--rename --apply exits 0" || ko "--rename exit $rc: $o"
python3 - "$T/graphql.in" <<'P' && ok "the rename sends one field write" || ko "rename writes: $(cat "$T/graphql.in")"
import json, sys
calls = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
upd = [c for c in calls if "updateProjectV2Field" in c.get("query", "")]
assert len(upd) == 1, calls
P
# the count is a real read before the call: the items read comes before the field write in the call log
readline=$(grep -n 'items?per_page=100' "$T/gh.calls" | head -1 | cut -d: -f1)
writeline=$(grep -n 'api graphql' "$T/gh.calls" | head -1 | cut -d: -f1)
[ -n "$readline" ] && [ -n "$writeline" ] && [ "$readline" -lt "$writeline" ] && ok "the count reads the cards before the field call" || ko "call order: $(cat "$T/gh.calls")"
python3 - "$T/graphql.in" <<'P' && ok "the call carries every option id, and the two old names read Reviewing and Batching" || ko "rename payload: $(cat "$T/graphql.in")"
import json, sys
body = json.loads([l for l in open(sys.argv[1]) if l.strip()][-1])
inp = body["variables"]["input"]; opts = inp["singleSelectOptions"]
assert "updateProjectV2Field" in body["query"] and inp["fieldId"] == "F_STATUS", body
assert [(o["id"], o["name"]) for o in opts] == [("o_backlog", "Backlog"), ("o_review", "Reviewing"),
                                                ("o_landing", "Batching"), ("o_closed", "Closed")], opts
P
grep -q 'done: 3 card(s), 3 before the call; every card kept its column' <<<"$o" && ok "one items read proves the count is the same and every card kept its column" || ko "rename proof: $o"
python3 - "$T/fields.json" <<'P' && ok "the two options are renamed in place and keep their ids" || ko "renamed field: $(cat "$T/fields.json")"
import json, sys
s = json.load(open(sys.argv[1]))[0]
assert [(o["id"], o["name"]["raw"]) for o in s["options"]] == [("o_backlog", "Backlog"), ("o_review", "Reviewing"), ("o_landing", "Batching"), ("o_closed", "Closed")]
P
: > "$T/graphql.in"; : > "$T/gh.calls"
o=$(run --rename --apply o 4 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ ! -s "$T/graphql.in" ] && grep -q 'no old name' <<<"$o" && ok "a second call writes nothing, changing nothing" || ko "second rename: exit $rc, writes: $(cat "$T/graphql.in"): $o"

# #770: with no SG_STATE the run names the gap and exits 2, never a KeyError traceback
o=$(env -u SG_STATE PATH="$T/bin:$PATH" python3 "$T/board_define.py" --definition "$T/board.json" o 4 2>&1); rc=$?
[ "$rc" -eq 2 ] && grep -q "SG_STATE is not set" <<<"$o" && ! grep -q Traceback <<<"$o" && ok "no SG_STATE is named, not a KeyError" || ko "no SG_STATE: exit $rc: $o"

# #781: a refused read (rate limit) exits non-zero in one line, never a RuntimeError traceback
mkdir -p "$T/rbin"; printf '#!/bin/bash\necho "gh: API rate limit exceeded" >&2; exit 1\n' > "$T/rbin/gh"; chmod +x "$T/rbin/gh"
o=$(PATH="$T/rbin:$PATH" SG_STATE="$T/st2" SG_BOARD_READ_BACKOFF=0 python3 "$T/board_define.py" --definition "$T/board.json" --check o 4 2>&1); rc=$?
[ "$rc" -ge 2 ] && [ "$(printf '%s\n' "$o" | wc -l | tr -d ' ')" = 1 ] && grep -q "rate limit exceeded" <<<"$o" && ! grep -q Traceback <<<"$o" \
  && ok "a refused read is one line, not a traceback" || ko "refused read: exit $rc: $o"

# The shipped definition itself: eight columns with Testing between Batching and Closed, its own colour, the actor and
# the rule that an attempt never moves a card, and the Gate Tries number field (spec #1218, #2148).
def="$(cd "$(dirname "$src")" && pwd)/board-definition.json"
if [ ! -f "$def" ]; then ko "shipped definition: $def is missing"
elif o=$(python3 - "$def" 2>&1 <<'P'
import json, sys
d = json.load(open(sys.argv[1])); names = [s["name"] for s in d["statuses"]]
assert len(names) == 8, names
assert names.index("Batching") + 1 == names.index("Testing") == names.index("Closed") - 1, names
g = d["statuses"][names.index("Testing")]
assert g["color"] == "PINK" and [s["color"] for s in d["statuses"]].count("PINK") == 1, g
assert g["description"] == "A gate or proof waits for a slot or runs for the wave; a red or unproven attempt is a marker, not a move", g   # the wording; `fact` holds the expression (#1901)
assert isinstance(d.get("definition_version"), int), d.get("definition_version")
assert g["actor"] == "The gate" and g["terminal"] is False, g
assert "An attempt is a marker, not a move: the card leaves Testing only forward." in d["rules"], d["rules"]
assert d["fieldNames"]["gateTries"] == "Gate Tries" and d["gateTries"] == {"type": "number"}, d["fieldNames"]
P
); then ok "the shipped definition names eight columns, Testing between Batching and Closed, and Gate Tries"
else ko "shipped definition: $(printf '%s\n' "$o" | tail -n 1)"; fi

rm -rf "$T"; [ "$fail" -eq 0 ] && echo "TDD GREEN: board_define" || exit 1
