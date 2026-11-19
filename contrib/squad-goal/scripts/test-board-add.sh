#!/bin/bash
# seconds: 8.13
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
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; D="TMPDIR"
echo "$a" >> "$D/gh.calls"
case "$a" in *"--input -"*) cat > "$D/last.in";; esac
cards="2001 2002 2003 2004 2006 2007 2008 2009 2012 2013 2014 2015 2016 2017 2018"
case "$a" in
  *users/o*) echo '{"type":"Organization"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_TODO","name":{"raw":"Todo"}},{"id":"O_DOING","name":{"raw":"Doing"}},{"id":"O_DONE","name":{"raw":"Done"}}]}]';;
  *items?per_page=100\&fields*)   # the one Status read: number, the state the card carries (its option id), the repository url
    s15=$(( $(cat "$D/sreads15" 2>/dev/null || echo 0) + 1 )); echo "$s15" > "$D/sreads15"
    for n in $cards; do [ "$n" = 2015 ] && [ "$s15" -lt 6 ] && continue   # #2015 is carried from the sixth Status read only (#403)
      [ -e "$D/seen-$n" ] || continue
      v=$(cat "$D/st-$n" 2>/dev/null)
      if [ "$n" = 2016 ]; then s16=$(( $(cat "$D/sreads16" 2>/dev/null || echo 0) + 1 )); echo "$s16" > "$D/sreads16"; [ "$s16" -lt 6 ] && v=O_TODO; fi   # #2016 reads its old Status until the sixth Status read (#434)
      if [ "$n" = 2018 ]; then s18=$(( $(cat "$D/sreads18" 2>/dev/null || echo 0) + 1 )); echo "$s18" > "$D/sreads18"; [ "$s18" -ge 3 ] && v=O_DONE; fi   # #2018 is moved by another writer to a third Status (O_DONE) from the third Status read
      printf '%s\t%s\t%s\n' "$n" "$v" "https://api.github.com/repos/x/y"; done; exit 0;;
  *items?per_page=100*)   # the items route is eventually consistent: the read is counted, and #2002 is only carried from the second read (#96)
    r=$(( $(cat "$D/reads" 2>/dev/null || echo 0) + 1 )); echo "$r" > "$D/reads"
    for n in $cards; do
      [ -e "$D/seen-$n" ] || continue
      [ "$n" = 2002 ] && [ "$r" -lt 2 ] && continue
      if [ "$n" = 2014 ]; then r14=$(( $(cat "$D/reads14" 2>/dev/null || echo 0) + 1 )); echo "$r14" > "$D/reads14"; [ "$r14" -lt 7 ] && continue; fi   # #2014 is carried from its seventh read only (#403)
      printf '[%s,"ITEM%s","https://api.github.com/repos/x/y"]\n' "$n" "$((n - 2000))"
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
  *repos/x/y/issues/2014*) echo '{"id":3014}';;
  *"-X POST"*items*)
    case "$(cat "$D/last.in")" in
      *3003*) touch "$D/seen-2003"; echo '{"message":"Validation Failed: item already exists"}' >&2; exit 1;;   # the cache was stale: the item was there
      *3011*) echo '{"message":"Validation Failed: Content already exists in this project"}' >&2; exit 1;;   # the refusal proves the card is on the board; no read ever carries it
      *3014*) touch "$D/seen-2014"; echo '{"message":"Validation Failed: Content already exists in this project"}' >&2; exit 1;;   # a new card the items read lags behind (#403)
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
      2012|2013) case "$(cat "$D/last.in")" in
          *O_OLD*|*O_GONE*) echo '{"message":"Invalid field values (HTTP 400)"}' >&2; exit 1;;   # the option id the cache holds is not the board's any more
          *) echo "$w" > "$D/st-$n"; echo '{"id":"ITEM'"$i"'}';;
        esac;;
      2017|2018) echo '{"id":"ITEM'"$i"'}';;   # accepted, and the card never carries it (#2017 stays on Todo; #2018 is moved on by another writer)
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
echo '{"2001":"ITEM1"}' > "$T/st/rest-items-o-4-x_y.json"; touch "$T/seen-2001"   # a fresh cache that does not know #2003
o=$(run --add o 4 Todo 2003 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "a refused add whose re-read finds the item exits 0" || ko "exit $rc: $o"
case $o in *"already exists"*|*"not on the board"*) ko "the stale cache is reported as a defect: $o";; *) ok "nothing is named as a defect";; esac
/usr/bin/grep -q 'items/ITEM3 ' "$T/gh.calls" 2>/dev/null && ok "the Status is written to the id the re-read found" || ko "no PATCH on ITEM3: $(cat "$T/gh.calls")"
[ "$(/usr/bin/grep -c 'items?per_page=100 ' "$T/gh.calls")" = 2 ] && ok "the items are re-read once after the refused add" || ko "items reads: $(/usr/bin/grep -c 'items?per_page=100 ' "$T/gh.calls")"
/usr/bin/grep -q '"2003": "ITEM3"' "$T/st/rest-items-o-4-x_y.json" && ok "the cache carries the repaired item" || ko "cache: $(cat "$T/st/rest-items-o-4-x_y.json")"
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
/usr/bin/grep -q '"2006"' "$T/st/rest-items-o-4-x_y.json" && ko "the cache records a card whose write is unproven: $(cat "$T/st/rest-items-o-4-x_y.json")" || ok "the cache does not record the unproven card"
[ "$(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls")" -gt 1 ] && [ "$(/usr/bin/grep -c 'fields\[\]=' "$T/gh.calls")" -gt 1 ] && ok "the unproven write is retried" || ko "no retry: $(cat "$T/gh.calls")"
# The bound is the one board.py enforces on this card: SG_BOARD_READ_ATTEMPTS (4) gives the write one attempt fewer than
# the bound (3), and each attempt costs its one-card read plus the whole-board read that answers a miss (#1300), beside the
# one compare read: 3 PATCH and 7 reads is the whole budget, never a fourth write (ADR-0018, re-anchored from the 4 the
# suite held before the miss read existed).
[ "$(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls")" -le 3 ] && [ "$(/usr/bin/grep -c 'fields\[\]=' "$T/gh.calls")" -le 7 ] && ok "the retry is bounded" || ko "retries: $(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls") PATCH, $(/usr/bin/grep -c 'fields\[\]=' "$T/gh.calls") reads"
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2007 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "a write a retry lands exits 0" || ko "repaired write exit $rc: $o"
case $o in *"#2007 -> Todo"*) ok "the card the read carries is reported as written";; *) ko "repaired write message: $o";; esac
[ "$(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls")" = 2 ] && ok "the dropped write is written again" || ko "PATCHes: $(cat "$T/gh.calls")"
/usr/bin/grep -q '"2007"' "$T/st/rest-items-o-4-x_y.json" && ok "a proven card stays in the cache" || ko "cache: $(cat "$T/st/rest-items-o-4-x_y.json")"
: > "$T/gh.calls"; o=$(run --add o 4 Todo 2008 2>&1); rc=$?
[ "$rc" -eq 1 ] && ok "a field write the board refuses and the card never carries is not a success" || ko "refused write exit $rc: $o"
case $o in *"#2008"*"Invalid field values"*"read failure"*) ok "the refusal and the read failure are both named";; *) ko "refused write message: $o";; esac
case $o in *"-> Todo"*) ko "the refused write is reported as written: $o";; *) ok "it never claims the card was written";; esac
/usr/bin/grep -q '"2008"' "$T/st/rest-items-o-4-x_y.json" && ko "the cache records a refused card: $(cat "$T/st/rest-items-o-4-x_y.json")" || ok "the cache does not record the refused card"
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
# a cached Status option id the board no longer carries (a migrated or recreated option) is refused "Invalid field values":
# the refusal drops the cached fields, the next attempt reads them again and sends the id the board carries now
echo '{"id":"F_STATUS","opt":{"Todo":"O_OLD"}}' > "$T/st/rest-fields-o-4.json"; touch "$T/seen-2012"; echo '{"2012":"ITEM12"}' > "$T/st/rest-items-o-4-x_y.json"; : > "$T/gh.calls"
o=$(run o 4 Todo 2012 2>&1); rc=$?
[ "$rc" -eq 0 ] && case $o in *"#2012 -> Todo"*) ok "a stale cached option id is re-read after the refusal and the write lands";; *) ko "stale id message: $o";; esac || ko "stale option id exit $rc: $o"
# an option the board dropped for good: named as no option, never a traceback and never a write of the dead id
echo '{"id":"F_STATUS","opt":{"Todo":"O_OLD","Gone":"O_GONE"}}' > "$T/st/rest-fields-o-4.json"; touch "$T/seen-2013"; echo '{"2013":"ITEM13"}' > "$T/st/rest-items-o-4-x_y.json"; : > "$T/gh.calls"
o=$(run o 4 Gone 2013 2>&1); rc=$?
[ "$rc" -eq 1 ] && case $o in *"no Status option 'Gone'"*) ok "an option the board dropped is named, exit 1";; *) ko "dropped option message: $o";; esac || ko "dropped option exit $rc: $o"
case $o in *Traceback*) ko "a traceback: $o";; *) ok "no traceback";; esac
# (#403) a new card is eventually consistent: a card the add proved, or a write the PATCH accepted, can be missing from
# reads for longer than the bounded attempts. The read-back keeps reading within SG_BOARD_SETTLE_SECONDS before it names a
# READ FAILURE, so the first move of a new issue is not a refusal that autobug files as one more bug.
settle() { SG_STATE="$T/st" SG_REPO=x/y SG_BOARD_READ_BACKOFF=0.01 SG_BOARD_SETTLE_SECONDS=3 python3 "$T/s-board.py" "$@"; }
: > "$T/gh.calls"; o=$(settle --add o 4 Todo 2014 2>&1); rc=$?
[ "$rc" -eq 0 ] && case $o in *"#2014 -> Todo"*) ok "a card the add proved, carried only after the bounded attempts, is written within the settle bound";; *) ko "settle add message: $o";; esac || ko "settle add exit $rc: $o"
echo '{"2015":"ITEM15"}' > "$T/st/rest-items-o-4-x_y.json"; touch "$T/seen-2015"; rm -f "$T/sreads15"; : > "$T/gh.calls"
o=$(settle o 4 Todo 2015 2>&1); rc=$?
[ "$rc" -eq 0 ] && case $o in *"#2015 -> Todo"*) ok "a write whose card the read carries only after the bounded attempts is proven within the settle bound";; *) ko "settle write message: $o";; esac || ko "settle write exit $rc: $o"
: > "$T/gh.calls"; o=$(settle --add o 4 Todo 2011 2>&1); rc=$?
[ "$rc" -eq 1 ] && case $o in *"#2011"*"read failure"*) ok "a card no read carries within the settle bound is still a read failure";; *) ko "settle failure message: $o";; esac || ko "settle failure exit $rc: $o"
# (#434) the same lag on a card that already carries a Status: GitHub accepted the write, and the read still answers the
# old Status (Todo) for longer than the bounded attempts. That is the read behind the write, as for a card with no Status
# yet, so it is read again within the settle bound, never named "carries no 'Doing' after 3 reads" a few seconds after the
# write. The settle only reads: it never writes again, so a card another writer moved is never moved back (ADR-0143).
echo '{"2016":"ITEM16"}' > "$T/st/rest-items-o-4-x_y.json"; touch "$T/seen-2016"; echo O_TODO > "$T/st-2016"; rm -f "$T/sreads16"; : > "$T/gh.calls"
o=$(settle o 4 Doing 2016 2>&1); rc=$?
[ "$rc" -eq 0 ] && case $o in *"#2016 -> Doing"*) ok "a write whose card the read shows on its old Status past the bounded attempts is proven within the settle bound";; *) ko "settle old-Status message: $o";; esac || ko "settle old-Status exit $rc: $o"
[ "$(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls")" = 3 ] && ok "the settle reads and never writes the card again: exactly the 3 bounded attempts, no 4th PATCH" || ko "settle wrote again: $(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls") PATCHes"
# (#438) a write GitHub accepted that an old-Status card never carries is a lost write, not lag: its settle is capped
# (SG_BOARD_OLD_SETTLE_SECONDS, default 10), so it does not wait the full SG_BOARD_SETTLE_SECONDS before the failure.
echo '{"2017":"ITEM17"}' > "$T/st/rest-items-o-4-x_y.json"; touch "$T/seen-2017"; echo O_TODO > "$T/st-2017"; : > "$T/gh.calls"
t0=$(date +%s); o=$(SG_BOARD_OLD_SETTLE_SECONDS=0.1 SG_STATE="$T/st" SG_REPO=x/y SG_BOARD_READ_BACKOFF=0.01 SG_BOARD_SETTLE_SECONDS=30 python3 "$T/s-board.py" o 4 Doing 2017 2>&1); rc=$?; t1=$(date +%s)
[ "$rc" -eq 1 ] && case $o in *"#2017"*"read failure"*) ok "a lost write on an old-Status card is a read failure";; *) ko "lost write message: $o";; esac || ko "lost write exit $rc: $o"
[ $((t1 - t0)) -lt 5 ] && ok "its settle is capped: it does not wait the full settle bound" || ko "the lost write waited $((t1 - t0)) s"
[ "$(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls")" = 3 ] && ok "a lost write is written for the 3 bounded attempts only" || ko "lost write PATCHes: $(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls")"
# another writer moved the card to a third Status during the settle: the settle never moves it back (ADR-0143).
echo '{"2018":"ITEM18"}' > "$T/st/rest-items-o-4-x_y.json"; touch "$T/seen-2018"; echo O_TODO > "$T/st-2018"; rm -f "$T/sreads18"; : > "$T/gh.calls"
o=$(SG_BOARD_OLD_SETTLE_SECONDS=0.5 settle o 4 Doing 2018 2>&1); rc=$?
[ "$rc" -eq 1 ] && case $o in *"#2018"*"read failure"*) ok "a card another writer moved to a third Status is not claimed as written";; *) ko "third-Status message: $o";; esac || ko "third-Status exit $rc: $o"
[ "$(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls")" = 3 ] && ok "a card moved to a third Status is written for the 3 bounded attempts only, never moved back by the settle" || ko "third-Status PATCHes: $(/usr/bin/grep -c -- '-X PATCH' "$T/gh.calls")"
rm -rf "$T"; exit $fail
