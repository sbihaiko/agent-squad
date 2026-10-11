#!/bin/bash
# Regression test for forkclose.sh: a branch merged into the base, the merge reverted (red), then a fix commit added on the branch. The next landing must bring back the branch's original change as well as the fix, not only the commits made after the revert. Stub gh/test.sh, throwaway repo.
src=${1:-$(dirname "$0")/forkclose.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/fork-gate.sh" "$(dirname "$src")/serial.sh" "$T/s/"; printf "exit 0\n" > "$T/s/shell-suites.sh"   # the gate runs the squad suites too; this test is about the landing, not them
printf '#!/bin/bash\necho "pass-1"; exit 0\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
g checkout -q -b gui/1 local/next; printf 'orig\n' > "$T/fork/one.py"; g add .; g commit -q -m "work 1"; g checkout -q local/next
g merge -q --no-ff -m "Merge gui/1 (Refs #1)" gui/1; g revert -m 1 --no-edit HEAD >/dev/null   # red: the merge was reverted
g checkout -q gui/1; printf 'orig\nfixed\n' > "$T/fork/one.py"; echo fix > "$T/fork/fix.py"; g add .; g commit -q -m "fix"; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
printf '#!/bin/bash\ncase "$*" in *"issues/"*" -q .state"*) echo open;; esac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
[ "$(g rev-list --count local/next..gui/1)" = 1 ] && [ ! -e "$T/fork/one.py" ] && ok "setup: only the fix is ahead, the original change is gone from the base" || ko "setup wrong"
out=$(bash "$T/s/forkclose.sh" 2>&1); [ -n "${FC_VERBOSE:-}" ] && echo "$out"
[ "$(cat "$T/fork/one.py" 2>/dev/null)" = "$(printf 'orig\nfixed')" ] && ok "the original change landed, with the fix on top" || ko "one.py wrong: '$(cat "$T/fork/one.py" 2>&1)'"
[ -e "$T/fork/fix.py" ] && ok "the fix landed" || ko "fix.py missing"
[ -z "$(g diff --name-only HEAD gui/1)" ] && ok "the base tree matches the branch" || ko "tree differs: $(g diff --name-only HEAD gui/1 | tr '\n' ' ')"
[ -z "$(g status --porcelain)" ] && ok "checkout left clean" || ko "dirty checkout: $(g status --porcelain)"
rm -rf "$T"; exit $fail
