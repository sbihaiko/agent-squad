#!/bin/bash
# Suite for priority_rank.py: the queue order by effective priority across native blocker edges (#1965): runs its pytest file under the gate interpreter.
# usage: bash test-priority-rank.sh
d=$(cd "$(dirname "$0")" && pwd); root=$(cd "$d/../../.." && pwd)
. "$d/caps.sh"
cd "$root" && exec "$(sg_gate_python)" -m pytest tests/test_priority_rank.py -q -p no:deepeval
