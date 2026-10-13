#!/bin/bash
# usage: board.sh [--add] <owner> <project> "<Status>" <n>...   REST-only card moves (board.py); flags the hub to refresh at once
export SG_GH_PASS=board   # the pass that spends the GitHub calls, named in the call log (ADR-0277)
exec python3 "$(dirname "$0")/board.py" "$@"
