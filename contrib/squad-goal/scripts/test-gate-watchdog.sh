#!/bin/bash
# seconds: 19.44
# Regression test for #1441: a gate that passes, fails, is skipped (exit 73), is stopped past its deadline or is stopped from
# outside leaves no watchdog sleep behind. Each gate's watchdog sleeps its bound plus 5 (gate.sh) and its bound plus 300 (the
# serial hold); every pid under the gate is sampled while it runs, so only a sleep this gate started can count as its leak.
# usage: bash test-gate-watchdog.sh [scripts dir]
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; export SG_STATE=$T/st; mkdir -p "$SG_STATE"
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SQUAD_CAPS_POLL=0.2
git init -q "$T/w"; $G -C "$T/w" commit -q --allow-empty -m one

kids() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do printf '%s\n' "$c"; kids "$c"; done; }
sample() {  # <gate pid>: every pid under the gate, written to $T/seen until it exits
  ( while kill -0 "$1" 2>/dev/null; do kids "$1"; sleep 0.2; done ) >> "$T/seen" & sp=$!; }
leftover() {  # <bound>: the pids sampled under that gate that are still a live sleep of its two durations
  local x
  for x in $(sort -u "$T/seen" 2>/dev/null); do
    [ "$(/bin/ps -o stat= -p "$x" 2>/dev/null | cut -c1)" = Z ] && continue
    case "$(/bin/ps -ww -o command= -p "$x" 2>/dev/null)" in
      "sleep $(( $1 + 5 ))"|"sleep $(( $1 + 300 ))") kill -0 "$x" 2>/dev/null && printf '%s ' "$x";; esac
  done; }
reap_leaks() { local x; for x in $(leftover "$1"); do kill "$x" 2>/dev/null; done; }
settle() { sleep 1; }

: > "$T/seen"
(cd "$T/w" && exec env SG_GATE_BOUND_SECONDS=881 SG_GATE_KIND=fast bash "$d/gate.sh" sh -c 'sleep 1') >/dev/null 2>&1 & g=$!
sample "$g"; wait "$g" 2>/dev/null; rc=$?; wait "$sp" 2>/dev/null
settle; l=$(leftover 881)
[ $rc = 0 ] && [ -z "$l" ] && ok "a passing gate leaves no sleep behind" || { ko "a passing gate left sleep: rc $rc pids '${l:-none}'"; reap_leaks 881; }

: > "$T/seen"
(cd "$T/w" && exec env SG_GATE_BOUND_SECONDS=882 SG_GATE_KIND=fast bash "$d/gate.sh" sh -c 'sleep 1; exit 7') >/dev/null 2>&1 & g=$!
sample "$g"; wait "$g" 2>/dev/null; rc=$?; wait "$sp" 2>/dev/null
settle; l=$(leftover 882)
[ $rc = 7 ] && [ -z "$l" ] && ok "a failing gate leaves no sleep behind" || { ko "a failing gate left sleep: rc $rc pids '${l:-none}'"; reap_leaks 882; }

: > "$T/seen"
sleep 60 >/dev/null 2>&1 & lv=$!
mkdir -p "$SG_STATE/locks/gate"; echo "$lv" > "$SG_STATE/locks/gate/pid"
(cd "$T/w" && exec env SG_GATE_BOUND_SECONDS=883 SERIAL_TRY=1 SG_GATE_KIND=fast bash "$d/gate.sh" true) >/dev/null 2>&1 & g=$!
sample "$g"; wait "$g" 2>/dev/null; rc=$?; wait "$sp" 2>/dev/null
kill "$lv" 2>/dev/null; wait "$lv" 2>/dev/null; rm -rf "$SG_STATE/locks/gate"
settle; l=$(leftover 883)
[ $rc = 73 ] && [ -z "$l" ] && ok "a skipped gate (exit 73) leaves no sleep behind" || { ko "a skipped gate left sleep: rc $rc pids '${l:-none}'"; reap_leaks 883; }

: > "$T/seen"
(cd "$T/w" && exec env SG_GATE_BOUND_SECONDS=3 SG_GATE_KIND=fast bash "$d/gate.sh" sh -c 'sleep 60') >/dev/null 2>&1 & g=$!
sample "$g"; wait "$g" 2>/dev/null; rc=$?; wait "$sp" 2>/dev/null
settle; l=$(leftover 3)
[ -z "$l" ] && ok "a gate stopped past its deadline leaves no sleep behind" || { ko "a deadline gate left sleep (rc $rc): pids '${l:-none}'"; reap_leaks 3; }

# A gate stopped from outside: the watchdog is still inside its sleep when the gate goes, and the gate's group is the only
# thing that can reach it (#1441). No serial wrapper above it, which would otherwise kill the group for it.
: > "$T/seen"
(cd "$T/w" && exec env SG_SERIAL_HELD=gate SG_GATE_BOUND_SECONDS=884 SG_GATE_KIND=fast bash "$d/gate.sh" sh -c 'sleep 60') >/dev/null 2>&1 & g=$!
sample "$g"; sleep 2; kill -TERM "$g" 2>/dev/null; wait "$g" 2>/dev/null; rc=$?; wait "$sp" 2>/dev/null
settle; l=$(leftover 884)
[ $rc = 143 ] && [ -z "$l" ] && ok "a gate stopped from outside leaves no sleep behind" || { ko "a stopped gate left sleep: rc $rc pids '${l:-none}'"; reap_leaks 884; }

rm -rf "$T"; exit $fail
