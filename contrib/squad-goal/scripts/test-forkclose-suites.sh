#!/bin/bash
# Regression test for the fork gate's suite choice (#1409): the path table (gate-suites.sh), the gate honoring FG_SUITES, the GUI suite and the shell suites running side by side, and forkclose.sh choosing by the branch's diff. Stub gate, no emulator.
unset SG_E2E_DIR SG_E2E_BUILD_DIR SG_E2E_BUILD_CMD   # the real e2e checkouts of the caller must never be touched by a stub run
here="$(cd "$(dirname "$0")" && pwd)"; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export HOME="$T/home"; mkdir -p "$HOME"   # serial.sh's durable GUI kill switch lives at $HOME/.squad-gui-tests-off, so a private home is what keeps THIS machine's switch from answering for the fixture instead of the gate decision it asserts (the GUI command here is a stub, so no emulator can start either way)
cls() { printf '%s\n' "$@" | bash "$here/gate-suites.sh"; }
[ "$(cls contrib/squad-goal/scripts/a.sh contrib/squad-goal/scripts/test-a.sh)" = shell ] && ok "contrib/squad-goal/ alone is the shell suites" || ko "squad-goal: $(cls contrib/squad-goal/scripts/a.sh)"
[ "$(cls dashboard/hub.py tests/test_dashboard_x.py)" = python ] && ok "dashboard/ plus tests/ is the python tests" || ko "dashboard: $(cls dashboard/hub.py tests/test_dashboard_x.py)"
[ "$(cls tests/test_dashboard_x.py)" = python ] && ok "tests/ alone is the python tests" || ko "tests alone"
[ "$(cls dashboard/hub.py tests/test_gui_test_e2e.py)" = full ] && ok "a test_gui_test_* file is the full gate" || ko "gui_test"
[ "$(cls contrib/squad-goal/scripts/a.sh dashboard/hub.py)" = full ] && ok "two confined areas mixed is the full gate" || ko "mixed"
[ "$(cls contrib/squad-goal/scripts/a.sh README.md)" = full ] && ok "anything else is the full gate" || ko "other"
[ "$(cls)" = full ] && ok "no file list (doubt) is the full gate" || ko "empty"
[ "$(cls contrib/squad-goal-evil/x)" = full ] && ok "a lookalike directory is the full gate" || ko "lookalike"
# the gate honors FG_SUITES
mkdir -p "$T/g/contrib/squad-goal/scripts"; cp "$here/fork-gate.sh" "$here/serial.sh" "$T/g/contrib/squad-goal/scripts/"
printf 'touch %s/shell.started\nexit 0\n' "$T" > "$T/g/contrib/squad-goal/scripts/shell-suites.sh"
gate() { rm -f "$T"/*.ran "$T/shell.started"; (cd "$T/g" && SG_STATE="$T/lk" FG_SUITES=$1 SG_FORK_PY_CMD="touch $T/py.ran" bash contrib/squad-goal/scripts/fork-gate.sh "${2:-touch $T/gui.ran}" >/dev/null 2>&1); }
gate shell; [ ! -e "$T/gui.ran" ] && [ -e "$T/shell.started" ] && [ ! -e "$T/py.ran" ] && ok "FG_SUITES=shell runs the shell suites only" || ko "shell mode"
gate python; [ -e "$T/py.ran" ] && [ ! -e "$T/gui.ran" ] && [ ! -e "$T/shell.started" ] && ok "FG_SUITES=python runs the python tests only" || ko "python mode"
gate full; [ -e "$T/gui.ran" ] && [ -e "$T/shell.started" ] && [ -e "$T/py.ran" ] && ok "FG_SUITES=full runs the GUI suite, the shell suites and the pytest suite (#1822)" || ko "full mode"
gate "" ; [ -e "$T/gui.ran" ] && [ -e "$T/shell.started" ] && [ -e "$T/py.ran" ] && ok "no FG_SUITES is the full gate" || ko "default mode"
# the shell suites run beside the GUI suite: the GUI command only passes once the shell suites have started
wait4="for i in \$(seq 60); do [ -e $T/shell.started ] && exit 0; sleep 0.1; done; exit 1"
gate full "$wait4"; (cd "$T/g" && SG_STATE="$T/lk" FG_SUITES=full SG_FORK_PY_CMD=true bash contrib/squad-goal/scripts/fork-gate.sh "$wait4" >/dev/null 2>&1) && ok "shell suites run beside the GUI suite, not after it" || ko "shell suites waited for the GUI suite"
rm -f "$T/shell.started"; printf 'exit 1\n' > "$T/g/contrib/squad-goal/scripts/shell-suites.sh"
(cd "$T/g" && SG_STATE="$T/lk" FG_SUITES=full SG_FORK_PY_CMD=true bash contrib/squad-goal/scripts/fork-gate.sh "exit 3" >/dev/null 2>&1) && ko "a red gate exited 0" || ok "a red suite still fails the gate"
# forkclose.sh picks the suites from the branch's diff
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$here/forkclose.sh" "$here/forkonly.sh" "$here/fork-gate.sh" "$here/gate-suites.sh" "$here/serial.sh" "$T/s/"; cp $here/ghread.py "$T/s/"; cp $here/ghthrottle.py "$T/s/"
printf 'echo shell >> %s/shell.log; exit 0\n' "$T" > "$T/s/shell-suites.sh"
printf '#!/bin/bash\niss=$1; shift; [ "$1" = -C ] && { cd "$2"; shift 2; }; [ "$1" = -- ] && shift; "$@"; rc=$?; echo "TEST #$iss x test-$iss-x"; exit $rc\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_OWNER=x SG_PROJECT=4 BOARD=true\nexport SG_FORK_TEST_CMD="echo gui >> %s/gui.log"\nexport SG_FORK_PY_CMD=true\n' "$T/st" "$T/fork" "$T/co" "$T" > "$T/s/env.sh"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; mkdir -p "$T/fork/contrib/squad-goal" "$T/fork/dashboard"; echo 0 > "$T/fork/contrib/squad-goal/f"; echo 0 > "$T/fork/dashboard/d"; g add .; g commit -q -m init
g checkout -q -b gui/1 local/next; echo 1 >> "$T/fork/contrib/squad-goal/f"; g commit -qam "squad-goal only"; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
printf '#!/bin/bash\ncase "$*" in\n  *"issues/"*" -q .state"*) echo open;;\n\n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
FC_NOBATCH=1 bash "$T/s/forkclose.sh" >/dev/null 2>&1
[ "$(wc -l < "$T/gui.log" 2>/dev/null | tr -d ' ')" = 1 ] && ok "squad-goal-only branch: the GUI suite ran once (the base test), not for the branch" || ko "gui runs: $(cat "$T/gui.log" 2>/dev/null | wc -l)"
[ "$(wc -l < "$T/shell.log" 2>/dev/null | tr -d ' ')" = 2 ] && ok "squad-goal-only branch: the shell suites ran for the base and for the branch" || ko "shell runs: $(cat "$T/shell.log" 2>/dev/null | wc -l)"
rm -rf "$T"
# #15: the set that gates a LANDING is read off the landing's own change to the base, never off the branch's diff at its fork point
# (git diff --name-only $(git merge-base $1 $2) $2). A branch that only touched contrib/squad-goal/ is a shell branch by fork point --
# and the landing that restores the spec a reverted merge took, a file that branch's own diff never named, is not: the gate has to
# answer for the tree the landing just made. The base test keeps the rule it always had (basesuites(): the base's OWN diff since the
# last base recorded green), the log line names which rule answered, and this case drives both side by side in one pass.
T=$(mktemp -d); mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin" "$T/co/runs/.hub" "$T/fork"
export HOME="$T/home"; mkdir -p "$HOME"
cp "$here/forkclose.sh" "$here/forkonly.sh" "$here/fork-gate.sh" "$here/gate-suites.sh" "$here/serial.sh" "$T/s/"
printf 'echo shell >> %s/shell.log; exit 0\n' "$T" > "$T/s/shell-suites.sh"
printf '#!/bin/bash\niss=$1; shift; [ "$1" = -C ] && { cd "$2"; shift 2; }; [ "$1" = -- ] && shift; "$@"; rc=$?; echo "TEST #$iss x test-$iss-x"; exit $rc\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_OWNER=x SG_PROJECT=4 BOARD=true\nexport SG_FORK_TEST_CMD="echo gui >> %s/gui.log"\nexport SG_FORK_PY_CMD="echo py >> %s/py.log"\n' "$T/st" "$T/fork" "$T/co" "$T" "$T" > "$T/s/env.sh"
g init -q -b local/next; mkdir -p "$T/fork/contrib/squad-goal" "$T/fork/tests"; echo 0 > "$T/fork/contrib/squad-goal/f"; g add .; g commit -q -m init
g checkout -q -b gui/9 local/next; echo 0 > "$T/fork/tests/test_spec.py"; g add .; g commit -qam "the merge that brings a spec"; g checkout -q local/next
g merge -q --no-ff -m "Merge gui/9 (Refs #9)" gui/9; rv=$(g rev-parse HEAD)
g checkout -q -b gui/1 local/next; echo 1 >> "$T/fork/contrib/squad-goal/f"; g commit -qam "squad-goal only"; g checkout -q local/next   # forked HERE, while the base still holds the spec: its own diff is squad-goal only
g revert -m 1 --no-edit "$rv" >/dev/null   # the base lost tests/test_spec.py to that revert; gui/1 still carries it, so a landing restores a file the branch's own diff never named
touch "$T/st/auto/basetest-$(g rev-parse --short=9 local/next)"   # a base recorded green there: the base test answers from the base's OWN diff since it
printf 'base\n' > "$T/fork/contrib/squad-goal/g"; g add .; g commit -q -m "a base-only squad-goal change"   # so the base's own answer is shell, not full
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
printf '#!/bin/bash\ncase "$*" in\n  *"issues/"*" -q .state"*) echo open;;\nesac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
out=$(FC_NOBATCH=1 bash "$T/s/forkclose.sh" 2>&1)
case "$out" in *"full by the landing rule"*) ok "#15: the landing gate answers from the landing's own change, not from the branch's fork point";; *) ko "#15: no landing-rule line in: $out";; esac
case "$out" in *"the branch's fork-point diff answered shell"*) ok "#15: the log line names which rule answered, and here the two disagree";; *) ko "#15: the rule that answered is not named: $out";; esac
[ -e "$T/fork/tests/test_spec.py" ] && ok "#15: the resolution restored the spec the branch's own diff never named" || ko "#15: the landing did not carry tests/test_spec.py"
[ "$(wc -l < "$T/gui.log" 2>/dev/null | tr -d ' ')" = 1 ] && ok "#15: the landing gate is full (it carries tests/test_spec.py); the base test's own rule asked for no GUI suite" || ko "#15: gui runs: $(cat "$T/gui.log" 2>/dev/null | wc -l)"
[ "$(wc -l < "$T/shell.log" 2>/dev/null | tr -d ' ')" = 2 ] && ok "#15: basesuites() answered shell for the base (the base's own diff) while the landing answered full: both rules side by side" || ko "#15: shell runs: $(cat "$T/shell.log" 2>/dev/null | wc -l)"
[ "$(wc -l < "$T/py.log" 2>/dev/null | tr -d ' ')" = 1 ] && ok "#15: the full landing gate ran the pytest suite; the base test (shell) did not" || ko "#15: py runs: $(cat "$T/py.log" 2>/dev/null | wc -l)"
rm -rf "$T"; exit $fail
