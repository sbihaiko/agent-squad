#!/bin/bash
# Regression test for #1470: a fork gate killed by a signal (exit 143/137) is an environment failure, not a code red. forkclose.sh logs ENV-KILL
# with the test id and log path (known before the gate starts), reverts and re-applies as for a red, retries once and dispatches no fix run;
# a second kill on the same head escalates to NEEDS OWNER. Stub test.sh kills itself with SIGTERM, stub gh/mkreq.sh, throwaway fork repo.
# usage: bash test-forkclose-envkill.sh [path/to/forkclose.sh]
src=${1:-$(dirname "$0")/forkclose.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_E2E_DIR SG_E2E_BUILD_DIR; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/tests" "$T/st/req" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp "$(dirname "$src")/gate-suites.sh" "$T/s/"; cp $(dirname "$src")/ghread.py "$T/s/"; cp $(dirname "$src")/ghthrottle.py $(dirname "$src")/ghcall.sh "$T/s/"
printf '#!/bin/bash\neval "$1"\n' > "$T/s/fork-gate.sh"
printf '#!/bin/bash\nkill -TERM $$\n' > "$T/s/test.sh"   # killed before it prints its id or finalizes a result JSON
printf '#!/bin/bash\necho x >> %s/fixruns; echo "$2"\n' "$T" > "$T/s/mkreq.sh"; printf 'import sys\n' > "$T/s/launch.py"; chmod +x "$T/s/test.sh" "$T/s/mkreq.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
g checkout -q -b gui/1 local/next; echo 1 > "$T/fork/f1"; g add .; g commit -q -m "work 1"; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
printf '#!/bin/bash\ncase "$*" in *"pulls?state=open"*) ;; *"-q .state"*) echo open;; *"issues/1/comments"*) echo "TDD RED (test-x.sh, failure line): FAIL y";; \n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { bash "$T/s/forkclose.sh" 2>&1; }; nfix() { [ -e "$T/fixruns" ] && wc -l < "$T/fixruns" | tr -d ' ' || echo 0; }
r=$(run)
case "$r" in *"FORKCLOSE ENV-KILL #1 (test-1-"*", exit 143, log "*"tests/test-1-"*".log)"*) ok "a SIGTERM'd gate is ENV-KILL with the test id and its log path";; *) ko "no ENV-KILL line with id and log: '$r'";; esac
case "$r" in *"tests/.log"*|*"FORKCLOSE RED"*) ko "still reported as a red: '$r'";; esac
[ "$(nfix)" = 0 ] && ok "no fix run is dispatched" || ko "a fix run was dispatched ($(nfix))"
[ -z "$(g rev-list --count gui/1..local/next | grep -v '^[0-9]*$')" ] && [ "$(g rev-list --count local/next..gui/1)" -ge 0 ] && ok "base checkout is intact" || ko "base broken"
r=$(run)
case "$r" in *"NEEDS OWNER #1 (test-1-"*", exit 143, log "*"tests/test-1-"*".log)"*) ok "the next cycle retries the gate; a second kill on the same head escalates to NEEDS OWNER with id and log";; *) ko "no retry/escalation: '$r'";; esac
[ "$(nfix)" = 0 ] && ok "still no fix run after the escalation" || ko "a fix run was dispatched ($(nfix))"
rm -rf "$T"; exit $fail
