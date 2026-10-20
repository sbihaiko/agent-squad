#!/bin/bash
# Regression test for fast-gate.sh: the fast gate of a work-branch tip or a wave entry is the base battery (ruff, mypy, the fast
# pytest) and then the shell suites of the scripts the tip changes against the merge-base, all under the machine lock, with one
# status line for the hub. A throwaway repository, a stub base command, stub suites: nothing here runs a real battery.
# usage: bash test-fast-gate.sh [scripts dir]
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so the hold is proved
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; export SG_STATE=$T/st; mkdir -p "$SG_STATE"
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
printf '#!/bin/bash\necho "base-cmd lock=$([ -d "%s/locks/gate" ] && echo held || echo free)" >> "%s/ran.log"\n[ "$BASE_FAIL" = 1 ] && echo "FAILED tests/test_x.py::test_a - boom"\n[ -z "$BASE_FAIL" ]\n' "$SG_STATE" "$T" > "$T/base.sh"; chmod +x "$T/base.sh"
fg() { rm -f "$T/ran.log" "$SG_STATE/gate-status"; (cd "$R" && SG_FAST_BASE=dev SG_FAST_CMD="$T/base.sh" bash "$d/fast-gate.sh" 2>&1); }

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
printf '#!/bin/bash\necho "retry-cmd $*" >> "%s/ran.log"\n[ -z "$RETRY_FAIL" ]\n' "$T" > "$T/retry.sh"; chmod +x "$T/retry.sh"
fgr() { rm -f "$T/ran.log" "$SG_STATE/gate-status"; (cd "$R" && SG_FAST_BASE=dev SG_FAST_CMD="$T/base.sh" SG_FAST_RETRY_CMD="$T/retry.sh" bash "$d/fast-gate.sh" 2>&1); }
out=$(BASE_FAIL=1 fgr); rc=$?
[ $rc = 0 ] && ok "a base battery red only under load passes once its failed tests pass alone" || ko "saturation retry: rc $rc: $out"
case "$out" in *saturation*) ok "the gate names the saturation it passed over";; *) ko "the retry is silent: $out";; esac
/usr/bin/grep -qx "retry-cmd tests/test_x.py::test_a" "$T/ran.log" && /usr/bin/grep -q "test-foo.sh" "$T/ran.log" && ok "the suites still run after a passed retry" || ko "ran: '$(cat "$T/ran.log")'"
out=$(BASE_FAIL=1 RETRY_FAIL=1 fgr); rc=$?
[ $rc -ne 0 ] && ! /usr/bin/grep -q "test-foo.sh" "$T/ran.log" && ok "a failed test that fails alone too fails the gate" || ko "retry red: rc $rc: '$(cat "$T/ran.log")'"
out=$(fgr); /usr/bin/grep -q "retry-cmd" "$T/ran.log" && ko "a green battery ran the retry" || ok "a green battery runs no retry"
# pyproject turns the pytest cache off, so --lf names nothing: the retry runs the failed tests the log names, never the battery.
out=$(BASE_FAIL=lint fgr); rc=$?
[ $rc -ne 0 ] && ! /usr/bin/grep -q "retry-cmd" "$T/ran.log" && ok "a red that names no failed test (lint, types) runs no retry and fails" || ko "lint red: rc $rc: '$(cat "$T/ran.log")'"

# (#670) A red that is lint or types names the check it failed at, with its own line. A reason that reads "names no failed test"
# hides which check failed, and the child that must repair it never learns which one it was.
printf '#!/bin/bash\n[ -n "$RUFF_FAIL" ] && { echo "F401 [*] os imported but unused"; echo "  --> tests/test_hub_models_v2.py:8:8"; echo "Found 1 error."; exit 1; }\n[ -n "$RUFF_BARE" ] && { echo "F811 Redefinition of unused hub from line 13"; echo "   --> tests/test_models_v2.py:175:60"; echo "Found 4 errors."; exit 1; }\n[ -n "$MYPY_FAIL" ] && { echo "squad/x.py:12: error: bad type"; exit 1; }\nexit 0\n' > "$T/lint.sh"; chmod +x "$T/lint.sh"
lintgate() { rm -f "$T/ran.log" "$SG_STATE/gate-status"; (cd "$R" && SG_FAST_BASE=dev SG_FAST_CMD="$T/lint.sh" bash "$d/fast-gate.sh" 2>&1); }
out=$(RUFF_FAIL=1 lintgate); rc=$?
[ $rc -ne 0 ] && case "$out" in *"ruff:"*"tests/test_hub_models_v2.py"*"F401"*) true;; *) false;; esac && ok "a lint red names ruff, its path and its code" || ko "ruff red: rc $rc: $out"
# (#671) A ruff code with no fix marker, `[*]`, is ruff too, never pytest.
out=$(RUFF_BARE=1 lintgate); rc=$?
[ $rc -ne 0 ] && case "$out" in *"ruff:"*"tests/test_models_v2.py"*"F811"*) true;; *) false;; esac && ok "a lint red with no fix marker names ruff, its path and its code" || ko "bare ruff red: rc $rc: $out"
out=$(MYPY_FAIL=1 lintgate); rc=$?
[ $rc -ne 0 ] && case "$out" in *"mypy"*"squad/x.py:12"*) true;; *) false;; esac && ok "a type red names mypy and its line" || ko "mypy red: rc $rc: $out"

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

out=$(cd "$R" && SG_FAST_BASE=no-such-ref SG_FAST_CMD="$T/base.sh" bash "$d/fast-gate.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && ok "a base that does not resolve fails the gate: it is never skipped" || ko "unresolved base: rc $rc: $out"
# The wave entry gate defaults to the fast gate, so a child's own changed scripts are proven before it enters.
/usr/bin/grep -q 'SG_WAVE_GATES:=.*fast-gate\.sh' "$d/env.sh" && ok "the default wave entry gate is the fast gate" || ko "env.sh: the default SG_WAVE_GATES does not run fast-gate.sh"

# (#566) The default battery is the loop's one definition (base-battery.sh), and its pytest runs with a bounded worker count,
# SG_GATE_WORKERS, instead of `auto`. A stub python logs the argv of each leg: the count is set here, so this suite reads no
# profile's default and depends on no worker count.
printf '#!/bin/bash\necho "$*" >> "%s/py.log"\nexit 0\n' "$T" > "$T/py"; chmod +x "$T/py"
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
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
