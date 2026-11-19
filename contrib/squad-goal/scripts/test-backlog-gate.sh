#!/bin/bash
# seconds: 1.51
# Regression test for backlog-gate.sh (#2436): the publisher's gate at the Backlog card limit. With Backlog at its limit,
# a spec publish and a ticket publish each add no card and get the refusal that names the limit; under the limit and with
# no limit both pass; a bug passes at once and reads no board; an unreadable board or an unreadable limit is a defect,
# never "no limit". Offline and hermetic: a stub board read, a temp settings tree, no live board and no live cache.
# usage: bash test-backlog-gate.sh [path/to/backlog-gate.sh]
src=${1:-$(dirname "$0")/backlog-gate.sh}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
if [ ! -f "$src" ]; then echo "FAIL backlog-gate.sh: $src not found"; rm -rf "$T"; exit 1; fi
d=$(cd "$(dirname "$src")" && pwd); repo=$(cd "$d/../../.." && pwd)
mkdir -p "$T/s" "$T/co/docs/squad"
cp "$src" "$d/columnlimit.sh" "$T/s/"
# The one board read: the stub answers the rows of $T/rows, or fails when $T/broken exists.
cat > "$T/s/issue_state.py" <<'PY'
import pathlib, sys
d = pathlib.Path(sys.argv[0]).parent
if (d / "broken").exists(): sys.exit(1)
sys.stdout.write((d / "rows").read_text() if (d / "rows").exists() else "")
PY
rows() { printf '%b\n' "$@" > "$T/s/rows"; }
limits() { printf 'columns:\n  Backlog:\n    max_cards: %s\n' "$1" > "$T/co/docs/squad/defaults.yaml"; }
gate() { SG_SQUAD=$repo SG_CHECKOUT=$T/co bash "$T/s/backlog-gate.sh" "$@"; }

# 1) A full Backlog refuses a spec publish and names the limit; the gate adds no card.
rows '41\tBacklog' '42\tBacklog'; limits 2
out=$(gate spec); rc=$?
[ "$rc" = 3 ] && ok "a full Backlog refuses a spec publish" || ko "the spec gate exited $rc, not 3: $out"
case "$out" in *"2 of 2"*Backlog*spec*) ok "the spec refusal names the limit" ;; *) ko "the spec refusal was '$out'" ;; esac

# 2) A full Backlog refuses a ticket publish too.
out=$(gate ticket); rc=$?
[ "$rc" = 3 ] && ok "a full Backlog refuses a ticket publish" || ko "the ticket gate exited $rc, not 3: $out"
case "$out" in *"2 of 2"*ticket*) ok "the ticket refusal names the limit" ;; *) ko "the ticket refusal was '$out'" ;; esac

# 3) Under the limit both publishes pass with no line.
rows '41\tBacklog'; limits 2
out=$(gate spec); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] && ok "under the limit a spec publish passes" || ko "the spec gate exited $rc: $out"
out=$(gate ticket); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] && ok "under the limit a ticket publish passes" || ko "the ticket gate exited $rc: $out"

# 4) With no limit both publishes pass, whatever the column holds.
rows '41\tBacklog' '42\tBacklog' '43\tBacklog'; limits null
gate spec >/dev/null; a=$?; gate ticket >/dev/null; b=$?
[ "$a" = 0 ] && [ "$b" = 0 ] && ok "with no limit both publishes pass" || ko "a no-limit gate exited $a/$b"

# 5) A bug never goes to Backlog: a full Backlog holds it never, and the gate reads no board.
: > "$T/s/rows"; touch "$T/s/broken"; limits 1
out=$(gate bug); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] && ok "a full Backlog never holds a bug" || ko "the bug gate exited $rc: $out"
rm -f "$T/s/broken"

# 6) A board read that fails is a defect, never "no limit".
limits 9; touch "$T/s/broken"
out=$(gate spec); rc=$?
[ "$rc" = 2 ] && case "$out" in *"did not answer"*) true;; *) false;; esac \
  && ok "an unreadable board is a defect, not a pass" || ko "an unreadable board gave rc=$rc out='$out'"

# 7) An unknown kind is refused, never guessed.
out=$(gate feature 2>&1); rc=$?
[ "$rc" = 2 ] && case "$out" in usage*) true;; *) false;; esac \
  && ok "an unknown publish kind is refused" || ko "an unknown kind gave rc=$rc out='$out'"

rm -rf "$T"; [ "$fail" = 0 ] && echo "PASS test-backlog-gate.sh" || echo "FAIL test-backlog-gate.sh"; exit "$fail"
