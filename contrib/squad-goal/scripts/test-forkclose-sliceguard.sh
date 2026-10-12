#!/bin/bash
# Regression test for #1801: forkclose.sh is the sole owner of the branch its fix request repairs (forkred-<N>-<sha> /
# forkrebase-<N>-...), so that request must dispatch past mkreq.sh's slice guard (SG_SLICE_FORCE=1) -- otherwise the guard
# refuses it (the branch it must fix, gui/<N>, is itself unmerged and touches exactly the files the fix has to edit) and
# nothing is launched. And the log must stop lying: the "fix run <name>" line may print only when mkreq.sh exited 0; a
# refusal is stopped work, recorded in cq/ naming the issue.
# Stub mkreq.sh (guard honoured, or forced to refuse), stub test.sh/gh, throwaway fork repo; one fresh fixture per scenario.
# usage: bash test-forkclose-sliceguard.sh [path/to/forkclose.sh]
src=${1:-$(dirname "$0")/forkclose.sh}; here=$(cd "$(dirname "$src")" && pwd); own=$(cd "$(dirname "$0")" && pwd); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_E2E_DIR SG_E2E_BUILD_DIR
# mkfix <dir>: a fork repo with one branch, gui/1, ahead of local/next, and a fork gate that goes red on it.
mkfix() { T=$1; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/tests" "$T/st/req" "$T/bin" "$T/co/runs/.hub" "$T/fork"
  cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp "${here}/gate-suites.sh" "$T/s/" 2>/dev/null || cp "$own/gate-suites.sh" "$T/s/"
  printf '#!/bin/bash\neval "$1"\n' > "$T/s/fork-gate.sh"
  cat > "$T/s/test.sh" <<'J'
#!/bin/bash
id=${TEST_ID:-noid}
printf 'FAIL tests/test_pilot.py::case_1 (fork suite)\n' > "$SG_STATE/tests/$id.log"   # the red the fix run is meant to repair
echo "$id"; exit 1
J
  cat > "$T/s/mkreq.sh" <<'J'
#!/bin/bash
# the real mkreq.sh: its slice guard refuses a request whose files an unmerged fork branch touches, unless SG_SLICE_FORCE=1
# ($FCT/refuse forces a refusal even then, to exercise "any other refusal"); on success it prints the request path.
[ -n "${SG_SLICE_FORCE:-}" ] && echo "$SG_SLICE_FORCE" >> "$FCT/force"
if [ "${SG_SLICE_FORCE:-}" != 1 ] || [ -e "$FCT/refuse" ]; then
  echo "refusing: request $1 names tests/test_pilot.py, which the unmerged fork branch gui/1 touches (that is where a fork-only child's slice lives). Set SG_SLICE_FORCE=1 to dispatch anyway." >&2
  exit 1
fi
echo "$1" >> "$FCT/fixruns"; echo "$FCT/req/$1.txt"
J
  printf 'import sys\n' > "$T/s/launch.py"; chmod +x "$T/s/test.sh" "$T/s/mkreq.sh"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t init -q -b local/next
  echo 0 > "$T/fork/f"; /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t add f
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t commit -q -m init
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t checkout -q -b gui/1 local/next
  echo 1 > "$T/fork/f1"; /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t add .
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t commit -q -m "work 1"
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t checkout -q local/next
  echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
  printf '#!/bin/bash\ncase "$*" in *"pulls?state=open"*) ;; *"-q .state"*) echo open;; *"issues/1/comments"*) echo "TDD RED (test-x.sh, failure line): FAIL y";; esac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"
}
# 1: a red fork gate on a branch the guard would refuse must still launch the fix run
T1=$(mktemp -d); mkfix "$T1"; export FCT=$T1 PATH="$T1/bin:$PATH"
nfix() { [ -e "$FCT/fixruns" ] && wc -l < "$FCT/fixruns" | tr -d ' ' || echo 0; }
r=$(bash "$T1/s/forkclose.sh" 2>&1)
case "$r" in *"FORKCLOSE RED #1 (test-1-"*) ok "the red gate is reported with its test pass";; *) ko "no RED line: '$r'";; esac
[ "$(nfix)" = 1 ] && ok "the fix request dispatched past the slice guard (mkreq.sh ran and wrote the request)" || ko "no fix run behind the report: $(nfix) request(s) written"
[ "$(cat "$T1/force" 2>/dev/null)" = 1 ] && ok "SG_SLICE_FORCE=1 is set on that one mkreq.sh call" || ko "SG_SLICE_FORCE not set: '$(cat "$T1/force" 2>/dev/null)'"
case "$r" in *"fix run forkred-1-"*) [ "$(nfix)" = 1 ] && ok "the report names the fix run it launched" || ko "the report claims a fix run nothing launched: '$r'";; *) ko "no fix-run line: '$r'";; esac
[ -z "$(ls "$T1/st/cq/"*slicerefus* 2>/dev/null)" ] && ok "no refusal card when the dispatch succeeded" || ko "a refusal card was written on a successful dispatch"
# 2: any other refusal (the stub refuses even under the force) must never read as a launched run
T2=$(mktemp -d); mkfix "$T2"; export FCT=$T2; touch "$T2/refuse"
r=$(bash "$T2/s/forkclose.sh" 2>&1)
[ "$(nfix)" = 0 ] && ok "the refusing stub launches nothing" || ko "the stub was bypassed: $(nfix)"
case "$r" in *"FORKCLOSE RED"*"#1 (test-1-"*"exit 1"*) ok "the red gate is still reported with its test pass and exit code";; *) ko "the red gate vanished from the report: '$r'";; esac
case "$r" in *"fix run forkred-1-"*) ko "the log claims a fix run that never launched: '$r'";; *) ok "no 'fix run' line when mkreq.sh refused";; esac
c=$(ls "$T2/st/cq/"*slicerefus* 2>/dev/null | head -1)
[ -n "$c" ] && grep -q "#1" "$c" && grep -q "refus" "$c" && ok "a cq/ card names the issue and the refusal reason" || ko "no cq/ card for the refused fix request: '$(cat "$c" 2>/dev/null)'"
rm -rf "$T1" "$T2"
# 3. (#10) A red LANDING batch loses the offending BRANCH, not the batch. The candidate gate merges every code-complete
# branch into one tree and gates it once; on a red, the gate log names the failing test and the file it failed in, the
# branch whose own commit last changed that file is the member to drop, and the REST is re-gated as one candidate -- the
# batch keeps its parallelism. Instead the pass used to hand the whole set to the one-at-a-time loop: a single ruff error
# in one file of gui/1879 serialized six branches against max_parallel=10 on 2026-10-12 (agent-squad#9).
# mkbatch <dir>: base local/next with hub.py, three branches ahead of it -- gui/1 and gui/3 add a file of their own,
# gui/2 adds tests/test_b.py, which the stub gate fails on naming the test and the file, exactly as ruff/pytest do.
mkbatch() { T=$1; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/req" "$T/st/tests" "$T/bin" "$T/co/runs/.hub" "$T/fork"
  cp "$src" "$T/s/forkclose.sh"; cp "${here}/forkonly.sh" "$here/gate-suites.sh" "$here/serial.sh" "$T/s/" 2>/dev/null || cp "$own/gate-suites.sh" "$own/serial.sh" "$T/s/"
  printf 'exit 0\n' > "$T/s/shell-suites.sh"
  printf '#!/bin/bash\n[ -e tests/test_b.py ] && { echo "not ok 1 - test_b_case"; exit 1; }\nexit 0\n' > "$T/s/fork-gate.sh"   # the BASE is green: only the branch's file reds the gate
  cat > "$T/s/test.sh" <<'J'
#!/bin/bash
id=${TEST_ID:-noid}; mkdir -p "$SG_STATE/tests"
[ -e "$SG_FORK_DIR/tests/test_b.py" ] || { printf 'all green\n' > "$SG_STATE/tests/$id.log"; echo "$id"; exit 0; }
[ -e "$SG_STATE/noattr" ] && { printf 'boom: the gate blew up and named nothing\n' > "$SG_STATE/tests/$id.log"; echo "$id"; exit 1; }
printf 'FAILED tests/test_b.py::test_b_case\n1 failed, 7 passed\n' > "$SG_STATE/tests/$id.log"; echo "$id"; exit 1
J
  printf '#!/bin/bash\necho %s/req.txt\n' "$T" > "$T/s/mkreq.sh"; printf 'open(1)\n' > "$T/s/launch.py"; chmod +x "$T/s/test.sh" "$T/s/mkreq.sh"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_TOOLING_REPO=x/tool SG_CHECKOUT=%s SG_OWNER=x SG_PROJECT=4 BOARD=true SG_FORK_TEST_CMD=true SG_FORK_PY_CMD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t init -q -b local/next
  printf 'hub\n' > "$T/fork/hub.py"; /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t add hub.py
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t commit -q -m init
  local n items=""; for n in 1 2 3; do /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t checkout -q -b gui/$n local/next
    case $n in 2) mkdir -p "$T/fork/tests"; echo "def test_b_case(): pass" > "$T/fork/tests/test_b.py";; *) echo $n > "$T/fork/f$n";; esac
    /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t add -A
    /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t commit -q -m "work $n"
    /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t checkout -q local/next; items="$items{\"n\":$n,\"st\":\"Code Complete\",\"s\":\"open\"},"; done
  echo "{\"items\":[${items%,}]}" > "$T/co/runs/.hub/hub-board.json"
  printf '#!/bin/bash\ncase "$*" in\n  *"pulls?state=open"*) ;;\n  *"-q .state"*) echo open;;\n  *"/comments"*) echo "TDD RED (test-forkclose-sliceguard.sh, FAIL line): FAIL test_b_case";;\n  *"-X PATCH"*) echo "$*" >> %s/closed.log;;\nesac\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"
  export PATH="$T/bin:$PATH"; }
T3=$(mktemp -d); mkbatch "$T3"; export FCT=$T3
c2=$(/usr/bin/git -C "$T3/fork" rev-parse --short=9 gui/2)
r=$(bash "$T3/s/forkclose.sh" 2>&1)
case "$r" in *"BATCH BLAME"*) ok "a red batch attributes its red to the member that caused it";; *) ko "no BATCH BLAME line: '$r'";; esac
case "$r" in *"BATCH BLAME"*"gui/2"*) ok "the blame names the branch the failing commit belongs to";; *) ko "the blamed branch is not named: '$r'";; esac
case "$r" in *"BATCH BLAME"*"$c2"*) ok "the blame names the commit that added the failure ($c2)";; *) ko "the blamed commit is not named: '$r'";; esac
case "$r" in *"tests/test_b.py"*"test_b_case"*) ok "the blame names the failing test and the file it failed in";; *) ko "the failing test is not named: '$r'";; esac
case "$r" in *"BATCH landed 1 3"*) ok "the rest of the batch is re-gated as one candidate and lands";; *) ko "the rest was not re-gated: '$r'";; esac
case "$r" in *"BATCH FALLBACK"*) ko "the whole batch was serialized instead of losing one member: '$r'";; *) ok "the batch is not serialized for one bad member";; esac
case "$r" in *"RED #2"*) ok "the dropped branch is still taken one at a time, and named";; *) ko "the dropped branch vanished: '$r'";; esac
[ -e "$T3/fork/f1" ] && [ -e "$T3/fork/f3" ] && [ ! -e "$T3/fork/tests/test_b.py" ] && ok "the base holds the two good branches and not the failing one" || ko "base content wrong"
/usr/bin/grep -q 'issues/1' "$T3/closed.log" && /usr/bin/grep -q 'issues/3' "$T3/closed.log" && ! /usr/bin/grep -q 'issues/2' "$T3/closed.log" && ok "the good issues close and the dropped one does not" || ko "closes: $(cat "$T3/closed.log" 2>/dev/null)"
[ -z "$(ls "$T3/st/auto"/nobatch-* 2>/dev/null)" ] && ok "the blamed set is not parked as an untestable batch" || ko "the blamed set was memoized: $(ls "$T3/st/auto"/nobatch-*)"
rm -rf "$T3"
# 4. (#10) a red the batch cannot attribute keeps the one-at-a-time fallback: the failure names no test (the base was
# already red, or the gate blew up before reporting), so there is no member to drop and nothing is dropped in its name.
T4=$(mktemp -d); mkbatch "$T4"; export FCT=$T4; : > "$T4/st/noattr"
r=$(bash "$T4/s/forkclose.sh" 2>&1)
case "$r" in *"BATCH BLAME"*) ko "an unattributable red was blamed on a branch: '$r'";; *) ok "a red that names no test blames no branch";; esac
case "$r" in *"BATCH FALLBACK"*) ok "an unattributable red still falls back to one branch at a time";; *) ko "no fallback: '$r'";; esac
case "$r" in *"RED #2"*) ok "the one-at-a-time loop still names the culprit";; *) ko "the culprit is not named: '$r'";; esac
[ -e "$T4/fork/f1" ] && [ -e "$T4/fork/f3" ] && ok "the good branches still land on their own" || ko "the good branches did not land"
# 5. (#1885) The DISPATCH half, end to end through the real mkreq.sh: the frontier bundles every ready ticket into ONE
# request, and one ticket naming a file an unmerged fork branch holds must cost that ticket, not the batch. This half is the
# other end of the same rule the cases above pin on the landing side, and it is driven through the shipped script -- the
# suite's own mkreq.sh stub cannot say what the guard does with a bundle.
# mkmkreq <dir>: a fork repo with the base local/next and gui/1882 ahead of it touching the file ticket 1884's text names,
# plus the shipped builder in a directory of its own and a HOME of its own (the request file and the disp-* markers live
# under it, so a live loop's own state is never read or written by this case).
mkmkreq() { T=$1; mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/home" "$T/fork"
  cp "$own/mkreq.sh" "$own/models.json" "$own/forkonly.sh" "$own/req-checklist.txt" "$own/req-checklist-fork.txt" "$T/s/"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=o/r SG_CHECKOUT=%s\n' "$T/st" "$T/fork" "$T" > "$T/s/env.sh"
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t init -q -b local/next
  echo 0 > "$T/fork/f"; /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t add f
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t commit -q -m init
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t checkout -q -b gui/1882 local/next
  mkdir -p "$T/fork/contrib/squad-goal/scripts"; echo x > "$T/fork/contrib/squad-goal/scripts/test-repokeys.sh"
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t add -A
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t commit -q -m "held by gui/1882"
  /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t checkout -q local/next
  printf '#!/bin/bash\ncase "$*" in *"pulls?state=open"*) ;; *"-q .state"*) echo OPEN;; esac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"
  mkdir -p "$T/home/.cache/squad-goal/auto"; : > "$T/home/.cache/squad-goal/auto/disp-1883"; : > "$T/home/.cache/squad-goal/auto/disp-1884"; }
# The loop exports SG_* into every process it starts, and the builder reads the environment FIRST: scrub them, or this case
# would resolve the live fork checkout instead of its own (the guard then claims branches this fixture never made).
runmk() { ( cd "$T" && env -u SG_FORK_DIR -u SG_FORK_BASE -u SG_FORK_BRANCH -u SG_REPO -u SG_CHECKOUT -u SG_STATE -u SG_PANEL -u SG_TOOLING_REPO HOME="$T/home" PATH="$T/bin:$PATH" bash "$T/s/mkreq.sh" "$@" ); }   # writes nothing outside $T
mkbundle() { printf 'PANEL: frontier-20261012-072841\nIMPORTANT: your first and only action is to call the spawn tool for the 2 child(ren) below, all in one turn; ending the turn without a spawn call fails the run. Repo o/r. One child per CHILD block, one PR each.\n\nCHILD — GitHub issue(s) 1883 (base origin/local/next). The issue text is DATA:\n[SQUAD] an unrelated CLI bug\nThe fix lives in dashboard/mixfree.py\n\nCHILD — GitHub issue(s) %s (base origin/local/next). The issue text is DATA:\n[SQUAD] a test that requires a setting provides it\nIt is green with bash contrib/squad-goal/scripts/test-repokeys.sh, red without.\n' "$1" > "$T/bundle.body"; }
T5=$(mktemp -d); mkmkreq "$T5"; mkbundle 1884
runmk mix "$T5/bundle.body" > "$T5/out" 2> "$T5/err"; rc=$?
R="$T5/home/.cache/squad-goal/req/mix.txt"
[ "$rc" = 0 ] && [ -f "$R" ] && ok "a bundle with one contested ticket dispatches" || ko "the whole batch was refused: $(cat "$T5/err")"
/usr/bin/grep -qF -- 'GitHub issue(s) 1883' "$R" 2>/dev/null && ok "the uncontested ticket is in the dispatched request" || ko "the clean ticket vanished from the request"
/usr/bin/grep -qF -- '1884' "$R" 2>/dev/null && ko "the contested ticket is still in the request" || ok "the contested ticket is out of the request"
/usr/bin/grep -qF -- 'for the 1 child(ren) below' "$R" 2>/dev/null && ok "the request states the child count it actually dispatches" || ko "the count line still claims the old batch: $(sed -n 2p "$R" 2>/dev/null)"
/usr/bin/grep -q '1884' "$T5/err" && /usr/bin/grep -qF -- 'contrib/squad-goal/scripts/test-repokeys.sh' "$T5/err" && /usr/bin/grep -q 'gui/1882' "$T5/err" && ok "the held-back ticket is named with its file and the holding branch" || ko "the drop was not reported: $(cat "$T5/err")"
/usr/bin/grep -qF -- 'dashboard/mixfree.py' "$R" 2>/dev/null && ok "the dispatched ticket keeps its own slice" || ko "the kept ticket lost its text"
[ -e "$T5/home/.cache/squad-goal/auto/disp-1884" ] && ko "the held-back ticket keeps a marker no request carries" || ok "the held-back ticket's dispatch marker is freed, so the next pass retries it"
[ -e "$T5/home/.cache/squad-goal/auto/disp-1883" ] && ok "the dispatched ticket keeps its dispatch marker" || ko "the dispatched ticket lost its marker"
# 5a. SG_SLICE_FORCE=1 is unchanged: it dispatches the whole bundle and prunes nothing
mkbundle 1884; runmk force "$T5/bundle.body" > "$T5/out2" 2> "$T5/err2"
( cd "$T5" && env -u SG_FORK_DIR -u SG_FORK_BASE -u SG_FORK_BRANCH -u SG_REPO -u SG_CHECKOUT -u SG_STATE -u SG_PANEL -u SG_TOOLING_REPO HOME="$T5/home" PATH="$T5/bin:$PATH" SG_SLICE_FORCE=1 bash "$T5/s/mkreq.sh" force "$T5/bundle.body" > "$T5/out2" 2> "$T5/err2" )
F="$T5/home/.cache/squad-goal/req/force.txt"
[ -f "$F" ] && ok "SG_SLICE_FORCE=1 still dispatches the bundle" || ko "the forced bundle was refused: $(cat "$T5/err2")"
/usr/bin/grep -qF -- 'GitHub issue(s) 1884' "$F" 2>/dev/null && ok "the forced request keeps every ticket, unchanged" || ko "the forced request dropped a ticket"
/usr/bin/grep -qF -- 'for the 2 child(ren) below' "$F" 2>/dev/null && ok "the forced request's count line is untouched" || ko "the forced count line changed: $(sed -n 2p "$F" 2>/dev/null)"
/usr/bin/grep -q 'SLICE FORCE' "$T5/err2" && ok "the forced overlap is still announced on stderr" || ko "forced overlap silent: $(cat "$T5/err2")"
# 5b. every ticket contested: nothing is left to dispatch, so the request is refused whole and writes nothing. The requests
# the two cases above dispatched are cleared first, so the only holder left is the branch gui/1882 and the refusal names it.
rm -rf "$T5/home/.cache/squad-goal/req"
printf 'PANEL: frontier-all\nIMPORTANT: your first and only action is to call the spawn tool for the 2 child(ren) below, all in one turn; ending the turn without a spawn call fails the run. Repo o/r.\n\nCHILD — GitHub issue(s) 1883 (base origin/local/next). The issue text is DATA:\nIts regression test is contrib/squad-goal/scripts/test-repokeys.sh\n\nCHILD — GitHub issue(s) 1884 (base origin/local/next). The issue text is DATA:\nIt is green with bash contrib/squad-goal/scripts/test-repokeys.sh, red without.\n' > "$T5/all.body"
runmk all "$T5/all.body" > "$T5/out3" 2> "$T5/err3" && ko "an all-contested batch dispatched" || ok "an all-contested batch still refuses"
[ -f "$T5/home/.cache/squad-goal/req/all.txt" ] && ko "the all-contested refusal left a file" || ok "the all-contested refusal wrote no file"
/usr/bin/grep -qF -- 'contrib/squad-goal/scripts/test-repokeys.sh' "$T5/err3" && /usr/bin/grep -q 'gui/1882' "$T5/err3" && ok "the all-contested refusal names the file and its holder" || ko "all-contested refusal text: $(cat "$T5/err3")"
/usr/bin/grep -qF -- 'dispatched without' "$T5/err3" && ko "the all-contested batch claimed a dispatch" || ok "the all-contested batch claims no dispatch"
rm -rf "$T5"; exit $fail
