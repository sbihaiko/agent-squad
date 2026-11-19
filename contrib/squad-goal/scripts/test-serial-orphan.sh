#!/bin/bash
# seconds: 5.14
# Guard: issue #1660. A serial holder's watchdog sleeps out the whole hold, so a holder that dies without running its traps
# (SIGKILL, a killed worker, a killed run tree) leaves the watchdog behind, orphaned to pid 1. The machine carried 29 such
# roots and 60 processes, each holding a `sleep` for the rest of its hold. The watchdog belongs to the holder: when the
# holder is gone it ends, within a few seconds, and holds nothing.
# usage: bash test-serial-orphan.sh
set -u
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
cleanup() { [ -n "${h:-}" ] && kill -9 "$h" 2>/dev/null; [ -n "${wd:-}" ] && kill -9 "$wd" 2>/dev/null; rm -rf "$T"; }
trap cleanup EXIT
export SG_STATE=$T/st SG_PS=/bin/ps; mkdir -p "$SG_STATE"
SERIAL_HOLD_SECONDS=300 bash "$d/serial.sh" orphan-test sleep 300 >/dev/null 2>&1 & h=$!
for _ in $(seq 1 40); do [ -s "$SG_STATE/locks/orphan-test/wd" ] && break; sleep 0.1; done
wd=$(cat "$SG_STATE/locks/orphan-test/wd" 2>/dev/null)
[ -n "$wd" ] && kill -0 "$wd" 2>/dev/null && ok "the holder started a watchdog" || ko "no watchdog: the case cannot run"
kill -9 "$h" 2>/dev/null; wait "$h" 2>/dev/null   # the holder dies the way a killed worker dies: no trap runs
gone=0
for _ in $(seq 1 30); do kill -0 "$wd" 2>/dev/null || { gone=1; break; }; sleep 0.2; done
[ "$gone" = 1 ] && ok "the watchdog ends within 6 s of its holder's death" \
  || ko "the watchdog outlived its holder by more than 6 s: $(/bin/ps -ww -o pid=,ppid=,etime=,command= -p "$wd" 2>/dev/null)"
[ "$fail" = 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
