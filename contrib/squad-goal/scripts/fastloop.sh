#!/bin/bash
# The fast lane of the watch daemon: every SG_FAST_INTERVAL seconds (120 by default, a profile setting) childcc (a passed child moves its card) and the two
# dispatch passes, autofrontier and autobug, so a dispatchable issue is picked up within one beat instead of on the slow cycle's beat (~6 min late,
# what frontier-idle kept reporting, #1845).
# Why a lane of its own: watch.sh runs them after forkclose, whose fork suite can take minutes behind the GUI suite lock; a dispatch then waits for the whole cycle.
# Each step runs under single.sh, so it never overlaps its own previous run. Pid in $SG_STATE/fastloop.pid.
# --interval prints the resolved beat and exits (tests). SG_FASTLOOP_ONCE=1 runs one iteration and exits (tests).
. "$(dirname "$0")/env.sh"; [ "${1:-}" = --interval ] && { echo "$SG_FAST_INTERVAL"; exit 0; }; here="$(dirname "$0")"; P="$SG_STATE/fastloop.pid"
[ -n "${SG_FASTLOOP_ONCE:-}" ] || { echo $$ > "$P"; trap 'rm -f "$P"; exit 0' TERM INT; }
while :; do
  [ -n "${SG_FASTLOOP_ONCE:-}" ] || echo $$ > "$P"   # every beat: watch.sh reads this file to know the lane is up, and a missing one sent the cycle to run childcc inline (#513)
  bash "$here/single.sh" childcc python3 "$here/childcc.py" 2>&1
  # Both go out detached: a pass ends in a squad start that runs for minutes, and it must not hold the beat. They are called bare, as watch.sh calls them,
  # because each takes its own single.sh <stage> lock inside — so a pass already running is skipped here, and never doubled against the cycle's standalone path.
  ( "$here/autofrontier.sh" 2>&1 & ) 2>/dev/null
  ( "$here/autobug.sh" 2>&1 & ) 2>/dev/null
  [ -n "${SG_FASTLOOP_ONCE:-}" ] && exit 0
  sleep "$SG_FAST_INTERVAL"; done
