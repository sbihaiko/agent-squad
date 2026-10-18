#!/bin/bash
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# usage: single.sh <name> <command...>   Runs the command unless a previous run under the same name is still alive; then it exits 0 at once.
# Why: a slow step (forkclose waits for the GUI suite lock) must not hold the rest of the watch cycle, and a step started in the background
# must not overlap its own previous run. Lock = directory with the holder's pid, stale when the pid is gone (as serial.sh, which waits instead).
# The state directory the locks live in: the caller's SG_STATE, else the seam's own base (address.sh) -- a path spelled here
# would be a second dialect of the state rule the seam owns (test_squad_goal_state_split).
if [ -z "${SG_STATE:-}" ]; then . "$(dirname "$0")/address.sh"; SG_STATE=$(sg_state_base); fi
st=$SG_STATE; n=$1; shift; L="$st/locks/single-$n"; mkdir -p "$st/locks"
# SG_SINGLE_WAIT=<polls>: a caller that must not skip its step in silence waits up to that many one-second polls for the holder
# (#471), and still exits 0 when the lock stays held; the caller reads the holder from $L/pid to say why.
w=${SG_SINGLE_WAIT:-0}
while [ "$w" -gt 0 ] && [ -d "$L" ] && h=$(cat "$L/pid" 2>/dev/null) && [ -n "$h" ] && kill -0 "$h" 2>/dev/null; do sleep 1; w=$((w-1)); done
if ! mkdir "$L" 2>/dev/null; then
  h=$(cat "$L/pid" 2>/dev/null)
  if [ -n "$h" ] && ! kill -0 "$h" 2>/dev/null; then rm -rf "$L"; mkdir "$L" 2>/dev/null || exit 0   # stale: take over
  elif [ -z "$h" ] && [ $(( $(date +%s) - $(mtime "$L") )) -gt 10 ]; then rm -rf "$L"; mkdir "$L" 2>/dev/null || exit 0   # no pid after 10 s: owner died between mkdir and write
  else exit 0; fi
fi
# The release is the holder's only: a lock taken over meanwhile names another pid, and removing it would start a pass beside that holder (#531).
echo $$ > "$L/pid"; trap '[ "$(cat "$L/pid" 2>/dev/null)" = "$$" ] && rm -rf "$L"' EXIT INT TERM
"$@"
