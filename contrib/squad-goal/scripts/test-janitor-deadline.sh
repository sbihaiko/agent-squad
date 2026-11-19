#!/bin/bash
# seconds: 12.95
# ADR-0051: a call has a deadline. The janitor runs commands whose child may leave a grandchild behind, and a command may
# hang. Neither may hold the pass: the module runs every command into a file (never a pipe) and cuts it at its deadline.
#  (A) the stub `gh` starts `sleep` in the background: the child exits while a pipe-based caller would keep reading;
#  (B) the stub `gh` hangs on the first call: the call itself misses the deadline, and the pass names it;
#  (C) the stub `gh` records only 3 s after it starts: a slow entry under load is still a reach, never a missed deadline.
# Stubbed gh/board, a temp state dir; the live loop is never read or written. usage: bash test-janitor-deadline.sh [janitor.py]
# The suite declares its own world (#393): the loop's own shell exports SG_* (SG_BOARD_VOCAB=shipped, SG_FORK_DIR, SG_LAND_BRANCH,
# ...), and those reached the janitor below, which then took the shipped path and its fork rules instead of the fixture's. A hand
# run went red on the owner's machine where shell-suites.sh -- which clears every SG_* for the gate -- stayed green.
# The fixture owns only what it starts: the stub records the pid of each sleep it starts, and the cleanup kills those pids.
# A global `pkill -f "sleep 20"` reaches every copy of this suite under a parallel gate and every unrelated process on the
# machine, so a concurrent copy's hanging case stopped hanging and the suite named a defect the product did not have.
# A case waits on the stub's readiness mark, not on a pre-pass wall-clock window: under load the pass can spend seconds in
# its own startup calls before it reaches gh, and that time is not the deadline's. The elapsed bound is measured from the
# reach, so only the referent moves. On a failure the suite prints the pass's own output and exit code.
for v in $(env | /usr/bin/grep -o '^SG_[A-Za-z0-9_]*'); do unset "$v"; done
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs" "$T/caps/runs" "$T/caps/children"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"
# Rule O reads the card of an issue whose dispatch marker a run left behind, so a pass reaches gh.
echo run-pr > "$T/st/active-runs.txt"; mkdir -p "$T/co/runs/run-pr"; : > "$T/st/auto/disp-901"
printf 'CHILD — GitHub issue #901 (bug; one PR):\n' > "$T/co/runs/run-pr/request.txt"
printf '{"event": "verdict", "verdict": "needs-human", "reasons": ["x"], "exit_code": 0}\n' > "$T/co/runs/run-pr/record.jsonl"
diag() {   # a failing case names its evidence: the pass's output and exit code, never a guessed cause
  echo "     janitor exited ${RC:-?}"
  echo "     --- janitor output ---"
  /usr/bin/sed 's/^/     /' "$T/out"
  echo "     --- end janitor output ---"
}
pass_ends() {   # $1 = the stub body; $2 = the call deadline (default 8 s); $3 = "self" when the body writes the record itself.
  # The pass is killed if it outlives 60 s. Sets REACHED, ELAPSED (from the reach) and RC.
  local deadline=${2:-8} j t0
  { printf '#!/bin/bash\n'
    [ "${3:-}" = self ] || printf 'echo "$*" >> "%s/gh.calls"\n: > "%s/gh.reached"\n' "$T" "$T"
    printf '%s\n' "$1"; } > "$T/bin/gh"
  chmod +x "$T/bin/gh"; : > "$T/gh.calls"; rm -f "$T/gh.reached"
  PATH="$T/bin:$PATH" SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=x SG_PROJECT=1 SG_BASE=main BOARD="$T/s/board.sh" \
    SG_LAND_REMOTE="$T/origin.git" JANITOR_DRY=1 JANITOR_NOTIFY=/bin/true JANITOR_CALL_TIMEOUT="$deadline" \
    SQUAD_CAPS_DIR="$T/caps" T="$T" T_SLEEPS="$T/sleeps" T_COUNT="$T/count" T_COUNT_C="$T/count-c" \
    python3 -I "$T/s/janitor.py" > "$T/out" 2>&1 &
  j=$!
  # The readiness handshake: the stub writes gh.reached on entry. This bound is the test's patience (60 s), never the
  # deadline under test, so a loaded machine cannot spend the deadline before the pass reaches the command.
  REACHED=0
  for i in $(seq 1 300); do
    [ -e "$T/gh.reached" ] && { REACHED=1; break; }
    kill -0 "$j" 2>/dev/null || break
    sleep 0.2
  done
  t0=$(date +%s)
  for i in $(seq 1 300); do kill -0 "$j" 2>/dev/null || break; sleep 0.2; done   # 60 s to end: a hang is caught here
  ELAPSED=$(( $(date +%s) - t0 ))
  if kill -0 "$j" 2>/dev/null; then kill -9 "$j" 2>/dev/null; wait "$j" 2>/dev/null; RC=137; return 1; fi
  wait "$j"; RC=$?
  [ -e "$T/gh.reached" ] && REACHED=1
  return 0
}
# (A) a grandchild holds the caller's pipe
A=0
if pass_ends 'sleep 20 & echo $! >> "$T_SLEEPS"'; then ok "A: the pass ends although a grandchild holds stdout (${ELAPSED}s after the reach)"; else ko "A: the pass hung (${ELAPSED}s after the reach)"; A=1; fi
# A pipe held by the grandchild would keep the pass for the 20 s the stub sleeps; the pass measured from the reach never
# waits on it. Under load only the bound moves, never the referent: one uncut sleep is 20 s.
[ "$ELAPSED" -lt 20 ] && ok "A: the pass does not wait for the grandchild (${ELAPSED}s after the reach < the 20s it sleeps)" || { ko "A: the pass took ${ELAPSED}s after the reach, as long as the grandchild sleeps"; A=1; }
[ "$REACHED" = 1 ] && ok "A: the pass reached the stubbed command" || { ko "A: the pass never reached gh: the case proves nothing"; A=1; }
[ "$A" = 1 ] && diag
# (B) the command itself hangs on its first call, so the run stays short and the deadline still cuts the call
B=0
if pass_ends 'n=$(cat "$T_COUNT" 2>/dev/null || echo 0); echo $((n+1)) > "$T_COUNT"; if [ "$n" = 0 ]; then sleep 20 & echo $! >> "$T_SLEEPS"; wait $!; fi'; then ok "B: the pass ends although the command hangs (${ELAPSED}s after the reach)"; else ko "B: the pass hung (${ELAPSED}s after the reach)"; B=1; fi
[ "$ELAPSED" -lt 20 ] && ok "B: the call is cut at its deadline (${ELAPSED}s after the reach < the 20s the stub sleeps)" || { ko "B: the pass took ${ELAPSED}s after the reach, as long as the stub sleeps"; B=1; }
/usr/bin/grep -qE "timed out" "$T/out" && ok "B: the missed deadline is named in the pass log" || { ko "B: nothing names the missed deadline"; B=1; }
[ "$REACHED" = 1 ] && ok "B: the pass reached the stubbed command" || { ko "B: the pass never reached gh: the case proves nothing"; B=1; }
[ "$B" = 1 ] && diag
# (C) a slow entry is not a missed deadline: the first call records only after 3 s, longer than the old 2 s deadline
C=0
if pass_ends 'n=$(cat "$T_COUNT_C" 2>/dev/null || echo 0); echo $((n+1)) > "$T_COUNT_C"; [ "$n" = 0 ] && sleep 3; echo "$*" >> "$T/gh.calls"; : > "$T/gh.reached"' 8 self; then ok "C: the pass survives a command that records 3 s after it starts (${ELAPSED}s after the reach)"; else ko "C: the pass hung (${ELAPSED}s after the reach)"; C=1; fi
[ "$REACHED" = 1 ] && ok "C: a slow entry is still read as a reach" || { ko "C: a 3 s entry was cut at the deadline: the pass never reached gh"; C=1; }
[ "$ELAPSED" -lt 20 ] && ok "C: the pass ends after the slow entry (${ELAPSED}s after the reach)" || { ko "C: the pass took ${ELAPSED}s after the reach"; C=1; }
[ "$C" = 1 ] && diag
# The cleanup kills the pids this fixture recorded, and only while that pid still runs the stub's sleep: a pid the machine
# reused belongs to someone else, and a process this suite did not start is never its to kill.
if [ -f "$T/sleeps" ]; then
  while read -r p; do
    case "$p" in ''|*[!0-9]*) continue;; esac
    /bin/ps -ww -o command= -p "$p" 2>/dev/null | /usr/bin/grep -qx 'sleep 20' && kill -9 "$p" 2>/dev/null
  done < "$T/sleeps"
fi
rm -rf "$T"; exit $fail
