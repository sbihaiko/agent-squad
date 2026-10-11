#!/bin/bash
# usage: serial.sh <name> <command...>   Runs the command while holding the named lock, so two GUI suites never run at once.
# Why: Mesen's SingleInstance (global named mutex) makes a second emulator pass its args to the first and exit 0, so concurrent suites fail with
# "application exited (0) before the hook came up". The lock is a directory with the holder's pid; a lock whose pid is gone is stale and taken over.
# Re-entrant: a command already running under this lock (SG_SERIAL_HELD names it) that asks for it again just runs, so a gate inside a gui-suite holder cannot deadlock.
# SERIAL_WAIT_SECONDS (default 1800) bounds the wait; the command's exit code is passed through (75 when the wait ran out).
st=${SG_STATE:-$HOME/.cache/squad-goal}; n=$1; shift; case ":${SG_SERIAL_HELD:-}:" in *":$n:"*) exec "$@";; esac; L="$st/locks/$n"; mkdir -p "$st/locks"; w=${SERIAL_WAIT_SECONDS:-1800}; t0=$(date +%s)
until mkdir "$L" 2>/dev/null; do
  h=$(cat "$L/pid" 2>/dev/null); if [ -n "$h" ] && ! kill -0 "$h" 2>/dev/null; then rm -rf "$L"; continue; fi
  [ -z "$h" ] && [ -d "$L" ] && [ $(( $(date +%s) - $(stat -f %m "$L" 2>/dev/null || echo 0) )) -gt 10 ] && { rm -rf "$L"; continue; }   # a lock with no pid after 10 s: its owner died before writing it
  [ $(( $(date +%s) - t0 )) -gt "$w" ] && { echo "serial: waited ${w}s for lock $n held by pid ${h:-?}" >&2; exit 75; }
  sleep 3; done
echo $$ > "$L/pid"; export SG_SERIAL_HELD="${SG_SERIAL_HELD:+$SG_SERIAL_HELD:}$n"; trap 'rm -rf "$L"' EXIT INT TERM
"$@"
