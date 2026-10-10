#!/bin/bash
# usage: board.sh [--add] <owner> <project> "<Status>" <n>...   REST-only card moves (board.py); flags the hub to refresh at once
exec python3 "$(dirname "$0")/board.py" "$@"
