#!/bin/bash
# A card that a reset returns to Todo says so on its own issue: one comment, one label from the closed set, the label created
# when absent. The resets are no-landing, launch-refused and reopened (SG_MOVE_REASON). A repeat of the same event at the same
# sha writes no comment and no label. A forward move drops the reset label. A card that does not move gets neither.
# A card in Abandoned stays in Abandoned: the loop never moves it to Closed.
# Stubbed gh and a temp checkout for the sha; a temp state dir, so the live board and the loop's cache are never touched.
# usage: bash test-board-reset.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_MOVE_BY SG_MOVE_REASON SG_MOVE_EVENT
mkdir -p "$T/bin" "$T/st" "$T/co"; cp "$src" "$T/s-board.py"; d=$(dirname "$src"); cp "$d/issue_state.py" "$d/board_columns.py" "$d/board-definition.json" "$T/"
echo x/y > "$T/urlrepo"
git -C "$T/co" init -q && git -C "$T/co" -c user.name=t -c user.email=t@t commit -q --allow-empty -m base
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in *"/comments"*) cat > "TMPDIR/comment.in";; *"--input -"*) cat > "TMPDIR/patch.in";; esac
case "$a" in
  *"/comments"*) echo '{"id":1}';;
  *"/labels"*) echo '[]';;
  *"-X PATCH"*items/*)
    n=${a#*items/}; n=${n%% *}; n=${n#ITEM}
    opt=$(python3 -c 'import json;print(json.load(open("TMPDIR/patch.in"))["fields"][0]["value"])')
    name=${opt//_/ }
    /usr/bin/grep -v "^$n	" "TMPDIR/cards.tsv" > "TMPDIR/cards.new"; printf '%s\t%s\n' "$n" "$name" >> "TMPDIR/cards.new"; mv "TMPDIR/cards.new" "TMPDIR/cards.tsv"
    echo '{"id":"ITEM"}';;
  *users/o*) echo '{"type":"Organization"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"Backlog","name":{"raw":"Backlog"}},{"id":"Todo","name":{"raw":"Todo"}},{"id":"Doing","name":{"raw":"Doing"}},{"id":"Code_Review","name":{"raw":"Code Review"}},{"id":"Code_Complete","name":{"raw":"Code Complete"}},{"id":"Closed","name":{"raw":"Closed"}},{"id":"Abandoned","name":{"raw":"Abandoned"}}]}]';;
  *'fields[]=F_STATUS'*) while IFS=$'\t' read -r n s; do printf '%s\t%s\thttps://api.github.com/repos/%s\n' "$n" "$s" "$(cat "TMPDIR/urlrepo")"; done < "TMPDIR/cards.tsv";;
  *items?per_page=100*) u=$(cat "TMPDIR/urlrepo"); while IFS=$'\t' read -r n s; do printf '[%s,"ITEM%s","https://api.github.com/repos/%s"]\n' "$n" "$n" "$u"; done < "TMPDIR/cards.tsv";;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
sed -i.bak "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
seed() { printf '2001\tTodo\n2002\tDoing\n2003\tCode Review\n2004\tCode Complete\n2005\tClosed\n2007\tAbandoned\n' > "$T/cards.tsv"; : > "$T/gh.calls"; rm -f "$T/st/board-rejections.jsonl" "$T/st/board-resets.json" "$T/comment.in" "$T/patch.in"; }
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_BOARD_READ_BACKOFF=0 SG_BOARD_SETTLE_SECONDS=0 SG_REPO="$(cat "$T/urlrepo")" python3 "$T/s-board.py" "$@"; }
col() { awk -F'\t' -v n="$1" '$1 == n {print $2}' "$T/cards.tsv"; }
comments() { /usr/bin/grep -c "issues/$1/comments" "$T/gh.calls"; }
labeled() { /usr/bin/grep -F "labels[]=$2" "$T/gh.calls" | /usr/bin/grep -cF "issues/$1/labels"; }
dropped() { /usr/bin/grep -c "DELETE.*issues/$1/labels/$2" "$T/gh.calls"; }
created() { /usr/bin/grep -c "repos/x/y/labels.*name=$1" "$T/gh.calls"; }
sha=$(git -C "$T/co" rev-parse HEAD)

seed; o=$(SG_MOVE_BY=loop SG_MOVE_REASON=no-landing SG_MOVE_EVENT=run-a run o 4 Todo 2004 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2004)" = "Todo" ] && ok "a no-landing reset returns the card to Todo" || ko "no-landing reset: exit $rc, column $(col 2004): $o"
[ "$(comments 2004)" = 1 ] && ok "a no-landing reset posts one comment on its issue" || ko "no-landing comments: $(comments 2004): $(cat "$T/gh.calls")"
[ "$(labeled 2004 'board-reset:no-delivery')" = 1 ] && ok "a no-landing reset labels its issue board-reset:no-delivery" || ko "no-landing label: $(cat "$T/gh.calls")"
[ "$(created 'board-reset:no-delivery')" = 1 ] && ok "a reset label absent from the repository is created" || ko "label create: $(cat "$T/gh.calls")"
python3 -c 'import json,sys; b=json.load(open(sys.argv[1]))["body"]; sys.exit(0 if all(s in b for s in ("Code Complete","Todo","run-a")) else 1)' "$T/comment.in" 2>/dev/null \
  && ok "the comment names the column left, the column reached and the event id" || ko "comment body: $(cat "$T/comment.in" 2>/dev/null)"

seed; o=$(SG_MOVE_BY=loop SG_MOVE_REASON=launch-refused SG_MOVE_EVENT=run-b run o 4 Todo 2002 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2002)" = "Todo" ] && [ "$(comments 2002)" = 1 ] && [ "$(labeled 2002 'board-reset:launch-refused')" = 1 ] \
  && ok "a refused launch returns its card to Todo with one comment and board-reset:launch-refused" || ko "launch-refused: exit $rc, column $(col 2002), comments $(comments 2002): $o"

seed; o=$(SG_MOVE_BY=loop SG_MOVE_REASON=reopened SG_MOVE_EVENT=reopen-2005 run o 4 Todo 2005 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2005)" = "Todo" ] && [ "$(comments 2005)" = 1 ] && [ "$(labeled 2005 'board-reset:reopened')" = 1 ] \
  && ok "a reopened issue returns its card to Todo with one comment and board-reset:reopened" || ko "reopened: exit $rc, column $(col 2005), comments $(comments 2005): $o"

seed; SG_MOVE_BY=loop SG_MOVE_REASON=no-landing SG_MOVE_EVENT=run-a run o 4 Todo 2004 >/dev/null 2>&1
SG_MOVE_BY=loop run o 4 Doing 2004 >/dev/null 2>&1
[ "$(dropped 2004 'board-reset%3Ano-delivery')" = 1 ] && ok "a forward move drops the reset label it answers" || ko "forward drop: $(cat "$T/gh.calls")"
SG_MOVE_BY=loop SG_MOVE_REASON=no-landing SG_MOVE_EVENT=run-a run o 4 Todo 2004 >/dev/null 2>&1; rc=$?
[ "$(comments 2004)" = 1 ] && [ "$(labeled 2004 'board-reset:no-delivery')" = 1 ] \
  && ok "a repeat of the same event at the same sha writes no comment and no label" || ko "repeat: comments $(comments 2004), labels $(labeled 2004 'board-reset:no-delivery'): $(cat "$T/gh.calls")"

seed; o=$(SG_MOVE_BY=loop SG_MOVE_REASON=no-landing SG_MOVE_EVENT=run-c run o 4 Todo 2001 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(comments 2001)" = 0 ] && [ "$(labeled 2001 'board-reset')" = 0 ] \
  && ok "a reset on a card that does not move writes no comment and no label" || ko "no move: exit $rc, comments $(comments 2001), labels $(labeled 2001 'board-reset'): $o"

seed; o=$(SG_MOVE_BY=loop run o 4 Closed 2007 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2007)" = "Abandoned" ] && [ "$(comments 2007)" = 0 ] \
  && ok "a card in Abandoned stays in Abandoned when its issue closes" || ko "abandoned: exit $rc, column $(col 2007): $o"

rm -rf "$T"; [ "$fail" -eq 0 ] && echo "all reset checks passed" || { echo "reset checks failed"; exit 1; }
