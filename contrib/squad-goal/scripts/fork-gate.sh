#!/bin/bash
# usage: fork-gate.sh "<fork GUI suite command>"   (cwd = the fork checkout; forkclose.sh runs it as the fork gate)
# The gate of a fork branch is the GUI suite ($SG_FORK_TEST_CMD) AND the squad's own shell suites (shell-suites.sh): a branch
# that edits contrib/squad-goal/scripts is otherwise gated by a suite that never looks at them (#1392). Both always run, so one
# red never hides the other; the GUI suite runs under the gui-suite lock, the one forkclose holds while it rebuilds the e2e emulator (#1410); the exit is non-zero when either fails.
here="$(cd "$(dirname "$0")" && pwd)"; gui=${1:-true}; rc=0
echo "=== fork gate: GUI suite: $gui"; bash "$here/serial.sh" gui-suite bash -c "$gui" || { g=$?; echo "--- fork gate: GUI suite FAILED (exit $g)"; rc=1; }
s="$PWD/contrib/squad-goal/scripts/shell-suites.sh"; [ -f "$s" ] || s="$here/shell-suites.sh"   # the merged tree's copy, so the branch's own edits are what runs
echo "=== fork gate: squad shell suites"; bash "$s" || { echo "--- fork gate: shell suites FAILED"; rc=1; }
exit $rc
