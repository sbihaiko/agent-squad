#!/bin/bash
# The nightly live check (spec 1163, #1178): the queue's invariants, proven against the running loop and the live state.
# It asserts only what the rules promise (ADR-0153, ADR-0163, ADR-0164):
#   - no child waits on the lock or the slots beyond the bound while every slot holder is itself queued for the lock,
#   - every `unproven` entry carries an obligation with a future deadline,
#   - no measured red is released, and a correctly held red is never a failure.
# The suite drives the real check one time against a fixture state with the shape of the live one: the lock, the slot
# leases and waits, the gate history, the obligations, the settlements and the ledger. A short bound stands in for the
# live one-hour window, so no test sleeps for an hour. The live run is heavy (it samples for an hour), which is why CI's
# fast battery leaves its suite out; the nightly full run carries it.
# usage: bash test-live-check.sh [scripts dir]
# Hermetic first: the live loop exports its own SG_* to every run it dispatches, so drop them all (#1410).
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
for v in $(compgen -e | /usr/bin/grep '^SERIAL_'); do unset "$v"; done
unset SQUAD_CAPS_DIR SQUAD_CAPS_POLL
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_SQUAD=$(cd "$d/../../.." && pwd) SG_STATE=$T/st SG_REPO=o/r SQUAD_CAPS_DIR=$T/caps HEALTH_NOTIFY=/bin/true
export SG_LIVE_CHECK_WINDOW_SECONDS=3600
pids=()
hold() { sleep 300 >/dev/null 2>&1 & pids+=("$!"); printf '%s\n' "$!"; }
cleanup() { local p; for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$T"; }
trap cleanup EXIT
fresh() { rm -rf "$T/st" "$T/caps"; mkdir -p "$T/st/locks/waiting" "$T/caps/waiting" "$T/caps/gates"; : > "$T/st/gate-history"; }
age() { python3 -c 'import os,sys,time; t=time.time()-float(sys.argv[2]); os.utime(sys.argv[1],(t,t))' "$1" "$2"; }
lc() { bash "$d/live-check.sh" "$@"; }
now=$(date +%s)

# 1) A child starved by a queued slot holder is reported (ADR-0163, ADR-0164, #1166).
fresh
h=$(hold); lease_since=$(date +%s)   # the lease is written after its holder starts, as caps.try_take writes it
printf '{"holder":"gate-1","pid":%s,"since":%s}\n' "$h" "$lease_since" > "$T/caps/gates/gate-1.json"
printf '{"ceiling":"caps.gates","holder":"run-1","pid":%s}\n' "$h" > "$T/caps/waiting/gates-run-1.json"
age "$T/caps/waiting/gates-run-1.json" 1000
printf '%s\tgate\t%s\n' "$((now-1000))" "$h" > "$T/st/locks/waiting/gate-$h"
out=$(lc --once --bound 60 2>&1); rc=$?
[ $rc = 1 ] && printf '%s\n' "$out" | /usr/bin/grep -q "^STARVED run-1:" \
  && ok "a child starved by a queued slot holder is reported" || ko "starvation: rc $rc, '$out'"
case "$out" in *"queued for lock gate"*) ok "the report names the lock the slot holder queues for";; *) ko "the report does not name the lock: '$out'";; esac
[ "$(cut -f2 "$T/st/live-check-result" 2>/dev/null)" = fail ] \
  && case "$(cut -f5 "$T/st/live-check-result" 2>/dev/null)" in *STARVED*) true;; *) false;; esac \
  && ok "the result record names the finding, so a reader sees what the run found" || ko "record: '$(cat "$T/st/live-check-result" 2>&1)'"
# The same wait is no starvation while no slot holder queues for the lock: nothing is starved.
rm -f "$T/st/locks/waiting/gate-$h"
out=$(lc --once --bound 60 2>&1); rc=$?
[ $rc = 0 ] && case "$out" in *STARVED*) false;; *) true;; esac \
  && ok "a slot wait whose holders are not queued for the lock is no starvation" || ko "unstarved wait: rc $rc, '$out'"
# ...nor inside the bound.
printf '%s\tgate\t%s\n' "$now" "$h" > "$T/st/locks/waiting/gate-$h"
age "$T/caps/waiting/gates-run-1.json" 5
out=$(lc --once --bound 60 2>&1); rc=$?
[ $rc = 0 ] && case "$out" in *STARVED*) false;; *) true;; esac \
  && ok "a wait inside the bound is no starvation" || ko "in-bound wait: rc $rc, '$out'"

# 2) An unproven entry with no obligation, or with an expired one, is reported (ADR-0164).
fresh
sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
printf '%s\tfast\tfail\t%s\t%s\t-\tdelta\tunproven\tbounded: no check reported a defect\t-\t-\t2\t-\n' \
  "$now" "$sha" "$((now-10))" > "$T/st/gate-history"
out=$(lc --once 2>&1); rc=$?
[ $rc = 1 ] && printf '%s\n' "$out" | /usr/bin/grep -q "^UNPROVEN-NO-OBLIGATION $sha:" \
  && ok "an unproven entry with no obligation is reported" || ko "no obligation: rc $rc, '$out'"
mkdir -p "$T/st/obligations"
printf 'o/r\t%s\tdev\truff mypy pytest\tfull\t%s\n' "$sha" "$((now+900))" > "$T/st/obligations/111"
out=$(lc --once 2>&1); rc=$?
[ $rc = 0 ] && case "$out" in *UNPROVEN*) false;; *) true;; esac \
  && ok "an unproven entry with a future obligation is clean" || ko "future obligation: rc $rc, '$out'"
printf 'o/r\t%s\tdev\truff mypy pytest\tfull\t%s\n' "$sha" "$((now-60))" > "$T/st/obligations/111"
out=$(lc --once 2>&1); rc=$?
[ $rc = 1 ] && printf '%s\n' "$out" | /usr/bin/grep -q "^UNPROVEN-EXPIRED $sha:" \
  && ok "an unproven entry whose obligation expired is reported" || ko "expired obligation: rc $rc, '$out'"
# A settlement answers the entry: the nightly ran the check the obligation named.
rm -f "$T/st/obligations/111"; mkdir -p "$T/st/obligation-settled"
printf '%s\to/r\t%s\tdev\tfull\truff mypy pytest\tgreen\t1@tree\n' "$((now-5))" "$sha" > "$T/st/obligation-settled/111"
out=$(lc --once 2>&1); rc=$?
[ $rc = 0 ] && case "$out" in *UNPROVEN*) false;; *) true;; esac \
  && ok "a settled entry carries an answered obligation" || ko "settled: rc $rc, '$out'"

# 3) The wave entry's own record (the three outcomes: refuse, admit, admit with obligation) is read too.
fresh
mkdir -p "$T/st/state"
printf '{"key":"o/r#7","wave":{"run-9":{"state":"entered","ts":%s,"entered":"entered wave/dev at abc123; unproven: bounded; not run: -"}}}\n' "$now" \
  > "$T/st/state/o__r__7.json"
out=$(lc --once 2>&1); rc=$?
[ $rc = 1 ] && printf '%s\n' "$out" | /usr/bin/grep -q "^UNPROVEN-NO-OBLIGATION o/r#7:" \
  && ok "a wave entry admitted unproven with no obligation is reported" || ko "wave entry: rc $rc, '$out'"
printf '{"key":"o/r#7","wave":{"run-9":{"state":"entered","ts":%s,"entered":"entered wave/dev at abc123; unproven: bounded; not run: -; obligation 222 (ruff at full, base dev, deadline 99)"}}}\n' "$now" \
  > "$T/st/state/o__r__7.json"
mkdir -p "$T/st/obligations"
printf 'o/r\tabc123\tdev\truff\tfull\t%s\n' "$((now+900))" > "$T/st/obligations/222"
out=$(lc --once 2>&1); rc=$?
[ $rc = 0 ] && case "$out" in *UNPROVEN*) false;; *) true;; esac \
  && ok "a wave entry admitted with its obligation is clean" || ko "wave obligation: rc $rc, '$out'"
printf 'o/r\tabc123\tdev\truff\tfull\t%s\n' "$((now-60))" > "$T/st/obligations/222"
out=$(lc --once 2>&1); rc=$?
[ $rc = 1 ] && printf '%s\n' "$out" | /usr/bin/grep -q "^UNPROVEN-EXPIRED o/r#7:" \
  && ok "a wave entry whose obligation expired is reported" || ko "wave expired: rc $rc, '$out'"

# 4) A correctly held red is never a failure (ADR-0164), and a release the record cannot justify is reported.
fresh
mkdir -p "$T/st/gate-reds"
printf '%s\to/r\tdeadbeef\tdelta\truff mypy pytest\ta test failed\t-\t-\n' "$now" > "$T/st/gate-reds/k1"
printf '%s\tfast\tfail\tdeadbeef\t%s\t-\tdelta\tfail\ta test failed\truff mypy pytest\t-\t2\tred\n' \
  "$((now-10))" "$((now-20))" > "$T/st/gate-history"
out=$(lc --once 2>&1); rc=$?
[ $rc = 0 ] && case "$out" in *RED-RELEASED*) false;; *) true;; esac \
  && ok "the check never fails on a correctly held red" || ko "held red: rc $rc, '$out'"
# A covered pass on the same tree releases it, and the record says so.
printf '%s\tfast\tpass\tdeadbeef\t%s\t-\tdelta\tpass\t-\truff mypy pytest\t-\t2\tcleared-green deadbeef\n' \
  "$((now-5))" "$((now-6))" >> "$T/st/gate-history"
out=$(lc --once 2>&1); rc=$?
[ $rc = 0 ] && case "$out" in *RED-RELEASED*) false;; *) true;; esac \
  && ok "a covered pass may release the red" || ko "covered release: rc $rc, '$out'"
# A pass of another scope never releases it (ADR-0164, ADR-0018).
fresh
printf '%s\tfast\tfail\tdeadbeef\t%s\t-\tfull\tfail\ta test failed\truff mypy pytest\t-\t2\tred\n' \
  "$((now-10))" "$((now-20))" > "$T/st/gate-history"
printf '%s\tfast\tpass\tdeadbeef\t%s\t-\tdelta\tpass\t-\truff mypy pytest\t-\t2\tcleared-green deadbeef\n' \
  "$((now-5))" "$((now-6))" >> "$T/st/gate-history"
out=$(lc --once 2>&1); rc=$?
[ $rc = 1 ] && printf '%s\n' "$out" | /usr/bin/grep -q "^RED-RELEASED deadbeef:" \
  && ok "a pass of another scope that releases a red is reported" || ko "other scope: rc $rc, '$out'"
# A pass that reran fewer checks than the red named never releases it either.
fresh
printf '%s\tfast\tfail\tdeadbeef\t%s\t-\tfull\tfail\ta test failed\truff mypy pytest\t-\t2\tred\n' \
  "$((now-10))" "$((now-20))" > "$T/st/gate-history"
printf '%s\tfast\tpass\tdeadbeef\t%s\t-\tfull\tpass\t-\truff\t-\t2\tcleared-green deadbeef\n' \
  "$((now-5))" "$((now-6))" >> "$T/st/gate-history"
out=$(lc --once 2>&1); rc=$?
[ $rc = 1 ] && printf '%s\n' "$out" | /usr/bin/grep -q "^RED-RELEASED deadbeef:" \
  && ok "a pass that reran fewer checks than the red named is reported" || ko "unrun checks: rc $rc, '$out'"

# 5) The check holds no dispatch: it takes its own lock, never the gate lock, and writes nothing a dispatch hold reads.
fresh
mkdir -p "$T/st/locks/gate"; echo $$ > "$T/st/locks/gate/pid"
out=$(lc --once 2>&1); rc=$?
[ $rc = 0 ] && [ "$(cat "$T/st/locks/gate/pid" 2>/dev/null)" = $$ ] \
  && ok "a held gate lock is no obstacle and is left as it was" || ko "gate lock: rc $rc, '$(cat "$T/st/locks/gate/pid" 2>&1)'"
[ ! -e "$T/st/nightly-result" ] && [ ! -e "$T/st/promote-reserve" ] && [ ! -e "$T/st/childcc-waiting" ] \
  && ok "the check writes no record a dispatch hold reads" || ko "a dispatch-hold record appeared: '$(/bin/ls "$T/st" 2>&1)'"
[ ! -d "$T/st/locks/live-check" ] && ok "the check's own lock is released when it ends" || ko "the live-check lock stayed behind"
# A live holder of its own lock makes a second start skip at once, never queue behind it.
mkdir -p "$T/st/locks/live-check"; echo $$ > "$T/st/locks/live-check/pid"
out=$(lc --once 2>&1); rc=$?
[ $rc = 73 ] && case "$out" in *"skipped, not queued"*) true;; *) false;; esac \
  && ok "the check's own lock is taken in try mode: a second start never queues" || ko "try mode: rc $rc, '$out'"
rm -rf "$T/st/locks/live-check"

# 6) The live run samples the state over its window, and the window is one hour unless the profile names another.
fresh
out=$(SG_LIVE_CHECK_WINDOW_SECONDS=1 SG_LIVE_CHECK_INTERVAL_SECONDS=0.2 lc 2>&1); rc=$?
n=$(cut -f4 "$T/st/live-check-result" 2>/dev/null)
[ $rc = 0 ] && case "$n" in ''|*[!0-9]*) false;; *) [ "$n" -ge 2 ];; esac \
  && ok "the check samples the live state over its window ($n samples)" || ko "sampling: rc $rc, samples '$n', '$out'"
[ "$(/usr/bin/grep -c 'LIVE_CHECK_WINDOW_SECONDS:-3600' "$d/live-check.sh" 2>/dev/null)" = 1 ] \
  && ok "one hour is the live check's own default window" || ko "the default window is not one hour"

[ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
