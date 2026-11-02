#!/bin/bash
# Regression test for gate.sh: a gate runs under the machine lock, passes the exit code through, and leaves the hub's latest
# status in $SG_STATE/gate-status. A full result also stays in $SG_STATE/gate-status-full after a later fast gate.
# Both lines are `<epoch> TAB <kind> TAB <pass|fail> TAB <sha>`.
# usage: bash test-gate.sh [scripts dir]
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so the hold is proved
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; export SG_STATE=$T/st; mkdir -p "$SG_STATE"
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
[ "$(tail -n 1 "$H" | tr -cd '\t' | wc -c | tr -d ' ')" = 11 ] && ok "the history line has twelve fields: the selection and scope, then the verdict and the slots" || ko "history fields: '$(tail -n 1 "$H")'"
[ "$sel $scope" = "selected 2 files delta" ] && ok "the history line carries the gate's selection and its scope" || ko "history selection: '$sel' scope: '$scope'"
IFS=$'\t' read -r _ kind res _ sel scope < "$S" 2>/dev/null
[ "$sel $scope" = "selected 2 files delta" ] && ok "the status line carries the selection and the scope" || ko "status selection: '$(cat "$S" 2>&1)'"
case "$start" in ''|*[!0-9]*) ko "the history line carries no start epoch: '$start'";; *) [ "$((fin - start))" -ge 1 ] && ok "the duration is finish minus start, and the sleep counts" || ko "duration: fin $fin start $start";; esac
cmp -s "$T/h0" <(head -n "$hn0" "$H") && [ "$(wc -l < "$H" | tr -d ' ')" = "$((hn0 + 1))" ] && ok "the history is append-only: earlier lines are untouched" || ko "history was rewritten"

(cd "$T/w" && SG_GATE_KIND=full bash "$d/gate.sh" sh -c 'exit 7'); rc=$?
[ "$(tail -n 1 "$H" | tr -cd '\t' | wc -c | tr -d ' ')" = 11 ] && [ "$(tail -n 1 "$H" | cut -f2,3)" = "$(printf 'full\tfail')" ] && ok "a gate with no selection writes an empty selection field" || ko "fail history: '$(tail -n 1 "$H")'"

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

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
