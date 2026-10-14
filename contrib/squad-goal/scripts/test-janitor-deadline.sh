#!/bin/bash
# ADR-0051: a call has a deadline. The janitor runs commands whose child may leave a grandchild behind, and a command may
# hang. Neither may hold the pass: the module runs every command into a file (never a pipe) and cuts it at its deadline.
#  (A) the stub `gh` starts `sleep` in the background: the child exits while a pipe-based caller would keep reading;
#  (B) the stub `gh` sleeps in the foreground: the call itself misses the deadline, and the pass names it.
# Stubbed gh/board, a temp state dir; the live loop is never read or written. usage: bash test-janitor-deadline.sh [janitor.py]
# The suite declares its own world (#393): the loop's own shell exports SG_* (SG_BOARD_VOCAB=shipped, SG_FORK_DIR, SG_LAND_BRANCH,
# ...), and those reached the janitor below, which then took the shipped path and its fork rules instead of the fixture's. A hand
# run went red on the owner's machine where shell-suites.sh -- which clears every SG_* for the gate -- stayed green.
for v in $(env | /usr/bin/grep -o '^SG_[A-Za-z0-9_]*'); do unset "$v"; done
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs" "$T/caps/runs" "$T/caps/children"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"
# Rule O reads the card of an issue whose dispatch marker a run left behind, so a pass reaches gh.
echo run-pr > "$T/st/active-runs.txt"; mkdir -p "$T/co/runs/run-pr"; : > "$T/st/auto/disp-901"
printf 'CHILD — GitHub issue #901 (bug; one PR):\n' > "$T/co/runs/run-pr/request.txt"
printf '{"event": "verdict", "verdict": "needs-human", "reasons": ["x"], "exit_code": 0}\n' > "$T/co/runs/run-pr/record.jsonl"
pass_ends() {   # $1 = the stub body; the pass runs at JANITOR_CALL_TIMEOUT=2 and is killed if it outlives 15 s
  printf '#!/bin/bash\necho "$*" >> "%s/gh.calls"\n%s\n' "$T" "$1" > "$T/bin/gh"; chmod +x "$T/bin/gh"; : > "$T/gh.calls"
  PATH="$T/bin:$PATH" SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=x SG_PROJECT=1 SG_BASE=main BOARD="$T/s/board.sh" \
    JANITOR_DRY=1 JANITOR_NOTIFY=/bin/true JANITOR_CALL_TIMEOUT=2 SQUAD_CAPS_DIR="$T/caps" python3 -I "$T/s/janitor.py" > "$T/out" 2>&1 &
  local j=$! t0=$(date +%s)
  for i in $(seq 1 150); do kill -0 "$j" 2>/dev/null || break; sleep 0.2; done
  ELAPSED=$(( $(date +%s) - t0 ))
  if kill -0 "$j" 2>/dev/null; then kill -9 "$j" 2>/dev/null; return 1; fi
  return 0
}
# (A) a grandchild holds the caller's pipe
if pass_ends 'sleep 20 &'; then ok "A: the pass ends although a grandchild holds stdout (${ELAPSED}s)"; else ko "A: the pass hung (${ELAPSED}s)"; fi
[ "$ELAPSED" -le 15 ] && ok "A: the deadline bounds the pass (${ELAPSED}s <= 15s)" || ko "A: the pass took ${ELAPSED}s"
[ -s "$T/gh.calls" ] && ok "A: the pass reached the stubbed command" || ko "A: the pass never reached gh: the case proves nothing"
# (B) the command itself hangs
if pass_ends 'sleep 20'; then ok "B: the pass ends although the command hangs (${ELAPSED}s)"; else ko "B: the pass hung (${ELAPSED}s)"; fi
[ "$ELAPSED" -le 15 ] && ok "B: the call is cut at the deadline (${ELAPSED}s <= 15s)" || ko "B: the pass took ${ELAPSED}s"
/usr/bin/grep -qE "timed out" "$T/out" && ok "B: the missed deadline is named in the pass log" || ko "B: nothing names the missed deadline: $(head -c 200 "$T/out")"
pkill -f "sleep 20" 2>/dev/null
rm -rf "$T"; exit $fail
