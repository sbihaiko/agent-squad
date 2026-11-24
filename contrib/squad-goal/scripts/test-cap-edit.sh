#!/bin/bash
# seconds: 11.65
# Regression test for cap-edit.sh: the one procedure every cap edit goes through. Stop and start are stubs, the settings
# file is a real project file in a throw-away checkout, and the settings check is the real `config check` unless a case stubs it.
# usage: bash test-cap-edit.sh
here="$(cd "$(dirname "$0")" && pwd)"; E="$here/cap-edit.sh"; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
F="$T/co/docs/squad/defaults.yaml"; LOG="$T/calls.log"; AR="$T/st/active-runs.txt"
mkdir -p "$T/st" "$T/co/docs/squad" "$T/co/runs/run-live" "$T/co/runs/run-done" "$T/bin"
printf '{"event": "run_start", "ts": 1}\n' > "$T/co/runs/run-live/record.jsonl"
printf '{"event": "run_start", "ts": 1}\n{"event": "verdict", "ts": 2, "verdict": "completed"}\n' > "$T/co/runs/run-done/record.jsonl"
printf '#!/bin/bash\necho stop >> "%s"\nexit ${STOP_RC:-0}\n' "$LOG" > "$T/bin/stop"
printf '#!/bin/bash\necho start >> "%s"\nexit 0\n' "$LOG" > "$T/bin/start"
printf '#!/bin/bash\nexit 1\n' > "$T/bin/check-fail"
chmod +x "$T/bin/stop" "$T/bin/start" "$T/bin/check-fail"
export SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_CAP_EDIT_STOP="$T/bin/stop" SG_CAP_EDIT_START="$T/bin/start"
export SG_CAP_EDIT_TRIES=2 SG_CAP_EDIT_POLL=0
ORIG='limits:
  max_stalls: 4'

fresh() { printf '%s\n' "$ORIG" > "$F"; : > "$LOG"; : > "$AR"; unset SG_CAP_EDIT_CHECK STOP_RC; }

# held: a live run (no verdict) is listed, so the edit waits, names the run, and writes nothing
fresh; echo "run-live" > "$AR"
out=$(bash "$E" set limits.max_stalls 8 2>&1); rc=$?
[ "$rc" = 3 ] && case "$out" in *"run-live"*) true;; *) false;; esac \
  && [ "$(cat "$F")" = "$ORIG" ] && [ "$(cat "$LOG")" = "$(printf 'stop\nstart')" ] \
  && ok "held: a live run holds the edit, the procedure names it, and dispatch comes back" \
  || ko "held: rc=$rc out=$out log=$(tr '\n' ' ' < "$LOG")"

# empty: a finished run (verdict) does not hold, so the edit goes through and dispatch comes back
fresh; echo "run-done" > "$AR"
out=$(bash "$E" set limits.max_stalls 8 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$F")" = "$(printf 'limits:\n  max_stalls: 8')" ] && [ "$(cat "$LOG")" = "$(printf 'stop\nstart')" ] \
  && case "$out" in *"$F"*) true;; *) false;; esac \
  && ok "empty: no live run lets the edit through, names the file, and dispatch comes back" \
  || ko "empty: rc=$rc out=$out file=$(tr '\n' ' ' < "$F")"

# revert goes through the same procedure: reset removes the key, the file keeps its other lines
fresh; echo "run-done" > "$AR"; printf 'limits:\n  max_stalls: 8\n' > "$F"
bash "$E" reset limits.max_stalls >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && ! grep -q max_stalls "$F" && [ "$(cat "$LOG")" = "$(printf 'stop\nstart')" ] \
  && ok "revert: a reset runs the same procedure and dispatch comes back" \
  || ko "revert: rc=$rc file=$(tr '\n' ' ' < "$F") log=$(tr '\n' ' ' < "$LOG")"

# failed check: the edit is written, the check fails, the old value is restored, and dispatch comes back
fresh; echo "run-done" > "$AR"; export SG_CAP_EDIT_CHECK="$T/bin/check-fail"
out=$(bash "$E" set limits.max_stalls 8 2>&1); rc=$?
[ "$rc" = 1 ] && [ "$(cat "$F")" = "$ORIG" ] && [ "$(cat "$LOG")" = "$(printf 'stop\nstart')" ] \
  && case "$out" in *"check"*) true;; *) false;; esac \
  && ok "failed check: the old value stays in place and dispatch comes back" \
  || ko "failed check: rc=$rc out=$out file=$(tr '\n' ' ' < "$F") log=$(tr '\n' ' ' < "$LOG")"

# failure: the edit itself is refused (out of range), the file is untouched, and dispatch comes back
fresh; echo "run-done" > "$AR"
out=$(bash "$E" set limits.max_stalls 999 2>&1); rc=$?
[ "$rc" = 1 ] && [ "$(cat "$F")" = "$ORIG" ] && [ "$(cat "$LOG")" = "$(printf 'stop\nstart')" ] \
  && case "$out" in *"edit"*) true;; *) false;; esac \
  && ok "failure: a refused edit leaves the file and dispatch comes back" \
  || ko "failure: rc=$rc out=$out log=$(tr '\n' ' ' < "$LOG")"

# failure: the stop itself fails, nothing is edited, and dispatch is still started again
fresh; echo "run-done" > "$AR"
out=$(STOP_RC=1 bash "$E" set limits.max_stalls 8 2>&1); rc=$?
[ "$rc" = 1 ] && [ "$(cat "$F")" = "$ORIG" ] && [ "$(cat "$LOG")" = "$(printf 'stop\nstart')" ] \
  && ok "stop failure: nothing is edited and dispatch is started again" \
  || ko "stop failure: rc=$rc out=$out log=$(tr '\n' ' ' < "$LOG")"

# (#1708) the edit's own stop and start are not the owner's: with no seam named, the stop is the internal `cycle-stop`,
# which leaves no owner mark and keeps a loop-bug drain's mark, and the start is `start --not-owner`, which never starts a
# loop the owner stopped. The stub watchd.sh records the verbs it is called with and refuses the stop, so nothing is edited.
fresh; mkdir -p "$T/k/a/b"; cp "$E" "$T/k/a/b/cap-edit.sh"
printf '#!/bin/bash\necho "$*" >> "%s/verbs"\n[ "$1" = cycle-stop ] && exit 1\nexit 0\n' "$T" > "$T/k/a/b/watchd.sh"
out=$(env -u SG_CAP_EDIT_STOP -u SG_CAP_EDIT_START bash "$T/k/a/b/cap-edit.sh" set limits.max_stalls 8 2>&1)
[ "$(cat "$T/verbs" 2>/dev/null)" = "$(printf 'cycle-stop\nstart --not-owner')" ] \
  && ok "1708 a cap edit stops and starts the loop through the verbs that are not the owner's" \
  || ko "1708 the cap edit called watchd.sh with: $(tr '\n' ';' < "$T/verbs" 2>/dev/null) ($out)"

rm -rf "$T"; exit $fail
