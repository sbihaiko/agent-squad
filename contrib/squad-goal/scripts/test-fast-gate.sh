#!/bin/bash
# Regression test for fast-gate.sh: the fast gate of a work-branch tip or a wave entry is the base battery (ruff, mypy, the fast
# pytest) and then the shell suites of the scripts the tip changes against the merge-base, all under the machine lock, with one
# status line for the hub. A throwaway repository, a stub base command, stub suites: nothing here runs a real battery.
# usage: bash test-fast-gate.sh [scripts dir]
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so the hold is proved
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; export SG_STATE=$T/st SQUAD_CAPS_DIR=$T/caps; mkdir -p "$SG_STATE"   # (#801) the gate takes slots: its own budget, never the machine's
export SG_FAST_LOG_DIR=$T/logs; mkdir -p "$SG_FAST_LOG_DIR"   # (#945) the gate's log sits outside SG_STATE, where the child that ran it may read it
export SG_FAST_MEMO=0   # the pass memo is proved in its own block at the end; every other case here runs a fresh battery
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

# (#566) The default battery is the loop's one definition (base-battery.sh), and its pytest runs with a bounded worker count,
# SG_GATE_WORKERS, instead of `auto`. A stub python logs the argv of each leg: the count is set here, so this suite reads no
# profile's default and depends on no worker count.
printf '#!/bin/bash\necho "$*" >> "%s/py.log"\ncase "$*" in *squad.testselect*) echo "all: stub";; esac\nexit 0\n' "$T" > "$T/py"; chmod +x "$T/py"
rm -f "$T/ran.log" "$T/py.log"; $G -C "$R" checkout -q dev   # no script changes at the base: the battery alone runs
out=$(cd "$R" && SG_FAST_BASE=dev SG_GATE_PY="$T/py" SG_GATE_WORKERS=3 bash "$d/fast-gate.sh" 2>&1); rc=$?
[ $rc = 0 ] && ok "the default battery passes on a stub python" || ko "default battery: rc $rc: $out"
/usr/bin/grep -q -- '-m ruff check squad tests' "$T/py.log" && /usr/bin/grep -q -- '-m mypy squad' "$T/py.log" && ok "the default battery runs ruff and mypy" || ko "default battery: '$(cat "$T/py.log" 2>&1)'"
/usr/bin/grep 'pytest' "$T/py.log" | /usr/bin/grep -q -- '-n 3' && ok "the default battery's pytest runs with SG_GATE_WORKERS" || ko "the worker count: '$(grep pytest "$T/py.log" 2>&1)'"
/usr/bin/grep -q -- '-n auto' "$T/py.log" && ko "the default battery runs pytest -n auto" || ok "the default battery never runs pytest -n auto"
/usr/bin/grep -q 'sg_base_battery' "$d/fast-gate.sh" && ok "fast-gate builds its default command from the shared definition" || ko "fast-gate keeps its own copy of the base battery"
# The retry of what failed stays serial, and runs only the names the battery failed: the id list, never the battery.
retryline=$(/usr/bin/grep -E '^retrycmd=' "$d/fast-gate.sh")
case "$retryline" in *'-p no:xdist'*) ok "the retry serializes pytest";; *) ko "the retry is not serial: '$retryline'";; esac
case "$retryline" in *' -n '*) ko "the serial retry takes workers: '$retryline'";; *) ok "the serial retry stays serial";; esac
/usr/bin/grep -qE 'SG_GATE_WORKERS:=[0-9]+' "$d/env.sh" && ok "env.sh holds the default worker count" || ko "env.sh: no numeric SG_GATE_WORKERS default"
# (#646) The base battery's pytest runs over the selection the selector answers for the base. A stub python logs each call's argv
# on one line and answers squad.testselect from $T/select.out, so each pytest line is compared exactly, never by one word.
printf '#!/bin/bash\necho "$*" >> "%s/argv.log"\ncase "$*" in\n  *squad.testselect*) cat "%s/select.out" 2>/dev/null;;\n  *pytest*) [ "$PY_FAIL" = 1 ] && { echo "FAILED tests/test_a.py::test_one - boom"; exit 1; };;\nesac\nexit 0\n' "$T" "$T" > "$T/selpy"; chmod +x "$T/selpy"
printf '#!/bin/bash\necho "$*" >> "%s/retry.log"\n' "$T" > "$T/retry.sh"
selrun() { rm -f "$T/argv.log" "$T/retry.log" "$SG_STATE/gate-status"; (cd "$R" && env -u SG_TEST_SELECT -u SG_FAST_CMD SG_FAST_BASE=dev SG_GATE_PY="$T/selpy" SG_GATE_WORKERS=3 SG_FAST_RETRY_CMD="bash $T/retry.sh" "$@" bash "$d/fast-gate.sh" 2>&1); }
PYTEST_OPTS='-p no:deepeval -o heavy_cap_seconds=20 -n 3 -m not heavy'
printf 'tests/test_a.py\ntests/test_b.py\n' > "$T/select.out"
out=$(selrun); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx -- "-m pytest $PYTEST_OPTS tests/test_a.py tests/test_b.py" "$T/argv.log" && ok "the battery passes the selection to pytest, after the unchanged options" || ko "selection argv: rc $rc: '$(cat "$T/argv.log" 2>&1)' $out"
/usr/bin/grep -q -- "squad.testselect --stage fast --base dev" "$T/argv.log" && ok "the fast gate asks the selector at the fast stage" || ko "fast stage: '$(cat "$T/argv.log" 2>&1)'"
IFS=$'\t' read -r _ kind res _ sel scope < "$SG_STATE/gate-status" 2>/dev/null
[ "$kind $res $sel $scope" = "fast pass selected 2 files delta" ] && ok "the status line states the selected file count and the delta scope" || ko "selection status: '$(cat "$SG_STATE/gate-status" 2>&1)'"
echo "all: no test imports squad/x.py" > "$T/select.out"
out=$(selrun); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx -- "-m pytest $PYTEST_OPTS" "$T/argv.log" && ok "an all answer runs the whole battery" || ko "all argv: rc $rc: '$(cat "$T/argv.log" 2>&1)'"
IFS=$'\t' read -r _ kind res _ sel scope < "$SG_STATE/gate-status" 2>/dev/null
[ "$sel $scope" = "all: no test imports squad/x.py full" ] && ok "the status line states the all reason and the full scope" || ko "all status: '$(cat "$SG_STATE/gate-status" 2>&1)'"
: > "$T/select.out"
out=$(selrun); rc=$?
[ $rc = 0 ] && ! /usr/bin/grep -q -- "-m pytest" "$T/argv.log" && /usr/bin/grep -q -- "-m ruff check squad tests" "$T/argv.log" && /usr/bin/grep -q -- "-m mypy squad" "$T/argv.log" && ok "an empty selection runs no pytest and still runs ruff and mypy" || ko "empty selection: rc $rc: '$(cat "$T/argv.log" 2>&1)'"
/usr/bin/grep -q "selected 0 files" "$SG_FAST_LOG_DIR"/fast-gate-*.log && ok "an empty selection logs that it ran none" || ko "empty selection log: '$(cat "$SG_FAST_LOG_DIR"/fast-gate-*.log 2>&1)'"
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
# (memo) A pass is kept for its (tree content, dirty state, base ref, base sha, merge-base, battery): a second gate at that key
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
$G -C "$R" checkout -q dev && $G -C "$R" commit -q --allow-empty -m moved && $G -C "$R" checkout -q "$cur"
out=$(mg "$md" dev); rc=$?
[ $rc = 0 ] && [ "$(battery_runs)" = 1 ] && ok "the base's commit is part of the key: a base ref moved to a new commit runs the battery" || ko "moved base: rc $rc runs $(battery_runs): $out"

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
printf '#!/bin/bash\necho "retry-cmd $*" >> "%s/ran.log"\nsleep 2718\n' "$T" > "$T/retry-hang.sh"
vstub hangfail 'echo "FAILED tests/test_h.py::test_hang - boom"; sleep 2718'
s0=$(date +%s); out=$(SG_GATE_BOUND_SECONDS=2 vfg hangfail); rc=$?; el=$(( $(date +%s) - s0 ))
[ $rc = 1 ] && [ "$el" -lt 20 ] && case "$(last "$out")" in "fast-gate: pytest: "*"tests/test_h.py::test_hang"*) true;; *) false;; esac \
  && ok "a failing test followed by a hang at the deadline ends fail and names the defect (${el}s)" || ko "hang after a defect: rc $rc, last '$(last "$out")'"
[ "$(vhist | cut -f8)" = fail ] && ok "the history records the deadline-bound attempt as fail, not bounded" || ko "hang history: '$(vhist)'"
pgrep -f "sleep 2718" >/dev/null 2>&1 && ko "the battery's tree survived the deadline" || ok "the battery's tree is stopped at the deadline"

vstub fastred 'echo "FAILED tests/test_r.py::test_r - boom"; exit 1'
s0=$(date +%s); out=$(RETRY="bash $T/retry-hang.sh" SG_GATE_BOUND_SECONDS=3 vfg fastred); rc=$?; el=$(( $(date +%s) - s0 ))
[ $rc = 1 ] && [ "$el" -lt 20 ] && /usr/bin/grep -qx "retry-cmd tests/test_r.py::test_r" "$T/ran.log" \
  && ok "a retry shares the one budget: a retry that hangs leaves the gate red (${el}s)" || ko "retry budget: rc $rc in ${el}s: '$(cat "$T/ran.log")' '$(last "$out")'"
[ "$(vhist | cut -f8)" = fail ] && ok "a retry that never answers records the red as fail" || ko "retry hang history: '$(vhist)'"
pgrep -f "sleep 2718" >/dev/null 2>&1 && ko "the retry's tree survived the deadline" || ok "the retry's tree is stopped at the deadline"

vstub silent 'sleep 2718'
bs=$T/bounded-gate; mkdir -p "$bs"
s0=$(date +%s); out=$(cd "$R" && SG_STATE="$bs" SG_FAST_BASE=dev SG_GATE_BOUND_SECONDS=2 SG_FAST_CMD="bash $T/silent.sh" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1); rc=$?; el=$(( $(date +%s) - s0 ))
bh=$(tail -n 1 "$bs/gate-history")
[ $rc = 4 ] && [ "$el" -lt 20 ] && case "$(last "$out")" in *bounded*) true;; *) false;; esac \
  && ok "a battery that reports nothing and hangs is bounded, never a pass (${el}s)" || ko "silent hang: rc $rc, last '$(last "$out")'"
[ "$(printf '%s' "$bh" | cut -f8)" = unproven ] && case "$(printf '%s' "$bh" | cut -f9)" in bounded*) true;; *) false;; esac \
  && ok "the bounded attempt is recorded as bounded and unproven" || ko "bounded history: '$bh'"
pgrep -f "sleep 2718" >/dev/null 2>&1 && ko "the silent battery's tree survived the deadline" || ok "the silent battery's tree is stopped at the deadline"

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
/usr/bin/grep -q 'sg_gate_python' "$d/fast-gate.sh" && ok "fast-gate names sg_gate_python" || ko "fast-gate keeps its own interpreter lookup"
/usr/bin/grep -q 'py=python3' "$d/fast-gate.sh" && ko "fast-gate keeps a hand-rolled interpreter fallback" || ok "fast-gate keeps no hand-rolled interpreter fallback"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
pkill -f "sleep 2718" 2>/dev/null   # a red run leaves the hang its case started
rm -rf "$T"; exit $fail
