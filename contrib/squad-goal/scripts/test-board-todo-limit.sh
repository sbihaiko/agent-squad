#!/bin/bash
# seconds: 1.5
# The Todo card limit holds one move only: a card that leaves Backlog for Todo (#2437). A full Todo refuses that move and
# names the limit; the card keeps Backlog and the pass exits 3. A bug still enters Todo at once: its card is added in Todo
# with no card before, and an add is never a move (ADR-0148), so the door autobug writes through adds it to a full Todo.
# The count a decision reads is the live board: a bug added into Todo, and a card that left Todo, both count as the board
# carries them now, so a move that fits is never refused on a count this pass kept.
# Stubbed gh; a temp state dir and a temp checkout, so the live board, the loop state and the project file are never read.
# The stub plays a second writer too: it applies a pending mutation to its card table on the first proved card write.
# usage: bash test-board-todo-limit.sh [board.py]
src=${1:-$(dirname "$0")/board.py}; here=$(cd "$(dirname "$src")" && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_MOVE_BY SG_SQUAD
export TD="$T"
mkdir -p "$T/bin" "$T/st" "$T/co/docs/squad"
echo x/y > "$T/urlrepo"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "$TD/gh.calls"
case "$a" in *"--input -"*) cat > "$TD/patch.in";; esac
case "$a" in
  *"/labels"*) echo '[]';;
  *"-X PATCH"*items/*)
    n=${a#*items/}; n=${n%% *}; n=${n#ITEM}
    opt=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["fields"][0]["value"])' "$TD/patch.in")
    name=${opt//_/ }
    awk -F'\t' -v n="$n" '$1 != n' "$TD/cards.tsv" > "$TD/cards.new"; printf '%s\t%s\n' "$n" "$name" >> "$TD/cards.new"; mv "$TD/cards.new" "$TD/cards.tsv"
    if [ -f "$TD/mutate" ]; then bash "$TD/mutate"; rm -f "$TD/mutate"; fi   # the second writer acts while the pass runs
    echo '{"id":"ITEM"}';;
  *users/o*) echo '{"type":"Organization"}';;
  *repos/x/y/issues/*) n=${a#*issues/}; n=${n%% *}; echo "{\"id\":$n}";;   # the issue's database id, the add's input
  *"-X POST"*items*)   # the add of a new card: it joins the board with no Status yet, then its own move writes Todo
    n=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["id"])' "$TD/patch.in")
    printf '%s\t\n' "$n" >> "$TD/cards.tsv"; echo "{\"id\":\"ITEM$n\"}";;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"Backlog","name":{"raw":"Backlog"}},{"id":"Todo","name":{"raw":"Todo"}},{"id":"Doing","name":{"raw":"Doing"}},{"id":"Closed","name":{"raw":"Closed"}}]}]';;
  *'fields[]=F_STATUS'*) while IFS=$'\t' read -r n s; do printf '%s\t%s\thttps://api.github.com/repos/%s\n' "$n" "$s" "$(cat "$TD/urlrepo")"; done < "$TD/cards.tsv";;
  *items?per_page=100*) u=$(cat "$TD/urlrepo"); while IFS=$'\t' read -r n s; do printf '[%s,"ITEM%s","https://api.github.com/repos/%s"]\n' "$n" "$n" "$u"; done < "$TD/cards.tsv";;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
seed() { printf '2001\tTodo\n2006\tBacklog\n' > "$T/cards.tsv"; : > "$T/gh.calls"; rm -f "$T/st/board-rejections.jsonl" "$T/mutate"; }
cards() { printf '%b' "$1" > "$T/cards.tsv"; : > "$T/gh.calls"; rm -f "$T/st/board-rejections.jsonl" "$T/mutate"; }
mutate() { printf '%s\n' "$1" > "$T/mutate"; }
limit() { printf 'columns:\n  Todo:\n    max_cards: %s\n' "$1" > "$T/co/docs/squad/defaults.yaml"; }
nolimit() { printf 'columns:\n  Todo:\n    max_cards: none\n' > "$T/co/docs/squad/defaults.yaml"; }
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_BOARD_READ_BACKOFF=0 SG_BOARD_SETTLE_SECONDS=0 SG_REPO="$(cat "$T/urlrepo")" python3 "$src" "$@"; }
run_sh() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_BOARD_READ_BACKOFF=0 SG_BOARD_SETTLE_SECONDS=0 SG_REPO="$(cat "$T/urlrepo")" bash "$here/board.sh" "$@"; }

col() { awk -F'\t' -v n="$1" '$1 == n {print $2}' "$T/cards.tsv"; }

# 1 (#2437): Todo at its limit refuses the move from Backlog and names the limit; the card keeps Backlog.
seed; limit 1; o=$(SG_MOVE_BY=owner run o 4 "Todo" 2006 2>&1); rc=$?
[ "$rc" -eq 3 ] && [ "$(col 2006)" = "Backlog" ] && printf '%s' "$o" | grep -q 'Todo limit of 1 card' \
  && ok "a full Todo refuses the Backlog to Todo move, names the limit, and keeps the card in Backlog" || ko "full Todo move: exit $rc, column $(col 2006): $o"
[ -f "$T/st/board-rejections.jsonl" ] && grep -q '"issue": 2006' "$T/st/board-rejections.jsonl" && ok "the refusal is recorded with its issue" || ko "refusal not recorded: $(cat "$T/st/board-rejections.jsonl" 2>/dev/null)"

# 2 (#2437 AC 2, ADR-0148, autobug.sh:164): a bug still enters Todo at once, through the door autobug writes through
# (`board.sh --add <owner> <project> Todo <n>`), with the loop as the writer and Todo full. Its card is added in Todo with
# no card before, and an add is no move.
seed; limit 1; o=$( (export SG_MOVE_BY=loop; run_sh --add o 4 "Todo" 2007) 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2007)" = "Todo" ] && ok "a bug the loop adds through board.sh --add enters a full Todo at once" || ko "bug filing into a full Todo: exit $rc, column $(col 2007): $o [cards: $(tr '\n' ' ' < "$T/cards.tsv")] [calls: $(tr '\n' '|' < "$T/gh.calls")]"

# 3 (#2437): a Todo under its limit moves the card from Backlog.
seed; limit 2; o=$(SG_MOVE_BY=owner run o 4 "Todo" 2006 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2006)" = "Todo" ] && ok "a Todo under its limit takes the Backlog card" || ko "under the limit: exit $rc, column $(col 2006): $o"

# 4 (#2437): no Todo limit moves the card from Backlog.
seed; nolimit; o=$(SG_MOVE_BY=owner run o 4 "Todo" 2006 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2006)" = "Todo" ] && ok "a Todo with no limit takes the Backlog card" || ko "no limit: exit $rc, column $(col 2006): $o"

# 5 (#2437, Blocking 1): a card that leaves Todo frees its slot. While the pass runs, the stub moves 2001 out of Todo, so
# the second move fits where the count of the pass's own writes would still say full.
cards '2001\tTodo\n2006\tBacklog\n2008\tBacklog\n'; limit 2
mutate 'awk -F"\t" "\$1 != 2001" "$TD/cards.tsv" > "$TD/c.new"; printf "2001\tDoing\n" >> "$TD/c.new"; mv "$TD/c.new" "$TD/cards.tsv"'
o=$(SG_MOVE_BY=owner run o 4 "Todo" 2006 2008 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2006)" = "Todo" ] && [ "$(col 2008)" = "Todo" ] && [ "$(col 2001)" = "Doing" ] \
  && ok "a card that left Todo frees its slot for a later Backlog to Todo move" || ko "freed slot: exit $rc, 2006 $(col 2006), 2008 $(col 2008): $o"

# 6 (#2437, Blocking 1): a bug added into a full Todo blocks nothing. After the first move Todo holds its limit of 3, so a
# bug the autobug pass adds enters a full Todo; two cards then leave, and the later Backlog move still fits.
cards '2001\tTodo\n2002\tTodo\n2006\tBacklog\n2008\tBacklog\n'; limit 3
mutate 'printf "2007\tTodo\n" >> "$TD/cards.tsv"; awk -F"\t" "\$1 != 2001 && \$1 != 2002" "$TD/cards.tsv" > "$TD/c.new"; printf "2001\tDoing\n2002\tDoing\n" >> "$TD/c.new"; mv "$TD/c.new" "$TD/cards.tsv"'
o=$(SG_MOVE_BY=owner run o 4 "Todo" 2006 2008 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2007)" = "Todo" ] && [ "$(col 2006)" = "Todo" ] && [ "$(col 2008)" = "Todo" ] \
  && ok "a bug added into a full Todo blocks no later Backlog to Todo move that fits" || ko "bug add into a full Todo: exit $rc, 2007 $(col 2007), 2006 $(col 2006), 2008 $(col 2008): $o"

# 7 (#2437, Blocking 2): a limit the reader answers as text counts as that number, and a zero limit is no limit. The tree
# here carries a stub squad/board_limits.py, so the reader's answer is the value under test, not the project file's.
tree="$T/tree"; mkdir -p "$tree/contrib/squad-goal/scripts" "$tree/squad"
cp "$src" "$tree/contrib/squad-goal/scripts/board.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board-definition.json" "$tree/contrib/squad-goal/scripts/"
stub() { printf 'def limit(name, target="."):\n    return %s\n' "$1" > "$tree/squad/board_limits.py"; }
run_tree() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_BOARD_READ_BACKOFF=0 SG_BOARD_SETTLE_SECONDS=0 SG_REPO="$(cat "$T/urlrepo")" python3 "$tree/contrib/squad-goal/scripts/board.py" "$@"; }
seed; limit 1; stub '"2"'   # a text limit wins over the project file's 1: Todo holds 1, so it is under the text limit of 2
o=$(SG_MOVE_BY=owner run_tree o 4 "Todo" 2006 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2006)" = "Todo" ] && ok "a limit the reader answers as text counts as that whole number" || ko "text limit: exit $rc, column $(col 2006): $o"
seed; limit 1; stub '0'   # zero is no bound: a zero would refuse every move, and the reader promises none for no limit
o=$(SG_MOVE_BY=owner run_tree o 4 "Todo" 2006 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$(col 2006)" = "Todo" ] && ok "a zero limit is no limit" || ko "zero limit: exit $rc, column $(col 2006): $o"

[ "$fail" = 0 ] && echo "board-todo-limit: all pass" || echo "board-todo-limit: FAIL"
exit "$fail"
