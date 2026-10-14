#!/bin/bash
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# usage: serial.sh <name> <command...>   Runs the command while holding the named lock, so two GUI suites never run at once.
# Why: Mesen's SingleInstance (global named mutex) makes a second emulator pass its args to the first and exit 0, so concurrent suites fail with
# "application exited (0) before the hook came up". The lock is a directory with the holder's pid; a lock whose pid is gone is stale and taken over.
# Re-entrant: a command already running under this lock (SG_SERIAL_HELD names it) that asks for it again just runs, so a gate inside a gui-suite holder cannot deadlock.
# SERIAL_WAIT_SECONDS (default 1800) bounds the wait, and a waiter prints one line naming its holder before it waits, so a gate
# held up by another gate says so instead of reading as a hang; the command's exit code is passed through (75 when the wait ran out).
#
# A gate that outlives its run is found without a caller (ADR-0051). The holder records the deadline it keeps (SERIAL_HOLD_SECONDS,
# else the run's own SG_CALL_DEADLINE_SECONDS, else 1800) and its command name beside its pid, stops its own command at that
# deadline (exit 75), and a waiter reaps a holder that is past its deadline AND orphaned (its parent is pid 1: its run is gone),
# matching the holder by the name recorded in the lock so a reused pid is never killed. A live, owned holder is only waited for.
# The stop is proved by the wall clock and by the lock: the holder stops on a marker that names it, written by its own watchdog
# only while that holder still owns the lock and only past the recorded deadline (#392). The holder's own cleanup TERMs the
# watchdog's process group, and the sleep of that watchdog can be the process that dies: a sleep cut short is not a deadline.

st=${SG_STATE:-${TMPDIR:-/tmp}/sg-state}; n=$1; shift; # GUI kill switch (scripts/gui-tests.sh off, or SG_GUI_TESTS=no for one process tree): the gui-suite lock is the chokepoint every
# Mesen GUI run takes -- the fork gate, the e2e and a hand-run suite -- so one marker stops them all without touching a caller. It
# exits 0, never non-zero: a suite deliberately not run is not a failure, and a red gate would revert every branch in flight.
case "$n" in gui-suite)
  k=""; [ "${SG_GUI_TESTS:-}" = no ] && k="SG_GUI_TESTS=no"; [ -e "$st/auto/gui-tests-off" ] && k="${k:-$st/auto/gui-tests-off}"
  [ -e "$HOME/.squad-gui-tests-off" ] && k="${k:-$HOME/.squad-gui-tests-off}"   # the durable copy: a suite that inherits SG_STATE can clean it
  [ -n "$k" ] && { echo "serial: gui-suite NOT run ($k); re-enable with scripts/gui-tests.sh on" >&2; exit 0; };; esac
case ":${SG_SERIAL_HELD:-}:" in *":$n:"*) exec "$@";; esac; L="$st/locks/$n"; mkdir -p "$st/locks"; w=${SERIAL_WAIT_SECONDS:-1800}; t0=$(date +%s); said=0
hold=${SERIAL_HOLD_SECONDS:-${SG_CALL_DEADLINE_SECONDS:-1800}}; cmdn=$(basename "${1:-command}")
ppid_of() { /bin/ps -o ppid= -p "$1" 2>/dev/null | tr -d ' '; }
runs_as() { /bin/ps -ww -o command= -p "$1" 2>/dev/null | grep -qF -- "$2"; }
until mkdir "$L" 2>/dev/null; do
  h=$(cat "$L/pid" 2>/dev/null)
  if [ -n "$h" ] && ! kill -0 "$h" 2>/dev/null; then rm -rf "$L"; continue; fi
  [ -z "$h" ] && [ -d "$L" ] && [ $(( $(date +%s) - $(mtime "$L") )) -gt 10 ] && { rm -rf "$L"; continue; }   # a lock with no pid after 10 s: its owner died before writing it
  # The holder is alive. Past its recorded deadline with no owner left (its parent is pid 1: its run is gone), it is a stop
  # with nobody behind it, so reap it -- matched by the name it recorded, never by a bare pid that may have been reused.
  d=$(cat "$L/due" 2>/dev/null); c=$(cat "$L/cmd" 2>/dev/null)
  if [ -n "$d" ] && [ -n "$c" ] && [ "$(date +%s)" -gt "$d" ] && [ "$(ppid_of "$h")" = 1 ] && runs_as "$h" "$c"; then
    echo "serial: reaping orphaned lock $n: holder pid $h ($c) is past its deadline and its parent is gone" >&2
    kill -TERM "$h" 2>/dev/null; sleep 1; kill -KILL "$h" 2>/dev/null; rm -rf "$L"; continue; fi
  # One line, the first time this process has to wait, naming what it waits for: a gate that waits for another gate has to say so
  # once -- silence reads as a hang. `said` keeps it to one line however long the wait runs.
  [ $said = 1 ] || { echo "serial: waiting for lock $n held by pid ${h:-?}" >&2; said=1; }
  [ $(( $(date +%s) - t0 )) -gt "$w" ] && { echo "serial: waited ${w}s for lock $n held by pid ${h:-?}" >&2; exit 75; }
  sleep 3; done
hpid=$$; due=$(( $(date +%s) + hold ))
echo "$hpid" > "$L/pid"; printf '%s\n' "$cmdn" > "$L/cmd"; echo "$due" > "$L/due"
export SG_SERIAL_HELD="${SG_SERIAL_HELD:+$SG_SERIAL_HELD:}$n"; trap 'rm -rf "$L"' EXIT INT TERM
# The deadline reaches the command itself: past it the holder stops the command's whole process tree and releases the lock
# (exit 75). Without this the cap lives only in the waiter's head, where an orphaned holder can never read it.
set -m   # each background job leads its own process group, so the watchdog stops the command and its children together
"$@" & c=$!
( sleep "$hold"
  [ "$(cat "$L/pid" 2>/dev/null)" = "$hpid" ] || exit 0   # the lock moved on: this watchdog has no command left to stop
  [ "$(date +%s)" -ge "$due" ] || exit 0                  # its sleep was cut short: the deadline has not come
  printf '%s\n' "$hpid" > "$L/timed-out"; kill -TERM -- -"$c" 2>/dev/null; sleep 5; kill -KILL -- -"$c" 2>/dev/null ) >/dev/null 2>&1 & wd=$!
# The command leads a process group of its own, so a signal to this holder's group does not reach it: a holder that is stopped
# (a pass past its deadline, #130) stops its command's whole tree and its watchdog before it releases the lock (#393).
stop() {   # stderr goes first: the shell's job notices would land in the stopped pass's log, over the line that names its deadline
  exec 2>/dev/null; kill -TERM -- -"$c"; kill -- -"$wd"; kill "$wd"; rm -rf "$L"; exit "$1"; }
trap 'stop 143' TERM; trap 'stop 130' INT
wait "$c"; rc=$?
kill -- -"$wd" 2>/dev/null; kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
[ "$(cat "$L/timed-out" 2>/dev/null)" = "$hpid" ] && exit 75
exit "$rc"
