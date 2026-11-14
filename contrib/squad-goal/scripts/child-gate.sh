#!/bin/bash
# usage: child-gate.sh <suite> [args...]   Runs a shell suite or gate a child session starts (#1762), under the machine's gates ceiling.
# It takes one caps.gates slot (ADR-0166's bound, the one a loop gate takes): a full ceiling makes it wait up to SG_CHILD_GATE_WAIT
# seconds (default 900), and that wait is written as wait, never as run time. A second copy of the same suite file is refused at
# once, with a reason on stderr, and exits 73. Each run appends one tab-separated line to $SG_STATE/child-suite-history:
# finish epoch, start epoch, wait seconds, load at start, load at finish, exit code, suite path. The load is the one-minute
# average from uptime, `-` when no reading is possible; it labels the run and decides nothing.
here="$(cd "$(dirname "$0")" && pwd)"
. "$here/caps.sh"
[ $# -gt 0 ] || { echo "usage: child-gate.sh <suite> [args...]" >&2; exit 2; }
suite=$(cd "$(dirname "$1")" && pwd)/$(basename "$1"); shift
st=${SG_STATE:-/tmp/sg-state}; mkdir -p "$st" 2>/dev/null
lock="$st/child-gate-$(printf '%s' "$suite" | cksum | cut -d' ' -f1).lock"
if ! mkdir "$lock" 2>/dev/null; then
  held=$(cat "$lock/pid" 2>/dev/null)
  if [ -n "$held" ] && kill -0 "$held" 2>/dev/null; then
    echo "child-gate: $suite is already running (pid $held); a second copy is refused, not queued" >&2; exit 73
  fi
  rm -rf "$lock"; mkdir "$lock" 2>/dev/null || { echo "child-gate: $suite could not take its lock $lock" >&2; exit 73; }
fi
echo $$ > "$lock/pid"
holder="child-gate-$$"
trap 'sg_caps release gates "$holder" >/dev/null 2>&1; rm -rf "$lock"' EXIT
load() { local l; l=$(LC_ALL=C uptime 2>/dev/null | sed -n 's/.*load average[s]*: *\([0-9.]*\).*/\1/p'); echo "${l:--}"; }
t0=$(date +%s)
sg_caps take gates "$holder" --count 1 --wait "${SG_CHILD_GATE_WAIT:-900}" --pid $$ --target "$PWD" >/dev/null || exit $?
start=$(date +%s); wait_s=$((start - t0)); ls=$(load)
bash "$suite" "$@"; rc=$?
fin=$(date +%s)
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$fin" "$start" "$wait_s" "$ls" "$(load)" "$rc" "$suite" >> "$st/child-suite-history"
exit "$rc"
