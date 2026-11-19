#!/bin/bash
# seconds: 0.65
# Clearing a card's Run fields is a write GitHub accepts (#101). The REST update endpoint takes a value to set a field and
# null to clear it -- "To clear the field, set this to null" (REST, Update an item in a project) -- and an empty string is a
# value the field refuses, answered "Invalid field values" (HTTP 400). A card that leaves In Progress is therefore written
# with null, never with "", so the card does not keep the run it no longer belongs to and no refusal is logged every cycle.
# The same rule covers a Run field that has no value to carry: an empty string is a refused value there too, and null is
# how the field is left unset. The clear keeps the two field ids it read, so the write names the fields it empties.
# The clear goes through the seam (#241), so the read that follows the PATCH is its proof.
# The stub gh refuses exactly what GitHub refuses -- an empty string value -- so this suite is red on the old payload and
# green on the accepted one. SG_BOARD_READ_BACKOFF is 0, so the suite sleeps for no read.
# usage: bash test-board-clear-run.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in *"--input -"*) cat > "TMPDIR/patch.in";; esac
case "$a" in
  *items/ITEM1*)   # the one write route: a value sets the field, null clears it, an empty string is refused as a field value
    if /usr/bin/grep -q '"value": ""' "TMPDIR/patch.in"; then echo '{"message":"Invalid field values","status":"400"}' >&2; exit 1; fi
    [ -e "TMPDIR/drop" ] || python3 "TMPDIR/bin/holder.py" "TMPDIR/patch.in" > "TMPDIR/holder.tsv"   # the card keeps what the PATCH set, unless the write is answered ok and never lands
    echo '{"id":"ITEM1"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_TODO","name":{"raw":"Todo"}}]},{"id":"F_RUN","name":"Run"},{"id":"F_START","name":"Run started"}]';;
  *'fields[]=F_RUN'*) cat "TMPDIR/holder.tsv" 2>/dev/null;;   # the read that proves a Run write: number, Run, Run started, repository url
  *items?per_page=100*) printf '[2001,"ITEM1","https://api.github.com/repos/x/y"]\n';;
  *users/o*) echo '{"type":"Organization"}';;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
cat > "$T/bin/holder.py" <<'P'
import json, sys
f = {x["id"]: x["value"] for x in json.load(open(sys.argv[1]))["fields"]}
if f.get("F_RUN"): print("2001\t%s\t%s\thttps://api.github.com/repos/x/y" % (f["F_RUN"], f.get("F_START") or ""))
P
sed -i '' "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { SG_STATE="$T/st" SG_REPO=x/y SG_BOARD_READ_BACKOFF=0 python3 "$T/s-board.py" "$@"; }
rm -f "$T/patch.in"; o=$(run --clear-run o 4 2001 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "a clear is a write GitHub accepts, not a refusal" || ko "clear exit $rc: $o"
body=$(cat "$T/patch.in" 2>/dev/null)
case "$body" in *'"id": "F_RUN", "value": null'*) ok "the Run field is cleared with null, the value the endpoint documents";; *) ko "Run field not cleared with null: $body";; esac
case "$body" in *'"id": "F_START", "value": null'*) ok "the Run started field is cleared with null";; *) ko "Run started field not cleared with null: $body";; esac
case "$body" in *'"value": ""'*) ko "the clear still sends an empty string, which GitHub refuses: $body";; *) ok "no field value is an empty string";; esac
[ "$(/usr/bin/grep -c 'items/ITEM1' "$T/gh.calls" 2>/dev/null | tr -d ' ')" = "1" ] && ok "one clear is one PATCH" || ko "gh calls: $(cat "$T/gh.calls")"
[ -e "$T/st/board-dirty" ] && ok "the hub is flagged to refresh" || ko "board-dirty not touched"
sed -n '/items\/ITEM1/,$p' "$T/gh.calls" | /usr/bin/grep -q 'fields\[\]=F_RUN' && ok "the clear is proven by the read that follows it (#241)" || ko "no Run read after the clear: $(cat "$T/gh.calls")"
[ -z "$(cat "$T/holder.tsv" 2>/dev/null)" ] && ok "the next read carries no holder" || ko "holder after clear: $(cat "$T/holder.tsv")"
rm -f "$T/patch.in"; o=$(run --run-fields o 4 2001 run-abc 2026-10-13T08:00:00Z 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "writing Run fields exits 0" || ko "run-fields exit $rc: $o"
body=$(cat "$T/patch.in" 2>/dev/null)
case "$body" in *'"id": "F_RUN", "value": "run-abc"'*) ok "Run carries the run id";; *) ko "Run field: $body";; esac
case "$body" in *'"id": "F_START", "value": "2026-10-13T08:00:00Z"'*) ok "Run started carries the run's start time";; *) ko "Run started field: $body";; esac
rm -f "$T/patch.in"; o=$(run --run-fields o 4 2001 run-abc "" 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "a Run field with no value to carry is left unset, not sent as an empty string" || ko "run-fields, no start time, exit $rc: $o"
body=$(cat "$T/patch.in" 2>/dev/null)
case "$body" in *'"id": "F_START", "value": null'*) ok "the unset Run started is written as null";; *) ko "empty Run started: $body";; esac
case "$body" in *'"id": "F_RUN", "value": "run-abc"'*) ok "the run id beside it is still written";; *) ko "Run field beside the empty start: $body";; esac
rm -rf "$T"; exit $fail
