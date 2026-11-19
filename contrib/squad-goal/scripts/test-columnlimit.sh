#!/bin/bash
# seconds: 1.43
# Regression test for columnlimit.sh (#2287, #2430): the one bash seam over squad/board_limits.py. A working module answers
# a column's card limit only: a machine cap is another unit, and this seam holds none. A module that is missing or fails is
# a defect, never "no limit": the read prints nothing, returns 2, and names the failure on stderr once per pass. Offline and
# hermetic: temp trees, no board read. usage: bash test-columnlimit.sh [path/to/columnlimit.sh]
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

# 2) The seam answers the card limit of the column and no machine cap (#2430). A run slot is another unit: the pass that
#    bounds run slots reads it, and it never enters this seam.
got=$(sg_column_limit Doing "$T/co")
[ "$got" = 7 ] && ok "the seam answers the column's card limit whatever a machine cap says" || ko "the seam answered '$got', not 7"
[ -z "$(type -t sg_board_limit_effective)" ] && ok "the seam holds no machine-cap helper" || ko "the seam still exposes sg_board_limit_effective"

# 2b) The card count of a column (#2430): every board row of that column, and for Doing each hand hold whose issue has no
#     card there. A hold on a card already in Doing counts once.
printf '41\tDoing\n42\tDoing\n43\tIn Progress\n44\tTodo\n' > "$T/board"
got=$(sg_column_cards Doing "$T/board")
[ "$got" = 3 ] && ok "the count reads Doing and In Progress rows" || ko "the count answered '$got', not 3"
printf '[{"number":42,"labels":[{"name":"ready-for-human"}]}]' > "$T/issues.json"
got=$(sg_column_cards Doing "$T/board" "$T/issues.json")
[ "$got" = 3 ] && ok "a hold on a card already in Doing counts once" || ko "a hold on a Doing card counted twice: '$got'"
printf '[{"number":90,"labels":[{"name":"ready-for-HAL9K"}]}]' > "$T/issues.json"
got=$(sg_column_cards Doing "$T/board" "$T/issues.json")
[ "$got" = 4 ] && ok "a hand hold whose issue has no Doing card adds one" || ko "the hold did not count in Doing: '$got'"
got=$(sg_column_cards Todo "$T/board" "$T/issues.json")
[ "$got" = 1 ] && ok "another column counts its rows only" || ko "the Todo count answered '$got', not 1"

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

# 5) A missing squad/board_limits.py is the same defect, never an empty limit.
. "$T/s/columnlimit.sh"; SG_SQUAD=$T/nothing SG_CHECKOUT=$T/co
got=$(sg_column_limit Doing "$T/co" 2>/dev/null); rc=$?
[ "$rc" = 2 ] && [ -z "$got" ] && ok "a missing module returns 2, not an empty limit" || ko "a missing module: '$got' rc=$rc"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
