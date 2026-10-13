#!/bin/bash
# usage: fork-gate.sh "<fork GUI suite command>"   (cwd = the fork checkout; forkclose.sh runs it as the fork gate)
# The gate of a fork branch is the GUI suite ($SG_FORK_TEST_CMD), the squad's own shell suites (shell-suites.sh) -- a branch
# that edits contrib/squad-goal/scripts is otherwise gated by a suite that never looks at them (#1392) -- and the pytest suite
# (#1822: a diff touching tests/test_gui_test_*.py is mapped to `full`, and the unit tests it rides with used to never run, so
# #1809 shipped an inconsistent test). Every suite of the class always runs, so one red never hides another; the GUI suite runs
# under the gui-suite lock, the one forkclose holds while it rebuilds the e2e emulator (#1410); the exit is non-zero when any of them fails.
here="$(cd "$(dirname "$0")" && pwd)"; gui=${1:-true}; rc=0
# FG_SUITES (gate-suites.sh decides it from the branch's diff): full (default, any doubt) | shell | python. Only the full gate runs the GUI suite; the full and python gates run the pytest suite, the full and shell gates the shell suites.
suites=${FG_SUITES:-full}; s="$PWD/contrib/squad-goal/scripts/shell-suites.sh"; [ -f "$s" ] || s="$here/shell-suites.sh"   # the merged tree's copy, so the branch's own edits are what runs
py=${SG_FORK_PY_CMD:-uv run --extra dev pytest tests -q --ignore-glob='tests/test_gui_test_*'}
if [ "$suites" = python ]; then
  echo "=== fork gate: python tests: $py"; eval "$py" || { echo "--- fork gate: python tests FAILED"; rc=1; }; exit $rc
fi
# the shell suites and the pytest suite are offline and need no emulator, so they run beside the GUI suite (which holds the serial lock itself), not after it
sl=$(mktemp); bash "$s" > "$sl" 2>&1 & sp=$!
pl=""; if [ "$suites" != shell ]; then pl=$(mktemp); eval "$py" > "$pl" 2>&1 & pp=$!; fi
if [ "$suites" != shell ]; then echo "=== fork gate: GUI suite: $gui"; bash "$here/serial.sh" gui-suite bash -c "$gui" || { g=$?; echo "--- fork gate: GUI suite FAILED (exit $g)"; rc=1; }; fi
echo "=== fork gate: squad shell suites"; wait "$sp" || { shr=$?; echo "--- fork gate: shell suites FAILED"; rc=1; }
cat "$sl"; rm -f "$sl"
if [ -n "$pl" ]; then echo "=== fork gate: python tests: $py"; wait "$pp" || { echo "--- fork gate: python tests FAILED"; rc=1; }; cat "$pl"; rm -f "$pl"; fi
exit $rc
