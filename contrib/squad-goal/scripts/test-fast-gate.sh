#!/bin/bash
# Regression test for fast-gate.sh: the fast gate of a work-branch tip or a wave entry is the base battery (ruff, mypy, the fast
# pytest) and then the shell suites of the scripts the tip changes against the merge-base, all under the machine lock, with one
# status line for the hub. A throwaway repository, a stub base command, stub suites: nothing here runs a real battery.
# usage: bash test-fast-gate.sh [scripts dir]
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so the hold is proved
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; export SG_STATE=$T/st SQUAD_CAPS_DIR=$T/caps; mkdir -p "$SG_STATE"   # (#801) the gate takes slots: its own budget, never the machine's
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
cg() { rm -f "$T/ran.log" "$T/cls.n" "$SG_STATE/gate-status"; (cd "$R" && SG_FAST_BASE=dev SG_FAST_CMD="bash $T/cls.sh" SG_FAST_RETRY_CMD="bash $T/retry.sh" bash "$d/fast-gate.sh" 2>&1); }
out=$(CLS=collection cg); rc=$?
[ $rc = 1 ] && case "$(last "$out")" in "fast-gate: collection: "*"tests/test_c.py"*) true;; *) false;; esac && ok "a collection error names the collection class and its file" || ko "collection: rc $rc: '$(last "$out")'"
[ "$(/usr/bin/grep -c '^battery' "$T/ran.log")" = 1 ] && ! /usr/bin/grep -q retry-cmd "$T/ran.log" && ok "a collection error is a defect: no retry" || ko "collection retried: '$(cat "$T/ran.log")'"
out=$(CLS=errorid RETRY_FAIL=1 cg); rc=$?
[ $rc = 1 ] && /usr/bin/grep -qx "retry-cmd tests/test_e.py::test_b" "$T/ran.log" && case "$(last "$out")" in "fast-gate: pytest: "*"tests/test_e.py::test_b"*) true;; *) false;; esac \
  && ok "a test that errors is a failed id: it is retried alone and named" || ko "error id: rc $rc: '$(last "$out")' '$(cat "$T/ran.log")'"
for c in saturation killed unknown; do
  out=$(CLS=$c cg); rc=$?; want=$c; [ $c = killed ] && want=saturation
  [ $rc = 4 ] && case "$(last "$out")" in "fast-gate: $want: "*inconclusive*) true;; *) false;; esac && ok "a $c red, twice, is inconclusive: the $want class and exit 4" || ko "$c: rc $rc: '$(last "$out")'"
  [ "$(/usr/bin/grep -c '^battery' "$T/ran.log")" = 2 ] && ok "a $c red runs the battery once more, and only once" || ko "$c retries: '$(cat "$T/ran.log")'"
  ! /usr/bin/grep -q 'test-foo.sh' "$T/ran.log" && ok "an inconclusive $c result runs no suite" || ko "$c ran suites: '$(cat "$T/ran.log")'"
  case "$out" in *"named no failed test"*) ko "$c is read as pytest: '$out'";; *) ok "a $c red is not read as one pytest cause";; esac
  IFS=$'\t' read -r _ kind res _ < "$SG_STATE/gate-status" 2>/dev/null; [ "$kind $res" = "fast fail" ] && ok "an inconclusive $c result leaves no pass line" || ko "$c status: '$(cat "$SG_STATE/gate-status" 2>&1)'"
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
IFS=$'\t' read -r _ kind res _ sel < "$SG_STATE/gate-status" 2>/dev/null
[ "$kind $res $sel" = "fast pass selected 2 files" ] && ok "the status line states the selected file count" || ko "selection status: '$(cat "$SG_STATE/gate-status" 2>&1)'"
echo "all: no test imports squad/x.py" > "$T/select.out"
out=$(selrun); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx -- "-m pytest $PYTEST_OPTS" "$T/argv.log" && ok "an all answer runs the whole battery" || ko "all argv: rc $rc: '$(cat "$T/argv.log" 2>&1)'"
IFS=$'\t' read -r _ kind res _ sel < "$SG_STATE/gate-status" 2>/dev/null
[ "$sel" = "all: no test imports squad/x.py" ] && ok "the status line states the all reason" || ko "all status: '$(cat "$SG_STATE/gate-status" 2>&1)'"
: > "$T/select.out"
out=$(selrun); rc=$?
[ $rc = 0 ] && ! /usr/bin/grep -q -- "-m pytest" "$T/argv.log" && /usr/bin/grep -q -- "-m ruff check squad tests" "$T/argv.log" && /usr/bin/grep -q -- "-m mypy squad" "$T/argv.log" && ok "an empty selection runs no pytest and still runs ruff and mypy" || ko "empty selection: rc $rc: '$(cat "$T/argv.log" 2>&1)'"
/usr/bin/grep -q "selected 0 files" "$SG_STATE"/fast-gate-*.log && ok "an empty selection logs that it ran none" || ko "empty selection log: '$(cat "$SG_STATE"/fast-gate-*.log 2>&1)'"
printf 'tests/test_a.py\n' > "$T/select.out"
out=$(selrun SG_TEST_SELECT=no); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx -- "-m pytest $PYTEST_OPTS" "$T/argv.log" && ! /usr/bin/grep -q "squad.testselect" "$T/argv.log" && ok "SG_TEST_SELECT=no runs the whole battery without the selector" || ko "SG_TEST_SELECT=no: rc $rc: '$(cat "$T/argv.log" 2>&1)'"
/usr/bin/grep -q "all: SG_TEST_SELECT=no" "$SG_STATE"/fast-gate-*.log && ok "SG_TEST_SELECT=no says so in the log" || ko "SG_TEST_SELECT=no log: '$(cat "$SG_STATE"/fast-gate-*.log 2>&1)'"
printf 'tests/test_a.py\ntests/test_b.py\n' > "$T/select.out"
out=$(selrun PY_FAIL=1); rc=$?
[ "$(cat "$T/retry.log" 2>/dev/null)" = "tests/test_a.py::test_one" ] && ok "the serial retry reruns only the failed test names" || ko "retry argv: rc $rc: '$(cat "$T/retry.log" 2>&1)' $out"
# (#801) The fixture waits on nothing outside itself. A stub the fixture writes and then executes directly costs macOS a first-run
# assessment that idles the wall clock, so each stub runs through bash. A fixture case that does not set a retry command runs the
# real pytest, so every case sets one.
/usr/bin/grep -qE 'SG_FAST_(CMD|RETRY_CMD)="\$T/' "$0" && ko "a fixture stub is executed directly, not through bash (#801)" || ok "every fixture stub runs through bash"
/usr/bin/grep -qE '^fg\(\) \{.*SG_FAST_RETRY_CMD=' "$0" && ok "the base-fail case names its retry, and runs no real pytest" || ko "fg runs the real pytest retry (#801)"
# (#885) The pytest tally and the code-quality reading print together after a green battery, and the reading never changes the exit.
out=$(cd "$R" && SG_FAST_BASE=dev SG_FAST_CMD="echo '5 passed in 0.1s'" SG_FAST_RETRY_CMD=false bash "$d/fast-gate.sh" 2>&1); rc=$?
[ $rc = 0 ] && /usr/bin/grep -q "fast-gate: pytest tally: 5 passed in 0.1s" "$SG_STATE"/fast-gate-*.log && /usr/bin/grep -q "quality: no changed squad module, skipped" "$SG_STATE"/fast-gate-*.log && ok "the tally and the quality reading land in the battery log and the gate exits 0" || ko "tally beside reading: rc $rc: $out $(cat "$SG_STATE"/fast-gate-*.log 2>&1)"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
