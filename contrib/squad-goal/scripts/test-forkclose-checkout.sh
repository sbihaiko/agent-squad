#!/bin/bash
# Regression test for #1798: a fork-only child that works in the shared checkout instead of its own ~/wt-as-<N> worktree
# leaves it checked out on its branch, and forkclose then refused every merge pass forever -- one WAIT line per pass, no
# cause, the finished branch unlanded. A clean tree nobody holds is the loop's own to return to the base; uncommitted work
# is named and left alone (never a reset).
# Not a dry run: the WAIT lives on the merge path, past the FC_DRY return. Stub gh, stubbed gate, throwaway fork repo.
# usage: bash test-forkclose-checkout.sh [path/to/forkclose.sh]
src=${1:-$(dirname "$0")/forkclose.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp $(dirname "$src")/ghread.py "$T/s/"; cp $(dirname "$src")/ghthrottle.py "$T/s/"   # the land resolves the branch through forkonly.sh (#1836)
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
printf '#!/bin/bash\nbash -c "$1"\n' > "$T/s/fork-gate.sh"   # the gate is not what this test is about
printf '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -- ] && { shift; "$@"; exit $?; }; shift; done\nexit 0\n' > "$T/s/test.sh"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
for n in 1 2; do g checkout -q -b gui/$n local/next; echo $n > "$T/fork/f$n"; g add .; g commit -q -m "work $n"; done; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"},{"n":2,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"pulls?state=open"*) ;;
  *"issues/"*"/comments"*) echo 'TDD RED (test-forkclose-checkout.sh: FAIL x)';;
  *"issues/"*" -q .body"*) ;;
  *"issues/"*" -q .state"*) echo open;;
  *"graphql"*) echo "[]";;
  *"--include"*) echo "{\"state\":\"open\"}";;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"

# 1: the shared checkout left on a branch, clean and with nobody in it. Before the fix the pass died on one WAIT line and the
# branch stayed unlanded for good; now the loop returns the checkout to the base itself and lands in the same pass.
g checkout -q gui/1
r=$(FC_NOBATCH=1 bash "$T/s/forkclose.sh" 2>&1)
case "$r" in *"not a clean local/next checkout"*) ko "the pass is blocked by the checkout: '$(grep -m1 WAIT <<<"$r")'";; *) ok "no checkout WAIT";; esac
case "$r" in *"left on gui/1"*) ok "the recovery names the branch it was left on";; *) ko "silent recovery: '$r'";; esac
[ "$(g branch --show-current)" = local/next ] && ok "the clean checkout came back to the base without an operator" || ko "the checkout is still on $(g branch --show-current)"
$G -C "$T/fork" log --oneline local/next | grep -q "Merge gui/1" && ok "the finished branch landed in the pass that recovered it" || ko "gui/1 is still unlanded"
case "$r" in *WAIT*) ko "a WAIT line survived the recovery: '$(grep -m1 WAIT <<<"$r")'";; *) ok "no WAIT on a clean, unheld checkout";; esac

# 2: uncommitted work is never moved (a checkout is not reset, and it is not waited on in silence either). A branch the first
# pass already merged carries its marker, so this half lands on a branch of its own.
g checkout -q -b gui/3 local/next; echo 3 > "$T/fork/f3"; g add .; g commit -q -m "work 3"
echo '{"items":[{"n":3,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
echo dirty >> "$T/fork/f"
r=$(FC_NOBATCH=1 bash "$T/s/forkclose.sh" 2>&1)
case "$r" in *"uncommitted changes"*) ok "a dirty shared checkout is named";; *) ko "the dirty checkout is not named: '$r'";; esac
[ "$(g branch --show-current)" = gui/3 ] && ok "a dirty checkout is left where it is" || ko "moved a dirty checkout to $(g branch --show-current)"
if g diff --quiet; then ko "the uncommitted change is gone (the checkout was reset)"; else ok "the uncommitted change is still there"; fi
rm -rf "$T"; exit $fail
