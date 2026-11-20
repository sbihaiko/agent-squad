#!/bin/bash
# seconds: 4.30
# Suite for priority_backfill.py and the Priority binding it writes through (#1982): runs their pytest files under the gate interpreter.
# usage: bash test-priority-backfill.sh
d=$(cd "$(dirname "$0")" && pwd); root=$(cd "$d/../../.." && pwd)
. "$d/caps.sh"
# The daily pass hands the suite a temp HOME (#2307), which hides the user site that python3's pygments comes from; the real user base is read (test-hubstream.sh).
export PYTHONUSERBASE="${PYTHONUSERBASE:-$(HOME="$(eval echo "~$(id -un)")" python3 -c 'import site;print(site.USER_BASE)' 2>/dev/null)}"
cd "$root" && exec "$(sg_gate_python)" -m pytest tests/test_priority_backfill.py tests/test_issue_state_priority.py -q -p no:deepeval
