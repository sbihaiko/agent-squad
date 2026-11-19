#!/bin/bash
# usage: . columnlimit.sh
#   sg_column_limit <column> [target]        the card limit of a column, or nothing for no limit; exit 2 when the read fails
#   sg_column_cards <column> <board> [issues]  the cards a column holds; for Doing, each hand hold adds one (#2430)
# The card limit of one board column (#2287), from the plugin defaults and the project settings file, resolved by
# squad/board_limits.py: the one module the frontier, the review pass and the hub read. The seam answers a card limit
# only (#2430): a machine cap counts run slots, and a run carries one or more cards, so the lower of the two numbers is
# a limit of neither. The pass that bounds a column reads the cap in its own unit.
# A read that fails is a defect, never "no limit": the function prints nothing, returns 2, and names the failure on stderr
# once per pass, so a caller stops on the failure instead of dispatching unbounded.
_SG_COLUMN_ROOT=; _SG_COLUMN_LOGGED=
sg_column_root() {   # the repository that holds squad/board_limits.py, or exit 1
  if [ -z "$_SG_COLUMN_ROOT" ]; then
    local root; root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
    if [ -f "$root/squad/board_limits.py" ]; then _SG_COLUMN_ROOT=$root
    elif [ -n "${SG_SQUAD:-}" ] && [ -f "$SG_SQUAD/squad/board_limits.py" ]; then _SG_COLUMN_ROOT=$SG_SQUAD; fi
  fi
  [ -n "$_SG_COLUMN_ROOT" ]
}
sg_column_fail() {   # <reason>: one line on stderr per pass, never a second
  [ -n "$_SG_COLUMN_LOGGED" ] && return 0
  printf 'COLUMNLIMIT the board column limit is unreadable (%s): squad/board_limits.py is missing or failed; a pass stops instead of dispatching unbounded\n' "$1" >&2
  _SG_COLUMN_LOGGED=1
}
sg_column_read() {   # <column> [target]: set _SG_COLUMN_OUT and return 0, or return 2 when the module fails
  local rc
  _SG_COLUMN_OUT=
  sg_column_root || { sg_column_fail "no squad/board_limits.py"; return 2; }
  # `-P` keeps the caller's working directory off sys.path: the module is the one `_SG_COLUMN_ROOT` names, never a `squad`
  # package of whatever directory the pass happens to run in. A shadowed module read the wrong tree and hid a broken one.
  _SG_COLUMN_OUT=$(PYTHONPATH="$_SG_COLUMN_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m squad.board_limits "$1" --target "${2:-${SG_CHECKOUT:-.}}" 2>/dev/null); rc=$?
  [ "$rc" -eq 0 ] || { sg_column_fail "squad.board_limits exited $rc"; return 2; }
  return 0
}
sg_column_limit() {   # <column> [target]
  sg_column_read "$1" "${2:-}" || return $?
  case "$_SG_COLUMN_OUT" in none) return 0 ;; esac
  printf '%s\n' "$_SG_COLUMN_OUT"
}
sg_column_cards() {   # <column> <board> [issues]: the cards of one column; a hand hold counts in Doing (#2430)
  # The board answers one tab-separated row per card, `<issue>\t<column>`. Doing answers under both its names. A hand hold
  # is a person's own work on a card, so it takes its share of the Doing column: the hold file's label face on the issue
  # (ready-for-human, ready-for-HAL9K) counts one card, and a card already in Doing counts once. A pass reads only the
  # label face (squad.handhold); an issue list that is absent or unreadable adds no hold, and names nothing.
  local col=$1 board=$2 issues=${3:-} rows n held=0 h x
  rows=$(awk -F '\t' -v c="$col" 'NF && ($2 == c || (c == "Doing" && $2 == "In Progress")) { print $1 }' "$board" 2>/dev/null)
  n=$(printf '%s\n' "$rows" | awk 'NF { n++ } END { print n + 0 }')
  if [ "$col" = Doing ] && [ -n "$issues" ] && [ -r "$issues" ]; then
    h=$(jq -r '.[]?|select(.pull_request|not)|select([.labels[]?|(.name // .)]|any(.=="ready-for-human" or .=="ready-for-HAL9K"))|(.number|tostring)' "$issues" 2>/dev/null)
    for x in $h; do case " $(printf '%s' "$rows" | tr '\n' ' ') " in *" $x "*) ;; *) held=$((held + 1));; esac; done
  fi
  printf '%s\n' $((n + held))
}
