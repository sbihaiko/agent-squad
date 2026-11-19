#!/bin/bash
# seconds: 5.05
# Regression test for #1848 / ADR-0136: the guard the ADR names, driven through its public interface only
# (a repo, a range, the env). It proves the refusal in both directions and the squash case:
#   - a fix whose test FAILS at the fix's parent passes, and the pass names the measurement;
#   - a fix whose test already PASSES at its parent is refused, and the refusal names the test -- the two
#     are the same refusal (ADR-0136 §2/§4);
#   - a fix that squashes its test and its code into one commit hid the red the history would have shown:
#     refused, and accepted only once the red run is recorded in the deliverable (§3), which the test
#     exercises through both channels a local commit has -- the commit message and $SG_TDD_RED.
# Throwaway repos under mktemp -d, no network, and the tree under check is never written to.
# usage: bash test-check-tdd-red.sh [check-tdd-red.sh]
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/check-tdd-red.sh}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
G=/usr/bin/git
fail=0
ok() { echo "ok   $1"; }
ko() { echo "FAIL $1"; fail=1; }
[ -f "$src" ] || { echo "FAIL no guard at $src"; exit 1; }
# the ambient run's env must not reach the guard: it reads all of these
unset SG_TDD_RANGE PREPUSH_RANGE PREPUSH_BODY SG_TDD_RED SG_REPO SG_BASE SG_TDD_TEST_CMD SG_TDD_TEST_RE

mk() {   # mk <name> -> a repo whose lib.sh prints v1; echoes its path
  r="$T/$1"; mkdir -p "$r"; $G -C "$r" init -q -b main
  $G -C "$r" config user.email t@example.com; $G -C "$r" config user.name t
  printf '#!/bin/bash\necho v1\n' > "$r/lib.sh"
  $G -C "$r" add -A; $G -C "$r" commit -q -m base; echo "$r"
}
want() { printf '#!/bin/bash\necho v%d\n' "$2" > "$1/lib.sh"; }                 # the fix
red()  { printf '#!/bin/bash\n[ "$(bash lib.sh)" = v2 ]\n' > "$1/test-lib.sh"; }  # fails on the unfixed lib
green(){ printf '#!/bin/bash\n[ "$(bash lib.sh)" = v1 ]\n' > "$1/test-lib.sh"; }  # passes at the base
add()  { r=$1; shift; $G -C "$r" add -A; $G -C "$r" commit -q "$@"; }
guard() { # guard <repo> <range> -> sets $out and $rc; never writes to <repo>
  out=$(cd "$1" && SG_REPO= bash "$src" "$2" 2>&1); rc=$?
}
clean() { # clean <repo>: the tree under check was not written to, and no scratch worktree was left behind
  [ -z "$($G -C "$1" status --porcelain)" ] || return 1
  [ "$($G -C "$1" worktree list | wc -l | tr -d ' ')" = 1 ] || return 1
}

# --- a test that fails at the fix's parent: the red was measured, the push passes
r=$(mk red-at-parent); red "$r"; add "$r" -m "Add the failing test for the fix"
want "$r" 2; add "$r" -m "Fix: lib prints v2"
b=$($G -C "$r" rev-parse HEAD~2); guard "$r" "$b..HEAD"
if [ "$rc" = 0 ]; then ok "a test that fails at its parent passes"; else ko "a test that fails at its parent passes (rc=$rc)"; fi
case "$out" in *"TDD-RED OK"*test-lib.sh*) ok "the pass names the measurement";; *) ko "the pass names the measurement";; esac
clean "$r" && ok "the guard leaves the tree under check clean" || ko "the guard leaves the tree under check clean"

# --- a test that already passed at the fix's parent: not this fix's red, refused
r=$(mk green-at-parent); green "$r"; add "$r" -m "Add a test that passes on the unfixed code"
b=$($G -C "$r" rev-parse HEAD~1); guard "$r" "$b..HEAD"
if [ "$rc" = 1 ]; then ok "a test that passes at its parent is refused"; else ko "a test that passes at its parent is refused (rc=$rc)"; fi
case "$out" in *"TDD-RED REFUSED"*test-lib.sh*) ok "the refusal names the test";; *) ko "the refusal names the test";; esac

# --- a range that adds or changes no test at all: the same refusal
r=$(mk no-test); want "$r" 2; add "$r" -m "Fix: lib prints v2, no test"
b=$($G -C "$r" rev-parse HEAD~1); guard "$r" "$b..HEAD"
if [ "$rc" = 1 ]; then ok "a push with no test is refused"; else ko "a push with no test is refused (rc=$rc)"; fi
case "$out" in *"adds or changes no test"*) ok "the refusal names the missing test";; *) ko "the refusal names the missing test";; esac

# --- test and code squashed into one commit, no recorded red: refused
r=$(mk squash-unrecorded); want "$r" 2; red "$r"; add "$r" -m "Fix: lib prints v2, test and code together"
b=$($G -C "$r" rev-parse HEAD~1); guard "$r" "$b..HEAD"
if [ "$rc" = 1 ]; then ok "a squash with no recorded red is refused"; else ko "a squash with no recorded red is refused (rc=$rc)"; fi
case "$out" in *squash*) ok "the refusal names the squash";; *) ko "the refusal names the squash";; esac

# --- the same squash, the red recorded in the commit message (the deliverable) : accepted
r=$(mk squash-message); want "$r" 2; red "$r"
add "$r" -m "Fix: lib prints v2 (TDD RED: test-lib.sh -> lib.sh expected v2, got v1)"
b=$($G -C "$r" rev-parse HEAD~1); guard "$r" "$b..HEAD"
if [ "$rc" = 0 ]; then ok "a squash with the red in the deliverable's message passes"; else ko "a squash with the red in the deliverable's message passes (rc=$rc)"; fi
case "$out" in *"records the red run"*) ok "the pass names the recorded red";; *) ko "the pass names the recorded red";; esac

# --- the same squash, the red recorded through $SG_TDD_RED (the run's own record): accepted, tree clean
r=$(mk squash-env); want "$r" 2; red "$r"; add "$r" -m "Fix: lib prints v2, test and code together"
b=$($G -C "$r" rev-parse HEAD~1)
out=$(cd "$r" && SG_REPO= SG_TDD_RED="TDD RED: test-lib.sh -> expected v2, got v1" bash "$src" "$b..HEAD" 2>&1); rc=$?
if [ "$rc" = 0 ]; then ok "a squash with the red in \$SG_TDD_RED passes"; else ko "a squash with the red in \$SG_TDD_RED passes (rc=$rc)"; fi
clean "$r" && ok "the squash pass leaves the tree under check clean" || ko "the squash pass leaves the tree under check clean"

# --- a companion edit (#640): a test present at the merge-base changes and still passes at its parent, and the range
#     carries a test red at its parent: the edit has no red of its own, so it is reported as a companion, not refused
other() { printf '#!/bin/bash\n# %s\n[ -f lib.sh ]\n' "$2" > "$1/test-other.sh"; }   # passes at every commit
r=$(mk companion); other "$r" v1; add "$r" -m "Add a test that lib.sh exists"
b=$($G -C "$r" rev-parse HEAD)
red "$r"; other "$r" v2; add "$r" -m "Add the failing test, and touch the fixture of the other test"
want "$r" 2; add "$r" -m "Fix: lib prints v2"
guard "$r" "$b..HEAD"
if [ "$rc" = 0 ]; then ok "a changed test that passes at its parent, beside a red, passes"; else ko "a changed test that passes at its parent, beside a red, passes (rc=$rc): $out"; fi
case "$out" in *"TDD-RED OK (companion)"*test-other.sh*) ok "the pass names the companion";; *) ko "the pass names the companion: $out";; esac

# --- the same changed test with no red in the range: refused
r=$(mk companion-alone); other "$r" v1; add "$r" -m "Add a test that lib.sh exists"
b=$($G -C "$r" rev-parse HEAD); other "$r" v2; add "$r" -m "Touch the other test"
guard "$r" "$b..HEAD"
if [ "$rc" = 1 ]; then ok "a changed test that passes at its parent, with no red, is refused"; else ko "a changed test that passes at its parent, with no red, is refused (rc=$rc)"; fi
case "$out" in *"TDD-RED REFUSED"*test-other.sh*) ok "the refusal names the changed test";; *) ko "the refusal names the changed test: $out";; esac

# --- an ADDED test that passes at its parent, beside a red: still refused, a new test must be red
r=$(mk added-green); b=$($G -C "$r" rev-parse HEAD)
red "$r"; other "$r" v1; add "$r" -m "Add the failing test and a test that passes"
want "$r" 2; add "$r" -m "Fix: lib prints v2"
guard "$r" "$b..HEAD"
if [ "$rc" = 1 ]; then ok "an added test that passes at its parent, beside a red, is refused"; else ko "an added test that passes at its parent, beside a red, is refused (rc=$rc)"; fi
case "$out" in *"TDD-RED REFUSED"*test-other.sh*) ok "the refusal names the added test";; *) ko "the refusal names the added test: $out";; esac

# --- the base moved past the fork point (#640): its files are not the range's, so none of them is measured or refused
r=$(mk moved-base); printf "#!/bin/bash\ntrue\n" > "$r/test-base-side.sh"; add "$r" -m "A test both sides hold"
$G -C "$r" checkout -q -b work
red "$r"; add "$r" -m "Add the failing test for the fix"
want "$r" 2; add "$r" -m "Fix: lib prints v2"
$G -C "$r" checkout -q main
printf "#!/bin/bash\n# the base side\ntrue\n" > "$r/test-base-side.sh"; add "$r" -m "The base changes a test both sides hold"
$G -C "$r" checkout -q work
guard "$r" "main..work"
if [ "$rc" = 0 ]; then ok "a range whose base moved passes on its own red"; else ko "a range whose base moved passes on its own red (rc=$rc): $out"; fi
case "$out" in *test-base-side.sh*) ko "a base-side test is not the range's: $out";; *) ok "a base-side test is not the range's";; esac

# --- a range that touches no code and no test is not a defect fix: the guard names the exemption and passes
r=$(mk docs-only)
mkdir -p "$r/docs"; printf '# A decision\n' > "$r/docs/decision.md"; add "$r" -m "Write the decision"
b=$($G -C "$r" rev-parse HEAD~1); guard "$r" "$b..HEAD"
if [ "$rc" = 0 ]; then ok "a range that changes documents only passes"; else ko "a range that changes documents only passes (rc=$rc): $out"; fi
case "$out" in *"TDD-RED N/A"*docs/decision.md*) ok "the pass names the documents it exempts";; *) ko "the pass names the documents it exempts: $out";; esac

# --- the same exemption never covers code: a script with no test is refused exactly as before
r=$(mk code-only)
printf '#!/bin/bash\necho v2\n' > "$r/lib.sh"; add "$r" -m "Change the lib with no test"
b=$($G -C "$r" rev-parse HEAD~1); guard "$r" "$b..HEAD"
if [ "$rc" = 1 ]; then ok "a range that changes code only is still refused"; else ko "a range that changes code only is still refused (rc=$rc): $out"; fi

# --- a document beside a changed script is not a document-only range
r=$(mk docs-and-code)
printf '# A decision\n' > "$r/decision.md"; printf '#!/bin/bash\necho v2\n' > "$r/lib.sh"; add "$r" -m "Write the decision and change the lib"
b=$($G -C "$r" rev-parse HEAD~1); guard "$r" "$b..HEAD"
if [ "$rc" = 1 ]; then ok "a document beside code does not exempt the code"; else ko "a document beside code does not exempt the code (rc=$rc): $out"; fi

[ "$fail" = 0 ] && echo "test-check-tdd-red: all cases pass"
exit "$fail"
