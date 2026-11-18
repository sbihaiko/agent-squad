#!/bin/bash
# usage: . columnlimit.sh
#   sg_column_limit <column> [target]                  the number, or nothing for no limit; exit 2 when the read fails
#   sg_board_limit_effective <column> <machine_cap> [target]   the lower of the column limit and the machine cap; exit 2 on a failed read
# The card limit of one board column (#2287), from the plugin defaults and the project settings file, resolved by
# squad/board_limits.py: the one module the frontier, the review pass and the hub read. `effective` combines the column
# limit with the machine cap that also bounds it, so a column limit never raises a machine cap.
# A read that fails is a defect, never "no limit": the function prints nothing, returns 2, and names the failure on stderr
# once per pass. A caller stops on the failure instead of dispatching with an unread bound.
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
sg_column_read() {   # <column> [machine_cap] [target]: set _SG_COLUMN_OUT and return 0, or return 2 when the module fails
  local rc; local -a args
  _SG_COLUMN_OUT=
  sg_column_root || { sg_column_fail "no squad/board_limits.py"; return 2; }
  args=("$1" --target "${3:-${SG_CHECKOUT:-.}}")
  case "${2:-}" in ''|none) ;; *) args+=(--machine-cap "$2") ;; esac
  # `-P` keeps the caller's working directory off sys.path: the module is the one `_SG_COLUMN_ROOT` names, never a `squad`
  # package of whatever directory the pass happens to run in. A shadowed module read the wrong tree and hid a broken one.
  _SG_COLUMN_OUT=$(PYTHONPATH="$_SG_COLUMN_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m squad.board_limits "${args[@]}" 2>/dev/null); rc=$?
  [ "$rc" -eq 0 ] || { sg_column_fail "squad.board_limits exited $rc"; return 2; }
  return 0
}
sg_column_limit() {   # <column> [target]
  sg_column_read "$1" "" "${2:-}" || return $?
  case "$_SG_COLUMN_OUT" in none) return 0 ;; esac
  printf '%s\n' "$_SG_COLUMN_OUT"
}
sg_board_limit_effective() {   # <column> <machine_cap> [target]
  sg_column_read "$1" "${2:-}" "${3:-}" || return $?
  case "$_SG_COLUMN_OUT" in none) return 0 ;; esac
  printf '%s\n' "$_SG_COLUMN_OUT"
}
