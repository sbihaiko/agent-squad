#!/bin/bash
# seconds: 7.32
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
# The check a case starts is detached, so the suite ends every one of them, by pid and process group, before it exits, a red
# run included: each fake reading records its own pid and its process group in its runs file. A check runs under serial.sh's
# `set -m`, so its reading leads a group of its own, never the group the suite shares with its caller; the suite signals that
# group, so the reading ends with the door. A lease in lc or le is the suite's own sleep holder, not a check.
runs=("$T"/ra "$T"/rc "$T"/rd "$T"/re1 "$T"/re2 "$T"/rf "$T"/rh)
ck_pids() { local f; for f in "${runs[@]}"; do [ -f "$f" ] && awk '/^start /{print $2}' "$f"; done; }
ck_groups() { local f; for f in "${runs[@]}"; do [ -f "$f" ] && awk '/^start /{print $3}' "$f"; done; }
alive() { kill -0 "$1" 2>/dev/null; }
alive_group() { [ -n "$1" ] && kill -0 -- "-$1" 2>/dev/null; }
left_of() { local p g; for p in $(awk '/^start /{print $2}' "$1" 2>/dev/null); do alive "$p" && printf '%s ' "$p"; done
  for g in $(awk '/^start /{print $3}' "$1" 2>/dev/null); do alive_group "$g" && printf 'g%s ' "$g"; done; }
left_checks() { local p g; for g in $(ck_groups | sort -u); do alive_group "$g" && printf 'g%s ' "$g"; done
  for p in $(ck_pids | sort -u); do alive "$p" && printf '%s ' "$p"; done; }
end_checks() { local g p i
  for g in $(ck_groups | sort -u); do alive_group "$g" && kill -TERM -- "-$g" 2>/dev/null; done
  for p in $(ck_pids | sort -u); do alive "$p" && kill -TERM "$p" 2>/dev/null; done
  i=0; while [ -n "$(left_checks)" ] && [ $i -lt 40 ]; do sleep 0.05; i=$((i+1)); done
  for g in $(ck_groups | sort -u); do alive_group "$g" && kill -KILL -- "-$g" 2>/dev/null; done
  for p in $(ck_pids | sort -u); do alive "$p" && kill -KILL "$p" 2>/dev/null; done; }
cleanup() { local p; for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; [ ${#pids[@]} -gt 0 ] && wait "${pids[@]}" 2>/dev/null
  end_checks; rm -rf "$T"; }
trap cleanup EXIT
fresh() { rm -rf "$T/st" "$T/caps"; mkdir -p "$T/st/locks/waiting" "$T/caps/waiting" "$T/caps/gates"; : > "$T/st/gate-history"; }
age() { python3 -c 'import os,sys,time; t=time.time()-float(sys.argv[2]); os.utime(sys.argv[1],(t,t))' "$1" "$2"; }
lc() { bash "$d/live-check.sh" "$@"; }
now=$(date +%s)

# 1) A child starved by a queued slot holder is reported (ADR-0163, ADR-0164, #1166). The queue marker is the one serial.sh
# writes (#2887): a live pid holds lock gate, and serial.sh, the slot holder, queues for it, so the reading meets the real format.
fresh
h=$(hold); mkdir -p "$T/st/locks/gate"; echo "$h" > "$T/st/locks/gate/pid"
SERIAL_POLL_SECONDS=0.1 SERIAL_WAIT_SECONDS=300 bash "$d/serial.sh" gate true >/dev/null 2>&1 & q=$!; pids+=("$q")
i=0; while [ ! -e "$T/st/locks/waiting/gate-$q" ] && [ $i -lt 100 ]; do sleep 0.02; i=$((i+1)); done
[ -e "$T/st/locks/waiting/gate-$q" ] && ok "serial.sh records its queue marker" || ko "no queue marker from serial.sh pid $q"
lease_since=$(date +%s)   # the lease is written after its holder starts, as caps.try_take writes it
printf '{"holder":"gate-1","pid":%s,"since":%s}\n' "$q" "$lease_since" > "$T/caps/gates/gate-1.json"
printf '{"ceiling":"caps.gates","holder":"run-1","pid":%s}\n' "$h" > "$T/caps/waiting/gates-run-1.json"
age "$T/caps/waiting/gates-run-1.json" 1000
out=$(lc --once --bound 60 2>&1); rc=$?
[ $rc = 1 ] && printf '%s\n' "$out" | /usr/bin/grep -q "^STARVED run-1:" \
  && ok "a child starved by a queued slot holder is reported" || ko "starvation: rc $rc, '$out'"
case "$out" in *"queued for lock gate"*) ok "the report names the lock the slot holder queues for";; *) ko "the report does not name the lock: '$out'";; esac
[ "$(cut -f2 "$T/st/live-check-result" 2>/dev/null)" = fail ] \
  && case "$(cut -f5 "$T/st/live-check-result" 2>/dev/null)" in *STARVED*) true;; *) false;; esac \
  && ok "the result record names the finding, so a reader sees what the run found" || ko "record: '$(cat "$T/st/live-check-result" 2>&1)'"
# The same queue is no starvation inside the bound.
age "$T/caps/waiting/gates-run-1.json" 5
out=$(lc --once --bound 60 2>&1); rc=$?
[ $rc = 0 ] && case "$out" in *STARVED*) false;; *) true;; esac \
  && ok "a wait inside the bound is no starvation" || ko "in-bound wait: rc $rc, '$out'"
# A slot wait whose holder is not queued for the lock is no starvation: the lease names the lock's live holder instead.
age "$T/caps/waiting/gates-run-1.json" 1000
printf '{"holder":"gate-1","pid":%s,"since":%s}\n' "$h" "$lease_since" > "$T/caps/gates/gate-1.json"
out=$(lc --once --bound 60 2>&1); rc=$?
[ $rc = 0 ] && case "$out" in *STARVED*) false;; *) true;; esac \
  && ok "a slot wait whose holders are not queued for the lock is no starvation" || ko "unstarved wait: rc $rc, '$out'"
kill "$q" 2>/dev/null; wait "$q" 2>/dev/null

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

# 7) One live check per state directory: the lease (#1660). A start is a no-op while a live lease names a check, the check
# takes the lease before it measures and drops it on every exit, and a dead owner's lease is taken over. The fake reading
# holds for FAKE_HOLD seconds and logs one line per start; the door and the lock are the repository's own copies.
fake=$T/fake; mkdir -p "$fake/squad" "$fake/contrib/squad-goal/scripts"; : > "$fake/squad/__init__.py"
cp "$d/live-check.sh" "$d/serial.sh" "$d/caps.sh" "$fake/contrib/squad-goal/scripts/"
cat > "$fake/squad/livecheck.py" <<'PY'
import os, time
if __name__ == "__main__":
    with open(os.environ["FAKE_RUNS"], "a") as f: f.write("start %d %d\n" % (os.getpid(), os.getpgrp()))
    time.sleep(float(os.environ.get("FAKE_HOLD", "0.3")))
PY
fls=$fake/contrib/squad-goal/scripts/live-check.sh
cd "$fake"   # python -m puts the working directory first: a run from the repository would import the real reading
rl() { if [ -f "$1" ]; then wc -l < "$1" | tr -d ' '; else echo 0; fi; }
# settle <state> <runs file> <count>: at most 2 s for <count> starts and for the lease to be gone, then a short grace for a second start.
settle() { local i=0; while { [ "$(rl "$2")" -lt "$3" ] || [ -e "$1/live-check.lease" ]; } && [ $i -lt 40 ]; do sleep 0.05; i=$((i+1)); done; sleep 0.2; }
mkdir -p "$T/la"; : > "$T/ra"
# a) Concurrent triggers start one check: three starts at once, one reading.
for i in 1 2 3; do SG_STATE=$T/la FAKE_RUNS=$T/ra bash "$fls" --start >/dev/null 2>&1 & done; wait
settle "$T/la" "$T/ra" 1
[ "$(rl "$T/ra")" = 1 ] && ok "concurrent triggers start one check" || ko "concurrent triggers: $(rl "$T/ra") starts"
# b) A supervisor restart is a trigger too: a start while the check runs adds no second one.
: > "$T/ra"; SG_STATE=$T/la FAKE_RUNS=$T/ra bash "$fls" --start >/dev/null 2>&1 & first=$!
i=0; while [ "$(rl "$T/ra")" = 0 ] && [ $i -lt 40 ]; do sleep 0.02; i=$((i+1)); done
SG_STATE=$T/la FAKE_RUNS=$T/ra bash "$fls" --start >/dev/null 2>&1
wait "$first"; settle "$T/la" "$T/ra" 1
[ "$(rl "$T/ra")" = 1 ] && ok "a supervisor restart adds no second check" || ko "restart: $(rl "$T/ra") starts"
# c) A live lease makes the start a silent no-op that logs one line.
mkdir -p "$T/lc/live-check.lease"; sleep 300 >/dev/null 2>&1 & h=$!; pids+=("$h"); echo "$h" > "$T/lc/live-check.lease/pid"; : > "$T/rc"
SG_STATE=$T/lc FAKE_RUNS=$T/rc bash "$fls" --start >/dev/null 2>&1; sleep 0.3
[ "$(rl "$T/rc")" = 0 ] && [ "$(rl "$T/lc/live-check.log")" = 1 ] && [ -d "$T/lc/live-check.lease" ] \
  && ok "a live lease makes a start a no-op that logs one line" \
  || ko "live lease: $(rl "$T/rc") starts, $(rl "$T/lc/live-check.log") log lines"
# d) A dead owner's lease is taken over, and the check releases it when it ends.
mkdir -p "$T/ld/live-check.lease"; echo 99999999 > "$T/ld/live-check.lease/pid"; : > "$T/rd"
SG_STATE=$T/ld FAKE_RUNS=$T/rd bash "$fls" --start >/dev/null 2>&1
settle "$T/ld" "$T/rd" 1
[ "$(rl "$T/rd")" = 1 ] && [ ! -e "$T/ld/live-check.lease" ] \
  && ok "a dead owner's lease is taken over and released" || ko "dead owner: $(rl "$T/rd") starts, lease left: $(ls "$T/ld" 2>&1)"
# e) Two state directories run independently: a live lease in one never blocks the other.
mkdir -p "$T/le/live-check.lease"; sleep 300 >/dev/null 2>&1 & h2=$!; pids+=("$h2"); echo "$h2" > "$T/le/live-check.lease/pid"; : > "$T/re1"; : > "$T/re2"
mkdir -p "$T/lf"
SG_STATE=$T/le FAKE_RUNS=$T/re1 bash "$fls" --start >/dev/null 2>&1
SG_STATE=$T/lf FAKE_RUNS=$T/re2 bash "$fls" --start >/dev/null 2>&1
settle "$T/lf" "$T/re2" 1
[ "$(rl "$T/re1")" = 0 ] && [ "$(rl "$T/re2")" = 1 ] \
  && ok "two state directories run independently" || ko "two dirs: blocked $(rl "$T/re1"), free $(rl "$T/re2")"
# f) The next check starts after the previous one released.
: > "$T/rf"; SG_STATE=$T/lf FAKE_RUNS=$T/rf bash "$fls" --start >/dev/null 2>&1
settle "$T/lf" "$T/rf" 1
SG_STATE=$T/lf FAKE_RUNS=$T/rf bash "$fls" --start >/dev/null 2>&1
settle "$T/lf" "$T/rf" 2
[ "$(rl "$T/rf")" = 2 ] && ok "the next check starts after the previous released" || ko "next check: $(rl "$T/rf") starts"
# g) The nightly starts the check through the lease door, never by a bare detached start.
/usr/bin/grep -q 'live-check.sh" --start' "$d/nightly.sh" && ! /usr/bin/grep -q 'nohup bash "$here/live-check.sh"' "$d/nightly.sh" \
  && ok "the nightly starts the check through the lease door" || ko "nightly.sh starts the check without the lease door"

# h) A detached check outlives its case, so the suite ends it by pid (#1722). The check keeps the live default window (an
# hour, far past the guard's wait) and a hold of a minute: no timer of its own can end it inside this suite, so a green
# guard proves the suite ended it, never that the check ran out. This case is the suite's own evidence that end_checks acts.
: > "$T/rh"; mkdir -p "$T/lh"
FAKE_HOLD=60 SG_LIVE_CHECK_WINDOW_SECONDS=3600 SG_STATE=$T/lh FAKE_RUNS=$T/rh bash "$fls" --start >/dev/null 2>&1
i=0; while [ "$(rl "$T/rh")" -lt 1 ] && [ $i -lt 100 ]; do sleep 0.05; i=$((i+1)); done
sleep 0.5   # a short fixed wait, far below the window and the hold: a check left here leaks for its whole hour
left=$(left_of "$T/rh")
[ -n "$left" ] && ok "a detached check outlives its case" || ko "no detached check was alive to end: '$(cat "$T/rh" 2>&1)'"
end_checks
[ -z "$(left_of "$T/rh")" ] && ok "the suite ends a detached check by pid" || ko "the suite left a check alive: $(left_of "$T/rh")"

# i) No check the suite started is alive when it exits (#1722): a short fixed wait for one a case above left, then the guard
# names every pid or group still alive. The EXIT trap ends them, red or green.
i=0; while [ -n "$(left_checks)" ] && [ $i -lt 40 ]; do sleep 0.05; i=$((i+1)); done
alive_checks=$(left_checks)
[ -z "$alive_checks" ] && ok "no check the suite started is alive when it exits" || ko "the suite left a check alive: $alive_checks"
held="$T/la/live-check.lease $T/ld/live-check.lease $T/lf/live-check.lease $T/lh/live-check.lease"
left_leases=""; for f in $held; do [ -e "$f" ] && left_leases="$left_leases $f"; done
[ -z "$left_leases" ] && ok "the door of every check the suite started is released" || ko "a check the suite started still holds its door:$left_leases"

[ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
