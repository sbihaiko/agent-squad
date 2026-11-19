#!/bin/bash
# seconds: 1.18
# The Todo card limit holds one move only: a card that leaves Backlog for Todo (#2437). A full Todo refuses that move and
# names the limit; the card keeps Backlog and the pass exits 3. A bug still enters Todo at once: its card is added in Todo
# with no card before, and an add is never a move (ADR-0148). A Todo under its limit, or with no limit, moves the card.
# Stubbed gh; a temp state dir and a temp checkout, so the live board, the loop state and the project file are never read.
# usage: bash test-board-todo-limit.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_MOVE_BY
mkdir -p "$T/bin" "$T/st" "$T/co/docs/squad"
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
  *repos/x/y/issues/*) n=${a#*issues/}; n=${n%% *}; echo "{\"id\":$n}";;   # the issue's database id, the add's input
  *"-X POST"*items*)   # the add of a new card: it joins the board with no Status yet, then its own move writes Todo
    n=$(python3 -c 'import json;print(json.load(open("TMPDIR/patch.in"))["id"])')
    printf '%s\t\n' "$n" >> "TMPDIR/cards.tsv"; echo "{\"id\":\"ITEM$n\"}";;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"Backlog","name":{"raw":"Backlog"}},{"id":"Todo","name":{"raw":"Todo"}},{"id":"Doing","name":{"raw":"Doing"}},{"id":"Closed","name":{"raw":"Closed"}}]}]';;
  *'fields[]=F_STATUS'*) while IFS=$'\t' read -r n s; do printf '%s\t%s\thttps://api.github.com/repos/%s\n' "$n" "$s" "$(cat "TMPDIR/urlrepo")"; done < "TMPDIR/cards.tsv";;
  *items?per_page=100*) u=$(cat "TMPDIR/urlrepo"); while IFS=$'\t' read -r n s; do printf '[%s,"ITEM%s","https://api.github.com/repos/%s"]\n' "$n" "$n" "$u"; done < "TMPDIR/cards.tsv";;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
sed -i.bak "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
seed() { printf '2001\tTodo\n2006\tBacklog\n' > "$T/cards.tsv"; : > "$T/gh.calls"; rm -f "$T/st/board-rejections.jsonl"; }
limit() { printf 'columns:\n  Todo:\n    max_cards: %s\n' "$1" > "$T/co/docs/squad/defaults.yaml"; }
nolimit() { printf 'columns:\n  Todo:\n    max_cards: none\n' > "$T/co/docs/squad/defaults.yaml"; }
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_BOARD_READ_BACKOFF=0 SG_BOARD_SETTLE_SECONDS=0 SG_REPO="$(cat "$T/urlrepo")" python3 "$src" "$@"; }
col() { awk -F'\t' -v n="$1" '$1 == n {print $2}' "$T/cards.tsv"; }

# 1 (#2437): Todo at its limit refuses the move from Backlog and names the limit; the card keeps Backlog.
seed; limit 1; o=$(SG_MOVE_BY=owner run o 4 "Todo" 2006 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2006)" = "Backlog" ] && printf '%s' "$o" | /usr/bin/grep -q 'Todo limit of 1 card' \
  && ok "a full Todo refuses the Backlog to Todo move, names the limit, and keeps the card in Backlog" || ko "full Todo move: exit $rc, column $(col 2006): $o"
[ -f "$T/st/board-rejections.jsonl" ] && /usr/bin/grep -q '"issue": 2006' "$T/st/board-rejections.jsonl" && ok "the refusal is recorded with its issue" || ko "refusal not recorded: $(cat "$T/st/board-rejections.jsonl" 2>/dev/null)"

# 2 (#2437, ADR-0148): a bug still enters Todo at once -- its card is added in Todo with no card before, and an add is no move.
seed; limit 1; o=$(SG_MOVE_BY=owner run --add o 4 "Todo" 2007 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2007)" = "Todo" ] && ok "a bug whose card is added in Todo enters a full Todo at once" || ko "bug filing into a full Todo: exit $rc, column $(col 2007): $o"

# 3 (#2437): a Todo under its limit moves the card from Backlog.
seed; limit 2; o=$(SG_MOVE_BY=owner run o 4 "Todo" 2006 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2006)" = "Todo" ] && ok "a Todo under its limit takes the Backlog card" || ko "under the limit: exit $rc, column $(col 2006): $o"

# 4 (#2437): no Todo limit moves the card from Backlog.
seed; nolimit; o=$(SG_MOVE_BY=owner run o 4 "Todo" 2006 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2006)" = "Todo" ] && ok "a Todo with no limit takes the Backlog card" || ko "no limit: exit $rc, column $(col 2006): $o"

rm -rf "$T"
[ "$fail" = 0 ] && echo "board-todo-limit: all pass" || echo "board-todo-limit: FAIL"
exit "$fail"
