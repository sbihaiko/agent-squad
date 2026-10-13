#!/bin/bash
# Regression test for forkclose.sh's base-red park: one escalation card per base sha, one solo retry past the budget (green lifts, red keeps).
# usage: bash test-forkclose-basered.sh [path/to/forkclose.sh]
src=${1:-$(dirname "$0")/forkclose.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/tests" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp $(dirname "$src")/ghread.py "$T/s/"; cp $(dirname "$src")/ghthrottle.py $(dirname "$src")/ghcall.sh "$T/s/"   # the land resolves the branch through forkonly.sh (#1836)
printf '#!/bin/bash\neval "$1"\n' > "$T/s/fork-gate.sh"; printf '#!/bin/bash\necho t-1\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_OWNER=x SG_PROJECT=4 BOARD=true\nexport SG_FORK_TEST_CMD='"'"'echo run >> %s/runs; [ -e %s/green ]'"'"'\n' "$T/st" "$T/fork" "$T/co" "$T" "$T" > "$T/s/env.sh"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
g checkout -q -b gui/1 local/next; echo 1 > "$T/fork/f1"; g add .; g commit -q -m "work 1"; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
printf '#!/bin/bash\ncase "$*" in *"pulls?state=open"*) ;; *"-q .state"*) echo open;; *"issues/1/comments"*) echo "TDD RED (test-x.sh, failure line): FAIL y";; \n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
bs=$(g rev-parse --short=9 local/next); A="$T/st/auto"
printf 'FAIL test-pilot-script.sh: case home-06\n--- fork gate: GUI suite FAILED (exit 1)\n' > "$T/st/basetest-$bs.log"
run() { bash "$T/s/forkclose.sh" 2>&1; }; cards() { ls "$T/st/cq" | wc -l | tr -d ' '; }
touch "$A/basered-$bs"
r=$(run); case "$r" in *"BASE-RED #1"*) ok "a fresh park is reported";; *) ko "no BASE-RED: '$r'";; esac
[ "$(cards)" = 1 ] && grep -q "test-pilot-script.sh" "$T/st/cq/"* && grep -q "gui/1" "$T/st/cq/"* && ok "one card names the failing test and the waiting branch" || ko "card missing or wrong: $(cat "$T/st/cq/"* 2>/dev/null)"
run >/dev/null; [ "$(cards)" = 1 ] && ok "the escalation is emitted once per base sha" || ko "escalated again: $(cards) cards"
[ ! -e "$T/runs" ] && ok "inside the budget the suite is not retried" || ko "retried inside the budget"
touch -t 202001010000 "$A/basered-$bs"
r=$(run); [ "$(wc -l < "$T/runs" | tr -d ' ')" = 1 ] && [ -e "$A/basered-$bs" ] && ok "past the budget one solo retry runs; a second red keeps the park" || ko "retry/keep wrong: '$r'"
run >/dev/null; [ "$(wc -l < "$T/runs" | tr -d ' ')" = 1 ] && ok "the solo retry happens once" || ko "retried again"
rm -f "$A/basered-$bs.retried"; touch -t 202001010000 "$A/basered-$bs"; touch "$T/green"
r=$(run); [ ! -e "$A/basered-$bs" ] && [ -e "$A/basetest-$bs" ] && ok "a green retry lifts the park" || ko "park not lifted: '$r'"
# #16: a red base whose failing test arrived with a revert names that revert, instead of reporting the base's own failing test with
# no culprit at all: the merge the park complains about was already taken back, so a reader has to be told, or they go looking for
# a branch that is not there. The last commit that touched the failing test's file is what says whether a revert is the cause.
printf 'orig\n' > "$T/fork/test-pilot-script.sh"; g add .; g commit -q -m "the spec a merge of the base brought"
g revert --no-edit HEAD >/dev/null 2>&1; rs=$(g log -1 --format='%s' HEAD)
bs2=$(g rev-parse --short=9 local/next)
printf 'FAIL test-pilot-script.sh: case home-06\n' > "$T/st/basetest-$bs2.log"; touch "$A/basered-$bs2"
rm -f "$A"/fork-1-*   # the green retry above landed gui/1 and left its "wait for a fix commit" marker; a pass that reaches the base gate is one whose branch is not parked behind that marker
r=$(run)
case "$r" in *"BASE-RED #1"*) case "$r" in *"$rs"*) ok "#16: the park line names the revert that took the failing test's merge out";; *) ko "#16: the park line names no revert: '$r'";; esac;; *) ko "#16: no BASE-RED line: '$r'";; esac
rm -rf "$T"; exit $fail
