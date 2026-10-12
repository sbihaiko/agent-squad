#!/bin/bash
# Regression test for the fork merge queue (#1409): a tested merge is recorded as a tested base (no second run), several code-complete branches land on one candidate with one gate run, a red batch falls back to one-by-one so the culprit is named, and a conflicting later branch is the only one sent back. Stub gate, stub gh, throwaway fork repo, no emulator.
unset SG_E2E_DIR SG_E2E_BUILD_DIR SG_E2E_BUILD_CMD   # the real e2e checkouts of the caller must never be touched by a stub run
here="$(cd "$(dirname "$0")" && pwd)"; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
runs() { [ -f "$T/gui.log" ] && wc -l < "$T/gui.log" | tr -d ' ' || echo 0; }
setup() {   # setup <issue>...: a fork whose base has hub.py (10 lines) and one branch per issue; 1 edits line 1, 2 edits line 10, 3 edits line 1 too (conflicts with 1), 4 adds the file "bad" (the gate goes red on it), others add their own file
  T=$(mktemp -d); mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/req" "$T/bin" "$T/co/runs/.hub" "$T/fork"
  export HOME="$T/home"; mkdir -p "$HOME"   # serial.sh's durable GUI kill switch is $HOME/.squad-gui-tests-off, so a private home is what keeps THIS fixture's gate decision observable instead of letting the machine's switch answer for it (the command the gate runs is the stub in SG_FORK_TEST_CMD, so no emulator can start either way)
  cp "$here/forkclose.sh" "$here/forkonly.sh" "$here/fork-gate.sh" "$here/gate-suites.sh" "$here/serial.sh" "$T/s/"; printf 'exit 0\n' > "$T/s/shell-suites.sh"; cp $here/ghread.py "$T/s/"; cp $here/ghthrottle.py "$T/s/"
  # test.sh's stub models the seam the landing reads its failures from: the real test.sh writes the gate's whole output to
  # $SG_STATE/tests/<TEST_ID>.log, which is what the batch pass reads a red batch's failing test out of (#13).
  cat > "$T/s/test.sh" <<'EOS'
#!/bin/bash
iss=$1; shift; [ "$1" = -C ] && { cd "$2"; shift 2; }; [ "$1" = -- ] && shift
o=$("$@" 2>&1); rc=$?
mkdir -p "$SG_STATE/tests"; [ -n "$TEST_ID" ] && printf '%s\n' "$o" > "$SG_STATE/tests/$TEST_ID.log"
printf '%s\n' "$o" | tail -3; echo "TEST #$iss x test-$iss-x"; exit $rc
EOS
  printf '#!/bin/bash\necho %s/req.txt\n' "$T" > "$T/s/mkreq.sh"; printf 'open(__import__("sys").argv[1]+".launched","w")\n' > "$T/s/launch.py"; chmod +x "$T/s/test.sh" "$T/s/mkreq.sh"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_OWNER=x SG_PROJECT=4 BOARD=true\nexport SG_FORK_TEST_CMD="echo gui >> %s/gui.log; [ ! -e bad ]"\nexport SG_FORK_PY_CMD=true\n' "$T/st" "$T/fork" "$T/co" "$T" > "$T/s/env.sh"
  g init -q -b local/next; seq 1 10 > "$T/fork/hub.py"; g add .; g commit -q -m init
  local n items=""; for n in "$@"; do g checkout -q -b gui/$n local/next
    case $n in 1|3) sed -i '' "1s/.*/edit-$n/" "$T/fork/hub.py";; 2) sed -i '' "10s/.*/edit-$n/" "$T/fork/hub.py";; 4) echo x > "$T/fork/bad";; *) echo $n > "$T/fork/f$n";; esac
    g add .; g commit -q -m "work $n"; g checkout -q local/next; items="$items{\"n\":$n,\"st\":\"Code Complete\",\"s\":\"open\"},"; done
  echo "{\"items\":[${items%,}]}" > "$T/co/runs/.hub/hub-board.json"
  printf '#!/bin/bash\ncase "$*" in\n  *"issues/"*" -q .state"*) echo open;;\n  *"-X PATCH"*"issues/"*) echo "$*" >> %s/closed.log;;\n\n  *"graphql"*) echo "[]";;\n  *"--include"*) echo "{\\"state\\":\\"open\\"}";;\nesac\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"; }
pass() { bash "$T/s/forkclose.sh" 2>&1; }

# 1. one branch, one pass: the merge was tested on the exact tree that becomes the base, so that sha is a tested base
setup 5; r=$(pass); h=$(g rev-parse --short=9 HEAD)
[ -e "$T/st/auto/basetest-$h" ] && ok "the tested merge is recorded as a tested base" || ko "no basetest-$h: $r"
[ "$(runs)" = 2 ] && ok "the first pass ran the gate twice (base test, branch test)" || ko "gui runs: $(runs)"
g checkout -q -b gui/6 local/next; echo 6 > "$T/fork/f6"; g add .; g commit -q -m "work 6"; g checkout -q local/next
echo '{"items":[{"n":6,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"; pass >/dev/null
[ "$(runs)" = 3 ] && ok "the next landing paid for its own test only: no second base test" || ko "gui runs after the second landing: $(runs)"
rm -rf "$T"

# 2. two code-complete branches editing hub.py: one candidate, one gate run, both land in order
setup 1 2; r=$(pass)
[ "$(runs)" = 2 ] && ok "batch of two: base test once, one gate run for the candidate" || ko "gui runs: $(runs); $r"
[ "$(g log --merges --reverse --format=%s | tr '\n' '|')" = "Merge gui/1 (Refs #1)|Merge gui/2 (Refs #2)|" ] && ok "both branches landed, in order" || ko "merges: $(g log --merges --reverse --format=%s | tr '\n' '|')"
[ "$(wc -l < "$T/closed.log" 2>/dev/null | tr -d ' ')" = 2 ] && ok "both issues closed" || ko "closes: $(cat "$T/closed.log" 2>/dev/null)"
[ -z "$(g branch --list 'fc-batch*')" ] && [ -z "$(g status --porcelain)" ] && [ "$(g branch --show-current)" = local/next ] && ok "no candidate branch left, checkout clean on the base" || ko "leftovers: $(g branch | tr '\n' ' ')"
[ -e "$T/st/auto/basetest-$(g rev-parse --short=9 HEAD)" ] && ok "the landed candidate is a tested base" || ko "no basetest for the landed candidate"
rm -rf "$T"

# 3. red batch: back to one-by-one, the culprit is named and the good branch still lands
setup 4 5; r=$(pass)
case "$r" in *"BATCH RED"*) ok "the red batch says so";; *) ko "no BATCH RED: $r";; esac
case "$r" in *"RED #4"*) ok "one-by-one names the culprit";; *) ko "culprit not named: $r";; esac
case "$r" in *"RED #5"*) ko "the good branch was blamed: $r";; *"FORKCLOSE #5 closed"*) ok "the good branch lands on its own";; *) ko "#5 did not land: $r";; esac
[ ! -e "$T/fork/bad" ] && [ -e "$T/fork/f5" ] && ok "the base holds the good work only" || ko "base content wrong"
rm -rf "$T"

# 4. a later branch that conflicts with an earlier one: only it is sent back
setup 1 2 3; r=$(pass)
case "$r" in *"CONFLICT #3"*) ok "the conflicting later branch is sent back";; *) ko "no CONFLICT #3: $r";; esac
case "$r" in *"CONFLICT #1"*|*"CONFLICT #2"*) ko "an earlier branch was sent back: $r";; *) ok "the earlier branches are not";; esac
[ "$(g log --merges --reverse --format=%s | tr '\n' '|')" = "Merge gui/1 (Refs #1)|Merge gui/2 (Refs #2)|" ] && ok "the earlier branches landed" || ko "merges: $(g log --merges --format=%s | tr '\n' '|')"
rm -rf "$T"

# 5. a red batch it CAN attribute names the culprit instead of serializing every candidate (#13). The gate log names the
# failing test, the pass reads it off the candidate tree to the commit that added it and to the branch that commit belongs
# to, drops that branch and re-gates the rest in the same pass. Measured 2026-10-12: the batch printed "nothing landed,
# falling back to one branch at a time so the culprit is named" and named nothing, while the cause was five ruff errors in
# one file of one branch -- six branches lost their parallelism against max_parallel=10.
setup 5 6 7
g checkout -q gui/7; mkdir -p "$T/fork/tests"; echo x > "$T/fork/tests/test_tooling_x.py"; g add .; g commit -q -m "the spec whose ruff errors red the batch"; g checkout -q local/next
printf "export SG_FORK_PY_CMD='[ -e tests/test_tooling_x.py ] && { echo \"FAILED tests/test_tooling_x.py::test_ruff_check_is_clean - Found 5 errors. No fixes available\"; exit 1; }; true'\n" >> "$T/s/env.sh"
r=$(pass)
cl=$(printf '%s\n' "$r" | grep 'BATCH CULPRIT' | head -1)
case "$cl" in *"tests/test_tooling_x.py"*) ok "#13: the red batch names the failing test";; *) ko "#13: the failing test is not named: $cl";; esac
case "$cl" in *"gui/7"*) ok "#13: the red batch names the branch the failing test belongs to";; *) ko "#13: no branch named in the culprit line: $cl";; esac
case "$cl" in *"the spec whose ruff errors red the batch"*) ok "#13: the red batch names the commit that added the failing test (git log -1 --format=%s -- <test file>)";; *) ko "#13: no commit named in the culprit line: $cl";; esac
case "$r" in *"BATCH RED (5 6 7"*) ok "#13: the batch it could attribute is still reported red";; *) ko "#13: no BATCH RED for the batch it could attribute: $r";; esac
case "$r" in *"FORKCLOSE BATCH landed 5 6"*) ok "#13: the rest is re-gated in the same pass and lands as its own candidate";; *) ko "#13: the rest was serialized instead of re-gated: $r";; esac
[ ! -e "$T/fork/tests/test_tooling_x.py" ] && [ -e "$T/fork/f5" ] && [ -e "$T/fork/f6" ] && ok "#13: the base holds the two good branches and not the culprit's file" || ko "#13: base content wrong (f5:$( [ -e "$T/fork/f5" ] && echo y || echo n) f6:$( [ -e "$T/fork/f6" ] && echo y || echo n) spec:$( [ -e "$T/fork/tests/test_tooling_x.py" ] && echo present || echo absent))"
rm -rf "$T"

# 6. the dropped branch is the only one left (#13): the pass reports the rest as parked and does not retry the same batch
#    -- a candidate is built from at least two branches, so re-gating one buys nothing and retrying the set would spend the
#    same red gate pass on every later pass. The one-at-a-time loop below still names the culprit.
setup 8 7
g checkout -q gui/7; mkdir -p "$T/fork/tests"; echo x > "$T/fork/tests/test_tooling_x.py"; g add .; g commit -q -m "the spec whose ruff errors red the batch"; g checkout -q local/next
printf "export SG_FORK_PY_CMD='[ -e tests/test_tooling_x.py ] && { echo \"FAILED tests/test_tooling_x.py::test_ruff_check_is_clean - Found 5 errors\"; exit 1; }; true'\n" >> "$T/s/env.sh"
r=$(pass)
case "$r" in *"BATCH PARKED"*) ok "#13: a batch with one candidate left after the drop is reported parked";; *) ko "#13: no BATCH PARKED: $r";; esac
case "$r" in *"RED #7"*) ok "#13: the one-at-a-time loop still names the culprit";; *) ko "#13: culprit not named: $r";; esac
[ -e "$T/fork/f8" ] && [ ! -e "$T/fork/tests/test_tooling_x.py" ] && ok "#13: the good branch lands, the culprit's file does not" || ko "#13: base content wrong"
n=$(ls "$T/st/auto"/nobatch-* 2>/dev/null | wc -l | tr -d ' ')
[ "$n" -ge 1 ] && ok "#13: the batch that could not be re-gated is memoized, so it is not retried unchanged" || ko "#13: the set is not memoized: nothing under nobatch-*"
r2=$(pass)
case "$r2" in *"BATCH RED"*) ko "#13: the same batch was retried on the next pass: $r2";; *) ok "#13: the next pass does not retry the parked batch";; esac
rm -rf "$T"; exit $fail
