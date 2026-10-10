#!/bin/bash
# usage: board.sh <owner> <project> "<Status>" <n>...   drive-board's board-status.sh, then flags the hub to refresh at once
~/.claude/skills/drive-board/scripts/board-status.sh "$@"; rc=$?
touch "${SG_STATE:-$HOME/.cache/squad-goal}/board-dirty"; exit $rc
