#!/bin/bash
# Regression test for forkclose.sh: a branch merged into the base, the merge reverted (red), then a fix commit added on the branch. The next landing must bring back the branch's original change as well as the fix, not only the commits made after the revert. Stub gh/test.sh, throwaway repo.
src=${1:-$(dirname "$0")/forkclose.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp "$(dirname "$src")/fork-gate.sh" "$(dirname "$src")/serial.sh" "$T/s/"; printf "exit 0\n" > "$T/s/shell-suites.sh"; cp $(dirname "$src")/ghread.py "$T/s/"; cp $(dirname "$src")/ghthrottle.py "$T/s/"   # the gate runs the squad suites too; this test is about the landing, not them
printf '#!/bin/bash\necho "pass-1"; exit 0\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_FORK_PY_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
g checkout -q -b gui/1 local/next; printf 'orig\n' > "$T/fork/one.py"; g add .; g commit -q -m "work 1"; g checkout -q local/next
g merge -q --no-ff -m "Merge gui/1 (Refs #1)" gui/1; g revert -m 1 --no-edit HEAD >/dev/null   # red: the merge was reverted
g checkout -q gui/1; printf 'orig\nfixed\n' > "$T/fork/one.py"; echo fix > "$T/fork/fix.py"; g add .; g commit -q -m "fix"; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
printf '#!/bin/bash\ncase "$*" in *"issues/"*" -q .state"*) echo open;; \n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
[ "$(g rev-list --count local/next..gui/1)" = 1 ] && [ ! -e "$T/fork/one.py" ] && ok "setup: only the fix is ahead, the original change is gone from the base" || ko "setup wrong"
out=$(bash "$T/s/forkclose.sh" 2>&1); [ -n "${FC_VERBOSE:-}" ] && echo "$out"
[ "$(cat "$T/fork/one.py" 2>/dev/null)" = "$(printf 'orig\nfixed')" ] && ok "the original change landed, with the fix on top" || ko "one.py wrong: '$(cat "$T/fork/one.py" 2>&1)'"
[ -e "$T/fork/fix.py" ] && ok "the fix landed" || ko "fix.py missing"
[ -z "$(g diff --name-only HEAD gui/1)" ] && ok "the base tree matches the branch" || ko "tree differs: $(g diff --name-only HEAD gui/1 | tr '\n' ' ')"
[ -z "$(g status --porcelain)" ] && ok "checkout left clean" || ko "dirty checkout: $(g status --porcelain)"
rm -rf "$T"
# #16: reverting a merge takes the spec that merge brought. `git revert -m 1` reverses the merge's whole diff, so the test files it
# added go back out with it -- and the revert commit's body has to say WHY (a red gate, not a wrong change), because the two are
# indistinguishable from the tree alone.
T=$(mktemp -d); mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin" "$T/co/runs/.hub" "$T/fork"
cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp "$(dirname "$src")/fork-gate.sh" "$(dirname "$src")/serial.sh" "$T/s/"; printf 'exit 0\n' > "$T/s/shell-suites.sh"; cp $(dirname "$src")/ghread.py $(dirname "$src")/ghthrottle.py "$T/s/"
printf '#!/bin/bash\necho "FAILED tests/test_spec.py::test_spec - boom"; exit 1\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_FORK_PY_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
g checkout -q -b gui/1 local/next; mkdir -p "$T/fork/tests"; printf 'orig\n' > "$T/fork/one.py"; printf 'spec\n' > "$T/fork/tests/test_spec.py"; g add .; g commit -q -m "work 1"; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
printf '#!/bin/bash\ncase "$*" in *"issues/"*" -q .state"*) echo open;; \n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"   # the aggregate reads (#5): the list is one GraphQL request the stub answers empty, so each candidate falls back to its own read
out=$(bash "$T/s/forkclose.sh" 2>&1)
[ ! -e "$T/fork/one.py" ] && [ ! -e "$T/fork/tests/test_spec.py" ] && ok "#16: the revert of the merge took the spec it added back out with it" || ko "#16: the red work is still in the base: one.py $( [ -e "$T/fork/one.py" ] && echo present || echo gone), tests/test_spec.py $( [ -e "$T/fork/tests/test_spec.py" ] && echo present || echo gone)"
g log -1 --format=%B HEAD | grep -qi 'red\|fail' && ok "#16: the revert commit's body says why the merge is being undone (a red gate, not a wrong change)" || ko "#16: the revert body names no reason: '$(g log -1 --format=%B HEAD | head -2 | tr '\n' '|')'"
rm -rf "$T"
# #16: two interdependent merges of the same branch, one pass: either both come back out or neither. The branch landed, a sibling
# then rewrote one of the specs it had added, and the branch came back with a fix that goes red. Taking back only the tip merge
# would leave the first one's work standing, so the set is reverted by ONE sequencer run; the spec a later commit rewrote cannot go
# back (it is not that merge's any more), so it stays in the base as quarantined, with the reason in the log.
T=$(mktemp -d); mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin" "$T/co/runs/.hub" "$T/fork"
cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp "$(dirname "$src")/fork-gate.sh" "$(dirname "$src")/serial.sh" "$T/s/"; printf 'exit 0\n' > "$T/s/shell-suites.sh"; cp $(dirname "$src")/ghread.py $(dirname "$src")/ghthrottle.py "$T/s/"
printf '#!/bin/bash\ncd %s/fork 2>/dev/null || exit 1\n[ -e fix.py ] && { echo "FAILED tests/test_spec.py::test_spec - boom"; exit 1; }\nexit 0\n' "$T" > "$T/s/test.sh"; chmod +x "$T/s/test.sh"   # green on the base, red once the branch's tip is merged
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_FORK_PY_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
g checkout -q -b gui/1 local/next; mkdir -p "$T/fork/tests"; printf 'orig\n' > "$T/fork/one.py"; printf 'spec\n' > "$T/fork/tests/test_spec.py"; g add .; g commit -q -m "work 1"; g checkout -q local/next
g merge -q --no-ff -m "Merge gui/1 (Refs #1)" gui/1   # the first merge of the branch stands in the base
printf 'spec\nrewritten by a sibling\n' > "$T/fork/tests/test_spec.py"; g commit -qam "a sibling rewrites the spec that merge brought"
g checkout -q gui/1; echo fix > "$T/fork/fix.py"; g add .; g commit -q -m "fix 1"; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
printf '#!/bin/bash\ncase "$*" in *"issues/"*" -q .state"*) echo open;; \n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"   # the aggregate reads (#5): the list is one GraphQL request the stub answers empty, so each candidate falls back to its own read
out=$(bash "$T/s/forkclose.sh" 2>&1)
[ ! -e "$T/fork/one.py" ] && [ ! -e "$T/fork/fix.py" ] && ok "#16: both merges of the branch came back out (the first one is not left standing)" || ko "#16: half reverted: one.py $( [ -e "$T/fork/one.py" ] && echo present || echo gone), fix.py $( [ -e "$T/fork/fix.py" ] && echo present || echo gone)"
[ "$(g log --format=%s | grep -c '^Revert')" = 1 ] && ok "#16: the two interdependent merges were reverted by one sequencer run, so it was both or neither" || ko "#16: reverts in the base: $(g log --format=%s | grep -c '^Revert') -- $(g log --format=%s | tr '\n' '|')"
[ -e "$T/fork/tests/test_spec.py" ] && ok "#16: the spec a later commit rewrote is left in the base instead of dropped with the merge" || ko "#16: the sibling's spec was dropped by the revert"
case "$out" in *"QUARANTINE"*"tests/test_spec.py"*) ok "#16: the quarantined spec is named in the log with its reason";; *) ko "#16: no quarantine line: $out";; esac
[ -z "$(g status --porcelain)" ] && [ -z "$(g diff --name-only --diff-filter=U)" ] && ok "#16: the revert left the checkout clean: no conflict and no half-applied revert" || ko "#16: checkout dirty: $(g status --porcelain | tr '\n' '|')"
rm -rf "$T"; exit $fail
