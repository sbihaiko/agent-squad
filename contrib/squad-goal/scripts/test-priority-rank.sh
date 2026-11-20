#!/bin/bash
# seconds: 1.77
# Suite for priority_rank.py: the queue order by effective priority across native blocker edges (#1965): runs its pytest file under the gate interpreter.
# usage: bash test-priority-rank.sh
d=$(cd "$(dirname "$0")" && pwd); root=$(cd "$d/../../.." && pwd)
. "$d/caps.sh"
# The runner's temp HOME hides the user site that pytest's pygments comes from; the real user base is read, nothing written.
export PYTHONUSERBASE="${PYTHONUSERBASE:-$(HOME="$(eval echo "~$(id -un)")" python3 -c 'import site;print(site.USER_BASE)')}"
cd "$root" && exec "$(sg_gate_python)" -m pytest tests/test_priority_rank.py -q -p no:deepeval
