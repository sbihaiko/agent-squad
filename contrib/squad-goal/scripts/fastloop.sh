#!/bin/bash
# The fast lane of the watch daemon: every SG_FAST_INTERVAL seconds (120 by default, a profile setting) childcc (a passed child moves its card), autopilot (review, automerge, round cap) and the two
# dispatch passes, autofrontier and autobug, so a dispatchable issue is picked up within one beat instead of on the slow cycle's beat (~6 min late,
# what frontier-idle kept reporting, #1845).
# Why a lane of its own: watch.sh runs them after forkclose, whose fork suite can take minutes behind the GUI suite lock; a PR that is APPROVE + clean
# then waits for the whole cycle (#1239 waited ~5 min). Each step runs under single.sh, so it never overlaps its own previous run. Pid in $SG_STATE/fastloop.pid.
# --interval prints the resolved beat and exits (tests). SG_FASTLOOP_ONCE=1 runs one iteration and exits (tests). AUTOPILOT-GAP goes to cycle.log when two autopilot starts are more than 120 s apart.
. "$(dirname "$0")/env.sh"; [ "${1:-}" = --interval ] && { echo "$SG_FAST_INTERVAL"; exit 0; }; here="$(dirname "$0")"; P="$SG_STATE/fastloop.pid"; G="$SG_STATE/autopilot.last"
[ -n "${SG_FASTLOOP_ONCE:-}" ] || { echo $$ > "$P"; trap 'rm -f "$P"; exit 0' TERM INT; }
while :; do
  now=$(date +%s); last=$(cat "$G" 2>/dev/null || echo "$now"); [ $((now - last)) -gt 120 ] && echo "$(date +%T) AUTOPILOT-GAP $((now - last))s since the previous start" >> "$SG_STATE/cycle.log"
  echo "$now" > "$G"
  bash "$here/single.sh" childcc python3 "$here/childcc.py" 2>&1
  bash "$here/single.sh" autopilot bash "$here/autopilot.sh" 2>&1
  # After autopilot, so a merge that freed a slot is seen by the dispatch in the same beat. Both go out detached: a pass ends in a squad
  # start that runs for minutes, and it must not hold the beat. They are called bare, as watch.sh calls them, because each takes its
  # own single.sh <stage> lock inside — so a pass already running is skipped here, and never doubled against the cycle's standalone path.
  ( "$here/autofrontier.sh" 2>&1 & ) 2>/dev/null
  ( "$here/autobug.sh" 2>&1 & ) 2>/dev/null
  [ -n "${SG_FASTLOOP_ONCE:-}" ] && exit 0
  sleep "$SG_FAST_INTERVAL"; done
