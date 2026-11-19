#!/bin/bash
# seconds: 0.10
# Regression test for test.sh: a coordinator test pass records its result and moves no card, since the board has no Testing column.
# usage: bash test-test.sh [path/to/test.sh]
src=${1:-$(dirname "$0")/test.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/bin" "$T/st" "$T/cq"; cp "$src" "$T/s/test.sh"
cat > "$T/s/env.sh" <<E
SG_STATE=$T/st; SG_OWNER=o; SG_PROJECT=4; BOARD=$T/bin/board
sg_cq_file() { echo "$T/cq/\$1"; }
E
cat > "$T/bin/board" <<'J'
#!/bin/bash
echo "$*" >> "$BOARD_LOG"
J
chmod +x "$T/bin/board"
BOARD_LOG=$T/board.log bash "$T/s/test.sh" 1234 -- true >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && ok "a passing test pass exits 0" || ko "the test pass exited $rc"
! grep -q 'Testing' "$T/board.log" 2>/dev/null && ok "a test pass moves no card to Testing" || ko "a card was moved: $(cat "$T/board.log")"
grep -l '"state": "completed"' "$T"/st/tests/*.json >/dev/null 2>&1 && ok "the pass records its completion" || ko "no completed record"
rm -rf "$T"; exit $fail
