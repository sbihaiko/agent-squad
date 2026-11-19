#!/bin/bash
# seconds: 1.01
# A finished test pass is recorded by `board.py --test-case <owner> <project> <issue#> <pass|fail> <merge-commit> [ended]`: one
# `test case` item on the board, through the seam (#300). The read that follows the write is the proof, so an item no read carries
# is named on stderr and exits non-zero, never reported as written; a pass that ended failed stays failed.
# Stubbed gh keeps the board's items in a file; a temp state dir, so the live board and the loop's cache are never read or written.
# usage: bash test-board-test-case.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in *"--input -"*) cat > "TMPDIR/in";; esac
case "$a" in
  *"-X POST"*items*) [ -e "TMPDIR/drop" ] || python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["title"])' "TMPDIR/in" >> "TMPDIR/titles"; echo '{"id":"D1"}';;
  *"-X PATCH"*items/D1*) [ -e "TMPDIR/drop" ] || echo kind >> "TMPDIR/kinds"; echo '{"id":"D1"}';;
  *users/o*) echo '{"type":"Organization"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[]},{"id":"F_KIND","name":"Kind","options":[{"id":"O_TC","name":{"raw":"Test case"}}]}]';;
  *items?per_page=100*) python3 -c '
import json,os
t=open("TMPDIR/titles").read().splitlines() if os.path.exists("TMPDIR/titles") else []
k=os.path.exists("TMPDIR/kinds")
print("\n".join("%s\t%s\ttest case" % ("D1",x) for x in t) if k else "")' ;;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
sed -i '' "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { SG_STATE="$T/st" SG_REPO=x/y SG_BOARD_READ_BACKOFF=0 python3 "$T/s-board.py" "$@"; }
SHA=1641bc94f52afca78acdf6a915a200cccff3e5e9
o=$(run --test-case o 4 300 pass $SHA 2026-10-14T03:00:00Z 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "a finished pass is recorded and exits 0" || ko "exit $rc: $o"
[ "$(wc -l < "$T/titles" | tr -d ' ')" = "1" ] && /usr/bin/grep -q "300 | pass | $SHA |" "$T/titles" && ok "one item names the issue, the result and the merge commit" || ko "titles: $(cat "$T/titles" 2>/dev/null)"
[ -e "$T/st/board-dirty" ] && ok "the hub is flagged to refresh" || ko "board-dirty not touched"
[ ! -e "$T/st/board-moves.json" ] && ok "a test-case write records no move" || ko "a test-case write wrote a record: $(cat "$T/st/board-moves.json")"
run --test-case o 4 300 fail $SHA 2026-10-14T03:30:00Z >/dev/null 2>&1
o=$(run --test-case o 4 300 pass $SHA 2026-10-14T04:00:00Z 2>&1); rc=$?
[ "$rc" -eq 0 ] && case $o in *"test case fail"*) ok "a later green pass on a failed commit is answered with the failure, which stays failed";; *) ko "repeat: $o";; esac || ko "repeat exit $rc: $o"
o=$(run --test-case o 4 300 maybe $SHA 2>&1); rc=$?
[ "$rc" -eq 1 ] && ok "a result that is not pass or fail is refused and exits 1" || ko "bad result exit $rc: $o"
touch "$T/drop"   # the next write is answered ok and never lands
o=$(run --test-case o 4 301 pass $SHA 2026-10-14T05:00:00Z 2>&1); rc=$?
[ "$rc" -eq 1 ] && case $o in *"301"*"does not carry"*) ok "a write the next read does not carry is named and exits 1";; *) ko "unproven message: $o";; esac || ko "unproven exit $rc: $o"
rm -rf "$T"; exit $fail
