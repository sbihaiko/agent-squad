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
# collect when its own COLLECT_<mode> is set, and exits red there.
for m in parallel serial; do
  cat > "$T/$m.sh" <<EOF
#!/bin/bash
echo "$m \$PWD" >> "$T/ran.log"
[ "\${FAIL_$m:-}" = 1 ] && { printf 'FAILED tests/test_x.py::test_a - boom\nFAILED tests/test_x.py::test_x[case with space] - boom\nFAILED tests/test_y.py::test_b - boom\n'; exit 1; }
[ "\${COLLECT_$m:-}" = 1 ] && { printf 'ERROR tests/test_module.py - ImportError: no module named x\n'; exit 1; }
exit 0
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
bad_rec "$(date +%s)\tfail\t$sha\tpass\tmaybe\t-\n" && ok "a mode outside pass|fail never holds dispatch" || ko "an invalid mode held dispatch"
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
#    so the record says pass and no dispatch is held. A configured command that exits 5 is still red.
printf '#!/bin/bash\n[ "$1" = -c ] && exec python3 "$@"\necho "no tests ran"; exit 5\n' > "$T/py"; chmod +x "$T/py"
rm -f "$ST/alerts.log" "$ST/nightly-result"; mark
out=$(env -u SG_NIGHTLY_PARALLEL -u SG_NIGHTLY_SERIAL SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" \
  SG_NIGHTLY_TREE="$T/tree" SG_GATE_PY="$T/py" HEALTH_NOTIFY="$T/notify.sh" bash "$S/nightly.sh" 2>&1); rc=$?
rec
[ "$rc $r_res $r_par $r_ser" = "0 pass pass pass" ] && ok "a default pytest that collects nothing is no suite, not red (#758)" || ko "no-suite record: rc $rc '$r_res $r_par $r_ser' $out"
[ ! -e "$ST/alerts.log" ] && ok "no suite raises no alert" || ko "no suite alerted: $(cat "$ST/alerts.log")"
printf '#!/bin/bash\nexit 5\n' > "$T/five.sh"; chmod +x "$T/five.sh"; mark
out=$(env SG_STATE="$ST" SG_REPO=o/r SG_BASE=dev SG_NIGHTLY_REMOTE="$T/origin.git" SG_NIGHTLY_TREE="$T/tree" \
  SG_NIGHTLY_PARALLEL="$T/five.sh" SG_NIGHTLY_SERIAL="$T/serial.sh" HEALTH_NOTIFY="$T/notify.sh" bash "$S/nightly.sh" 2>&1); rc=$?
[ $rc = 1 ] && ok "a configured command that exits 5 is still red" || ko "configured exit 5 rc $rc"

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

rm -rf "$T"; exit $fail
