#!/bin/bash
# seconds: 2.06
# Regression test for quality-reading.py: the code-quality reading of the squad modules a tip changes (#885). It is a report
# beside the pytest counts, never a gate: each outcome is one line and the exit is 0. Stub radon and mutmut log their argv,
# which each case compares exactly, and print canned output. A throwaway repository; no real tool runs.
# usage: bash test-quality-reading.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
PY=$(command -v python3); G="git -c user.email=a@b -c user.name=t"
R=$T/r; mkdir -p "$R/squad"; git init -q -b dev "$R"
printf 'def f(x):\n    return x\n\n\ndef g(y):\n    return y\n' > "$R/squad/a.py"; echo 'x = 1' > "$R/squad/b.py"; echo base > "$R/README.md"
$G -C "$R" add -A; $G -C "$R" commit -q -m base
$G -C "$R" checkout -q -b gui/885; printf 'def f(x):\n    if x:\n        return 1\n    return x\n\n\ndef g(y):\n    return y\n' > "$R/squad/a.py"
$G -C "$R" add -A; $G -C "$R" commit -q -m work

mkdir -p "$T/with" "$T/mutonly" "$T/radonly"
cat > "$T/with/radon" <<'S'
#!/bin/bash
echo "radon $*" >> "$ARGV_LOG"
[ -n "$RADON_FAIL" ] && exit 3
echo '{"squad/a.py": [{"name": "f", "complexity": 2}, {"name": "g", "complexity": 7}]}'
S
cat > "$T/with/mutmut" <<'S'
#!/bin/bash
echo "mutmut $*" >> "$ARGV_LOG"
case " $* " in *" --paths-to-mutate "*|*" --paths-to-mutate="*) echo "Error: No such option: --paths-to-mutate" >&2; exit 2;; esac
case $1 in
  run) [ -n "$MUTMUT_SLOW" ] && sleep 5; mkdir -p mutants; exit "${MUTMUT_RUN_RC:-0}" ;;
  export-cicd-stats)
    [ -n "$MUTMUT_NO_STATS" ] && { echo "Usage: mutmut export-cicd-stats [OPTIONS]" >&2; exit 2; }
    mkdir -p mutants; echo '{"total": 10, "survived": 2, "killed": 8}' > mutants/mutmut-cicd-stats.json ;;
esac
S
chmod +x "$T/with/radon" "$T/with/mutmut"
cp "$T/with/mutmut" "$T/mutonly/mutmut"; cp "$T/with/radon" "$T/radonly/radon"

rd() { (cd "$R" && env PATH="$1:/usr/bin:/bin" ARGV_LOG="$T/argv.log" SG_QUALITY_MUTMUT_DEADLINE="${DL:-60}" "$PY" "$d/quality-reading.py" dev 2>&1); }
WANT_ARGV='radon cc -s -j squad/a.py
mutmut run squad.a.x_f__mutmut_*
mutmut export-cicd-stats'

rm -f "$T/argv.log"; out=$(rd "$T/with"); rc=$?
[ $rc = 0 ] && ok "the reading exits 0" || ko "exit $rc: $out"
[ "$(cat "$T/argv.log" 2>/dev/null)" = "$WANT_ARGV" ] && ok "radon gets the changed module, mutmut only the changed function" || ko "argv: '$(cat "$T/argv.log" 2>&1)'"
case $out in *"quality: radon cc: 2 blocks in 1 changed module, mean CC 4.5, max CC 7 (squad/a.py::g)"*) ok "radon prints its blocks, mean and worst block";; *) ko "radon line: $out";; esac
case $out in *"quality: mutmut: 2 survived of 10 mutants in 1 changed function"*) ok "mutmut prints its survivors beside the total";; *) ko "mutmut line: $out";; esac
[ -z "$(git -C "$R" status --porcelain)" ] && ok "the reading leaves the worktree untouched" || ko "worktree dirty: $(git -C "$R" status --porcelain)"

rm -f "$T/argv.log"; out=$(MUTMUT_RUN_RC=2 rd "$T/with"); rc=$?
[ $rc = 0 ] && case $out in *"quality: mutmut: 2 survived of 10 mutants"*) true;; *) false;; esac && ok "survivors make mutmut exit nonzero and the reading still prints them, exit 0" || ko "mutmut rc 2: rc $rc: $out"

rm -f "$T/argv.log"; out=$(RADON_FAIL=1 rd "$T/with"); rc=$?
[ $rc = 0 ] && case $out in *"quality: radon failed (exit 3), skipped"*"quality: mutmut: 2 survived"*) true;; *) false;; esac && ok "a failing radon is a named skip, mutmut still prints, exit 0" || ko "radon fail: rc $rc: $out"

rm -f "$T/argv.log"; out=$(rd "$T/mutonly"); rc=$?
[ $rc = 0 ] && case $out in *"quality: radon not installed, skipped"*"quality: mutmut: 2 survived"*) true;; *) false;; esac && ok "a missing radon prints a named skip and mutmut still prints, exit 0" || ko "missing radon: rc $rc: $out"
! /usr/bin/grep -q '^radon ' "$T/argv.log" 2>/dev/null && ok "no radon argv is sent when radon is missing" || ko "radon was called: $(cat "$T/argv.log")"

rm -f "$T/argv.log"; out=$(rd "$T/radonly"); rc=$?
[ $rc = 0 ] && case $out in *"quality: radon cc"*"quality: mutmut not installed, skipped"*) true;; *) false;; esac && ok "a missing mutmut prints a named skip and radon still prints, exit 0" || ko "missing mutmut: rc $rc: $out"

rm -f "$T/argv.log"; t0=$SECONDS; out=$(MUTMUT_SLOW=1 DL=1 rd "$T/with"); rc=$?; el=$((SECONDS - t0))
[ $rc = 0 ] && [ "$el" -lt 4 ] && case $out in *"quality: mutmut deadline of 1s passed, skipped"*) true;; *) false;; esac && ok "a mutation run past its deadline is a named skip, stopped in time" || ko "deadline: rc $rc in ${el}s: $out"

rm -f "$T/argv.log"; out=$(MUTMUT_NO_STATS=1 rd "$T/with"); rc=$?
[ $rc = 0 ] && case $out in *"quality: mutmut wrote no stats (Usage: mutmut export-cicd-stats [OPTIONS]), skipped"*) true;; *) false;; esac && ok "a mutmut that writes no stats names its first stderr line, exit 0" || ko "no stats: rc $rc: $out"

$G -C "$R" checkout -q -b gui/if dev; printf 'if True:\n    def h(z):\n        return z\n' > "$R/squad/c.py"
$G -C "$R" add -A; $G -C "$R" commit -q -m if-work
rm -f "$T/argv.log"; out=$(rd "$T/with"); rc=$?
[ "$(cat "$T/argv.log" 2>/dev/null)" = "radon cc -s -j squad/c.py
mutmut run squad.c.x_h__mutmut_*
mutmut export-cicd-stats" ] && ok "a function under a module-level if is named for mutation" || ko "if-block argv: '$(cat "$T/argv.log" 2>&1)'"

$G -C "$R" checkout -q -b gui/docs dev; echo more >> "$R/README.md"; $G -C "$R" commit -qam docs
rm -f "$T/argv.log"; out=$(rd "$T/with"); rc=$?
[ $rc = 0 ] && case $out in *"quality: no changed squad module, skipped"*) true;; *) false;; esac && [ ! -e "$T/argv.log" ] && ok "a tip with no changed squad module runs no tool" || ko "no squad change: rc $rc: $out"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
