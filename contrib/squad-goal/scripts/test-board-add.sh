#!/bin/bash
# `board.py --add` is idempotent: an issue the board does not carry is added and its Status written in the same call;
# an add GitHub refuses because the item already exists (a stale cached item list) is repaired by one re-read of the
# items, and the id that read finds is written to, with no error named. Only a second miss is a failure, named on
# stderr and exiting non-zero. Stubbed gh; a temp state dir, so the live board and the loop's cache are never touched.
# usage: bash test-board-add.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in *"--input -"*) cat > "TMPDIR/last.in";; esac
case "$a" in
  *users/o*) echo '{"type":"Organization"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_TODO","name":{"raw":"Todo"}}]}]';;
  *items?per_page=100*)
    printf '[2001,"ITEM1","https://api.github.com/repos/x/y"]\n'
    [ -e "TMPDIR/seen-2003" ] && printf '[2003,"ITEM3","https://api.github.com/repos/x/y"]\n'
    [ -e "TMPDIR/seen-2004" ] && printf '[2004,"ITEM4","https://api.github.com/repos/x/y"]\n'; exit 0;;
  *repos/x/y/issues/2003*) echo '{"id":3003}';;
  *repos/x/y/issues/2004*) echo '{"id":3004}';;
  *repos/x/y/issues/2005*) echo '{"id":3005}';;
  *"-X POST"*items*)
    case "$(cat "TMPDIR/last.in")" in
      *3003*) touch "TMPDIR/seen-2003"; echo '{"message":"Validation Failed: item already exists"}' >&2; exit 1;;   # the cache was stale: the item was there
      *3004*) touch "TMPDIR/seen-2004"; echo '{"id":"ITEM4"}';;
      *) echo '{"message":"Resource not accessible by integration"}' >&2; exit 1;;
    esac;;
  *"-X PATCH"*items/ITEM*) echo '{"id":"x"}';;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s-board.py" "$@"; }
echo '{"2001":"ITEM1"}' > "$T/st/rest-items-o-4.json"   # a fresh cache that does not know #2003
o=$(run --add o 4 Todo 2003 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "a refused add whose re-read finds the item exits 0" || ko "exit $rc: $o"
case $o in *"already exists"*|*"not on the board"*) ko "the stale cache is reported as a defect: $o";; *) ok "nothing is named as a defect";; esac
/usr/bin/grep -q 'items/ITEM3 ' "$T/gh.calls" 2>/dev/null && ok "the Status is written to the id the re-read found" || ko "no PATCH on ITEM3: $(cat "$T/gh.calls")"
[ "$(/usr/bin/grep -c 'items?per_page=100' "$T/gh.calls")" = 2 ] && ok "the items are re-read once after the refused add" || ko "items reads: $(/usr/bin/grep -c 'items?per_page=100' "$T/gh.calls")"
/usr/bin/grep -q '"2003": "ITEM3"' "$T/st/rest-items-o-4.json" && ok "the cache carries the repaired item" || ko "cache: $(cat "$T/st/rest-items-o-4.json")"
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2004 2>&1); rc=$?
[ "$rc" -eq 0 ] && /usr/bin/grep -q 'items/ITEM4 ' "$T/gh.calls" && ok "a missing issue is added and its Status written in one call" || ko "add #2004 exit $rc: $o $(cat "$T/gh.calls")"
[ "$(/usr/bin/grep -c -- '-X POST' "$T/gh.calls")" = 1 ] && ok "one POST adds it" || ko "POSTs: $(cat "$T/gh.calls")"
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2004 2>&1); rc=$?
[ "$rc" -eq 0 ] && ! /usr/bin/grep -q -- '-X POST' "$T/gh.calls" && ok "an issue already on the board is not added twice" || ko "#2004 added again: $(cat "$T/gh.calls") $o"
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2005 2>&1); rc=$?
[ "$rc" -eq 1 ] && case $o in *"2005"*"Resource not accessible"*) ok "a second miss is a failure, named on stderr";; *) ko "second miss message: $o";; esac || ko "second miss exit $rc: $o"
[ "$(/usr/bin/grep -c 'items?per_page=100' "$T/gh.calls")" = 2 ] && ok "a failed add re-reads once, never twice" || ko "items reads: $(cat "$T/gh.calls")"
rm -rf "$T"; exit $fail
