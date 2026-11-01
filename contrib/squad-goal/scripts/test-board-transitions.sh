#!/bin/bash
# A move the door proves is one transition in its repository's own record, `board-transitions.<owner>_<name>.jsonl`
# beside the state: the card, the column it came from, the column it went to, the instant and the repository (#897).
# A move to the column the card already holds writes no transition. Two repositories on one board keep two records,
# and a later process appends to the record without rewriting the lines an earlier process wrote.
# Stubbed gh; a temp state dir, so the live board and the loop's cache are never read or written.
# usage: bash test-board-transitions.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"
printf '2001\tTodo\n2002\tDoing\n2003\tTodo\n' > "$T/cards.tsv"; echo x/y > "$T/urlrepo"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in *"--input -"*) cat > "TMPDIR/patch.in";; esac
case "$a" in
  *"-X PATCH"*items/*)
    n=${a#*items/}; n=${n%% *}; n=${n#ITEM}; [ "$n" = 3003 ] && n=2003
    opt=$(python3 -c 'import json;print(json.load(open("TMPDIR/patch.in"))["fields"][0]["value"])')
    name=Todo; [ "$opt" = O_DOING ] && name=Doing
    /usr/bin/grep -v "^$n	" "TMPDIR/cards.tsv" > "TMPDIR/cards.new"; printf '%s\t%s\n' "$n" "$name" >> "TMPDIR/cards.new"; mv "TMPDIR/cards.new" "TMPDIR/cards.tsv"
    echo '{"id":"ITEM"}';;
  *users/o*) echo '{"type":"Organization"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_TODO","name":{"raw":"Todo"}},{"id":"O_DOING","name":{"raw":"Doing"}}]}]';;
  *'fields[]=F_STATUS'*) while IFS=$'\t' read -r n s; do printf '%s\t%s\thttps://api.github.com/repos/%s\n' "$n" "$s" "$(cat "TMPDIR/urlrepo")"; done < "TMPDIR/cards.tsv";;
  *items?per_page=100*) u=$(cat "TMPDIR/urlrepo"); printf '[2001,"ITEM2001","https://api.github.com/repos/%s"]\n[2002,"ITEM2002","https://api.github.com/repos/%s"]\n[2003,3003,"https://api.github.com/repos/%s"]\n' "$u" "$u" "$u";;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
sed -i.bak "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { SG_STATE="$T/st" SG_BOARD_READ_BACKOFF=0 SG_REPO="$(cat "$T/urlrepo")" python3 "$T/s-board.py" "$@"; }
rec() { python3 -c 'import json,sys
for l in open(sys.argv[1]): d = json.loads(l); print(d["issue"], d["from"], d["to"], d["repo"])' "$1" 2>/dev/null; }
X="$T/st/board-transitions.x_y.jsonl"; Y="$T/st/board-transitions.a_b.jsonl"

o=$(run o 4 Doing 2001 2>&1); rc=$?
o2=$(run o 4 Todo 2001 2>&1); rc2=$?
[ "$rc" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$(rec "$X" | wc -l | tr -d ' ')" = 2 ] && ok "two moves across two columns record two transitions" || ko "two moves: exit $rc/$rc2, records: $(rec "$X"): $o $o2"
rec "$X" | sed -n 1p | grep -q '^2001 Todo Doing x/y$' && ok "the first transition names its card, from, to and repository" || ko "first transition: $(rec "$X")"
rec "$X" | sed -n 2p | grep -q '^2001 Doing Todo x/y$' && ok "the second transition takes the first one's to as its from" || ko "second transition: $(rec "$X")"
o=$(run o 4 Todo 2001 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(rec "$X" | wc -l | tr -d ' ')" = 2 ] && ok "a move to the column the card already holds writes no transition" || ko "same-column move recorded: exit $rc, $(rec "$X"): $o"

echo a/b > "$T/urlrepo"; o=$(run o 4 Todo 2002 2>&1); rc=$?
echo x/y > "$T/urlrepo"
X_LINES=$(rec "$X" | wc -l | tr -d ' ')
[ "$X_LINES" = 2 ] && ok "a move in another repository leaves the first repository's record unchanged" || ko "first record after another repository's move: $X_LINES lines: $(rec "$X")"
[ "$(rec "$Y")" = "2002 Doing Todo a/b" ] && ok "the other repository keeps its own record of the same board" || ko "other repository record: exit $rc, $(rec "$Y"): $o"

before=$(rec "$X" | head -n 2)
o=$(run o 4 Doing 2003 2>&1); rc=$?
after=$(rec "$X" | head -n 2)
[ "$rc" -eq 0 ] && [ "$before" = "$after" ] && [ "$(rec "$X" | wc -l | tr -d ' ')" = 3 ] && ok "a later process appends and keeps the lines an earlier process wrote" || ko "restart: exit $rc, before [$before] after [$after]: $o"

rm -rf "$T"; [ "$fail" -eq 0 ] && echo "all transition checks passed" || { echo "transition checks failed"; exit 1; }
