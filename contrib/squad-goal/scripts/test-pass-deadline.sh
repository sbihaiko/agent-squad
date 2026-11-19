#!/bin/bash
# seconds: 15.65
# (#1735) The hang a deadline case starts sleeps a length only this run names, so the survivor probe and the cleanup
# below read this suite's own processes: `pgrep -f "sleep 137"` read every suite on the machine, and two gates side
# by side then read each other's hang as a survivor, or ended it.
export HANG_NAP=$(( 13700000 + $$ ))
# #130: a pass has a deadline. A full gate can hang -- one worker spins in a subprocess loop and never returns, so the pass
# sits at state=running for ever, keeps the machine busy and holds every lock its tree took. test.sh now runs its command in
# a process group of its own and stops that group at TEST_DEADLINE: the record leaves `running` and names the deadline, the
# lock the tree held goes with it, and the exit is 143 -- a pass killed by a signal, the environment, not a red gate.
#  (A) a command that hangs past the deadline: the pass ends, its record says killed/deadline, the lock is gone, exit 143;
#  (B) a command that ends on its own: the pass is completed, exit 0, the deadline never fires.
# A stub env.sh/board.sh beside a copy of test.sh and serial.sh, a temp state dir; the live loop is never read or written.
# usage: bash test-pass-deadline.sh [test.sh]
src=${1:-$(dirname "$0")/test.sh}; here=$(cd "$(dirname "$src")" && pwd); T=$(mktemp -d)
fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
cleanup() { pkill -f "$T" 2>/dev/null; pkill -f "sleep $HANG_NAP" 2>/dev/null; rm -rf "$T"; }
S=$T/s; mkdir -p "$S" "$T/st/tests" "$T/st/cq" "$T/co"
cp "$src" "$S/test.sh"; cp "$here/serial.sh" "$S/serial.sh"
# env.sh beside test.sh: the temp state dir the record and the lock land in. Exported, because the command tree is a child
# shell and the lock it takes has to be this pass's own. The board stub records the move test.sh makes at the start.
cat > "$S/env.sh" <<E
export SG_STATE=$T/st SG_OWNER=o SG_PROJECT=1
BOARD=$S/board.sh
sg_cq_file() { echo "$T/st/cq/\$1"; }
E
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$S/board.sh"
field() { python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));v=d.get(sys.argv[2],"<absent>");print(json.dumps(v) if isinstance(v,(dict,list)) else v)' "$1" "$2"; }
# The pass runs in the background and is killed if it outlives 20 s: the whole point of case A is that a hanging command must
# not keep the caller waiting, so the case itself cannot either.
run_pass() {   # $1 = TEST_DEADLINE, $2 = TEST_ID, $3 = the command's shell body
  : > "$T/pass.out"
  ( cd "$T/co" && TEST_DEADLINE=$1 TEST_ID=$2 bash "$S/test.sh" 7 -C "$T/co" -- bash -c "$3" ) > "$T/pass.out" 2>&1 &
  local j=$! t0=$(date +%s)
  for _ in $(seq 1 100); do kill -0 "$j" 2>/dev/null || break; sleep 0.2; done
  # a pass that outlived its bound is killed here; its tree is the case under test, and no case may leave it behind
  ELAPSED=$(( $(date +%s) - t0 )); RC=0
  if kill -0 "$j" 2>/dev/null; then kill -9 "$j" 2>/dev/null; pkill -f "$T" 2>/dev/null; pkill -f "sleep $HANG_NAP" 2>/dev/null; RC=killed; else wait "$j"; RC=$?; fi
  JC="$T/st/tests/$2.json"
  return 0
}
# (A) the command hangs: test.sh holds a lock in the tree it started and never comes back
run_pass 2 test-7-hang 'bash '"$S"'/serial.sh gate sleep $HANG_NAP'
[ "$RC" != killed ] && ok "A: the pass ends although its command hangs (${ELAPSED}s)" || ko "A: the pass never ended (${ELAPSED}s)"
[ "$ELAPSED" -le 15 ] && ok "A: the deadline bounds the pass (${ELAPSED}s <= 15s)" || ko "A: the pass took ${ELAPSED}s"
[ "$(field "$JC" state)" = failed ] && ok "A: the record left state=running" || ko "A: the record says $(field "$JC" state)"
[ "$(field "$JC" rc)" = 143 ] && ok "A: the record carries the killed pass's exit (143)" || ko "A: the record says rc $(field "$JC" rc)"
[ "$(field "$JC" reason)" = killed/deadline ] && ok "A: the record names the deadline" || ko "A: the record gives the reason $(field "$JC" reason)"
[ "$(field "$JC" deadline)" = 2 ] && ok "A: the record carries the deadline it ran under" || ko "A: the record says deadline $(field "$JC" deadline)"
/usr/bin/grep -q "deadline of 2s" "$T/st/tests/test-7-hang.log" && ok "A: the pass log names the missed deadline" || ko "A: the log says nothing: $(tail -c 200 "$T/st/tests/test-7-hang.log")"
[ ! -e "$T/st/locks/gate" ] && ok "A: the lock the dead pass's tree held is released" || ko "A: the pass died still holding $T/st/locks/gate"
pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1 && ko "A: the command's tree outlived the pass" || ok "A: nothing of the command's tree is left"
# (B) the command ends on its own well inside the deadline: the pass is green and the deadline never fires
run_pass 60 test-7-clean 'echo hello'
[ "$RC" = 0 ] && ok "B: a command that ends inside the deadline exits 0" || ko "B: exit $RC ($(cat "$T/pass.out"))"
[ "$(field "$JC" state)" = completed ] && ok "B: the record is completed" || ko "B: the record says $(field "$JC" state)"
[ "$(field "$JC" reason)" = '<absent>' ] && ok "B: a pass that ended on its own names no kill" || ko "B: the record names $(field "$JC" reason)"
cleanup; exit $fail
