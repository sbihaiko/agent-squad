#!/bin/bash
# A card moves forward only (ADR-0143). An automated writer (any Status write whose SG_MOVE_BY is not `owner`) that would
# move a card to an earlier column is rejected: the card keeps its column, the issue gets the `rework` label, and the
# rejection is logged to $SG_STATE/board-rejections.jsonl. The one backward move is back to Todo: the loop undoing its own
# claim (Doing), or its own column a run that landed nothing left behind (SG_MOVE_REASON=no-landing, #1059). Backlog is the
# owner's column: an automated writer reaches it only as the first column of a card with none.
# Stubbed gh; a temp state dir, so the live board and the loop's cache are never read or written.
# usage: bash test-board-forward.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_MOVE_BY
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"; d=$(dirname "$src"); cp "$d/issue_state.py" "$d/board_columns.py" "$d/board-definition.json" "$T/"
echo x/y > "$T/urlrepo"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
[ -e "TMPDIR/fail-next-read" ] && case "$a" in *"-X PATCH"*|*"-X POST"*) ;; *) rm -f "TMPDIR/fail-next-read"; echo "read refused" >&2; exit 1;; esac
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
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"Backlog","name":{"raw":"Backlog"}},{"id":"Todo","name":{"raw":"Todo"}},{"id":"Doing","name":{"raw":"Doing"}},{"id":"Review","name":{"raw":"Review"}},{"id":"Landing","name":{"raw":"Landing"}},{"id":"Closed","name":{"raw":"Closed"}}]}]';;
  *'fields[]=F_STATUS'*) while IFS=$'\t' read -r n s; do printf '%s\t%s\thttps://api.github.com/repos/%s\n' "$n" "$s" "$(cat "TMPDIR/urlrepo")"; done < "TMPDIR/cards.tsv";;
  *items?per_page=100*) u=$(cat "TMPDIR/urlrepo"); while IFS=$'\t' read -r n s; do printf '[%s,"ITEM%s","https://api.github.com/repos/%s"]\n' "$n" "$n" "$u"; done < "TMPDIR/cards.tsv";;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
sed -i.bak "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
seed() { printf '2001\tTodo\n2002\tDoing\n2003\tReview\n2004\tLanding\n2005\tClosed\n2006\tBacklog\n' > "$T/cards.tsv"; : > "$T/gh.calls"; rm -f "$T/st/board-rejections.jsonl" "$T/fail-next-read"; }
run() { SG_STATE="$T/st" SG_BOARD_READ_BACKOFF=0 SG_BOARD_SETTLE_SECONDS=0 SG_REPO="$(cat "$T/urlrepo")" python3 "$T/s-board.py" "$@"; }
col() { awk -F'\t' -v n="$1" '$1 == n {print $2}' "$T/cards.tsv"; }
rework() { /usr/bin/grep -c "issues/$1/labels" "$T/gh.calls"; }
rej() { [ -f "$T/st/board-rejections.jsonl" ] && wc -l < "$T/st/board-rejections.jsonl" | tr -d ' ' || echo 0; }

seed; o=$(SG_MOVE_BY=loop run o 4 "Todo" 2003 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2003)" = "Review" ] && ok "a review rejection (Review to Todo) by a loop writer is refused, the card keeps Review" || ko "review rejection: exit $rc, column $(col 2003): $o"
[ "$(rework 2003)" -ge 1 ] && ok "a refused backward move adds the rework label to its issue" || ko "rework label missing: $(cat "$T/gh.calls")"
[ "$(rej)" = 1 ] && grep -q '"issue": 2003' "$T/st/board-rejections.jsonl" && grep -q '"from": "Review"' "$T/st/board-rejections.jsonl" && ok "the refusal is logged with its issue and column" || ko "rejection log: $(cat "$T/st/board-rejections.jsonl" 2>/dev/null)"

seed; o=$(SG_MOVE_BY=loop run o 4 "Doing" 2004 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2004)" = "Landing" ] && ok "a requeue or failed landing (Landing to Doing) keeps its column" || ko "code complete backward: exit $rc, column $(col 2004): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 "Todo" 2005 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2005)" = "Closed" ] && ok "a reopening (Closed to Todo) by a loop writer keeps Closed" || ko "reopen: exit $rc, column $(col 2005): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 "Backlog" 2001 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2001)" = "Todo" ] && ok "a loop writer never moves a card to Backlog" || ko "loop to Backlog: exit $rc, column $(col 2001): $o"

seed; o=$(SG_MOVE_BY=owner run o 4 "Backlog" 2001 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2001)" = "Backlog" ] && ok "the owner moves a card to Backlog" || ko "owner to Backlog: exit $rc, column $(col 2001): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 "Todo" 2002 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2002)" = "Todo" ] && ok "the one backward move, Doing back to Todo, is allowed for a loop writer" || ko "unclaim: exit $rc, column $(col 2002): $o"

seed; o=$(SG_MOVE_BY=loop SG_MOVE_REASON=no-landing run o 4 "Todo" 2004 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2004)" = "Todo" ] && ok "a run that landed nothing unclaims its card from Landing (#1059)" || ko "no-landing unclaim: exit $rc, column $(col 2004): $o"

seed; o=$(SG_MOVE_BY=loop SG_MOVE_REASON=no-landing run o 4 "Backlog" 2004 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2004)" = "Landing" ] && ok "the no-landing exception reaches Todo only, never Backlog" || ko "no-landing to Backlog: exit $rc, column $(col 2004): $o"

seed; o=$(SG_MOVE_BY=owner SG_MOVE_REASON=no-landing run o 4 "Todo" 2004 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2004)" = "Todo" ] && ok "the owner's door moves a card back with or without the reason" || ko "owner unclaim: exit $rc, column $(col 2004): $o"
seed; o=$(SG_MOVE_BY=loop run o 4 "Doing" 2001 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2001)" = "Doing" ] && ok "a forward move by a loop writer is written" || ko "forward: exit $rc, column $(col 2001): $o"

seed; o=$(SG_MOVE_BY=owner run o 4 "Todo" 2004 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2004)" = "Todo" ] && ok "the owner's backward move is written" || ko "owner backward: exit $rc, column $(col 2004): $o"

seed; o=$(run o 4 "Todo" 2003 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2003)" = "Review" ] && ok "a write with no SG_MOVE_BY is an automated writer and is refused" || ko "no context: exit $rc, column $(col 2003): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 "Doing" 2005 2>&1); rc=$?
[ "$(col 2005)" = "Closed" ] && [ "$rc" -eq 3 ] && ok "a terminal card stays terminal for a loop writer" || ko "terminal: exit $rc, column $(col 2005): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 "Todo" 2003 2001 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2003)" = "Review" ] && [ "$(col 2001)" = "Todo" ] && ok "a refusal in a batch leaves the other cards of the batch to their own moves" || ko "batch: exit $rc: $o"

# #1909: only the owner moves a Backlog card to Todo. A hold names itself, keeps the card in Backlog, writes no rework label,
# and is recorded; the owner's door moves it.
seed; o=$(run o 4 "Todo" 2006 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2006)" = "Backlog" ] && [ "$(rework 2006)" = 0 ] && [ "$(rej)" = 1 ] && grep -q '"issue": 2006' "$T/st/board-rejections.jsonl" && printf '%s' "$o" | /usr/bin/grep -q 'held' \
  && ok "an automated Backlog to Todo move is held: exit 3, the card keeps Backlog, no rework label, the hold is recorded" || ko "backlog hold: exit $rc, column $(col 2006), rework $(rework 2006), rejections $(rej): $o"
seed; o=$(SG_MOVE_BY=owner run o 4 "Todo" 2006 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2006)" = "Todo" ] && ok "the owner moves a Backlog card to Todo" || ko "owner Backlog to Todo: exit $rc, column $(col 2006): $o"
for why in no-landing reopened; do
  seed; o=$(SG_MOVE_BY=loop SG_MOVE_REASON=$why run o 4 "Todo" 2006 2>&1); rc=$?
  [ "$rc" -ne 0 ] && [ "$(col 2006)" = "Backlog" ] && ok "a $why reason does not lift the Backlog hold" || ko "$why hold: exit $rc, column $(col 2006): $o"
done
# A compare read that fails names the failure and writes no Todo: an automated writer cannot see the card, so it does not move it.
seed; : > "$T/fail-next-read"; o=$(run o 4 "Todo" 2006 2>&1); rc=$?
[ "$rc" -ne 0 ] && [ "$(col 2006)" = "Backlog" ] && ! /usr/bin/grep -qF -- '-X PATCH' "$T/gh.calls" && printf '%s' "$o" | /usr/bin/grep -qi 'read' \
  && ok "an automated Todo write does not occur when the compare read fails" || ko "read failure: exit $rc, column $(col 2006), calls $(cat "$T/gh.calls"): $o"
# #1909: a transition records who asked (by), the caller's pass (pass) and the reason; board.sh names `hand` when no pass is given.
sh_move() { env -u SG_GH_PASS -u SG_MOVE_BY "$@" SG_STATE="$T/st" SG_BOARD_READ_BACKOFF=0 SG_BOARD_SETTLE_SECONDS=0 SG_REPO="$(cat "$T/urlrepo")" bash "$d/board.sh" o 4 Doing 2001; }
seed; rm -f "$T/st/board-transitions.x_y.jsonl"; sh_move SG_GH_PASS=janitor SG_MOVE_BY=loop >/dev/null 2>&1
case "$(cat "$T/st/board-transitions.x_y.jsonl" 2>/dev/null)" in *'"by": "loop"'*'"pass": "janitor"'*) ok "a move through board.sh records by loop and pass janitor";; *) ko "janitor pass: $(cat "$T/st/board-transitions.x_y.jsonl" 2>/dev/null)";; esac
seed; rm -f "$T/st/board-transitions.x_y.jsonl"; sh_move SG_MOVE_BY=owner >/dev/null 2>&1
case "$(cat "$T/st/board-transitions.x_y.jsonl" 2>/dev/null)" in *'"by": "owner"'*'"pass": "hand"'*) ok "an owner move with no pass records by owner and pass hand";; *) ko "owner move: $(cat "$T/st/board-transitions.x_y.jsonl" 2>/dev/null)";; esac
seed; rm -f "$T/st/board-transitions.x_y.jsonl"; sh_move >/dev/null 2>&1
case "$(cat "$T/st/board-transitions.x_y.jsonl" 2>/dev/null)" in *'"by": null'*'"pass": "hand"'*) ok "a move with no context records pass hand and no by";; *) ko "no context: $(cat "$T/st/board-transitions.x_y.jsonl" 2>/dev/null)";; esac
seed; env -u SG_GH_PASS SG_STATE="$T/st" SG_BOARD_READ_BACKOFF=0 SG_BOARD_SETTLE_SECONDS=0 SG_REPO="$(cat "$T/urlrepo")" SG_GH_PASS=janitor bash "$d/board.sh" o 4 Todo 2006 >/dev/null 2>&1
case "$(cat "$T/st/board-rejections.jsonl" 2>/dev/null)" in *'"pass": "janitor"'*'"reason": null'*) ok "a refusal records its pass and reason";; *) ko "refusal record: $(cat "$T/st/board-rejections.jsonl" 2>/dev/null)";; esac
rm -rf "$T"; [ "$fail" -eq 0 ] && echo "all forward checks passed" || { echo "forward checks failed"; exit 1; }
