#!/bin/bash
# Regression test for the fork merge queue (#1409): a tested merge is recorded as a tested base (no second run), several code-complete branches land on one candidate with one gate run, a red batch falls back to one-by-one so the culprit is named, and a conflicting later branch is the only one sent back. Stub gate, stub gh, throwaway fork repo, no emulator.
unset SG_E2E_DIR SG_E2E_BUILD_DIR SG_E2E_BUILD_CMD   # the real e2e checkouts of the caller must never be touched by a stub run
here="$(cd "$(dirname "$0")" && pwd)"; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
runs() { [ -f "$T/gui.log" ] && wc -l < "$T/gui.log" | tr -d ' ' || echo 0; }
setup() {   # setup <issue>...: a fork whose base has hub.py (10 lines) and one branch per issue; 1 edits line 1, 2 edits line 10, 3 edits line 1 too (conflicts with 1), 4 adds the file "bad" (the gate goes red on it), others add their own file
  T=$(mktemp -d); mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/req" "$T/bin" "$T/co/runs/.hub" "$T/fork"
  cp "$here/forkclose.sh" "$here/forkonly.sh" "$here/fork-gate.sh" "$here/gate-suites.sh" "$here/serial.sh" "$T/s/"; printf 'exit 0\n' > "$T/s/shell-suites.sh"
  printf '#!/bin/bash\niss=$1; shift; [ "$1" = -C ] && { cd "$2"; shift 2; }; [ "$1" = -- ] && shift; "$@"; rc=$?; echo "TEST #$iss x test-$iss-x"; exit $rc\n' > "$T/s/test.sh"
  printf '#!/bin/bash\necho %s/req.txt\n' "$T" > "$T/s/mkreq.sh"; printf 'open(__import__("sys").argv[1]+".launched","w")\n' > "$T/s/launch.py"; chmod +x "$T/s/test.sh" "$T/s/mkreq.sh"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_OWNER=x SG_PROJECT=4 BOARD=true\nexport SG_FORK_TEST_CMD="echo gui >> %s/gui.log; [ ! -e bad ]"\nexport SG_FORK_PY_CMD=true\n' "$T/st" "$T/fork" "$T/co" "$T" > "$T/s/env.sh"
  g init -q -b local/next; seq 1 10 > "$T/fork/hub.py"; g add .; g commit -q -m init
  local n items=""; for n in "$@"; do g checkout -q -b gui/$n local/next
    case $n in 1|3) sed -i '' "1s/.*/edit-$n/" "$T/fork/hub.py";; 2) sed -i '' "10s/.*/edit-$n/" "$T/fork/hub.py";; 4) echo x > "$T/fork/bad";; *) echo $n > "$T/fork/f$n";; esac
    g add .; g commit -q -m "work $n"; g checkout -q local/next; items="$items{\"n\":$n,\"st\":\"Code Complete\",\"s\":\"open\"},"; done
  echo "{\"items\":[${items%,}]}" > "$T/co/runs/.hub/hub-board.json"
  printf '#!/bin/bash\ncase "$*" in\n  *"issues/"*" -q .state"*) echo open;;\n  *"-X PATCH"*"issues/"*) echo "$*" >> %s/closed.log;;\nesac\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"; }
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
rm -rf "$T"; exit $fail
