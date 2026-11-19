#!/bin/bash
# seconds: 0.97
# A card in Done over a branch the base never took is a false delivery (#97).
#
# A run's own `land` pass is the run saying it finished, not the base taking the merge: the close pass merges gui/<n> and
# only then is the work on the base. The janitor read the `land` event alone and moved the card to Done, which is the one
# column the close pass never looks at -- it lands a fork-only issue whose card is Code Complete. Measured 2026-10-13:
# #89, #93, #94 and #96 sat in Done with gui/89, gui/93, gui/94 and gui/96 each one commit ahead of main and no merge in
# the history, and the close pass named none of them for over an hour.
#
# A run's own landing, whatever the fork base holds, keeps the card at Batching: only a merged pull request naming
# the issue on the base, or a closed issue, makes it Done (#239). Stubbed gh and board.sh, a temp fork repository, a temp
# state dir.
# usage: bash test-janitor-unlanded-branch.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs" "$T/home"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$(dirname "$src")/board_facts.py" "$(dirname "$src")/spec_fold.py" "$T/s/"
here="$(cd "$(dirname "$0")" && pwd)"   # this suite's own directory: the stubs it copies live beside it
cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$T/s/"
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$1" in --add) echo "ADD $*" >> "TMPDIR/board.calls";; --run-fields|--clear-run) ;; *) echo "STATUS $*" >> "TMPDIR/board.calls";; esac
J
sed -i '' "s|TMPDIR|$T|g" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"
case "$a" in
  *search/issues*) case "$a" in *"-X GET"*|*"--method GET"*) ;; *) echo '{"message":"Not Found"}'; exit 1;; esac; echo 0;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)   # both cards are on the board in the wrong column: the pass has to move them
    printf '2101\tTodo\thttps://api.github.com/repos/x/y\n'
    printf '2102\tTodo\thttps://api.github.com/repos/x/y\n'
    printf '2103\tDone\thttps://api.github.com/repos/x/y\n';;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    printf '{"number":2101,"title":"a","body":"","state":"OPEN","labels":{"nodes":[]},"comments":{"nodes":[]}},'
    printf '{"number":2102,"title":"b","body":"","state":"OPEN","labels":{"nodes":[]},"comments":{"nodes":[]}},'
    printf '{"number":2103,"title":"c","body":"","state":"OPEN","labels":{"nodes":[]},"comments":{"nodes":[]}}'
    printf ']}}}}';;
  *issues/2101*) echo $'open\t\tIssue 2101';;
  *issues/2102*) echo $'open\t\tIssue 2102';;
  *issues/2103*) echo $'open\t\tIssue 2103';;
esac
J
sed -i '' "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# The fork: main with one commit, and gui/2101 one commit ahead of it. gui/2102 does not exist: its work is on the base.
# gui/2103 was merged and then taken back: ancestry cannot tell it from a landed branch, because the merge commit stays in
# the history and only the tree moved (#100).
git init -q -b main "$T/fork" 2>/dev/null || { git init -q "$T/fork"; git -C "$T/fork" symbolic-ref HEAD refs/heads/main; }; git -C "$T/fork" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
git -C "$T/fork" checkout -q -b gui/2101
git -C "$T/fork" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "the run's own commit"
git -C "$T/fork" checkout -q main
git -C "$T/fork" checkout -q -b gui/2103
echo "the run's own change" > "$T/fork/work.txt"
git -C "$T/fork" add work.txt
git -C "$T/fork" -c user.email=t@t -c user.name=t commit -q -m "a run whose landing was taken back"
git -C "$T/fork" checkout -q main
git -C "$T/fork" -c user.email=t@t -c user.name=t merge -q --no-ff gui/2103 -m 'Merge gui/2103 (Refs #2103)'
git -C "$T/fork" -c user.email=t@t -c user.name=t revert --no-edit -m 1 HEAD
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s:\n' "$2" > "$T/co/runs/$1/request.txt"; }
rec() { printf '%s\n' "$2" > "$T/co/runs/$1/record.jsonl"; }
mk run-ahead 2101
rec run-ahead '{"event":"run_start","run_id":"run-ahead","ts":"2026-10-13T08:00:00Z"}
{"event":"land","outcome":"pass"}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
mk run-on-base 2102
rec run-on-base '{"event":"run_start","run_id":"run-on-base","ts":"2026-10-13T08:01:00Z"}
{"event":"land","outcome":"pass"}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
mk run-reverted 2103
rec run-reverted '{"event":"run_start","run_id":"run-reverted","ts":"2026-10-13T08:02:00Z"}
{"event":"land","outcome":"pass"}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
o=$(HOME="$T/home" SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_FORK_DIR="$T/fork" SG_FORK_BASE=main SG_FORK_BRANCH='gui/%n' \
    SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1)
bc() { cat "$T/board.calls" 2>/dev/null; }
/usr/bin/grep -q '^STATUS o 4 Batching 2101$' "$T/board.calls" 2>/dev/null \
  && ok "a branch ahead of the base keeps the card at Batching, never Done" \
  || ko "#2101 not Batching: $(bc) $o"
/usr/bin/grep -q '^STATUS o 4 Done 2101$' "$T/board.calls" 2>/dev/null \
  && ko "#2101 moved to Done over a branch the base never took" || ok "and it is never written to Done"
/usr/bin/grep -q '^STATUS o 4 Batching 2102$' "$T/board.calls" 2>/dev/null \
  && ok "a run whose work is on the base stops at Batching until a merge names it" || ko "#2102 not Batching: $(bc) $o"
/usr/bin/grep -q '^STATUS o 4 Done 2102$' "$T/board.calls" 2>/dev/null \
  && ko "#2102 moved to Done from the run's own landing" || ok "and #2102 never reads Done from the local landing"
# #100: gui/2103 was merged and taken back. Its commits are ancestors of the base, so `branch_ahead` answers no and the
# card read Done over work the base does not hold. The card belongs at Batching until the base takes the merge again.
/usr/bin/grep -q '^STATUS o 4 Batching 2103$' "$T/board.calls" 2>/dev/null \
  && ok "a landing that was taken back leaves the card at Batching" || ko "#2103 not Batching: $(bc) $o"
/usr/bin/grep -q '^STATUS o 4 Done 2103$' "$T/board.calls" 2>/dev/null \
  && ko "#2103 moved to Done over a landing the base took back" || ok "and a card already in Done is moved out of it"
rm -rf "$T"
[ "$fail" = 0 ] && echo "test-janitor-unlanded-branch: PASS" || echo "test-janitor-unlanded-branch: FAIL"
exit "$fail"
