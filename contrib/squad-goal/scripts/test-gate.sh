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
IFS=$'\t' read -r fin kind res at start sel scope < <(tail -n 1 "$H")
[ $rc = 0 ] && [ "$kind" = fast ] && [ "$res" = pass ] && [ "$at" = "$sha" ] && ok "a finished gate appends one history line: kind, result and sha" || ko "history line: '$(tail -n 1 "$H" 2>&1)'"
[ "$(tail -n 1 "$H" | tr -cd '\t' | wc -c | tr -d ' ')" = 6 ] && ok "the history line has seven fields, the scope beside the selection" || ko "history fields: '$(tail -n 1 "$H")'"
[ "$sel $scope" = "selected 2 files delta" ] && ok "the history line carries the gate's selection and its scope" || ko "history selection: '$sel' scope: '$scope'"
IFS=$'\t' read -r _ kind res _ sel scope < "$S" 2>/dev/null
[ "$sel $scope" = "selected 2 files delta" ] && ok "the status line carries the selection and the scope" || ko "status selection: '$(cat "$S" 2>&1)'"
case "$start" in ''|*[!0-9]*) ko "the history line carries no start epoch: '$start'";; *) [ "$((fin - start))" -ge 1 ] && ok "the duration is finish minus start, and the sleep counts" || ko "duration: fin $fin start $start";; esac
cmp -s "$T/h0" <(head -n "$hn0" "$H") && [ "$(wc -l < "$H" | tr -d ' ')" = "$((hn0 + 1))" ] && ok "the history is append-only: earlier lines are untouched" || ko "history was rewritten"

(cd "$T/w" && SG_GATE_KIND=full bash "$d/gate.sh" sh -c 'exit 7'); rc=$?
[ "$(tail -n 1 "$H" | tr -cd '\t' | wc -c | tr -d ' ')" = 5 ] && [ "$(tail -n 1 "$H" | cut -f2,3)" = "$(printf 'full\tfail')" ] && ok "a gate with no selection writes an empty selection field" || ko "fail history: '$(tail -n 1 "$H")'"

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
