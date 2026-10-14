#!/bin/bash
# The board pass names the run that holds a card: `board.py --run-fields <owner> <project> <issue#> <run> <run-started>` writes
# the two text fields Run and Run started, and `--clear-run` clears them when the card leaves In Progress. Both are one
# PATCH on the one item, they flag the hub to refresh at once, and a refusal is named on stderr and exits non-zero.
# The clear sends null, the value the endpoint documents for clearing a field; the stubbed gh refuses an empty string as a
# field value exactly as GitHub does ("Invalid field values", HTTP 400, #101).
# A miss re-reads the items with a bounded backoff (#96); SG_BOARD_READ_BACKOFF is 0 here, so the suite sleeps for no read.
# Stubbed gh; a temp state dir, so the live board and the loop's cache are never read or written.
# usage: bash test-board-run-fields.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in *"--input -"*) cat > "TMPDIR/patch.in";; esac
case "$a" in
  *items/ITEM_REFUSE*) echo '{"message":"Resource not accessible by integration","status":"403"}' >&2; exit 1;;
  *items/ITEM1*)
    if /usr/bin/grep -q '"value": ""' "TMPDIR/patch.in"; then echo '{"message":"Invalid field values","status":"400"}' >&2; exit 1; fi   # an empty string is not a field value GitHub takes (#101)
    echo '{"id":"ITEM1"}';;
  *users/o*) echo '{"type":"Organization"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_TODO","name":{"raw":"Todo"}}]},{"id":"F_RUN","name":"Run"},{"id":"F_START","name":"Run started"}]';;
  *items?per_page=100*) printf '[2001,"ITEM1","https://api.github.com/repos/x/y"]\n[2002,"ITEM_REFUSE","https://api.github.com/repos/x/y"]\n';;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { SG_STATE="$T/st" SG_REPO=x/y SG_BOARD_READ_BACKOFF=0 python3 "$T/s-board.py" "$@"; }
o=$(run --run-fields o 4 2001 run-abc 2026-10-13T08:00:00Z 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "writing Run fields exits 0" || ko "exit $rc: $o"
/usr/bin/grep -q '"id": "F_RUN", "value": "run-abc"' "$T/patch.in" 2>/dev/null && ok "Run carries the run id" || ko "Run field: $(cat "$T/patch.in" 2>/dev/null)
$o"
/usr/bin/grep -q '"id": "F_START", "value": "2026-10-13T08:00:00Z"' "$T/patch.in" 2>/dev/null && ok "Run started carries the run's start time" || ko "Run started field: $(cat "$T/patch.in" 2>/dev/null)"
[ "$(wc -l < "$T/gh.calls" | tr -d ' ')" = "4" ] && ok "one card is one PATCH (owner kind, fields, items, patch)" || ko "gh calls: $(cat "$T/gh.calls")"
[ -e "$T/st/board-dirty" ] && ok "the hub is flagged to refresh" || ko "board-dirty not touched"
rm -f "$T/patch.in"; o=$(run --clear-run o 4 2001 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "clearing Run fields exits 0" || ko "clear exit $rc: $o"
[ "$(grep -o '"value": null' "$T/patch.in" 2>/dev/null | wc -l | tr -d ' ')" = "2" ] && ok "both text fields are cleared with null, the value the endpoint documents" || ko "clear payload: $(cat "$T/patch.in" 2>/dev/null)"
o=$(run --run-fields o 4 2009 run-abc "" 2>&1); rc=$?
[ "$rc" -eq 1 ] && case $o in *"2009 not on the board"*) ok "an issue not on the board is named and exits 1";; *) ko "not-on-board message: $o";; esac || ko "not-on-board exit $rc: $o"
o=$(run --run-fields o 4 2002 run-abc "" 2>&1); rc=$?
[ "$rc" -eq 1 ] && case $o in *"Resource not accessible"*) ok "a refused write is named on stderr and exits 1";; *) ko "refusal message: $o";; esac || ko "refusal exit $rc: $o"
rm -rf "$T"; exit $fail
