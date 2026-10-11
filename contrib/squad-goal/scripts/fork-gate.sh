#!/bin/bash
# usage: fork-gate.sh "<fork GUI suite command>"   (cwd = the fork checkout; forkclose.sh runs it as the fork gate)
# The gate of a fork branch is the GUI suite ($SG_FORK_TEST_CMD) AND the squad's own shell suites (shell-suites.sh): a branch
# that edits contrib/squad-goal/scripts is otherwise gated by a suite that never looks at them (#1392). Both always run, so one
# red never hides the other; the exit is non-zero when either fails.
here="$(cd "$(dirname "$0")" && pwd)"; gui=${1:-true}; rc=0
# FG_SUITES (gate-suites.sh decides it from the branch's diff): full (default, any doubt) | shell | python. Only the full gate runs the GUI suite.
suites=${FG_SUITES:-full}; s="$PWD/contrib/squad-goal/scripts/shell-suites.sh"; [ -f "$s" ] || s="$here/shell-suites.sh"   # the merged tree's copy, so the branch's own edits are what runs
if [ "$suites" = python ]; then
  py=${SG_FORK_PY_CMD:-uv run --extra dev pytest tests -q --ignore-glob='tests/test_gui_test_*'}
  echo "=== fork gate: python tests: $py"; eval "$py" || { echo "--- fork gate: python tests FAILED"; rc=1; }; exit $rc
fi
# the shell suites are offline and need no emulator, so they run beside the GUI suite (which holds the serial lock itself), not after it
sl=$(mktemp); bash "$s" > "$sl" 2>&1 & sp=$!
if [ "$suites" != shell ]; then echo "=== fork gate: GUI suite: $gui"; eval "$gui" || { g=$?; echo "--- fork gate: GUI suite FAILED (exit $g)"; rc=1; }; fi
echo "=== fork gate: squad shell suites"; wait "$sp" || { shr=$?; echo "--- fork gate: shell suites FAILED"; rc=1; }
cat "$sl"; rm -f "$sl"; exit $rc
