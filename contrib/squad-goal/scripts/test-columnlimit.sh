#!/bin/bash
# Regression test for columnlimit.sh (#2287): the one bash seam over squad/board_limits.py. A working module answers a
# column's limit and the lower of that limit and a machine cap. A module that is missing or fails is a defect, never "no
# limit": the read prints nothing, returns 2, and names the failure on stderr once per pass. Offline and hermetic: temp
# trees, no board read. usage: bash test-columnlimit.sh [path/to/columnlimit.sh]
src=${1:-$(dirname "$0")/columnlimit.sh}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
if [ ! -f "$src" ]; then echo "FAIL columnlimit.sh: $src not found"; rm -rf "$T"; exit 1; fi
repo=$(cd "$(dirname "$src")/../../.." && pwd); mkdir -p "$T/s" "$T/co/docs/squad"
cp "$src" "$T/s/columnlimit.sh"
limits() { printf 'columns:\n  Doing:\n    max_cards: %s\n' "$1" > "$T/co/docs/squad/defaults.yaml"; }

# 1) A working module answers one column's limit from the project file.
limits 7; . "$T/s/columnlimit.sh"
SG_SQUAD=$repo SG_CHECKOUT=$T/co
got=$(sg_column_limit Doing "$T/co"); rc=$?
[ "$rc" = 0 ] && [ "$got" = 7 ] && ok "the module answers a column's limit" || ko "sg_column_limit answered '$got' rc=$rc"

# 2) The effective limit is the lower of the column limit and the machine cap.
got=$(sg_board_limit_effective Doing 3 "$T/co")
[ "$got" = 3 ] && ok "a machine cap below the column limit applies" || ko "effective answered '$got', not 3"
got=$(sg_board_limit_effective Doing 9 "$T/co")
[ "$got" = 7 ] && ok "a machine cap above the column limit keeps the column limit" || ko "effective answered '$got', not 7"

# 3) A column with no limit answers nothing and rc 0.
limits null
got=$(sg_column_limit Doing "$T/co"); rc=$?
[ "$rc" = 0 ] && [ -z "$got" ] && ok "a column with no limit answers nothing" || ko "no-limit answered '$got' rc=$rc"

# 4) A broken board_limits.py is a defect, not no limit: rc 2, no value, and a named line on stderr once per pass.
mkdir -p "$T/broken/squad"; printf 'def (\n' > "$T/broken/squad/board_limits.py"; : > "$T/broken/squad/__init__.py"
. "$T/s/columnlimit.sh"; SG_SQUAD=$T/broken SG_CHECKOUT=$T/co
sg_column_limit Doing "$T/co" > "$T/out" 2> "$T/err"; rc=$?
[ "$rc" = 2 ] && [ ! -s "$T/out" ] && ok "a broken module returns 2 and no limit" || ko "a broken module returned rc=$rc out='$(cat "$T/out")'"
[ "$(/usr/bin/grep -c COLUMNLIMIT "$T/err")" = 1 ] && ok "the failed read is named on stderr" || ko "stderr: '$(cat "$T/err")'"
sg_column_limit Doing "$T/co" > "$T/out" 2> "$T/err"; rc=$?
[ "$rc" = 2 ] && [ ! -s "$T/err" ] && ok "the failure is said once per pass" || ko "a second read said it again: '$(cat "$T/err")'"
# The effective read falls back to the machine cap with one alert, so dispatch continues inside the machine bound.
mkdir -p "$T/state"; export SG_STATE=$T/state
got=$(sg_board_limit_effective Doing 3 "$T/co" 2>/dev/null); rc=$?
[ "$rc" = 0 ] && [ "$got" = 3 ] && ok "an unread limit falls back to the machine cap" || ko "effective on a broken module: '$got' rc=$rc, not 3 rc=0"
[ "$(/usr/bin/grep -c 'COLUMNLIMIT' "$T/state/alerts.log" 2>/dev/null)" = 1 ] && ok "the fallback writes one alert" || ko "alerts.log: '$(cat "$T/state/alerts.log" 2>/dev/null)'"
sg_board_limit_effective Doing 3 "$T/co" > /dev/null 2>&1
[ "$(/usr/bin/grep -c 'COLUMNLIMIT' "$T/state/alerts.log" 2>/dev/null)" = 1 ] && ok "the alert is written once per pass" || ko "a second fallback alerted again"
got=$(sg_board_limit_effective Doing "" "$T/co" 2>/dev/null); rc=$?
[ "$rc" = 2 ] && [ -z "$got" ] && ok "with no machine cap the unread limit still fails" || ko "no cap on a broken module: '$got' rc=$rc"
unset SG_STATE

# 5) A missing squad/board_limits.py is the same defect, never an empty limit.
. "$T/s/columnlimit.sh"; SG_SQUAD=$T/nothing SG_CHECKOUT=$T/co
got=$(sg_column_limit Doing "$T/co" 2>/dev/null); rc=$?
[ "$rc" = 2 ] && [ -z "$got" ] && ok "a missing module returns 2, not an empty limit" || ko "a missing module: '$got' rc=$rc"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
