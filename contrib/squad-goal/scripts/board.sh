#!/bin/bash
# usage: board.sh [--add] <owner> <project> "<Status>" <n>...   REST-only card moves (board.py); flags the hub to refresh at once
#        board.sh --proof <owner> <project> <n> <unproven|green|red>   the card's proof fact (#1176)
export SG_MOVE_PASS="${SG_GH_PASS:-hand}"   # the caller's pass, kept for the transition record; `hand` when there is none (#1909)
export SG_GH_PASS=board   # the pass that spends the GitHub calls, named in the call log (ADR-0277)
exec python3 "$(dirname "$0")/board.py" "$@"
