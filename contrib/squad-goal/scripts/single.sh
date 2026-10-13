#!/bin/bash
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# usage: single.sh <name> <command...>   Runs the command unless a previous run under the same name is still alive; then it exits 0 at once.
# Why: a slow step (forkclose waits for the GUI suite lock) must not hold the rest of the watch cycle, and a step started in the background
# must not overlap its own previous run. Lock = directory with the holder's pid, stale when the pid is gone (as serial.sh, which waits instead).
st=${SG_STATE:-$HOME/.cache/squad-goal}; n=$1; shift; L="$st/locks/single-$n"; mkdir -p "$st/locks"
if ! mkdir "$L" 2>/dev/null; then
  h=$(cat "$L/pid" 2>/dev/null)
  if [ -n "$h" ] && ! kill -0 "$h" 2>/dev/null; then rm -rf "$L"; mkdir "$L" 2>/dev/null || exit 0   # stale: take over
  elif [ -z "$h" ] && [ $(( $(date +%s) - $(mtime "$L") )) -gt 10 ]; then rm -rf "$L"; mkdir "$L" 2>/dev/null || exit 0   # no pid after 10 s: owner died between mkdir and write
  else exit 0; fi
fi
echo $$ > "$L/pid"; trap 'rm -rf "$L"' EXIT INT TERM
"$@"
