#!/bin/bash
# usage: fork-gate.sh "<fork GUI suite command>"   (cwd = the fork checkout; forkclose.sh runs it as the fork gate)
# The gate of a fork branch is the GUI suite ($SG_FORK_TEST_CMD), the squad's own shell suites (shell-suites.sh) -- a branch
# that edits contrib/squad-goal/scripts is otherwise gated by a suite that never looks at them (#1392) -- and the pytest suite
# (#1822: a diff touching tests/test_gui_test_*.py is mapped to `full`, and the unit tests it rides with used to never run, so
# #1809 shipped an inconsistent test). Every suite of the class always runs, so one red never hides another; the GUI suite runs
# under the gui-suite lock, the one forkclose holds while it rebuilds the e2e emulator (#1410); the exit is non-zero when any of them fails.
# The gate grades a tree of its own (#99): HEAD is checked out into a detached worktree and every suite runs there, so an
# uncommitted edit beside the landing never decides the verdict; the checkout's dirty paths are reported once, by path.
# One full gate per machine (#81): the suite phase -- the shell suites and the pytest suite this class needs -- runs inside ONE
# `serial.sh gate` holder, so a second full gate (the land pass and a child's own suite run alike) waits for it, prints one wait
# line and starts when it ends, instead of both taking every core. The GUI suite stays outside that holder: it takes gui-suite and
# runs beside the phase, and holding `gate` for the emulator's minutes would make every other run wait for the emulator.
here="$(cd "$(dirname "$0")" && pwd)"; gui=${1:-true}; rc=0; G=/usr/bin/git
# The first five paths, then a count: the shape forkclose.sh's own WAIT line uses, so a long list never fills the log.
namelist() { local n; n=$(printf '%s\n' "$@" | grep -c .)
  printf '%s' "$(printf '%s\n' "$@" | grep . | head -5 | paste -sd, - | sed 's/,/, /g')"; [ "$n" -gt 5 ] && printf ' and %d more (%d paths)' $(( n - 5 )) "$n"; return 0; }
# (#99) The gate grades a TREE OF ITS OWN, never the checkout the landing sits in. forkclose.sh runs it in $SG_FORK_DIR --
# the owner's working tree, with the merge already landed there -- so every suite used to read that tree PLUS whatever the
# owner or a child had edited and not committed, and the verdict was about the checkout instead of the tree that was
# landing. Three merges were reverted over work no branch carried (Revert "Merge gui/72", gui/83, gui/84): the #84 gate log
# failed two tests/test_hub_machine_load.py and six tests/test_model_fallthrough.py tests, while gui/84 touched
# squad/engine.py and two test files, and the base 3b5186d passes both files. So HEAD is checked out into a detached
# worktree of its own and every suite runs there. The checkout's uncommitted paths are a FACT the gate reports once, by
# path -- never a verdict it trusts. No commit to check out (no repository, or a fixture with none): graded where it
# stands, exactly as before.
orig=$PWD; wt=""
if $G -C "$orig" rev-parse -q --verify HEAD >/dev/null 2>&1; then
  t=$(mktemp -d); rmdir "$t" 2>/dev/null   # the shape rebasestale() uses: `worktree add` wants the path absent
  if $G -C "$orig" worktree add -q --detach "$t" HEAD >/dev/null 2>&1; then
    wt=$t
    # The one thing a tree cannot bring with it is the project's own environment: uv builds a fresh .venv per tree, and a gate
    # that reinstalls the dev extras on every landing is not a gate anybody keeps. A venv is machine state, never graded
    # content, so the checkout's is linked in for uv to find; a tree whose HEAD carries an environment of its own keeps it.
    [ -d "$orig/.venv" ] && [ ! -e "$wt/.venv" ] && ln -s "$orig/.venv" "$wt/.venv" 2>/dev/null
    cleanup() { cd / 2>/dev/null; rm -rf "$wt" 2>/dev/null; $G -C "$orig" worktree prune >/dev/null 2>&1; }
    trap cleanup EXIT
    cd "$wt" || exit 1
    echo "=== fork gate: grades $($G -C "$wt" rev-parse --short=9 HEAD) in $wt, a tree of its own"
    d=$($G -C "$orig" status --porcelain --untracked-files=no 2>/dev/null | awk '{print substr($0,4)}')
    [ -z "$d" ] || echo "=== fork gate: NOTE $orig is not the graded tree; it has uncommitted changes ($(namelist $d)), and they are not graded"
  fi
fi
# FG_SUITES (gate-suites.sh decides it from the branch's diff): full (default, any doubt) | shell | python. Only the full gate runs the GUI suite; the full and python gates run the pytest suite, the full and shell gates the shell suites.
suites=${FG_SUITES:-full}; s="$PWD/contrib/squad-goal/scripts/shell-suites.sh"; [ -f "$s" ] || s="$here/shell-suites.sh"   # the graded tree's copy, so the branch's own edits are what runs
py=${SG_FORK_PY_CMD:-uv run --extra dev pytest tests -q --ignore-glob='tests/test_gui_test_*'}
if [ "$suites" = python ]; then
  echo "=== fork gate: python tests: $py"; bash "$here/serial.sh" gate bash -c "$py" || { echo "--- fork gate: python tests FAILED"; rc=1; }; exit $rc
fi
# the shell suites and the pytest suite are offline and need no emulator, so they run beside the GUI suite (which holds the gui-suite lock itself), not after it
sl=$(mktemp); pl=""; [ "$suites" = shell ] || pl=$(mktemp); rcf=$(mktemp)
# The phase's own shell, so the two suites run side by side inside the holder as before: each one's exit is written to rcf, which is
# what lets the gate name the half that failed. Both inherit SG_SERIAL_HELD, so a suite that runs the gate inside one waits for nobody.
phase='
  bash "$FG_SHELL" > "$FG_SL" 2>&1 & a=$!
  if [ -n "$FG_PL" ]; then eval "$FG_PY" > "$FG_PL" 2>&1 & b=$!; fi
  wait "$a" || echo shell >> "$FG_RC"
  if [ -n "$FG_PL" ]; then wait "$b" || echo py >> "$FG_RC"; fi
'
FG_SHELL=$s FG_PY=$py FG_SL=$sl FG_PL=$pl FG_RC=$rcf bash "$here/serial.sh" gate bash -c "$phase" & ph=$!
if [ "$suites" != shell ]; then echo "=== fork gate: GUI suite: $gui"; bash "$here/serial.sh" gui-suite bash -c "$gui" || { g=$?; echo "--- fork gate: GUI suite FAILED (exit $g)"; rc=1; }; fi
wait "$ph"
echo "=== fork gate: squad shell suites"; cat "$sl"; rm -f "$sl"
if /usr/bin/grep -qx shell "$rcf"; then echo "--- fork gate: shell suites FAILED"; rc=1; fi
if [ -n "$pl" ]; then echo "=== fork gate: python tests: $py"; cat "$pl"; rm -f "$pl"; if /usr/bin/grep -qx py "$rcf"; then echo "--- fork gate: python tests FAILED"; rc=1; fi; fi
rm -f "$rcf"
exit $rc
