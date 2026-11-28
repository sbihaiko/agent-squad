#!/bin/bash
# seconds: 13.68
# A beside-script fixture owns only the processes it starts, and a failing case shows the pass it judged (ADR-0018, ADR-0051).
#  (A) an unrelated `sleep 20` on the machine survives test-janitor-deadline.sh: its cleanup kills the pids of the sleeps its
#      own stub started, never every process whose command line matches. Two copies of the fixture run at once under a parallel
#      gate (test-janitor-env-hermetic.sh runs it too), and one copy's global `pkill -f "sleep 20"` killed the other copy's
#      hanging stub, so that copy stopped hanging and the suite named a defect the product did not have.
#  (B) a case that fails prints the janitor's own output and exit code. The deadline suite once reported only that the pass
#      never reached the stubbed command, with no evidence and no next step.
#  (C) the deadline suite declares its world, the working directory too: from a folder outside the repository it is green.
#      It reads the janitor's support module through the tree it runs from, so a caller's working directory once decided
#      whether the pass reached its first command: the day pass ran the suite outside the repository, the janitor died at
#      `_procs_module()`, and three cases named a reach that never happened.
# usage: bash test-janitor-cleanup.sh [deadline suite]
here=$(cd "$(dirname "$0")" && pwd); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
D=${1:-$here/test-janitor-deadline.sh}; D=$(cd "$(dirname "$D")" && pwd)/$(basename "$D")   # (C) reads it from another folder
# (A) the fixture's cleanup kills only its own sleeps
sleep 20 & other=$!   # a process of this suite, not of the deadline suite: no other fixture may reach it
o=$(bash "$D" 2>&1); rc=$?
if kill -0 "$other" 2>/dev/null; then ok "A: an unrelated 'sleep 20' survives the deadline suite's cleanup"; else
  ko "A: the deadline suite killed a process it did not start (its cleanup reaches every matching process on the machine)"
fi
kill "$other" 2>/dev/null; wait "$other" 2>/dev/null
chk=$(printf '%s' "$o" | /usr/bin/grep -cE '^FAIL'); [ "$chk" = 0 ] && ok "A: the deadline suite itself is green beside an unrelated sleep" || ko "A: the deadline suite reported $chk FAIL: $(printf '%s' "$o" | /usr/bin/grep -E '^FAIL' | head -3 | tr '\n' ' ')"
# (B) a failing pass is read from its own output and exit code, not guessed
S=$(mktemp -d)
printf '#!/usr/bin/env python3\nprint("STUB-JANITOR-OUTPUT")\nraise SystemExit(3)\n' > "$S/janitor.py"
cp "$here/issue_state.py" "$S/issue_state.py"
o=$(bash "$D" "$S/janitor.py" 2>&1); rc=$?
[ "$rc" != 0 ] && ok "B: a case that proves nothing fails the suite (exit $rc)" || ko "B: the suite passed although the pass never reached the stubbed command"
case "$o" in *STUB-JANITOR-OUTPUT*) ok "B: a failure prints the janitor's own output";; *) ko "B: a failure printed no janitor output: $(printf '%s' "$o" | tail -3 | tr '\n' ' ')";; esac
case "$o" in *"janitor exited 3"*) ok "B: a failure prints the janitor's exit code";; *) ko "B: a failure printed no exit code: $(printf '%s' "$o" | tail -3 | tr '\n' ' ')";; esac
rm -rf "$S"
# (C) the fixture reads no caller working directory: from a folder outside the repository the deadline suite is green
F=$(mktemp -d)
o=$(cd "$F" && bash "$D" 2>&1); rc=$?
if [ "$rc" = 0 ]; then ok "C: the deadline suite is green from a working directory outside the repository"; else
  ko "C: the deadline suite went red from a working directory outside the repository (exit $rc): $(printf '%s' "$o" | /usr/bin/grep -E '^FAIL' | head -3 | tr '\n' ' ')"; fi
rm -rf "$F"; exit $fail
