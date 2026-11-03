#!/bin/bash
# usage: live-check.sh [--once] [--bound SECONDS]   The nightly live check (spec 1163, #1178): the queue's invariants,
# proved against the running loop and the live state, over one hour.
# It asserts only what the rules promise (ADR-0153, ADR-0163, ADR-0164):
#   - no child waits on the lock or the slots beyond the bound while every holder of those slots is queued for the lock;
#   - every `unproven` entry carries an obligation with a future deadline, or a settlement that answered it;
#   - no measured red is released, and a red the record still holds is never a failure.
# The reading is `squad/livecheck.py`; this script is the door the nightly runs. It takes its own lock in try mode, so a
# second start skips at once and never queues behind the first (SERIAL_TRY), and it takes no gate lock and writes no record
# a dispatch hold reads: it holds no dispatch (ADR-0163). One result record is its only write beside its log.
# A finding reaches the owner as one line in `$SG_STATE/alerts.log` and one notification; a clean pass stays in the log.
# SG_LIVE_CHECK_WINDOW_SECONDS is its window, one hour unless the profile names another; SG_LIVE_CHECK_INTERVAL_SECONDS is
# the pause between its samples; SG_LIVE_CHECK_BOUND_SECONDS is the bound a wait must pass before it can be a starvation.
# Guard: test-live-check.sh, tests/test_livecheck.py.
here="$(cd "$(dirname "$0")" && pwd)"; st=${SG_STATE:-/tmp/sg-state}
if [ "${1:-}" != --run ]; then
  win=${SG_LIVE_CHECK_WINDOW_SECONDS:-3600}; case $win in ''|*[!0-9]*) win=3600;; esac
  SERIAL_TRY=1 SERIAL_HOLD_SECONDS=$(( win + 300 )) exec bash "$here/serial.sh" live-check bash "$0" --run "$@"
fi
shift
root=$(cd "$here/../../.." && pwd); [ -f "$root/squad/livecheck.py" ] || root=${SG_SQUAD:-}
mkdir -p "$st"
# The reading must be there before a finding can mean anything: an interpreter that cannot import it measured nothing, so
# it exits 2 and raises no alert, never the exit 1 a finding uses.
PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" python3 -c 'import squad.livecheck' >/dev/null 2>&1 \
  || { echo "live-check: squad.livecheck is not readable from $root; nothing was measured" >&2; exit 2; }
out=$(PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" python3 -m squad.livecheck "$@" 2>&1); rc=$?
[ -n "$out" ] && { printf '%s\n' "$out" >> "$st/live-check.log"; printf '%s\n' "$out"; }
case $rc in
  0|1) ;;
  *) exit "$rc";;
esac
[ $rc = 0 ] && exit 0
found=$(printf '%s\n' "$out" | /usr/bin/grep '^[A-Z][A-Z-]* ')
msg="LIVE CHECK: $(printf '%s\n' "$found" | wc -l | tr -d ' ') finding(s) in the queue's invariants: $(printf '%s\n' "$found" | cut -d' ' -f1 | sort -u | tr '\n' ' ')"
msg=${msg% }
printf '%s %s\n' "$(date '+%F %T')" "$msg" >> "$st/alerts.log"
notify=${HEALTH_NOTIFY:-/usr/bin/osascript}
[ -x "$notify" ] && "$notify" -e "display notification \"${msg//\"/\'}\" with title \"squad-goal\"" >/dev/null 2>&1
exit 1
