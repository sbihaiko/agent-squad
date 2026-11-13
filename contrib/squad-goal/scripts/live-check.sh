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
# One check per state directory (#1660): the check holds `$SG_STATE/live-check.lease` (a directory, `mkdir` atomic, with its
# pid) from before it measures until it exits; `--start` is the detached start the nightly uses, and it is a no-op that logs
# one line while a live lease names a check. A dead owner's lease is taken over, never waited for.
# Guard: test-live-check.sh, tests/test_livecheck.py.
here="$(cd "$(dirname "$0")" && pwd)"; st=${SG_STATE:-/tmp/sg-state}; lease=$st/live-check.lease
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# lease_live: the lease names a live pid. An empty lease is live for 10 s only: a crash between its mkdir and its pid is no owner.
lease_live() { local p; p=$(cat "$lease/pid" 2>/dev/null)
  if [ -z "$p" ]; then [ -d "$lease" ] && [ $(( $(date +%s) - $(mtime "$lease") )) -le 10 ]; return; fi
  kill -0 "$p" 2>/dev/null; }
# take_lease: take the lease, or take over a stale one. A stale lease is claimed once per (owner, age), so only the first
# taker removes it: a second taker never deletes a lease the first one just wrote.
take_lease() {
  if mkdir "$lease" 2>/dev/null; then echo $$ > "$lease/pid"; return 0; fi
  lease_live && return 1
  local p k; p=$(cat "$lease/pid" 2>/dev/null); k="$p-$(mtime "$lease")"
  mkdir "$lease.claim-$k" 2>/dev/null || return 1
  [ "$(cat "$lease/pid" 2>/dev/null)" = "$p" ] || return 1
  rm -rf "$lease"; mkdir "$lease" 2>/dev/null && echo $$ > "$lease/pid"
}
if [ "${1:-}" = --start ]; then
  mkdir -p "$st"
  if lease_live; then echo "$(date '+%F %T') live-check: lease held by pid $(cat "$lease/pid" 2>/dev/null); start skipped" >> "$st/live-check.log"; exit 0; fi
  env -u SG_SERIAL_HELD nohup bash "$0" >/dev/null 2>&1 &
  exit 0
fi
if [ "${1:-}" != --run ]; then
  win=${SG_LIVE_CHECK_WINDOW_SECONDS:-3600}; case $win in ''|*[!0-9]*) win=3600;; esac
  SERIAL_TRY=1 SERIAL_HOLD_SECONDS=$(( win + 300 )) exec bash "$here/serial.sh" live-check bash "$0" --run "$@"
fi
shift
root=$(cd "$here/../../.." && pwd); [ -f "$root/squad/livecheck.py" ] || root=${SG_SQUAD:-}
mkdir -p "$st"
# A start that finds a live owner measures nothing and says so in the log. The lease is dropped on every exit, TERM and INT included.
take_lease || { echo "$(date '+%F %T') live-check: lease held by pid $(cat "$lease/pid" 2>/dev/null); this start measured nothing" >> "$st/live-check.log"; exit 0; }
trap '[ "$(cat "$lease/pid" 2>/dev/null)" = "$$" ] && rm -rf "$lease"' EXIT
trap 'exit 143' TERM; trap 'exit 130' INT
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
