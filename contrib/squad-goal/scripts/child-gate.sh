#!/bin/bash
# usage: child-gate.sh <suite> [args...]   Runs a shell suite or gate a child session starts (#1762), under the machine's gates ceiling.
# It takes one caps.gates slot (ADR-0166's bound, the one a loop gate takes): a full ceiling makes it wait up to SG_CHILD_GATE_WAIT
# seconds (default 900), and that wait is written as wait, never as run time. A second copy of the same suite file is refused at
# once, with a reason on stderr, and exits 73. Each run appends one tab-separated line to $SG_STATE/child-suite-history:
# finish epoch, start epoch, wait seconds, load at start, load at finish, exit code, suite path, tree sha. The load is the
# one-minute average from uptime, `-` when no reading is possible; it labels the run and decides nothing. Under
# SG_GATE_SLOTS_HELD, set by the command runner that holds the slots for it, the suite takes no slot and its wait is 0.
here="$(cd "$(dirname "$0")" && pwd)"
. "$here/caps.sh"
[ $# -gt 0 ] || { echo "usage: child-gate.sh <suite> [args...]" >&2; exit 2; }
suite=$(cd "$(dirname "$1")" && pwd)/$(basename "$1"); shift
st=${SG_STATE:-/tmp/sg-state}; mkdir -p "$st" 2>/dev/null
lock="$st/child-gate-$(printf '%s' "$suite" | cksum | cut -d' ' -f1).lock"
# The folder exists a moment before its pid is written, so a start that reads it in that instant retries instead of calling a
# live holder stale. A lock whose pid is dead, or one that never stamped itself, is stale and is taken.
take_lock() {
  local i held
  if mkdir "$lock" 2>/dev/null; then echo $$ > "$lock/pid"; return 0; fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    held=$(cat "$lock/pid" 2>/dev/null)
    [ -z "$held" ] || { kill -0 "$held" 2>/dev/null && return 1; break; }
    sleep 0.1
  done
  rm -rf "$lock"
  if mkdir "$lock" 2>/dev/null; then echo $$ > "$lock/pid"; return 0; fi
  return 1
}
if ! take_lock; then
  echo "child-gate: $suite is already running (pid $(cat "$lock/pid" 2>/dev/null)); a second copy is refused, not queued" >&2
  exit 73
fi
holder="child-gate-$$"
took=
trap '[ -z "$took" ] || sg_caps release gates "$holder" >/dev/null 2>&1; rm -rf "$lock"' EXIT
load() { local l; l=$(LC_ALL=C uptime 2>/dev/null | sed -n 's/.*load average[s]*: *\([0-9.]*\).*/\1/p'); echo "${l:--}"; }
if [ -n "${SG_GATE_SLOTS_HELD:-}" ]; then
  start=$(date +%s); wait_s=0
else
  t0=$(date +%s)
  sg_caps take gates "$holder" --count 1 --wait "${SG_CHILD_GATE_WAIT:-900}" --pid $$ --target "$PWD" >/dev/null; rc=$?
  if [ "$rc" != 0 ]; then
    echo "child-gate: no caps.gates slot within ${SG_CHILD_GATE_WAIT:-900}s; $suite did not run (exit $rc)" >&2; exit "$rc"
  fi
  took=1
  start=$(date +%s); wait_s=$((start - t0))
fi
load0=$(load); sha=$(git rev-parse HEAD 2>/dev/null) || sha=-
bash "$suite" "$@"; rc=$?
fin=$(date +%s)
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$fin" "$start" "$wait_s" "$load0" "$(load)" "$rc" "$suite" "$sha" >> "${SG_STATE:-/tmp/sg-state}/child-suite-history"
exit "$rc"
