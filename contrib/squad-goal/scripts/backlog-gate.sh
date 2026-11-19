#!/bin/bash
# usage: backlog-gate.sh <spec|ticket|bug>
# The publisher's gate (#2436): a Backlog card limit holds a new spec or ticket out of a full Backlog.
# It reads the board once, counts the column the publish adds its card to, and asks the one limit module
# (squad/board_limits.py, through columnlimit.sh) for its answer. It adds no card and writes nothing.
# Exit 0 means the publish may add its card. Exit 3 prints the one-line refusal: add no card, and report that line as the
# reason the publish stopped. Any other exit means the board or the settings did not answer: add no card, and report the
# line too, because a read that fails is a defect, never "no limit" (ADR-0018).
# A bug is startable by design (ADR-0148), so it passes at once and reads no board.
here="$(cd "$(dirname "$0")" && pwd)"
. "$here/columnlimit.sh"
kind=${1:-}
case "$kind" in
  bug) exit 0 ;;
  spec|ticket) ;;
  *) echo "usage: backlog-gate.sh <spec|ticket|bug>" >&2; exit 2 ;;
esac
col=$(sg_column_publish_column "$kind") || { echo "BACKLOG-GATE the publish column is unreadable; add no card"; exit 2; }
rows=$(python3 "$here/issue_state.py" 2>/dev/null) || { echo "BACKLOG-GATE the board did not answer; add no card"; exit 2; }
cards=$(printf '%s\n' "$rows" | awk -F '\t' -v c="$col" 'NF && $2 == c' | wc -l | tr -d ' ')
sg_column_publish_refusal "$kind" "$cards"; rc=$?
case "$rc" in
  0) exit 0 ;;
  3) exit 3 ;;
esac
echo "BACKLOG-GATE the Backlog limit is unreadable; add no card"
exit 2
