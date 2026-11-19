#!/bin/bash
# seconds: 2.54
# Regression test for #1848 / ADR-0136: the guard refuses a fix whose test passes at the fix's parent, and
# passes one whose test fails there -- the two are the same refusal, and so is a range that carries no test.
# A commit that puts its test and its code together (a squash) hid the red the history would have shown:
# refused unless the red run is recorded in the deliverable (ADR-0136 §3).
# Throwaway repos, no network. usage: bash test-tdd-red.sh [check-tdd-red.sh]
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/check-tdd-red.sh}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
[ -f "$src" ] || { echo "FAIL no guard at $src"; exit 1; }
G=/usr/bin/git
mk() { # mk <name>: a repo whose lib.sh prints old
  r="$T/$1"; mkdir -p "$r"; $G -C "$r" init -q -b main
  $G -C "$r" config user.email a@b; $G -C "$r" config user.name t
  printf '#!/bin/bash\necho old\n' > "$r/lib.sh"; $G -C "$r" add .; $G -C "$r" commit -q -m base; echo "$r"
}
newlib() { printf '#!/bin/bash\necho new\n' > "$1/lib.sh"; }
newtest() { printf '#!/bin/bash\n[ "$(bash lib.sh)" = new ]\n' > "$1/test-lib.sh"; }   # red on the unfixed lib
add() { r=$1; shift; $G -C "$r" add -A; $G -C "$r" commit -q "$@"; }
at() { $G -C "$1" rev-parse "$2"; }
guard() { # guard <repo>: run the check over the whole branch; sets $r (output) and $rc
  b=$($G -C "$1" rev-list --max-parents=0 HEAD | tail -1); h=$($G -C "$1" rev-parse HEAD)
  r=$(cd "$1" && bash "$src" "$b..$h" 2>&1); rc=$?
}
# 1. red before green: the test's commit, then the fix's -- the test is red at the fix's parent, the push passes
A=$(mk A); newtest "$A"; add "$A" -m "test: lib prints new"; newlib "$A"; add "$A" -m "fix: the lib prints new"
guard "$A"
[ "$rc" = 0 ] && ok "a test that fails at the fix's parent passes" || ko "a measured red was refused: $r"
case "$r" in *"TDD-RED OK"*) ok "the pass names the measurement";; *) ko "no verdict line: '$r'";; esac
# 2. the same test, added after the fix: it passes at its own parent, so it is not this fix's red
B=$(mk B); newlib "$B"; add "$B" -m "fix: the lib prints new"; newtest "$B"; add "$B" -m "test: lib prints new"
guard "$B"
[ "$rc" != 0 ] && ok "a test that passes at its parent is refused" || ko "a green-at-parent test passed the guard: $r"
case "$r" in *test-lib.sh*) ok "the refusal names the test";; *) ko "the refusal does not name the test: '$r'";; esac
# 3. no test at all: the same refusal
C=$(mk C); newlib "$C"; add "$C" -m "fix: the lib prints new"
guard "$C"
[ "$rc" != 0 ] && ok "a push with no test is refused" || ko "a test-free push passed the guard: $r"
# 4. a squash: test and code in one commit, no red recorded -- the red is hidden, refused
D=$(mk D); newlib "$D"; newtest "$D"; add "$D" -m "fix: the lib prints new"
guard "$D"
[ "$rc" != 0 ] && ok "a squash with no recorded red is refused" || ko "a squash hid its red and passed: $r"
case "$r" in *squash*) ok "the refusal names the squash";; *) ko "the refusal does not name the squash: '$r'";; esac
# 5. the same squash with the red run recorded in the deliverable body: accepted
E=$(mk E); newlib "$E"; newtest "$E"; add "$E" -m "fix: the lib prints new" -m "TDD RED (test-lib.sh, line 2): [ old = new ] fails at the parent"
guard "$E"
[ "$rc" = 0 ] && ok "a squash with the recorded red passes" || ko "a squash with its red recorded was refused: $r"
# 6. a range that changes no test, with the red recorded: the evidence is the recorded run, not one more commit
F=$(mk F); newlib "$F"; add "$F" -m "fix: the lib prints new" -m "TDD RED (test-lib.sh, line 2): [ old = new ] fails at the parent"
guard "$F"
[ "$rc" = 0 ] && ok "a recorded red carries a test-free range" || ko "a recorded red was refused: $r"
# 7. the tree under check is never written to: the guard measures in a scratch checkout
[ -z "$($G -C "$A" status --porcelain)" ] && ok "the guard leaves the tree clean" || ko "the guard dirtied $A"
rm -rf "$T"; exit $fail
