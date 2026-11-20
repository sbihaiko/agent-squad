#!/bin/bash
# seconds: 27.75
# Regression test for nightly.sh (#704): the full suite in both modes, heavy included, once a day, in its own clone under its
# own serial lock, never under the landing (gate) lock; one atomic result record the hub reads, one alert on red, and a red
# record that holds dispatch until one green run. usage: bash test-nightly.sh [path/to/nightly.sh]
# A throwaway bare repository is the remote, the two modes are stub commands that log the tree they ran in, and the notifier is
# a stub: nothing here runs a real suite, no case touches the network, a model or the emulator.
# Hermetic first: the live loop exports its own SG_* to every run it dispatches, and a leaked SG_STATE would point this suite at
# the owner's state. Drop them all, as shell-suites.sh does for every suite (#1410).
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(dirname "$0"); src=${1:-$d/nightly.sh}; T=$(mktemp -d); S=$T/scripts; ST=$T/st; fail=0
mkdir -p "$S" "$ST"; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# The clock reports the window hour from FAKE_HOUR (04 by default) and the minute and second in it from FAKE_MS (00:00 by
# default), so a case runs in or out of the nightly's window, and far from its end, whatever the real time is. Every other
# date read passes through.
mkdir -p "$T/fakebin"
printf '#!/bin/bash\ncase "$*" in *+%%H*) echo "${FAKE_HOUR:-04}";; *+%%M:%%S*) echo "${FAKE_MS:-00:00}";; *) exec /bin/date "$@";; esac\n' > "$T/fakebin/date"; chmod +x "$T/fakebin/date"
export PATH="$T/fakebin:$PATH"
G="git -c user.email=a@b -c user.name=t"
cp "$d/serial.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/ghread.py" "$d/ghthrottle.py" "$d/live-check.sh" "$S/" 2>/dev/null
sq=$(cd "$d/../../.." && pwd)   # the repository the live check reads its module from
# The live check the nightly runs last (spec 1163, #1178) reads the live state and samples for an hour. In this suite it
# samples once and reads the fixture's folders, so no case waits an hour and no case reads the owner's live state.
export SG_SQUAD="$sq" SQUAD_CAPS_DIR="$T/caps" SG_LIVE_CHECK_WINDOW_SECONDS=0
[ -f "$src" ] && cp "$src" "$S/nightly.sh"
# The remote: one bare repository with one commit on dev. The nightly clones it into its own tree and runs there.
git init -q -b dev "$T/src"; $G -C "$T/src" commit -q --allow-empty -m one
git init -q --bare "$T/origin.git"; $G -C "$T/src" push -q "$T/origin.git" dev; sha=$($G -C "$T/src" rev-parse dev)
# Each mode stub logs the tree it ran in, names a failing test when its own FAIL_<mode> is set, names a file it could not
# collect when its own COLLECT_<mode> is set, and exits red there. KILL_<mode> ends it by SIGTERM with no test line, as a
# suite the machine killed does (#1183); KILLFAIL_<mode> names one failing test and then dies, so the red and the stop
# reason are both on the record. Each stub prints pytest's summary line, the line a finished mode writes (#1201).
for m in parallel serial; do
  cat > "$T/$m.sh" <<EOF
#!/bin/bash
echo "$m \$PWD" >> "$T/ran.log"
[ -n "\${SLEEP_$m:-}" ] && sleep "\${SLEEP_$m}"
[ "\${KILL_$m:-}" = 1 ] && exit 143
[ "\${KILLFAIL_$m:-}" = 1 ] && { printf 'FAILED tests/test_x.py::test_a - boom\n1 failed, 10 passed in 1.00s\n'; exit 143; }
[ "\${FAIL_$m:-}" = 1 ] && { printf 'FAILED tests/test_x.py::test_a - boom\nFAILED tests/test_x.py::test_x[case with space] - boom\nFAILED tests/test_y.py::test_b - boom\n3 failed, 10 passed in 1.00s\n'; exit 1; }
[ "\${COLLECT_$m:-}" = 1 ] && { printf 'ERROR tests/test_module.py - ImportError: no module named x\n1 error in 0.10s\n'; exit 1; }
printf '4270 passed, 25 skipped in 12.34s\n'; exit 0
EOF
  chmod +x "$T/$m.sh"; done
printf '#!/bin/bash\necho "$*" >> "%s/notify.log"\n' "$T" > "$T/notify.sh"; chmod +x "$T/notify.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
export T SG_GHREAD=false   # no case reads GitHub: the verdict cases name their own reader (#1975)
# ng [VAR=val...]: one nightly run against the fixture, its own state, tree and remote
ng() { env "$@" SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_OWNER=o SG_PROJECT=4 BOARD="$T/board.sh" SG_NIGHTLY_REMOTE="$T/origin.git" SG_NIGHTLY_TREE="$T/tree" \
  SG_NIGHTLY_PARALLEL="$T/parallel.sh" SG_NIGHTLY_SERIAL="$T/serial.sh" HEALTH_NOTIFY="$T/notify.sh" bash "$S/nightly.sh" 2>&1; }
rec() { r_ts= r_res= r_sha= r_par= r_ser= r_ids=
  [ -f "$ST/nightly-result" ] && IFS=$'\t' read -r r_ts r_res r_sha r_par r_ser r_ids < "$ST/nightly-result"; }
hold() { ( export SG_STATE="$ST" SG_CHECKOUT="$T/src"; . "$S/ghcall.sh"; gh_dispatch_hold ); }   # the dispatch hold both dispatch passes read (ghcall.sh)
mark() { rm -f "$ST/auto/nightly-$(date +%F)"; }
# The live check starts detached, so it may still sample when ng returns. lc_wait waits for its record and the release of its
# own lock, so a case that reads the result never reads a stale or half-written state.
lc_wait() { local i=0; while [ $i -lt 400 ] && { [ ! -e "$ST/live-check-result" ] || [ -d "$ST/locks/live-check" ]; }; do sleep 0.05; i=$((i+1)); done; }
# The live check is detached, so the suite owns its end (#1722): the check's serial lock and lease name its pid, and the suite
# ends it and the reading it leads, by process group, before it exits, a red run included. The guard at the end fails when
# one is still alive.
ckpid() { local f p; for f in "$ST/locks/live-check/pid" "$ST/live-check.lease/pid"; do
    [ -f "$f" ] && { p=$(cat "$f" 2>/dev/null); case $p in ''|*[!0-9]*) ;; *) printf '%s\n' "$p";; esac; }; done; }
# A pid that is alive and not a zombie: a killed check the suite's own subshell has not reaped yet is gone. Linux's /proc
# names the state; where it is absent a live pid is the reading.
alive_pid() { kill -0 "$1" 2>/dev/null || return 1
  if [ -r "/proc/$1/stat" ]; then [ "$(cut -d' ' -f3 "/proc/$1/stat")" = Z ] && return 1; fi; return 0; }
# end_one signals the pid's group first: a door's own pid leads the group its reading runs in, so the group kill reaches the
# `squad.livecheck` child too. A pid that leads no group (the serial holder) gets the TERM its own trap answers by stopping
# the group. No ps is needed to tell them apart: a group kill that finds no group falls back to the pid.
end_one() { kill -TERM -- "-$1" 2>/dev/null || kill -TERM "$1" 2>/dev/null; }
end_check() { local p pass i
  for pass in 1 2 3 4; do
    for p in $(ckpid); do alive_pid "$p" && end_one "$p"; done
    i=0; while [ -n "$(ckpid)" ] && [ $i -lt 20 ]; do sleep 0.05; i=$((i+1)); done
    [ -z "$(ckpid)" ] && return 0
    sleep 0.2   # re-scan after a short settle: a check that took its lease while the scan above ran
  done
  for p in $(ckpid); do kill -KILL -- "-$p" 2>/dev/null || kill -KILL "$p" 2>/dev/null; done; }
trap end_check EXIT
# json_ids <field>: 0 when the field is ONE JSON array of strings, the one encoding both readers parse (#704)
json_ids() { printf '%s' "$1" | python3 -c 'import json,sys
try: v=json.load(sys.stdin)
except Exception: sys.exit(1)
sys.exit(0 if type(v) is list and all(type(x) is str for x in v) else 1)'; }

# 1) both modes run once, in the nightly tree, and the record says pass with the sha and both modes
rm -f "$ST/live-check-result"
out=$(ng); rc=$?
[ $rc = 0 ] && ok "a green nightly exits 0" || ko "green nightly rc $rc: $out"
[ "$(cat "$T/ran.log" 2>/dev/null)" = "parallel $T/tree
serial $T/tree" ] && ok "both modes run in the nightly tree, parallel then serial" || ko "ran: '$(cat "$T/ran.log" 2>&1)'"
rec
[ "$r_res $r_sha $r_par $r_ser $r_ids" = "pass $sha pass pass []" ] && ok "the record is pass, the tip sha, both modes green and no failing test" || ko "record: '$r_res $r_sha $r_par $r_ser $r_ids'"
[ -e "$ST/auto/nightly-$(date +%F)" ] && ok "the run leaves its once-a-day marker" || ko "no day marker after a run"
[ ! -d "$ST/locks/nightly" ] && ok "the nightly lock is released when the run ends" || ko "the nightly lock stayed behind"
lc_wait   # the check is detached: wait for its own record before reading it
[ ! -e "$ST/alerts.log" ] && ok "a green nightly raises no alert" || ko "a green nightly alerted: $(cat "$ST/alerts.log")"
# The live check (spec 1163, #1178): the night ends by reading the live state, and its own record is what it leaves.
[ "$(cut -f2 "$ST/live-check-result" 2>/dev/null)" = pass ] \
  && ok "the nightly runs the live check, and a clean state passes it" || ko "live check: '$(cat "$ST/live-check-result" 2>&1)'"
[ ! -e "$ST/alerts.log" ] && ok "a clean live check raises no alert of its own" || ko "the live check alerted on a clean state: $(cat "$ST/alerts.log")"

# 2) a red parallel mode fails the record, names its failing tests, and still runs the serial mode: the record carries both
rm -f "$T/ran.log" "$ST/alerts.log"; mark
out=$(ng FAIL_parallel=1); rc=$?
[ $rc = 1 ] && ok "a red nightly exits 1" || ko "red nightly rc $rc: $out"
rec
[ "$r_res $r_par $r_ser" = "fail fail pass" ] && ok "the record says fail, and which mode failed" || ko "record: '$r_res $r_par $r_ser'"
[ "$r_ids" = '["tests/test_x.py::test_a","tests/test_x.py::test_x[case with space]","tests/test_y.py::test_b"]' ] && ok "the record names every failing test whole, a parametrized id with a space included" || ko "ids: '$r_ids'"
json_ids "$r_ids" && ok "the ids field is one JSON array, so no reader splits an id at a space" || ko "ids field is not one JSON array of strings: '$r_ids'"
/usr/bin/grep -qx "serial $T/tree" "$T/ran.log" && ok "the serial mode still runs after a red parallel one" || ko "ran: '$(cat "$T/ran.log" 2>&1)'"

# 3) a red result raises one alert through the existing alert path: alerts.log and the notifier
/usr/bin/grep -q "NIGHTLY" "$ST/alerts.log" 2>/dev/null && ok "a red nightly writes one alert line" || ko "no NIGHTLY line in alerts.log: '$(cat "$ST/alerts.log" 2>&1)'"
/usr/bin/grep -q "${sha:0:7}" "$ST/alerts.log" 2>/dev/null && ok "the alert names the sha it ran at" || ko "the alert names no sha: '$(cat "$ST/alerts.log" 2>&1)'"
[ "$(/usr/bin/grep -c . "$T/notify.log" 2>/dev/null)" = 1 ] && ok "the notifier is called once" || ko "notify calls: '$(cat "$T/notify.log" 2>&1)'"

# 4) (owner, 2026-11-05; ADR-0153) a red record is a report, never a hold: the pass goes on, one note names the test it
#    measured, and it is said once per record rather than on every pass.
o=$(hold); rc=$?
[ $rc = 1 ] && ok "a red nightly holds no dispatch: it is a report" || ko "a red nightly held dispatch: rc $rc, '$o'"
case "$o" in *nightly*) ok "the note names the nightly as its cause";; *) ko "the note names no nightly: '$o'";; esac
case "$o" in *'tests/test_x.py::test_x[case with space]'*) ok "the note names a failing test whole, its space included";; *) ko "the note split a parametrized id: '$o'";; esac
o2=$(hold); [ -z "$o2" ] && ok "the red note is said once per record, not on every pass" || ko "the red note repeated: '$o2'"
mark; out=$(ng)                                     # one green run
hold >/dev/null; [ $? = 1 ] && ok "one green nightly releases delivery" || ko "a green nightly did not release: $(hold)"
# 4b (#747) a red record holds delivery while it names the dev tip. A tip the nightly has not seen is unproven, not red: the
#    pass says so in one line naming both shas and delivers, and the next nightly proves the new tip. A sha the checkout does
#    not carry holds nothing.
$G -C "$T/src" commit -q --allow-empty -m two; new=$($G -C "$T/src" rev-parse dev)
printf '%s\tfail\t%s\tfail\tpass\t["tests/test_x.py::test_a"]\n' "$(date +%s)" "$sha" > "$ST/nightly-result"
o=$(hold); rc=$?
[ $rc = 1 ] && ok "a red record at another sha holds nothing (#747)" || ko "a stale red held dispatch: rc $rc, '$o'"
case "$o" in *"$sha"*"$new"*) ok "the pass names the record's sha and the tip, so the wait is legible";; *) ko "the note names not both: '$o'";; esac
$G -C "$T/src" reset -q --hard "$sha"
printf '%s\tfail\t%s\tfail\tpass\t["tests/test_x.py::test_a"]\n' "$(date +%s)" "0000000000000000000000000000000000000000" > "$ST/nightly-result"
hold >/dev/null; [ $? = 1 ] && ok "a sha the checkout does not carry holds nothing" || ko "a ghost sha held dispatch"
rm -f "$ST/nightly-result"

# 4c) (#1183) a leg killed by a signal names no failing test: it measured nothing, so the record says bounded with the
#     signal it died of, never a red the suite found (ADR-0164). The two facts stay apart -- the serial leg finished, the
#     parallel one was stopped -- the hold never stops delivery on it.
rm -f "$T/ran.log" "$ST/alerts.log" "$ST/nightly-result" "$ST/nightly-bounded-noted"; mark
out=$(ng KILL_parallel=1); rc=$?
rec
[ $rc = 1 ] && ok "a nightly with a killed leg exits 1" || ko "killed nightly rc $rc: $out"
[ "$r_res $r_par $r_ser $r_ids" = "bounded bounded:signal-15 pass []" ] \
  && ok "a leg killed with no failing test is bounded, carrying its signal, while the other finished" \
  || ko "killed record: '$r_res $r_par $r_ser $r_ids'"
/usr/bin/grep -qx "serial $T/tree" "$T/ran.log" && ok "the finished leg still runs after a killed one" || ko "ran: '$(cat "$T/ran.log" 2>&1)'"
o=$(hold); rc=$?
[ $rc = 1 ] && ok "a bounded leg never holds delivery" || ko "a bounded leg held dispatch: rc $rc, '$o'"
case "$o" in *bounded*) ok "the dispatch note names the stopped leg, so the reader tells it from a red";; *) ko "the dispatch note does not name the stopped leg: '$o'";; esac
o2=$(hold); [ -z "$o2" ] && ok "the bounded note is said once for the record, not on every pass" || ko "the bounded note repeated: '$o2'"

# 4c2) (#2005) A pass record writes no stopped note: only a record whose suite was stopped before it measured anything says
#      the suite was stopped. The reader answers a pass like no reading -- nothing to say -- so the pass goes on in silence,
#      and the bounded record above still names its stopped leg.
rm -f "$ST/nightly-result" "$ST/nightly-bounded-noted" "$ST/nightly-red-noted"
printf '%s\tpass\t%s\tpass\tpass\t[]\n' "$(date +%s)" "$sha" > "$ST/nightly-result"
o=$(hold); rc=$?
[ $rc = 1 ] && ok "a pass record holds no dispatch" || ko "a pass record held dispatch: rc $rc, '$o'"
case "$o" in *"stopped before it measured"*) ko "a pass record wrote the stopped note: '$o'";; *) ok "a pass record in both modes writes no stopped note";; esac
printf '%s\tbounded\t%s\tbounded:signal-15\tpass\t[]\n' "$(date +%s)" "$sha" > "$ST/nightly-result"
o=$(hold); rc=$?
[ $rc = 1 ] && case "$o" in *"stopped before it measured"*) ok "a stopped record still writes the stopped note";; *) ko "a stopped record wrote no stopped note: '$o'";; esac \
  || ko "a stopped record held dispatch: rc $rc, '$o'"
rm -f "$ST/nightly-result"

# 4d) (#1183) A leg that named a failing test and was then killed is still red, unchanged: its ids are carried, the alert
#     fires and delivery holds. Its mode also names the signal that stopped it, so the record keeps the stop reason.
rm -f "$ST/alerts.log" "$ST/nightly-result"; mark
out=$(ng KILLFAIL_parallel=1); rc=$?
rec
[ $rc = 1 ] && ok "a red leg killed by a signal still exits 1" || ko "red killed nightly rc $rc: $out"
[ "$r_res $r_par $r_ser $r_ids" = 'fail fail:signal-15 pass ["tests/test_x.py::test_a"]' ] \
  && ok "a leg that names a failing test stays red and its mode names the signal that also stopped it" \
  || ko "red killed record: '$r_res $r_par $r_ser $r_ids'"
/usr/bin/grep -q "NIGHTLY" "$ST/alerts.log" 2>/dev/null && ok "a red leg that was also stopped still alerts" \
  || ko "red killed leg raised no alert: '$(cat "$ST/alerts.log" 2>&1)'"
o=$(hold); rc=$?
[ $rc = 1 ] && case "$o" in *nightly*red*) true;; *) false;; esac \
  && ok "a red leg is a report, even when a signal also stopped it" \
  || ko "a red killed leg held dispatch or said nothing: rc $rc, '$o'"
rm -f "$T/ran.log"

# 4e) (#2004) The note is said once per record, never once per second. Two red records that share a sha and land in the
#     same second of the same day are still two records, and the second one names the tip that moved. A note keyed on the
#     sha and the record's mtime in whole seconds reads the second as already said and prints nothing, so the pass that
#     should name both shas goes silent about one run in four.
rm -f "$ST/nightly-result" "$ST/nightly-red-noted" "$ST/nightly-bounded-noted"
$G -C "$T/src" reset -q --hard "$sha"
printf '%s\tfail\t%s\tfail\tpass\t["tests/test_x.py::test_a"]\n' "$(date +%s)" "$sha" > "$ST/nightly-result"
same_mt=$(date -r "$ST/nightly-result" +%Y%m%d%H%M.%S)   # the whole second both records carry
o=$(hold)
case "$o" in *nightly*) ok "the first of two same-second records says its note";; *) ko "the first same-second record said no note: '$o'";; esac
$G -C "$T/src" commit -q --allow-empty -m moved; new2=$($G -C "$T/src" rev-parse dev)
printf '%s\tfail\t%s\tfail\tpass\t["tests/test_x.py::test_b"]\n' "$(date +%s)" "$sha" > "$ST/nightly-result"
touch -t "$same_mt" "$ST/nightly-result"              # the second record lands in the first one's second
o=$(hold); rc=$?
[ $rc = 1 ] && ok "a second same-second record holds no dispatch" || ko "the second same-second record held dispatch: rc $rc, '$o'"
case "$o" in *"$sha"*"$new2"*) ok "a second same-second record still names its sha and the moved tip (#2004)";; *) ko "the second same-second note names not both: '$o'";; esac
$G -C "$T/src" reset -q --hard "$sha"; rm -f "$ST/nightly-result"

# 5) (#1527, ADR-0153) a held gate lock holds the nightly, which never takes the landing lock: it runs no mode and writes no day
#    marker, and it is left as it was. Once the gate is free the nightly runs both modes, as before.
mkdir -p "$ST/locks/gate"; echo $$ > "$ST/locks/gate/pid"; rm -f "$T/ran.log"; mark
out=$(ng); rc=$?
[ $rc = 0 ] && [ ! -s "$T/ran.log" ] && [ ! -e "$ST/auto/nightly-$(date +%F)" ] && ok "a held landing lock holds the nightly: no mode runs and no day marker is written" || ko "gate lock rc $rc: $out"
[ -e "$ST/locks/gate/pid" ] && ok "the nightly never took the landing lock" || ko "the nightly removed the landing lock"
rm -rf "$ST/locks/gate"; rm -f "$T/ran.log"
out=$(ng); rc=$?
[ $rc = 0 ] && [ "$(/usr/bin/grep -c . "$T/ran.log")" = 2 ] && ok "a released gate lock lets the nightly run both modes" || ko "released gate lock rc $rc: $out"

# 6) once a day: a second start the same day runs nothing, and a later day runs again
rm -f "$T/ran.log"
out=$(ng); rc=$?
[ $rc = 0 ] && [ ! -s "$T/ran.log" ] && ok "a second start the same day runs no mode" || ko "second start rc $rc ran: '$(cat "$T/ran.log" 2>&1)'"
mark; out=$(ng); rc=$?
[ $rc = 0 ] && [ "$(/usr/bin/grep -c . "$T/ran.log")" = 2 ] && ok "the next day runs both modes again" || ko "next day rc $rc: '$(cat "$T/ran.log" 2>&1)'"

# 6b) two facts refuse the start without writing the day marker, so the nightly still runs once they clear: a profile that
#     switches it off (ADR-0153), and a promotion proof measuring the tip in its own tree. Two full suites saturate the box
#     before either shows in the load average, which is what the proof's own guard reads the other way round.
mark; rm -f "$T/ran.log"
SG_NIGHTLY_OFF=1 ng >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && [ ! -s "$T/ran.log" ] && [ ! -e "$ST/auto/nightly-$(date +%F)" ] && ok "a profile that switches the nightly off runs no mode and leaves no day marker" || ko "SG_NIGHTLY_OFF: rc $rc ran '$(cat "$T/ran.log" 2>&1)' marker $([ -e "$ST/auto/nightly-$(date +%F)" ] && echo written)"
printf '%s 1\n' "$(date +%s)" > "$ST/promote-reserve"; ng >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && [ ! -s "$T/ran.log" ] && [ ! -e "$ST/auto/nightly-$(date +%F)" ] && ok "a promotion proof in flight holds the nightly, and no day marker is spent" || ko "promote-reserve: rc $rc ran '$(cat "$T/ran.log" 2>&1)'"
rm -f "$ST/promote-reserve"; ng >/dev/null 2>&1
[ "$(/usr/bin/grep -c . "$T/ran.log")" = 2 ] && ok "the nightly runs once the proof is over" || ko "after the proof: '$(cat "$T/ran.log" 2>&1)'"

# 7) a record the loop cannot read never holds dispatch, and neither does a line that only LOOKS red: the hold reads the
#    whole six-field record, never its second field alone (#704, ADR-0153)
bad_rec() { printf "$1" > "$ST/nightly-result"; hold >/dev/null; [ $? = 1 ]; }
bad_rec 'not a result\n' && ok "a record the loop cannot read never holds dispatch" || ko "an unreadable record held dispatch"
bad_rec 'garbage\tfail\n' && ok "a two-field line that says fail never holds dispatch" || ko "a two-field fail line held dispatch"
bad_rec "$(date +%s)\tfail\t$sha\tpass\tmaybe\t-\n" && ok "a mode outside pass, fail or bounded never holds dispatch" || ko "an invalid mode held dispatch"
bad_rec "$(date +%s)\tbounded\t$sha\tpass\tbounded:15\t-\n" && ok "a bounded reason that is neither a signal nor an exit never holds dispatch" || ko "a malformed bounded reason held dispatch"
bad_rec "$(date +%s)\tbounded\t$sha\tpass\tfail\t-\n" && ok "a bounded result over a red leg never holds dispatch" || ko "a bounded result over a red leg held dispatch"
bad_rec "$(date +%s)\tbounded\t$sha\tpass\tpass\t-\n" && ok "a bounded result over two finished legs never holds dispatch" || ko "a bounded result over two green legs held dispatch"
bad_rec "$(date +%s)\tfail\t$sha\tpass\tfail:bogus-15\t-\n" && ok "a red mode with a stop reason that is not a signal never holds dispatch" || ko "a malformed red stop reason held dispatch"
bad_rec "$(date +%s)\tbounded\t$sha\tpass\tbounded:signal-15\t[\"tests/test_x.py::test_a\"]\n" && ok "a bounded record that names a failing test never holds dispatch" || ko "a bounded record with ids held dispatch"
bad_rec "$(date +%s)\tfail\t$sha\tpass\tfail\n" && ok "a record short a field never holds dispatch" || ko "a five-field fail record held dispatch"
bad_rec "$(date +%s)\tpass\t$sha\tfail\tpass\t-\n" && ok "a result that contradicts its modes never holds dispatch" || ko "a contradictory record held dispatch"
bad_rec "nope\tfail\t$sha\tpass\tfail\t-\n" && ok "a timestamp that is not a number never holds dispatch" || ko "a non-numeric time held dispatch"
bad_rec "$(date +%s)\tfail\t\tpass\tfail\t-\n" && ok "a record with no sha never holds dispatch" || ko "an empty sha held dispatch"
#    The hold reads the whole file, never its first line alone: a valid red line with anything after it is a record the loop
#    cannot trust, and such a record holds nothing (#704, ADR-0153).
bad_rec "$(date +%s)\tfail\t$sha\tpass\tfail\ttests/test_x.py::test_a\ncorrupt\n" && ok "a valid red line followed by a corrupt second line never holds dispatch" || ko "a red line over a corrupt second line held dispatch"
bad_rec "$(date +%s)\tfail\t$sha\tpass\tfail\ttests/test_x.py::test_a\n"$'\n' && ok "a red line followed by an empty line never holds dispatch" || ko "a red line with a trailing empty line held dispatch"
rm -f "$ST/nightly-result"
hold >/dev/null; [ $? = 1 ] && ok "no record at all never holds dispatch" || ko "a missing record held dispatch"

# 8) a collection error names the file it could not collect: `ERROR tests/test_module.py` carries no `::`, and it is
#    still a failing id the hub must show (#704, ADR-0153)
rm -f "$T/ran.log" "$ST/alerts.log"; mark
out=$(ng COLLECT_parallel=1); rc=$?
rec
[ "$r_res $r_ids" = 'fail ["tests/test_module.py"]' ] && ok "a collection error names the file it could not collect" || ko "record: '$r_res $r_ids' (rc $rc: $out)"

# 9) (#758) a repository with no pytest suite: the default command collects nothing and pytest exits 5. No suite is not red,
#    so the record says pass and no dispatch is held. A configured command that exits 5 and names no test measured nothing:
#    it is bounded with its exit, never red (#1183).
printf '#!/bin/bash\n[ "$1" = -c ] && exec python3 "$@"\necho "no tests ran"; exit 5\n' > "$T/py"; chmod +x "$T/py"
rm -f "$ST/alerts.log" "$ST/nightly-result"; mark
out=$(env -u SG_NIGHTLY_PARALLEL -u SG_NIGHTLY_SERIAL SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" \
  SG_NIGHTLY_TREE="$T/tree" SG_GATE_PY="$T/py" HEALTH_NOTIFY="$T/notify.sh" bash "$S/nightly.sh" 2>&1); rc=$?
rec
[ "$rc $r_res $r_par $r_ser" = "0 pass pass pass" ] && ok "a default pytest that collects nothing is no suite, not red (#758)" || ko "no-suite record: rc $rc '$r_res $r_par $r_ser' $out"
[ ! -e "$ST/alerts.log" ] && ok "no suite raises no alert" || ko "no suite alerted: $(cat "$ST/alerts.log")"
printf '#!/bin/bash\nexit 5\n' > "$T/five.sh"; chmod +x "$T/five.sh"; mark; rm -f "$ST/nightly-result"
out=$(env SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" SG_NIGHTLY_TREE="$T/tree" \
  SG_NIGHTLY_PARALLEL="$T/five.sh" SG_NIGHTLY_SERIAL="$T/serial.sh" HEALTH_NOTIFY="$T/notify.sh" bash "$S/nightly.sh" 2>&1); rc=$?
rec
[ "$rc $r_res $r_par" = "1 bounded bounded:exit-5" ] \
  && ok "a configured command that collects nothing measured nothing: bounded with its exit, never red (#1183)" \
  || ko "configured exit 5: rc $rc record '$r_res $r_par' (out: $out)"


# --- proof obligations (spec 1163, ADR-0153): the nightly settles what a gate could not prove. It reads each record in
#     $SG_STATE/obligations, runs the named checks over the named tree, and writes one settlement line in
#     $SG_STATE/obligation-settled. The stub checks log the tree and its HEAD, so credit and tree are read from the run.
# Dev moves to a new tip, and the obligation cases below run the nightly there.
$G -C "$T/src" commit -q --allow-empty -m two; $G -C "$T/src" push -q "$T/origin.git" dev; newtip=$($G -C "$T/src" rev-parse dev)
OBL=$ST/obligations; SET=$ST/obligation-settled
obkey() { printf '%s\t%s\t%s' "$1" "$2" "$3" | cksum | cut -d' ' -f1; }   # the gate's own key for one obligation entry
obwrite() {  # <repository> <sha> <base> <checks> <scope> <deadline>: the gate's own key for one obligation entry
  mkdir -p "$OBL"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" > "$OBL/$(obkey "$1" "$2" "$3")"
}
proofmap() { mkdir -p "$ST/proof-card/$1"; : > "$ST/proof-card/$1/$2"; }   # wave.sh's card mapping: issue, obligation key
cat > "$T/settle.sh" <<EOF
#!/bin/bash
echo "\$1 \$PWD \$(git rev-parse HEAD 2>/dev/null)" >> "$T/settle.log"
case " \${FAIL_SETTLE:-} " in *" \$1 "*) exit 1;; esac
exit 0
EOF
chmod +x "$T/settle.sh"
obclean() { rm -rf "$OBL" "$SET"; rm -f "$T/settle.log" "$ST/alerts.log" "$ST/nightly-result"; mark; }
sf() { [ -d "$SET" ] || return 1; /bin/ls "$SET"/* 2>/dev/null | head -n 1; }

# 11) a green settlement clears the obligation and names the run; it credits the tree and the scope it measured, and the
#     full suite the run already made answers pytest at the tip, so no second suite runs there.
obclean
obwrite o/r "$newtip" "-" "mypy pytest" delta "$(( $(date +%s) + 900 ))"
out=$(ng "SG_SETTLE_MYPY=$T/settle.sh mypy" "SG_SETTLE_PYTEST=$T/settle.sh pytest"); rc=$?; rec
f=$(sf); [ -n "$f" ] || f=/dev/null; IFS=$'\t' read -r s_ts s_repo s_sha s_base s_scope s_checks s_res s_run < "$f" 2>/dev/null
[ "$rc" = 0 ] && ok "a green settlement lets the nightly exit 0" || ko "green settlement rc $rc: $out"
[ -z "$(/bin/ls -A "$OBL" 2>/dev/null)" ] && ok "a green settlement clears the obligation" || ko "obligation left: $(/bin/ls "$OBL" 2>&1)"
[ "$s_res" = green ] && ok "the settlement is green" || ko "settlement: '$(cat "$f" 2>&1)'"
[ "$s_run" = "$(cut -f1 "$ST/nightly-result")@$newtip" ] && ok "the settlement names the run it was measured in" \
  || ko "run: '$s_run' vs '$(cut -f1 "$ST/nightly-result")@$newtip'"
[ "$s_sha $s_repo $s_scope" = "$newtip o/r full" ] && ok "the settlement credits the tree and the scope it measured" || ko "credit: '$s_sha $s_repo $s_scope'"
/usr/bin/grep -qx "mypy $T/tree $newtip" "$T/settle.log" && ok "the named check ran over the tree the obligation names" || ko "settle log: '$(cat "$T/settle.log" 2>&1)'"
/usr/bin/grep -q "^pytest " "$T/settle.log" 2>/dev/null && ko "pytest ran again at the tip the suite already measured" || ok "the suite already made answers pytest at the measured tip"

# 12) a red settlement holds the tip like any red: the record names the checks that failed, the obligation is answered,
#     the run alerts, and the dispatch hold reads the settlement.
obclean
obwrite o/r "$newtip" "-" "ruff" delta "$(( $(date +%s) + 900 ))"
out=$(ng "SG_SETTLE_RUFF=$T/settle.sh ruff" FAIL_SETTLE=ruff); rc=$?; rec
f=$(sf); [ -n "$f" ] || f=/dev/null; IFS=$'\t' read -r _ _ _ _ _ s_checks s_res s_run < "$f" 2>/dev/null
[ "$rc" = 1 ] && ok "a red settlement fails the nightly" || ko "red settlement rc $rc: $out"
[ "$s_res $s_checks" = "red ruff" ] && ok "the settlement is red and names the check that failed" || ko "settlement: '$(cat "$f" 2>&1)'"
[ -z "$(/bin/ls -A "$OBL" 2>/dev/null)" ] && ok "a red settlement answers the obligation" || ko "obligation left: $(/bin/ls "$OBL" 2>&1)"
/usr/bin/grep -q "OBLIGATION settled-red" "$ST/alerts.log" 2>/dev/null && ok "a red settlement alerts" || ko "alerts: '$(cat "$ST/alerts.log" 2>&1)'"
o=$(hold); rc=$?
if [ "$rc" = 0 ] && case "$o" in *"${newtip:0:7}"*) true;; *) false;; esac; then
  ok "the dispatch hold stops a run on the tip a red settlement measured"
else ko "hold rc $rc: '$o'"; fi
case "$o" in *obligation*) ok "the hold names the obligation as its cause";; *) ko "hold cause: '$o'";; esac
rm -rf "$SET"; hold >/dev/null; [ $? = 1 ] && ok "clearing the settled-red record releases the hold" || ko "the hold outlived the record"

# 13) a settlement is credited to the tree it measured, never another: an obligation at an older sha is measured and
#     settled there, the nightly record still names the tip, and the red holds no tip it did not measure.
obclean
obwrite o/r "$sha" "-" "ruff" delta "$(( $(date +%s) + 900 ))"
out=$(ng "SG_SETTLE_RUFF=$T/settle.sh ruff" FAIL_SETTLE=ruff); rc=$?; rec
f=$(sf); [ -n "$f" ] || f=/dev/null; IFS=$'\t' read -r _ _ s_sha s_base s_scope _ s_res _ < "$f" 2>/dev/null
[ "$s_sha $s_scope $s_res" = "$sha full red" ] && [ "$r_sha" = "$newtip" ] \
  && ok "the red credits the settled tree, and the nightly record still names the tip" \
  || ko "credit: settled '$s_sha/$s_scope/$s_res', record '$r_sha', want '$sha/full/red' and '$newtip'"
/usr/bin/grep -qx "ruff $T/tree $sha" "$T/settle.log" && ok "the check ran over the tree the obligation names, not the tip" \
  || ko "settle log: '$(cat "$T/settle.log" 2>&1)'"
hold >/dev/null; [ $? = 1 ] && ok "a red at another tree holds no tip" || ko "a red at another tree held the tip"

# 14) an obligation past its deadline is reported by name, never dropped: the alert names its tree and checks, and the run
#     still measures it, because a nightly is always past the gate's fifteen-minute bound (ADR-0163).
obclean
obwrite o/r "$newtip" "-" "mypy" delta "$(( $(date +%s) - 60 ))"
out=$(ng "SG_SETTLE_MYPY=$T/settle.sh mypy"); rc=$?
a=$(cat "$ST/alerts.log" 2>/dev/null)
case $a in *OBLIGATION*overdue*"${newtip:0:7}"*mypy*) ok "an overdue obligation is reported by name, with its tree and checks";; *) ko "overdue report: '$a'";; esac
/usr/bin/grep -qx "mypy $T/tree $newtip" "$T/settle.log" && ok "an overdue obligation is still measured, never dropped" \
  || ko "settle log: '$(cat "$T/settle.log" 2>&1)'"

# 15) a settlement never turns a red into a green: the full suite is red at the measured tip, so an obligation whose named
#     check passes still settles red.
obclean
obwrite o/r "$newtip" "-" "ruff" delta "$(( $(date +%s) + 900 ))"
out=$(ng "SG_SETTLE_RUFF=$T/settle.sh ruff" FAIL_parallel=1); rc=$?; rec
f=$(sf); [ -n "$f" ] || f=/dev/null; IFS=$'\t' read -r _ _ _ _ _ _ s_res _ < "$f" 2>/dev/null
[ "$r_res $s_res" = "fail red" ] && ok "a red suite is never settled green by a passing named check" \
  || ko "record '$r_res', settlement '$s_res': $(cat "$f" 2>&1)"
rm -rf "$OBL" "$SET"

# 16) a settlement never turns a red into a green: the same entry is settled red, then a later nightly measures a passing
#     check at the same tree and base. The red stands, the run fails, and the tip stays held (ADR-0153, the same rule as its
#     own record: one green run replaces it only through the full suite, never through a narrower check).
obclean
obwrite o/r "$newtip" "-" "ruff" delta "$(( $(date +%s) + 900 ))"
out=$(ng "SG_SETTLE_RUFF=$T/settle.sh ruff" FAIL_SETTLE=ruff); rc=$?
k=$(basename "$(sf)")
obwrite o/r "$newtip" "-" "ruff" delta "$(( $(date +%s) + 900 ))"; mark
out=$(ng "SG_SETTLE_RUFF=$T/settle.sh ruff"); rc=$?
IFS=$'\t' read -r _ _ _ _ _ _ s_res _ < "$SET/$k" 2>/dev/null
[ "$rc" = 1 ] && [ "$s_res" = red ] && ok "a later passing measurement never turns a settled red green" \
  || ko "re-settle rc $rc, settlement '$s_res': $(cat "$SET/$k" 2>&1)"
hold >/dev/null; [ $? = 0 ] && ok "the red still holds the tip after the passing re-measurement" || ko "a later green released the red"

# 17) the nightly result is written before the first obligation is measured: a settlement check that hangs is stopped at the
#     serial hold, and the record the hub and the dispatch hold read is already there (ADR-0153). The record on main was
#     written as soon as both modes finished, and a settlement never delays it.
obclean
printf '#!/bin/bash\nsleep 60\n' > "$T/hang.sh"; chmod +x "$T/hang.sh"
obwrite o/r "$newtip" "-" "ruff" delta "$(( $(date +%s) + 900 ))"
out=$(ng SERIAL_HOLD_SECONDS=6 "SG_SETTLE_RUFF=$T/hang.sh ruff"); rc=$?
rec
[ "$r_res $r_sha" = "pass $newtip" ] && ok "the nightly result is written before the first settlement runs" \
  || ko "record after a hung settlement: '$r_res $r_sha' (rc $rc: $(printf '%s' "$out" | tail -n 1))"
[ -n "$(/bin/ls -A "$OBL" 2>/dev/null)" ] && ok "the obligation a hang left unmeasured stays in place" \
  || ko "the hung obligation was dropped"

# 18) a tip obligation is answered by the suite the run already made, whichever tree a record before it measured: no second
#     full suite runs at the tip (ADR-0168). The older tree is measured where it names, and credited there.
obclean
obwrite o/r "$sha" "-" "ruff" delta "$(( $(date +%s) + 900 ))"
obwrite o/r "$newtip" "-" "pytest" delta "$(( $(date +%s) + 900 ))"
out=$(ng "SG_SETTLE_RUFF=$T/settle.sh ruff" "SG_SETTLE_PYTEST=$T/settle.sh pytest"); rc=$?
[ "$rc" = 0 ] && ok "both obligations settle green" || ko "two obligations rc $rc: $out"
[ "$(/bin/ls -A "$SET" 2>/dev/null | /usr/bin/grep -c .)" = 2 ] && ok "each obligation wrote its own settlement" \
  || ko "settlements: '$(/bin/ls "$SET" 2>&1)'"
/usr/bin/grep -qx "ruff $T/tree $sha" "$T/settle.log" && ok "the older tree is measured where it names" \
  || ko "settle log: '$(cat "$T/settle.log" 2>&1)'"
/usr/bin/grep -q "^pytest " "$T/settle.log" 2>/dev/null && ko "the full suite ran at the tip a second time" \
  || ok "the suite already made answers pytest at the tip after an older tree"

# 19) a default settlement command is the fast gate's battery over this repository's layout. A tree the nightly measures for
#     another project holds no `squad/` or `tests/`, so its default check measures nothing: the obligation is reported by name
#     and left in place, never settled red for a path that is not there (ADR-0138).
obclean
obwrite o/r "$newtip" "-" "ruff mypy" delta "$(( $(date +%s) + 900 ))"
out=$(ng); rc=$?
[ "$rc" = 0 ] && ok "a default check that cannot measure does not fail the run" || ko "unmeasured default rc $rc: $out"
[ -n "$(/bin/ls -A "$OBL" 2>/dev/null)" ] && ok "the obligation a default check cannot measure stays in place" \
  || ko "the obligation was dropped"
if [ ! -d "$SET" ] || [ -z "$(/bin/ls -A "$SET" 2>/dev/null)" ]; then ok "no settlement is written for a check that did not measure"
else ko "settlement: '$(/bin/ls "$SET" 2>&1)'"; fi
a=$(cat "$ST/alerts.log" 2>/dev/null)
case $a in *OBLIGATION*unmeasured*"${newtip:0:7}"*) ok "the unmeasured obligation is reported by name";; *) ko "report: '$a'";; esac

# 20) old settled-red records never collect: one whose tree is no longer the tip, or older than one nightly period, is pruned
#     and reported by name. A pass that cannot read the tip holds only for a red from this period, so a red from weeks ago at
#     a long-dead tree can never stop every dispatch.
obclean
mkdir -p "$SET"
dead=$(( $(date +%s) - 90000 ))
printf '%s\t%s\t%s\t%s\t%s\t%s\tred\t%s\n' "$dead" o/r "$sha" - full ruff "$dead@$sha" > "$SET/$(obkey o/r "$sha" -)"
printf '%s\t%s\t%s\t%s\t%s\t%s\tred\t%s\n' "$(( $(date +%s) - 60 ))" o/r "$newtip" - full ruff "x@$newtip" > "$SET/$(obkey o/r "$newtip" -)"
out=$(ng); rc=$?
[ "$rc" = 0 ] && ok "a pruned red does not fail the nightly" || ko "prune run rc $rc: $out"
[ -f "$SET/$(obkey o/r "$sha" -)" ] && ko "the red at a tree that is not the tip was not pruned" \
  || ok "the red at a tree that is not the tip is pruned"
[ -f "$SET/$(obkey o/r "$newtip" -)" ] && ok "the red at the tip from this period is kept" \
  || ko "the red at the tip was pruned"
a=$(cat "$ST/alerts.log" 2>/dev/null)
case $a in *OBLIGATION*pruned*"${sha:0:7}"*) ok "a prune is reported by name";; *) ko "prune report: '$a'";; esac
printf '%s\t%s\t%s\t%s\t%s\t%s\tred\t%s\n' "$dead" o/r "$newtip" - full ruff "$dead@$newtip" > "$SET/$(obkey o/r "$newtip" -)"
o=$(env -u SG_CHECKOUT SG_STATE="$ST" bash -c '. "$0"; gh_dispatch_hold' "$S/ghcall.sh"); rc=$?
[ "$rc" = 1 ] && ok "a pass that cannot read the tip holds nothing for a red older than one nightly period" \
  || ko "an old red held a pass with no tip: '$o'"
printf '%s\t%s\t%s\t%s\t%s\t%s\tred\t%s\n' "$(date +%s)" o/r "$newtip" - full ruff "y@$newtip" > "$SET/$(obkey o/r "$newtip" -)"
o=$(env -u SG_CHECKOUT SG_STATE="$ST" bash -c '. "$0"; gh_dispatch_hold' "$S/ghcall.sh"); rc=$?
[ "$rc" = 0 ] && ok "a pass that cannot read the tip still holds for a red from this period" \
  || ko "a current red did not hold: '$o'"
rm -rf "$OBL" "$SET"

# 21) (#1176) the settlement clears or holds the card fact of an unproven landing. wave.sh records the issue and the
#     obligation key in $ST/proof-card/<issue>/<key>; a green settlement removes the key and, when no other obligation is
#     open for the issue, clears the fact; a red settlement holds the card and names the defect. A settlement no card
#     mapping names writes nothing.
obclean; rm -rf "$ST/proof-card"; : > "$T/board.log"
obwrite o/r "$newtip" "-" "ruff" delta "$(( $(date +%s) + 900 ))"; k=$(obkey o/r "$newtip" -)
proofmap 7 "$k"
out=$(ng "SG_SETTLE_RUFF=$T/settle.sh ruff"); rc=$?
[ "$rc" = 0 ] && /usr/bin/grep -qx -- '--proof o 4 7 green' "$T/board.log" && [ ! -e "$ST/proof-card/7" ] \
  && ok "a green settlement clears the card fact and its mapping" \
  || ko "green clearance: rc $rc, board '$(cat "$T/board.log" 2>/dev/null)', mapping '$(/bin/ls "$ST/proof-card" 2>&1)'"

obclean; rm -rf "$ST/proof-card"; : > "$T/board.log"
obwrite o/r "$newtip" "-" "ruff" delta "$(( $(date +%s) + 900 ))"; proofmap 8 "$(obkey o/r "$newtip" -)"
out=$(ng "SG_SETTLE_RUFF=$T/settle.sh ruff" FAIL_SETTLE=ruff); rc=$?
[ "$rc" = 1 ] && /usr/bin/grep -qx -- '--proof o 4 8 red' "$T/board.log" && [ ! -e "$ST/proof-card/8" ] \
  && ok "a red settlement holds the card and answers its mapping" \
  || ko "red hold: rc $rc, board '$(cat "$T/board.log" 2>/dev/null)', mapping '$(/bin/ls "$ST/proof-card" 2>&1)'"

obclean; rm -rf "$ST/proof-card"; : > "$T/board.log"
obwrite o/r "$newtip" "-" "ruff" delta "$(( $(date +%s) + 900 ))"; k=$(obkey o/r "$newtip" -)
proofmap 9 "$k"; proofmap 9 another-open-entry
out=$(ng "SG_SETTLE_RUFF=$T/settle.sh ruff"); rc=$?
[ "$rc" = 0 ] && [ -e "$ST/proof-card/9/another-open-entry" ] && [ ! -e "$ST/proof-card/9/$k" ] && [ ! -s "$T/board.log" ] \
  && ok "a green settlement keeps the fact while another obligation of the issue is open" \
  || ko "other open entry: rc $rc, mapping '$(/bin/ls "$ST/proof-card/9" 2>&1)', board '$(cat "$T/board.log" 2>/dev/null)'"

obclean; rm -rf "$ST/proof-card"; : > "$T/board.log"
obwrite o/r "$newtip" "-" "ruff" delta "$(( $(date +%s) + 900 ))"
out=$(ng "SG_SETTLE_RUFF=$T/settle.sh ruff"); rc=$?
[ "$rc" = 0 ] && [ ! -s "$T/board.log" ] && ok "a settlement no card mapping names writes no card fact" \
  || ko "unmapped settlement: rc $rc, board '$(cat "$T/board.log" 2>/dev/null)'"
rm -rf "$OBL" "$SET" "$ST/proof-card"

# 22) (#1201) a mode that ends before pytest writes its summary did not fail: it never finished. The parallel mode below is
#     what the nightly logs showed -- pytest's dots, then the workers' old-style hookwrapper warning at
#     pytest_sessionfinish and no `... in Ns` line, the mode's process ended by signal 15 (143). Such a mode is bounded,
#     never a red with an empty id list, and the alert names the mode, the signal and the cause instead of "no failing
#     test named".
cat > "$T/killed.sh" <<EOF
#!/bin/bash
echo "killed \$PWD" >> "$T/ran.log"
printf '.F.s..\nPluggyTeardownRaisedWarning: A plugin raised an exception during an old-style hookwrapper teardown.\nHook: pytest_sessionfinish\nOSError: cannot send (already closed?)\n'
exit 143
EOF
chmod +x "$T/killed.sh"
rm -f "$ST/alerts.log" "$ST/nightly-result" "$T/ran.log"; mark
out=$(env SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" SG_NIGHTLY_TREE="$T/tree" \
  SG_NIGHTLY_PARALLEL="$T/killed.sh" SG_NIGHTLY_SERIAL="$T/serial.sh" HEALTH_NOTIFY="$T/notify.sh" bash "$S/nightly.sh" 2>&1); rc=$?
rec
[ "$rc $r_res $r_par $r_ser" = "1 bounded bounded:signal-15 pass" ] \
  && ok "a mode killed before its summary is bounded, never fail" || ko "killed mode record: rc $rc '$r_res $r_par $r_ser' (out: $out)"
[ "$r_ids" = '[]' ] && ok "a mode that never finished names no failing test" || ko "killed mode ids: '$r_ids'"
/usr/bin/grep -q "ended before its summary" "$ST/alerts.log" 2>/dev/null && ok "the alert names the mode that ended before its summary" || ko "no kill in the alert: '$(cat "$ST/alerts.log" 2>&1)'"
/usr/bin/grep -q "SIGTERM" "$ST/alerts.log" 2>/dev/null && ok "the alert names the signal that ended the mode" || ko "no signal in the alert: '$(cat "$ST/alerts.log" 2>&1)'"
/usr/bin/grep -q "parallel" "$ST/alerts.log" 2>/dev/null && ok "the alert names the parallel mode" || ko "the alert names no mode: '$(cat "$ST/alerts.log" 2>&1)'"
/usr/bin/grep -q "cannot send" "$ST/alerts.log" 2>/dev/null && ok "the alert names the OSError at pytest_sessionfinish" || ko "the alert names no cause: '$(cat "$ST/alerts.log" 2>&1)'"
/usr/bin/grep -qi "no failing test named" "$ST/alerts.log" 2>/dev/null && ko "the alert still reads 'no failing test named'" || ok "the alert never says 'no failing test named'"
hold >/dev/null; [ $? = 1 ] && ok "a bounded record never holds dispatch" || ko "a bounded record held dispatch: $(hold)"

# 23) (#1201) a mode that ends without a summary once is run once more: the end may be transient, and the retry's own
#     summary is the reading. The first attempt's log is kept beside the second, never overwritten.
cat > "$T/retry.sh" <<EOF
#!/bin/bash
echo "retry \$PWD" >> "$T/ran.log"
n=\$(cat "$T/retry.n" 2>/dev/null || echo 0); echo \$((n+1)) > "$T/retry.n"
[ "\$n" = 0 ] && { printf 'F..\nOSError: cannot send (already closed?)\n'; exit 143; }
printf '4270 passed, 25 skipped, 24 deselected in 2844.15s\n'; exit 0
EOF
chmod +x "$T/retry.sh"
rm -f "$ST/alerts.log" "$ST/nightly-result" "$T/retry.n" "$T/ran.log"; mark
out=$(env SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" SG_NIGHTLY_TREE="$T/tree" \
  SG_NIGHTLY_PARALLEL="$T/retry.sh" SG_NIGHTLY_SERIAL="$T/serial.sh" HEALTH_NOTIFY="$T/notify.sh" bash "$S/nightly.sh" 2>&1); rc=$?
rec
[ "$rc $r_res $r_par $r_ser" = "0 pass pass pass" ] \
  && ok "a mode that ended without a summary is run once more, and the retry's summary is the record" \
  || ko "retry: rc $rc '$r_res $r_par $r_ser' (out: $out)"
/usr/bin/grep -q "4270 passed" "$ST/nightly-parallel.log" 2>/dev/null && ok "the parallel mode log carries a real pytest summary line" || ko "the mode log has no summary: '$(cat "$ST/nightly-parallel.log" 2>&1)'"
/usr/bin/grep -q "cannot send" "$ST/nightly-parallel.log.first" 2>/dev/null && ok "the first attempt's log is kept beside the second" || ko "the first attempt's log is gone: '$(/bin/ls "$ST" 2>&1)'"
[ ! -e "$ST/alerts.log" ] && ok "a retry that finished raises no alert" || ko "a finished retry alerted: '$(cat "$ST/alerts.log" 2>&1)'"

# 24) (#1201) a start killed before a mode result still leaves the one record, and it names the kill: the trap writes the
#     line with the modes it had, an alert names the signal, and no day marker is spent, so the next cycle tries again.
cat > "$T/slow.sh" <<'SLEOF'
#!/bin/bash
trap 'exit 143' TERM INT
echo started
while :; do sleep 1; done
SLEOF
chmod +x "$T/slow.sh"
start_tip=$($G -C "$T/src" rev-parse dev)
rm -f "$ST/alerts.log" "$ST/nightly-result" "$T/ran.log" "$ST/auto/nightly-$(date +%F)"
env SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" SG_NIGHTLY_TREE="$T/tree" \
  SG_NIGHTLY_PARALLEL="$T/slow.sh" SG_NIGHTLY_SERIAL="$T/serial.sh" HEALTH_NOTIFY="$T/notify.sh" \
  bash "$S/nightly.sh" > "$T/killed-start.out" 2>&1 &
kpid=$!
i=0; while [ $i -lt 100 ] && ! /usr/bin/grep -q started "$ST/nightly-parallel.log" 2>/dev/null; do sleep 0.1; i=$((i+1)); done
kill -TERM $kpid; wait $kpid; rc=$?
# The holder exits as soon as it has passed the signal on, and the nightly writes its record on the trap: wait for it.
i=0; while [ $i -lt 100 ] && [ ! -s "$ST/nightly-result" ]; do sleep 0.1; i=$((i+1)); done
i=0; while [ $i -lt 100 ] && [ ! -s "$ST/alerts.log" ]; do sleep 0.1; i=$((i+1)); done
rec
[ "$rc" = 143 ] && ok "a killed start exits on its own signal" || ko "killed start rc $rc: $(cat "$T/killed-start.out")"
[ "$r_res $r_par $r_ser" = "bounded bounded:signal-15 bounded:signal-15" ] \
  && ok "a start killed before its first mode result writes the record, never no record" \
  || ko "killed start record: '$r_res $r_par $r_ser'"
[ "$r_sha" = "$start_tip" ] && ok "the killed start's record names the tip it was proving" || ko "killed start sha: '$r_sha', dev $start_tip"
/usr/bin/grep -q "SIGTERM" "$ST/alerts.log" 2>/dev/null && ok "the alert names the signal that ended the start" || ko "no signal in the alert: '$(cat "$ST/alerts.log" 2>&1)'"
[ ! -e "$ST/auto/nightly-$(date +%F)" ] && ok "a killed start spends no day marker, so the next cycle runs again" || ko "a killed start spent the day marker"
[ ! -e "$ST/locks/nightly" ] && ok "a killed start releases its lock" || ko "the lock outlived the killed start"
rm -f "$ST/alerts.log" "$ST/nightly-result"

# 25) (#1201) the two readings are told apart at the readers: a red record names its ids and is a report, and a bounded one
#     names no test and is a report too. A bounded result over a red mode is still no reading.
rm -f "$T/ran.log"; mark
out=$(ng FAIL_parallel=1); rc=$?; rec
[ "$r_res $r_par" = "fail fail" ] && [ -n "$r_ids" ] && ! hold >/dev/null \
  && ok "a red record names its ids and is a report, not a hold" || ko "red record: '$r_res $r_par $r_ids'"
printf '%s\tbounded\t%s\tbounded:signal-15\tpass\t[]\n' "$(date +%s)" "$start_tip" > "$ST/nightly-result"
o=$(hold); rc=$?
[ $rc = 1 ] && ok "a bounded record holds no dispatch" || ko "a bounded record held dispatch: '$o'"
case "$o" in *bounded*) ok "the pass says the nightly was stopped and names the mode";; *) ko "the note does not name the bounded run: '$o'";; esac
bad_rec "$(date +%s)\tpass\t$sha\tpass\tbounded:signal-15\t[]\n" && ok "a pass over a bounded mode is no reading" || ko "a pass over a bounded mode held dispatch"
bad_rec "$(date +%s)\tbounded\t$sha\tfail\tpass\t[]\n" && ok "a bounded result over a red mode is no reading" || ko "a bounded result over a red mode held dispatch"
rm -f "$ST/nightly-result"

# 26) (ADR-0163, ADR-0164, #1178) the live check must hold no delivery: it samples for an hour, and a held nightly lock
#     refuses every promotion tick. The nightly starts it detached, so its body exits and serial.sh frees locks/nightly while
#     the check still samples under its own lock alone.
mark; rm -f "$ST/live-check-result" "$ST/alerts.log"
out=$(ng SG_LIVE_CHECK_WINDOW_SECONDS=2 SG_LIVE_CHECK_INTERVAL_SECONDS=0.1); rc=$?
[ "$rc" = 0 ] && ok "a nightly that starts the live check still exits 0" || ko "detached live check rc $rc: $out"
i=0; while [ $i -lt 200 ] && [ ! -d "$ST/locks/live-check" ]; do sleep 0.05; i=$((i+1)); done
[ -d "$ST/locks/live-check" ] && ok "the live check samples after the nightly body returns" \
  || ko "the live check did not sample after the nightly returned: '$(/bin/ls "$ST/locks" 2>&1)'"
[ ! -d "$ST/locks/nightly" ] && ok "the nightly lock is released while the live check still samples" \
  || ko "the nightly lock is held for the live check's window, so it holds delivery"
lc_wait   # let the detached check finish before the suite ends

# 27) (#1527, ADR-0153) A full suite beside live runs and their gates bounds every child's gate at 900 s, so the nightly holds
#     while a run is live or the gate lock is held. Liveness is the one reader's rule (squad/run_liveness.py): a fresh launch is
#     live; a run that ended, a run that died with no verdict and a bare name whose record ended are not. A hold writes no day
#     marker, so the next idle cycle runs the suite.
mkdir -p "$T/src/runs/done-run" "$T/live-run"
printf '{"event":"run_start"}\n' > "$T/live-run/record.jsonl"; echo "$T/live-run" > "$ST/active-runs.txt"
mark; rm -f "$T/ran.log"; rs0=$(cat "$ST/nightly-result" 2>/dev/null | cksum)
out=$(ng SG_CHECKOUT="$T/src"); rc=$?
[ $rc = 0 ] && case "$out" in "nightly: held"*"live"*) true;; *) false;; esac && [ ! -e "$T/ran.log" ] && [ ! -e "$ST/auto/nightly-$(date +%F)" ] \
  && [ "$(cat "$ST/nightly-result" 2>/dev/null | cksum)" = "$rs0" ] \
  && ok "the nightly holds while a listed run is launching (#1527)" || ko "a live run did not hold the nightly: rc $rc, '$out', ran '$(cat "$T/ran.log" 2>/dev/null)'"
nrun() {  # <label>: the nightly runs both modes
  mark; rm -f "$T/ran.log"; out=$(ng SG_CHECKOUT="$T/src"); rc=$?
  [ $rc = 0 ] && /usr/bin/grep -qx 'parallel '"$T"'/tree' "$T/ran.log" && /usr/bin/grep -qx 'serial '"$T"'/tree' "$T/ran.log" \
    && ok "$1: the nightly runs both modes" || ko "$1 held the nightly: rc $rc, '$out', ran '$(cat "$T/ran.log" 2>/dev/null)'"
}
touch -t 202001010000 "$T/live-run/record.jsonl"
nrun "a listed run that died with no verdict and a stale record is not live"
printf '{"event":"run_start"}\n{"event":"verdict","verdict":"passed"}\n' > "$T/live-run/record.jsonl"; touch -t 202001010000 "$T/live-run/record.jsonl"
nrun "a listed run with a verdict and an idle record is not live"
printf '{"event":"run_start"}\n{"event":"verdict","verdict":"passed"}\n' > "$T/src/runs/done-run/record.jsonl"; touch -t 202001010000 "$T/src/runs/done-run/record.jsonl"
echo done-run > "$ST/active-runs.txt"
nrun "a bare run name whose record under SG_CHECKOUT/runs ended is not live"
: > "$ST/active-runs.txt"; mkdir -p "$ST/locks/gate"; echo $$ > "$ST/locks/gate/pid"
mark; rm -f "$T/ran.log"; out=$(ng SG_CHECKOUT="$T/src"); rc=$?
[ $rc = 0 ] && case "$out" in "nightly: held"*"gate lock"*) true;; *) false;; esac && [ ! -e "$T/ran.log" ] && [ ! -e "$ST/auto/nightly-$(date +%F)" ] \
  && ok "the nightly holds while a live pid holds the gate lock (#1527)" || ko "a held gate lock did not hold the nightly: rc $rc, '$out', ran '$(cat "$T/ran.log" 2>/dev/null)'"
rm -f "$ST/locks/gate/pid"
nrun "a gate lock with no live pid is stale and never holds"
rm -rf "$ST/locks/gate"

# 27b) (#2593) A nightly that ended bounded proved nothing, so the next one takes no machine: it runs beside a live run and a
#      held gate lock, and the dispatch keeps starting runs. A pass, a fail or a missing verdict holds it as before, as case 27
#      and case 5 prove. The run's own record rewrites the verdict, so the case clears it after.
printf '%s\tunproven\t%s\tbounded\tmissing\t-\n' "$(date +%s)" "$sha" > "$ST/nightly-verdict"
printf '{"event":"run_start"}\n' > "$T/live-run/record.jsonl"; echo "$T/live-run" > "$ST/active-runs.txt"
mkdir -p "$ST/locks/gate"; echo $$ > "$ST/locks/gate/pid"
mark; rm -f "$T/ran.log"; out=$(ng SG_CHECKOUT="$T/src"); rc=$?
[ $rc = 0 ] && [ "$(/usr/bin/grep -c . "$T/ran.log")" = 2 ] && case "$out" in *"last nightly ended bounded"*) true;; *) false;; esac \
  && ok "a bounded nightly runs beside a live run and a held gate lock, and says why" \
  || ko "a bounded nightly did not run beside the runs: rc $rc, '$out', ran '$(cat "$T/ran.log" 2>/dev/null)'"
rm -f "$ST/locks/gate/pid"; rm -rf "$ST/locks/gate"; : > "$ST/active-runs.txt"
rm -f "$ST/nightly-verdict"

# 28) (#1722) the nightly starts the live check detached, so the suite owns its end. A check that keeps a window longer than
#     this case is still alive when the nightly body returns: a suite that does not end it leaks `live-check.sh --run` and
#     its `squad.livecheck` reading for that whole window, orphaned to pid 1. The case waits for the check to name its pid,
#     proves it is alive after a short wait, then the suite ends it by pid and group and proves it is gone, so a green guard
#     cannot pass by the check's own timer.
mark; rm -f "$T/ran.log"; out=$(ng SG_LIVE_CHECK_WINDOW_SECONDS=60); rc=$?
[ $rc = 0 ] && ok "a nightly whose live check keeps a long window ends" || ko "long-window nightly rc $rc: '$out'"
i=0; while [ -z "$(ckpid)" ] && [ $i -lt 100 ]; do sleep 0.05; i=$((i+1)); done   # bounded: the check takes its lease a moment after the body returns
p=$(ckpid | awk 'NR==1')
[ -n "$p" ] && alive_pid "$p" && ok "a detached long-window check outlives the nightly body" \
  || ko "no live check was alive to end: '$p', log '$(cat "$ST/live-check.log" 2>/dev/null)'"
sleep 0.3   # a short fixed wait, far below the check's own window: a check left here would leak for its whole window
alive_pid "$p" && ok "the check is still alive, so the suite must end it" || ko "the check ended on its own, so the guard proves nothing"
end_check
i=0; while [ -n "$(ckpid)" ] && [ $i -lt 40 ]; do sleep 0.05; i=$((i+1)); done
[ -z "$(ckpid)" ] && ok "the suite ends the check it started by pid" || ko "the check the suite started is still alive: $(ckpid)"

# 31) (#1975) The nightly runs only in its window: the profile's hour (SG_NIGHTLY_HOUR, 04 by default, America/Sao_Paulo).
mark; rm -f "$T/ran.log"; out=$(ng FAKE_HOUR=05); rc=$?
[ $rc = 0 ] && [ ! -e "$T/ran.log" ] && [ ! -e "$ST/auto/nightly-$(date +%F)" ] && case "$out" in *window*) true;; *) false;; esac \
  && ok "the nightly never starts outside its window, and writes no day marker" \
  || ko "the nightly started outside its window: rc $rc, '$out', ran '$(cat "$T/ran.log" 2>/dev/null)'"
mark; rm -f "$T/ran.log"; out=$(ng FAKE_HOUR=03); rc=$?
[ $rc = 0 ] && [ ! -e "$T/ran.log" ] && ok "the nightly never starts before its window" \
  || ko "the nightly started before its window: rc $rc, '$out'"
mark; rm -f "$T/ran.log"; out=$(ng FAKE_HOUR=05 SG_NIGHTLY_HOUR=05); rc=$?
[ $rc = 0 ] && [ -e "$T/ran.log" ] && ok "the profile's hour moves the window" \
  || ko "the nightly did not run in the profile's hour: rc $rc, '$out'"

# 32) (#1975) A window that closes with no run leaves one bounded record, and the alert names the window.
mark; rm -f "$ST/nightly-result" "$ST/alerts.log" "$ST/auto/nightly-missed-$(date +%F)"; out=$(ng FAKE_HOUR=05); rc=$?; rec
[ "$r_res" = bounded ] && grep -q 'window' "$ST/alerts.log" 2>/dev/null \
  && ok "a window that closed with no run records one bounded attempt" \
  || ko "a closed window recorded '$r_res' with alerts '$(cat "$ST/alerts.log" 2>/dev/null)': '$out'"
mark; rm -f "$ST/nightly-result" "$ST/alerts.log"; out=$(ng FAKE_HOUR=03); rec
[ -z "$r_res" ] && ok "a window not yet open records nothing" || ko "a window not yet open recorded '$r_res'"
yday=$(date -v-1d +%F 2>/dev/null || date -d yesterday +%F)
rm -f "$ST/nightly-result" "$ST/alerts.log" "$ST/auto/nightly-$yday" "$ST/auto/nightly-missed-$yday"; out=$(ng FAKE_HOUR=00 SG_NIGHTLY_HOUR=23); rec
[ "$r_res" = bounded ] && [ -e "$ST/auto/nightly-missed-$yday" ] && ok "a 23:00 window that closed at midnight with no run records one bounded attempt" \
  || ko "a 23:00 window closed with no run recorded '$r_res': '$out'"
rm -f "$ST/nightly-result" "$ST/alerts.log"

# 33) (#1975) The dispatch stops in the window until the nightly ran that day, and while the nightly runs in it.
mark; rm -f "$ST/auto/nightly-missed-$(date +%F)"; out=$(export FAKE_HOUR=04; hold)   # case 32 left today's missed record
case "$out" in *"nightly window"*) ok "the dispatch holds in the open window before the nightly ran";;
  *) ko "the dispatch did not hold in the window: '$out'";; esac
touch "$ST/auto/nightly-$(date +%F)"
out=$(export FAKE_HOUR=04; hold)
case "$out" in *"nightly window"*) ko "the dispatch holds in the window after the nightly ran today: '$out'";;
  *) ok "the window hold ends once the nightly ran that day";; esac
mark; out=$(export FAKE_HOUR=05; hold)
case "$out" in *"nightly window"*) ko "the dispatch holds outside the window: '$out'";;
  *) ok "the dispatch does not hold outside the window";; esac
mkdir -p "$ST/locks/nightly"; sleep 60 & np=$!; echo "$np" > "$ST/locks/nightly/pid"
out=$(export FAKE_HOUR=04; hold)
kill "$np" 2>/dev/null; wait "$np" 2>/dev/null; rm -rf "$ST/locks/nightly"
case "$out" in *"the nightly runs"*) ok "the dispatch holds while the nightly runs in its window";;
  *) ko "the dispatch did not hold while the nightly runs: '$out'";; esac

# 33b) (#2527) The dispatch drains SG_NIGHTLY_DRAIN_MINUTES (40) before the hour, so the live runs end before it opens. The
#      hold ends on the day's missed record, and once the hour has less time left than the suite needs (SG_NIGHTLY_NEED_MINUTES, 45).
mark; rm -f "$ST/auto/nightly-missed-$(date +%F)"
out=$(export FAKE_HOUR=03 FAKE_MS=30:00; hold)
case "$out" in *"nightly drain"*) ok "the dispatch drains in the lead before the window";;
  *) ko "the dispatch did not drain 30 min before the window: '$out'";; esac
out=$(export FAKE_HOUR=03 FAKE_MS=10:00; hold)
case "$out" in *nightly*) ko "the dispatch drains before its lead: '$out'";; *) ok "the dispatch runs before the drain lead";; esac
out=$(export FAKE_HOUR=03 FAKE_MS=30:00 SG_NIGHTLY_DRAIN_MINUTES=20; hold)
case "$out" in *nightly*) ko "the drain lead ignores its setting: '$out'";; *) ok "the profile's drain minutes move the lead";; esac
out=$(export FAKE_HOUR=04 FAKE_MS=20:00; hold)
case "$out" in *nightly*) ko "the dispatch holds with 40 min left, under the 45 the suite needs: '$out'";;
  *) ok "the window hold ends once the time left is under the need";; esac
out=$(export FAKE_HOUR=04 FAKE_MS=10:00; hold)
case "$out" in *"nightly window"*) ok "the window still holds while the suite can finish";;
  *) ko "the window released with 50 min left: '$out'";; esac
touch "$ST/auto/nightly-missed-$(date +%F)"
out=$(export FAKE_HOUR=04 FAKE_MS=05:00; hold)
case "$out" in *"nightly window"*) ko "the window holds after the day's missed record: '$out'";;
  *) ok "the day's missed record ends the window hold";; esac
out=$(export FAKE_HOUR=03 FAKE_MS=30:00; hold)
case "$out" in *"nightly drain"*) ko "the drain holds after the day's missed record: '$out'";;
  *) ok "the day's missed record ends the drain";; esac
rm -f "$ST/auto/nightly-missed-$(date +%F)"
mark; rm -f "$T/ran.log"; out=$(ng FAKE_MS=20:00); rc=$?
[ $rc = 0 ] && [ ! -e "$T/ran.log" ] && [ ! -e "$ST/auto/nightly-$(date +%F)" ] && case "$out" in *"min left"*) true;; *) false;; esac \
  && ok "the nightly never starts with less time left than it needs" \
  || ko "the nightly started with 40 min left: rc $rc, '$out', ran '$(cat "$T/ran.log" 2>/dev/null)'"

# 33c) (#2593) A nightly that ended bounded proved nothing, so its next window holds no dispatch: the runs keep the machine and
#      the nightly shares it. A pass, a fail, or a missing verdict holds the window as before. The reason is one cycle.log line,
#      said once per verdict, so a bounded verdict that stands until the next nightly does not bury the log.
verdict_line() { printf '%s\t%s\t%s\t%s\tmissing\t-\n' "$(date +%s)" "$1" "$sha" "$2" > "$ST/nightly-verdict"; }
mark; rm -f "$ST/auto/nightly-missed-$(date +%F)" "$ST/nightly-result" "$ST/nightly-verdict" "$ST/nightly-bounded-release-noted" "$ST/cycle.log"
out=$(export FAKE_HOUR=04; hold)
case "$out" in *"nightly window"*) ok "a missing verdict holds the window as before";;
  *) ko "a missing verdict released the window: '$out'";; esac
verdict_line pass pass
out=$(export FAKE_HOUR=04; hold)
case "$out" in *"nightly window"*) ok "a pass verdict holds the window as before";;
  *) ko "a pass verdict released the window: '$out'";; esac
verdict_line fail fail
out=$(export FAKE_HOUR=04; hold)
case "$out" in *"nightly window"*) ok "a fail verdict holds the window as before";;
  *) ko "a fail verdict released the window: '$out'";; esac
verdict_line unproven bounded
out=$(export FAKE_HOUR=04; hold)
case "$out" in *"nightly window"*) ko "a bounded nightly held the next window: '$out'";;
  *) ok "a bounded nightly holds no dispatch in the next window";; esac
/usr/bin/grep -q 'DISPATCH NOTE: the last nightly at .* ended bounded: its next window holds no dispatch' "$ST/cycle.log" 2>/dev/null \
  && ok "one cycle.log line names the bounded nightly as the reason" \
  || ko "cycle.log does not name the bounded release: '$(cat "$ST/cycle.log" 2>/dev/null)'"
out2=$(export FAKE_HOUR=04; hold)
[ -z "$out2" ] && ok "the bounded release is said once per verdict, not on every pass" || ko "the bounded release repeated: '$out2'"
rm -f "$ST/nightly-verdict" "$ST/nightly-bounded-release-noted"

# 34) (#1975) The local half runs only what a Linux runner deselects: both default mode commands carry the conftest's own
#     switch, SQUAD_NIGHTLY_LOCAL_HALF=1, so the one deselection rule (tests/conftest.py, ADR-0139) picks the tests, never a list here.
cat > "$T/py" <<EOF
#!/bin/bash
[ "\$1" = -c ] && exec python3 "\$@"
echo "half=\${SQUAD_NIGHTLY_LOCAL_HALF:-} \$*" >> "$T/py.log"; printf '12 passed in 0.10s\n'
EOF
chmod +x "$T/py"
mark; rm -f "$T/py.log"
out=$(env SG_GATE_PY="$T/py" SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" SG_NIGHTLY_TREE="$T/tree" \
  HEALTH_NOTIFY="$T/notify.sh" SG_GHREAD=false bash "$S/nightly.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(/usr/bin/grep -c '^half=1 -m pytest' "$T/py.log" 2>/dev/null)" = 2 ] \
  && /usr/bin/grep -q 'half=1 -m pytest -p no:deepeval -n auto' "$T/py.log" && /usr/bin/grep -q 'half=1 -m pytest -p no:deepeval -p no:xdist' "$T/py.log" \
  && ok "both default modes run the local half only" || ko "the default modes do not run the local half: rc $rc, '$(cat "$T/py.log" 2>/dev/null)', '$out'"
end_check

# 35) (#1975) One verdict: the nightly verdict combines the local record and the GitHub half, read through ghcall.sh. Fail in
#     either half is fail, pass in both on the same dev sha is pass, and any other pair is unproven.
cat > "$T/ghread" <<EOF
#!/bin/bash
echo "\$1" >> "$T/gh.log"
[ "\${GH_RC:-0}" = 0 ] || { echo 'refused: HTTP 403'; exit "\$GH_RC"; }
case "\$1" in
  *actions/workflows/nightly.yml/runs*) printf '{"workflow_runs":[%s]}' "\${GH_RUN:-}";;
  *actions/artifacts\?name=nightly-dev-sha-*) w=\${1#*name=nightly-dev-sha-}; w=\${w%%&*}
    if [ -n "\${GH_RUN:-}" ] && [ "\$w" = "\${GH_SHA:-}" ]; then printf '{"artifacts":[{"name":"nightly-dev-sha-%s","workflow_run":{"id":77}}]}' "\$w"
    else printf '{"artifacts":[]}'; fi;;
  *actions/runs/77/artifacts*) printf '{"artifacts":[{"name":"nightly-dev-sha-%s"}]}' "\${GH_SHA:-}";;
  *actions/runs/77) [ -n "\${GH_RUN:-}" ] && printf '%s' "\$GH_RUN" || exit 1;;
  *) exit 1;;
esac
EOF
chmod +x "$T/ghread"
vd() { env "$@" SG_STATE="$ST" SG_REPO=o/r SG_GHREAD="$T/ghread" HEALTH_NOTIFY="$T/notify.sh" bash "$S/nightly.sh" --verdict >/dev/null 2>&1
  v_ts= v_res= v_sha= v_loc= v_gh= v_ghsha=
  [ -f "$ST/nightly-verdict" ] && IFS=$'\t' read -r v_ts v_res v_sha v_loc v_gh v_ghsha < "$ST/nightly-verdict"; }
lrec() { printf '%s\t%s\t%s\t%s\t%s\t[]\n' "$(date +%s)" "$1" "$2" "$3" "$4" > "$ST/nightly-result"; }
today=$(date -u +%F)
done_ok='{"id":77,"status":"completed","conclusion":"success","event":"schedule","created_at":"'$today'T07:00:00Z"}'
done_red='{"id":77,"status":"completed","conclusion":"failure","event":"schedule","created_at":"'$today'T07:00:00Z"}'
running='{"id":77,"status":"in_progress","conclusion":null,"event":"schedule","created_at":"'$today'T07:00:00Z"}'
done_old='{"id":77,"status":"completed","conclusion":"success","event":"schedule","created_at":"2020-01-01T07:00:00Z"}'
vcase() {  # <want> <label> [VAR=val...]
  local want=$1 label=$2; shift 2; rm -f "$ST/nightly-verdict" "$T/gh.log"; vd "$@"
  [ "$v_res" = "$want" ] && ok "verdict $want: $label" || ko "verdict '$v_res' (local '$v_loc', github '$v_gh' at '$v_ghsha'), want $want: $label"
}
lrec pass "$sha" pass pass
vcase pass "both halves pass on the same dev sha" GH_RUN="$done_ok" GH_SHA="$sha"
[ "$v_sha" = "$sha" ] && [ "$v_ghsha" = "$sha" ] && /usr/bin/grep -q "repos/o/r/actions/artifacts?name=nightly-dev-sha-$sha" "$T/gh.log" \
  && ok "the verdict names the dev sha both halves tested, read through the gh door" || ko "verdict sha '$v_sha', github sha '$v_ghsha', reads '$(cat "$T/gh.log" 2>/dev/null)'"
vcase fail "the GitHub half fails" GH_RUN="$done_red" GH_SHA="$sha"
vcase unproven "the GitHub half tested another sha" GH_RUN="$done_ok" GH_SHA=0000000000000000000000000000000000000000
vcase unproven "the GitHub half is unfinished" GH_RUN="$running" GH_SHA="$sha"
vcase unproven "a GitHub run of an earlier day never answers today's verdict" GH_RUN="$done_old" GH_SHA="$sha"
/usr/bin/grep -q 'per_page=1$' "$T/gh.log" 2>/dev/null && /usr/bin/grep -q 'nightly.yml/runs' "$T/gh.log" \
  && ko "the verdict read the latest run, not the run that tested the local sha" || ok "the verdict reads the run that tested the local sha"
vcase unproven "the GitHub half has no run" GH_RUN=
vcase unproven "the GitHub read is refused" GH_RC=2
lrec fail "$sha" fail pass
vcase fail "the local half fails, whatever GitHub says" GH_RUN="$done_ok" GH_SHA="$sha"
lrec bounded "$sha" bounded:signal-15 pass
vcase fail "a bounded local half and a red GitHub half" GH_RUN="$done_red" GH_SHA="$sha"
vcase unproven "a bounded local half and a green GitHub half" GH_RUN="$done_ok" GH_SHA="$sha"
rm -f "$ST/nightly-result"
vcase unproven "no local record" GH_RUN="$done_ok" GH_SHA="$sha"
# An unproven verdict is read again by a later cycle, also outside the window, until a pass or a fail settles it.
lrec pass "$sha" pass pass; rm -f "$ST/nightly-verdict"; touch "$ST/auto/nightly-$(date +%F)"
vd GH_RUN="$running" GH_SHA="$sha"
env FAKE_HOUR=05 SG_STATE="$ST" SG_REPO=o/r SG_GHREAD="$T/ghread" GH_RUN="$done_ok" GH_SHA="$sha" SG_NIGHTLY_VERDICT_EVERY=0 bash "$S/nightly.sh" >/dev/null 2>&1
IFS=$'\t' read -r _ v_res _ < "$ST/nightly-verdict" 2>/dev/null
[ "$v_res" = pass ] && ok "a later cycle settles an unproven verdict" || ko "a later cycle left the verdict '$v_res'"

# 36) (#1975) A GitHub-half red is a red like a local one: one alert through the same path, said once per verdict, and one
#     dispatch note, said once; the pass goes on, as it does for a local red (ADR-0153 rule 6).
rm -f "$ST/alerts.log" "$T/notify.log" "$ST/nightly-gh-red-alerted" "$ST/nightly-gh-red-noted"; lrec pass "$sha" pass pass
vd GH_RUN="$done_red" GH_SHA="$sha"; vd GH_RUN="$done_red" GH_SHA="$sha"
[ "$(/usr/bin/grep -c "NIGHTLY GitHub half red at ${sha:0:7}" "$ST/alerts.log" 2>/dev/null)" = 1 ] && [ -s "$T/notify.log" ] \
  && ok "a GitHub-half red alerts once, through the alerts file and the notifier" \
  || ko "GitHub-half red alerts: '$(cat "$ST/alerts.log" 2>/dev/null)', notify '$(cat "$T/notify.log" 2>/dev/null)'"
rm -f "$ST/nightly-result"   # the GitHub-half note alone, with no local record beside it
out=$(hold); rc=$?; out2=$(hold)
case "$out" in *"DISPATCH NOTE"*"GitHub half"*"red"*) [ $rc = 1 ] && [ -z "$out2" ] && ok "a GitHub-half red is one dispatch note, and the pass goes on" \
  || ko "a GitHub-half red note: rc $rc, second '$out2'";; *) ko "a GitHub-half red said no note: rc $rc, '$out'";; esac
rm -f "$ST/nightly-verdict" "$ST/nightly-result"

# 37) (#1975) The local half never runs the full suite for a pytest obligation at the tip it measured: the GitHub half's result
#     at that sha answers it, and with no such result the obligation stays in place for a later nightly.
obclean; rm -f "$ST/nightly-github-seen"
obwrite o/r "$newtip" "-" "pytest" delta "$(( $(date +%s) + 900 ))"
ngh() { env "$@" SG_GATE_PY="$T/py" SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" SG_NIGHTLY_TREE="$T/tree" \
  HEALTH_NOTIFY="$T/notify.sh" "SG_SETTLE_PYTEST=$T/settle.sh pytest" bash "$S/nightly.sh" 2>&1; end_check; }
out=$(ngh SG_GHREAD=false); rc=$?
! /usr/bin/grep -q '^pytest ' "$T/settle.log" 2>/dev/null && [ -n "$(/bin/ls -A "$OBL" 2>/dev/null)" ] \
  && ok "with no GitHub result, the tip's pytest obligation stays in place and no full suite runs" \
  || ko "a tip obligation with no GitHub result: rc $rc, settle '$(cat "$T/settle.log" 2>/dev/null)', left '$(/bin/ls "$OBL" 2>/dev/null)'"
mark; rm -f "$T/settle.log"
out=$(ngh SG_GHREAD="$T/ghread" GH_RUN="$done_ok" GH_SHA="$newtip"); rc=$?
! /usr/bin/grep -q '^pytest ' "$T/settle.log" 2>/dev/null && [ -z "$(/bin/ls -A "$OBL" 2>/dev/null)" ] && /usr/bin/grep -q 'green' "$(sf)" 2>/dev/null \
  && ok "the GitHub half's pass at the tip answers pytest green" \
  || ko "GitHub pass at the tip: rc $rc, settle '$(cat "$T/settle.log" 2>/dev/null)', settled '$(cat "$(sf)" 2>/dev/null)'"
obclean; rm -f "$ST/nightly-github-seen"
obwrite o/r "$newtip" "-" "pytest" delta "$(( $(date +%s) + 900 ))"
out=$(ngh SG_GHREAD="$T/ghread" GH_RUN="$done_red" GH_SHA="$newtip"); rc=$?
! /usr/bin/grep -q '^pytest ' "$T/settle.log" 2>/dev/null && /usr/bin/grep -q 'red' "$(sf)" 2>/dev/null \
  && ok "the GitHub half's red at the tip answers pytest red" \
  || ko "GitHub red at the tip: rc $rc, settle '$(cat "$T/settle.log" 2>/dev/null)', settled '$(cat "$(sf)" 2>/dev/null)'"
obclean; rm -f "$ST/nightly-github-seen" "$ST/nightly-verdict"

# 38) (#1975) The local half runs on the sha today's GitHub run tested, so both halves name one sha; with no such run it runs
#     on the dev tip.
mark; rm -f "$ST/nightly-result"
out=$(env SG_GATE_PY="$T/py" SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" SG_NIGHTLY_TREE="$T/tree" \
  HEALTH_NOTIFY="$T/notify.sh" SG_GHREAD="$T/ghread" GH_RUN="$running" GH_SHA="$sha" bash "$S/nightly.sh" 2>&1); rc=$?; rec; end_check
[ "$r_sha" = "$sha" ] && ok "the local half runs on the sha today's GitHub run tested" \
  || ko "the local half ran at '$r_sha', not the GitHub half's $sha: rc $rc, '$out'"
mark; rm -f "$ST/nightly-result"
out=$(env SG_GATE_PY="$T/py" SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" SG_NIGHTLY_TREE="$T/tree" \
  HEALTH_NOTIFY="$T/notify.sh" SG_GHREAD="$T/ghread" GH_RUN= bash "$S/nightly.sh" 2>&1); rc=$?; rec; end_check
[ "$r_sha" = "$newtip" ] && ok "with no GitHub run today, the local half runs on the dev tip" \
  || ko "with no GitHub run the local half ran at '$r_sha', not the tip $newtip: rc $rc"

# 39) (#1975) A nightly still running at its hour's end is stopped there: it records one bounded attempt, the alert names the
#     bound, and the hour's close writes no second record. The dispatch hold ends with the hour.
mark; rm -f "$ST/nightly-result" "$ST/alerts.log" "$ST/auto/nightly-missed-$(date +%F)"
out=$(ng FAKE_MS=59:58 SLEEP_parallel=6 SG_NIGHTLY_NEED_MINUTES=0); rc=$?; rec   # the need gate (#2527) is off: this case measures the bound
[ "$r_res" = bounded ] && /usr/bin/grep -q 'bound' "$ST/alerts.log" 2>/dev/null && [ -e "$ST/auto/nightly-missed-$(date +%F)" ] \
  && [ ! -e "$ST/auto/nightly-$(date +%F)" ] \
  && ok "a nightly past its hour's end is stopped and records one bounded attempt" \
  || ko "a nightly past its bound: rc $rc, record '$r_res', alerts '$(cat "$ST/alerts.log" 2>/dev/null)', '$out'"
r0=$(cat "$ST/nightly-result" 2>/dev/null); out=$(ng FAKE_HOUR=05)
[ "$(cat "$ST/nightly-result" 2>/dev/null)" = "$r0" ] && ok "the hour's close writes no second record" || ko "the hour's close rewrote the record"

rm -rf "$T"; exit $fail
