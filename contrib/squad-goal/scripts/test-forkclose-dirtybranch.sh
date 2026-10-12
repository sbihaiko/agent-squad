#!/bin/bash
# Regression test for #1821: a land merged the branch tip, not the run's final state. A child that finishes its work in its
# own ~/wt-as-<N> worktree but leaves it uncommitted (staged or not) has a branch whose tip is a mid-work commit; the land
# merged that tip, reported success and closed the issue while the base never got the code (`d01cd53`: one parent, tests only,
# the source half of #1809 still in ~/wt-as-1809's index). A tip that is not the final state must not report success: the pass
# refuses, names the branch as unlanded, and leaves the work alone (it never commits, checks out or resets someone's work).
# The refusal releases by itself: the child commits on the branch and the next pass lands it as usual.
# Not a dry run: the refusal lives on the merge path, past the FC_DRY return. Stub gh, stubbed gate, throwaway fork repo.
# usage: bash test-forkclose-dirtybranch.sh [path/to/forkclose.sh]
src=${1:-$(dirname "$0")/forkclose.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/req" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp $(dirname "$src")/ghread.py "$T/s/"   # the land resolves the branch through forkonly.sh (#1836)
cp "$(dirname "$src")/fork-gate.sh" "$(dirname "$src")/serial.sh" "$T/s/"; printf "exit 0\n" > "$T/s/shell-suites.sh"   # the gate runs the squad suites too; this test is about the dirty branch, not them, and the full gate also runs the pytest suite (#1822), so SG_FORK_PY_CMD is pinned below with the other gate inputs: the fixture's fork is a stub repo with no tests/ for the default command to run
printf "echo shell\n" > "$T/s/gate-suites.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_FORK_PY_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
printf '#!/bin/bash\necho "pass-1"; exit 0\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
# The e2e trees are real checkouts a pass would fetch and move: the fixture must never point at the caller's (shell-suites.sh
# clears the SG_* variables; a bare `bash test-forkclose-dirtybranch.sh`, as the PR body's RED run does, does not).
FC="env -u SG_E2E_DIR -u SG_E2E_BUILD_DIR -u SG_E2E_HEADLESS_DIR FC_NOBATCH=1"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
g checkout -q -b gui/1 local/next; echo mid > "$T/fork/source"; g add source; g commit -q -m "work 1 (mid)"; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
cat > "$T/bin/gh" <<J
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *"pulls?state=open"*) ;;
  *"issues/"*"/comments"*) echo 'TDD RED (test-forkclose-dirtybranch.sh: FAIL x)';;
  *"issues/"*" -q .body"*) ;;
  *"issues/"*" -q .state"*) echo open;;
  *"graphql"*) echo "[]";;
  *"--include"*) echo "{\"state\":\"open\"}";;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"

# The child's own worktree: the branch is checked out at ~/wt-as-<n>, and the run's final state (the line the branch tip lacks)
# is only in its index. The tip is therefore not the final state.
g worktree add -q "$T/wt-1" gui/1
printf 'mid\nfinal\n' > "$T/wt-1/source"; $G -C "$T/wt-1" add source
g show gui/1:source | grep -q final && ko "the fixture is wrong: the branch tip already has the final state" || ok "the branch tip is the mid-work commit; the final state is only in the worktree's index"
$G -C "$T/wt-1" diff --cached --quiet && ko "the fixture is wrong: nothing is staged in the worktree" || ok "the child worktree holds staged, uncommitted work"

r=$($FC bash "$T/s/forkclose.sh" 2>&1)
case "$r" in *gui/1*uncommitted*) ok "the refusal names gui/1 and its uncommitted changes";; *) ko "the pass did not name the branch as unlanded: '$r'";; esac
case "$r" in *"$T/wt-1"*) ok "the refusal names the worktree that holds the work";; *) ko "silent about where the work is: '$r'";; esac
g log --oneline local/next | grep -q "Merge gui/1" && ko "the mid-work tip was merged into the base (that is the bug)" || ok "nothing was merged into the base"
/usr/bin/grep -q "state=closed" "$T/gh.log" 2>/dev/null && ko "the issue was closed with its code absent from local/next" || ok "the issue did not close"
$G -C "$T/wt-1" diff --cached --quiet && ko "the uncommitted work is gone (the pass committed, checked out or reset it)" || ok "the uncommitted work is still there, untouched"

# The refusal releases on its own: the child commits on the branch and the next pass lands it and closes the issue.
$G -C "$T/wt-1" commit -q -m "work 1 (final)"; rm -f "$T/gh.log"
r=$($FC bash "$T/s/forkclose.sh" 2>&1)
g log --oneline local/next | grep -q "Merge gui/1" && ok "once the tip is the final state the branch lands" || ko "the branch is still unlanded: '$r'"
g show local/next:source | grep -q final && ok "the base got the run's final code" || ko "the base has the mid-work version: $(g show local/next:source | tr '\n' ' ')"
/usr/bin/grep -q "state=closed" "$T/gh.log" 2>/dev/null && ok "the issue closes once its code is in the base" || ko "no close after a real landing"
rm -rf "$T"; exit $fail
