#!/bin/bash
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
G="git -c user.email=a@b -c user.name=t"
cp "$d/serial.sh" "$d/ghcall.sh" "$d/ghread.py" "$d/ghthrottle.py" "$S/" 2>/dev/null
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
[ "\${KILL_$m:-}" = 1 ] && exit 143
[ "\${KILLFAIL_$m:-}" = 1 ] && { printf 'FAILED tests/test_x.py::test_a - boom\n1 failed, 10 passed in 1.00s\n'; exit 143; }
[ "\${FAIL_$m:-}" = 1 ] && { printf 'FAILED tests/test_x.py::test_a - boom\nFAILED tests/test_x.py::test_x[case with space] - boom\nFAILED tests/test_y.py::test_b - boom\n3 failed, 10 passed in 1.00s\n'; exit 1; }
[ "\${COLLECT_$m:-}" = 1 ] && { printf 'ERROR tests/test_module.py - ImportError: no module named x\n1 error in 0.10s\n'; exit 1; }
printf '4270 passed, 25 skipped in 12.34s\n'; exit 0
EOF
  chmod +x "$T/$m.sh"; done
printf '#!/bin/bash\necho "$*" >> "%s/notify.log"\n' "$T" > "$T/notify.sh"; chmod +x "$T/notify.sh"
export T
# ng [VAR=val...]: one nightly run against the fixture, its own state, tree and remote
ng() { env "$@" SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" SG_NIGHTLY_TREE="$T/tree" \
  SG_NIGHTLY_PARALLEL="$T/parallel.sh" SG_NIGHTLY_SERIAL="$T/serial.sh" HEALTH_NOTIFY="$T/notify.sh" bash "$S/nightly.sh" 2>&1; }
rec() { r_ts= r_res= r_sha= r_par= r_ser= r_ids=
  [ -f "$ST/nightly-result" ] && IFS=$'\t' read -r r_ts r_res r_sha r_par r_ser r_ids < "$ST/nightly-result"; }
hold() { ( export SG_STATE="$ST" SG_CHECKOUT="$T/src"; . "$S/ghcall.sh"; gh_dispatch_hold ); }   # the dispatch hold both dispatch passes read (ghcall.sh)
mark() { rm -f "$ST/auto/nightly-$(date +%F)"; }
# json_ids <field>: 0 when the field is ONE JSON array of strings, the one encoding both readers parse (#704)
json_ids() { printf '%s' "$1" | python3 -c 'import json,sys
try: v=json.load(sys.stdin)
except Exception: sys.exit(1)
sys.exit(0 if type(v) is list and all(type(x) is str for x in v) else 1)'; }

# 1) both modes run once, in the nightly tree, and the record says pass with the sha and both modes
out=$(ng); rc=$?
[ $rc = 0 ] && ok "a green nightly exits 0" || ko "green nightly rc $rc: $out"
[ "$(cat "$T/ran.log" 2>/dev/null)" = "parallel $T/tree
serial $T/tree" ] && ok "both modes run in the nightly tree, parallel then serial" || ko "ran: '$(cat "$T/ran.log" 2>&1)'"
rec
[ "$r_res $r_sha $r_par $r_ser $r_ids" = "pass $sha pass pass []" ] && ok "the record is pass, the tip sha, both modes green and no failing test" || ko "record: '$r_res $r_sha $r_par $r_ser $r_ids'"
[ -e "$ST/auto/nightly-$(date +%F)" ] && ok "the run leaves its once-a-day marker" || ko "no day marker after a run"
[ ! -d "$ST/locks/nightly" ] && ok "the nightly lock is released when the run ends" || ko "the nightly lock stayed behind"
[ ! -e "$ST/alerts.log" ] && ok "a green nightly raises no alert" || ko "a green nightly alerted: $(cat "$ST/alerts.log")"

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

# 4) a red record holds dispatch until one green run, and a green one releases it
hold >/dev/null; [ $? = 0 ] && ok "the dispatch hold stops a pass after a red nightly" || ko "a red nightly held no dispatch"
[ -z "$(hold 2>&1 | /usr/bin/grep -i nightly)" ] && ko "the hold does not say why it stopped" || ok "the hold names the nightly as its cause"
hold 2>&1 | /usr/bin/grep -qF 'tests/test_x.py::test_x[case with space]' && ok "the hold names a failing test whole, its space included" || ko "the hold split a parametrized id: $(hold)"
mark; out=$(ng)                                     # one green run
hold >/dev/null; [ $? = 1 ] && ok "one green nightly releases delivery" || ko "the hold outlived a green nightly: $(hold)"
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
#     parallel one was stopped -- the hold never stops delivery on it, and an open bound marker settles it as unproven.
rm -f "$T/ran.log" "$ST/alerts.log" "$ST/nightly-result" "$ST/promote-bound" "$ST/promote-bound-settled" \
  "$ST/nightly-bounded-noted"; mark
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
# A bounded settlement is unproven, so it never closes the marker: dev moves past the marked tip, the next nightly still
# proves the marked tip, a pass then closes the marker, and the night after that proves the dev tip again.
printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$sha" 1 "squad/x.py" >> "$ST/promote-bound"; mark
out=$(ng KILL_parallel=1); rc=$?
IFS=$'\t' read -r _ st_t st_r < "$ST/promote-bound-settled" 2>/dev/null
[ "$st_t $st_r" = "$sha bounded" ] && ok "a bounded leg settles the bound marker as unproven, never as red" \
  || ko "bounded settle: '$st_t $st_r' (rc $rc: $out)"
$G -C "$T/src" commit -q --allow-empty -m after-bound; $G -C "$T/src" push -q "$T/origin.git" dev
after=$($G -C "$T/src" rev-parse dev); rm -f "$T/ran.log"; mark
out=$(ng); rc=$?; rec
[ "$rc $r_res $r_sha" = "0 pass $sha" ] \
  && ok "a bounded settle leaves the marker open: the next nightly still proves the marked tip, not the dev tip" \
  || ko "re-prove after a bounded settle: rc $rc, record '$r_res $r_sha', want $sha, dev $after"
[ "$(tail -n 1 "$ST/promote-bound-settled" 2>/dev/null | cut -f2,3 | tr '\t' ' ')" = "$sha pass" ] \
  && ok "a pass settles the marker" || ko "settle after bounded: '$(tail -n 1 "$ST/promote-bound-settled" 2>/dev/null)'"
mark; out=$(ng); rc=$?; rec
[ "$rc $r_sha" = "0 $after" ] && ok "a settled marker sends the nightly back to the dev tip" \
  || ko "after a pass settle: rc $rc, record sha $r_sha, dev $after"
rm -f "$ST/promote-bound"

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
[ $rc = 0 ] && ok "a red leg still holds delivery, even when a signal also stopped it" \
  || ko "a red killed leg held no dispatch: rc $rc, '$o'"
rm -f "$T/ran.log"

# 5) the landing lock is never taken: a held gate lock is no obstacle, and it is left as it was
mkdir -p "$ST/locks/gate"; echo $$ > "$ST/locks/gate/pid"; rm -f "$T/ran.log"; mark
out=$(ng); rc=$?
[ $rc = 0 ] && [ "$(/usr/bin/grep -c . "$T/ran.log")" = 2 ] && ok "a held landing lock does not stop the nightly" || ko "gate lock rc $rc: $out"
[ -e "$ST/locks/gate/pid" ] && ok "the nightly never took the landing lock" || ko "the nightly removed the landing lock"
rm -rf "$ST/locks/gate"

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

# 10) (#1046, ADR-0164) an open bound marker: the nightly proves the marked tip, not the dev tip, and settles the marker with
#     the result. Dev moves past the marked tip first, so the record can only name the marked one.
$G -C "$T/src" commit -q --allow-empty -m two; $G -C "$T/src" push -q "$T/origin.git" dev; newtip=$($G -C "$T/src" rev-parse dev)
rm -f "$T/ran.log" "$ST/alerts.log" "$ST/nightly-result" "$ST/promote-bound" "$ST/promote-bound-settled"; mark
printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$sha" 1 "squad/x.py" >> "$ST/promote-bound"
out=$(ng); rc=$?; rec
[ "$rc $r_res $r_sha" = "0 pass $sha" ] && ok "an open bound marker makes the nightly prove the marked tip" \
  || ko "marked tip: rc $rc, record '$r_res $r_sha', dev $newtip (out: $out)"
IFS=$'\t' read -r _ st_t st_r < "$ST/promote-bound-settled" 2>/dev/null
[ "$st_t $st_r" = "$sha pass" ] && ok "the nightly settles the marker with its result" || ko "settled: '$st_t $st_r'"

# 10b) the marker is settled: the next day's nightly proves the dev tip again
mark; out=$(ng); rc=$?; rec
[ "$rc $r_sha" = "0 $newtip" ] && ok "a settled marker no longer changes the nightly tip" || ko "after settle: rc $rc, record sha $r_sha, dev $newtip"

# 10c) a red on the marked tip is settled as red and fails the record: the nightly never turns a red into a pass
rm -f "$ST/promote-bound-settled" "$ST/alerts.log" "$ST/nightly-result"; mark
out=$(ng FAIL_parallel=1); rc=$?; rec
IFS=$'\t' read -r _ st_t st_r < "$ST/promote-bound-settled" 2>/dev/null
[ "$rc $r_res $r_sha" = "1 fail $sha" ] && [ "$st_t $st_r" = "$sha fail" ] && ok "a red on the marked tip fails the record and settles the marker as red" \
  || ko "red marked tip: rc $rc, record '$r_res $r_sha', settled '$st_t $st_r'"

# --- proof obligations (spec 1163, ADR-0153): the nightly settles what a gate could not prove. It reads each record in
#     $SG_STATE/obligations, runs the named checks over the named tree, and writes one settlement line in
#     $SG_STATE/obligation-settled. The stub checks log the tree and its HEAD, so credit and tree are read from the run.
OBL=$ST/obligations; SET=$ST/obligation-settled
obwrite() {  # <repository> <sha> <base> <checks> <scope> <deadline>: the gate's own key for one obligation entry
  mkdir -p "$OBL"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" > "$OBL/$(printf '%s\t%s\t%s' "$1" "$2" "$3" | cksum | cut -d' ' -f1)"
}
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
obkey() { printf '%s\t%s\t%s' "$1" "$2" "$3" | cksum | cut -d' ' -f1; }
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

rm -rf "$T"; exit $fail
