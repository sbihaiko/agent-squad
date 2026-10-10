#!/bin/bash
# The fast lane of the watch daemon: every 30 s childcc (a passed child moves its card) and autopilot (review, automerge, round cap).
# Why a lane of its own: watch.sh runs them after forkclose, whose fork suite can take minutes behind the GUI suite lock; a PR that is APPROVE + clean
# then waits for the whole cycle (#1239 waited ~5 min). Each step runs under single.sh, so it never overlaps its own previous run. Pid in $SG_STATE/fastloop.pid.
# SG_FASTLOOP_ONCE=1 runs one iteration and exits (tests). AUTOPILOT-GAP goes to cycle.log when two autopilot starts are more than 120 s apart.
. "$(dirname "$0")/env.sh"; here="$(dirname "$0")"; P="$SG_STATE/fastloop.pid"; G="$SG_STATE/autopilot.last"
[ -n "${SG_FASTLOOP_ONCE:-}" ] || { echo $$ > "$P"; trap 'rm -f "$P"; exit 0' TERM INT; }
while :; do
  now=$(date +%s); last=$(cat "$G" 2>/dev/null || echo "$now"); [ $((now - last)) -gt 120 ] && echo "$(date +%T) AUTOPILOT-GAP $((now - last))s since the previous start" >> "$SG_STATE/cycle.log"
  echo "$now" > "$G"
  bash "$here/single.sh" childcc python3 "$here/childcc.py" 2>&1
  bash "$here/single.sh" autopilot bash "$here/autopilot.sh" 2>&1
  [ -n "${SG_FASTLOOP_ONCE:-}" ] && exit 0
  sleep 30; done
