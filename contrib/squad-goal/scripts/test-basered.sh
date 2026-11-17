#!/bin/bash
# Regression test for the base-red dispatch hold (#2056): two full landing or tip checks in a row, on two different trees,
# red at the same test, say the base carries that red. The frontier then holds every dispatch, one bug owns the red, and
# the bug lane goes on. A green full check after the red, or the close of its bug, lifts the hold. An unproven check
# never sets it, and SG_BASE_RED_HOLD=0 turns it off. Hermetic: a temp SG_STATE, a stub gh_api and a stub filer.
# usage: bash test-basered.sh [scripts dir]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
export SG_STATE=$T/st SG_REPO=o/r; mkdir -p "$SG_STATE/auto"
# The filer stub logs each filing and answers the line autohunt.sh --file prints. The issue state is the file issue-<n>.
printf '#!/bin/bash\necho "$1" >> "%s/filed.log"\necho "HUNT filed #900"\n' "$T" > "$T/filer"; chmod +x "$T/filer"
export SG_BASE_RED_FILE_CMD=$T/filer
# row <finish> <sha> <outcome> <stage> [scope] [reason]: one gate-history line in the shape gate.sh writes.
row() { printf '%s\tfast\t%s\t%s\t%s\tall: x\t%s\t%s\t%s\t-\t-\t3\t-\t%s\t-\t-\tgated\t-\t0\t1\t1\tno\tlocal\t-\n' \
  "$1" "$([ "$3" = pass ] && echo pass || echo fail)" "$2" "$(( $1 - 60 ))" "${5:-full}" "$3" "${6:--}" "$4" >> "$SG_STATE/gate-history"; }
RED='the base battery failed at tests/test_x.py::test_y'
hold() { ( gh_api() { case "$*" in *issues/900*) cat "$T/issue-900" 2>/dev/null || echo open;; *) return 1;; esac; }
  . "$d/basered.sh"; sg_base_red_hold ); }
reset() { rm -rf "$SG_STATE"; mkdir -p "$SG_STATE/auto"; : > "$T/filed.log"; rm -f "$T/issue-900"; }

# a: one red full check is the wave's own, never the base's: no hold, no bug.
reset; row 1000 aaa fail tip full "$RED"
o=$(hold); rc=$?
[ $rc = 1 ] && ok "a: one red check sets no hold" || ko "a: rc $rc: '$o'"
[ -s "$T/filed.log" ] && ko "a: a bug was filed for one red" || ok "a: no bug for one red"

# b: two red full checks on two trees at the same test set the hold, name the test and the bug, and file one bug.
row 1100 bbb fail landing full "$RED"
o=$(hold); rc=$?
[ $rc = 0 ] && ok "b: two reds at one test on two trees set the hold" || ko "b: rc $rc: '$o'"
case "$o" in *"tests/test_x.py::test_y"*) ok "b: the hold names the red test";; *) ko "b: no test named: '$o'";; esac
case "$o" in *"#900"*) ok "b: the hold names the bug that owns it";; *) ko "b: no bug named: '$o'";; esac
o=$(hold)
[ "$(/usr/bin/grep -c . "$T/filed.log")" = 1 ] && ok "b: one bug for the red, however many passes read it" || ko "b: filings: '$(cat "$T/filed.log")'"

# c: an unproven check never sets the hold, and it never lifts one either.
reset; row 1000 aaa fail tip full "$RED"; row 1100 bbb unproven tip full "bounded: stopped"
o=$(hold); [ $? = 1 ] && ok "c: an unproven check sets no hold" || ko "c: '$o'"
row 1200 ccc fail tip full "$RED"
o=$(hold); [ $? = 0 ] && ok "c: an unproven check between two reds does not break them" || ko "c: '$o'"
row 1300 ddd unproven tip full "bounded: stopped"
o=$(hold); [ $? = 0 ] && ok "c: an unproven check does not lift the hold" || ko "c: '$o'"

# d: a green full check after the red lifts the hold; a delta or a child gate never does.
row 1400 eee pass child full -; row 1500 fff pass tip delta -
o=$(hold); [ $? = 0 ] && ok "d: a child gate or a delta green does not lift the hold" || ko "d: '$o'"
row 1600 ggg pass tip full -
o=$(hold); [ $? = 1 ] && ok "d: a green full check lifts the hold" || ko "d: '$o'"

# e: the close of the bug that owns the red lifts the hold, and the same red does not set it again.
reset; row 1000 aaa fail tip full "$RED"; row 1100 bbb fail tip full "$RED"
o=$(hold); [ $? = 0 ] || ko "e: no hold to lift: '$o'"
echo closed > "$T/issue-900"
o=$(hold); [ $? = 1 ] && ok "e: the close of its bug lifts the hold" || ko "e: '$o'"
o=$(hold); [ $? = 1 ] && [ "$(/usr/bin/grep -c . "$T/filed.log")" = 1 ] && ok "e: the closed red files no second bug" || ko "e: '$o', filings '$(cat "$T/filed.log")'"
# A newer red pair after the close is a new red: it holds again.
rm -f "$T/issue-900"; row 1200 ccc fail tip full "$RED"; row 1300 ddd fail tip full "$RED"
o=$(hold); [ $? = 0 ] && ok "e: a red measured after the close holds again" || ko "e: '$o'"

# f: two reds at different tests are no base red.
reset; row 1000 aaa fail tip full "$RED"; row 1100 bbb fail tip full 'the base battery failed at tests/test_z.py::test_w'
o=$(hold); [ $? = 1 ] && ok "f: two reds at different tests set no hold" || ko "f: '$o'"

# g: SG_BASE_RED_HOLD=0 turns the hold off.
reset; row 1000 aaa fail tip full "$RED"; row 1100 bbb fail tip full "$RED"
o=$(SG_BASE_RED_HOLD=0 hold); [ $? = 1 ] && ok "g: SG_BASE_RED_HOLD=0 turns the hold off" || ko "g: '$o'"

# h: the frontier reads the hold, and the bug lane does not, so a bug still dispatches while the hold stands.
/usr/bin/grep -q 'sg_base_red_hold' "$d/autofrontier.sh" && ok "h: the frontier reads the hold" || ko "h: autofrontier.sh does not read the hold"
/usr/bin/grep -q 'sg_base_red_hold' "$d/autobug.sh" && ko "h: the bug lane reads the hold" || ok "h: the bug lane dispatches bugs while the hold stands"

[ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
