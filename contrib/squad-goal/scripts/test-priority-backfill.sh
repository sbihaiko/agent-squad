#!/bin/bash
# seconds: 4.30
# Suite for priority_backfill.py and the Priority binding it writes through (#1982): runs their pytest files under the gate interpreter.
# usage: bash test-priority-backfill.sh
d=$(cd "$(dirname "$0")" && pwd); root=$(cd "$d/../../.." && pwd)
. "$d/caps.sh"
cd "$root" && exec "$(sg_gate_python)" -m pytest tests/test_priority_backfill.py tests/test_issue_state_priority.py -q -p no:deepeval
