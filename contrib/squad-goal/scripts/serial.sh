#!/bin/bash
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# usage: serial.sh <name> <command...>   Runs the command while holding the named lock, so two GUI suites never run at once.
# Why: Mesen's SingleInstance (global named mutex) makes a second emulator pass its args to the first and exit 0, so concurrent suites fail with
# "application exited (0) before the hook came up". The lock is a directory with the holder's pid; a lock whose pid is gone is stale and taken over.
# Re-entrant: a command already running under this lock (SG_SERIAL_HELD names it) that asks for it again just runs, so a gate inside a gui-suite holder cannot deadlock.
# SERIAL_POLL_SECONDS (default 3) is the pause between a waiter's looks at the lock; a test lowers it to a fraction of a second.
# SERIAL_TRY=1 is try mode (#459): a lock held by a live holder is skipped at once (exit 73, one line), never queued; the command
# does not inherit the mode, so a lock it takes inside waits as usual.
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
# A pid is not an identity: the system reuses one, so the holder records its own start time beside its pid, and a lock whose
# token does not name the process alive now is stale (#392). An old-format lock, a pid alone, keeps the older reading: a live
# pid is trusted, so a reader never deletes a lock a live holder may still need.

st=${SG_STATE:-/tmp/sg-state};   # one machine-wide path: TMPDIR is per user on macOS, so a TMPDIR default would split the lock
ps_=${SG_PS:-/bin/ps}; n=$1; shift; # GUI kill switch (scripts/gui-tests.sh off, or SG_GUI_TESTS=no for one process tree): the gui-suite lock is the chokepoint every
# Mesen GUI run takes -- the fork gate, the e2e and a hand-run suite -- so one marker stops them all without touching a caller. It
# exits 0, never non-zero: a suite deliberately not run is not a failure, and a red gate would revert every branch in flight.
case "$n" in gui-suite)
  k=""; [ "${SG_GUI_TESTS:-}" = no ] && k="SG_GUI_TESTS=no"; [ -e "$st/auto/gui-tests-off" ] && k="${k:-$st/auto/gui-tests-off}"
  [ -e "$HOME/.squad-gui-tests-off" ] && k="${k:-$HOME/.squad-gui-tests-off}"   # the durable copy: a suite that inherits SG_STATE can clean it
  [ -n "$k" ] && { echo "serial: gui-suite NOT run ($k); re-enable with scripts/gui-tests.sh on" >&2; exit 0; };; esac
case ":${SG_SERIAL_HELD:-}:" in *":$n:"*) exec "$@";; esac; L="$st/locks/$n"; mkdir -p "$st/locks"; w=${SERIAL_WAIT_SECONDS:-1800}; t0=$(date +%s); said=0
hold=${SERIAL_HOLD_SECONDS:-${SG_CALL_DEADLINE_SECONDS:-1800}}; cmdn=$(basename "${1:-command}")
ppid_of() { $ps_ -o ppid= -p "$1" 2>/dev/null | tr -d ' '; }
runs_as() { $ps_ -ww -o command= -p "$1" 2>/dev/null | grep -qF -- "$2"; }
# A pid that is alive and not a zombie: a watchdog that exited but is not yet reaped by its parent is gone (#805).
alive() { kill -0 "$1" 2>/dev/null && [ "$($ps_ -o stat= -p "$1" 2>/dev/null | cut -c1)" != Z ]; }
# The incarnation of a pid: its start time, which GNU ps and BSD ps both print. The token is only ever read back against the pid
# it was written for, so one stable form of it is enough: the fields are squeezed to one space apiece.
inc_of() { $ps_ -o lstart= -p "$1" 2>/dev/null | awk '{$1=$1};1'; }
until mkdir "$L" 2>/dev/null; do
  h=$(cat "$L/pid" 2>/dev/null); hi=$(cat "$L/inc" 2>/dev/null | awk '{$1=$1};1')
  if [ -n "$h" ] && ! kill -0 "$h" 2>/dev/null; then rm -rf "$L"; continue; fi
  # The pid lives, but the process wearing it may have started after the lock was written: the token settles that. A lock in the
  # old form, a pid with no token, is left to the reading above, where a live pid is trusted.
  # A token that cannot be read back (ps failed: another user's pid, another pid namespace) names nothing, so it is no proof of
  # a reused pid: the lock then keeps the live-pid reading above and the deadline reap below.
  if [ -n "$h" ] && [ -n "$hi" ]; then now=$(inc_of "$h"); if [ -n "$now" ] && [ "$hi" != "$now" ]; then rm -rf "$L"; continue; fi; fi
  [ -z "$h" ] && [ -d "$L" ] && [ $(( $(date +%s) - $(mtime "$L") )) -gt 10 ] && { rm -rf "$L"; continue; }   # a lock with no pid after 10 s: its owner died before writing it
  # The holder is alive. Past its recorded deadline with nothing left to stop its command -- its parent is pid 1 (its run is gone),
  # or its recorded watchdog is gone (#805: a holder whose parent is not pid 1 is still wedged) -- it is reaped, matched by the
  # name it recorded, never by a bare pid that may have been reused.
  d=$(cat "$L/due" 2>/dev/null); c=$(cat "$L/cmd" 2>/dev/null); wdp=$(cat "$L/wd" 2>/dev/null)
  if [ -n "$d" ] && [ -n "$c" ] && [ "$(date +%s)" -gt "$d" ] && runs_as "$h" "$c" \
     && { [ "$(ppid_of "$h")" = 1 ] || { [ -n "$wdp" ] && ! alive "$wdp"; }; }; then
    echo "serial: reaping orphaned lock $n: holder pid $h ($c) is past its deadline and nothing stops it (parent gone or watchdog dead)" >&2
    kill -TERM "$h" 2>/dev/null; sleep 1; kill -KILL "$h" 2>/dev/null; rm -rf "$L"; continue; fi
  # One line, the first time this process has to wait, naming what it waits for: a gate that waits for another gate has to say so
  # once -- silence reads as a hang. `said` keeps it to one line however long the wait runs.
  [ -n "${SERIAL_TRY:-}" ] && { echo "serial: lock $n held by pid ${h:-?}: skipped, not queued (SERIAL_TRY)" >&2; exit 73; }
  [ $said = 1 ] || { echo "serial: waiting for lock $n held by pid ${h:-?}" >&2; said=1; }
  [ $(( $(date +%s) - t0 )) -gt "$w" ] && { echo "serial: waited ${w}s for lock $n held by pid ${h:-?}" >&2; exit 75; }
  sleep "${SERIAL_POLL_SECONDS:-3}"; done
hpid=$$; due=$(( $(date +%s) + hold ))
echo "$hpid" > "$L/pid"; printf '%s\n' "$cmdn" > "$L/cmd"; echo "$due" > "$L/due"; inc_of "$hpid" > "$L/inc"
unset SERIAL_TRY; export SG_SERIAL_HELD="${SG_SERIAL_HELD:+$SG_SERIAL_HELD:}$n"; trap 'rm -rf "$L"' EXIT INT TERM
# The deadline reaches the command itself: past it the holder stops the command's whole process tree and releases the lock
# (exit 75). Without this the cap lives only in the waiter's head, where an orphaned holder can never read it.
set -m   # each background job leads its own process group, so the watchdog stops the command and its children together
"$@" & c=$!
( sleep "$hold"
  [ "$(cat "$L/pid" 2>/dev/null)" = "$hpid" ] || exit 0   # the lock moved on: this watchdog has no command left to stop
  [ "$(date +%s)" -ge "$due" ] || exit 0                  # its sleep was cut short: the deadline has not come
  printf '%s\n' "$hpid" > "$L/timed-out"; kill -TERM -- -"$c" 2>/dev/null; sleep 5; kill -KILL -- -"$c" 2>/dev/null ) >/dev/null 2>&1 & wd=$!
echo "$wd" > "$L/wd"   # the watchdog's pid: a waiter that finds it gone past the deadline knows nothing will stop the command (#805)
# The command leads a process group of its own, so a signal to this holder's group does not reach it: a holder that is stopped
# (a pass past its deadline, #130) stops its command's whole tree and its watchdog before it releases the lock (#393).
stop() {   # stderr goes first: the shell's job notices would land in the stopped pass's log, over the line that names its deadline
  exec 2>/dev/null; kill -TERM -- -"$c"; kill -- -"$wd"; kill "$wd"; rm -rf "$L"; exit "$1"; }
trap 'stop 143' TERM; trap 'stop 130' INT
wait "$c"; rc=$?
kill -- -"$wd" 2>/dev/null; kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
[ "$(cat "$L/timed-out" 2>/dev/null)" = "$hpid" ] && exit 75
exit "$rc"
