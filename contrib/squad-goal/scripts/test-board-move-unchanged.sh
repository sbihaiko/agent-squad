#!/bin/bash
# seconds: 1.72
# A card move is sent only when its value changes: `board.py <owner> <project> "<Status>" <issue#>...` (and `--add`) reads the card,
# and a card that already carries the Status is not written -- no PATCH, no proving read, exit 0 (#327). The card's own read is its
# one-item REST route when an id is known, and the whole-board read only when it is not (#1300, #1303). A hinted move to the column
# the card already holds is settled by that one card read, so it takes no proving read either (#1329). A card in another column, or
# a card the board does not carry yet, is still written and proven by the read that follows. A board that does not answer the read
# writes on, so a read that fails never blocks a move.
# Every read and every write is counted against a stubbed gh. The stub answers the exact calls the seam sends: the one-item route is
# `/projectsV2/<n>/items/<item id>?fields[]=<field id>` and its answer is `{isArchived, content.number, fields[].name/value.name.raw}`,
# and the item id -> issue map lives in `item-map`, the one table the item list, the card read and the PATCH all take from. An id the
# table does not name, a GraphQL call and any other unrecognized call are stub errors, never a guessed answer.
# A move the door proves is recorded beside the refresh flag, in `board-moves.json`: one entry per item, the column
# it set and the instant, replaced by a later move of the same item. A move the door cannot prove and a skipped move
# write no entry. The record is written to a temp file in the state directory and renamed, so a reader sees the old
# file or the new one, never a part-written one.
# Stubbed gh; a temp state dir, so the live board and the loop's cache are never read or written. The suite hands a board move no
# dispatch context of its own: a gate the live loop runs exports SG_MOVE_BY=loop (#861), and the suite asserts that a move with no
# context records none (#864, #1329).
# usage: bash test-board-move-unchanged.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/"
printf '2001\tTodo\n2002\tDoing\n2003\tTodo\n' > "$T/cards.tsv"
# item id -> issue, the fixture's board: 2003's item id is a bare number, as the REST items route answered it
printf 'ITEM2001\t2001\nITEM2002\t2002\n3003\t2003\n' > "$T/item-map"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in *"--input -"*) cat > "TMPDIR/patch.in";; esac
item() {   # the issue the fixture's item-map binds to this item id; an id the map does not name is a stub error, never a guess
  iss=$(/usr/bin/awk -F'\t' -v i="$1" '$1==i{print $2; exit}' "TMPDIR/item-map")
  [ -n "$iss" ] && { echo "$iss"; return 0; }
  echo "stub: no item-map entry for item id '$1' in: $a" >&2; return 1
}
case "$a" in
  *"/items/"*"fields[]"*)   # the card's own read: the one-item REST route, `/projectsV2/<n>/items/<item id>?fields[]=<field id>` (#1300, #1303)
    [ -e "TMPDIR/board-down" ] && { echo "API rate limit exceeded" >&2; exit 1; }
    echo node >> "TMPDIR/nodes"
    n=${a##*/items/}; n=${n%%/*}; n=${n%%\?*}; iss=$(item "$n") || exit 1
    st=$(/usr/bin/grep "^$iss	" "TMPDIR/cards.tsv" | cut -f2)
    printf '{"isArchived":false,"content":{"number":%s},"fields":[{"name":"Status","value":{"name":{"raw":"%s"}}}]}\n' "$iss" "$st";;
  *"-X PATCH"*"/items/"*)
    echo PATCH >> "TMPDIR/patches"
    [ -e "TMPDIR/drop" ] && { echo '{"id":"ITEM"}'; exit 0; }   # a write answered ok and never landed
    n=${a##*/items/}; n=${n%% *}; n=${n%%/*}; iss=$(item "$n") || exit 1
    opt=$(python3 -c 'import json;print(json.load(open("TMPDIR/patch.in"))["fields"][0]["value"])')
    name=Todo; [ "$opt" = O_DOING ] && name=Doing
    /usr/bin/grep -v "^$iss	" "TMPDIR/cards.tsv" > "TMPDIR/cards.new"; printf '%s\t%s\n' "$iss" "$name" >> "TMPDIR/cards.new"; mv "TMPDIR/cards.new" "TMPDIR/cards.tsv"
    echo '{"id":"ITEM"}';;
  *users/o*) echo '{"type":"Organization"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_TODO","name":{"raw":"Todo"}},{"id":"O_DOING","name":{"raw":"Doing"}}]}]';;
  *'fields[]=F_STATUS'*) [ -e "TMPDIR/board-down" ] && { echo "API rate limit exceeded" >&2; exit 1; }
    echo read >> "TMPDIR/reads"; while IFS=$'\t' read -r n s; do printf '%s\t%s\thttps://api.github.com/repos/x/y\n' "$n" "$s"; done < "TMPDIR/cards.tsv";;
  *items?per_page=100*) while IFS=$'\t' read -r iid iss; do printf '[%s,"%s","https://api.github.com/repos/x/y"]\n' "$iss" "$iid"; done < "TMPDIR/item-map";;
  *graphql*) echo "stub: the seam sent a GraphQL call, and the loop reads over REST only: $a" >&2; exit 1;;   # a regression is red, never a silent missing count
  *) echo "stub: unrecognized call: $a" >&2; exit 1;;
esac
J
sed -i.bak "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { env -u SG_CHECKOUT SG_MOVE_BY="${RUN_MOVE_BY-}" SG_STATE="$T/st" SG_REPO=x/y SG_BOARD_READ_BACKOFF=0 python3 "$T/s-board.py" "$@"; }   # a context the suite names itself: none is inherited from the gate that runs this suite (#861, #864, #1329)
patches() { [ -e "$T/patches" ] && wc -l < "$T/patches" | tr -d ' ' || echo 0; }
reads() { [ -e "$T/reads" ] && wc -l < "$T/reads" | tr -d ' ' || echo 0; }
nodes() { [ -e "$T/nodes" ] && wc -l < "$T/nodes" | tr -d ' ' || echo 0; }
o=$(run o 4 Todo 2001 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(patches)" = 0 ] && ok "a move to the column the card already holds sends no PATCH" || ko "same-column move: exit $rc, $(patches) PATCH: $o"
[ "$(reads)" = 1 ] && ok "the skipped move costs one read of the board, and no proving read" || ko "reads for a skipped move: $(reads)"
o=$(run --add o 4 Doing 2002 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(patches)" = 0 ] && ok "--add over a card already in the column sends no PATCH" || ko "--add same column: exit $rc, $(patches) PATCH: $o"
o=$(run o 4 Todo 2001 2002 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(patches)" = 1 ] && ok "a batch writes only the card that changes (2002 Doing -> Todo)" || ko "batch: exit $rc, $(patches) PATCH: $o"
rm -f "$T/reads"; o=$(run o 4 Doing 2001 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(patches)" = 2 ] && grep -q '^2001	Doing' "$T/cards.tsv" && ok "a changed column is still written, and the card carries it" || ko "changed move: exit $rc, $(patches) PATCH: $o"
[ "$(reads)" = 2 ] && ok "a written move is read before (compare) and once after (proof)" || ko "reads for a written move: $(reads)"
touch "$T/board-down"; o=$(run o 4 Doing 2002 2>&1); rc=$?
[ "$(patches)" -ge 3 ] && ok "a board read that fails writes on, so a move is never blocked by the compare" || ko "board down: exit $rc, $(patches) PATCH: $o"
rm -f "$T/board-down"
cat > "$T/bin/rec.py" <<'P'
import json, sys
try: d = json.load(open(sys.argv[1]))
except (OSError, ValueError): print(); sys.exit()
for k, v in sorted(d.items()): print(k, v.get("column", ""), v.get("at", ""))
P
rec() { python3 "$T/bin/rec.py" "$T/st/board-moves.json"; }
entries() { rec | wc -l | tr -d ' '; }
run o 4 Todo 2001 >/dev/null 2>&1   # 2001 is Doing after the batch: set it to Todo
rm -f "$T/st/board-moves.json" "$T/st/board-dirty"
o=$(run o 4 Doing 2001 2>&1); rc=$?   # a proved move to Doing
r=$(rec)
[ "$rc" -eq 0 ] && [ "$(entries)" = 1 ] && case "$r" in "ITEM2001 Doing "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) ok "a proved move writes one entry with the item, the column and the instant";; *) ko "entry: $r";; esac || ko "proved move: exit $rc, entries $(entries): $o"
[ -e "$T/st/board-dirty" ] && ok "the proved move touches the refresh flag in the same step" || ko "board-dirty not touched by a proved move"
rm -f "$T/st/board-moves.json"
o=$(RUN_MOVE_BY=loop run o 4 Todo 2001 2>&1); rc=$?   # a move a loop script makes names its dispatch context
[ "$rc" -eq 0 ] && grep -q '"by": "loop"' "$T/st/board-moves.json" && ok "a move made with a dispatch context records it in the entry" || ko "a loop move records no dispatch context: exit $rc, record $(rec): $o"
rm -f "$T/st/board-moves.json"
o=$(run o 4 Doing 2001 2>&1); rc=$?   # no context named: the entry carries no `by`
[ "$rc" -eq 0 ] && [ -e "$T/st/board-moves.json" ] && ! grep -q '"by"' "$T/st/board-moves.json" && ok "a move with no dispatch context records no context" || ko "a move with no context recorded one: exit $rc, record $(rec): $o"
grep -q '^export SG_MOVE_BY=loop' "$(dirname "$src")/env.sh" && ok "env.sh exports the loop as the dispatch context of a loop script's move" || ko "env.sh exports no loop dispatch context"
rm -f "$T/st/board-moves.json" "$T/st/board-dirty"; touch "$T/drop"
o=$(run o 4 Todo 2001 2>&1); rc=$?   # the PATCH is answered ok and the card never carries it
[ "$rc" -ne 0 ] && [ ! -e "$T/st/board-moves.json" ] && ok "a move the door cannot prove writes no entry" || ko "unproved move: exit $rc, record $(rec): $o"
rm -f "$T/drop"
o=$(run o 4 Doing 2001 2>&1); rc=$?   # the unproved move left 2001 in Todo: move it to Doing for the skipped move
[ "$rc" -eq 0 ] && ok "setup: the card is in Doing for the skipped move" || ko "setup exit $rc: $o"
rm -f "$T/st/board-moves.json"; o=$(run o 4 Doing 2001 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ ! -e "$T/st/board-moves.json" ] && ok "a move to the column the card already carries writes no entry" || ko "skipped move: exit $rc, record $(rec): $o"
rm -f "$T/st/board-moves.json"
run o 4 Todo 2001 >/dev/null 2>&1; run o 4 Doing 2001 >/dev/null 2>&1
r=$(rec)
[ "$(entries)" = 1 ] && case "$r" in "ITEM2001 Doing "*) ok "two proved moves of one item leave one entry, the later one";; *) ko "later entry: $r";; esac || ko "entries after two moves: $(entries), $r"
o=$(run o 4 Doing 2003 2>&1); rc=$?   # a numeric item id joins a record whose keys were read back as text (#652)
[ "$rc" -eq 0 ] && [ "$(entries)" = 2 ] && case "$(rec)" in *"3003 Doing "*) ok "a move with a numeric item id after an earlier entry writes the record and exits 0";; *) ko "numeric id record: $(rec)";; esac || ko "numeric id move: exit $rc, $(entries) entries: $o"
run o 4 Todo 2001 >/dev/null 2>&1   # the kill interrupts a proved move away from Todo
python3 -c 'import json,sys; json.dump({"ITEM2001":{"column":"Todo","at":"2026-10-14T00:00:00Z"}}, open(sys.argv[1],"w"))' "$T/st/board-moves.json"
mkdir -p "$T/hooks"
cat > "$T/hooks/sitecustomize.py" <<'P'
import os
_real = os.replace
def replace(src, dst):
    if str(dst).endswith("board-moves.json"):   # the rename is where a reader would first see the new file: die just before it
        open("TMPDIR/replace.src", "w").write(str(src))
        raise RuntimeError("killed before the rename")
    return _real(src, dst)
os.replace = replace
P
sed -i.bak "s|TMPDIR|$T|g" "$T/hooks/sitecustomize.py"
o=$(PYTHONPATH="$T/hooks" run o 4 Doing 2001 2>&1); rc=$?   # 2001 is Todo after the two moves: the write the kill interrupts
before=$(rec)
[ "$rc" -ne 0 ] && case "$before" in "ITEM2001 Todo "*) ok "a write killed before the rename leaves the old record whole, never a part-written file";; *) ko "record after the kill: $before";; esac || ko "killed write exit $rc: $o"
s=$(cat "$T/replace.src" 2>/dev/null)
[ -n "$s" ] && [ "$(dirname "$s")" = "$T/st" ] && case "$(basename "$s")" in board-moves.json.*.tmp) ok "the record is formed in a temp file in the state directory";; *) ko "temp name: $(basename "$s")";; esac || ko "no temp beside the record: $s"
case "$(python3 "$T/bin/rec.py" "$s" 2>/dev/null)" in "ITEM2001 Doing "*) ok "the temp file holds the finished record before the rename";; *) ko "temp content: $(python3 "$T/bin/rec.py" "$s" 2>/dev/null)";; esac
# One hinted move (#1300): the hub names the card's item, so the move reads no whole-project list for the id, and its compare and
# its proof are the card's own node reads. No whole-board read is sent.
printf '2001\tTodo\n2002\tDoing\n2003\tTodo\n' > "$T/cards.tsv"; rm -f "$T/reads" "$T/nodes" "$T/gh.calls" "$T/patches"
export SG_BOARD_ITEM_HINTS='{"2002":"ITEM2002"}'; o=$(run o 4 Todo 2002 2>&1); rc=$?; unset SG_BOARD_ITEM_HINTS
[ "$rc" -eq 0 ] && grep -q '^2002	Todo' "$T/cards.tsv" && ok "a hinted move writes the card and the card carries the Status" || ko "hinted move: exit $rc $o"
[ "$(reads)" = 0 ] && ok "a hinted move sends no whole-board read" || ko "whole-board reads for a hinted move: $(reads)"
n=$(/usr/bin/grep -c 'items?per_page' "$T/gh.calls" 2>/dev/null); [ "${n:-0}" = 0 ] && ok "a hinted move reads no whole-project item list for its id" || ko "item list reads: ${n:-0}"
[ "$(nodes)" -ge 2 ] && ok "the compare and the proof are node reads of the card" || ko "node reads: $(nodes)"
# A hinted move to the column the card already holds (#1329): the card's own read settles it, so no PATCH, no proving read,
# and no whole-board read either -- the hub named the item, so nothing has to look the card up.
printf '2001\tTodo\n2002\tDoing\n2003\tTodo\n' > "$T/cards.tsv"; rm -f "$T/reads" "$T/nodes" "$T/gh.calls" "$T/patches"
export SG_BOARD_ITEM_HINTS='{"2002":"ITEM2002"}'; o=$(run o 4 Doing 2002 2>&1); rc=$?; unset SG_BOARD_ITEM_HINTS
[ "$rc" -eq 0 ] && [ "$(patches)" = 0 ] && ok "a hinted move to the column the card already holds sends no PATCH" || ko "hinted same-column move: exit $rc, $(patches) PATCH: $o"
[ "$(reads)" = 0 ] && ok "a hinted move to the held column sends no whole-board read" || ko "whole-board reads for a hinted same-column move: $(reads)"
[ "$(nodes)" = 1 ] && ok "the held column is settled by the card's own node read, and no proving read follows" || ko "node reads for a hinted same-column move: $(nodes)"
rm -rf "$T"; exit $fail
