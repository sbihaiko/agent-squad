#!/bin/bash
# usage: . columnlimit.sh   (then sg_column_limit <column> [target])
# The card limit of one board column, from the plugin defaults and the project settings file (#2287), printed as a whole
# number or `none`. It is the one resolution the frontier, the review pass and the hub read (squad/board_limits.py).
# A read that fails prints nothing: a caller treats an unread limit as no limit, so a broken settings file never stops a
# pass, and the module's own defaults still bound the column.
sg_column_limit() {
  local root; root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd); [ -f "$root/squad/board_limits.py" ] || root=${SG_SQUAD:-}
  PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" python3 -m squad.board_limits "$1" --target "${2:-${SG_CHECKOUT:-.}}" 2>/dev/null
}
