#!/bin/bash
# (#1735) The hang a deadline case starts sleeps a length only this run names, so the survivor probe and the cleanup
# below read this suite's own processes: `pgrep -f "sleep 2718"` read every suite on the machine, and two gates side
# by side then read each other's hang as a survivor, or ended it.
export HANG_NAP=$(( 271800000 + $$ ))
# Regression test for gate.sh: a gate runs under the machine lock, passes the exit code through, and leaves the hub's latest
# status in $SG_STATE/gate-status. A full result also stays in $SG_STATE/gate-status-full after a later fast gate.
# Both lines are `<epoch> TAB <kind> TAB <pass|fail> TAB <sha>`.
# usage: bash test-gate.sh [scripts dir]
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so the hold is proved
# The machine-wide gate a suite is often run under exports its own bound into every command it starts (#392): this suite reads
# the gate's default bound, so an inherited one is dropped here, as a promotion gate's 1200 s would move the obligation's deadline.
unset SG_GATE_BOUND_SECONDS
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; export SG_STATE=$T/st; mkdir -p "$SG_STATE"
# (#1166) The gate takes its worker slots from a throwaway budget, never the machine's live one.
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SQUAD_CAPS_POLL=0.2
git init -q "$T/w"; $G -C "$T/w" commit -q --allow-empty -m one; sha=$(git -C "$T/w" rev-parse HEAD); S=$SG_STATE/gate-status

bash "$d/gate.sh" sh -c "[ -d '$SG_STATE/locks/gate' ] && echo held > '$T/held'" 
[ "$(cat "$T/held" 2>/dev/null)" = held ] && ok "the gate command runs while the machine lock is held" || ko "the lock was not held while the gate ran"
[ -d "$SG_STATE/locks/gate" ] && ko "the lock is left behind" || ok "the lock is released after the gate"

(cd "$T/w" && SG_GATE_KIND=fast bash "$d/gate.sh" true); rc=$?
IFS=$'\t' read -r ep kind res at < "$S" 2>/dev/null
[ $rc = 0 ] && [ "$kind" = fast ] && [ "$res" = pass ] && [ "$at" = "$sha" ] && ok "a pass leaves one line: kind, pass and the sha it ran at" || ko "pass line: rc $rc '$(cat "$S" 2>&1)'"
case "$ep" in ''|*[!0-9]*) ko "the line carries no epoch: '$ep'";; *) ok "the line carries the epoch";; esac
[ "$(wc -l < "$S" | tr -d ' ')" = 1 ] && ok "the status is one line" || ko "status lines: $(cat "$S")"

(cd "$T/w" && SG_GATE_KIND=full bash "$d/gate.sh" sh -c 'exit 7'); rc=$?
IFS=$'\t' read -r ep kind res at < "$S" 2>/dev/null
[ $rc = 7 ] && [ "$kind" = full ] && [ "$res" = fail ] && [ "$at" = "$sha" ] && ok "a red gate passes its exit code through and the hub says fail" || ko "fail line: rc $rc '$(cat "$S" 2>&1)'"
F=$SG_STATE/gate-status-full
IFS=$'\t' read -r _ kind res at < "$F" 2>/dev/null
[ "$kind" = full ] && [ "$res" = fail ] && [ "$at" = "$sha" ] && ok "a full result writes promotion proof" || ko "full proof: '$(cat "$F" 2>&1)'"

(cd "$T/w" && SG_GATE_KIND=fast bash "$d/gate.sh" true)
IFS=$'\t' read -r _ kind res at < "$S" 2>/dev/null
[ "$kind" = fast ] && [ "$res" = pass ] && [ "$at" = "$sha" ] && ok "a later fast gate replaces the hub's latest status" || ko "latest status: '$(cat "$S" 2>&1)'"
IFS=$'\t' read -r _ kind res at < "$F" 2>/dev/null
[ "$kind" = full ] && [ "$res" = fail ] && [ "$at" = "$sha" ] && ok "a later fast gate does not replace full proof" || ko "full proof after fast: '$(cat "$F" 2>&1)'"
[ "$(wc -l < "$S" | tr -d ' ')" = 1 ] && ok "the latest status is one line" || ko "status lines: $(cat "$S")"

# --- the history: one line per finished gate, appended, its duration the run time after the lock wait (#647) ---
H=$SG_STATE/gate-history
hn0=$(wc -l < "$H" 2>/dev/null | tr -d ' '); hn0=${hn0:-0}; cp "$H" "$T/h0" 2>/dev/null || : > "$T/h0"
(cd "$T/w" && SG_GATE_KIND=fast bash "$d/gate.sh" sh -c 'sleep 1; echo "selected 2 files" > "$GATE_SELECT_FILE"; echo delta > "$GATE_SCOPE_FILE"'); rc=$?
IFS=$'\t' read -r fin kind res at start _ < <(tail -n 1 "$H"); sel=$(tail -n 1 "$H" | cut -f6); scope=$(tail -n 1 "$H" | cut -f7)
[ $rc = 0 ] && [ "$kind" = fast ] && [ "$res" = pass ] && [ "$at" = "$sha" ] && ok "a finished gate appends one history line: kind, result and sha" || ko "history line: '$(tail -n 1 "$H" 2>&1)'"
[ "$(tail -n 1 "$H" | tr -cd '\t' | wc -c | tr -d ' ')" = 25 ] && ok "the history line has twenty-six fields: the selection and scope, the verdict, the slots, how a red cleared, the stage, the load at the start and at the finish, the entry mode, then the tree, the wait, the wall time, the cpu, the reuse, the runner, its CI run id and the two slot clocks" || ko "history fields: '$(tail -n 1 "$H")'"
# (#2317) The slot clocks: when the gate asked for its slots inside the lock and when it held them, so the gate stint
# splits into its wait and its run (ADR-0166).
IFS=$'\t' read -r _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ sla slg < <(tail -n 1 "$H")
case "$sla$slg" in ''|*[!0-9]*) ko "the history line carries no slot ask or slot got: '$sla' '$slg'";; \
  *) [ "$slg" -ge "$sla" ] && ok "the row states when the gate asked for its slots and when it held them" || ko "slot clocks: ask $sla got $slg";; esac
# (#1798) The entry mode is the seventeenth field: a gate that ran its battery is `gated`, and one that ran none is `skipped`.
[ "$(tail -n 1 "$H" | cut -f17)" = gated ] && ok "a gate that ran a battery names its entry mode gated" || ko "entry mode of a battery gate: '$(tail -n 1 "$H")'"
[ "$sel $scope" = "selected 2 files delta" ] && ok "the history line carries the gate's selection and its scope" || ko "history selection: '$sel' scope: '$scope'"
IFS=$'\t' read -r _ kind res _ sel scope < "$S" 2>/dev/null
[ "$sel $scope" = "selected 2 files delta" ] && ok "the status line carries the selection and the scope" || ko "status selection: '$(cat "$S" 2>&1)'"
case "$start" in ''|*[!0-9]*) ko "the history line carries no start epoch: '$start'";; *) [ "$((fin - start))" -ge 1 ] && ok "the duration is finish minus start, and the sleep counts" || ko "duration: fin $fin start $start";; esac
cmp -s "$T/h0" <(head -n "$hn0" "$H") && [ "$(wc -l < "$H" | tr -d ' ')" = "$((hn0 + 1))" ] && ok "the history is append-only: earlier lines are untouched" || ko "history was rewritten"

(cd "$T/w" && SG_GATE_KIND=full bash "$d/gate.sh" sh -c 'exit 7'); rc=$?
[ "$(tail -n 1 "$H" | tr -cd '\t' | wc -c | tr -d ' ')" = 25 ] && [ "$(tail -n 1 "$H" | cut -f2,3)" = "$(printf 'full\tfail')" ] && ok "a gate with no selection writes an empty selection field" || ko "fail history: '$(tail -n 1 "$H")'"
[ "$(tail -n 1 "$H" | cut -f17)" = skipped ] && ok "a gate that ran no battery names its entry mode skipped" || ko "entry mode of a gate with no battery: '$(tail -n 1 "$H")'"

# --- the load (#1691): a gate line carries the one-minute load at its start and at its finish, so a set of gate runs can be
# labeled before any load bound enters a gate (ADR-0012, ADR-0018). The reader is a seam: this suite cannot set the machine's load.
case "$(tail -n 1 "$H" | cut -f15)" in ''|*[!0-9.]*) ko "the line carries no load at the start: '$(tail -n 1 "$H" | cut -f15)'";; *) ok "the history line carries the machine's load at the start";; esac
case "$(tail -n 1 "$H" | cut -f16)" in ''|*[!0-9.]*) ko "the line carries no load at the finish: '$(tail -n 1 "$H" | cut -f16)'";; *) ok "the history line carries the machine's load at the finish";; esac
printf 0 > "$T/ln"
cat > "$T/loadread" <<LR
n=\$(( \$(cat "$T/ln") + 1 )); printf '%s' "\$n" > "$T/ln"; echo "\$n.50"
LR
(cd "$T/w" && GATE_LOAD_READ=$T/loadread SG_GATE_KIND=fast bash "$d/gate.sh" true)
[ "$(tail -n 1 "$H" | cut -f15,16)" = "$(printf '1.50\t2.50')" ] && ok "the start load is read before the command and the finish load after it" \
  || ko "load fields: '$(tail -n 1 "$H" | cut -f15,16)'"
printf 'echo no-number\n' > "$T/loadbad"
(cd "$T/w" && GATE_LOAD_READ=$T/loadbad SG_GATE_KIND=fast bash "$d/gate.sh" true); rc=$?
[ $rc = 0 ] && [ "$(tail -n 1 "$H" | cut -f15,16)" = "$(printf -- '-\t-')" ] && ok "a load no reader can read is a dash, and the gate's verdict stands" \
  || ko "unreadable load: rc $rc '$(tail -n 1 "$H" | cut -f15,16)'"
# The labeled set: each gate that carries a load, with its outcome and the outcome of the next gate on the same tree.
LS=$T/ls; mkdir -p "$LS"
{ printf '100\tfast\tfail\taaa\t90\t\tall\tfail\texit 1\tpytest\t-\t3\t-\tloop\t52.10\t48.00\n'
  printf '200\tfast\tpass\tbbb\t190\t\tall\tpass\t-\tpytest\t-\t3\t-\tloop\t4.00\t5.00\n'
  printf '300\tfast\tpass\taaa\t290\t\tall\tpass\t-\tpytest\t-\t3\t-\tloop\t6.00\t7.25\n'
  printf '400\tfast\tfail\tccc\t390\t\tall\tfail\texit 1\tpytest\t-\t3\t-\tother\n'; } > "$LS/gate-history"
set_out=$(python3 "$d/gate_load_set.py" "$LS" 2>&1)
[ "$(printf '%s\n' "$set_out" | sed -n 2p)" = "$(printf '100\taaa\tfast\tall\tfail\t52.10\t48.00\tpass')" ] \
  && ok "the labeled set names a red, its load at both ends and the same-tree rerun that passed" || ko "labeled red: '$set_out'"
[ "$(printf '%s\n' "$set_out" | sed -n 4p)" = "$(printf '300\taaa\tfast\tall\tpass\t6.00\t7.25\t-')" ] \
  && ok "a gate with no later gate on its tree has no rerun outcome" || ko "labeled last: '$set_out'"
[ "$(printf '%s\n' "$set_out" | /usr/bin/grep -c .)" = 4 ] && ok "a line from before the load fields is no row of the set" || ko "set rows: '$set_out'"

# --- the verdict (spec 1163): the outcome, its one reason, the checks that ran and the ones that did not, and the slots ---
(cd "$T/w" && SG_GATE_KIND=fast bash "$d/gate.sh" sh -c 'printf "unproven\tmypy is not installed\truff\tmypy pytest\n" > "$GATE_OUTCOME_FILE"; exit 4'); rc=$?
vl=$(tail -n 1 "$H")
[ $rc = 4 ] && [ "$(printf '%s' "$vl" | cut -f3)" = fail ] && [ "$(printf '%s' "$vl" | cut -f8-11)" = "$(printf 'unproven\tmypy is not installed\truff\tmypy pytest')" ] \
  && ok "a gate that reports an unproven verdict writes its outcome, reason, ran and not-run checks, and its exit code is 4" || ko "unproven verdict: rc $rc: '$vl'"
case "$(printf '%s' "$vl" | cut -f12)" in ''|*[!0-9]*) ko "the history line carries no slots: '$vl'";; *) ok "the history line carries the slots the gate took";; esac
(cd "$T/w" && SG_GATE_KIND=fast bash "$d/gate.sh" true)
[ "$(tail -n 1 "$H" | cut -f8-11)" = "$(printf 'pass\t-\t-\t-')" ] && ok "a gate with no outcome file writes pass, and dashes for the reason and the checks" || ko "pass verdict: '$(tail -n 1 "$H")'"
(cd "$T/w" && SG_GATE_KIND=fast bash "$d/gate.sh" sh -c 'exit 7')
[ "$(tail -n 1 "$H" | cut -f8-11)" = "$(printf 'fail\texit 7\t-\t-')" ] && ok "a gate with no outcome file that fails writes fail and its exit code as the reason" || ko "fail verdict: '$(tail -n 1 "$H")'"

# --- the proof obligation (spec 1163): an unproven entry writes one obligation, the job that settles it later. The record
# names the repository, the tree sha, the base, the checks that did not run and their scope, and a deadline: the proof bound
# (ADR-0163) past the gate. A pass and a fail write none. A second obligation for the same entry merges into the one record,
# never a second one, and the deadline never moves later. Its own state directory, so no earlier case answers a count.
OS=$T/obl; mkdir -p "$OS"; O=$OS/obligations; HS=$OS/gate-history
cat > "$T/unproven.sh" <<'S'
#!/bin/bash
printf 'unproven\tmypy is not installed\truff\t%s\n' "$CHECKS" > "$GATE_OUTCOME_FILE"
[ -z "${SCOPE:-}" ] || printf '%s\n' "$SCOPE" > "$GATE_SCOPE_FILE"
[ -z "${BASE:-}" ] || printf '%s\n' "$BASE" > "$GATE_BASE_FILE"
exit 4
S
obn() { /bin/ls "$O" 2>/dev/null | /usr/bin/grep -c .; }
obf() { /bin/ls "$O"/* 2>/dev/null | head -n 1; }
ou() { (cd "$T/w" && SG_STATE="$OS" SG_REPO=owner/repo SG_GATE_KIND=fast CHECKS="$1" SCOPE="$2" BASE="$3" bash "$d/gate.sh" bash "$T/unproven.sh" 2>&1); }
out=$(ou "mypy pytest" delta 0123456789abcdef); rc=$?
IFS=$'\t' read -r orp osh oba och osc odl < "$(obf)" 2>/dev/null
[ $rc = 4 ] && [ "$(obn)" = 1 ] && [ "$orp $osh $oba" = "owner/repo $sha 0123456789abcdef" ] \
  && ok "an unproven entry writes one obligation naming the repository, the tree sha and the base" || ko "unproven obligation: rc $rc, $(obn) record(s): '$(cat "$(obf)" 2>&1)'"
[ "$och $osc" = "mypy pytest delta" ] && ok "the obligation names the checks that did not run and their scope" || ko "obligation checks and scope: '$och' '$osc'"
fin=$(tail -n 1 "$HS" | cut -f1)
[ "$odl" = "$((fin + 900))" ] && [ "$odl" -gt "$(date +%s)" ] && ok "the obligation carries a deadline, the proof bound after the gate" || ko "obligation deadline: '$odl' after finish '$fin'"
d1=$odl; sleep 1
out=$(ou "ruff" full 0123456789abcdef); rc=$?
IFS=$'\t' read -r _ _ _ och osc odl < "$(obf)" 2>/dev/null
[ $rc = 4 ] && [ "$(obn)" = 1 ] && [ "$och $osc" = "mypy pytest ruff full" ] && [ "$odl" = "$d1" ] \
  && ok "a second obligation for the same entry merges into the one record: the checks join, the wider scope wins and the deadline stays" || ko "merged obligation: $(obn) record(s), checks '$och', scope '$osc', deadline '$odl' (was '$d1')"
# A gate that named no missing check and no scope still writes a record that names them: every check of the gate is unproven,
# and a check that did not run cannot be said to cover less than the whole tree (ADR-0018).
out=$(ou - "" 0123456789abcdef); rc=$?
IFS=$'\t' read -r _ _ _ och osc _ < "$(obf)" 2>/dev/null
[ $rc = 4 ] && [ "$(obn)" = 1 ] && [ "$och $osc" = "all full" ] \
  && ok "an unproven gate that named no check writes the checks as all and the scope as full" || ko "unnamed obligation: $(obn) record(s), '$och' '$osc'"
$G -C "$T/w" commit -q --allow-empty -m moved
out=$(ou "pytest" delta 0123456789abcdef); rc=$?
[ $rc = 4 ] && [ "$(obn)" = 2 ] && ok "another tree sha is another entry: a second obligation record" || ko "second entry: $(obn) record(s)"
op=$T/obl-pass; mkdir -p "$op"
(cd "$T/w" && SG_STATE="$op" SG_GATE_KIND=fast bash "$d/gate.sh" true) >/dev/null 2>&1
[ -z "$(/bin/ls -A "$op/obligations" 2>/dev/null)" ] && ok "a pass entry writes no obligation" || ko "a pass wrote: '$(/bin/ls "$op/obligations" 2>/dev/null)'"
ofail=$T/obl-fail; mkdir -p "$ofail"
(cd "$T/w" && SG_STATE="$ofail" SG_GATE_KIND=fast bash "$d/gate.sh" sh -c 'exit 7') >/dev/null 2>&1
[ -z "$(/bin/ls -A "$ofail/obligations" 2>/dev/null)" ] && ok "a fail entry writes no obligation" || ko "a fail wrote: '$(/bin/ls "$ofail/obligations" 2>/dev/null)'"

# The second gate waits for the first (a 3 s hold). Its duration is the run time, not the wait: the history shows the hold, and not the queue.
(cd "$T/w" && SG_GATE_KIND=fast bash "$d/gate.sh" sh -c 'sleep 3') & hold=$!
sleep 1
(cd "$T/w" && SG_GATE_KIND=fast bash "$d/gate.sh" true)
wait "$hold"
IFS=$'\t' read -r fin _ _ _ start _ < <(tail -n 1 "$H")
held=$(tail -n 2 "$H" | head -n 1); hfin=${held%%$'\t'*}; hstart=$(printf '%s' "$held" | cut -f5)
[ "$((hfin - hstart))" -ge 2 ] && ok "the held gate's history line shows its run time" || ko "held gate duration: '$held'"
case "$fin$start" in *[!0-9]*|'') ko "the queued gate has no history line: '$(tail -n 1 "$H" 2>&1)'";; *) [ "$((fin - start))" -lt 2 ] && ok "a gate that waited for the lock logs only its run time, not the wait" || ko "queued gate logged its wait: fin $fin start $start";; esac

# --- a measured red survives (spec 1163, #1170): a timeout, a retry, a fallback, a later attempt of another scope and a
# second inconclusive attempt never clear it. Only a covered pass on the same tree (green) or a pass on a descendant (fix)
# clears it, and the red's record names which of the two did. A gate that names no check is no measured red: the simple
# exit-code fails above never stand as one.
RS=$T/red; mkdir -p "$RS"
cat > "$T/red-delta.sh" <<'S'
#!/bin/bash
printf 'fail\tpytest tests/test_a.py::test_one\truff mypy pytest\t-\n' > "$GATE_OUTCOME_FILE"
printf 'delta\n' > "$GATE_SCOPE_FILE"
exit 1
S
cat > "$T/red-full.sh" <<'S'
#!/bin/bash
printf 'fail\tpytest tests/test_a.py::test_one\truff mypy pytest\t-\n' > "$GATE_OUTCOME_FILE"
printf 'full\n' > "$GATE_SCOPE_FILE"
exit 1
S
cat > "$T/green-delta.sh" <<'S'
#!/bin/bash
printf 'pass\t-\truff mypy pytest\t-\n' > "$GATE_OUTCOME_FILE"
printf 'delta\n' > "$GATE_SCOPE_FILE"
exit 0
S
cat > "$T/green-full.sh" <<'S'
#!/bin/bash
printf 'pass\t-\truff mypy pytest\t-\n' > "$GATE_OUTCOME_FILE"
printf 'full\n' > "$GATE_SCOPE_FILE"
exit 0
S
rrun() { (cd "$T/w" && SG_STATE="$RS" SG_REPO=owner/repo SG_GATE_KIND=fast bash "$d/gate.sh" bash "$T/$1.sh" 2>&1); }
rh() { tail -n 1 "$RS/gate-history"; }
rfile() {  # <sha>: the standing red record for that tree, empty when none
  local f; for f in "$RS"/gate-reds/*; do [ -f "$f" ] || continue; IFS=$'\t' read -r _ _ s _ < "$f"; [ "$s" = "$1" ] && { printf '%s\n' "$f"; return; }; done
}
rsha=$(git -C "$T/w" rev-parse HEAD)
# (#1410) A gate row names its caller in field 14: the word SG_GATE_CALLER carries, `other` when it names none.
(cd "$T/w" && SG_STATE="$RS" SG_REPO=owner/repo SG_GATE_KIND=fast SG_GATE_CALLER=entry bash "$d/gate.sh" bash "$T/green-full.sh" >/dev/null 2>&1)
[ "$(rh | cut -f14)" = entry ] && ok "a gate row names the caller the environment gave" || ko "caller field: '$(rh)'"
(cd "$T/w" && SG_STATE="$RS" SG_REPO=owner/repo SG_GATE_KIND=fast bash "$d/gate.sh" bash "$T/green-full.sh" >/dev/null 2>&1)
[ "$(rh | cut -f14)" = other ] && ok "a gate row with no caller named reads other" || ko "default caller: '$(rh)'"
# (#1856) The row tells queue time from work: the tree it read, the seconds it waited for the lock and its slots, its wall
# time, its CPU and whether the verdict was reused. Without them a window of rows cannot say how much of a gate stage was
# waiting (ADR-0166: gate wait is no run time).
(cd "$T/w" && SG_STATE="$RS" SG_REPO=owner/repo SG_GATE_KIND=fast SG_GATE_CALLER=landing bash "$d/gate.sh" bash "$T/green-full.sh" >/dev/null 2>&1)
row=$(rh); rtree=$(git -C "$T/w" rev-parse 'HEAD^{tree}')
[ "$(printf '%s' "$row" | awk -F'\t' '{print NF}')" = 26 ] \
  && ok "a gate row carries twenty-six fields: the entry mode, the tree, the wait, the wall time, the CPU, the reuse, the runner, its CI run id and the two slot clocks" || ko "row width: '$(printf '%s' "$row" | awk -F'\t' '{print NF}')' fields: '$row'"
[ "$(printf '%s' "$row" | cut -f14)" = landing ] && ok "the landing's gate names its stage landing" || ko "landing stage: '$(printf '%s' "$row" | cut -f14)'"
[ "$(printf '%s' "$row" | cut -f17)" = gated ] && ok "a landing gate that ran its battery names its mode gated" || ko "mode field: '$(printf '%s' "$row" | cut -f17)'"
[ "$(printf '%s' "$row" | cut -f18)" = "$rtree" ] && ok "the row names the tree sha the gate read, never only its commit" || ko "tree field: '$(printf '%s' "$row" | cut -f18)', want $rtree"
[ "$(printf '%s' "$row" | cut -f20)" = "$(( $(printf '%s' "$row" | cut -f1) - $(printf '%s' "$row" | cut -f5) ))" ] \
  && ok "the row carries its wall time, finish minus start" || ko "wall field: '$(printf '%s' "$row" | cut -f20)' on '$(printf '%s' "$row" | cut -f1)-$(printf '%s' "$row" | cut -f5)'"
case "$(printf '%s' "$row" | cut -f19)" in ''|*[!0-9]*) ko "wait field is no whole second: '$(printf '%s' "$row" | cut -f19)'";; *) ok "the row carries the lock wait in whole seconds";; esac
case "$(printf '%s' "$row" | cut -f21)" in ''|*[!0-9]*) ko "cpu field is no whole second: '$(printf '%s' "$row" | cut -f21)'";; *) ok "the row carries the CPU the gate's command spent";; esac
[ "$(printf '%s' "$row" | cut -f22)" = no ] && ok "a gate that measured the tree itself is not a reuse" || ko "reused field: '$(printf '%s' "$row" | cut -f22)'"
# The wait is measured from the gate's own start, so a gate queued behind a holder records the seconds it stood in the queue.
QS=$T/queue; mkdir -p "$QS"
( SG_STATE="$QS" bash "$d/serial.sh" gate sleep 3 ) 2>/dev/null & qhold=$!
sleep 1
(cd "$T/w" && SG_STATE="$QS" SERIAL_POLL_SECONDS=0.2 bash "$d/gate.sh" bash "$T/green-full.sh" >/dev/null 2>&1)
qw=$(tail -n 1 "$QS/gate-history" | cut -f19)
case "$qw" in ''|*[!0-9]*) ko "a queued gate wrote no wait: '$qw'";; *) [ "$qw" -ge 1 ] && ok "a gate queued behind a holder records the seconds it waited" || ko "queued wait: '${qw}s', want 1 or more";; esac
wait "$qhold" 2>/dev/null
out=$(rrun red-delta); rc=$?
[ $rc = 1 ] && [ "$(rh | cut -f8)" = fail ] && [ -n "$(rfile "$rsha")" ] && [ "$(rh | cut -f13)" = red ] \
  && ok "a measured red is recorded, and the history names it red" || ko "red record: rc $rc, '$(rh)'"
IFS=$'\t' read -r _ _ _ rsc rch _ rcl _ < "$(rfile "$rsha")" 2>/dev/null
[ "$rsc $rch $rcl" = "delta ruff mypy pytest -" ] \
  && ok "the red record names its tree's scope, the checks that reported it and that it stands" || ko "red file: '$(cat "$(rfile "$rsha")" 2>&1)'"
out=$(rrun red-delta); rc=$?
[ $rc = 1 ] && [ "$(rh | cut -f8)" = fail ] && [ "$(rh | cut -f13)" = red ] && [ -n "$(rfile "$rsha")" ] \
  && ok "a red followed by another red on the same tree stands" || ko "second red: rc $rc, '$(rh)'"
# A second inconclusive attempt on the same tree ends fail, and its reason names the red that stands.
printf '#!/bin/bash\nprintf "unproven\\tmypy is not installed\\truff\\tmypy pytest\\n" > "$GATE_OUTCOME_FILE"\nprintf "delta\\n" > "$GATE_SCOPE_FILE"\nexit 4\n' > "$T/unproven-delta.sh"
out=$(rrun unproven-delta); rc=$?
[ $rc = 1 ] && [ "$(rh | cut -f8)" = fail ] && case "$(rh | cut -f9)" in *"red stands"*) true;; *) false;; esac \
  && [ "$(rh | cut -f13)" = "red-stands $rsha" ] \
  && ok "a red followed by an unproven attempt on the same tree ends fail, and the record names the red" || ko "red over unproven: rc $rc, '$(rh)'"
# A pass of another scope does not clear it: the red measured the full tree, and a delta pass covers only the delta.
out=$(rrun red-full); rc=$?
out=$(rrun green-delta); rc=$?
[ $rc = 1 ] && [ "$(rh | cut -f8)" = fail ] && [ "$(rh | cut -f13)" = "red-stands $rsha" ] \
  && ok "a pass of another scope never clears a red" || ko "other scope: rc $rc, '$(rh)'"
# A covered pass on the same tree clears it as green, and the record names the run that cleared it.
out=$(SCOPE=full rrun green-full); rc=$?
fin=$(rh | cut -f1)
[ $rc = 0 ] && [ "$(rh | cut -f8)" = pass ] && [ "$(rh | cut -f13)" = "cleared-green $rsha" ] \
  && ok "a red cleared by a covered pass on the same tree ends pass, and the record names the green run" || ko "green clear: rc $rc, '$(rh)'"
[ -z "$(rfile "$rsha")" ] && [ "$(rh | cut -f1)" = "$fin" ] && ok "a cleared red leaves no standing record, and the clearing stays on the history line" \
  || ko "cleared red still stands: '$(cat "$(rfile "$rsha")" 2>&1)'"
# A red cleared by a fix: the red tree is an ancestor of the passing tree, and the record names the change that cleared it.
out=$(rrun red-delta); rc=$?
$G -C "$T/w" commit -q --allow-empty -m "the fix"
fixed=$(git -C "$T/w" rev-parse HEAD)
out=$(rrun green-delta); rc=$?
[ $rc = 0 ] && [ "$(rh | cut -f13)" = "cleared-fix $rsha..$fixed" ] \
  && ok "a red cleared by a fix on a descendant names the change that cleared it" || ko "fix clear: rc $rc, '$(rh)'"
[ -z "$(rfile "$rsha")" ] && ok "the fixed red leaves no standing record, and the history names the change that cleared it" \
  || ko "fixed red still stands: '$(cat "$(rfile "$rsha")" 2>&1)'"

# --- one deadline covers the whole attempt (#1171): the clock starts when the gate takes the lock, the gate's own tree is
# stopped at the deadline, and the outcome is bounded unless a check already reported a defect or a red stands.
DB=$T/deadline; mkdir -p "$DB"
cat > "$T/defect-hang.sh" <<'S'
#!/bin/bash
printf 'fail\tpytest tests/test_h.py::test_hang\truff mypy pytest\t-\n' > "$GATE_OUTCOME_FILE"
sleep $HANG_NAP &
sleep $HANG_NAP
S
cat > "$T/plain-hang.sh" <<'S'
#!/bin/bash
sleep $HANG_NAP
S
# The case itself must end even when the code under test never does: a hard bound kills the gate it started, so a red run
# reports FAIL lines instead of hanging the suite.
bg() {  # <seconds> <out> <command...>: print the command's rc, 124 when the bound killed it
  local b=$1 o=$2 p w rc; shift 2
  "$@" > "$o" 2>&1 & p=$!
  ( sleep "$b"; kill -TERM "$p" 2>/dev/null; sleep 1; kill -KILL "$p" 2>/dev/null ) >/dev/null 2>&1 & w=$!
  wait "$p" 2>/dev/null; rc=$?
  kill "$w" 2>/dev/null; wait "$w" 2>/dev/null
  printf '%s\n' "$rc"
}
dst=$DB
drun() { rc=$(bg 12 "$T/drun.out" env SG_STATE="$dst" SG_REPO=owner/repo SG_GATE_KIND=fast SG_GATE_BOUND_SECONDS=2 \
  bash -c "cd '$T/w' && exec bash '$d/gate.sh' bash '$T/$1.sh'"); out=$(cat "$T/drun.out"); }
s0=$(date +%s); drun defect-hang; el=$(( $(date +%s) - s0 ))
[ "$el" -lt 20 ] && ok "the gate ends at its deadline instead of waiting for its tree (${el}s)" || ko "the deadline did not bound the gate (${el}s)"
dh=$(tail -n 1 "$DB/gate-history")
[ "$(printf '%s' "$dh" | cut -f8)" = fail ] && [ "$(printf '%s' "$dh" | cut -f9)" = "pytest tests/test_h.py::test_hang" ] \
  && ok "a failing test followed by a hang at the deadline ends fail, with the defect's reason" || ko "defect at the deadline: rc $rc, '$dh'"
pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1 && ko "a descendant process survived the deadline" || ok "no descendant process survives the deadline"
# A red that stands is not cleared by a bounded attempt on the same tree: it ends fail, and the record says so.
drun plain-hang
bh=$(tail -n 1 "$DB/gate-history")
case "$(printf '%s' "$bh" | cut -f13)" in red-stands*) bstand=1;; *) bstand=0;; esac
[ $rc = 1 ] && [ "$(printf '%s' "$bh" | cut -f8)" = fail ] && [ $bstand = 1 ] \
  && ok "a bounded attempt never clears a red: it ends fail" || ko "bounded over red: rc $rc, '$bh'"
# With no red standing, the same bounded attempt is unproven and bounded, never a pass, and its exit is the unproven one.
DB2=$T/deadline-plain; mkdir -p "$DB2"; dst=$DB2
drun plain-hang
bh=$(tail -n 1 "$DB2/gate-history")
[ $rc = 4 ] && [ "$(printf '%s' "$bh" | cut -f8)" = unproven ] && case "$(printf '%s' "$bh" | cut -f9)" in bounded*) true;; *) false;; esac \
  && ok "a bounded attempt is recorded as bounded, never as a pass, and exits unproven" || ko "bounded plain: rc $rc, '$bh'"
IFS=$'\t' read -r _ _ bres _ < "$DB2/gate-status" 2>/dev/null
[ "$bres" = fail ] && ok "a bounded attempt's status line is no pass" || ko "bounded status: '$(cat "$DB2/gate-status" 2>&1)'"
pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1 && ko "a descendant process survived the second deadline" || ok "the second bounded attempt leaves no descendant"

# --- the watchdog's forked sleep dies with the gate, through --inner and through --run (#1441, #1510) ---
# A short command ends long before the watchdog's bound, so its forked sleep can go only when the gate reaps it. The pid
# fallback in reap_wd would mask the group kill, so the gate runs with a pgrep that answers nothing: the group kill is then
# the one reaper, and it reads the process table through $SG_PS, the seam the top-level source of caps.sh supplies to every
# branch, the fresh `bash gate.sh --run` child included (#1508). The bound is a value no other case uses, so the forked sleep
# is found by its own argv.
WG=$T/watchdog; mkdir -p "$WG" "$T/nopgrep"; wb=31415; wsleep="sleep $((wb + 5))"
printf '#!/bin/sh\nexit 1\n' > "$T/nopgrep/pgrep"; chmod +x "$T/nopgrep/pgrep"
wrun() {  # <mode>: run a short command through the gate, print its rc; never inherited by the other gate invocations
  local rc
  ( cd "$T/w" && exec env PATH="$T/nopgrep:$PATH" SG_STATE="$WG" SG_GATE_KIND=fast SG_GATE_BOUND_SECONDS=$wb \
      bash "$d/gate.sh" "$1" true ) > "$T/wgate.out" 2>&1; rc=$?
  printf '%s\n' "$rc"
}
for wmode in --inner --run; do
  wrc=$(wrun "$wmode"); wleft=
  for _ in 1 2 3 4 5 6 7 8 9 10; do   # a short grace: the group kill is a signal, and its delivery is not instant
    wleft=$(pgrep -f "$wsleep" 2>/dev/null); [ -z "$wleft" ] && break
    sleep 0.1
  done
  [ "$wrc" = 0 ] && [ -z "$wleft" ] \
    && ok "the watchdog's forked sleep is gone right after the gate returns ($wmode)" \
    || ko "the watchdog sleep survives $wmode: rc $wrc, pids '${wleft:-none}', '$(tail -2 "$T/wgate.out" | tr '\n' ' ')'"
  [ -z "$wleft" ] || pkill -f "$wsleep"
done

# --- (#2379, ADR-0169 rule 9) a full battery takes its own bound. gate.sh reads it once (sg_full_bound) and hands the command
# GATE_FULL_BOUND_SECONDS and GATE_FULL_DEADLINE_EPOCH beside the delta clock. Its watchdog keeps the delta deadline until the
# command writes `full` to GATE_SCOPE_FILE, and then keeps the full one even when the file changes again. The lock hold, the
# obligation of a full scope and a priority caller's slot wait follow the full bound, so no lease runs out under a full gate.
FB=$T/full-bound; mkdir -p "$FB"
fbclean="-u SERIAL_HOLD_SECONDS -u SERIAL_WAIT_SECONDS -u SG_GATE_SLOT_WAIT -u SG_GATE_SLOT_WAIT_SECONDS -u SG_GATE_MAX_SECONDS -u SG_GATE_FULL_MAX_SECONDS"
fbrun() {  # <command>: one gate with a 1 s delta bound and a 30 s full bound on its own state, the command's output kept
  (cd "$T/w" && env $fbclean SG_STATE="$FB" SG_REPO=owner/repo SG_GATE_KIND=fast SG_GATE_BOUND_SECONDS=1 SG_GATE_FULL_BOUND_SECONDS=30 \
    bash "$d/gate.sh" bash -c "$1" > "$T/fb.out" 2>&1)
}
s0=$(date +%s); fbrun 'echo "$GATE_START_EPOCH $GATE_FULL_DEADLINE_EPOCH $GATE_FULL_BOUND_SECONDS $SERIAL_HOLD_SECONDS" > "'"$T"'/fb.env"; printf "full\n" > "$GATE_SCOPE_FILE"; sleep 1; printf "delta\n" > "$GATE_SCOPE_FILE"; sleep 11'; rc=$?; el=$(( $(date +%s) - s0 ))
[ $rc = 0 ] && [ "$el" -ge 12 ] && ok "2379 a command that named the full scope runs past the delta deadline, and a later delta never shortens it (${el}s)" \
  || ko "2379 full scope under gate.sh: rc $rc in ${el}s: $(tail -n 2 "$T/fb.out" | tr '\n' ' ')"
read -r fbs fbd fbb fbh < "$T/fb.env" 2>/dev/null
[ "$(( ${fbd:-0} - ${fbs:-0} ))" = 30 ] && [ "$fbb" = 30 ] && ok "2379 the command reads the full clock beside the delta one" || ko "2379 full clock: '$(cat "$T/fb.env" 2>/dev/null)'"
[ "$(tail -n 1 "$FB/gate-history" | cut -f7)" = full ] && ok "2379 the history records the full scope the watchdog latched" || ko "2379 latched scope: '$(tail -n 1 "$FB/gate-history" 2>/dev/null)'"
[ "$fbh" = 330 ] && ok "2379 the lock hold is the full bound plus its backstop" || ko "2379 lock hold: '$fbh', want 330"
fbh=$(cd "$T/w" && env $fbclean SG_STATE="$FB" SG_GATE_KIND=fast SG_GATE_CALLER=landing SG_GATE_BOUND_SECONDS=1 SG_GATE_FULL_BOUND_SECONDS=1000 \
  bash "$d/gate.sh" sh -c 'echo "$SERIAL_HOLD_SECONDS"' 2>/dev/null | tail -n 1)
[ "$fbh" = 2360 ] && ok "2379 a landing gate's hold also covers its slot wait: the full bound and 60 s, then the full bound and 300 s" || ko "2379 landing hold: '$fbh', want 2360"
$G -C "$T/w" commit -q --allow-empty -m full-obligation
FO=$FB/obl; mkdir -p "$FO"
(cd "$T/w" && env $fbclean SG_STATE="$FO" SG_REPO=owner/repo SG_GATE_KIND=fast SG_GATE_FULL_BOUND_SECONDS=1500 CHECKS=pytest SCOPE=full BASE=b \
  bash "$d/gate.sh" bash "$T/unproven.sh" >/dev/null 2>&1)
IFS=$'\t' read -r _ _ _ _ fosc fodl < "$(/bin/ls "$FO/obligations"/* 2>/dev/null | head -n 1)" 2>/dev/null
fofin=$(tail -n 1 "$FO/gate-history" | cut -f1)
[ "$fosc" = full ] && [ "$fodl" = "$(( fofin + 1500 ))" ] && ok "2379 a full-scope obligation's deadline is the full bound past the gate" || ko "2379 full obligation: scope '$fosc' deadline '$fodl' finish '$fofin'"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
pkill -f "sleep $HANG_NAP" 2>/dev/null   # a red run leaves the hang its case started
rm -rf "$T"; exit $fail
