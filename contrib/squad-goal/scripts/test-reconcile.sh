#!/bin/bash
# Regression test for reconcile.py's scope: RECONCILE_MIN floors open issues only; a closed issue of any number reaches Done (stub gh and board.sh, no network).
# And for its watermark (#246): a recomputed column lower in the shipped flow than the one the card carries is refused and reported
# by name, never written; a card behind the recomputed column moves forward as it always did; a revert keeps the column and adds the tag.
# usage: bash test-reconcile.sh [path/to/reconcile.py]
src=${1:-$(dirname "$0")/reconcile.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/bin" "$T/st"; test -f "$(dirname "$src")/board_columns.py" || exit 1; cp "$src" "$T/s/reconcile.py"; cp "$(dirname "$src")/board_columns.py" "$T/s/board_columns.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"
printf '#!/bin/bash\necho "$*" >> "%s/moves"\n' "$T" > "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  "api users/x") echo '{"type":"User"}';;
  *"/fields"*) echo '[{"id":7,"name":"Status"}]';;
  *"/items"*) printf '1090\tTodo\thttps://api.github.com/repos/x/y\n1092\tTesting\thttps://api.github.com/repos/x/y\n1095\tTodo\thttps://api.github.com/repos/x/y\n1110\tTesting\thttps://api.github.com/repos/x/y\n1120\tTodo\thttps://api.github.com/repos/x/y\n1130\tBlocked\thttps://api.github.com/repos/x/y\n1140\tDone\thttps://api.github.com/repos/x/y\n1150\tTodo\thttps://api.github.com/repos/x/y\n1160\tTodo\thttps://api.github.com/repos/x/y\n';;   # the one Status read of the seam: number, state, repository url
  *"--method POST"*"/labels"*) echo "$*" >> "TMPDIR/tags"; echo '[]';;   # the tag a refused move back writes: the tag plus a new issue, never a card moved back
  *"/labels"*) echo "$*" >> "TMPDIR/tagreads"; if [ -n "$HAS_TAG" ]; then echo '[{"name":"reverted"}]'; else echo '[]'; fi;;   # the labels the issue carries now
  *"issues/1090") echo '{"state":"closed","body":""}';;
  *"issues/1092") echo '{"state":"closed","body":""}';;
  *"issues/1093") echo '{"state":"open","body":""}';;
  *"issues/1095") echo '{"state":"open","body":""}';;
  *"issues/1110") echo '{"state":"closed","body":""}';;
  *"issues/1120") echo '{"state":"open","body":"## Blocked by\n\n- #1093\n"}';;
  *"issues/1130") echo '{"state":"closed","body":""}';;
  *"issues/1140") echo '{"state":"open","body":""}';;
  *"pulls?state=open"*) echo "$*" >> "TMPDIR/pullreads"; echo '[{"number":9,"title":"x","body":"Refs #1160"}]';;   # an open PR names #1160: it must move no card
  *"repos/x/y/branches"*) echo '[{"name":"fix/1150-x","commit":{"sha":"sha1150"}},{"name":"main","commit":{"sha":"shamain"}}]';;   # a work branch names #1150 and is ahead of dev
  *"compare/dev...sha1150"*) echo '{"status":"ahead"}';;
  *"issues/1150") echo '{"state":"open","body":""}';;
  *"issues/1160") echo '{"state":"open","body":""}';;
  *"issues?state=open&labels=bug"*) echo '[]';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH" SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_STATE="$T/st" RECONCILE_MIN=1102
r=$(RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1); m=$(cat "$T/moves" 2>/dev/null)
case "$m" in *1090*) ok "a closed issue #1090 below RECONCILE_MIN moves to Done";; *) ko "a closed issue #1090 not moved: '$r'";; esac
case "$m" in *1092*) ok "a closed issue #1092 below RECONCILE_MIN moves to Done";; *) ko "a closed issue #1092 not moved: '$r'";; esac
case "$m" in *1110*) ok "a closed issue #1110 above RECONCILE_MIN still moves to Done";; *) ko "a closed issue #1110 not moved: '$r'";; esac
case "$r" in *"#1095"*) ko "open #1095 below RECONCILE_MIN was derived: '$r'";; *) ok "open issue below RECONCILE_MIN stays out of scope";; esac
# A card never moves backwards (#246): the card's own column is the watermark, and the shipped flow order says which way is back.
# (1) #1120 sits in Todo, its recomputed column is Blocked, one step back in the flow: it is not moved, and the attempt is named.
case "$m" in *1120*) ko "the backward move of #1120 was written: '$m'";; *) ok "a card one step ahead of the recomputed column is not moved";; esac
case "$r" in *"BACKWARD #1120: Todo -> Blocked"*) ok "the refused move names the card and both columns";; *) ko "the backward attempt of #1120 is not reported by name: '$r'";; esac
# (2) #1130 sits in Blocked and the issue closed: Done is ahead of the card, so it moves forward as before.
case "$m" in *"Done 1130"*) ok "a card behind the recomputed column moves forward";; *) ko "closed #1130 did not move forward: '$m'";; esac
case "$r" in *"BACKWARD #1130"*) ko "a forward move was reported as backwards: '$r'";; *) ok "a forward move is never reported as backwards";; esac
# (3) #1140 reached Done and the issue is open again, so the merge was taken back: the card keeps its column and carries the tag.
case "$m" in *1140*) ko "a revert moved #1140 back: '$m'";; *) ok "a revert keeps the column";; esac
case "$r" in *"BACKWARD #1140: Done -> Todo"*) ok "the revert is reported by name";; *) ko "the revert of #1140 is not reported by name: '$r'";; esac
case "$(cat "$T/tags" 2>/dev/null)" in *"issues/1140/labels"*reverted*|*reverted*"issues/1140/labels"*) ok "a revert adds the tag";; *) ko "a revert added no tag: '$(cat "$T/tags" 2>/dev/null)'";; esac
case "$(cat "$T/tags" 2>/dev/null)" in *"issues/1120"*) ko "a plain backward attempt tagged the card: '$(cat "$T/tags" 2>/dev/null)'";; *) ok "only a revert carries the tag";; esac
# (4) a reconcile run on an issue that already carries the tag reads the labels and sends no label write (#328, criterion c).
rm -f "$T/tags" "$T/tagreads"; r=$(HAS_TAG=1 RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1)
case "$(cat "$T/tagreads" 2>/dev/null)" in *"issues/1140/labels"*) ok "the labels are read before the tag is added";; *) ko "the labels of #1140 were not read: '$(cat "$T/tagreads" 2>/dev/null)'";; esac
[ ! -s "$T/tags" ] && ok "an issue that already has the tag gets no label write" || ko "a label write was sent for a tagged issue: '$(cat "$T/tags")'"
rm -f "$T/tags"; r=$(RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1)
[ "$(grep -c 'issues/1140/labels' "$T/tags" 2>/dev/null)" = 1 ] && ok "an issue without the tag still gets it" || ko "the tag was not added to an untagged issue: '$(cat "$T/tags" 2>/dev/null)'"
# The branch is the fact (#358): an open work branch ahead of dev moves its card to Code Complete; an open pull request moves none, and no pull request is read.
case "$m" in *"Code Complete 1150"*) ok "an open work branch ahead of dev moves its card to Code Complete";; *) ko "#1150 not moved on its branch: '$m' '$r'";; esac
case "$m" in *1160*) ko "an open pull request moved #1160: '$m'";; *) ok "an open pull request moves no card";; esac
[ ! -s "$T/pullreads" ] && ok "no pull request is read" || ko "a pull request route was read: $(cat "$T/pullreads")"
# The vocabulary the loop writes (ADR-0141, #454): the Squad Board carries the shipped names, and SG_BOARD_VOCAB=shipped tells
# every writer so. This pass wrote the internal names instead, so the carrier refused each write ("no Status option 'Done'") and
# the pass swallowed it -- the MOVE line it filed was a write that never landed, week after week.
rm -f "$T/moves"; r=$(SG_BOARD_VOCAB=shipped RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1); m=$(cat "$T/moves" 2>/dev/null)
case "$m" in *"Done 1090"*) ko "the shipped board was written the internal name Done: '$m'";; *) ok "no internal name reaches the writer under the shipped vocabulary";; esac
case "$m" in *"Closed 1090"*) ok "a closed issue moves to the shipped Closed";; *) ko "no shipped Closed for #1090 in the write: '$m' '$r'";; esac
# The writer takes four fields in one order -- `<owner> <project> <status> <n>` -- and the stub here echoes them all, so a
# caller that drops one is caught here rather than by the carrier: the pass that shipped #454 called `board.sh Closed 1090`,
# board.py read the status as the owner, and every write of the pass failed with an IndexError it named as a refusal (#457).
# The stub's own argv IS the contract, so the whole line is compared and not just the status that happens to sit in it.
case "$m" in *"x 4 Closed 1090"*) ok "the writer is called with owner, project, status and number, in that order";;
  *) ko "the write does not carry the writer's four fields: '$m'";; esac
case "$m" in *"x 4 Code Complete 1150"*) ok "a forward move carries the same four fields";;
  *) ko "a forward move lost a field of the writer's contract: '$m'";; esac
# A write the carrier refused is named on the pass's stream and ends the pass non-zero, never swallowed.
printf '#!/bin/bash\necho "no Status option $3" >&2; exit 1\n' > "$T/s/board.sh"; chmod +x "$T/s/board.sh"
r=$(RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1); rc=$?
case "$r" in *"WRITE REFUSED #1090"*"no Status option"*) ok "a refused write is named with the carrier's own text";; *) ko "a refused write was swallowed: '$r'";; esac
[ "$rc" != 0 ] && ok "a pass whose write was refused ends non-zero" || ko "the refused write still ended the pass 0"
rm -rf "$T"; exit $fail
