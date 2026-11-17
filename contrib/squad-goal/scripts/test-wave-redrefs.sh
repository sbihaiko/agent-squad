#!/bin/bash
# Regression test for #1474: a wave whose fix commit says `TDD RED` closes its issues. A squashed RED commit (a RED message that
# changes code as well as tests) delivers by the per-commit rule, as any commit does. A RED commit that changes test files only
# (a path under tests/, fixtures/ or a helper, conftest.py, pytest.ini, test-*.sh, test_*.py) delivers nothing (#533). A wave
# with a test-only RED commit and a fix commit carrying no Refs closes the one issue the wave names. The landing's WAVE line never
# says "no Refs" when a commit names an issue. Each case builds real commits on real paths in a throwaway repo, and asserts on the
# closed issue set and the WAVE line. A bare local remote, a stub gh that logs each argv, a stub board: no GitHub call.
# usage: bash test-wave-redrefs.sh [scripts dir]
unset REV REV_BASE REV_SHA SG_SERIAL_HELD SG_GATE_STATUS SG_GATE_KIND SERIAL_WAIT_SECONDS SERIAL_HOLD_SECONDS SERIAL_POLL_SECONDS
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
cp "$d/wave.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/wavepause.py" "$d/child_issues.py" "$d/address.sh" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/base-battery.sh" "$d/basecheck.sh" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf 'export SG_LAND_REMOTE=%s/origin.git SG_WAVE_TIP_GATES=true SG_BOARD_VOCAB=shipped\n' "$T"
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *) echo "{}";; esac\n' "$T" > "$T/bin/gh"
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"
# closes: the issue numbers the stub gh was asked to close, one per line, in order.
closes() { sed -n 's#^api -X PATCH repos/x/y/issues/\([0-9]*\) -f state=closed -f state_reason=completed$#\1#p' "$T/gh.log" 2>/dev/null | tr '\n' ' '; }
# mkwave <run> <issues> [<message> <paths>]...: the run's folder with its dispatch request and one entry per issue, its wave
# branch from wave.sh start, then one commit per pair on the wave. Each commit writes its own paths (relative to the repo root).
mkwave() { local run=$1 n=$2 rd i k=0 msg paths p; shift 2
  rd="$T/co/runs/$run"; mkdir -p "$rd"; : > "$rd/record.jsonl"
  printf 'CHILD — work GitHub issue(s) %s: do it\n' "$n" > "$rd/request.txt"
  for i in ${n//,/ }; do echo "$run" > "$T/st/auto/disp-$i"; done
  (cd "$T/co" && bash "$T/s/wave.sh" start "$run") >/dev/null
  rm -rf "$T/c-$run"; $G clone -q "$T/origin.git" "$T/c-$run" 2>/dev/null
  (cd "$T/c-$run" && $G checkout -q -b w "origin/wave/$run") 2>/dev/null || return 1
  while [ $# -ge 2 ]; do msg=$1; paths=$2; shift 2; k=$((k + 1))
    for p in $paths; do mkdir -p "$T/c-$run/$(dirname "$p")"; echo "$run $k" > "$T/c-$run/$p"; done
    (cd "$T/c-$run" && $G add -- $paths && $G -c user.email=a@b -c user.name=t commit -q -m "$msg") || return 1
  done
  (cd "$T/c-$run" && $G push -q origin "HEAD:refs/heads/wave/$run") 2>/dev/null; }
land() { (cd "$T/co" && bash "$T/s/wave.sh" land "$1" 2>&1); }
# wave_line <output>: the WAVE line of a landing, or empty.
wave_line() { printf '%s\n' "$1" | sed -n "/^WAVE wave\//p" | head -1; }

# A: a squashed RED commit (test and script) with Refs #1 is a delivery: #1 closes once.
mkwave ra 1 "TDD RED: a squashed red and fix (Refs #1)" "tests/test_a.py scripts/a.sh"; : > "$T/gh.log"
r=$(land ra); rc=$?
[ $rc = 0 ] && [ "$(closes)" = "1 " ] && ok "A: a squashed RED commit with Refs #1 closes #1" || ko "A: exit $rc, closes '$(closes)': '$r'"
case "$(wave_line "$r")" in *"landed in dev (#1)"*) ok "A: the WAVE line names #1";; *) ko "A: the WAVE line: '$(wave_line "$r")'";; esac

# B: a RED commit that changes test files only (#533) delivers nothing: nothing closes, and the WAVE line gives the reason.
mkwave rb 2 "TDD RED: test only (Refs #2)" "tests/test_b.py test-b.sh"; : > "$T/gh.log"
r=$(land rb); rc=$?
[ $rc = 0 ] && [ -z "$(closes)" ] && ok "B: a test-only RED commit closes nothing (#533)" || ko "B: exit $rc, closes '$(closes)': '$r'"
case "$(wave_line "$r")" in *"no issue closed:"*"test files only"*) ok "B: the WAVE line names the test-only reason";; *) ko "B: the WAVE line: '$(wave_line "$r")'";; esac
case "$(wave_line "$r")" in *"no Refs"*) ko "B: the WAVE line says no Refs while a commit names #2";; *) ok "B: the WAVE line never says no Refs";; esac
case "$r" in *"WAVE-NOTE wave/rb:"*"test files only"*) ok "B: a WAVE-NOTE names the test-only reason";; *) ko "B: no WAVE-NOTE with the reason: '$r'";; esac

# C: a RED commit that changes tests/ plus a fixture, a helper, conftest.py and pytest.ini is still test-only: nothing closes.
mkwave rc 3 "TDD RED: helpers and fixtures (Refs #3)" "tests/test_c.py tests/helpers/h.py fixtures/c.json conftest.py pytest.ini"; : > "$T/gh.log"
r=$(land rc); rc=$?
[ $rc = 0 ] && [ -z "$(closes)" ] && ok "C: a RED commit of tests, fixtures and helpers is still test-only" || ko "C: exit $rc, closes '$(closes)': '$r'"
case "$(wave_line "$r")" in *"no Refs"*) ko "C: the WAVE line says no Refs while a commit names #3";; *) ok "C: the WAVE line never says no Refs";; esac

# D: a test-only RED commit with Refs #4, then a fix commit that names no issue: #4 closes once, the one issue the wave names.
mkwave rd 4 "TDD RED: four (Refs #4)" "tests/test_d.py" "fix four" "scripts/d.sh"; : > "$T/gh.log"
r=$(land rd); rc=$?
[ $rc = 0 ] && [ "$(closes)" = "4 " ] && ok "D: the test-only RED and the unnamed fix close #4 once" || ko "D: exit $rc, closes '$(closes)': '$r'"
case "$(wave_line "$r")" in *"landed in dev (#4)"*) ok "D: the WAVE line names #4";; *) ko "D: the WAVE line: '$(wave_line "$r")'";; esac

# E: two squashed RED commits both with Refs #5: #5 closes once.
mkwave re 5 "TDD RED: five one (Refs #5)" "scripts/e1.sh" "TDD RED: five two (Refs #5)" "scripts/e2.sh"; : > "$T/gh.log"
r=$(land re); rc=$?
[ $rc = 0 ] && [ "$(closes)" = "5 " ] && ok "E: two squashed RED commits for #5 close #5 once" || ko "E: exit $rc, closes '$(closes)': '$r'"

# F: a test-only RED commit for #6 and a fix commit for #7: #7 closes only.
mkwave rf 6,7 "TDD RED: six (Refs #6)" "tests/test_f.py" "fix seven (Refs #7)" "scripts/f.sh"; : > "$T/gh.log"
r=$(land rf); rc=$?
[ $rc = 0 ] && [ "$(closes)" = "7 " ] && ok "F: the fix closes #7 and the test-only RED for #6 closes nothing" || ko "F: exit $rc, closes '$(closes)': '$r'"

# G: one RED commit that names two issues delivers neither (#869), and the WAVE line says why.
mkwave rg 8,9 "TDD RED: eight and nine (Refs #8 Refs #9)" "scripts/g.sh"; : > "$T/gh.log"
r=$(land rg); rc=$?
[ $rc = 0 ] && [ -z "$(closes)" ] && ok "G: a RED commit naming two issues closes neither (#869)" || ko "G: exit $rc, closes '$(closes)': '$r'"
case "$(wave_line "$r")" in *"no issue closed:"*"several issues"*) ok "G: the WAVE line names the several-issues reason";; *) ko "G: the WAVE line: '$(wave_line "$r")'";; esac
case "$r" in *"WAVE-NOTE wave/rg:"*"several issues"*) ok "G: a WAVE-NOTE names the several-issues reason";; *) ko "G: no WAVE-NOTE with the reason: '$r'";; esac

# H: a RED commit whose test files are another language's (foo_test.go, test/x.spec.js) is test-only, as check-tdd-red.sh reads
# them (TEST_RE): nothing closes.
mkwave rh 11 "TDD RED: eleven in go and js (Refs #11)" "foo_test.go test/x.spec.js"; : > "$T/gh.log"
r=$(land rh); rc=$?
[ $rc = 0 ] && [ -z "$(closes)" ] && ok "H: a RED commit of foo_test.go and test/x.spec.js is test-only (#533)" || ko "H: exit $rc, closes '$(closes)': '$r'"

# I: a RED commit of a test and a .md note is test-only: check-tdd-red.sh skips a .md, so the note is no code and nothing closes.
mkwave ri 12 "TDD RED: twelve with a note (Refs #12)" "tests/test_i.py NOTES.md"; : > "$T/gh.log"
r=$(land ri); rc=$?
[ $rc = 0 ] && [ -z "$(closes)" ] && ok "I: a RED commit of a test and a .md note is test-only (#533)" || ko "I: exit $rc, closes '$(closes)': '$r'"

# J: SG_TDD_TEST_RE, when the host names its tests, is the test notion: a RED commit of a spec file it names is test-only.
mkwave rj 13 "TDD RED: thirteen in rb (Refs #13)" "spec/x_spec.rb"; : > "$T/gh.log"
r=$(SG_TDD_TEST_RE='(^|/)spec/[^/]+_spec\.rb$' land rj); rc=$?
[ $rc = 0 ] && [ -z "$(closes)" ] && ok "J: a RED commit of a spec file SG_TDD_TEST_RE names is test-only" || ko "J: exit $rc, closes '$(closes)': '$r'"

# K: a test-only RED commit and a fix commit, both with Refs #14: #14 closes once (acceptance criterion 3).
mkwave rk 14 "TDD RED: fourteen test (Refs #14)" "tests/test_k.py" "fix fourteen (Refs #14)" "scripts/k.sh"; : > "$T/gh.log"
r=$(land rk); rc=$?
[ $rc = 0 ] && [ "$(closes)" = "14 " ] && ok "K: a test-only RED and a fix, both for #14, close #14 once" || ko "K: exit $rc, closes '$(closes)': '$r'"

[ $fail = 0 ] && echo "PASS test-wave-redrefs" || echo "FAIL test-wave-redrefs"
exit $fail
