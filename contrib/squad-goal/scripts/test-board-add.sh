#!/bin/bash
# `board.py --add` is idempotent: an issue the board does not carry is added and its Status written in the same call;
# an add GitHub refuses because the item already exists (a stale cached item list) is repaired by one re-read of the
# items, and the id that read finds is written to, with no error named. Only a second miss is a failure, named on
# stderr and exiting non-zero. Stubbed gh; a temp state dir, so the live board and the loop's cache are never touched.
# The success path is proven the way the refusal path is: the answer to a PATCH proves nothing (a field write can be
# answered ok and never carried), so the card is read back and the Status it carries is the proof (#105). A card whose
# Status is unset is NOT written: the write is retried, and a card no read then carries the Status is a READ FAILURE,
# named, never a card written. The local state cache never records a card whose write is unproven.
# A miss on the item map re-reads the items with a bounded backoff (#96): the items route is eventually consistent, so a
# card written a moment ago is not always in the next read. A card a later read carries is written to, never reported
# absent; a card the refused add proved is on the board, which no read then carries, is a READ FAILURE, never "not on
# the board". SG_BOARD_READ_BACKOFF is 0 here, so the retries sleep for no read and the suite stays fast.
# usage: bash test-board-add.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; D="TMPDIR"
echo "$a" >> "$D/gh.calls"
case "$a" in *"--input -"*) cat > "$D/last.in";; esac
cards="2001 2002 2003 2004 2006 2007 2008 2009"
case "$a" in
  *users/o*) echo '{"type":"Organization"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_TODO","name":{"raw":"Todo"}}]}]';;
  *items?per_page=100\&fields*)   # the Status read: the option id the card carries, "" when it carries none (the write was lost or refused)
    for n in $cards; do [ -e "$D/seen-$n" ] && printf '[%s,"%s"]\n' "$n" "$(cat "$D/st-$n" 2>/dev/null)"; done; exit 0;;
  *items?per_page=100*)   # the items route is eventually consistent: the read is counted, and #2002 is only carried from the second read (#96)
    r=$(( $(cat "$D/reads" 2>/dev/null || echo 0) + 1 )); echo "$r" > "$D/reads"
    for n in $cards; do
      [ -e "$D/seen-$n" ] || continue
      [ "$n" = 2002 ] && [ "$r" -lt 2 ] && continue
      printf '[%s,"ITEM%s","https://api.github.com/repos/x/y"]\n' "$n" "${n#200}"
    done; exit 0;;
  *repos/x/y/issues/2002*) echo '{"id":3002}';;
  *repos/x/y/issues/2003*) echo '{"id":3003}';;
  *repos/x/y/issues/2004*) echo '{"id":3004}';;
  *repos/x/y/issues/2005*) echo '{"id":3005}';;
  *repos/x/y/issues/2006*) echo '{"id":3006}';;
  *repos/x/y/issues/2007*) echo '{"id":3007}';;
  *repos/x/y/issues/2008*) echo '{"id":3008}';;
  *repos/x/y/issues/2009*) echo '{"id":3009}';;
  *repos/x/y/issues/2011*) echo '{"id":3011}';;
  *"-X POST"*items*)
    case "$(cat "$D/last.in")" in
      *3003*) touch "$D/seen-2003"; echo '{"message":"Validation Failed: item already exists"}' >&2; exit 1;;   # the cache was stale: the item was there
      *3011*) echo '{"message":"Validation Failed: Content already exists in this project"}' >&2; exit 1;;   # the refusal proves the card is on the board; no read ever carries it
      *3004*) touch "$D/seen-2004"; echo '{"id":"ITEM4"}';;
      *3006*) touch "$D/seen-2006"; echo '{"id":"ITEM6"}';;
      *3007*) touch "$D/seen-2007"; echo '{"id":"ITEM7"}';;
      *3008*) touch "$D/seen-2008"; echo '{"id":"ITEM8"}';;
      *3009*) touch "$D/seen-2009"; echo '{"id":"ITEM9"}';;
      *) echo '{"message":"Resource not accessible by integration"}' >&2; exit 1;;
    esac;;
  *"-X PATCH"*items/ITEM*)
    i=${a##*items/ITEM}; i=${i%% *}; n=$((2000 + i)); w=$(sed -n 's/.*"value": "\([^"]*\)".*/\1/p' "$D/last.in")
    case "$n" in
      2006) echo '{"id":"ITEM6"}';;   # the write is answered ok and the card never carries it: the lost write of #105
      2008) echo '{"message":"Invalid field values (HTTP 400)"}' >&2; exit 1;;   # the field write is refused, and no card carries it
      2009) echo '{"message":"Invalid field values (HTTP 400)"}' >&2; exit 1;;   # refused, but the card already carries the Status
      2007) if [ -e "$D/dropped-2007" ]; then echo "$w" > "$D/st-2007"; echo '{"id":"ITEM7"}';   # the retry lands
            else touch "$D/dropped-2007"; echo '{"id":"ITEM7"}'; fi;;   # the first write is dropped, never carried
      *) echo "$w" > "$D/st-$n"; echo '{"id":"ITEM'"$i"'}';;   # the write lands: the card carries it
    esac;;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { SG_STATE="$T/st" SG_REPO=x/y SG_BOARD_READ_BACKOFF=0 python3 "$T/s-board.py" "$@"; }
echo '{"2001":"ITEM1"}' > "$T/st/rest-items-o-4.json"; touch "$T/seen-2001"   # a fresh cache that does not know #2003
o=$(run --add o 4 Todo 2003 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "a refused add whose re-read finds the item exits 0" || ko "exit $rc: $o"
case $o in *"already exists"*|*"not on the board"*) ko "the stale cache is reported as a defect: $o";; *) ok "nothing is named as a defect";; esac
/usr/bin/grep -q 'items/ITEM3 ' "$T/gh.calls" 2>/dev/null && ok "the Status is written to the id the re-read found" || ko "no PATCH on ITEM3: $(cat "$T/gh.calls")"
[ "$(/usr/bin/grep -c 'items?per_page=100 ' "$T/gh.calls")" = 2 ] && ok "the items are re-read once after the refused add" || ko "items reads: $(/usr/bin/grep -c 'items?per_page=100 ' "$T/gh.calls")"
/usr/bin/grep -q '"2003": "ITEM3"' "$T/st/rest-items-o-4.json" && ok "the cache carries the repaired item" || ko "cache: $(cat "$T/st/rest-items-o-4.json")"
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2004 2>&1); rc=$?
[ "$rc" -eq 0 ] && /usr/bin/grep -q 'items/ITEM4 ' "$T/gh.calls" && ok "a missing issue is added and its Status written in one call" || ko "add #2004 exit $rc: $o $(cat "$T/gh.calls")"
[ "$(/usr/bin/grep -c -- '-X POST' "$T/gh.calls")" = 1 ] && ok "one POST adds it" || ko "POSTs: $(cat "$T/gh.calls")"
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2004 2>&1); rc=$?
[ "$rc" -eq 0 ] && ! /usr/bin/grep -q -- '-X POST' "$T/gh.calls" && ok "an issue already on the board is not added twice" || ko "#2004 added again: $(cat "$T/gh.calls") $o"
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2005 2>&1); rc=$?
[ "$rc" -eq 1 ] && case $o in *"2005"*"Resource not accessible"*) ok "a second miss is a failure, named on stderr";; *) ko "second miss message: $o";; esac || ko "second miss exit $rc: $o"
[ "$(/usr/bin/grep -c 'items?per_page=100 ' "$T/gh.calls")" = 2 ] && ok "a failed add re-reads once, never twice" || ko "items reads: $(cat "$T/gh.calls")"
# the write is proven by the card, never by the answer to the PATCH (#105)
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2006 2>&1); rc=$?
[ "$rc" -eq 1 ] && ok "a Status write the card never carries is not a success" || ko "unproven write exit $rc: $o"
case $o in *"#2006"*"read failure"*) ok "it is named a read failure, not an absence";; *) ko "unproven write message: $o";; esac
case $o in *"-> Todo"*) ko "the unproven write is reported as written: $o";; *) ok "it never claims the card was written";; esac
/usr/bin/grep -q '"2006"' "$T/st/rest-items-o-4.json" && ko "the cache records a card whose write is unproven: $(cat "$T/st/rest-items-o-4.json")" || ok "the cache does not record the unproven card"
[ "$(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls")" -gt 1 ] && [ "$(/usr/bin/grep -c 'fields\[\]=' "$T/gh.calls")" -gt 1 ] && ok "the unproven write is retried" || ko "no retry: $(cat "$T/gh.calls")"
[ "$(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls")" -le 4 ] && [ "$(/usr/bin/grep -c 'fields\[\]=' "$T/gh.calls")" -le 4 ] && ok "the retry is bounded" || ko "retries: $(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls") PATCH, $(/usr/bin/grep -c 'fields\[\]=' "$T/gh.calls") reads"
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2007 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "a write a retry lands exits 0" || ko "repaired write exit $rc: $o"
case $o in *"#2007 -> Todo"*) ok "the card the read carries is reported as written";; *) ko "repaired write message: $o";; esac
[ "$(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls")" = 2 ] && ok "the dropped write is written again" || ko "PATCHes: $(cat "$T/gh.calls")"
/usr/bin/grep -q '"2007"' "$T/st/rest-items-o-4.json" && ok "a proven card stays in the cache" || ko "cache: $(cat "$T/st/rest-items-o-4.json")"
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2008 2>&1); rc=$?
[ "$rc" -eq 1 ] && ok "a field write the board refuses and the card never carries is not a success" || ko "refused write exit $rc: $o"
case $o in *"#2008"*"Invalid field values"*"read failure"*) ok "the refusal and the read failure are both named";; *) ko "refused write message: $o";; esac
case $o in *"-> Todo"*) ko "the refused write is reported as written: $o";; *) ok "it never claims the card was written";; esac
/usr/bin/grep -q '"2008"' "$T/st/rest-items-o-4.json" && ko "the cache records a refused card: $(cat "$T/st/rest-items-o-4.json")" || ok "the cache does not record the refused card"
echo O_TODO > "$T/st-2009"; : > "$T/gh.calls"; o=$(run --add o 4 Todo 2009 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "a refused PATCH whose card carries the Status is a success" || ko "carried write exit $rc: $o"
case $o in *"#2009 -> Todo"*) ok "the card is read back as the judge of the write";; *) ko "carried write message: $o";; esac
# the items read is behind the write (#96): #2002 is on the board, and only the second read carries it
touch "$T/seen-2002"; echo 0 > "$T/reads"; : > "$T/gh.calls"
o=$(run o 4 Todo 2002 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "a card the retry read carries exits 0" || ko "short read exit $rc: $o"
/usr/bin/grep -q 'items/ITEM2 ' "$T/gh.calls" 2>/dev/null && ok "the retry read finds it and its Status is written" || ko "no PATCH on ITEM2: $(cat "$T/gh.calls") $o"
[ "$(/usr/bin/grep -c 'items?per_page=100 ' "$T/gh.calls")" -ge 2 ] && ok "the reader re-read the items past the miss" || ko "items reads: $(cat "$T/gh.calls")"
case $o in *"not on the board"*) ko "a card a later read carries is reported as absent: $o";; *) ok "it is never reported as absent";; esac
# the write is confirmed (the refused add proves the card is there) but no read ever carries it: a read failure, named
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2011 2>&1); rc=$?
[ "$rc" -eq 1 ] && ok "a card no read carries, whose add was refused as already there, exits 1" || ko "read failure exit $rc: $o"
case $o in *"#2011"*"read failure"*) ok "it is named as a read failure";; *) ko "read-failure message: $o";; esac
case $o in *"not on the board"*) ko "a card the write proved is reported as absent: $o";; *) ok "it is never reported as absent";; esac
[ "$(/usr/bin/grep -c 'items?per_page=100 ' "$T/gh.calls")" -le 5 ] && ok "the re-read is bounded" || ko "items reads: $(cat "$T/gh.calls")"
rm -rf "$T"; exit $fail
