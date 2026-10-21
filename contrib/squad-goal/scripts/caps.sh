#!/bin/bash
# Sourced by gate.sh and branchreviewpass.sh: the machine-wide budget's one command (squad/caps.py), never a second lock.
#   sg_caps take <gates|reviews> <holder> [--count N] [--try] [--wait SECONDS] [--pid PID] [--target DIR]
#   sg_caps release <kind> <holder>
# `take` prints the count of slots it took: the count it asked for, the lower ceiling, or the free slots when two or more are free
# (#734). Try mode exits 73, a wait past its bound exits 75, as serial.sh does. A holder that died loses its slots to the next
# count, so a release is a courtesy, never a need.
# The module is the one of the repository this script sits in, so the command and its caller are one version; the live copy is
# not in a repository, and it reads SG_SQUAD (env.sh sets it) instead.
sg_caps() {
  local root; root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd); [ -f "$root/squad/caps.py" ] || root=${SG_SQUAD:-}
  PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" python3 -m squad.caps "$@"
}
