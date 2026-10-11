#!/bin/bash
# usage: forkclose.sh   (watch.sh, every loop; FC_DRY=1 prints the candidates)
# Fork work merges itself: an issue that also has a MesenAI PR waits for it, then gets its fork half merged with no close.
# Fork-only work closes itself: an open issue whose card is Code Complete, with no open PR naming it and a fork branch
# ($SG_FORK_BRANCH, %n = issue) ahead of $SG_FORK_BASE in $SG_FORK_DIR, is merged into the base (--no-ff), the card goes to
# Testing and the fork suite ($SG_FORK_TEST_CMD) runs through test.sh. Green: the issue closes with the evidence and the
# card goes Done. Red: the merge is reverted (git revert -m 1, never a reset) and the card goes back to In Progress. A merge
# conflict aborts and launches a conflict-fix squad run on the branch (base merged in, both sides kept); its new head re-arms the close. One issue at a time (lock); the fork checkout must be clean and on the base branch.
# An issue whose `## Blocked by` names an issue with an unlanded fork branch waits for it (FORKCLOSE WAIT, once per set): its tests import the blocker's files.
# Owner-approved Done rule for fork-only issues (#1180, 2026-10-10). Unset SG_FORK_DIR turns it off.
. "$(dirname "$0")/env.sh"; here="$(cd "$(dirname "$0")" && pwd)"; A="$SG_STATE/auto"; mkdir -p "$A"
[ -n "${SG_FORK_DIR:-}" ] && [ -d "$SG_FORK_DIR" ] || exit 0; [ -e "$A/off" ] && exit 0
L="$A/forkclose.lock"   # a test pass is still running; a lock whose pid is gone (loop restarted mid-pass) is stale
if ! mkdir "$L" 2>/dev/null; then kill -0 "$(cat "$L/pid" 2>/dev/null)" 2>/dev/null && exit 0; rm -rf "$L"; mkdir "$L" || exit 0; fi
echo $$ > "$L/pid"
trap 'rm -rf "$L"' EXIT
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
lost() { $G diff --name-only --diff-filter=A "$SG_FORK_BASE" "$1" 2>/dev/null; }   # files the branch has that the base lacks: a revert of a merge of the branch (or a sibling's merge that carried it) took them, whatever commit added them
mergebr() { if $G merge --no-ff --no-commit -q "$br" >/dev/null 2>&1; then :; else [ -n "$lf" ] || return 1; fi   # ahead of the base AND files lost to a revert: the merge only brings the commits after the revert, so a modify/delete conflict on a lost file is expected and the branch's version of every lost file wins
  if [ -n "$lf" ]; then $G checkout -q "$br" -- $lf || return 1; [ -z "$($G diff --name-only --diff-filter=U)" ] || return 1; fi
  $G commit -q --no-edit -m "Merge $br (Refs #$n)" >/dev/null 2>&1 || [ -z "$($G rev-parse -q --verify MERGE_HEAD)" ]; }
relandbr() { $G checkout -q "$br" -- $lf || { $G reset -q --hard HEAD; return 1; }   # the branch is an ancestor already (merging is a no-op): restore the lost files in a merge commit so the close and the revert (-m 1) work as usual
  t=$($G write-tree) && c=$($G commit-tree "$t" -p HEAD -p "$br" -m "Merge $br (Refs #$n)") && $G merge --ff-only -q "$c" >/dev/null 2>&1 || { $G reset -q --hard HEAD; return 1; }; }
ready=" $(jq -r '.items[]?|select((.st=="Code Complete" or .st=="Testing") and .s=="open")|.n' "${SG_HUB_DIR:-$SG_CHECKOUT/runs/.hub}/hub-board.json" 2>/dev/null | tr '\n' ' ') "
pat=${SG_FORK_BRANCH//%n/}; cc=$($G for-each-ref --format='%(refname:short)' "refs/heads/$pat*" | sed "s#^$pat##" | grep -E '^[0-9]+$')   # every fork branch, so a closed issue's fork half still lands
[ -n "$cc" ] || exit 0
refs=" $(gh api "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[]|(.title+" "+(.body//""))' 2>/dev/null | grep -oE 'Refs #[0-9]+' | grep -oE '[0-9]+' | tr '\n' ' ') "
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
  [ -n "${FC_DRY:-}" ] && { echo "FORKCLOSE would merge $br into $SG_FORK_BASE and test #$n"; continue; }
  if [ "$($G branch --show-current)" != "$SG_FORK_BASE" ] || [ -n "$($G status --porcelain --untracked-files=no)" ]; then echo "FORKCLOSE WAIT #$n: $SG_FORK_DIR is not a clean $SG_FORK_BASE checkout"; exit 0; fi
  bs=$($G rev-parse --short=9 "$SG_FORK_BASE")
  if [ -e "$A/basered-$bs" ]; then
    # A park nobody hears about is a silent stall (#1407): say so once per base sha, naming the failing test and who waits.
    if [ ! -e "$A/basered-$bs.escalated" ]; then touch "$A/basered-$bs.escalated"
      ft=$(grep -E 'FAIL|not ok' "$SG_STATE/basetest-$bs.log" 2>/dev/null | grep -v 'fork gate' | head -3)
      wb=""; for w in $cc; do [ "$($G rev-list --count "$SG_FORK_BASE..${SG_FORK_BRANCH//%n/$w}" 2>/dev/null)" -gt 0 ] && wb="$wb ${SG_FORK_BRANCH//%n/$w}"; done
      mkdir -p "$SG_STATE/cq"; printf "Fork base is red: %s %s fails the fork suite on its own, so every branch waiting to land is parked:%s.\nFailing test:\n\`\`\`\n%s\n\`\`\`\nLog: %s/basetest-%s.log. After %s min the base suite is retried once on its own; a green retry lifts the park, a second red keeps it.\n" "$SG_FORK_BASE" "$bs" "${wb:- $br}" "${ft:-(no FAIL line in the log)}" "$SG_STATE" "$bs" "${SG_FORK_BASERED_BUDGET_MINUTES:-30}" > "$SG_STATE/cq/$n-basered$$.md"
    fi
    # Past the budget, one solo retry (this pass holds the lock and the e2e rebuild comes later, so no rebuild is in flight): a red that came from a rebuild window must not park the branches forever.
    if [ ! -e "$A/basered-$bs.retried" ] && [ $(( now - $(stat -f %m "$A/basered-$bs") )) -ge $(( ${SG_FORK_BASERED_BUDGET_MINUTES:-30} * 60 )) ]; then touch "$A/basered-$bs.retried"
      if ( cd "$SG_FORK_DIR" && bash "$here/fork-gate.sh" "$SG_FORK_TEST_CMD" ) > "$SG_STATE/basetest-$bs.log" 2>&1; then rm -f "$A/basered-$bs" "$A/basered-$bs.tries"; touch "$A/basetest-$bs"; echo "FORKCLOSE BASE-GREEN #$n: $SG_FORK_BASE $bs passed the solo retry after the park; lifting it"
      else echo "FORKCLOSE BASE-RED #$n: the solo retry of $SG_FORK_BASE $bs failed too (see $SG_STATE/basetest-$bs.log); the park stays"; continue; fi
    else echo "FORKCLOSE BASE-RED #$n: $SG_FORK_BASE $bs fails the fork suite on its own, so a red run here would not be $br's fault; waiting for a green base"; continue; fi
  fi
  if [ ! -e "$A/basetest-$bs" ]; then   # a red base reds every branch at once: test it once per base head, before merging anything
    if ( cd "$SG_FORK_DIR" && bash "$here/fork-gate.sh" "$SG_FORK_TEST_CMD" ) > "$SG_STATE/basetest-$bs.log" 2>&1; then touch "$A/basetest-$bs"; rm -f "$A/basered-$bs.tries"
    else   # one sample is not proof: the full suite flakes on the real emulator (a hold step loses the window to a suite running in
           # parallel), and a single red would park every branch on a flake. Two consecutive reds is the base, one is a retry.
      bt=$(( $(cat "$A/basered-$bs.tries" 2>/dev/null || echo 0) + 1 )); echo "$bt" > "$A/basered-$bs.tries"
      if [ "$bt" -ge "${SG_FORK_BASERED_TRIES:-2}" ]; then touch "$A/basered-$bs"; echo "FORKCLOSE BASE-RED #$n: $SG_FORK_BASE $bs fails the fork suite on its own $bt runs in a row (see $SG_STATE/basetest-$bs.log); not merging $br, no revert, no fix run. Every branch is parked until the base is green."
      else echo "FORKCLOSE BASE-FLAKE #$n: $SG_FORK_BASE $bs failed the fork suite on its own (run $bt of ${SG_FORK_BASERED_TRIES:-2}); one red is not proof, retrying next cycle."; fi
      continue; fi
  fi
  [ -z "$late" ] && touch "$mk"
  if [ -z "$late" ] && ! { if [ -n "$reland" ]; then relandbr; else mergebr; fi; }; then
    $G merge --abort 2>/dev/null
    files=$($G merge-tree --write-tree --name-only "$SG_FORK_BASE" "$br" 2>/dev/null | sed -n '2,/^$/p' | grep . | tr '\n' ' ')
    nm="forkrebase-$n-${head:0:9}"; body="$SG_STATE/req/$nm.body"
    { echo "IMPORTANT: your first and only action is to call the spawn tool for the one child below; ending the turn without a spawn call fails the run. Spawn the child with spend 8 (the router may not request more: spend_outside_budget). One child, a CONFLICT FIX on the local fork branch $br for GitHub issue #$n (repo $SG_REPO). en-US; no Co-Authored-By and no \"Generated with Claude Code\" line."
      echo "In $SG_FORK_DIR the branch $br (head ${head:0:9}) no longer merges into $SG_FORK_BASE: conflict in $files. Work in a worktree: \`git -C $SG_FORK_DIR worktree add ~/wt-fr-$n $br\` (or reuse the existing worktree of $br), then \`git merge $SG_FORK_BASE\` into $br and resolve every conflict KEEPING BOTH SIDES (sibling issues appended their own cases; never drop one). Run \`$SG_FORK_TEST_CMD\` there until it passes, commit the merge on $br, NEVER push, keep the worktree. Do not change anything beyond the conflict resolution."
      echo "GitHub issue #$n: the issue is already implemented; only the conflict is in scope."; } > "$body"
    req=$("$here/mkreq.sh" "$nm" "$body") && (cd "$SG_CHECKOUT" && python3 -c 'import subprocess,sys;subprocess.Popen(["python3",sys.argv[1],sys.argv[2]],stdout=open(sys.argv[3],"w"),stderr=subprocess.STDOUT,start_new_session=True)' "$here/launch.py" "$req" "$SG_STATE/$nm.log")
    printf "Fork-only close: merging %s (%s) into %s conflicts (%s). A conflict-fix squad run (%s) merges the base into the branch keeping both sides; the loop retries the close when the branch head moves.\n" "$br" "${head:0:9}" "$SG_FORK_BASE" "$files" "$nm" > "$SG_STATE/cq/$n-forkconflict$$.md"
    echo "FORKCLOSE CONFLICT #$n $br into $SG_FORK_BASE ($files) -> fix run $nm"; continue; fi
  m=$([ -n "$late" ] && printf '%s' "$late" || $G rev-parse HEAD)
  [ -n "${SG_E2E_DIR:-}" ] && [ -d "$SG_E2E_DIR" ] && [ -z "$(/usr/bin/git -C "$SG_E2E_DIR" status --porcelain --untracked-files=no)" ] \
    && /usr/bin/git -C "$SG_E2E_DIR" fetch -q origin && /usr/bin/git -C "$SG_E2E_DIR" checkout -q --detach "origin/$SG_BASE"
  if [ -n "${SG_E2E_BUILD_DIR:-}" ] && [ -d "$SG_E2E_BUILD_DIR" ]; then   # the built emulator the real-app cases drive: rebuild at the base's head (incremental) under the gui-suite lock: the GUI suite never launches a build that is mid-publish (#1410)
    /usr/bin/git -C "$SG_E2E_BUILD_DIR" fetch -q origin && /usr/bin/git -C "$SG_E2E_BUILD_DIR" checkout -q --detach "origin/$SG_BASE" \
      && bash "$here/serial.sh" gui-suite bash -c 'cd "$1" && eval "$2"' _ "$SG_E2E_BUILD_DIR" "${SG_E2E_BUILD_CMD:-make core && make ui}" > "$SG_STATE/e2e-build.log" 2>&1 \
      || echo "FORKCLOSE WARN #$n: rebuilding $SG_E2E_BUILD_DIR failed (see e2e-build.log)"
  fi
  out=$("$here/test.sh" "$n" -C "$SG_FORK_DIR" -- bash "$here/fork-gate.sh" "$SG_FORK_TEST_CMD"); rc=$?; id=$(awk '{print $NF}' <<<"$out")
  tl=$(tail -3 "$SG_STATE/tests/$id.log" 2>/dev/null)
  if [ $rc = 0 ] && [ -n "$mixed" ]; then
    printf "Fork half merged: %s at %s into %s as %s, fork suite green (test pass %s). The MesenAI half already landed through its PR.\n" "$br" "${head:0:9}" "$SG_FORK_BASE" "${m:0:9}" "$id" > "$SG_STATE/cq/$n-forkhalf$$.md"
    echo "FORKCLOSE #$n fork half merged ($br ${head:0:9} -> ${m:0:9}, $id green; PR still open)"
  elif [ $rc = 0 ]; then
    [ -n "$late" ] && landline="- $br was already in $SG_FORK_BASE as $late: the pass that merged it ended before its close step, and every later pass read the branch as landed and skipped it. The fork suite is re-run here as the evidence that rule asks for." \
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
    $G revert -m 1 --no-edit "$m" >/dev/null 2>&1; rv=$($G rev-parse HEAD)
    # the branch is now an ancestor of the base, so a later merge would be a no-op: re-apply its work on the branch
    # (merge the base in, then revert the revert) so the branch is ahead again and a fix commit re-arms the close
    bw=$($G worktree list --porcelain | awk -v b="refs/heads/$br" '/^worktree /{w=$2} $0=="branch "b{print w}')
    [ -n "$bw" ] && /usr/bin/git -C "$bw" merge -q --no-edit "$SG_FORK_BASE" >/dev/null 2>&1 && /usr/bin/git -C "$bw" revert --no-edit "$rv" >/dev/null 2>&1 \
      && touch "$A/fork-$n-$(/usr/bin/git -C "$bw" rev-parse HEAD | cut -c1-9)"   # same content that just failed: wait for a real fix commit
    nm="forkred-$n-${head:0:9}"; body="$SG_STATE/req/$nm.body"   # a fix run makes that commit
    { echo "IMPORTANT: your first and only action is to call the spawn tool for the one child below; ending the turn without a spawn call fails the run. Spawn the child with spend 8 (the router may not request more: spend_outside_budget). One child, a FIX on the local fork branch $br for GitHub issue #$n (repo $SG_REPO). Spawn it with spend 8. en-US; no Co-Authored-By and no \"Generated with Claude Code\" line."
      echo "In $SG_FORK_DIR the fork suite failed after merging $br into $SG_FORK_BASE (test pass $id, exit $rc); the merge was reverted and the branch re-applied. Work in the existing worktree of $br (${bw:-git -C $SG_FORK_DIR worktree add ~/wt-fr-$n $br}). Run \`$SG_FORK_TEST_CMD\` there, fix the root cause (never weaken or skip a test; a test that needs a missing environment variable is fixed in the environment only if the variable is documented, else report it), commit on $br, NEVER push, keep the worktree. Log tail:"
      tail -40 "$SG_STATE/tests/$id.log"; echo "GitHub issue #$n: only the failing suite is in scope."; } > "$body"
    req=$("$here/mkreq.sh" "$nm" "$body") && (cd "$SG_CHECKOUT" && python3 -c 'import subprocess,sys;subprocess.Popen(["python3",sys.argv[1],sys.argv[2]],stdout=open(sys.argv[3],"w"),stderr=subprocess.STDOUT,start_new_session=True)' "$here/launch.py" "$req" "$SG_STATE/$nm.log")
    [ -n "$mixed" ] || $BOARD $SG_OWNER $SG_PROJECT "In Progress" $n >/dev/null
    printf "Fork-only close: the fork suite failed after merging %s (%s) as %s (test pass %s, exit %s); the merge was reverted on %s. Card back to In Progress.\n\`\`\`\n%s\n\`\`\`\n" "$br" "${head:0:9}" "${m:0:9}" "$id" "$rc" "$SG_FORK_BASE" "$tl" > "$SG_STATE/cq/$n-forkred$$.md"
    echo "FORKCLOSE RED #$n ($id exit $rc), merge reverted, branch re-applied, fix run $nm"
  fi
done
