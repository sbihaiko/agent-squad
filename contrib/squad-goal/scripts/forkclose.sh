#!/bin/bash
# usage: forkclose.sh   (watch.sh, every loop; FC_DRY=1 prints the candidates)
# Fork work merges itself: an issue that also has a MesenAI PR waits for it, then gets its fork half merged with no close.
# Fork-only work closes itself: an open issue whose card is Code Complete, with no open PR naming it and a fork branch
# ($SG_FORK_BRANCH, %n = issue) ahead of $SG_FORK_BASE in $SG_FORK_DIR, is merged into the base (--no-ff), the card goes to
# Testing and the fork suite ($SG_FORK_TEST_CMD) runs through test.sh. Green: the issue closes with the evidence and the
# card goes Done. Red: the merge is reverted (git revert -m 1, never a reset) and the card goes back to In Progress. A merge
# conflict aborts and launches a conflict-fix squad run on the branch (base merged in, both sides kept); its new head re-arms the close. One issue at a time (lock); a fork checkout a child left on its own branch is put back on the base by the pass itself while the tree is clean and no live run names that branch (WAIT, naming the reason, when it is not).
# An issue whose `## Blocked by` names an issue with an unlanded fork branch waits for it (FORKCLOSE WAIT, once per set): its tests import the blocker's files.
# Cheaper queue (#1409): a green merge is recorded as a tested base (basetest-<new sha>), so the next landing pays no second base test; the suites a
# branch's diff cannot affect are skipped (gate-suites.sh -> FG_SUITES, anything unknown runs the full gate); and when several branches are code
# complete they merge, in order, onto one candidate branch (fc-batch, the base is untouched) that the gate runs once: green fast-forwards the base
# and each issue then closes through the late-close path below with that run as its evidence; red drops the candidate and the loop lands them one
# at a time, so a culprit is still named. A branch that conflicts with an earlier one stays out of the batch and gets its conflict-fix run as usual.
# Owner-approved Done rule for fork-only issues (#1180, 2026-10-10). Unset SG_FORK_DIR turns it off.
. "$(dirname "$0")/env.sh"; here="$(cd "$(dirname "$0")" && pwd)"; A="$SG_STATE/auto"; mkdir -p "$A"
[ -n "${SG_FORK_DIR:-}" ] && [ -d "$SG_FORK_DIR" ] || exit 0; [ -e "$A/off" ] && exit 0
L="$A/forkclose.lock"   # a test pass is still running; a lock whose pid is gone (loop restarted mid-pass) is stale
if [ -z "${FC_COLLECT:-}" ]; then   # the batch pre-pass runs this script again under the lock its parent holds, only to list the candidates
  if ! mkdir "$L" 2>/dev/null; then kill -0 "$(cat "$L/pid" 2>/dev/null)" 2>/dev/null && exit 0; rm -rf "$L"; mkdir "$L" || exit 0; fi
  echo $$ > "$L/pid"
  trap 'rm -rf "$L"' EXIT
fi
G="/usr/bin/git -C $SG_FORK_DIR"
now=$(date +%s)
# The branches a live run is working on: a squad child runs from its own run ws, so a `git -C` on the fork worktree leaves
# no cwd inside it and the test below would call every branch orphaned while its fix run is mid-edit. A run is live when
# its record was touched inside the orphan window, and it names the branch it was launched for.
livebrs=" $(for r in $(ls -t "$SG_CHECKOUT/runs" 2>/dev/null | head -60); do
  f="$SG_CHECKOUT/runs/$r/record.jsonl"; [ -f "$f" ] || continue
  [ $(( now - $(stat -f %m "$f") )) -lt $(( ${SG_FORK_ORPHAN_MINUTES:-20} * 60 )) ] || continue
  grep -oE 'gui/[0-9]+' "$SG_CHECKOUT/runs/$r/request.txt" 2>/dev/null | sort -u | tr '\n' ' '
done) "
# A branch ahead of the base with nobody on it and no recent commit: the fix run meant to move it is gone. Without this its
# card (In Progress) and its marker (fork-<n>-<head>) would park it forever: a wait whose only release is a process that died.
orphan() { local b=$1 w t
  case "$livebrs" in *" $b "*) return 1;; esac
  w=$($G worktree list --porcelain | awk -v x="refs/heads/$b" '/^worktree /{p=$2} $0=="branch "x{print p}')
  [ -n "$w" ] || return 1
  printf '%s\n' "$(/usr/sbin/lsof -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')" | grep -qF "$w/" && return 1
  t=$($G log -1 --format=%ct "$b" 2>/dev/null) || return 1
  [ $(( now - t )) -ge $(( ${SG_FORK_ORPHAN_MINUTES:-20} * 60 )) ]; }
# What holds a branch: a live run that names it, or a process whose cwd sits inside its worktree. Prints the reason; empty = free.
# Our own shell is inside it only when the loop itself was started from that checkout, and this lsof form prints no pid, so it
# cannot tell the two apart; that case is read as free, which is what the loop wants (it does not hold a branch from itself).
held() { local w
  case "$livebrs" in *" $1 "*) echo "a live run names it"; return;; esac
  w=$($G worktree list --porcelain | awk -v x="refs/heads/$1" '/^worktree /{p=$2} $0=="branch "x{print p}')
  [ -n "$w" ] || return
  case "$PWD/" in "$w"/*) return;; esac
  printf '%s\n' "$(/usr/sbin/lsof -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')" | grep -qF "$w/" && echo "a process has its cwd inside its worktree"; }
# A fork-only child that works in the shared checkout instead of its own ~/wt-as-<N> worktree leaves it on its branch, and then
# one WAIT line per pass blocks every merge, forever, with nothing naming the cause (#1798). A clean tree nobody holds is the
# loop's own to move back to the base; uncommitted work is never reset and a branch a live run names is never taken from under
# it -- both are named instead, so the operator reads which of the two it is rather than the same silent WAIT.
needbase() { local cb why
  if [ -n "$($G status --porcelain --untracked-files=no)" ]; then
    echo "FORKCLOSE WAIT $1: $SG_FORK_DIR has uncommitted changes; a merge pass needs a clean tree, and forkclose never resets or checks out over someone's work. Commit them, or move the checkout to $SG_FORK_BASE; the pass resumes on its own."
    return 1; fi
  cb=$($G branch --show-current 2>/dev/null)
  [ "$cb" = "$SG_FORK_BASE" ] && return 0
  why=$(held "$cb")
  if [ -n "$why" ]; then echo "FORKCLOSE WAIT $1: $SG_FORK_DIR is on $cb and $why; not moving a checkout in use. A fork-only child belongs in its own ~/wt-as-<N> worktree (forkonly.sh), never in the shared checkout."
    return 1; fi
  if $G checkout -q "$SG_FORK_BASE" 2>/dev/null; then
    echo "FORKCLOSE CHECKOUT $1: $SG_FORK_DIR was left on $cb (clean tree, nobody in it); returned it to $SG_FORK_BASE"
    return 0; fi
  echo "FORKCLOSE WAIT $1: $SG_FORK_DIR is on $cb and could not be returned to $SG_FORK_BASE"; return 1; }
lost() { $G diff --name-only --diff-filter=A "$SG_FORK_BASE" "$1" 2>/dev/null; }   # files the branch has that the base lacks: a revert of a merge of the branch (or a sibling's merge that carried it) took them, whatever commit added them
mergebr() { if $G merge --no-ff --no-commit -q "$br" >/dev/null 2>&1; then :; else [ -n "$lf" ] || return 1; fi   # ahead of the base AND files lost to a revert: the merge only brings the commits after the revert, so a modify/delete conflict on a lost file is expected and the branch's version of every lost file wins
  if [ -n "$lf" ]; then $G checkout -q "$br" -- $lf || return 1; [ -z "$($G diff --name-only --diff-filter=U)" ] || return 1; fi
  $G commit -q --no-edit -m "Merge $br (Refs #$n)" >/dev/null 2>&1 || [ -z "$($G rev-parse -q --verify MERGE_HEAD)" ]; }
relandbr() { $G checkout -q "$br" -- $lf || { $G reset -q --hard HEAD; return 1; }   # the branch is an ancestor already (merging is a no-op): restore the lost files in a merge commit so the close and the revert (-m 1) work as usual
  t=$($G write-tree) && c=$($G commit-tree "$t" -p HEAD -p "$br" -m "Merge $br (Refs #$n)") && $G merge --ff-only -q "$c" >/dev/null 2>&1 || { $G reset -q --hard HEAD; return 1; }; }
brof() { echo "${SG_FORK_BRANCH//%n/$1}"; }
# a red base reds every branch at once: test it once per base head, before merging anything. 0 = the base is green (or already known green), 1 = wait
basegate() { local l=$1 bt
  if [ -e "$A/basered-$bs" ]; then
    # A park nobody hears about is a silent stall (#1407): say so once per base sha, naming the failing test and who waits.
    if [ ! -e "$A/basered-$bs.escalated" ]; then touch "$A/basered-$bs.escalated"
      ft=$(grep -E 'FAIL|not ok' "$SG_STATE/basetest-$bs.log" 2>/dev/null | grep -v 'fork gate' | head -3)
      wb=""; for w in $cc; do [ "$($G rev-list --count "$SG_FORK_BASE..${SG_FORK_BRANCH//%n/$w}" 2>/dev/null)" -gt 0 ] && wb="$wb ${SG_FORK_BRANCH//%n/$w}"; done
      mkdir -p "$SG_STATE/cq"; printf "Fork base is red: %s %s fails the fork suite on its own, so every branch waiting to land is parked:%s.\nFailing test:\n\`\`\`\n%s\n\`\`\`\nLog: %s/basetest-%s.log. After %s min the base suite is retried once on its own; a green retry lifts the park, a second red keeps it.\n" "$SG_FORK_BASE" "$bs" "${wb:- $l}" "${ft:-(no FAIL line in the log)}" "$SG_STATE" "$bs" "${SG_FORK_BASERED_BUDGET_MINUTES:-30}" > "$SG_STATE/cq/${l//[^0-9a-z]/}-basered$$.md"
    fi
    # Past the budget, one solo retry (this pass holds the lock and the e2e rebuild comes later, so no rebuild is in flight): a red that came from a rebuild window must not park the branches forever.
    if [ ! -e "$A/basered-$bs.retried" ] && [ $(( now - $(stat -f %m "$A/basered-$bs") )) -ge $(( ${SG_FORK_BASERED_BUDGET_MINUTES:-30} * 60 )) ]; then touch "$A/basered-$bs.retried"
      if ( cd "$SG_FORK_DIR" && FG_SUITES=full bash "$here/fork-gate.sh" "$SG_FORK_TEST_CMD" ) > "$SG_STATE/basetest-$bs.log" 2>&1; then rm -f "$A/basered-$bs" "$A/basered-$bs.tries"; echo "$SG_STATE/basetest-$bs.log" > "$A/basetest-$bs"; echo "FORKCLOSE BASE-GREEN $l: $SG_FORK_BASE $bs passed the solo retry after the park; lifting it"; return 0
      else echo "FORKCLOSE BASE-RED $l: the solo retry of $SG_FORK_BASE $bs failed too (see $SG_STATE/basetest-$bs.log); the park stays"; return 1; fi
    else echo "FORKCLOSE BASE-RED $l: $SG_FORK_BASE $bs fails the fork suite on its own, so a red run here would not be the branch's fault; waiting for a green base"; return 1; fi
  fi
  [ -e "$A/basetest-$bs" ] && return 0
  if ( cd "$SG_FORK_DIR" && FG_SUITES=full bash "$here/fork-gate.sh" "$SG_FORK_TEST_CMD" ) > "$SG_STATE/basetest-$bs.log" 2>&1; then echo "$SG_STATE/basetest-$bs.log" > "$A/basetest-$bs"; rm -f "$A/basered-$bs.tries"; return 0; fi
  # one sample is not proof: the full suite flakes on the real emulator (a hold step loses the window to a suite running in
  # parallel), and a single red would park every branch on a flake. Two consecutive reds is the base, one is a retry.
  bt=$(( $(cat "$A/basered-$bs.tries" 2>/dev/null || echo 0) + 1 )); echo "$bt" > "$A/basered-$bs.tries"
  if [ "$bt" -ge "${SG_FORK_BASERED_TRIES:-2}" ]; then touch "$A/basered-$bs"; echo "FORKCLOSE BASE-RED $l: $SG_FORK_BASE $bs fails the fork suite on its own $bt runs in a row (see $SG_STATE/basetest-$bs.log); not merging, no revert, no fix run. Every branch is parked until the base is green."
  else echo "FORKCLOSE BASE-FLAKE $l: $SG_FORK_BASE $bs failed the fork suite on its own (run $bt of ${SG_FORK_BASERED_TRIES:-2}); one red is not proof, retrying next cycle."; fi
  return 1; }
e2eprep() {   # $1 = label; the e2e checkouts the real-app cases drive, at the base's head
  [ -n "${SG_E2E_DIR:-}" ] && [ -d "$SG_E2E_DIR" ] && [ -z "$(/usr/bin/git -C "$SG_E2E_DIR" status --porcelain --untracked-files=no)" ] \
    && /usr/bin/git -C "$SG_E2E_DIR" fetch -q origin && /usr/bin/git -C "$SG_E2E_DIR" checkout -q --detach "origin/$SG_BASE"
  if [ -n "${SG_E2E_BUILD_DIR:-}" ] && [ -d "$SG_E2E_BUILD_DIR" ]; then   # the built emulator the real-app cases drive: rebuild at the base's head (incremental) under the gui-suite lock: the GUI suite never launches a build that is mid-publish (#1410)
    /usr/bin/git -C "$SG_E2E_BUILD_DIR" fetch -q origin && /usr/bin/git -C "$SG_E2E_BUILD_DIR" checkout -q --detach "origin/$SG_BASE" \
      && bash "$here/serial.sh" gui-suite bash -c 'cd "$1" && eval "$2"' _ "$SG_E2E_BUILD_DIR" "${SG_E2E_BUILD_CMD:-make core && make ui}" > "$SG_STATE/e2e-build.log" 2>&1 \
      || echo "FORKCLOSE WARN $1: rebuilding $SG_E2E_BUILD_DIR failed (see e2e-build.log)"
  fi
  if [ -n "${SG_E2E_HEADLESS_DIR:-}" ] && [ -d "$SG_E2E_HEADLESS_DIR" ]; then   # the separate tree the headless case builds in (#1414): same base head as the e2e build, moved under the same gui-suite lock so no headless build runs mid-checkout
    bash "$here/serial.sh" gui-suite bash -c 'cd "$1" && /usr/bin/git fetch -q origin && /usr/bin/git checkout -q --detach "origin/$2"' _ "$SG_E2E_HEADLESS_DIR" "$SG_BASE" > "$SG_STATE/e2e-headless.log" 2>&1 \
      || echo "FORKCLOSE WARN $1: moving $SG_E2E_HEADLESS_DIR to the base head failed (see e2e-headless.log)"
  fi
}
bdiff() { $G diff --name-only "$($G merge-base "$1" "$2" 2>/dev/null)" "$2" 2>/dev/null; }   # what branch $2 changed since it left $1 (empty on any doubt: the full gate)
batchpass() { local cf bl nn b2 key merged="" files="" suites first out rc id
  cf=$(mktemp); FC_COLLECT=$cf bash "$0"; bl=$(sort -un "$cf"); rm -f "$cf"   # the loop's own eligibility rules; its notices (WAIT, ORPHAN) print here
  [ "$(wc -w <<<"$bl")" -ge 2 ] || return 0
  needbase "batch" || return 0   # a checkout left on a branch is returned to the base here, so the batch is not skipped for a pass (the loop below says WAIT when it cannot be)
  bs=$($G rev-parse --short=9 "$SG_FORK_BASE"); basegate "batch" || exit 0
  key=$(for nn in $bl; do $G rev-parse "$(brof $nn)"; done | cksum | cut -d' ' -f1); [ -e "$A/nobatch-$key" ] && return 0   # this exact set already went red as a batch
  $G checkout -q -b fc-batch "$SG_FORK_BASE" || return 0
  for nn in $bl; do b2=$(brof $nn); files="$files $(bdiff HEAD "$b2")"   # in order; a branch that conflicts with an earlier one stays out and gets its conflict fix from the loop below
    if $G merge --no-ff -q -m "Merge $b2 (Refs #$nn)" "$b2" >/dev/null 2>&1; then merged="$merged $nn"; else $G merge --abort 2>/dev/null; $G reset -q --hard HEAD; fi; done
  if [ "$(wc -w <<<"$merged")" -lt 2 ]; then $G checkout -q "$SG_FORK_BASE"; $G branch -q -D fc-batch; return 0; fi
  suites=$(printf '%s\n' $files | bash "$here/gate-suites.sh"); first=$(awk '{print $1}' <<<"$merged"); e2eprep "batch"
  id=test-$first-$(date +%Y%m%d-%H%M%S)   # fixed before the gate starts (#1470)
  out=$(TEST_ID=$id FG_SUITES=$suites "$here/test.sh" "$first" -C "$SG_FORK_DIR" -- bash "$here/fork-gate.sh" "$SG_FORK_TEST_CMD"); rc=$?
  $G checkout -q "$SG_FORK_BASE"
  if [ $rc = 0 ] && $G merge --ff-only -q fc-batch >/dev/null 2>&1; then   # the tree that was tested is the new base
    echo "$SG_STATE/tests/$id.log" > "$A/basetest-$($G rev-parse --short=9 HEAD)"; $G branch -q -D fc-batch
    echo "FORKCLOSE BATCH landed ${merged# } ($suites suites, $id green); each issue closes below"
  else $G branch -q -D fc-batch; touch "$A/nobatch-$key"
    echo "FORKCLOSE BATCH RED (${merged# }; $id exit $rc): nothing landed, falling back to one branch at a time so the culprit is named"; fi; }
# Dispatch the fix request this pass just wrote. forkclose is the sole owner of the branch that request repairs (forkred-<n>-<sha>
# for a red gate, forkrebase-<n>-<sha> for a conflict), so the request is ABOUT that branch and must go past mkreq.sh's slice guard:
# the branch it has to fix, gui/<n>, is itself unmerged and touches exactly the files the fix edits, so the guard refuses it and
# nothing is ever launched (#1801). SG_SLICE_FORCE=1 is that guard's documented override (mkreq.sh) and it is set on this one call.
# A refusal is stopped work: it is recorded in cq/ naming the issue, and the caller must not print a "fix run" line for a run that
# was never launched. Returns non-zero on a refusal, having launched nothing.
dispatch() { local nm=$1 n=$2 kind=$3 mo="$SG_STATE/req/$nm.mkres" req rc
  req=$(SG_SLICE_FORCE=1 "$here/mkreq.sh" "$nm" "$SG_STATE/req/$nm.body" 2>"$mo"); rc=$?
  if [ $rc != 0 ]; then
    printf "Fork-only close: the %s fix request %s for issue #%s was refused by mkreq.sh and NO fix run was launched. The merge was reverted and the branch re-applied; the next pass retries the close. Nothing is waiting on a commit that cannot come.\n\`\`\`\n%s\n\`\`\`\n" "$kind" "$nm" "$n" "$(cat "$mo")" > "$SG_STATE/cq/$n-slicerefused$$.md"
    return $rc; fi
  (cd "$SG_CHECKOUT" && python3 -c 'import subprocess,sys;subprocess.Popen(["python3",sys.argv[1],sys.argv[2]],stdout=open(sys.argv[3],"w"),stderr=subprocess.STDOUT,start_new_session=True)' "$here/launch.py" "$req" "$SG_STATE/$nm.log"); }
ready=" $(jq -r '.items[]?|select((.st=="Code Complete" or .st=="Testing") and .s=="open")|.n' "${SG_HUB_DIR:-$SG_CHECKOUT/runs/.hub}/hub-board.json" 2>/dev/null | tr '\n' ' ') "
pat=${SG_FORK_BRANCH//%n/}; cc=$($G for-each-ref --format='%(refname:short)' "refs/heads/$pat*" | sed "s#^$pat##" | grep -E '^[0-9]+$')   # every fork branch, so a closed issue's fork half still lands
[ -n "$cc" ] || exit 0
refs=" $(gh api "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[]|(.title+" "+(.body//""))' 2>/dev/null | grep -oE 'Refs #[0-9]+' | grep -oE '[0-9]+' | tr '\n' ' ') "
[ -n "${FC_DRY:-}${FC_NOBATCH:-}${FC_COLLECT:-}" ] || [ "$(wc -w <<<"$cc")" -lt 2 ] || batchpass
for n in $cc; do
  case "$refs" in *" $n "*) continue;; esac   # an open MesenAI PR: its fork half waits for that merge (the fork suite runs against main's build)
  st=$(gh api repos/$SG_REPO/issues/$n -q .state 2>/dev/null) || continue
  br=${SG_FORK_BRANCH//%n/$n}; $G rev-parse -q --verify "refs/heads/$br" >/dev/null || continue
  mixed=""; if [ "$st" = closed ]; then mixed=1                       # closed: its PR half landed, merge the fork half without touching the issue
  else case "$ready" in *" $n "*) ;; *)
    orphan "$br" || continue   # the card is state THIS script owns (In Progress on a red, Done on a green): derived, never the gate
    echo "FORKCLOSE ORPHAN #$n: $br is ahead of $SG_FORK_BASE with nobody in its worktree and no commit for $(( ${SG_FORK_ORPHAN_MINUTES:-20} )) min; a fix run that is gone left the card behind. Re-arming."
    $BOARD $SG_OWNER $SG_PROJECT "Code Complete" $n >/dev/null;; esac; fi
  # 0 ahead: every commit of the branch is in the base already. If a file it touched is missing the base lost it to a sibling's
  # revert (reland). If nothing is missing it landed for real -- but the pass that merged it may have died before its close, and
  # then every later pass reads "landed" and skips the issue forever: the card sits at Code Complete with the work in the base.
  # A `Merge $br` commit in the base is the proof it was merged (and not a branch that was never anything), so the close is run
  # late here, with the fork suite as its evidence and without a second merge.
  reland=""; late=""; lf=""
  if [ "$($G rev-list --count "$SG_FORK_BASE..$br")" != 0 ]; then lf=$(lost "$br")   # ahead: a reverted ancestor's files are lost too, and the merge below must restore them
  else lf=$(lost "$br")
    if [ -n "$lf" ]; then reland=1; else late=$($G log --format=%h -1 --grep="Merge $br" "$SG_FORK_BASE" 2>/dev/null); [ -n "$late" ] || continue; fi; fi
  [ -n "$late" ] && [ "$st" = closed ] && continue   # landed and the issue is already closed: the close is not late, it happened
  wait=""; for d in $(gh api repos/$SG_REPO/issues/$n -q .body 2>/dev/null | awk '/^## Blocked by/{f=1;next}/^## /{f=0}f' | grep -oE '#[0-9]+' | tr -d '#'); do   # a blocker whose fork half is not in the base yet: merging this one now only goes red (it imports the blocker's files)
    bb=${SG_FORK_BRANCH//%n/$d}; $G rev-parse -q --verify "refs/heads/$bb" >/dev/null && { [ "$($G rev-list --count "$SG_FORK_BASE..$bb")" -gt 0 ] || [ -n "$(lost "$bb")" ]; } && wait="$wait #$d"; done
  [ -z "$wait" ] || { wm="$A/forkwait-$n${wait// /-}"; [ -e "$wm" ] || { touch "$wm"; echo "FORKCLOSE WAIT #$n: the fork half of$wait is not in $SG_FORK_BASE yet (Blocked by)"; }; continue; }
  head=$($G rev-parse "$br"); mk="$A/fork-$n-${head:0:9}"
  if [ -e "$mk" ]; then   # "wait for a real fix commit" is a wait with no deadline unless the marker expires on its own
    age=$(( now - $(stat -f %m "$mk") ))
    [ "$age" -lt $(( ${SG_FORK_MARKER_MINUTES:-45} * 60 )) ] && continue
    rm -f "$mk"; echo "FORKCLOSE RETRY #$n: the marker for ${head:0:9} waited $(( age / 60 )) min with no new commit on $br and no fix run alive; retrying"
  fi
  [ -n "${FC_COLLECT:-}" ] && { [ -z "$late$reland$mixed" ] && echo "$n" >> "$FC_COLLECT"; continue; }   # a plain open fork-only candidate
  # A fork-only fix has no PR body to carry its TDD RED output, so the child posts it on the issue ("TDD RED" first line, req-checklist-fork.txt). Missing: say so, once per issue; a warning only, the landing goes on (#1413).
  if ! gh api "repos/$SG_REPO/issues/$n/comments" --paginate -q '.[].body' 2>/dev/null | grep -q 'TDD RED'; then
    echo "FORKCLOSE WARN #$n: no TDD RED comment on the issue for $br (a fork-only fix records its failing test output there)"
    [ -n "${FC_DRY:-}" ] || [ -e "$A/redwarn-$n" ] || { touch "$A/redwarn-$n"; printf "Fork-only close: %s is landing but this issue has no \`TDD RED\` comment (test name and failure line, run against the base before the fix). A fork-only fix has no PR body to carry it; please post the RED and GREEN output here. The landing is not blocked.\n" "$br" > "$SG_STATE/cq/$n-redwarn$$.md"; }
  fi
  [ -n "${FC_DRY:-}" ] && { echo "FORKCLOSE would merge $br into $SG_FORK_BASE and test #$n"; continue; }
  needbase "#$n" || exit 0
  bs=$($G rev-parse --short=9 "$SG_FORK_BASE")
  basegate "#$n" || continue
  fs=full; [ -z "$late$reland" ] && fs=$(bdiff "$SG_FORK_BASE" "$br" | bash "$here/gate-suites.sh")   # suites by path; late/reland: doubt, full
  [ -z "$late" ] && touch "$mk"
  if [ -z "$late" ] && ! { if [ -n "$reland" ]; then relandbr; else mergebr; fi; }; then
    $G merge --abort 2>/dev/null
    files=$($G merge-tree --write-tree --name-only "$SG_FORK_BASE" "$br" 2>/dev/null | sed -n '2,/^$/p' | grep . | tr '\n' ' ')
    nm="forkrebase-$n-${head:0:9}"; body="$SG_STATE/req/$nm.body"
    { echo "FORK-ONLY: all"   # mkreq.sh: fork DONE CHECK and checklist, no PR text (#1411)
      echo "IMPORTANT: your first and only action is to call the spawn tool for the one child below; ending the turn without a spawn call fails the run. Spawn the child with spend 8 (the router may not request more: spend_outside_budget). One child, a CONFLICT FIX on the local fork branch $br for GitHub issue #$n (repo $SG_REPO). en-US; no Co-Authored-By and no \"Generated with Claude Code\" line."
      echo "In $SG_FORK_DIR the branch $br (head ${head:0:9}) no longer merges into $SG_FORK_BASE: conflict in $files. Work in a worktree: \`git -C $SG_FORK_DIR worktree add ~/wt-fr-$n $br\` (or reuse the existing worktree of $br), then \`git merge $SG_FORK_BASE\` into $br and resolve every conflict KEEPING BOTH SIDES (sibling issues appended their own cases; never drop one). Run \`$SG_FORK_TEST_CMD\` there until it passes, commit the merge on $br, NEVER push, keep the worktree. Do not change anything beyond the conflict resolution."
      echo "GitHub issue #$n: the issue is already implemented; only the conflict is in scope."; } > "$body"
    if ! dispatch "$nm" "$n" conflict; then
      echo "FORKCLOSE CONFLICT-REFUSED #$n $br into $SG_FORK_BASE ($files): mkreq.sh refused the fix request $nm; no fix run was launched, card left as it is"; continue; fi
    printf "Fork-only close: merging %s (%s) into %s conflicts (%s). A conflict-fix squad run (%s) merges the base into the branch keeping both sides; the loop retries the close when the branch head moves.\n" "$br" "${head:0:9}" "$SG_FORK_BASE" "$files" "$nm" > "$SG_STATE/cq/$n-forkconflict$$.md"
    echo "FORKCLOSE CONFLICT #$n $br into $SG_FORK_BASE ($files) -> fix run $nm"; continue; fi
  m=$([ -n "$late" ] && printf '%s' "$late" || $G rev-parse HEAD)
  if [ -n "$late" ] && [ -e "$A/basetest-$bs" ]; then   # the base that holds the branch is already tested green (a batch that just landed it, or the base test above): no second run
    rc=0; id=base-$bs; lg=$(cat "$A/basetest-$bs"); tl=$(tail -3 "${lg:-/dev/null}" 2>/dev/null)
  else e2eprep "#$n"
    id=test-$n-$(date +%Y%m%d-%H%M%S)   # fixed before the gate starts: a gate killed by a signal never prints it (#1470)
    out=$(TEST_ID=$id FG_SUITES=$fs "$here/test.sh" "$n" -C "$SG_FORK_DIR" -- bash "$here/fork-gate.sh" "$SG_FORK_TEST_CMD"); rc=$?; tl=$(tail -3 "$SG_STATE/tests/$id.log" 2>/dev/null)
    # the gate ran on the tree that is now the base head: record it as tested, so the next landing pays no second base test
    [ $rc = 0 ] && [ -z "$late" ] && [ "$($G rev-parse HEAD)" = "$m" ] && echo "$SG_STATE/tests/$id.log" > "$A/basetest-${m:0:9}"
  fi
  if [ $rc = 0 ] && [ -n "$mixed" ]; then
    printf "Fork half merged: %s at %s into %s as %s, fork suite green (test pass %s). The MesenAI half already landed through its PR.\n" "$br" "${head:0:9}" "$SG_FORK_BASE" "${m:0:9}" "$id" > "$SG_STATE/cq/$n-forkhalf$$.md"
    echo "FORKCLOSE #$n fork half merged ($br ${head:0:9} -> ${m:0:9}, $id green; PR still open)"
  elif [ $rc = 0 ]; then
    [ -n "$late" ] && landline="- $br was already in $SG_FORK_BASE as $late: the pass that merged it ended before its close step, and every later pass read the branch as landed and skipped it. The fork suite result below (a batch or base test that covers it) is the evidence that rule asks for." \
                   || landline="- $br at ${head:0:9} merged into $SG_FORK_BASE as ${m:0:9}."
    gh api -X POST repos/$SG_REPO/issues/$n/comments -F body=@- >/dev/null <<EOT
Done (fork-only work; owner-approved rule: merged into the fork's $SG_FORK_BASE and the fork suite green there).
$landline
- Test pass $id: \`$SG_FORK_TEST_CMD\` → exit 0.
\`\`\`
$tl
\`\`\`
No MesenAI file changed, so no PR.
EOT
    gh api -X PATCH repos/$SG_REPO/issues/$n -f state=closed -f state_reason=completed >/dev/null && $BOARD $SG_OWNER $SG_PROJECT Done $n >/dev/null
    [ -n "$late" ] && echo "FORKCLOSE #$n closed late ($br was already in $SG_FORK_BASE as $late; the close had been lost, $id green)" \
                   || echo "FORKCLOSE #$n closed ($br ${head:0:9} -> ${m:0:9}, $id green)"
  else
    [ -n "$late" ] && { printf "Fork-only close: %s was already in %s as %s and its issue #%s was still open, but the fork suite failed on the base itself (test pass %s, exit %s). Nothing of the branch was merged here and the branch has nothing to fix, so there is no revert and no fix run; the next pass retries.\n\`\`\`\n%s\n\`\`\`\n" "$br" "$SG_FORK_BASE" "$late" "$n" "$id" "$rc" "$tl" > "$SG_STATE/cq/$n-latered$$.md"
      echo "FORKCLOSE LATE-RED #$n ($id exit $rc): $br was already in $SG_FORK_BASE and the suite is red on the base itself; no revert, no fix run"; continue; }
    ek=""; { [ $rc = 143 ] || [ $rc = 137 ] || grep -q '"state": "running"' "$SG_STATE/tests/$id.json" 2>/dev/null; } && ek=1   # killed by a signal (or never finalized): the environment failed, not the code (#1470)
    $G revert -m 1 --no-edit "$m" >/dev/null 2>&1; rv=$($G rev-parse HEAD)
    # the branch is now an ancestor of the base, so a later merge would be a no-op: re-apply its work on the branch
    # (merge the base in, then revert the revert) so the branch is ahead again and a fix commit re-arms the close
    bw=$($G worktree list --porcelain | awk -v b="refs/heads/$br" '/^worktree /{w=$2} $0=="branch "b{print w}')
    [ -n "$bw" ] && /usr/bin/git -C "$bw" merge -q --no-edit "$SG_FORK_BASE" >/dev/null 2>&1 && /usr/bin/git -C "$bw" revert --no-edit "$rv" >/dev/null 2>&1 \
      && { [ -n "$ek" ] || touch "$A/fork-$n-$(/usr/bin/git -C "$bw" rev-parse HEAD | cut -c1-9)"; }   # same content that just failed: wait for a real fix commit (a killed gate is retried instead)
    if [ -n "$ek" ]; then   # no fix run: there is nothing to fix. One retry on the next cycle; a second kill on the same head goes to the owner
      rm -f "$mk"; nh=$($G rev-parse "$br"); kf="$A/envkill-$n"; kc=0; [ "$(cut -d' ' -f2 "$kf" 2>/dev/null)" = "$nh" ] && kc=$(cut -d' ' -f1 "$kf"); kc=$(( kc + 1 )); echo "$kc $nh" > "$kf"
      if [ "$kc" -ge 2 ]; then touch "$A/fork-$n-${nh:0:9}"   # parked until the head moves or the marker expires
        printf "Fork-only close: the fork gate for %s was killed by a signal %s times in a row on the same head (test pass %s, exit %s, log %s/tests/%s.log). That is the environment, not the code, so no fix run was dispatched. Owner: check what kills the gate (a reaper, memory pressure, a stray pkill); the close resumes when the branch head moves.\n" "$br" "$kc" "$id" "$rc" "$SG_STATE" "$id" > "$SG_STATE/cq/$n-envkill$$.md"
        echo "FORKCLOSE NEEDS OWNER #$n ($id, exit $rc, log $SG_STATE/tests/$id.log): the gate was killed $kc times in a row on ${nh:0:9}; no fix run"
      else echo "FORKCLOSE ENV-KILL #$n ($id, exit $rc, log $SG_STATE/tests/$id.log), merge reverted, branch re-applied; the gate is retried once on the next cycle, no fix run"; fi
      continue; fi
    nm="forkred-$n-${head:0:9}"; body="$SG_STATE/req/$nm.body"   # a fix run makes that commit
    { echo "FORK-ONLY: all"   # mkreq.sh: fork DONE CHECK and checklist, no PR text (#1411)
      echo "IMPORTANT: your first and only action is to call the spawn tool for the one child below; ending the turn without a spawn call fails the run. Spawn the child with spend 8 (the router may not request more: spend_outside_budget). One child, a FIX on the local fork branch $br for GitHub issue #$n (repo $SG_REPO). Spawn it with spend 8. en-US; no Co-Authored-By and no \"Generated with Claude Code\" line."
      echo "In $SG_FORK_DIR the fork suite failed after merging $br into $SG_FORK_BASE (test pass $id, exit $rc); the merge was reverted and the branch re-applied. Work in the existing worktree of $br (${bw:-git -C $SG_FORK_DIR worktree add ~/wt-fr-$n $br}). Run \`$SG_FORK_TEST_CMD\` there, fix the root cause (never weaken or skip a test; a test that needs a missing environment variable is fixed in the environment only if the variable is documented, else report it), commit on $br, NEVER push, keep the worktree. Log tail:"
      tail -40 "$SG_STATE/tests/$id.log"; echo "GitHub issue #$n: only the failing suite is in scope."; } > "$body"
    if dispatch "$nm" "$n" red; then
      [ -n "$mixed" ] || $BOARD $SG_OWNER $SG_PROJECT "In Progress" $n >/dev/null
      printf "Fork-only close: the fork suite failed after merging %s (%s) as %s (test pass %s, exit %s); the merge was reverted on %s. Card back to In Progress.\n\`\`\`\n%s\n\`\`\`\n" "$br" "${head:0:9}" "${m:0:9}" "$id" "$rc" "$SG_FORK_BASE" "$tl" > "$SG_STATE/cq/$n-forkred$$.md"
      echo "FORKCLOSE RED #$n ($id exit $rc, log $SG_STATE/tests/$id.log), merge reverted, branch re-applied, fix run $nm"
    else   # a refused request launched nothing: say so, and keep no park marker (nothing is coming to move that head)
      nh2=$($G rev-parse -q --verify "$br" 2>/dev/null); rm -f "$mk" "$A/fork-$n-${nh2:0:9}"
      echo "FORKCLOSE RED-REFUSED #$n ($id exit $rc, log $SG_STATE/tests/$id.log), merge reverted, branch re-applied: mkreq.sh refused the fix request $nm, so no fix run was launched (cq/$n-slicerefused$$.md)"
    fi
  fi
done
