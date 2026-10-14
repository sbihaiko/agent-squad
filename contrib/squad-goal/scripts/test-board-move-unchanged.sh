#!/bin/bash
# A card move is sent only when its value changes: `board.py <owner> <project> "<Status>" <issue#>...` (and `--add`) reads the board
# once, and a card that already carries the Status is not written -- no PATCH, no proving read, exit 0 (#327). A card in another
# column, or a card the board does not carry yet, is still written and proven by the read that follows. A board that does not
# answer the read writes on, so a read that fails never blocks a move. Every write is counted against a stubbed gh.
# Stubbed gh; a temp state dir, so the live board and the loop's cache are never read or written.
# usage: bash test-board-move-unchanged.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st"; cp "$src" "$T/s-board.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"
printf '2001\tTodo\n2002\tDoing\n' > "$T/cards.tsv"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in *"--input -"*) cat > "TMPDIR/patch.in";; esac
case "$a" in
  *"-X PATCH"*items/ITEM*)
    echo PATCH >> "TMPDIR/patches"
    n=${a#*items/ITEM}; n=${n%% *}
    opt=$(python3 -c 'import json;print(json.load(open("TMPDIR/patch.in"))["fields"][0]["value"])')
    name=Todo; [ "$opt" = O_DOING ] && name=Doing
    /usr/bin/grep -v "^$n	" "TMPDIR/cards.tsv" > "TMPDIR/cards.new"; printf '%s\t%s\n' "$n" "$name" >> "TMPDIR/cards.new"; mv "TMPDIR/cards.new" "TMPDIR/cards.tsv"
    echo '{"id":"ITEM"}';;
  *users/o*) echo '{"type":"Organization"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_TODO","name":{"raw":"Todo"}},{"id":"O_DOING","name":{"raw":"Doing"}}]}]';;
  *'fields[]=F_STATUS'*) [ -e "TMPDIR/board-down" ] && { echo "API rate limit exceeded" >&2; exit 1; }
    echo read >> "TMPDIR/reads"; while IFS=$'\t' read -r n s; do printf '%s\t%s\thttps://api.github.com/repos/x/y\n' "$n" "$s"; done < "TMPDIR/cards.tsv";;
  *items?per_page=100*) printf '[2001,"ITEM2001","https://api.github.com/repos/x/y"]\n[2002,"ITEM2002","https://api.github.com/repos/x/y"]\n';;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
sed -i '' "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { SG_STATE="$T/st" SG_REPO=x/y SG_BOARD_READ_BACKOFF=0 python3 "$T/s-board.py" "$@"; }
patches() { [ -e "$T/patches" ] && wc -l < "$T/patches" | tr -d ' ' || echo 0; }
reads() { [ -e "$T/reads" ] && wc -l < "$T/reads" | tr -d ' ' || echo 0; }
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
rm -rf "$T"; exit $fail
