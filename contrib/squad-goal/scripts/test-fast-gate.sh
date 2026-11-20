#!/bin/bash
# seconds: 196.13
# (#1735) The hang a deadline case starts sleeps a length only this run names, so the survivor probe and the cleanup
# below read this suite's own processes: `pgrep -f "sleep 2718"` read every suite on the machine, and two gates side
# by side then read each other's hang as a survivor, or ended it.
export HANG_NAP=$(( 271800000 + $$ ))
# Regression test for fast-gate.sh: the fast gate of a work-branch tip or a wave entry is the base battery (ruff, mypy, the fast
# pytest) and then the shell suites of the scripts the tip changes against the merge-base, all under the machine lock, with one
# status line for the hub. A throwaway repository, a stub base command, stub suites: nothing here runs a real battery.
# usage: bash test-fast-gate.sh [scripts dir]
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so the hold is proved
# gate.sh exports GATE_REUSE_FILE to the command it runs, so a suite under a gate inherits it: a memo hit of this suite then marks
# the outer gate's row instead of writing its own, and the reuse row it reads is missing. The suite's gates read no outer marker.
unset GATE_REUSE_FILE
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; export SG_STATE=$T/st SQUAD_CAPS_DIR=$T/caps; mkdir -p "$SG_STATE"   # (#801) the gate takes slots: its own budget, never the machine's
export SG_FAST_LOG_DIR=$T/logs; mkdir -p "$SG_FAST_LOG_DIR"   # (#945) the gate's log sits outside SG_STATE, where the child that ran it may read it
export SG_FAST_MEMO=0   # the pass memo is proved in its own block at the end; every other case here runs a fresh battery
# (#2421) The daily pass and shell-suites.sh hand the suite a temp HOME (#2307), and this machine's python3 imports pytest's
# pygments only from the user site under HOME: the trial battery a case starts then cannot import pytest at all. The real
# user base is read, never written (test-hubstream.sh).
export PYTHONUSERBASE="${PYTHONUSERBASE:-$(HOME="$(eval echo "~$(id -un)")" python3 -c 'import site;print(site.USER_BASE)' 2>/dev/null)}"
R=$T/r; S=$R/contrib/squad-goal/scripts; git init -q -b dev "$R"; mkdir -p "$S" "$R/docs"
# A stub suite logs its name and whether the machine lock was held, and fails when its name is in $SUITE_FAIL.
for n in foo bar baz; do
  printf '#!/bin/bash\necho "test-%s.sh lock=$([ -d "%s/locks/gate" ] && echo held || echo free)" >> "%s/ran.log"\ncase " $SUITE_FAIL " in *" test-%s.sh "*) exit 1;; esac\n' "$n" "$SG_STATE" "$T" "$n" > "$S/test-$n.sh"
  printf '#!/bin/bash\n# %s\n' "$n" > "$S/$n.sh"; done
rm "$S/test-baz.sh"   # baz has no suite
echo base > "$R/docs/x.md"; $G -C "$R" add -A; $G -C "$R" commit -q -m base

# A Python script selects its shell suite. baz.py has no suite and the docs change is outside scripts.
$G -C "$R" checkout -q -b gui/py; echo 1 >> "$S/foo.py"; echo 1 >> "$S/baz.py"; echo 1 >> "$R/docs/x.md"; $G -C "$R" add -A; $G -C "$R" commit -q -m python-work
py_tip=$(git -C "$R" rev-parse HEAD)
printf '#!/bin/bash\necho "base-cmd lock=$([ -d "%s/locks/gate" ] && echo held || echo free)" >> "%s/ran.log"\n[ "$BASE_FAIL" = 1 ] && echo "FAILED tests/test_x.py::test_a - boom"\n[ -z "$BASE_FAIL" ]\n' "$SG_STATE" "$T" > "$T/base.sh"
fg() { rm -f "$T/ran.log" "$SG_STATE/gate-status"; (cd "$R" && SG_FAST_BASE=dev SG_FAST_CMD="bash $T/base.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1); }

out=$(fg); rc=$?
[ $rc = 0 ] && ok "a tip whose Python script suite passes exits 0" || ko "Python pass: rc $rc: $out"
[ "$(cat "$T/ran.log" 2>/dev/null)" = "base-cmd lock=held
test-foo.sh lock=held" ] && ok "a Python script runs its suite, while a script with no suite and a file outside scripts add none" || ko "Python ran: '$(cat "$T/ran.log" 2>&1)'"
IFS=$'\t' read -r _ kind res at < "$SG_STATE/gate-status" 2>/dev/null
[ "$kind $res $at" = "fast pass $py_tip" ] && ok "the status line names the fast gate, its result and the Python tip" || ko "status line: '$(cat "$SG_STATE/gate-status" 2>&1)'"

out=$(SUITE_FAIL=test-foo.sh fg); rc=$?
[ $rc -ne 0 ] && case "$out" in *test-foo.sh*) true;; *) false;; esac && ok "a red suite selected by a Python script fails the gate and is named" || ko "Python red suite: rc $rc: $out"
IFS=$'\t' read -r _ kind res at < "$SG_STATE/gate-status" 2>/dev/null
[ "$kind $res" = "fast fail" ] && ok "a red suite leaves a fail line" || ko "status line: '$(cat "$SG_STATE/gate-status" 2>&1)'"

out=$(BASE_FAIL=1 fg); rc=$?
[ $rc -ne 0 ] && [ "$(cat "$T/ran.log")" = "base-cmd lock=held" ] && ok "a red base battery fails the gate and no suite runs" || ko "red base: rc $rc: '$(cat "$T/ran.log" 2>&1)'"

# A test that fails under the parallel battery and passes alone is saturation, not a defect (#520): the failed tests run once
# more, serially (SG_FAST_RETRY_CMD). Green there, the gate goes on and names it; red there too, the gate fails as before.
printf '#!/bin/bash\necho "retry-cmd $*" >> "%s/ran.log"\n[ -z "$RETRY_FAIL" ]\n' "$T" > "$T/retry.sh"
fgr() { rm -f "$T/ran.log" "$SG_STATE/gate-status"; (cd "$R" && SG_FAST_BASE=dev SG_FAST_CMD="bash $T/base.sh" SG_FAST_RETRY_CMD="bash $T/retry.sh" bash "$d/fast-gate.sh" 2>&1); }
out=$(BASE_FAIL=1 fgr); rc=$?
[ $rc = 0 ] && ok "a base battery red only under load passes once its failed tests pass alone" || ko "saturation retry: rc $rc: $out"
case "$out" in *saturation*) ok "the gate names the saturation it passed over";; *) ko "the retry is silent: $out";; esac
/usr/bin/grep -qx "retry-cmd tests/test_x.py::test_a" "$T/ran.log" && /usr/bin/grep -q "test-foo.sh" "$T/ran.log" && ok "the suites still run after a passed retry" || ko "ran: '$(cat "$T/ran.log")'"
out=$(BASE_FAIL=1 RETRY_FAIL=1 fgr); rc=$?
[ $rc -ne 0 ] && ! /usr/bin/grep -q "test-foo.sh" "$T/ran.log" && ok "a failed test that fails alone too fails the gate" || ko "retry red: rc $rc: '$(cat "$T/ran.log")'"
out=$(fgr); /usr/bin/grep -q "retry-cmd" "$T/ran.log" && ko "a green battery ran the retry" || ok "a green battery runs no retry"
# pyproject turns the pytest cache off, so --lf names nothing: the retry runs the failed tests the log names, never the battery.
out=$(BASE_FAIL=lint fgr); rc=$?
[ $rc -ne 0 ] && ! /usr/bin/grep -q "retry-cmd" "$T/ran.log" && ok "a red that names no failed test runs no id retry and fails" || ko "lint red: rc $rc: '$(cat "$T/ran.log")'"

# (#670) A red that is lint or types names the check it failed at, with its own line. A reason that reads "names no failed test"
# hides which check failed, and the child that must repair it never learns which one it was.
printf '#!/bin/bash\n[ -n "$RUFF_FAIL" ] && { echo "F401 [*] os imported but unused"; echo "  --> tests/test_hub_models_v2.py:8:8"; echo "Found 1 error."; exit 1; }\n[ -n "$RUFF_BARE" ] && { echo "F811 Redefinition of unused hub from line 13"; echo "   --> tests/test_models_v2.py:175:60"; echo "Found 4 errors."; exit 1; }\n[ -n "$MYPY_FAIL" ] && { echo "squad/x.py:12: error: bad type"; exit 1; }\nexit 0\n' > "$T/lint.sh"
lintgate() { rm -f "$T/ran.log" "$SG_STATE/gate-status"; (cd "$R" && SG_FAST_BASE=dev SG_FAST_CMD="bash $T/lint.sh" bash "$d/fast-gate.sh" 2>&1); }
out=$(RUFF_FAIL=1 lintgate); rc=$?
[ $rc -ne 0 ] && case "$out" in *"ruff:"*"tests/test_hub_models_v2.py"*"F401"*) true;; *) false;; esac && ok "a lint red names ruff, its path and its code" || ko "ruff red: rc $rc: $out"
# (#671) A ruff code with no fix marker, `[*]`, is ruff too, never pytest.
out=$(RUFF_BARE=1 lintgate); rc=$?
[ $rc -ne 0 ] && case "$out" in *"ruff:"*"tests/test_models_v2.py"*"F811"*) true;; *) false;; esac && ok "a lint red with no fix marker names ruff, its path and its code" || ko "bare ruff red: rc $rc: $out"
out=$(MYPY_FAIL=1 lintgate); rc=$?
[ $rc -ne 0 ] && case "$out" in *"mypy"*"squad/x.py:12"*) true;; *) false;; esac && ok "a type red names mypy and its line" || ko "mypy red: rc $rc: $out"

# (#699) The last line names the failing class: lint, type, collection, pytest (with the failed ids), saturation or unknown. A red
# that names no failed test is no longer read as one cause. Saturation and unknown are inconclusive: the battery runs once more,
# and a second inconclusive result exits 4, which holds the landing. Exit 1 is a defect. No class grants landing by its name.
last() { printf '%s\n' "$1" | /usr/bin/grep -v '^$' | tail -1; }
# lgof <pass or red line>: the log the line names, whatever the line's shape: the pass line `(log <path>)`, a red `(<path>)`.
lgof() { printf '%s\n' "$1" | /usr/bin/grep -o '([^()]*\.log)' | tail -1 | tr -d '()' | sed 's/^log //'; }
out=$(RUFF_FAIL=1 lintgate); case "$(last "$out")" in "fast-gate: lint: "*) ok "a lint red names the lint class on its last line";; *) ko "lint class: '$(last "$out")'";; esac
out=$(MYPY_FAIL=1 lintgate); case "$(last "$out")" in "fast-gate: type: "*) ok "a type red names the type class on its last line";; *) ko "type class: '$(last "$out")'";; esac
out=$(BASE_FAIL=1 RETRY_FAIL=1 fgr); rc=$?
[ $rc = 1 ] && case "$(last "$out")" in "fast-gate: pytest: "*"tests/test_x.py::test_a"*) true;; *) false;; esac && ok "a pytest red names the pytest class and its failed ids" || ko "pytest class: rc $rc: '$(last "$out")'"
# A stub battery that prints the shape of one class per call: CLS on the first call, CLS2 (default CLS) on the retry.
cat > "$T/cls.sh" <<CLS
#!/bin/bash
n=\$(cat "$T/cls.n" 2>/dev/null || echo 0); n=\$((n+1)); echo \$n > "$T/cls.n"; echo "battery \$n" >> "$T/ran.log"
c=\$CLS; [ \$n -gt 1 ] && c=\${CLS2-\$CLS}
case \$c in
  collection) echo "ERROR collecting tests/test_c.py"; echo "E   ImportError: No module named x"; echo "Interrupted: 1 error during collection"; exit 2;;
  saturation) echo "[gw2] node down: Not properly terminated"; echo "replacing crashed worker gw2"; exit 1;;
  killed) echo "Killed"; exit 137;;
  unknown) echo "something odd"; exit 1;;
  errorid) echo "ERROR tests/test_e.py::test_b - fixture boom"; exit 1;;
esac
exit 0
CLS
# The class cases keep their own state: a red an earlier case measured stands for the tree (spec 1163, #1170), and an
# inconclusive attempt on the same tree then ends fail. This block proves the inconclusive classes on a tree with no red.
cst=$T/cls-state; mkdir -p "$cst"
cg() { rm -f "$T/ran.log" "$T/cls.n" "$cst/gate-status"; (cd "$R" && SG_STATE="$cst" SG_FAST_BASE=dev SG_FAST_CMD="bash $T/cls.sh" SG_FAST_RETRY_CMD="bash $T/retry.sh" bash "$d/fast-gate.sh" 2>&1); }
out=$(CLS=collection cg); rc=$?
[ $rc = 1 ] && case "$(last "$out")" in "fast-gate: collection: "*"tests/test_c.py"*) true;; *) false;; esac && ok "a collection error names the collection class and its file" || ko "collection: rc $rc: '$(last "$out")'"
[ "$(/usr/bin/grep -c '^battery' "$T/ran.log")" = 1 ] && ! /usr/bin/grep -q retry-cmd "$T/ran.log" && ok "a collection error is a defect: no retry" || ko "collection retried: '$(cat "$T/ran.log")'"
out=$(CLS=errorid RETRY_FAIL=1 cg); rc=$?
[ $rc = 1 ] && /usr/bin/grep -qx "retry-cmd tests/test_e.py::test_b" "$T/ran.log" && case "$(last "$out")" in "fast-gate: pytest: "*"tests/test_e.py::test_b"*) true;; *) false;; esac \
  && ok "a test that errors is a failed id: it is retried alone and named" || ko "error id: rc $rc: '$(last "$out")' '$(cat "$T/ran.log")'"
cst=$T/cls-inconclusive; mkdir -p "$cst"   # a fresh tree record: the error-id red above must not answer for these attempts
for c in saturation killed unknown; do
  out=$(CLS=$c cg); rc=$?; want=$c; [ $c = killed ] && want=saturation
  [ $rc = 4 ] && case "$(last "$out")" in "fast-gate: $want: "*inconclusive*) true;; *) false;; esac && ok "a $c red, twice, is inconclusive: the $want class and exit 4" || ko "$c: rc $rc: '$(last "$out")'"
  [ "$(/usr/bin/grep -c '^battery' "$T/ran.log")" = 2 ] && ok "a $c red runs the battery once more, and only once" || ko "$c retries: '$(cat "$T/ran.log")'"
  ! /usr/bin/grep -q 'test-foo.sh' "$T/ran.log" && ok "an inconclusive $c result runs no suite" || ko "$c ran suites: '$(cat "$T/ran.log")'"
  case "$out" in *"named no failed test"*) ko "$c is read as pytest: '$out'";; *) ok "a $c red is not read as one pytest cause";; esac
  IFS=$'\t' read -r _ kind res _ < "$cst/gate-status" 2>/dev/null; [ "$kind $res" = "fast fail" ] && ok "an inconclusive $c result leaves no pass line" || ko "$c status: '$(cat "$cst/gate-status" 2>&1)'"
done
out=$(CLS=saturation CLS2=pass cg); rc=$?
[ $rc = 0 ] && /usr/bin/grep -q 'test-foo.sh' "$T/ran.log" && case "$out" in *saturation*) true;; *) false;; esac && ok "an inconclusive red whose retry passes goes on to the suites and names the class" || ko "retry pass: rc $rc: '$out'"
out=$(CLS=unknown CLS2=collection cg); rc=$?
[ $rc = 1 ] && case "$(last "$out")" in "fast-gate: collection: "*) true;; *) false;; esac && ok "an inconclusive red whose retry is a defect fails as that defect" || ko "retry defect: rc $rc: '$(last "$out")'"
# (#2421) A battery whose interpreter cannot import pytest exits 1 with an import traceback and no test id. The check that did
# not run is pytest, whatever module the traceback names: it is an absent tool, unproven at once, never an unknown red twice.
cat > "$T/nostart.sh" <<NOSTART
#!/bin/bash
echo "battery" >> "$T/ran.log"
echo "Traceback (most recent call last):"
echo "ModuleNotFoundError: No module named 'pygments'"
exit 1
NOSTART
nst=$T/cls-nostart; mkdir -p "$nst"
out=$(rm -f "$T/ran.log"; cd "$R" && SG_STATE="$nst" SG_FAST_BASE=dev SG_FAST_CMD="bash $T/nostart.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1); rc=$?
[ $rc = 4 ] && case "$(last "$out")" in "fast-gate: absent: pytest "*) true;; *) false;; esac \
  && ok "2421 a battery whose pytest cannot import is absent, exits 4, and names pytest" || ko "2421 no-start: rc $rc, last '$(last "$out")'"
[ "$(/usr/bin/grep -c '^battery' "$T/ran.log")" = 1 ] && ok "2421 a battery that cannot start is not retried" || ko "2421 no-start retried: '$(cat "$T/ran.log")'"
# The gate lock is gate.sh's, and fast-gate.sh takes no lock of its own.
/usr/bin/grep -v '^ *#' "$d/fast-gate.sh" | /usr/bin/grep -qE 'serial\.sh|lockf|flock|locks/' && ko "fast-gate.sh takes a lock of its own" || ok "fast-gate.sh takes no lock but gate.sh's"

# A shell script selects the same suite on a separate branch from the same base.
$G -C "$R" checkout -q dev; $G -C "$R" checkout -q -b gui/sh; echo 1 >> "$S/foo.sh"; echo 1 >> "$S/baz.sh"; echo 1 >> "$R/docs/x.md"; $G -C "$R" commit -qam shell-work
out=$(fg); rc=$?
[ $rc = 0 ] && ok "a tip whose shell script suite passes exits 0" || ko "shell pass: rc $rc: $out"
[ "$(cat "$T/ran.log" 2>/dev/null)" = "base-cmd lock=held
test-foo.sh lock=held" ] && ok "a shell script still runs its suite by name" || ko "shell ran: '$(cat "$T/ran.log" 2>&1)'"

# A changed suite file runs itself; the base moving on after the branch point adds nothing (the merge-base is the reference).
$G -C "$R" checkout -q dev; echo 2 >> "$S/bar.sh"; $G -C "$R" commit -qam "dev moves"; $G -C "$R" checkout -q gui/sh
echo 2 >> "$S/test-bar.sh"; $G -C "$R" commit -qam "suite edit"
out=$(fg); rc=$?
[ "$(cat "$T/ran.log" 2>/dev/null)" = "base-cmd lock=held
test-bar.sh lock=held
test-foo.sh lock=held" ] && ok "a changed suite runs itself, and a script changed only on the base side runs nothing" || ko "ran: '$(cat "$T/ran.log" 2>&1)'"

out=$(cd "$R" && SG_FAST_BASE=no-such-ref SG_FAST_CMD="bash $T/base.sh" bash "$d/fast-gate.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && ok "a base that does not resolve fails the gate: it is never skipped" || ko "unresolved base: rc $rc: $out"
# The wave entry gate defaults to the fast gate, so a child's own changed scripts are proven before it enters.
/usr/bin/grep -q 'SG_WAVE_GATES:=.*fast-gate\.sh' "$d/env.sh" && ok "the default wave entry gate is the fast gate" || ko "env.sh: the default SG_WAVE_GATES does not run fast-gate.sh"
# The wave entry's own battery is off by default (#1802): the wave's landing gate proves every child, and a profile turns it on.
/usr/bin/grep -q 'SG_WAVE_ENTRY_GATE:=off' "$d/env.sh" && ok "the wave entry battery is off by default" || ko "env.sh: SG_WAVE_ENTRY_GATE does not default to off"

# (#566) The default battery is the loop's one definition (base-battery.sh), and its pytest runs with a bounded worker count,
# SG_GATE_WORKERS_CHILD, instead of `auto`. A stub python logs the argv of each leg: the count is set here, so this suite
# reads no profile's default and depends on no worker count. (The stub answers a delta: a child's gate never builds a full
# battery, so an `all` answer there runs no pytest and the gate ends unproven, which this case does not prove.)
printf '#!/bin/bash\necho "$*" >> "%s/py.log"\ncase "$*" in *squad.testselect*) echo tests/test_x.py;; esac\nexit 0\n' "$T" > "$T/py"; chmod +x "$T/py"
rm -f "$T/ran.log" "$T/py.log"; $G -C "$R" checkout -q dev   # no script changes at the base: the battery alone runs
out=$(cd "$R" && SG_FAST_BASE=dev SG_GATE_PY="$T/py" SG_GATE_WORKERS_CHILD=3 bash "$d/fast-gate.sh" 2>&1); rc=$?
[ $rc = 0 ] && ok "the default battery passes on a stub python" || ko "default battery: rc $rc: $out"
/usr/bin/grep -q -- '-m ruff check squad tests' "$T/py.log" && /usr/bin/grep -q -- '-m mypy squad' "$T/py.log" && ok "the default battery runs ruff and mypy" || ko "default battery: '$(cat "$T/py.log" 2>&1)'"
# (#2085) The child's gate keeps the `fast` stage: the narrowing is the caller's rule, never a stage of its own, so the
# selector is asked what the change reaches, exactly as every other intermediate gate asks (ADR-0169 rules 1 and 8).
/usr/bin/grep -q -- 'squad.testselect --stage fast --base dev' "$T/py.log" && ok "the child's gate keeps the fast stage" || ko "child stage: '$(cat "$T/py.log" 2>&1)'"
/usr/bin/grep 'pytest' "$T/py.log" | /usr/bin/grep -q -- '-n 3' && ok "the default battery's pytest runs with SG_GATE_WORKERS_CHILD" || ko "the worker count: '$(grep pytest "$T/py.log" 2>&1)'"
/usr/bin/grep -q -- '-n auto' "$T/py.log" && ko "the default battery runs pytest -n auto" || ok "the default battery never runs pytest -n auto"
/usr/bin/grep -q 'sg_base_battery' "$d/fast-gate.sh" && ok "fast-gate builds its default command from the shared definition" || ko "fast-gate keeps its own copy of the base battery"
# The retry of what failed stays serial, and runs only the names the battery failed: the id list, never the battery.
retryline=$(/usr/bin/grep -E '^retrycmd=' "$d/fast-gate.sh")
case "$retryline" in *'-p no:xdist'*) ok "the retry serializes pytest";; *) ko "the retry is not serial: '$retryline'";; esac
case "$retryline" in *' -n '*) ko "the serial retry takes workers: '$retryline'";; *) ok "the serial retry stays serial";; esac
# (#1557) The retry must keep the serial 2 s line: a retry that lifts it to the 20 s cap clears the very slow unmarked call
# the serial pass found, and the gate reads it as saturation.
case "$retryline" in *heavy_cap_seconds*) ko "the serial retry lifts the 2 s line: '$retryline'";; *) ok "the serial retry keeps the 2 s line";; esac
/usr/bin/grep -qE 'SG_GATE_WORKERS:=[0-9]+' "$d/env.sh" && ok "env.sh holds the default worker count" || ko "env.sh: no numeric SG_GATE_WORKERS default"
# (#646) The base battery's pytest runs over the selection the selector answers for the base. A stub python logs each call's argv
# on one line and answers squad.testselect from $T/select.out, so each pytest line is compared exactly, never by one word.
printf '#!/bin/bash\necho "$*" >> "%s/argv.log"\ncase "$*" in\n  *squad.testselect*) cat "%s/select.out" 2>/dev/null;;\n  *pytest*)\n    case "$*" in *" -n "*) for s in $SLOW_IDS; do echo "SLOW-UNMARKED (deferred to the serial run): $s took 9.0 s, at or over the heavy line; the serial run enforces it"; done;; esac\n    [ "$PY_FAIL" = 1 ] && { echo "FAILED tests/test_a.py::test_one - boom"; exit 1; };;\nesac\nexit 0\n' "$T" "$T" > "$T/selpy"; chmod +x "$T/selpy"
printf '#!/bin/bash\necho "$*" >> "%s/retry.log"\n' "$T" > "$T/retry.sh"
selrun() { rm -f "$T/argv.log" "$T/retry.log" "$SG_STATE/gate-status"; (cd "$R" && env -u SG_TEST_SELECT -u SG_FAST_CMD SG_FAST_BASE=dev SG_GATE_PY="$T/selpy" SG_GATE_WORKERS_CHILD=3 SG_FAST_RETRY_CMD="bash $T/retry.sh" "$@" bash "$d/fast-gate.sh" 2>&1); }
PYTEST_OPTS='-p no:deepeval -n 3 -m not heavy'
printf 'tests/test_a.py\ntests/test_b.py\n' > "$T/select.out"
out=$(selrun); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx -- "-m pytest $PYTEST_OPTS tests/test_a.py tests/test_b.py" "$T/argv.log" && ok "the battery passes the selection to pytest, after the unchanged options" || ko "selection argv: rc $rc: '$(cat "$T/argv.log" 2>&1)' $out"
/usr/bin/grep -q -- "squad.testselect --stage fast --base dev" "$T/argv.log" && ok "the fast gate asks the selector at the fast stage" || ko "fast stage: '$(cat "$T/argv.log" 2>&1)'"
# (#1726, #1557) The parallel pass runs at the project's own line: an unmarked call over it is recorded there, and only the
# recorded calls run once more, serially, against that line. A selection with no recorded call ends the gate there, with no
# second pytest run.
SERIAL_OPTS='-p no:deepeval -p no:xdist -m not heavy'
[ "$(/usr/bin/grep -c -- '-m pytest' "$T/argv.log")" = 1 ] && ok "1726 no call over the project line: the gate runs no second pytest" || ko "1726 second pytest: '$(/usr/bin/grep -- '-m pytest' "$T/argv.log" 2>&1)'"
out=$(SLOW_IDS="tests/test_a.py::test_one" selrun); rc=$?
[ $rc = 0 ] && [ "$(/usr/bin/grep -- '-m pytest' "$T/argv.log" | tail -n 1)" = "-m pytest $SERIAL_OPTS tests/test_a.py::test_one" ] \
  && [ "$(/usr/bin/grep -c -- '-m pytest' "$T/argv.log")" = 2 ] \
  && ok "1726 only the recorded call runs once more, serially, at the line" || ko "1726 serial rerun: rc $rc: '$(cat "$T/argv.log" 2>&1)' $out"
IFS=$'\t' read -r _ kind res _ sel scope < "$SG_STATE/gate-status" 2>/dev/null
[ "$kind $res $sel $scope" = "fast pass selected 2 files delta" ] && ok "the status line states the selected file count and the delta scope" || ko "selection status: '$(cat "$SG_STATE/gate-status" 2>&1)'"
# (#2085, ADR-0169 rule 4) A child's gate is bounded (ADR-0163) and waits behind its siblings' gates, so it never widens to
# the full battery: a change the delta cannot place runs no pytest, and the gate records `selected 0 files` with the `delta`
# scope and ends unproven with the selector's reason. The landing gate runs the full battery over the branch.
echo "all: no test imports squad/x.py" > "$T/select.out"
rm -f "$SG_STATE/gate-history"
out=$(selrun); rc=$?
[ $rc = 4 ] && ! /usr/bin/grep -q -- "-m pytest" "$T/argv.log" && ok "an all answer at the child's gate runs no pytest: the child never builds a full battery" || ko "all argv: rc $rc: '$(cat "$T/argv.log" 2>&1)'"
case "$(last "$out")" in "fast-gate: unproven: no pytest ran: the delta cannot cover the change: no test imports squad/x.py"*) ok "an all answer ends unproven and names the change the delta cannot cover";; *) ko "all verdict line: '$(last "$out")'";; esac
IFS=$'\t' read -r _ kind res _ sel scope < "$SG_STATE/gate-status" 2>/dev/null
[ "$sel $scope" = "selected 0 files delta" ] && ok "an all answer keeps the delta scope and records that no file was selected" || ko "all status: '$(cat "$SG_STATE/gate-status" 2>&1)'"
IFS=$'\t' read -r _ hk hres _ _ hsel _ hoc hrs hran hnot _ < "$SG_STATE/gate-history" 2>/dev/null
[ "$hres $hoc $hnot" = "fail unproven pytest" ] && [ "$hrs" = "no pytest ran: the delta cannot cover the change: no test imports squad/x.py" ] \
  && ok "the history keeps the child's unplaced change apart from a suite that passed" || ko "all history: '$(cat "$SG_STATE/gate-history" 2>&1)'"
# The wave entry is not the child's bounded gate: it keeps the fall-through, so a change the delta cannot place still runs
# the full battery and the tree is proven before it enters (ADR-0169 rule 8). This is the rule the child's narrowing owes.
out=$(selrun SG_GATE_CALLER=entry); rc=$?
[ $rc = 0 ] && /usr/bin/grep -Eq -- '^-m pytest .* -m not heavy$' "$T/argv.log" && ok "an entry gate keeps the full-battery fallback an all answer owes" || ko "entry all argv: rc $rc: '$(cat "$T/argv.log" 2>&1)'"
IFS=$'\t' read -r _ kind res _ sel scope < "$SG_STATE/gate-status" 2>/dev/null
[ "$sel $scope" = "all: no test imports squad/x.py full" ] && ok "the entry gate's all answer records the full scope" || ko "entry all status: '$(cat "$SG_STATE/gate-status" 2>&1)'"
# (#2085, ADR-0168 rule 1) The narrowed battery is a different suite from the entry gate's: a pass the child won answers no
# entry gate, which still owes the full battery for the same tree.
nm=$T/memo-narrow; mkdir -p "$nm"
nrun() { rm -f "$T/argv.log"; (cd "$R" && env -u SG_TEST_SELECT -u SG_FAST_CMD SG_FAST_BASE=dev SG_STATE="$nm" SG_FAST_MEMO=1 SG_GATE_PY="$T/selpy" SG_FAST_RETRY_CMD=false "$@" bash "$d/fast-gate.sh" 2>&1); }
printf 'tests/test_a.py\n' > "$T/select.out"
out=$(nrun); rc=$?
[ $rc = 0 ] && /usr/bin/grep -Eq -- '^-m pytest .*tests/test_a\.py$' "$T/argv.log" && ok "the child's delta gate passes and keeps its memo" || ko "narrow memo pass: rc $rc: '$(cat "$T/argv.log" 2>&1)' $out"
printf 'all: no test imports squad/x.py\n' > "$T/select.out"
out=$(nrun SG_GATE_CALLER=entry); rc=$?
[ $rc = 0 ] && /usr/bin/grep -Eq -- '^-m pytest .* -m not heavy$' "$T/argv.log" && ok "an entry gate runs its own battery: a child's narrowed pass is no memo for it" || ko "narrow memo reuse: rc $rc: '$(cat "$T/argv.log" 2>&1)' $out"
# (#1249) A gate that runs no test never records a pass. An empty selection is unproven (exit 4), its reason is the one the
# selector gave, the verdict line names it, and the history keeps it apart from a suite that passed: outcome unproven, pytest
# in the not-run list. A selector that answers nothing at all is unproven too, with a reason that says it named no file.
echo "none: the tip changes no file against dev" > "$T/select.out"
rm -f "$SG_STATE/gate-history"
out=$(selrun); rc=$?
[ $rc = 4 ] && ! /usr/bin/grep -q -- "-m pytest" "$T/argv.log" && /usr/bin/grep -q -- "-m ruff check squad tests" "$T/argv.log" && /usr/bin/grep -q -- "-m mypy squad" "$T/argv.log" && ok "an empty selection runs no pytest, still runs ruff and mypy, and exits unproven" || ko "empty selection: rc $rc: '$(cat "$T/argv.log" 2>&1)' $out"
/usr/bin/grep -q "selected 0 files" "$SG_FAST_LOG_DIR"/fast-gate-*.log && ok "an empty selection logs that it ran none" || ko "empty selection log: '$(cat "$SG_FAST_LOG_DIR"/fast-gate-*.log 2>&1)'"
case "$(last "$out")" in "fast-gate: unproven: no pytest ran: the selection is empty: the tip changes no file against dev"*) ok "the verdict line says unproven and names why nothing was selected";; *) ko "empty verdict line: '$(last "$out")'";; esac
IFS=$'\t' read -r _ kind res _ sel scope < "$SG_STATE/gate-status" 2>/dev/null
[ "$kind $res $sel" = "fast fail selected 0 files" ] && ok "an empty selection never leaves a pass on the status line" || ko "empty status: '$(cat "$SG_STATE/gate-status" 2>&1)'"
IFS=$'\t' read -r _ hk hres _ _ hsel _ hoc hrs hran hnot _ < "$SG_STATE/gate-history" 2>/dev/null
[ "$hres $hoc $hnot" = "fail unproven pytest" ] && [ "$hran" = "ruff mypy" ] && [ "$hrs" = "no pytest ran: the selection is empty: the tip changes no file against dev" ] \
  && ok "the history records a suite that never ran as unproven, apart from a pass" || ko "empty history: '$(cat "$SG_STATE/gate-history" 2>&1)'"
: > "$T/select.out"
out=$(selrun); rc=$?
[ $rc = 4 ] && case "$(last "$out")" in "fast-gate: unproven: no pytest ran: the selection is empty: the selector named no test file"*) true;; *) false;; esac \
  && ok "a selector that answers nothing is unproven with its own reason" || ko "bare empty selection: rc $rc: '$(last "$out")'"
IFS=$'\t' read -r _ kind res _ < "$SG_STATE/gate-status" 2>/dev/null
[ "$res" = fail ] && ok "a bare empty selection leaves no pass either" || ko "bare empty status: '$(cat "$SG_STATE/gate-status" 2>&1)'"
# A red shell suite still wins over the empty selection: a measured defect is fail, never unproven.
echo "none: the tip changes no file against dev" > "$T/select.out"
$G -C "$R" checkout -q gui/py
# The green case runs first, with no standing red: a red a suite measured stands for its tree, so a later attempt is fail (gate.sh).
rm -rf "$SG_STATE/gate-reds" "$T/ran.log"; out=$(selrun); rc=$?
[ $rc = 4 ] && /usr/bin/grep -q "test-foo.sh" "$T/ran.log" 2>/dev/null && ok "a green suite beside an empty selection still leaves the gate unproven" || ko "empty plus green suite: rc $rc: $out"
out=$(SUITE_FAIL=test-foo.sh selrun); rc=$?
[ $rc = 1 ] && case "$out" in *test-foo.sh*) true;; *) false;; esac && ok "a red suite beside an empty selection fails the gate" || ko "empty plus red suite: rc $rc: $out"
$G -C "$R" checkout -q dev
printf 'tests/test_a.py\n' > "$T/select.out"
out=$(selrun SG_TEST_SELECT=no); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx -- "-m pytest $PYTEST_OPTS" "$T/argv.log" && ! /usr/bin/grep -q "squad.testselect" "$T/argv.log" && ok "SG_TEST_SELECT=no runs the whole battery without the selector" || ko "SG_TEST_SELECT=no: rc $rc: '$(cat "$T/argv.log" 2>&1)'"
/usr/bin/grep -q "all: SG_TEST_SELECT=no" "$SG_FAST_LOG_DIR"/fast-gate-*.log && ok "SG_TEST_SELECT=no says so in the log" || ko "SG_TEST_SELECT=no log: '$(cat "$SG_FAST_LOG_DIR"/fast-gate-*.log 2>&1)'"
IFS=$'\t' read -r _ kind res _ sel scope < "$SG_STATE/gate-status" 2>/dev/null
[ "$sel $scope" = "all: SG_TEST_SELECT=no full" ] && ok "SG_TEST_SELECT=no names the full scope" || ko "SG_TEST_SELECT=no status: '$(cat "$SG_STATE/gate-status" 2>&1)'"
printf 'tests/test_a.py\ntests/test_b.py\n' > "$T/select.out"
out=$(selrun PY_FAIL=1); rc=$?
[ "$(cat "$T/retry.log" 2>/dev/null)" = "tests/test_a.py::test_one" ] && ok "the serial retry reruns only the failed test names" || ko "retry argv: rc $rc: '$(cat "$T/retry.log" 2>&1)' $out"
# (#801) The fixture waits on nothing outside itself. A stub the fixture writes and then executes directly costs macOS a first-run
# assessment that idles the wall clock, so each stub runs through bash. A fixture case that does not set a retry command runs the
# real pytest, so every case sets one.
/usr/bin/grep -qE 'SG_FAST_(CMD|RETRY_CMD)="\$T/' "$0" && ko "a fixture stub is executed directly, not through bash (#801)" || ok "every fixture stub runs through bash"
/usr/bin/grep -qE '^fg\(\) \{.*SG_FAST_RETRY_CMD=' "$0" && ok "the base-fail case names its retry, and runs no real pytest" || ko "fg runs the real pytest retry (#801)"
# (#885) The pytest tally and the code-quality reading print together in the log this run writes, the reading after the tally, and
# the reading never changes the exit. Each case has its own state directory, so no earlier case's log can satisfy a check.
q=$T/quality-none; mkdir -p "$q"
out=$(cd "$R" && SG_STATE=$q SG_FAST_LOG_DIR=$q/logs SG_FAST_BASE=dev SG_FAST_CMD="echo '5 passed in 0.1s'" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1); rc=$?
lg=$(lgof "$(last "$out")")
t=$(/usr/bin/grep -n "^fast-gate: pytest tally: 5 passed in 0.1s$" "$lg" 2>/dev/null | cut -d: -f1)
qn=$(/usr/bin/grep -n "^quality: no changed squad module, skipped$" "$lg" 2>/dev/null | cut -d: -f1)
[ $rc = 0 ] && [ -n "$t" ] && [ -n "$qn" ] && [ "$qn" -gt "$t" ] && ok "the tally and the quality reading land in one battery log, the reading after the tally, and the gate exits 0" || ko "tally beside reading: rc $rc, tally line '$t', reading line '$qn': $out $(cat "$lg" 2>&1)"

# (#885) With a changed squad module and stub radon and mutmut first on PATH, both numbers print beside the tally in that log, and
# each tool's argv is compared exactly. The stubs log to QARGV and answer with canned output.
qb=$T/qbin; mkdir -p "$qb"
cat > "$qb/radon" <<'S'
#!/bin/bash
echo "radon $*" >> "$QARGV"
echo '{"squad/a.py": [{"name": "f", "complexity": 3}]}'
S
cat > "$qb/mutmut" <<'S'
#!/bin/bash
echo "mutmut $*" >> "$QARGV"
case $1 in
  run) mkdir -p mutants ;;
  export-cicd-stats) mkdir -p mutants; echo '{"total": 4, "survived": 1, "killed": 3}' > mutants/mutmut-cicd-stats.json ;;
esac
S
chmod +x "$qb/radon" "$qb/mutmut"
$G -C "$R" checkout -q -b gui/quality dev; mkdir -p "$R/squad"; printf 'def f(x):\n    return x + 1\n' > "$R/squad/a.py"
$G -C "$R" add -A; $G -C "$R" commit -q -m quality-work
qs=$T/quality-numbers; mkdir -p "$qs"; : > "$T/qargv.log"
out=$(cd "$R" && PATH="$qb:$PATH" QARGV="$T/qargv.log" SG_STATE=$qs SG_FAST_LOG_DIR=$qs/logs SG_FAST_BASE=dev SG_FAST_CMD="echo '5 passed in 0.1s'" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1); rc=$?
lg=$(lgof "$(last "$out")")
ta=$(/usr/bin/grep -n "^fast-gate: pytest tally: 5 passed in 0.1s$" "$lg" 2>/dev/null | cut -d: -f1)
ra=$(/usr/bin/grep -n "^quality: radon cc: 1 block in 1 changed module, mean CC 3.0, max CC 3 (squad/a.py::f)$" "$lg" 2>/dev/null | cut -d: -f1)
ma=$(/usr/bin/grep -n "^quality: mutmut: 1 survived of 4 mutants in 1 changed function$" "$lg" 2>/dev/null | cut -d: -f1)
[ "$(cat "$T/qargv.log")" = "radon cc -s -j squad/a.py
mutmut run squad.a.x_f__mutmut_*
mutmut export-cicd-stats" ] && argv_ok=1 || argv_ok=0
[ $rc = 0 ] && [ $argv_ok = 1 ] && [ -n "$ta" ] && [ -n "$ra" ] && [ -n "$ma" ] && [ "$ta" -lt "$ra" ] && [ "$ra" -lt "$ma" ] && ok "stub radon and mutmut numbers print beside the tally in the battery log, in order, and the gate exits 0" || ko "numbers beside tally: rc $rc, argv ok $argv_ok, lines '$ta' '$ra' '$ma': $out $(cat "$lg" 2>&1)"
# (#945) The gate's evidence must be readable to the child that ran it: the wall denies SG_STATE, so the gate keeps no evidence
# there. A pass ends on one line that names the log, and a red names its log too; both logs sit outside the state directory,
# and each run's log is its own, so two children at one short sha never write it at the same time.
$G -C "$R" checkout -q gui/quality   # a branch whose suites pass, as the green case
out=$(fg); rc=$?
pg=$(lgof "$(last "$out")")
[ $rc = 0 ] && case "$(last "$out")" in "fast-gate: pass "*) true;; *) false;; esac && ok "a pass ends on one line that says pass" || ko "pass line: rc $rc, last '$(last "$out")'"
[ -n "$pg" ] && [ -f "$pg" ] && case "$pg" in "$SG_STATE"/*) ko "the pass names a log inside the state directory: $pg";; *) ok "the pass names its log outside the state directory";; esac
out2=$(fg); pg2=$(lgof "$(last "$out2")")
[ -n "$pg2" ] && [ "$pg2" != "$pg" ] && ok "each run writes its own log, so two runs at one short sha never share one" || ko "two runs share one log path: '$pg' and '$pg2'"
out=$(BASE_FAIL=1 RETRY_FAIL=1 fg); rc=$?
rg=$(lgof "$(last "$out")")
[ $rc = 1 ] && [ -n "$rg" ] && [ -f "$rg" ] && case "$rg" in "$SG_STATE"/*) ko "a red names a log inside the state directory: $rg";; *) ok "a red names a log the child may read, outside the state directory";; esac
# (memo) A pass is kept for its (tree content, dirty state, base ref, merge-base, battery): a second gate at that key
# prints one memo line and runs no battery. A red gate leaves no memo, and a different base ref or battery is a different key.
# Each case has its own state directory.
mg() {  # <state dir> <base> [battery command]: a gate on gui/quality, the memo on unless `memo=0` is set; the stub battery logs to ran.log
  local st=$1 base=$2 cmd=${3:-"bash $T/base.sh"}; rm -f "$T/ran.log"
  (cd "$R" && SG_STATE=$st SG_FAST_MEMO=${memo:-1} SG_FAST_BASE=$base SG_FAST_CMD="$cmd" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1)
}
battery_runs() { /usr/bin/grep -c '^base-cmd' "$T/ran.log" 2>/dev/null || echo 0; }
ms=$T/memo-pass; mkdir -p "$ms"
out1=$(mg "$ms" dev); rc1=$?
[ $rc1 = 0 ] && [ "$(battery_runs)" = 1 ] && ok "the first gate runs the battery and passes" || ko "first memo gate: rc $rc1 runs $(battery_runs): $out1"
out2=$(mg "$ms" dev); rc2=$?
[ $rc2 = 0 ] && [ "$(printf '%s\n' "$out2" | wc -l | tr -d ' ')" = 1 ] && case "$out2" in "fast-gate: pass "*"memo"*) true;; *) false;; esac \
  && ok "a second gate at the same key prints one memo line and passes" || ko "memo line: rc $rc2: '$out2'"
[ ! -e "$T/ran.log" ] && ok "a memo hit runs no battery" || ko "memo hit ran the battery: '$(cat "$T/ran.log")'"
[ "$(lgof "$out2")" = "$(lgof "$out1")" ] && ok "the memo names the log of the pass it reuses" || ko "memo log: '$(lgof "$out2")' vs '$(lgof "$out1")'"
IFS=$'\t' read -r _ kind res at < "$ms/gate-status" 2>/dev/null
[ "$kind $res $at" = "fast pass $(git -C "$R" rev-parse HEAD)" ] && ok "a memo hit leaves the hub's pass line for the same sha" || ko "memo status: '$(cat "$ms/gate-status" 2>&1)'"
# (ADR-0168, #1856) A gate a green verdict already answers is cancelled, and it is still one row: the reader counts the saved
# gate and reads its wall time as none, so the share of a gate stage that is lock wait is computed, never guessed.
h0=$(wc -l < "$ms/gate-history" 2>/dev/null | tr -d ' '); h0=${h0:-0}
out=$(mg "$ms" dev); rc=$?
h1=$(wc -l < "$ms/gate-history" 2>/dev/null | tr -d ' '); h1=${h1:-0}
lr=$(tail -n 1 "$ms/gate-history" 2>/dev/null)
[ $rc = 0 ] && [ "$(( h1 - h0 ))" = 1 ] && [ "$(printf '%s' "$lr" | cut -f8)" = pass ] \
  && [ "$(printf '%s' "$lr" | cut -f22)" = yes ] && [ "$(printf '%s' "$lr" | cut -f20)" = 0 ] \
  && [ "$(printf '%s' "$lr" | cut -f17)" = reused ] && [ "$(printf '%s' "$lr" | cut -f1)" = "$(printf '%s' "$lr" | cut -f5)" ] \
  && ok "a cancelled gate whose verdict is reused writes one row, mode reused, its start at its finish, no wall time and the reuse named" \
  || ko "reuse row: rc $rc, rows $h0 -> $h1, '$(printf '%s' "$lr")'"
out=$(memo=0 mg "$ms" dev); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "SG_FAST_MEMO=0 forces a fresh battery" || ko "memo off: rc $rc runs $(battery_runs): $out"

mf=$T/memo-fail; mkdir -p "$mf"
out=$(BASE_FAIL=1 mg "$mf" dev); rc=$?
[ $rc -ne 0 ] && [ -z "$(ls -A "$mf/gate-memo" 2>/dev/null)" ] && ok "a failed gate leaves no memo" || ko "failed gate memo: rc $rc: '$(ls -A "$mf/gate-memo" 2>&1)'"
out=$(mg "$mf" dev); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "the gate after a red one runs the battery, because a red is never a memo" || ko "after red: rc $rc runs $(battery_runs): $out"

$G -C "$R" branch -f wave-memo dev   # another ref at the same commit: the key still differs
out=$(mg "$ms" wave-memo); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "the base ref is part of the key: the same sha on another base runs the battery" || ko "other base: rc $rc runs $(battery_runs): $out"
out=$(mg "$ms" dev "bash $T/base.sh # battery two"); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "a different battery is a different key" || ko "other battery: rc $rc runs $(battery_runs): $out"

md=$T/memo-dirty; mkdir -p "$md"
out=$(mg "$md" dev); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "a clean tree passes and keeps its memo" || ko "clean memo: rc $rc runs $(battery_runs): $out"
echo dirt >> "$R/docs/x.md"
out=$(mg "$md" dev); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "an uncommitted change is a different key: the battery runs over the tree, never for the memo of the commit" || ko "dirty tree reused a clean pass: rc $rc runs $(battery_runs): $out"
$G -C "$R" checkout -q -- docs/x.md

# (#1205) The memo names the tree's content and the suite, never the commit sha. A second gate on the same commit reuses the
# pass; a rebase that keeps every file rewrites the sha alone, so the entry gate reuses it too; and a commit that changes a
# file is a new tree, so the gate runs a fresh battery.
mc=$T/memo-commit; mkdir -p "$mc"
$G -C "$R" branch memo-base dev   # the base the gate reads, held while the tip moves under it
out=$(mg "$mc" memo-base); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "a committed tree passes and keeps its memo" || ko "committed memo: rc $rc runs $(battery_runs): $out"
out=$(mg "$mc" memo-base); rc=$?
[ $rc = 0 ] && [ ! -e "$T/ran.log" ] && ok "a second gate on the same commit reuses the pass: no battery" || ko "same commit reran the battery: rc $rc: $out"
# The wave tip moves under the child without changing one file (an empty commit), and the child rebases onto it: the sha is
# new and the tree is the same, so the memo still answers.
bt=$(git -C "$R" rev-parse "HEAD^{tree}"); bs=$(git -C "$R" rev-parse HEAD)
$G -C "$R" checkout -q dev && $G -C "$R" commit -q --allow-empty -m "tip moved, tree unchanged" && $G -C "$R" checkout -q gui/quality
$G -C "$R" rebase -q dev
at=$(git -C "$R" rev-parse "HEAD^{tree}"); as=$(git -C "$R" rev-parse HEAD)
[ "$bt" = "$at" ] && [ "$bs" != "$as" ] && ok "the fixture rebase keeps the tree and rewrites the commit sha" || ko "rebase fixture: tree '$bt' -> '$at', sha '$bs' -> '$as'"
out=$(mg "$mc" memo-base); rc=$?
[ $rc = 0 ] && [ ! -e "$T/ran.log" ] && ok "a rebase over the same tree reuses the pass: the memo names the tree, not the commit" || ko "a rebased tree missed its pass: rc $rc: $out"
echo more >> "$R/docs/x.md"; $G -C "$R" add -A; $G -C "$R" commit -q -m "an edit after the gate"
out=$(mg "$mc" memo-base); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "a commit that changes a file is a new tree: the entry gate runs a fresh battery" || ko "post-gate commit reused a pass: rc $rc runs $(battery_runs): $out"

cur=$($G -C "$R" rev-parse --abbrev-ref HEAD)
# (#1408) The key holds no base commit. A landing moves the base ref's commit and the merge-base stays: the pass still answers,
# and the hit records its reuse. A rebase moves the merge-base, so the same tree runs a fresh battery.
: > "$T/reuse.journal"
out1=$(mg "$md" dev); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "a gate at a new key runs the battery and keeps its pass" || ko "move setup: rc $rc runs $(battery_runs): $out1"
$G -C "$R" checkout -q dev && $G -C "$R" commit -q --allow-empty -m moved && $G -C "$R" checkout -q "$cur"
mbb=$($G -C "$R" merge-base HEAD dev)
out=$(SG_GATE_CARD=7 SG_GATE_JOURNAL=$T/reuse.journal mg "$md" dev); rc=$?
[ $rc = 0 ] && [ ! -e "$T/ran.log" ] && case "$out" in *memo*) true;; *) false;; esac \
  && ok "a moved base commit with the same merge-base hits the memo: the key holds no base commit" || ko "moved base: rc $rc: $out"
[ "$(/usr/bin/grep -c gate_reuse "$T/reuse.journal" 2>/dev/null)" = 1 ] && [ "$(lgof "$out")" = "$(lgof "$out1")" ] \
  && ok "a memo hit records one gate_reuse marker and names the log it used" || ko "reuse record: '$(cat "$T/reuse.journal" 2>&1)' / $out"
$G -C "$R" rebase -q dev
[ "$($G -C "$R" merge-base HEAD dev)" != "$mbb" ] && ok "the fixture rebase moves the merge-base" || ko "merge-base fixture: unchanged $mbb"
out=$(mg "$md" dev); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "a moved merge-base misses the memo: the battery runs" || ko "moved merge-base: rc $rc runs $(battery_runs): $out"

# (#1718, ADR-0168) `--memo-hit` asks the memo before any caller takes the machine gate lock: it answers whether a green pass
# already holds this tree, base, merge-base and suite. A hit prints the reuse line and exits 0 with no battery and no lock; a
# miss, a tree the key cannot name, or a memo turned off exits 1 with the reason, so the caller runs the gate as before.
mh() {  # <state dir> <base>: the probe alone
  local st=$1 base=$2; rm -f "$T/ran.log"
  (cd "$R" && SG_STATE=$st SG_FAST_MEMO=${memo:-1} SG_FAST_BASE=$base SG_FAST_CMD="bash $T/base.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" --memo-hit 2>&1)
}
mp=$T/memo-probe; mkdir -p "$mp"
out=$(mh "$mp" dev); rc=$?
[ $rc = 1 ] && [ ! -e "$T/ran.log" ] && ok "the probe at a key with no pass exits 1 and runs no battery" || ko "probe miss: rc $rc: '$out'"
out1=$(mg "$mp" dev); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "the gate at that key runs the battery and keeps its pass" || ko "probe setup: rc $rc runs $(battery_runs): $out1"
out=$(mh "$mp" dev); rc=$?
[ $rc = 0 ] && [ ! -e "$T/ran.log" ] && case "$out" in *memo*) true;; *) false;; esac \
  && ok "the probe answers a stored pass with one reuse line and runs no battery" || ko "probe hit: rc $rc: '$out'"
[ "$(lgof "$out")" = "$(lgof "$out1")" ] && ok "the probe names the log of the pass it reuses" || ko "probe log: '$(lgof "$out")' vs '$(lgof "$out1")'"
IFS=$'\t' read -r _ kind res at < "$mp/gate-status" 2>/dev/null
[ "$kind $res $at" = "fast pass $(git -C "$R" rev-parse HEAD)" ] && ok "the probe leaves the hub's pass line for the tip" || ko "probe status: '$(cat "$mp/gate-status" 2>&1)'"
out=$(memo=0 mh "$mp" dev); rc=$?
[ $rc = 1 ] && case "$out" in *"SG_FAST_MEMO"*) true;; *) false;; esac && ok "a memo turned off is no hit, and the probe names the memo" || ko "probe memo off: rc $rc: '$out'"
echo probe-dirt >> "$R/docs/x.md"
out=$(mh "$mp" dev); rc=$?
[ $rc = 1 ] && ok "an uncommitted change is a different key: the probe answers no pass for it" || ko "probe dirty: rc $rc: '$out'"
$G -C "$R" checkout -q -- docs/x.md
# A red is never kept, so it never answers the probe: the caller runs the gate and meets the red itself.
mpr=$T/memo-probe-red; mkdir -p "$mpr"
out=$(BASE_FAIL=1 mg "$mpr" dev); rc=$?
[ $rc = 1 ] && ok "a red gate fails and keeps no pass" || ko "probe red setup: rc $rc: '$out'"
out=$(mh "$mpr" dev); rc=$?
[ $rc = 1 ] && [ ! -e "$T/ran.log" ] && ok "the probe answers no pass for a tree whose gate was red, so the suite runs" || ko "probe after red: rc $rc: '$out'"

mq=$T/memo-queued; mkdir -p "$mq"; rm -f "$T/ran.log"
slow="bash $T/base.sh; sleep 2"
(cd "$R" && SG_STATE=$mq SG_FAST_MEMO=1 SG_FAST_BASE=dev SG_FAST_CMD="$slow" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" > "$T/q1.out" 2>&1) &
qp=$!; sleep 1
(cd "$R" && SG_STATE=$mq SG_FAST_MEMO=1 SG_FAST_BASE=dev SG_FAST_CMD="$slow" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" > "$T/q2.out" 2>&1); qrc2=$?
wait "$qp"; qrc1=$?
[ $qrc1 = 0 ] && [ $qrc2 = 0 ] && [ "$(battery_runs)" = 1 ] && ok "two gates queued at one key share one battery, because the memo is read again inside the lock" || ko "queued gates: rc $qrc1/$qrc2 runs $(battery_runs): '$(cat "$T/q2.out")'"
case "$(cat "$T/q2.out")" in *memo:*) ok "the gate that waited reads the pass the first one kept";; *) ko "the gate that waited ran a battery: '$(cat "$T/q2.out")'";; esac

# (#1108) A fixture suite that copies a module list states it in a `# mirrors:` line. A change to a listed file runs that suite,
# and a suite with no such line is never picked by its module list. The base carries the suites; each case is one branch.
$G -C "$R" checkout -q dev
printf '#!/bin/bash\n# mirrors: hub.py session_card.py\necho "test-hub-caps.sh" >> "%s/ran.log"\n' "$T" > "$S/test-hub-caps.sh"
printf '#!/bin/bash\necho "test-hub-other.sh" >> "%s/ran.log"\n' "$T" > "$S/test-hub-other.sh"
printf 'import session_card\n' > "$S/hub.py"; printf 'x = 1\n' > "$S/session_card.py"; printf 'x = 1\n' > "$S/unrelated.py"
$G -C "$R" add -A; $G -C "$R" commit -q -m mirror-base
$G -C "$R" checkout -q -b gui/hub-import dev && echo 'import session_card  # new import' >> "$S/hub.py"
$G -C "$R" add -A; $G -C "$R" commit -q -m hub-import
out=$(fg); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx "test-hub-caps.sh" "$T/ran.log" && ok "a change to hub.py runs the fixture suite whose mirror statement names it" || ko "hub.py change: rc $rc ran '$(cat "$T/ran.log" 2>/dev/null)': $out"
/usr/bin/grep -qx "test-hub-other.sh" "$T/ran.log" 2>/dev/null && ko "a suite with no mirror statement ran" || ok "a suite with no mirror statement is not picked by its module list"
$G -C "$R" checkout -q dev && $G -C "$R" checkout -q -b gui/unrelated dev && echo 'y = 2' >> "$S/unrelated.py"
$G -C "$R" add -A; $G -C "$R" commit -q -m unrelated
out=$(fg); rc=$?
/usr/bin/grep -q "test-hub-caps.sh" "$T/ran.log" 2>/dev/null && ko "an unlisted module ran the fixture suite" || ok "a module the mirror statement does not name runs no fixture suite"

# (#2373) A fixture suite that copies or sources a changed script names it in its own text, so the gate selects it from that
# text with no `# mirrors:` line. The copy forms in use today are "$d/<script>", $(dirname "$src")/<script> and a brace list
# {a.sh,b.sh}; a bare name in prose is no copy and selects nothing. A selected suite that goes red fails the gate.
$G -C "$R" checkout -q dev
printf '#!/bin/bash\n# a\n' > "$S/copy-a.sh"; printf '#!/bin/bash\n# b\n' > "$S/copy-b.sh"; printf '#!/bin/bash\n# c\n' > "$S/copy-c.sh"
printf '#!/bin/bash\nd=$(cd "$(dirname "$0")" && pwd); H=$(mktemp -d); cp "$d/copy-a.sh" "$H/" || exit 1\necho "test-copy-d.sh" >> "%s/ran.log"\n' "$T" > "$S/test-copy-d.sh"
printf '#!/bin/bash\nsrc=${1:-$(dirname "$0")/copy-b.sh}; H=$(mktemp -d); cp "$src" "$H/copy-b.sh" || exit 1\necho "test-copy-src.sh" >> "%s/ran.log"\n' "$T" > "$S/test-copy-src.sh"
printf '#!/bin/bash\n[ "$COPY_FAIL" = test-copy-brace.sh ] && exit 1\nH=$(mktemp -d); cp "$(dirname "$0")"/{copy-b.sh,copy-c.sh} "$H/" || exit 1\necho "test-copy-brace.sh" >> "%s/ran.log"\n' "$T" > "$S/test-copy-brace.sh"
printf '#!/bin/bash\n# this suite mentions copy-a.sh in prose only\necho "test-copy-none.sh" >> "%s/ran.log"\n' "$T" > "$S/test-copy-none.sh"
$G -C "$R" add -A; $G -C "$R" commit -q -m copy-base
$G -C "$R" checkout -q -b gui/copy dev && printf '# changed\n' >> "$S/copy-a.sh" && printf '# changed\n' >> "$S/copy-b.sh" && printf '# changed\n' >> "$S/copy-c.sh"
$G -C "$R" add -A; $G -C "$R" commit -q -m copy-work
out=$(fg); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx "test-copy-d.sh" "$T/ran.log" && ok "a suite that copies a changed script by \"\$d/<script>\" runs, with no mirror statement" || ko "copy \$d: rc $rc ran '$(cat "$T/ran.log" 2>/dev/null)': $out"
/usr/bin/grep -qx "test-copy-src.sh" "$T/ran.log" 2>/dev/null && ok "a suite that copies a changed script by \$(dirname \"\$src\")/<script> runs, with no mirror statement" || ko "copy \$(dirname src): ran '$(cat "$T/ran.log" 2>/dev/null)': $out"
/usr/bin/grep -qx "test-copy-brace.sh" "$T/ran.log" 2>/dev/null && ok "a suite that copies a changed script by a brace list runs, with no mirror statement" || ko "copy brace list: ran '$(cat "$T/ran.log" 2>/dev/null)': $out"
/usr/bin/grep -qx "test-copy-none.sh" "$T/ran.log" 2>/dev/null && ko "a suite that only names a changed script in prose ran" || ok "a bare name in prose selects no suite"
out=$(COPY_FAIL=test-copy-brace.sh fg); rc=$?
[ $rc -ne 0 ] && case "$out" in *test-copy-brace.sh*) true;; *) false;; esac && ok "a suite selected by the script it copies that goes red fails the gate and is named" || ko "copy red suite: rc $rc: $out"
$G -C "$R" checkout -q dev   # the red above belongs to the gui/copy tip; the cases below start from a tip no gate has marked

# (#2583) A suite that checks every script of the directory names no script, so it states `# guards: every-script` in its
# leading comment block. A change to any .sh or .py script there runs it; a change outside the directory does not. The same
# text inside a string after the first command is no header and selects nothing.
printf '#!/bin/bash\n# guards: every-script\necho "test-everyguard.sh" >> "%s/ran.log"\n' "$T" > "$S/test-everyguard.sh"
printf '#!/bin/bash\nH=$(mktemp -d); printf %s\n# guards: every-script\n%s > "$H/x.sh"\necho "test-everystring.sh" >> "%s/ran.log"\n' "\"#!/bin/bash" "\"" "$T" > "$S/test-everystring.sh"
printf '#!/bin/bash\n# other\n' > "$S/other.sh"
$G -C "$R" add -A; $G -C "$R" commit -q -m every-base
$G -C "$R" checkout -q -b gui/every-script dev && printf '# changed\n' >> "$S/other.sh"
$G -C "$R" add -A; $G -C "$R" commit -q -m every-script
out=$(fg); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx "test-everyguard.sh" "$T/ran.log" && ok "a change to any script runs the suite whose header guards every script" || ko "every-script: rc $rc ran '$(cat "$T/ran.log" 2>/dev/null)': $out"
/usr/bin/grep -qx "test-everystring.sh" "$T/ran.log" 2>/dev/null && ko "a header inside a string selected its suite" || ok "the header text inside a string after the first command selects no suite"
$G -C "$R" checkout -q dev && $G -C "$R" checkout -q -b gui/every-outside dev && echo 'outside' >> "$R/docs/x.md"
$G -C "$R" add -A; $G -C "$R" commit -q -m every-outside
out=$(fg); rc=$?
/usr/bin/grep -qx "test-everyguard.sh" "$T/ran.log" 2>/dev/null && ko "a change outside the scripts directory ran the every-script suite" || ok "a change outside the scripts directory runs no every-script suite"
$G -C "$R" checkout -q dev

# (spec 1163) The verdict: the history line carries the outcome, one reason, the checks that ran and the ones that did not. A tool
# that is absent is unproven and names the tool, with no retry; a present check that reports a defect is fail; a clean battery passes.
vstub() {  # <name> <body>: a battery stub that logs one line per call, then runs <body>
  printf '#!/bin/bash\necho "battery" >> "%s/ran.log"\n%s\n' "$T" "$2" > "$T/$1.sh"
}
vfg() {  # <battery stub>: one fast gate on the current branch with that battery; RETRY names its retry command
  rm -f "$T/ran.log" "$SG_STATE/gate-status"; (cd "$R" && SG_FAST_BASE=dev SG_FAST_CMD="bash $T/$1.sh" SG_FAST_RETRY_CMD="${RETRY:-bash $T/retry.sh}" bash "$d/fast-gate.sh" 2>&1)
}
vhist() { tail -n 1 "$SG_STATE/gate-history"; }
vstub absent 'echo "/usr/bin/python3: No module named mypy"; exit 1'
out=$(vfg absent); rc=$?
[ $rc = 4 ] && case "$(last "$out")" in "fast-gate: absent: mypy "*) true;; *) false;; esac \
  && ok "an absent type checker exits 4 and names mypy on its last line" || ko "absent mypy: rc $rc: '$(last "$out")'"
[ "$(/usr/bin/grep -c '^battery' "$T/ran.log")" = 1 ] && ok "an absent tool is not retried" || ko "absent tool retried: '$(cat "$T/ran.log")'"
[ "$(vhist | cut -f8-11)" = "$(printf 'unproven\tmypy is absent from the battery'"'"'s interpreter, so its check did not run\truff\tmypy pytest')" ] \
  && ok "the history line says unproven, names mypy as the reason, and lists ruff as run and mypy and pytest as not run" || ko "absent history: '$(vhist)'"
# The obligation of that entry names the base the gate read the tree against, as a commit (gate.sh, spec 1163). The state
# directory already holds an obligation for an earlier unresolved base, so the record is found by its tree and base.
wantb=$(git -C "$R" rev-parse HEAD); devb=$(git -C "$R" rev-parse dev); ob=
for f in "$SG_STATE"/obligations/*; do [ -f "$f" ] || continue
  IFS=$'\t' read -r _ os obase _ _ _ < "$f"
  [ "$os" = "$wantb" ] && [ "$obase" = "$devb" ] && { ob=$f; break; }
done
[ -n "$ob" ] && ok "the unproven entry's obligation names the base commit the gate read" \
  || ko "no obligation for ${wantb:0:9} names the dev commit $devb: '$(cat "$SG_STATE"/obligations/* 2>&1)'"

vstub typed 'echo "squad/x.py:12: error: bad type"; exit 1'
out=$(vfg typed); rc=$?
[ $rc = 1 ] && [ "$(vhist | cut -f8)" = fail ] && [ "$(vhist | cut -f10)" = "ruff mypy" ] && ok "a type checker that reports an error is a fail, with ruff and mypy as run" || ko "type fail: rc $rc: '$(vhist)'"

vstub collected 'echo "ERROR collecting tests/test_c.py"; exit 2'
out=$(vfg collected); rc=$?
[ $rc = 1 ] && [ "$(vhist | cut -f8)" = fail ] && [ "$(vhist | cut -f10)" = "ruff mypy pytest" ] && ok "a collection error is a fail, with every check run" || ko "collection fail: rc $rc: '$(vhist)'"

vstub clean 'exit 0'
out=$(vfg clean); rc=$?
[ $rc = 0 ] && [ "$(vhist | cut -f8-11)" = "$(printf 'pass\t-\truff mypy pytest\t-')" ] && ok "a clean battery passes, with every check run and none not run" || ko "clean pass: rc $rc: '$(vhist)'"

out=$(cd "$R" && SG_FAST_BASE=no-such-ref SG_FAST_CMD="bash $T/clean.sh" bash "$d/fast-gate.sh" 2>&1); rc=$?
[ $rc = 2 ] && [ "$(vhist | cut -f8)" = unproven ] && case "$(vhist | cut -f9)" in *"does not resolve"*) true;; *) false;; esac \
  && ok "an unresolved base is unproven: it exits 2 and the reason says the base does not resolve" || ko "unresolved base verdict: rc $rc: '$(vhist)'"

# (#1174) The caller reads the gate's own word. GATE_OUTCOME_READ_FILE receives the outcome line gate.sh read inside the
# lock, so the wave entry, the landing and the promotion decide on pass, fail or unproven and never on the exit code alone.
# The command the gate runs never sees the variable, so a nested gate cannot overwrite the caller's copy. Each case keeps its
# own state, because a measured red stands for its tree (#1170): a red one case records must not fold the next case's attempt.
vfr() {  # <battery stub>: vfg with a caller-named outcome copy, on that case's own state
  local vs=$T/vst-$1; rm -rf "$vs" "$T/ran.log" "$T/outcome"
  (cd "$R" && SG_STATE="$vs" SG_FAST_BASE=dev SG_FAST_CMD="bash $T/$1.sh" SG_FAST_RETRY_CMD="bash $T/retry.sh" GATE_OUTCOME_READ_FILE="$T/outcome" bash "$d/fast-gate.sh" 2>&1)
}
out=$(vfr clean); rc=$?
[ $rc = 0 ] && [ "$(cut -f1 "$T/outcome" 2>/dev/null)" = pass ] && ok "a pass entry gives the caller the word pass" || ko "read pass: rc $rc, '$(cat "$T/outcome" 2>/dev/null)'"
out=$(vfr typed); rc=$?
[ $rc = 1 ] && [ "$(cut -f1 "$T/outcome" 2>/dev/null)" = fail ] && ok "a fail entry gives the caller the word fail" || ko "read fail: rc $rc, '$(cat "$T/outcome" 2>/dev/null)'"
out=$(vfr absent); rc=$?
[ $rc = 4 ] && [ "$(cut -f1 "$T/outcome" 2>/dev/null)" = unproven ] && ok "an unproven entry gives the caller the word unproven" || ko "read unproven: rc $rc, '$(cat "$T/outcome" 2>/dev/null)'"
printf '#!/bin/bash\necho "read=${GATE_OUTCOME_READ_FILE:-unset}" >> "%s/ran.log"\nexit 0\n' "$T" > "$T/leak.sh"
out=$(vfr leak); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx 'read=unset' "$T/ran.log" && ok "the command never sees the caller's outcome file" || ko "the caller's variable leaked into the command: '$(cat "$T/ran.log" 2>/dev/null)'"
# (spec 1163, #1170, #1171) One deadline covers the battery and its retry, and a defect that was already reported survives
# the kill. A battery that prints a failing test and then hangs ends fail; a retry that hangs never clears the battery's red;
# and a battery that reports nothing and hangs is bounded and unproven, never a pass. Nothing of the killed tree survives.
printf '#!/bin/bash\necho "retry-cmd $*" >> "%s/ran.log"\nsleep $HANG_NAP\n' "$T" > "$T/retry-hang.sh"
vstub hangfail 'echo "FAILED tests/test_h.py::test_hang - boom"; sleep $HANG_NAP'
s0=$(date +%s); out=$(SG_GATE_BOUND_SECONDS=2 vfg hangfail); rc=$?; el=$(( $(date +%s) - s0 ))
[ $rc = 1 ] && [ "$el" -lt 20 ] && case "$(last "$out")" in "fast-gate: pytest: "*"tests/test_h.py::test_hang"*) true;; *) false;; esac \
  && ok "a failing test followed by a hang at the deadline ends fail and names the defect (${el}s)" || ko "hang after a defect: rc $rc, last '$(last "$out")'"
[ "$(vhist | cut -f8)" = fail ] && ok "the history records the deadline-bound attempt as fail, not bounded" || ko "hang history: '$(vhist)'"
pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1 && ko "the battery's tree survived the deadline" || ok "the battery's tree is stopped at the deadline"

vstub fastred 'echo "FAILED tests/test_r.py::test_r - boom"; exit 1'
s0=$(date +%s); out=$(RETRY="bash $T/retry-hang.sh" SG_GATE_BOUND_SECONDS=3 vfg fastred); rc=$?; el=$(( $(date +%s) - s0 ))
[ $rc = 1 ] && [ "$el" -lt 20 ] && /usr/bin/grep -qx "retry-cmd tests/test_r.py::test_r" "$T/ran.log" \
  && ok "a retry shares the one budget: a retry that hangs leaves the gate red (${el}s)" || ko "retry budget: rc $rc in ${el}s: '$(cat "$T/ran.log")' '$(last "$out")'"
[ "$(vhist | cut -f8)" = fail ] && ok "a retry that never answers records the red as fail" || ko "retry hang history: '$(vhist)'"
pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1 && ko "the retry's tree survived the deadline" || ok "the retry's tree is stopped at the deadline"

vstub silent 'sleep $HANG_NAP'
bs=$T/bounded-gate; mkdir -p "$bs"
s0=$(date +%s); out=$(cd "$R" && SG_STATE="$bs" SG_FAST_BASE=dev SG_GATE_BOUND_SECONDS=2 SG_FAST_CMD="bash $T/silent.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1); rc=$?; el=$(( $(date +%s) - s0 ))
bh=$(tail -n 1 "$bs/gate-history")
[ $rc = 4 ] && [ "$el" -lt 20 ] && case "$(last "$out")" in *bounded*) true;; *) false;; esac \
  && ok "a battery that reports nothing and hangs is bounded, never a pass (${el}s)" || ko "silent hang: rc $rc, last '$(last "$out")'"
[ "$(printf '%s' "$bh" | cut -f8)" = unproven ] && case "$(printf '%s' "$bh" | cut -f9)" in bounded*) true;; *) false;; esac \
  && ok "the bounded attempt is recorded as bounded and unproven" || ko "bounded history: '$bh'"
pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1 && ko "the silent battery's tree survived the deadline" || ok "the silent battery's tree is stopped at the deadline"

# (#1527) A child re-runs a bounded gate until its deadline, so the bound is kept per head: a child's second gate at a tree the
# bound already stopped starts no battery. The memo is on here (the suite turns it off above), and each case names its caller.
hl() { wc -l < "$bs/gate-history" | tr -d ' '; }   # the row count, unpadded (BSD wc pads it)
bfg() {  # <env assignment>...: one gate of the silent battery on the bounded state, the memo on, a 2 s bound
  (cd "$R" && env -u SG_GATE_CALLER -u SG_GATE_WORKERS SG_FAST_MEMO=1 SG_STATE="$bs" SG_FAST_BASE=dev SG_GATE_BOUND_SECONDS=2 SG_FAST_CMD="bash $T/silent.sh" SG_FAST_RETRY_CMD=false "$@" bash "$d/fast-gate.sh" 2>&1)
}
bfg >/dev/null   # the first memo-on attempt at this head: bounded, and its bound is kept under the key
h0=$(hl)
s0=$(date +%s); out=$(bfg); rc=$?; el=$(( $(date +%s) - s0 ))
[ $rc = 4 ] && [ "$el" -lt 2 ] && case "$(last "$out")" in "fast-gate: bounded already"*landing*) true;; *) false;; esac \
  && [ "$(hl)" = "$h0" ] && ! pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1 \
  && ok "a child's second gate at a tree the bound already stopped starts no battery: the landing owns the obligation (#1527, ${el}s)" \
  || ko "a child's second gate re-ran the bounded battery: rc $rc in ${el}s, last '$(last "$out")', history $h0 -> $(hl)"
h0=$(hl)
s0=$(date +%s); out=$(bfg SG_GATE_CALLER=entry); rc=$?; el=$(( $(date +%s) - s0 ))
[ $rc = 4 ] && [ "$el" -ge 2 ] && [ "$(hl)" = "$((h0 + 1))" ] && [ "$(tail -n 1 "$bs/gate-history" | cut -f8)" = unproven ] \
  && ok "the entry gate measures a bounded tree again: the cap is the child's alone (${el}s)" || ko "the entry gate did not measure again: rc $rc in ${el}s, history $h0 -> $(hl)"
echo again >> "$R/docs/x.md"; $G -C "$R" commit -qam "a new head"
h0=$(hl)
s0=$(date +%s); out=$(bfg); rc=$?; el=$(( $(date +%s) - s0 ))
[ $rc = 4 ] && [ "$el" -ge 2 ] && [ "$(hl)" = "$((h0 + 1))" ] \
  && ok "a new head spends a new attempt (${el}s)" || ko "a new head did not spend an attempt: rc $rc in ${el}s, history $h0 -> $(hl)"
$G -C "$R" reset -q --hard HEAD~1
# (#1527) The default battery names its worker count, so the key the child reads before the lock must carry the count the gate
# stores inside it: a child's default is 2 workers. A repeat at the same head with the gate lock held exits 4 at once, never 73.
wk=$T/wk; mkdir -p "$wk/repo" "$wk/st"; $G -C "$wk/repo" init -q -b dev
$G -C "$wk/repo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m base
printf '#!/bin/bash\nif [ "$1 $2" = "-m ruff" ]; then echo "unclassified stub failure"; exit 1; fi\nexec python3 "$@"\n' > "$wk/py"; chmod +x "$wk/py"
wfg() { (cd "$wk/repo" && env -u SG_GATE_CALLER -u SG_GATE_WORKERS -u SG_FAST_CMD -u SG_FAST_RETRY_CMD SG_FAST_MEMO=1 SG_STATE="$wk/st" SG_FAST_BASE=dev SG_GATE_PY="$wk/py" SG_FAST_LOG_DIR="$wk/logs" SQUAD_CAPS_DIR="$wk/caps" SG_GATE_BOUND_SECONDS=15 "$@" bash "$d/fast-gate.sh" 2>&1); }
out=$(wfg); rc1=$?
mkdir -p "$wk/st/locks/gate"; echo $$ > "$wk/st/locks/gate/pid"
out=$(wfg SERIAL_TRY=1); rc=$?
rm -rf "$wk/st/locks/gate"
[ $rc1 = 4 ] && [ $rc = 4 ] && case "$(last "$out")" in "fast-gate: bounded already"*) true;; *) false;; esac \
  && ok "a child's repeat on the default battery finds its bound before the lock: the key carries the child's 2 workers (#1527)" \
  || ko "a child's repeat on the default battery missed its bound: first rc $rc1, repeat rc $rc, last '$(last "$out")'"

# (#1839) The gate carries a ceiling of its own (ADR-0051): whatever clock the caller names -- a proof's bound, a run's call
# deadline, or a long one -- a gate never runs past SG_GATE_MAX_SECONDS, so a caller that never answers cannot park the gate and
# hold its worker slots for hours. Past the ceiling the suite's whole tree is killed, one line names that bound and the killed
# tree, the outcome is unproven, and the lease goes back as it would on any other exit. A caller's shorter bound still wins.
printf '#!/bin/bash\necho "battery" >> "%s/ran.log"\nsleep $HANG_NAP & echo "hang=$!" >> "%s/ran.log"; wait\n' "$T" "$T" > "$T/gmhang.sh"
gm=$T/gatemax; gmcap=$T/gmcaps; mkdir -p "$gm" "$gmcap"
gmax() {  # <bound> <ceiling>: one gate whose battery hangs, on that case's own state, lock and slot budget
  (cd "$R" && SG_STATE="$gm" SQUAD_CAPS_DIR="$gmcap" SG_FAST_BASE=dev SG_GATE_BOUND_SECONDS="$1" SG_GATE_MAX_SECONDS="$2" \
     SG_FAST_CMD="bash $T/gmhang.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1)
}
go=$T/gm.out
gmax 600 3 > "$go" 2>&1 & gp=$!
s0=$(date +%s); gkilled=
while [ $(( $(date +%s) - s0 )) -lt 60 ]; do kill -0 "$gp" 2>/dev/null || break; sleep 1; done
kill -0 "$gp" 2>/dev/null && { gkilled=1; kill "$gp" 2>/dev/null; }   # a gate past its ceiling: end it here, not in ten minutes
wait "$gp" 2>/dev/null; grc=$?; gel=$(( $(date +%s) - s0 ))
hpid=$(sed -n 's/^hang=//p' "$T/ran.log" 2>/dev/null | tail -1)
[ -z "$gkilled" ] && [ $grc = 4 ] && [ "$gel" -lt 20 ] \
  && case "$(last "$(cat "$go" 2>/dev/null)")" in "fast-gate: gate max:"*"3s"*"${hpid:-no-pid}"*) true;; *) false;; esac \
  && ok "a caller's 600s bound is capped at the gate's own 3s ceiling: unproven, one line naming the bound and the killed tree (${gel}s)" \
  || ko "gate ceiling: rc $grc in ${gel}s killed=$gkilled last '$(last "$(cat "$go" 2>/dev/null)")'"
pkill -f "sleep $HANG_NAP" 2>/dev/null
pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1 && ko "the suite's tree survived the gate's ceiling" || ok "the gate's ceiling kills the suite's whole tree"
[ -z "$(ls -A "$gmcap/gates" 2>/dev/null)" ] && ok "the gate gives its slot lease back at the ceiling, as on any other exit" \
  || ko "lease survived the ceiling: '$(ls -A "$gmcap/gates" 2>/dev/null | tr '\n' ' ')'"
[ "$(tail -n 1 "$gm/gate-history" | cut -f8)" = unproven ] && ok "the ceiling is recorded as unproven, never a pass" || ko "ceiling history: '$(tail -n 1 "$gm/gate-history" 2>/dev/null)'"
rm -rf "$gmcap"

# (#2379, ADR-0169 rule 9) A full battery takes its own bound, measured from this machine's gate history: 3/2 of the longest
# wall of a passing or bounded full-battery gate among the last 20 full-battery gates, never under the delta bound and never
# over the cap (SG_GATE_FULL_MAX_SECONDS, default twice the delta bound). A window with no such row answers the cap. The owner's
# SG_GATE_FULL_BOUND_SECONDS wins, clamped to the delta bound. A delta row, a reused row and a failed row measure nothing.
fbh=$T/fullbound; mkdir -p "$fbh"
hrow() {  # <selection> <scope> <outcome> <reason> <wall> [<mode>] [<reused>]: one 26-field history row
  local r=pass; [ "$3" = pass ] || r=fail
  printf '%s\tfast\t%s\tsha\t1000\t%s\t%s\t%s\t%s\t-\t-\t3\t-\tlanding\t1\t1\t%s\ttree\t0\t%s\t1\t%s\tlocal\t-\t-\t-\n' \
    "$(( 1000 + $5 ))" "$r" "$1" "$2" "$3" "$4" "${6:-gated}" "$5" "${7:-no}"
}
fpass() { hrow "all: landing runs the full battery" full pass - "$1"; }
fb() {  # [<VAR=value>...]: sg_full_bound over $fbh/gate-history for a 900 s delta bound
  env -u SG_GATE_FULL_BOUND_SECONDS -u SG_GATE_FULL_MAX_SECONDS SG_STATE="$fbh" "$@" bash -c '. "$1/caps.sh"; sg_full_bound 900' _ "$d"
}
fbcase() {  # <want> <label> [<VAR=value>...]: the history file is already written
  local want=$1 label=$2 got; shift 2; got=$(fb "$@")
  [ "$got" = "$want" ] && ok "2379 full bound: $label ($got s)" || ko "2379 full bound: $label: got '$got', want $want"
}
rm -f "$fbh/gate-history"; fbcase 1800 "no history answers the cap, twice the delta bound"
{ fpass 400; fpass 600; } > "$fbh/gate-history"; fbcase 900 "3/2 of a short full pass never falls under the delta bound"
fpass 1000 > "$fbh/gate-history"; fbcase 1500 "3/2 of the longest full pass"
fpass 1091 > "$fbh/gate-history"; fbcase 1637 "3/2 rounds up"
fpass 1300 > "$fbh/gate-history"; fbcase 1800 "the cap holds the measured value"
fbcase 1950 "an owner cap above the measure lets it through" SG_GATE_FULL_MAX_SECONDS=3000
fbcase 900 "a cap under the delta bound answers the delta bound" SG_GATE_FULL_MAX_SECONDS=100
fbcase 1400 "the owner's bound wins over the measure" SG_GATE_FULL_BOUND_SECONDS=1400
fbcase 900 "the owner's bound is clamped to the delta bound" SG_GATE_FULL_BOUND_SECONDS=100
fbcase 1800 "a bound that is not a whole number is no override" SG_GATE_FULL_BOUND_SECONDS=abc
{ fpass 400; hrow "selected 9 files" delta pass - 1500; hrow "all: x" full pass - 1500 reused; hrow "all: x" full pass - 1500 gated yes
  hrow "all: x" full fail "the base battery failed at t" 1500; hrow "selected 0 files" delta unproven "no pytest ran" 1500; } > "$fbh/gate-history"
fbcase 900 "a delta, reused, failed or narrowed row measures nothing"
{ fpass 400; hrow "all: x" full unproven "bounded: the battery passed its 905s deadline" 905; } > "$fbh/gate-history"
fbcase 1358 "a bounded full gate raises the bound by its own wall"
hrow "all: x" full unproven "bounded: the battery passed its 905s deadline" 905 > "$fbh/gate-history"
fbcase 1358 "a bounded full gate alone is a measure"
hrow "all: x" full fail "the base battery failed at t" 400 > "$fbh/gate-history"; fbcase 1800 "a window with no pass and no bounded gate answers the cap"
{ fpass 1100; for _ in $(seq 20); do fpass 400; done; } > "$fbh/gate-history"; fbcase 900 "a pass older than the last 20 full gates measures nothing"
{ fpass 1100; for _ in $(seq 19); do fpass 400; done; } > "$fbh/gate-history"; fbcase 1650 "a pass inside the last 20 full gates measures"
{ printf 'garbage\n'; printf 'x\tfast\tpass\tsha\ty\tall: x\tfull\tpass\t-\n'; fpass 400; } > "$fbh/gate-history"
fbcase 900 "a corrupt row is skipped and the answer stays a whole number"
printf '2000\tfast\tpass\tsha\t1000\tall: x\tfull\tpass\t-\t-\t-\t3\t-\n' > "$fbh/gate-history"
fbcase 1500 "a row from before the wall field measures finish minus start"
# The gate widens its clock only once the battery named the full scope, and it keeps the wider clock once named: a battery
# that writes `full` and then `delta` (the leak #2379 found) still finishes, and a delta battery keeps the delta bound.
# The `full` stands 3 s before the rewrite, so the watchdogs' looks (1 s in fast-gate, 2 s in gate.sh) read it under load too.
vstub fullthendelta 'printf "full\n" > "$GATE_SCOPE_FILE"; sleep 3; printf "delta\n" > "$GATE_SCOPE_FILE"; sleep 8; exit 0'
fbst=$T/fbst; mkdir -p "$fbst"   # its own state: a red an earlier case left on this tree would stand over these attempts
s0=$(date +%s); out=$(SG_STATE="$fbst" SG_GATE_BOUND_SECONDS=1 SG_GATE_FULL_BOUND_SECONDS=30 vfg fullthendelta); rc=$?; el=$(( $(date +%s) - s0 ))
[ $rc = 0 ] && [ "$el" -ge 11 ] && ok "2379 a full battery runs past the delta bound to the full bound, and a later delta never shortens it (${el}s)" \
  || ko "2379 full battery past the delta bound: rc $rc in ${el}s, last '$(last "$out")'"
[ "$(tail -n 1 "$fbst/gate-history" | cut -f7)" = full ] && ok "2379 the history records the full scope the gate latched" || ko "2379 latched scope: '$(tail -n 1 "$fbst/gate-history" 2>/dev/null)'"
vstub fullhang 'printf "full\n" > "$GATE_SCOPE_FILE"; sleep $HANG_NAP'
s0=$(date +%s); out=$(SG_STATE="$fbst" SG_GATE_BOUND_SECONDS=1 SG_GATE_FULL_BOUND_SECONDS=4 vfg fullhang); rc=$?; el=$(( $(date +%s) - s0 ))
[ $rc = 4 ] && [ "$el" -ge 3 ] && [ "$el" -lt 20 ] && case "$(last "$out")" in *"bounded"*" 4s "*|*"bounded"*" its 4s"*) true;; *) false;; esac \
  && ok "2379 a full battery past its full bound is bounded and the line names the full bound (${el}s)" || ko "2379 full hang: rc $rc in ${el}s, last '$(last "$out")'"
pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1 && ko "2379 the full battery's tree survived its bound" || ok "2379 the full bound kills the battery's tree"
# A caller that hands its own clock and no full clock (a proof) is never widened: the full scope keeps the caller's deadline.
s0=$(date +%s); out=$(cd "$R" && SG_STATE="$fbst" SG_SERIAL_HELD=gate GATE_DEADLINE_EPOCH=$(( $(date +%s) + 2 )) SG_GATE_FULL_BOUND_SECONDS=30 \
  env -u GATE_FULL_DEADLINE_EPOCH -u GATE_SCOPE_FILE SG_FAST_BASE=dev SG_FAST_CMD="bash $T/fullhang.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1); rc=$?; el=$(( $(date +%s) - s0 ))
[ $rc = 4 ] && [ "$el" -lt 10 ] && ok "2379 a caller's clock with no full clock is never widened (${el}s)" || ko "2379 a caller's clock was widened: rc $rc in ${el}s, last '$(last "$out")'"
pkill -f "sleep $HANG_NAP" 2>/dev/null
# The battery's pytest never reads or writes the gate's record or clock: a test that writes the inherited scope file turned
# every landing row of the history into `delta` (#2379). A stub interpreter plays ruff, mypy, the selector and that test.
lk=$T/leak; mkdir -p "$lk"
printf '#!/bin/bash\ncase "$1 $2" in\n  "-m squad.testselect") echo "all: stub";;\n  "-m pytest") [ -z "${GATE_SCOPE_FILE:-}" ] || echo delta > "$GATE_SCOPE_FILE"; env | /usr/bin/grep -cE "^(GATE_|SG_GATE_FULL_)" > "%s/vars"; echo "1 passed";;\nesac\nexit 0\n' "$lk" > "$lk/py"; chmod +x "$lk/py"
lcmd=$(. "$d/base-battery.sh"; SG_GATE_WORKERS=1 sg_base_battery "$lk/py" dev landing)
(cd "$R" && env GATE_SCOPE_FILE="$lk/scope" GATE_SELECT_FILE="$lk/sel" GATE_DEADLINE_EPOCH=1 SG_GATE_FULL_BOUND_SECONDS=7 bash -c "$lcmd") >/dev/null 2>&1
[ "$(cat "$lk/scope" 2>/dev/null)" = full ] && [ "$(cat "$lk/vars" 2>/dev/null)" = 0 ] \
  && ok "2379 the battery's pytest sees no gate record or clock, so the scope stays the battery's" || ko "2379 leak: scope '$(cat "$lk/scope" 2>/dev/null)', gate names seen by pytest '$(cat "$lk/vars" 2>/dev/null)'"

# (#1086) The gate's interpreter is sg_gate_python's answer (caps.sh): SG_GATE_PY, else the checkout's venv, else the venv
# beside the worktree's common git directory, else python3. The gate calls it for the code-quality reading, so a stub in the
# venv logs the exact interpreter the gate used. A checkout with no venv of its own must reach the venv beside it, never a
# python3 that lacks mypy: the old hand-rolled lookup sent a child's worktree there, and its gate ended absent.
ps=$T/interp; mkdir -p "$ps"
pycase() {  # <checkout>: one passing gate whose quality reading runs through the interpreter <checkout> resolves
  rm -f "$T/co-py.log"
  (cd "$R" && env -u SG_GATE_PY SG_CHECKOUT="$1" SG_STATE="$ps" SG_FAST_BASE=dev SG_FAST_CMD="bash $T/base.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1)
}
co=$T/co-venv; mkdir -p "$co/.venv/bin"
printf '#!/bin/bash\necho "$0 $*" >> "%s"\nexit 0\n' "$T/co-py.log" > "$co/.venv/bin/python"; chmod +x "$co/.venv/bin/python"
out=$(pycase "$co"); rc=$?
/usr/bin/grep -qF "$co/.venv/bin/python " "$T/co-py.log" && /usr/bin/grep -q "quality-reading.py" "$T/co-py.log" \
  && ok "the gate runs through sg_gate_python: the checkout's own venv answers" || ko "checkout venv: rc $rc, log '$(cat "$T/co-py.log" 2>/dev/null)' $out"
# No venv in the checkout itself, and a venv beside its common git directory: sg_gate_python answers that one, not python3.
$G -C "$R" worktree add -q "$T/wt-novenv" -b wt-novenv
mkdir -p "$R/.venv/bin"
printf '#!/bin/bash\necho "$0 $*" >> "%s"\nexit 0\n' "$T/co-py.log" > "$R/.venv/bin/python"; chmod +x "$R/.venv/bin/python"
out=$(pycase "$T/wt-novenv"); rc=$?
/usr/bin/grep -qF "$R/.venv/bin/python " "$T/co-py.log" && /usr/bin/grep -q "quality-reading.py" "$T/co-py.log" \
  && ok "a checkout with no venv of its own reaches sg_gate_python's answer: the venv beside its common git directory, never python3" \
  || ko "worktree venv: rc $rc, log '$(cat "$T/co-py.log" 2>/dev/null)' $out"
# No SG_CHECKOUT at all, as a hand gate started in the fix's worktree has none: sg_gate_python reads the caller's own tree
# through git, so it still reaches the venv beside the common git directory, never a python3 that lacks mypy.
pycase_nc() {  # <dir>: one passing gate run from <dir> with neither SG_GATE_PY nor SG_CHECKOUT
  rm -f "$T/co-py.log"
  (cd "$1" && env -u SG_GATE_PY -u SG_CHECKOUT SG_STATE="$ps" SG_FAST_BASE=dev SG_FAST_CMD="bash $T/base.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1)
}
out=$(pycase_nc "$T/wt-novenv"); rc=$?
/usr/bin/grep -qF "$R/.venv/bin/python " "$T/co-py.log" && /usr/bin/grep -q "quality-reading.py" "$T/co-py.log" \
  && ok "a gate with no SG_CHECKOUT reads its own tree through git and reaches the venv beside the common git directory" \
  || ko "no SG_CHECKOUT: rc $rc, log '$(cat "$T/co-py.log" 2>/dev/null)' $out"
# (#1724) The answer is read by a caller in another directory, as the slow-cap case reads it: a relative answer is resolved
# against that caller's cwd, so its exec fails with 126. Whenever sg_gate_python names a file it prints an absolute path.
ab=$T/abs-venv; mkdir -p "$ab/.venv/bin"
printf '#!/bin/bash\nexit 0\n' > "$ab/.venv/bin/python"; chmod +x "$ab/.venv/bin/python"
got=$(cd "$ab" && env -u SG_CHECKOUT -u SG_GATE_PY bash -c '. "$1/caps.sh"; sg_gate_python' _ "$d")
case "$got" in
  /*) ok "with no SG_CHECKOUT sg_gate_python prints an absolute path for the venv in the caller's directory";;
  *) ko "relative interpreter: '$got' from $ab";;
esac
/usr/bin/grep -q 'sg_gate_python' "$d/fast-gate.sh" && ok "fast-gate names sg_gate_python" || ko "fast-gate keeps its own interpreter lookup"
/usr/bin/grep -q 'py=python3' "$d/fast-gate.sh" && ko "fast-gate keeps a hand-rolled interpreter fallback" || ok "fast-gate keeps no hand-rolled interpreter fallback"

# (#1436, #1865) A child's own gate asks for one gate slot, so three children land at once on a three-slot budget; an entry or
# a tip gate keeps the full count. The budget is the plugin's here, so the count a gate took reaches its command as
# SQUAD_GATE_WORKERS. The loop's own environment sets SG_GATE_WORKERS (env.sh exports six), so a child's count is never read
# from that inherited variable: the child overrides it, and a caller that wants another count names SG_GATE_WORKERS_CHILD.
ws=$T/ws; mkdir -p "$ws"
printf '#!/bin/bash\nprintf "%%s\\n" "$SQUAD_GATE_WORKERS" > "%s"\n' "$T/workers" > "$T/wbase.sh"
wcase() {  # <env assignment>...: one passing child gate, the count it took written by its battery stub
  rm -f "$T/workers"
  (cd "$R" && env -u SG_GATE_WORKERS -u SG_GATE_WORKERS_CHILD -u SG_GATE_CALLER "$@" SG_STATE="$ws" SG_FAST_BASE=dev SG_FAST_CMD="bash $T/wbase.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" >/dev/null 2>&1)
  cat "$T/workers" 2>/dev/null
}
w=$(wcase); [ "$w" = 1 ] && ok "a child gate with no count named takes one gate slot" || ko "child gate slots: '$w', want 1"
w=$(wcase SG_GATE_CALLER=entry); [ "$w" = 6 ] && ok "an entry gate keeps the full count" || ko "entry gate slots: '$w', want 6"
w=$(wcase SG_GATE_CALLER=entry SG_GATE_WORKERS=3); [ "$w" = 3 ] && ok "a count named by the caller wins over the kind's default" || ko "named count: '$w', want 3"
# (#1865) The inherited default is the loop's, never the child's decision: a child in the loop's own environment still takes
# its own count, and only SG_GATE_WORKERS_CHILD names another.
w=$(wcase SG_GATE_WORKERS=6); [ "$w" = 1 ] && ok "a child overrides the loop's inherited SG_GATE_WORKERS" || ko "inherited count won: '$w', want 1"
w=$(wcase SG_GATE_WORKERS=6 SG_GATE_WORKERS_CHILD=2); [ "$w" = 2 ] && ok "a child's own count is named by SG_GATE_WORKERS_CHILD" || ko "child named count: '$w', want 2"
# The take reaches the lease, not only the command: the history row is where a count that never reached the slots would show.
hcase() {  # <env assignment>...: field 12 (slots) of the one history row a fresh state records
  local hs; hs=$(mktemp -d "$T/hs.XXXXXX")
  (cd "$R" && env -u SG_GATE_WORKERS -u SG_GATE_WORKERS_CHILD -u SG_GATE_CALLER "$@" SG_FAST_MEMO=0 SG_STATE="$hs" SG_FAST_BASE=dev SG_FAST_CMD="bash $T/wbase.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" >/dev/null 2>&1)
  awk -F'\t' 'NR==1{print $12}' "$hs/gate-history" 2>/dev/null
}
h=$(hcase SG_GATE_WORKERS=6); [ "$h" = 1 ] && ok "a child's take reaches the lease: the history row reads one slot" || ko "history slots: '$h', want 1"
# (#1783) The caller reaches the history row: a gate that names no caller is the child's own check, and gate.sh runs as a new
# process, so the caller is exported or the row reads `other`.
ccase() {  # <env assignment>...: field 14 of the one history row a fresh state records
  local cs; cs=$(mktemp -d "$T/cs.XXXXXX")
  (cd "$R" && env -u SG_GATE_WORKERS -u SG_GATE_CALLER "$@" SG_FAST_MEMO=0 SG_STATE="$cs" SG_FAST_BASE=dev SG_FAST_CMD="bash $T/wbase.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" >/dev/null 2>&1)
  tail -1 "$cs/gate-history" 2>/dev/null | cut -f14
}
c=$(ccase); [ "$c" = child ] && ok "a gate that names no caller is recorded as the child's" || ko "unnamed caller recorded as '$c', want child"
c=$(ccase SG_GATE_CALLER=entry); [ "$c" = entry ] && ok "a named caller is recorded as named" || ko "entry caller recorded as '$c', want entry"
# (#1872, #2085) A child's count is one whatever the selector answers: its battery is a delta, and a change the delta cannot
# place runs no pytest at all, so no child gate is ever a full battery that would need the whole count to fit its bound. The
# full-battery wait belongs to the delivery's own gates. The stub gate.sh records the values that reached the lock, so the
# case reads what a slot take would see, never a word in the script.
ps=$T/pscripts; mkdir -p "$ps"
cp "$d/fast-gate.sh" "$d/base-battery.sh" "$d/caps.sh" "$ps"/
printf '#!/bin/bash\nprintf "wait=%%s caller=%%s workers=%%s\\n" "${SG_GATE_SLOT_WAIT:-}" "${SG_GATE_CALLER:-}" "${SG_GATE_WORKERS:-}" >> "%s/gate.log"\nexit 0\n' "$T" > "$ps/gate.sh"
psel() {  # <selector answer> [env assignment]...: one child gate, the values its lock call carried, read back
  rm -f "$T/gate.log"
  printf '#!/bin/bash\necho "$*" >> "%s/pargv.log"\ncase "$*" in *squad.testselect*) echo "%s";; esac\nexit 0\n' "$T" "$1" > "$T/pselpy"; chmod +x "$T/pselpy"; shift
  (cd "$R" && env -u SG_GATE_WORKERS -u SG_GATE_WORKERS_CHILD -u SG_GATE_CALLER -u SG_SERIAL_PRIORITY -u SG_FAST_CMD "$@" SG_STATE="$(mktemp -d "$T/ps.XXXXXX")" SG_FAST_BASE=dev SG_GATE_PY="$T/pselpy" SG_FAST_RETRY_CMD=false bash "$ps/fast-gate.sh" >/dev/null 2>&1)
  /usr/bin/tail -1 "$T/gate.log" 2>/dev/null
}
o=$(psel "$(printf 'tests/test_a.py\ntests/test_b.py')")
case "$o" in "wait= "*) ok "a delta child takes one slot and waits for no slot inside the lock";; *) ko "delta child: '$o', want workers=1 and no slot wait";; esac
case "$o" in *"workers=1"*) ok "a delta child takes one slot";; *) ko "delta child: '$o', want workers=1";; esac
o=$(psel "all: the delta cannot place the change")
case "$o" in *"workers=1"*) ok "an all child takes one slot: its change runs no full battery";; *) ko "all child: '$o', want workers=1";; esac
case "$o" in "wait= "*) ok "an all child waits for no slot inside the lock";; *) ko "all child wait: '$o', want none";; esac
o=$(psel "all: the delta cannot place the change" SG_GATE_CALLER=entry)
case "$o" in *"workers=1"*) ko "an entry gate took the child's scope count: '$o'";; *) ok "an entry gate keeps its own count";; esac
case "$o" in "wait= "*) ok "an entry gate waits for no slot inside the lock";; *) ko "entry wait: '$o', want none";; esac

# (#1557, #1726) The gate proves the serial 2 s line end to end: a test whose unmarked call sits over the project's heavy
# line is recorded by the parallel pass, at that line, and red when only it runs again serially. The retry keeps the line, so
# it never clears the finding as saturation. The trial repo sets its own line at 0.05 s, so a 0.3 s call fails whatever the
# machine load and the case stays inside the gate's budget.
sc=$T/slowcap-repo; mkdir -p "$sc/tests"
cp "$d/../../../tests/slowcap.py" "$sc/slowcap.py"
printf 'pytest_plugins = ["slowcap"]\n' > "$sc/conftest.py"
printf '[tool.pytest.ini_options]\naddopts = "-p no:cacheprovider"\nheavy_cap_seconds = "0.05"\n' > "$sc/pyproject.toml"
printf 'import time\n\ndef test_unmarked_slow():\n    time.sleep(0.3)\n' > "$sc/tests/test_slow.py"
pyreal=$(cd "$d/../../.." && env -u SG_GATE_PY -u SG_CHECKOUT bash -c '. contrib/squad-goal/scripts/caps.sh; sg_gate_python')
printf '#!/bin/bash\necho "$*" >> "%s/sc-argv.log"\ncase "$*" in\n  *squad.testselect*) echo tests/test_slow.py;;\n  *"-m pytest"*) exec %q "$@";;\nesac\nexit 0\n' "$T" "$pyreal" > "$sc/py"; chmod +x "$sc/py"
git init -q -b dev "$sc"; $G -C "$sc" add -A; $G -C "$sc" commit -q -m base
slowgate() { (cd "$sc" && env -u SG_FAST_CMD -u SG_TEST_SELECT -u SG_FAST_RETRY_CMD SG_STATE="$T/sc-st" SG_FAST_LOG_DIR="$T/sc-logs" SG_FAST_BASE=dev SG_FAST_MEMO=0 SG_GATE_PY="$sc/py" bash "$d/fast-gate.sh" 2>&1); }
out=$(slowgate); rc=$?
[ $rc = 1 ] && case "$(last "$out")" in "fast-gate: pytest: "*"tests/test_slow.py::test_unmarked_slow"*) true;; *) false;; esac \
  && ok "1726 a slow unmarked call over the line is recorded, run again serially, and fails the gate by name" || ko "slow unmarked call: rc $rc, last '$(last "$out")'"
/usr/bin/grep -qx -- "-m pytest -p no:deepeval -p no:xdist -m not heavy tests/test_slow.py::test_unmarked_slow" "$T/sc-argv.log" \
  && ! /usr/bin/grep -qx -- "-m pytest -p no:deepeval -p no:xdist -m not heavy tests/test_slow.py" "$T/sc-argv.log" \
  && ok "1726 the serial pass covers only the recorded call, never the whole selection" || ko "1726 serial scope: '$(cat "$T/sc-argv.log" 2>&1)'"
if /usr/bin/grep -q "line for an unmarked call" "$T/sc-logs"/fast-gate-*.log 2>/dev/null; then ok "the log names the unmarked call over the line"
else ko "slow log: '$(cat "$T/sc-logs"/fast-gate-*.log 2>&1 | tail -n 5)'"; fi
# (#1726) A call that is slow only under parallel load is cleared by its serial run: the trial test sleeps 0.3 s inside an
# xdist worker alone, so the parallel pass records it and the serial run passes it, and the gate passes.
sl=$T/load-repo; mkdir -p "$sl/tests"
cp "$d/../../../tests/slowcap.py" "$sl/slowcap.py"
printf 'pytest_plugins = ["slowcap"]\n' > "$sl/conftest.py"
printf '[tool.pytest.ini_options]\naddopts = "-p no:cacheprovider"\nheavy_cap_seconds = "0.05"\n' > "$sl/pyproject.toml"
printf 'import os, time\n\ndef test_slow_under_load_only():\n    if os.environ.get("PYTEST_XDIST_WORKER"):\n        time.sleep(0.3)\n' > "$sl/tests/test_load.py"
printf '#!/bin/bash\necho "$*" >> "%s/sl-argv.log"\ncase "$*" in\n  *squad.testselect*) echo tests/test_load.py;;\n  *"-m pytest"*) exec %q "$@";;\nesac\nexit 0\n' "$T" "$pyreal" > "$sl/py"; chmod +x "$sl/py"
git init -q -b dev "$sl"; $G -C "$sl" add -A; $G -C "$sl" commit -q -m base
loadgate() { (cd "$sl" && env -u SG_FAST_CMD -u SG_TEST_SELECT -u SG_FAST_RETRY_CMD SG_STATE="$T/sl-st" SG_FAST_LOG_DIR="$T/sl-logs" SG_FAST_BASE=dev SG_FAST_MEMO=0 SG_GATE_PY="$sl/py" bash "$d/fast-gate.sh" 2>&1); }
out=$(loadgate); rc=$?
[ $rc = 0 ] && [ "$(/usr/bin/grep -- '-m pytest' "$T/sl-argv.log" | tail -n 1)" = "-m pytest -p no:deepeval -p no:xdist -m not heavy tests/test_load.py::test_slow_under_load_only" ] \
  && ok "1726 a call slow only under parallel load passes its own serial run, and the gate passes" || ko "1726 load-only call: rc $rc: '$(cat "$T/sl-argv.log" 2>&1)' '$(last "$out")'"

# (#2421) The daily pass and shell-suites.sh hand the suite a temp HOME (#2307), and this machine's python3 imports pytest's
# pygments only from the user site under HOME. The trial battery then could not import pytest, and the gate answered unknown
# twice where the case above reads its red. The same trial runs on python3 under a temp HOME, and the suite hands the real
# user base down (test-hubstream.sh), so the battery starts its pytest and names the slow call.
sh=$T/slowcap-home; mkdir -p "$sh/tests"
cp "$d/../../../tests/slowcap.py" "$sh/slowcap.py"
printf 'pytest_plugins = ["slowcap"]\n' > "$sh/conftest.py"
printf '[tool.pytest.ini_options]\naddopts = "-p no:cacheprovider"\nheavy_cap_seconds = "0.05"\n' > "$sh/pyproject.toml"
printf 'import time\n\ndef test_unmarked_slow():\n    time.sleep(0.3)\n' > "$sh/tests/test_slow.py"
printf '#!/bin/bash\necho "$*" >> "%s/sh-argv.log"\ncase "$*" in\n  *squad.testselect*) echo tests/test_slow.py;;\n  *"-m pytest"*) exec python3 "$@";;\nesac\nexit 0\n' "$T" > "$sh/py"; chmod +x "$sh/py"
git init -q -b dev "$sh"; $G -C "$sh" add -A; $G -C "$sh" commit -q -m base
hmh=$(mktemp -d)
outh=$(cd "$sh" && env -u SG_FAST_CMD -u SG_TEST_SELECT -u SG_FAST_RETRY_CMD HOME="$hmh" SG_STATE="$T/sh-st" SG_FAST_LOG_DIR="$T/sh-logs" SG_FAST_BASE=dev SG_FAST_MEMO=0 SG_GATE_PY="$sh/py" bash "$d/fast-gate.sh" 2>&1); rch=$?
rm -rf "$hmh"
[ $rch = 1 ] && case "$(last "$outh")" in "fast-gate: pytest: "*"tests/test_slow.py::test_unmarked_slow"*) true;; *) false;; esac \
  && ok "2421 the trial battery starts its pytest under the runner's temp HOME and names the slow call" || ko "2421 temp HOME: rc $rch, last '$(last "$outh")'"

# (#1684) A gate that cannot take its slots proved nothing and broke nothing: the machine's budget is full, so the fast gate
# answers unproven (exit 4) on one line that names the budget. Called out as 73, it read as a red of the branch, and three
# waves on 2026-11-13 threw a whole child's work away for a budget another gate held.
rm -rf "$T/caps"; mkdir -p "$T/caps"
sleep 120 >/dev/null 2>&1 & capslive=$!
# (#1865) The budget is filled to its ceiling, never to a count that happens to leave a slot free: the cap is the plugin's here,
# and a child asks for one slot, so a take that left one free would leave this gate passing.
PYTHONPATH="$(cd "$d/../../.." && pwd)" python3 -m squad.caps take gates other --count 999 --pid "$capslive" --target "$R" >/dev/null 2>&1
# (#2042) The memo is off: a pass kept from the case above answers the same tree with no lock and no slot (ADR-0168), so the
# call below took no slot, read a free budget and passed. This case measures the capacity path, never the reuse.
out=$(SG_FAST_MEMO=0 SG_CALL_DEADLINE_SECONDS=2 fg); rc=$?
[ $rc = 4 ] && ok "a gate with no slot to take answers unproven (exit 4), never 73" || ko "capacity answer: rc $rc, '$(last "$out")'"
case "$(last "$out")" in *"budget is full"*) ok "the capacity answer names the machine's budget on its last line";; *) ko "capacity line: '$(last "$out")'";; esac
# (#1715) The line is the one thing the child reads: it carries the word the request names and what the child does next, and it
# no longer says the gate does not pass, which a child read as a failure of its run.
case "$(last "$out")" in "fast-gate: inconclusive: "*"commit and report done"*"ADR-0164"*) ok "1715 the capacity line tells the child to commit and report done";; *) ko "1715 capacity line: '$(last "$out")'";; esac
case "$(last "$out")" in ""|*"does not pass"*) ko "1715 the capacity line is empty or still reads as a failed gate";; *) ok "1715 the capacity line names no failed gate";; esac
PYTHONPATH="$(cd "$d/../../.." && pwd)" python3 -m squad.caps release gates other >/dev/null 2>&1
kill "$capslive" 2>/dev/null; wait "$capslive" 2>/dev/null   # reaped here, so the kill prints no job line of its own

# (ADR-0166) A child that finds no gate slot waits for one inside its deadline; the wait is gate wait, never run time. A stub
# gate refuses its first calls with 73 and then runs its command, so each case takes the wait with no real lock or slot.
sg=$T/sg; mkdir -p "$sg"; cp "$d/fast-gate.sh" "$d/base-battery.sh" "$d/caps.sh" "$sg/"
printf '#!/bin/bash\nn=$(cat "$GATE_STUB_N" 2>/dev/null || echo 0); echo $((n+1)) > "$GATE_STUB_N"\n[ "$n" -lt "${GATE_STUB_73:-0}" ] && exit 73\nSG_SERIAL_HELD=gate exec "$@"\n' > "$sg/gate.sh"
printf '#!/bin/bash\nsleep 30\n' > "$T/hang.sh"
sgfg() { rm -f "$T/gate.n" "$T/ran.log" "$SG_STATE/gate-status"; (cd "$R" && env -u SG_SERIAL_HELD -u SG_GATE_CALLER -u SG_GATE_WORKERS -u GATE_DEADLINE_EPOCH -u GATE_BOUND_SECONDS -u GATE_START_EPOCH -u GATE_DELTA_RESERVE_SECONDS GATE_STUB_N="$T/gate.n" SG_FAST_BASE=dev SG_FAST_CMD="${SG_FAST_CMD:-bash $T/base.sh}" SG_FAST_RETRY_CMD=false SG_GATE_BOUND_SECONDS="${SG_GATE_BOUND_SECONDS:-60}" SG_CALL_DEADLINE_SECONDS="${SG_CALL_DEADLINE_SECONDS:-60}" bash "$sg/fast-gate.sh" 2>&1); }

out=$(GATE_STUB_73=2 sgfg); rc=$?; calls=$(cat "$T/gate.n" 2>/dev/null)
[ $rc = 0 ] && [ "$calls" = 3 ] && ok "1760 no slot twice, then a slot: the child waits and its gate passes" || ko "1760 no slot then a slot: rc $rc calls '$calls' '$(last "$out")'"
case "$out" in *"gate wait"*"not run time"*) ok "1760 the wait is named as gate wait, not run time";; *) ko "1760 no gate-wait line: '$out'";; esac

out=$(GATE_STUB_73=99 SG_CALL_DEADLINE_SECONDS=2 sgfg); rc=$?
[ $rc = 4 ] && case "$(last "$out")" in *"budget is full"*) ok "1760 no slot to the deadline: unproven (exit 4) on the budget line";; *) ko "1760 deadline budget line: '$(last "$out")'";; esac || ko "1760 no slot to the deadline: rc $rc '$(last "$out")'"

out=$(SG_CALL_DEADLINE_SECONDS=2 SG_GATE_BOUND_SECONDS=2 SG_FAST_CMD="bash $T/hang.sh" GATE_STUB_73=1 sgfg); rc=$?
[ $rc = 4 ] && case "$(last "$out")" in *"bounded"*) ok "1760 the bound with no defect named is unproven (exit 4), never a red";; *) ko "1760 bound line: '$(last "$out")'";; esac || ko "1760 bound: rc $rc '$(last "$out")'"

out=$(GATE_STUB_73=1 BASE_FAIL=1 sgfg); rc=$?
[ $rc = 1 ] && case "$out" in *"test_a"*) ok "1760 a measured red after a slot wait still fails the run (exit 1)";; *) ko "1760 real red: '$out'";; esac || ko "1760 real red: rc $rc '$(last "$out")'"

# (#2330) A tip check's gate that waited out its slot wait answers 75 with the budget's line. The fast gate passes that code
# through unchanged: a 4 reads as an unproven check, and the batch landing then lands each run alone instead of waiting.
printf '#!/bin/bash\necho "caps: gate-1 waited 2s for caps.gates, held by child-gate-7" >&2\nexit 75\n' > "$sg/gate.sh"
out=$(cd "$R" && env -u SG_SERIAL_HELD -u SG_GATE_WORKERS SG_GATE_CALLER=tip SG_FAST_BASE=dev SG_FAST_CMD="bash $T/base.sh" SG_FAST_RETRY_CMD=false bash "$sg/fast-gate.sh" 2>&1); rc=$?
[ $rc = 75 ] && ok "2330 a tip check's waited-out gate answers 75, never 4" || ko "2330 tip wait: rc $rc '$(last "$out")'"
case "$out" in *"waited 2s for caps.gates"*) ok "2330 the waited-out line reaches the caller";; *) ko "2330 no waited-out line: '$out'";; esac
case "$out" in *inconclusive*) ko "2330 the waited-out tip check reads as inconclusive: '$out'";; *) ok "2330 the waited-out tip check is not read as inconclusive";; esac

# (#1927) A suite the fast gate runs never inherits GATE_REUSE_FILE: gate.sh --inner exports that file for the gate's own
# verdict, and a fixture gate inside a suite that kept it would write the outer gate's reuse from its own memo hit, and its
# row would vanish. The fast gate found this on its own suite, whose memo row went missing under the gate.
$G -C "$R" checkout -q -b gui/reuse dev
printf '#!/bin/bash\necho "suite sees reuse=${GATE_REUSE_FILE:-none}" >> "%s/ran.log"\n' "$T" > "$S/test-reuse.sh"
printf '#!/bin/bash\necho reuse\n' > "$S/reuse.sh"; $G -C "$R" add -A; $G -C "$R" commit -q -m reuse-work
: > "$T/outer-reuse"
out=$(GATE_REUSE_FILE=$T/outer-reuse fg); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx "suite sees reuse=none" "$T/ran.log" && [ ! -s "$T/outer-reuse" ] \
  && ok "1927 a suite the fast gate runs inherits no GATE_REUSE_FILE" \
  || ko "1927 reuse leak: rc $rc ran '$(cat "$T/ran.log" 2>&1)' outer '$(cat "$T/outer-reuse" 2>&1)'"

# (#2032) A child's scratch log in its worktree is no change the delta cannot place: the scratch place is git-ignored, and the
# selector never reads it, so the delta keeps its scope. The repo's own .gitignore and a bare tree each prove one half.
sg=$T/scratch-repo; mkdir -p "$sg/tests" "$sg/squad"
printf 'def test_one():\n    pass\n' > "$sg/tests/test_one.py"
cp "$d/../../../squad/testselect_inert.txt" "$sg/squad/"; cp "$d/../../../.gitignore" "$sg/.gitignore"
git init -q -b dev "$sg"; $G -C "$sg" add -A; $G -C "$sg" commit -q -m base
$G -C "$sg" checkout -q -b gui/2032; printf 'def test_one():\n    assert True\n' > "$sg/tests/test_one.py"; $G -C "$sg" commit -q -am change
mkdir -p "$sg/.scratch"; echo scratch > "$sg/.scratch/fast-gate-1983-2.log"
sel=$(cd "$sg" && PYTHONPATH="$(cd "$d/../../.." && pwd)" "$pyreal" -m squad.testselect --stage fast --base dev 2>/dev/null)
[ "$sel" = "tests/test_one.py" ] && ok "2032 a scratch log in the git-ignored scratch place keeps the delta scope" || ko "2032 scratch log with the repo .gitignore: '$sel', want the delta tests/test_one.py"
sb=$T/scratch-bare; mkdir -p "$sb/tests" "$sb/squad"; printf 'def test_one():\n    pass\n' > "$sb/tests/test_one.py"
cp "$d/../../../squad/testselect_inert.txt" "$sb/squad/"
git init -q -b dev "$sb"; $G -C "$sb" add -A; $G -C "$sb" commit -q -m base
$G -C "$sb" checkout -q -b gui/2032; printf 'def test_one():\n    assert True\n' > "$sb/tests/test_one.py"; $G -C "$sb" commit -q -am change
mkdir -p "$sb/.scratch"; echo scratch > "$sb/.scratch/fast-gate-1983-2.log"
sel=$(cd "$sb" && PYTHONPATH="$(cd "$d/../../.." && pwd)" "$pyreal" -m squad.testselect --stage fast --base dev 2>/dev/null)
[ "$sel" = "tests/test_one.py" ] && ok "2032 the selector skips the scratch place even when git does not ignore it" || ko "2032 scratch log without .gitignore: '$sel', want the delta tests/test_one.py"

# (#2569) The gate runs the selected suites in parallel, up to the count it was granted, and a suite red under the pool runs once
# more alone. Overlap is proved from the work: two suites that each wait for the other's marker file pass only side by side.
# A suite the deadline kills prints KILLED, a suite it never started is named not run, and a real red wins over a kill.
pnew() {  # <case>: a throwaway repository with an empty base; pm is the case's marker directory
  pr=$T/par-$1; pm=$T/pm-$1; PS=$pr/contrib/squad-goal/scripts; rm -rf "$pr" "$pm"; mkdir -p "$PS" "$pm"
  git init -q -b dev "$pr"; echo base > "$pr/x"; $G -C "$pr" add -A; $G -C "$pr" commit -q -m base; }
psuite() { printf '#!/bin/bash\n%s\n' "$2" > "$PS/test-$1.sh"; }   # <name> <body>
ptip() { $G -C "$pr" checkout -q -b gui/p; $G -C "$pr" add -A; $G -C "$pr" commit -q -m suites; }
pwait() {  # <marker>: wait up to 10 s for a marker file; the exit code says whether it came
  echo "i=0; while [ ! -e $1 ] && [ \$i -lt 100 ]; do sleep 0.1; i=\$((i+1)); done; [ -e $1 ]"; }
pgate() {  # <env>...: one gate through gate.sh, so the granted count is the one gate.sh takes
  (cd "$pr" && env -u SG_SERIAL_HELD -u SG_GATE_CALLER -u SG_GATE_WORKERS -u GATE_DEADLINE_EPOCH -u GATE_BOUND_SECONDS SG_FAST_BASE=dev SG_FAST_CMD=true SG_FAST_RETRY_CMD=false SG_FAST_MEMO=0 "$@" bash "$d/fast-gate.sh" 2>&1); }
pheld() {  # <granted> <env>...: the gate inside a held lock, with that granted count and its verdict in $T/oc
  local g=$1; shift; rm -f "$T/oc"
  (cd "$pr" && env -u GATE_DEADLINE_EPOCH -u GATE_BOUND_SECONDS SG_SERIAL_HELD=gate SQUAD_GATE_WORKERS="$g" GATE_OUTCOME_FILE="$T/oc" SG_FAST_BASE=dev SG_FAST_CMD=true SG_FAST_RETRY_CMD=false SG_FAST_MEMO=0 SG_GATE_CALLER=tip "$@" bash "$d/fast-gate.sh" 2>&1); }
pfield() { cut -f"$1" "$T/oc" 2>/dev/null; }   # 1 outcome, 2 reason, 3 ran, 4 not run
pnohang() { ! pgrep -f "sleep $HANG_NAP" >/dev/null 2>&1; }

pnew ov; psuite a "touch $pm/a; $(pwait "$pm/b")"; psuite b "touch $pm/b; $(pwait "$pm/a")"; ptip
out=$(pgate SG_GATE_CALLER=tip SG_GATE_WORKERS=2); rc=$?
[ $rc = 0 ] && ok "2569 two suites that wait for each other's marker pass: the granted count runs them side by side" || ko "2569 overlap: rc $rc: $out"

conc='mkdir -p CM; touch CM/$$; /usr/bin/wc -l < <(ls CM) | tr -d " " >> CK; sleep 0.7; rm -f CM/$$'
pnew gr; for n in w x y z; do psuite $n "$(printf '%s' "$conc" | sed "s|CM|$pm/c|g; s|CK|$pm/conc|")"; done; ptip
out=$(pheld 2 SG_GATE_WORKERS=6); rc=$?; mx=$(sort -n "$pm/conc" 2>/dev/null | tail -n 1)
[ $rc = 0 ] && [ "$mx" = 4 ] && ok "2598 the wave tip gate's pool is four, whatever count it was granted" || ko "2598 tip pool: rc $rc, most at once '$mx': $out"
rm -rf "$pm/c" "$pm/conc"
out=$(pheld 2 SG_GATE_WORKERS=6 SG_TIP_SUITE_POOL=2); rc=$?; mx=$(sort -n "$pm/conc" 2>/dev/null | tail -n 1)
[ $rc = 0 ] && [ "$mx" = 2 ] && ok "2598 SG_TIP_SUITE_POOL names the tip gate's pool" || ko "2598 named tip pool: rc $rc, most at once '$mx': $out"
rm -rf "$pm/c" "$pm/conc"
out=$(pgate); rc=$?; mx=$(sort -n "$pm/conc" 2>/dev/null | tail -n 1)
[ $rc = 0 ] && [ "$mx" = 1 ] && ok "2569 a child's gate (count 1) stays serial" || ko "2569 child count: rc $rc, most at once '$mx': $out"

pnew ord; psuite a 'sleep 1; echo A-end'; psuite b 'echo B-end'; ptip
out=$(pheld 2); rc=$?; got=$(printf '%s\n' "$out" | /usr/bin/grep -E '^(=== |--- |[AB]-end)' | tr '\n' '|')
[ $rc = 0 ] && [ "$got" = "=== test-a.sh|A-end|--- ok: test-a.sh|=== test-b.sh|B-end|--- ok: test-b.sh|" ] \
  && ok "2569 each suite's block prints whole, in selection order, though b ends first" || ko "2569 block order: rc $rc, '$got'"

pnew kl; psuite a 'sleep $HANG_NAP'; psuite b 'sleep $HANG_NAP'; psuite c "touch $pm/late"; psuite d "touch $pm/late"; ptip
out=$(pheld 2 SG_GATE_BOUND_SECONDS=3); rc=$?
[ $rc = 4 ] && [ "$(pfield 1)" = unproven ] && ok "2569 a deadline that kills two suites is unproven (exit 4)" || ko "2569 kill: rc $rc, outcome '$(cat "$T/oc" 2>&1)': $out"
printf '%s\n' "$out" | /usr/bin/grep -qx -- '--- KILLED: test-a.sh' && printf '%s\n' "$out" | /usr/bin/grep -qx -- '--- KILLED: test-b.sh' \
  && ! printf '%s\n' "$out" | /usr/bin/grep -q 'FAILED' && ok "2569 a killed suite prints KILLED, never FAILED" || ko "2569 KILLED lines: $out"
case "$(pfield 2)" in *test-a.sh*test-b.sh*) ok "2569 the reason names every killed suite";; *) ko "2569 reason: '$(pfield 2)'";; esac
[ ! -e "$pm/late" ] && case "$(pfield 4)" in *test-c.sh*test-d.sh*) true;; *) false;; esac && case "$(pfield 3)" in *test-c.sh*|*test-d.sh*) false;; *) true;; esac \
  && ok "2569 the suites the deadline kept from starting are named not run, never ran" || ko "2569 not run: late '$(ls "$pm" 2>&1)', ran '$(pfield 3)', not run '$(pfield 4)'"
pnohang && ok "2569 no killed suite's tree survives" || ko "2569 a killed suite's tree survived"

pnew k1; psuite a 'sleep $HANG_NAP'; psuite b "touch $pm/late"; ptip
out=$(pheld 1 SG_GATE_BOUND_SECONDS=3); rc=$?
[ $rc = 4 ] && [ ! -e "$pm/late" ] && printf '%s\n' "$out" | /usr/bin/grep -qx -- '--- KILLED: test-a.sh' && case "$(pfield 4)" in *test-b.sh*) true;; *) false;; esac \
  && ok "2569 a count of 1 gains the KILLED line and starts no suite past the deadline" || ko "2569 count 1 kill: rc $rc, late '$(ls "$pm" 2>&1)', not run '$(pfield 4)': $out"
pnohang || ko "2569 count 1: a killed suite's tree survived"

pnew fl
psuite a "echo run >> $pm/aruns; touch $pm/pa; i=0; while [ ! -e $pm/pb ] && [ \$i -lt 20 ]; do sleep 0.1; i=\$((i+1)); done; r=0; [ -e $pm/pb ] && r=1; : > $pm/adone; rm -f $pm/pa; exit \$r"
psuite b "touch $pm/pb; $(pwait "$pm/adone"); rm -f $pm/pb"; ptip
out=$(pheld 2); rc=$?
[ $rc = 0 ] && [ "$(/usr/bin/wc -l < "$pm/aruns" | tr -d ' ')" = 2 ] && printf '%s\n' "$out" | /usr/bin/grep -q 'rerun alone' \
  && ok "2569 a suite red beside another and green alone is run alone once more, and the gate passes" || ko "2569 rerun alone: rc $rc, runs '$(cat "$pm/aruns" 2>&1)': $out"

pnew rd; psuite c "echo run >> $pm/cruns; exit 1"; psuite d 'true'; ptip
out=$(pheld 2); rc=$?
[ $rc = 1 ] && [ "$(/usr/bin/wc -l < "$pm/cruns" | tr -d ' ')" = 2 ] && [ "$(pfield 1)" = fail ] && case "$(pfield 2)" in *test-c.sh*) true;; *) false;; esac \
  && ok "2569 a suite red alone too fails the gate and is named" || ko "2569 red alone: rc $rc, runs '$(cat "$pm/cruns" 2>&1)', outcome '$(cat "$T/oc" 2>&1)'"
rm -f "$pm/cruns"
out=$(pheld 1); rc=$?
[ $rc = 1 ] && [ "$(/usr/bin/wc -l < "$pm/cruns" | tr -d ' ')" = 1 ] && ok "2569 a count of 1 ran the red suite alone already, so it runs it once" || ko "2569 count 1 red: rc $rc, runs '$(cat "$pm/cruns" 2>&1)'"

pnew lo; psuite a "echo run >> $pm/aruns; exit 1"; psuite b 'sleep $HANG_NAP'; ptip
out=$(pheld 2 SG_GATE_BOUND_SECONDS=3); rc=$?
[ $rc = 4 ] && [ "$(pfield 1)" = unproven ] && [ "$(/usr/bin/wc -l < "$pm/aruns" | tr -d ' ')" = 1 ] && case "$(pfield 2)" in *test-a.sh*) case "$(pfield 2)" in *test-b.sh*) true;; *) false;; esac;; *) false;; esac \
  && ok "2569 a suite red under the pool with no time left to run alone is unproven, and the reason names it" || ko "2569 no time to rerun: rc $rc, outcome '$(cat "$T/oc" 2>&1)'"
pnohang || ko "2569 no time to rerun: a killed suite's tree survived"

pnew rw; psuite a 'exit 1'; psuite b 'sleep $HANG_NAP'; ptip
out=$(pheld 1 SG_GATE_BOUND_SECONDS=3); rc=$?
[ $rc = 1 ] && [ "$(pfield 1)" = fail ] && printf '%s\n' "$out" | /usr/bin/grep -qx -- '--- KILLED: test-b.sh' \
  && ok "2569 a real red wins over a kill (ADR-0164)" || ko "2569 red over kill: rc $rc, outcome '$(cat "$T/oc" 2>&1)'"
pnohang || ko "2569 red over kill: a killed suite's tree survived"

pnew sc; psuite a "env | /usr/bin/grep -E '^(SG|GATE|SERIAL|SQUAD)_' > $pm/env; true"; ptip
out=$(pheld 1 SERIAL_FOO=1 SQUAD_FOO=1 SG_FOO=1); rc=$?
[ $rc = 0 ] && [ ! -s "$pm/env" ] && ok "2569 a suite sees no SG_, GATE_, SERIAL_ or SQUAD_ variable" || ko "2569 scrub: rc $rc, leaked '$(cat "$pm/env" 2>&1)'"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
pkill -f "sleep $HANG_NAP" 2>/dev/null   # a red run leaves the hang its case started
rm -rf "$T"; exit $fail
