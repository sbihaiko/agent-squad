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
  *"/items"*) printf '1090\tTodo\thttps://api.github.com/repos/x/y\n1092\tTesting\thttps://api.github.com/repos/x/y\n1095\tTodo\thttps://api.github.com/repos/x/y\n1110\tTesting\thttps://api.github.com/repos/x/y\n1120\tTodo\thttps://api.github.com/repos/x/y\n1130\tBlocked\thttps://api.github.com/repos/x/y\n1140\tDone\thttps://api.github.com/repos/x/y\n';;   # the one Status read of the seam: number, state, repository url
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
  *"pulls?state=open"*) echo '[]';;
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
rm -rf "$T"; exit $fail
