#!/bin/bash
# A card moves forward only (ADR-0143). An automated writer (any Status write whose SG_MOVE_BY is not `owner`) that would
# move a card to an earlier column is rejected: the card keeps its column, the issue gets the `rework` label, and the
# rejection is logged to $SG_STATE/board-rejections.jsonl. The one backward move is Doing back to Todo, the loop undoing its
# own claim. Backlog is the owner's column: an automated writer reaches it only as the first column of a card with none.
# Stubbed gh; a temp state dir, so the live board and the loop's cache are never read or written.
# usage: bash test-board-forward.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_MOVE_BY
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"; d=$(dirname "$src"); cp "$d/issue_state.py" "$d/board_columns.py" "$d/board-definition.json" "$T/"
echo x/y > "$T/urlrepo"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in *"--input -"*) cat > "TMPDIR/patch.in";; esac
case "$a" in
  *"/labels"*) echo '[]';;
  *"-X PATCH"*items/*)
    n=${a#*items/}; n=${n%% *}; n=${n#ITEM}
    opt=$(python3 -c 'import json;print(json.load(open("TMPDIR/patch.in"))["fields"][0]["value"])')
    name=${opt//_/ }
    /usr/bin/grep -v "^$n	" "TMPDIR/cards.tsv" > "TMPDIR/cards.new"; printf '%s\t%s\n' "$n" "$name" >> "TMPDIR/cards.new"; mv "TMPDIR/cards.new" "TMPDIR/cards.tsv"
    echo '{"id":"ITEM"}';;
  *users/o*) echo '{"type":"Organization"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"Backlog","name":{"raw":"Backlog"}},{"id":"Todo","name":{"raw":"Todo"}},{"id":"Doing","name":{"raw":"Doing"}},{"id":"Code_Review","name":{"raw":"Code Review"}},{"id":"Code_Complete","name":{"raw":"Code Complete"}},{"id":"Closed","name":{"raw":"Closed"}}]}]';;
  *'fields[]=F_STATUS'*) while IFS=$'\t' read -r n s; do printf '%s\t%s\thttps://api.github.com/repos/%s\n' "$n" "$s" "$(cat "TMPDIR/urlrepo")"; done < "TMPDIR/cards.tsv";;
  *items?per_page=100*) u=$(cat "TMPDIR/urlrepo"); while IFS=$'\t' read -r n s; do printf '[%s,"ITEM%s","https://api.github.com/repos/%s"]\n' "$n" "$n" "$u"; done < "TMPDIR/cards.tsv";;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
sed -i.bak "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
seed() { printf '2001\tTodo\n2002\tDoing\n2003\tCode Review\n2004\tCode Complete\n2005\tClosed\n' > "$T/cards.tsv"; : > "$T/gh.calls"; rm -f "$T/st/board-rejections.jsonl"; }
run() { SG_STATE="$T/st" SG_BOARD_READ_BACKOFF=0 SG_BOARD_SETTLE_SECONDS=0 SG_REPO="$(cat "$T/urlrepo")" python3 "$T/s-board.py" "$@"; }
col() { awk -F'\t' -v n="$1" '$1 == n {print $2}' "$T/cards.tsv"; }
rework() { /usr/bin/grep -c "issues/$1/labels" "$T/gh.calls"; }
rej() { [ -f "$T/st/board-rejections.jsonl" ] && wc -l < "$T/st/board-rejections.jsonl" | tr -d ' ' || echo 0; }

seed; o=$(SG_MOVE_BY=loop run o 4 "Todo" 2003 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2003)" = "Code Review" ] && ok "a review rejection (Code Review to Todo) by a loop writer is refused, the card keeps Code Review" || ko "review rejection: exit $rc, column $(col 2003): $o"
[ "$(rework 2003)" -ge 1 ] && ok "a refused backward move adds the rework label to its issue" || ko "rework label missing: $(cat "$T/gh.calls")"
[ "$(rej)" = 1 ] && grep -q '"issue": 2003' "$T/st/board-rejections.jsonl" && grep -q '"from": "Code Review"' "$T/st/board-rejections.jsonl" && ok "the refusal is logged with its issue and column" || ko "rejection log: $(cat "$T/st/board-rejections.jsonl" 2>/dev/null)"

seed; o=$(SG_MOVE_BY=loop run o 4 "Doing" 2004 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2004)" = "Code Complete" ] && ok "a requeue or failed landing (Code Complete to Doing) keeps its column" || ko "code complete backward: exit $rc, column $(col 2004): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 "Todo" 2005 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2005)" = "Closed" ] && ok "a reopening (Closed to Todo) by a loop writer keeps Closed" || ko "reopen: exit $rc, column $(col 2005): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 "Backlog" 2001 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2001)" = "Todo" ] && ok "a loop writer never moves a card to Backlog" || ko "loop to Backlog: exit $rc, column $(col 2001): $o"

seed; o=$(SG_MOVE_BY=owner run o 4 "Backlog" 2001 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2001)" = "Backlog" ] && ok "the owner moves a card to Backlog" || ko "owner to Backlog: exit $rc, column $(col 2001): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 "Todo" 2002 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2002)" = "Todo" ] && ok "the one backward move, Doing back to Todo, is allowed for a loop writer" || ko "unclaim: exit $rc, column $(col 2002): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 "Doing" 2001 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2001)" = "Doing" ] && ok "a forward move by a loop writer is written" || ko "forward: exit $rc, column $(col 2001): $o"

seed; o=$(SG_MOVE_BY=owner run o 4 "Todo" 2004 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2004)" = "Todo" ] && ok "the owner's backward move is written" || ko "owner backward: exit $rc, column $(col 2004): $o"

seed; o=$(run o 4 "Todo" 2003 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2003)" = "Code Review" ] && ok "a write with no SG_MOVE_BY is an automated writer and is refused" || ko "no context: exit $rc, column $(col 2003): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 "Doing" 2005 2>&1); rc=$?
[ "$(col 2005)" = "Closed" ] && [ "$rc" -eq 3 ] && ok "a terminal card stays terminal for a loop writer" || ko "terminal: exit $rc, column $(col 2005): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 "Todo" 2003 2001 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2003)" = "Code Review" ] && [ "$(col 2001)" = "Todo" ] && ok "a refusal in a batch leaves the other cards of the batch to their own moves" || ko "batch: exit $rc: $o"

rm -rf "$T"; [ "$fail" -eq 0 ] && echo "all forward checks passed" || { echo "forward checks failed"; exit 1; }
