#!/bin/bash
# usage: forkclose.sh   (watch.sh, every loop; FC_DRY=1 prints the candidates)
# Fork work merges itself: an issue that also has a MesenAI PR waits for it, then gets its fork half merged with no close.
# Fork-only work closes itself: an open issue whose card is Code Complete, with no open PR naming it and a fork branch
# ($SG_FORK_BRANCH, %n = issue) ahead of $SG_FORK_BASE in $SG_FORK_DIR, is merged into the base (--no-ff), the card goes to
# Testing and the fork suite ($SG_FORK_TEST_CMD) runs through test.sh. Green: the issue closes with the evidence and the
# card goes Done. Red: the merge is reverted (git revert -m 1, never a reset) and the card goes back to In Progress. A merge
# conflict aborts and launches a conflict-fix squad run on the branch (base merged in, both sides kept); its new head re-arms the close. One issue at a time (lock); a fork checkout a child left on its own branch is put back on the base by the pass itself while the tree is clean and no live run names that branch (WAIT, naming the reason, when it is not).
# A branch whose own worktree still holds uncommitted changes is not the run's final state: the pass refuses (FORKCLOSE UNLANDED, naming the branch) instead of merging a mid-work tip and closing the issue with the code absent from the base (#1821).
# An issue whose `## Blocked by` names an issue with an unlanded fork branch waits for it (FORKCLOSE WAIT, once per set): its tests import the blocker's files.
# Cheaper queue (#1409): a green merge is recorded as a tested base (basetest-<new sha>), so the next landing pays no second base test; the suites a
# branch's diff cannot affect are skipped (gate-suites.sh -> FG_SUITES, anything unknown runs the full gate). The base gate asks the
# same question about the base's OWN diff since the last base recorded green, from the same table (#1844), and the park and its solo
# retry reuse that answer: a base that only touched contrib/squad-goal/ is not retested with the GUI suite and the pytest suite.
# When several branches are code
# complete they merge, in order, onto one candidate branch (fc-batch, the base is untouched) that the gate runs once: green fast-forwards the base
# and each issue then closes through the late-close path below with that run as its evidence; red drops the candidate and the loop lands them one
# at a time, so a culprit is still named. A branch that conflicts with an earlier one stays out of the batch and gets its conflict-fix run as usual.
# Owner-approved Done rule for fork-only issues (#1180, 2026-10-10). Unset SG_FORK_DIR turns it off.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/forkonly.sh"; here="$(cd "$(dirname "$0")" && pwd)"; A="$SG_STATE/auto"; mkdir -p "$A"
# The collection's reads go through the loop's one door (ghcall.sh, #5, #1893, ADR-0277), and the reader behind it is the
# throttled one (#4): this is the loop's heaviest reader and the pass the bucket is sized for, so every route goes to
# ghthrottle.py, which spends a token from the bucket every process of the loop shares, answers a refusal without touching
# the network while a cooldown holds, and delegates the route itself to ghread.py -- an aggregate route stays one GraphQL
# request, and a cooldown held by another reader holds here too. The projection stays here -- `gh_api <route> -q <jq>`.
# The outcome is read from the module's exit code, never from the body: `gh api` writes the error body to stdout and
# exits non-zero, and it does not honour -q on a failed request, so a refusal used to arrive here as data -- and a
# quota-exhausted pass used to read as "no eligible branches", silently. The two lists are `gh_aggregate`: the snapshot
# answers a repeat inside its window, and the pass reads each list once for ALL its candidates (#45).
SG_GHREAD="python3 $here/ghthrottle.py"; . "$here/ghcall.sh"
# The repository's open issues, labels and blocking edges together: ONE request per pass (#5), read here in the parent
# shell -- a read inside `$( )` is a subshell, and its cache would not survive the candidate it was made for. This is the
# fan-out the pass used to pay per branch: a state read and a body read for every candidate.
oissues=""; oissues_ok=""; ocomplete=""
# The one issue's own facts -- its state and its blocking edges -- from the aggregate answer when it carries the issue,
# else from that one issue's read through the same reader: a candidate the answer does not carry is not an open issue of
# this repository (closed, or an issue the query did not cover), and that is one read, not one per fact. Prints the
# issue's object; prints the read's own line and fails when nothing answered.
issue_object() { local n=$1 a
  if [ -n "$oissues_ok" ]; then
    a=$(printf '%s' "$oissues" | jq -c --argjson n "$n" '.[]?|select(.number==$n)' 2>/dev/null)
    [ -n "$a" ] && { printf '%s' "$a"; return 0; }
    [ -n "$ocomplete" ] && return 1   # both lists answered and neither names it: there is no such issue, and that is not worth a read each
  fi
  a=$(gh_api "repos/$SG_REPO/issues/$n") || { printf '%s' "$a"; return 1; }
  printf '%s' "$a"; }
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
# The fork checkout is shallow, so a commit can sit here while its parent never arrived. Merging a branch that carries such a
# commit drags it into $SG_FORK_BASE and the push of the base dies in index-pack ("remote: fatal: did not receive expected
# object <parent>"), which is #1823: the tip is produced, it is unpushable, and nothing says so. Prints "<commit> <parent>"
# for the first such commit reachable in the range $1, empty when there is none; no fetch on this machine can supply the
# parent, so the branch has to be rebuilt on the base instead of landed.
shallowrange() { local c p
  for c in $($G rev-list "$1" 2>/dev/null); do
    # The parents come from the commit HEADER, never from a `sed` over the whole object: `cat-file -p` prints the message
    # verbatim after the headers, so a body line beginning with "parent " was collected as a parent hash, `cat-file -e` failed
    # on the words, and the branch was parked forever as a shallow boundary (#1852). `awk` stops at the first blank line --
    # the end of the headers -- so only real `parent` fields are read, whatever the message says.
    # NOT `rev-list --parents`: it is the obvious form and it is wrong here. `rev-list` honours `.git/shallow` and prints a
    # boundary commit with NO parents, so it reports an empty parent list for exactly the commits this function exists to
    # find -- the guard would never fire. `cat-file -p` reads the stored object, which still names the parent it does not have.
    for p in $($G cat-file -p "$c" 2>/dev/null | awk '/^$/{exit} /^parent /{print $2}'); do
      case $p in *[!0-9a-f]*|"") continue;; esac   # a field that is not a hash is not a parent
      [ ${#p} = 40 ] || continue
      $G cat-file -e "$p" 2>/dev/null || { echo "$c $p"; return 0; }; done; done
  return 1; }
# The run's final state is what the child left in the worktree it worked in, and a pass can only land what a commit carries. Prints
# the path of the worktree holding $1 with uncommitted (tracked) changes; empty = the tip is all there is, so the merge is the whole
# run. Untracked files are not work in progress here: a gate tree is full of them and one would park a finished branch forever.
# The shared checkout counts too -- a child that worked there instead of its own ~/wt-as-<n> has the same unfinished tip (#1798).
dirtywt() { local w
  w=$($G worktree list --porcelain | awk -v x="refs/heads/$1" '/^worktree /{p=$2} $0=="branch "x{print p}')
  [ -n "$w" ] || return 1
  [ -n "$(/usr/bin/git -C "$w" status --porcelain --untracked-files=no 2>/dev/null)" ] || return 1
  echo "$w"; }
mergebr() { if $G merge --no-ff --no-commit -q "$br" >/dev/null 2>&1; then :; else [ -n "$lf" ] || return 1; fi   # ahead of the base AND files lost to a revert: the merge only brings the commits after the revert, so a modify/delete conflict on a lost file is expected and the branch's version of every lost file wins
  if [ -n "$lf" ]; then $G checkout -q "$br" -- $lf || return 1; [ -z "$($G diff --name-only --diff-filter=U)" ] || return 1; fi
  $G commit -q --no-edit -m "Merge $br (Refs #$n)" >/dev/null 2>&1 || [ -z "$($G rev-parse -q --verify MERGE_HEAD)" ] || return 1
  auditmerge HEAD || { $G reset -q --hard HEAD^ 2>/dev/null; return 1; }; }   # (#14) the resolution this pass just made is audited before it becomes the base: a line both parents hold that the resolution dropped is refused, and the base is put back
relandbr() { $G checkout -q "$br" -- $lf || { $G reset -q --hard HEAD; return 1; }   # the branch is an ancestor already (merging is a no-op): restore the lost files in a merge commit so the close and the revert (-m 1) work as usual
  t=$($G write-tree) && c=$($G commit-tree "$t" -p HEAD -p "$br" -m "Merge $br (Refs #$n)") || { $G reset -q --hard HEAD; return 1; }
  auditmerge "$c" || { $G reset -q --hard HEAD; return 1; }   # (#14) the same audit before the commit is moved onto the branch
  $G merge --ff-only -q "$c" >/dev/null 2>&1 || { $G reset -q --hard HEAD; return 1; }; }
brof() { sg_fork_branch "$1"; }   # the land and the request name the branch from one setting (#1836)
# (#14) A merge is a resolution, and a resolution can drop a line both parents hold without anything noticing: the file still
# parses, `bash -n` is happy, and the symptom arrives hours later in a tree nobody wrote (#9's evidence: a deleted `watch.sh`
# statement took two hours to surface as two red suites). merge-audit.sh is the rule; these two run it.
# auditmerge <merge-commit>: names every line both parents hold that the commit's tree dropped. Prints nothing when clean.
auditmerge() { local c=$1 f o r=0
  [ -n "$c" ] || return 0
  for f in $($G diff --name-only "$c^1" "$c^2" 2>/dev/null); do
    o=$(cd "$SG_FORK_DIR" && bash "$here/merge-audit.sh" "$f" "$c" 2>&1) || case $? in 1) r=1; printf '%s\n' "$o" >&2;; esac; done
  return $r; }
# auditbranch: every merge the BRANCH carries, which is a conflict a child resolved in its own worktree and no pass ever saw.
# Prints the fabrication lines (or nothing) and returns 1 on the first merge commit that dropped a shared line.
auditbranch() { local c f o r=0
  for c in $($G rev-list --merges "$SG_FORK_BASE..$br" 2>/dev/null); do
    for f in $($G diff --name-only "$c^1" "$c^2" 2>/dev/null); do
      o=$(cd "$SG_FORK_DIR" && bash "$here/merge-audit.sh" "$f" "$c" 2>&1) || case $? in 1) r=1;; esac
      [ $r = 0 ] || { printf '%s\n' "$o" >&2; printf 'FORKCLOSE FABRICATION #%s: the merge %s on %s dropped a line both of its parents hold (%s)\n' "$n" "$($G rev-parse --short=9 "$c")" "$br" "$f" >&2; return 1; }; done; done
  return 0; }
# #11: a candidate lands on the base as it is NOW, not on the one it forked from. The evidence (2026-10-12): ten gui/* branches
# landed by hand -- 9 minutes to merge, 53 minutes to make the merged tree green (13 files, +148/-87). The three deepest branches
# (87, 119 and 122 commits behind) produced most of the 18 conflict hunks and every post-merge failure; replaying those three tips
# onto the base they were merged into (same commits, same bases) left 1 hunk to resolve against 15. So a tip that is behind the base
# is replayed before its gate runs, the stale merge is refused by name, and a replay that cannot be resolved falls back to the merge
# path saying which way it went. The branch's OWN worktree is where the child is, so the replay happens there and the child sees it;
# a worktree with uncommitted changes is never rewritten (a live child owns those files) and the refusal is by name.
# Returns 0 = the branch was replayed onto the current base; 1 = the replay conflicts, or it would DROP a file the branch carries
# (the merge path takes it); 2 = refused. The drop check is what keeps the replay from undoing the other half of a landing: a
# branch that carries a file the base lost to a sibling's revert inherited it from the base it forked from, so a replay -- which
# keeps the base's tree and re-applies only the branch's own commits -- would silently drop the very file the merge restores (#15).
rebasestale() { local w t nh old dr
  old=$($G rev-parse "$br")
  w=$($G worktree list --porcelain | awk -v x="refs/heads/$br" '/^worktree /{p=$2} $0=="branch "x{print p}')
  if [ -n "$w" ]; then
    [ -z "$(/usr/bin/git -C "$w" status --porcelain --untracked-files=no 2>/dev/null)" ] || return 2
    if /usr/bin/git -C "$w" rebase "$SG_FORK_BASE" >/dev/null 2>&1; then
      dr=$($G diff --name-only --diff-filter=D "$old" "$br" 2>/dev/null)
      [ -z "$dr" ] || { /usr/bin/git -C "$w" reset -q --hard "$old" >/dev/null 2>&1; return 1; }
      return 0; fi
    /usr/bin/git -C "$w" rebase --abort >/dev/null 2>&1; return 1; fi
  t="$(mktemp -d)"; rmdir "$t" 2>/dev/null   # no worktree of its own: replay the tip in a throwaway detached one and move the branch onto the result
  $G worktree add -q --detach "$t" "$br" >/dev/null 2>&1 || { rm -rf "$t"; return 1; }
  if /usr/bin/git -C "$t" rebase "$SG_FORK_BASE" >/dev/null 2>&1; then nh=$(/usr/bin/git -C "$t" rev-parse HEAD)
    dr=$($G diff --name-only --diff-filter=D "$old" "$nh" 2>/dev/null)
    $G worktree remove --force "$t" >/dev/null 2>&1; rm -rf "$t"
    [ -z "$dr" ] || return 1   # the branch was not moved yet: the merge path takes it from the tip it still has
    $G branch -f "$br" "$nh" >/dev/null 2>&1; return 0; fi
  /usr/bin/git -C "$t" rebase --abort >/dev/null 2>&1; $G worktree remove --force "$t" >/dev/null 2>&1; rm -rf "$t"; return 1; }
# The base gate asks the candidate gate's question -- which suites can this base's own diff affect -- and answers it from the one
# table (gate-suites.sh), over the base's diff since the last base recorded green ($A/basetest-<sha>, written by every green merge,
# batch and base test). Every landing commits to the base, so a base whose own diff only touches contrib/squad-goal/ was paying the
# GUI suite and the pytest suite again for nothing (#1844). No anchor (no base has been recorded green yet), a path the table does
# not know, two suites in the diff, or no diff at all: the full gate, exactly as before -- gate-suites.sh's own any-doubt rule, with
# no second copy of it here.
suiteset() { if [ -f "$here/gate-suites.sh" ]; then bash "$here/gate-suites.sh"; else cat >/dev/null; echo full; fi; }   # the one table's answer for the file names on stdin; a tree without the table is any doubt: the full gate
basesuites() { local a c
  a=$(for f in "$A"/basetest-*; do s=${f##*/basetest-}   # the marker holds the log path; its name is the base that was tested (a short sha, so nothing but hex survives this)
      [ -n "$s" ] && [ -z "${s//[0-9a-f]/}" ] || continue
      $G merge-base --is-ancestor "$s" "$1" 2>/dev/null || continue   # a base that is not an ancestor of this one says nothing about its diff
      c=$($G rev-list --count "$s..$1" 2>/dev/null) || continue; [ -n "$c" ] || continue
      printf '%s %s\n' "$c" "$s"; done | sort -n | head -1 | cut -d' ' -f2 -s)   # the closest ancestor: the fewest commits between it and the base
  [ -n "$a" ] || { echo full; return; }
  $G diff --name-only "$a" "$1" 2>/dev/null | suiteset; }
# a red base reds every branch at once: test it once per base head, before merging anything. 0 = the base is green (or already known green), 1 = wait
# A red base whose failing test arrived with a revert names that revert (#16): the last commit to touch the failing test's file says
# whether a revert is why it fails here, so a reader is not sent looking for a branch that was already taken back. Prints a clause, or nothing.
redrevert() { local f s
  f=$(redfile "$SG_STATE/basetest-$1.log") || return 0
  s=$($G log -1 --format='%h %s' "$SG_FORK_BASE" -- "$f" "*/$f" 2>/dev/null)
  case "${s#* }" in Revert\ *) printf '; the failing test file %s was last touched by the revert %s, so a revert of a merge, not a branch waiting to land, is where to look' "$f" "$s";; esac; }
basegate() { local l=$1 bt bgs rvt
  bgs=$(basesuites "$bs"); rvt=$(redrevert "$bs")   # the park and its solo retry reuse the candidate gate's selection, so the base is never parked by a suite its diff cannot affect
  if [ -e "$A/basered-$bs" ]; then
    # A park nobody hears about is a silent stall (#1407): say so once per base sha, naming the failing test and who waits.
    if [ ! -e "$A/basered-$bs.escalated" ]; then touch "$A/basered-$bs.escalated"
      ft=$(grep -E 'FAIL|not ok' "$SG_STATE/basetest-$bs.log" 2>/dev/null | grep -v 'fork gate' | head -3)
      wb=""; for w in $cc; do [ "$($G rev-list --count "$SG_FORK_BASE..$(sg_fork_branch "$w")" 2>/dev/null)" -gt 0 ] && wb="$wb $(sg_fork_branch "$w")"; done
      # the gate of one issue ("#12") reports on that issue; the batch gate names no issue, so its card has no address and cqflush.sh retires it by name
      cqn=${l//[^0-9]/}; if [ -n "$cqn" ]; then cqf=$(sg_cq_file "$cqn-basered$$.md"); else cqf="$SG_STATE/cq/batch-basered$$.md"; fi
      mkdir -p "$SG_STATE/cq"; printf "Fork base is red: %s %s fails the fork suite on its own, so every branch waiting to land is parked:%s.\nFailing test:\n\`\`\`\n%s\n\`\`\`\nLog: %s/basetest-%s.log. This base's own diff is answered with the %s gate; past %s min it is retried once on its own, and a green retry lifts the park, a second red keeps it%s.\n" "$SG_FORK_BASE" "$bs" "${wb:- $l}" "${ft:-(no FAIL line in the log)}" "$SG_STATE" "$bs" "$bgs" "${SG_FORK_BASERED_BUDGET_MINUTES:-30}" "$rvt" > "$cqf"
    fi
    # Past the budget, one solo retry (this pass holds the lock and the e2e rebuild comes later, so no rebuild is in flight): a red that came from a rebuild window must not park the branches forever.
    if [ ! -e "$A/basered-$bs.retried" ] && [ $(( now - $(stat -f %m "$A/basered-$bs") )) -ge $(( ${SG_FORK_BASERED_BUDGET_MINUTES:-30} * 60 )) ]; then touch "$A/basered-$bs.retried"
      if ( cd "$SG_FORK_DIR" && FG_SUITES=$bgs bash "$here/fork-gate.sh" "$SG_FORK_TEST_CMD" ) > "$SG_STATE/basetest-$bs.log" 2>&1; then rm -f "$A/basered-$bs" "$A/basered-$bs.tries"; echo "$SG_STATE/basetest-$bs.log" > "$A/basetest-$bs"; echo "FORKCLOSE BASE-GREEN $l: $SG_FORK_BASE $bs passed the solo retry ($bgs gate) after the park; lifting it"; return 0
      else echo "FORKCLOSE BASE-RED $l: the solo retry of $SG_FORK_BASE $bs ($bgs gate) failed too (see $SG_STATE/basetest-$bs.log); the park stays"; return 1; fi
    else echo "FORKCLOSE BASE-RED $l: $SG_FORK_BASE $bs fails the $bgs gate on its own, so a red run here would not be the branch's fault$rvt; waiting for a green base"; return 1; fi
  fi
  [ -e "$A/basetest-$bs" ] && return 0
  if ( cd "$SG_FORK_DIR" && FG_SUITES=$bgs bash "$here/fork-gate.sh" "$SG_FORK_TEST_CMD" ) > "$SG_STATE/basetest-$bs.log" 2>&1; then echo "$SG_STATE/basetest-$bs.log" > "$A/basetest-$bs"; rm -f "$A/basered-$bs.tries"; return 0; fi
  # one sample is not proof: the full suite flakes on the real emulator (a hold step loses the window to a suite running in
  # parallel), and a single red would park every branch on a flake. Two consecutive reds is the base, one is a retry.
  bt=$(( $(cat "$A/basered-$bs.tries" 2>/dev/null || echo 0) + 1 )); echo "$bt" > "$A/basered-$bs.tries"
  if [ "$bt" -ge "${SG_FORK_BASERED_TRIES:-2}" ]; then touch "$A/basered-$bs"; echo "FORKCLOSE BASE-RED $l: $SG_FORK_BASE $bs fails the $bgs gate on its own $bt runs in a row (see $SG_STATE/basetest-$bs.log); not merging, no revert, no fix run. Every branch is parked until the base is green$rvt."
  else echo "FORKCLOSE BASE-FLAKE $l: $SG_FORK_BASE $bs failed the $bgs gate on its own (run $bt of ${SG_FORK_BASERED_TRIES:-2}); one red is not proof, retrying next cycle."; fi
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
# The suite set a LANDING is judged by comes from the landing's OWN change to the base (#15), not from the branch's diff at its fork
# point: a resolution that restores a file the base lost to a sibling's revert touches files the branch's diff never named, and a
# gate judged as if it did not would run too narrow a suite for the tree it just made. bdiff() stays exactly as it is -- the base
# test (basesuites) and the conflict request both ask about a branch that has NOT landed, and there the fork point is the right
# question. Which rule answered is printed beside the class, so a reader never has to guess (#15).
ldiff() { $G diff --name-only "$1" "$2" 2>/dev/null; }
# The failing test a red gate log names (#13). A pytest line is `FAILED <file>::<test> - ...`, a shell suite `FAIL <file>: <case>`
# or `not ok 1 - <name>`; only the first two name a FILE, which is why an empty answer is "cannot attribute" and not "no failure".
# The anchor is the start of the line, so fork-gate.sh's own markers (`--- fork gate: python tests FAILED`) are never read as one.
redfile() { local l
  l=$(grep -m1 -E '^FAILED |^FAIL |^not ok ' "$1" 2>/dev/null) || return 1
  printf '%s\n' "$l" | grep -oE '[A-Za-z0-9_./-]+\.(py|sh)' | head -1; }
# A revert of a merge takes the spec that merge brought (#16): `revert -m 1` reverses the merge's whole diff, tests included, so a
# red landing never leaves the spec of a feature that is no longer there. The merges of one branch that still stand in the base are
# interdependent -- a branch that landed, was built on, and came back with a fix that goes red -- so taking back only the tip merge
# would mint a new bug (the first merge's work standing without the fix on top of it). They are reverted by ONE run, newest first,
# folded into ONE commit whose body says WHY ("the gate went red", never "the change is wrong"): both or neither. A spec file a later
# commit changed cannot go back out with the merge that added it (it is not that merge's any more); it stays in the base and is
# QUARANTINED, named with its reason. Any other conflict means the set cannot be taken back as a whole: nothing is reverted.
isspec() { case $1 in tests/*|*/tests/*|test_*|*/test_*|test-*|*/test-*) return 0;; esac; return 1; }
standing() { local m   # the merges of branch $1 still in the base, newest first; one an earlier revert already took back is not standing
  for m in $($G log --first-parent --merges --format=%H --grep="^Merge $1 (Refs #" "$SG_FORK_BASE" 2>/dev/null); do
    [ -n "$($G log -1 --format=%h --grep="This reverts commit $m" "$SG_FORK_BASE" 2>/dev/null)" ] || echo "$m"; done; }
unland() { local ms=$1 why=$2 start m u q="" subj body   # prints the QUARANTINE lines; returns 1, with the base exactly as it was, when the set cannot all be reverted
  start=$($G rev-parse HEAD); subj=$($G log -1 --format=%s "${ms%% *}")
  for m in $ms; do
    if ! $G revert -m 1 --no-edit "$m" >/dev/null 2>&1; then
      for u in $($G diff --name-only --diff-filter=U); do
        isspec "$u" || { $G revert --abort >/dev/null 2>&1; $G reset -q --hard "$start"; echo "$u"; return 1; }
        $G checkout -q HEAD -- "$u" 2>/dev/null || { $G revert --abort >/dev/null 2>&1; $G reset -q --hard "$start"; echo "$u"; return 1; }
        q="$q $u"; echo "FORKCLOSE QUARANTINE #$n: $u stays in $SG_FORK_BASE: a later commit changed it after ${m:0:9} brought it, so taking it back would drop that change (reverting the merge for a red gate: $why)"; done
      if [ -n "$($G diff --cached --name-only)" ]; then $G -c core.editor=true revert --continue >/dev/null 2>&1 || { $G revert --abort >/dev/null 2>&1; $G reset -q --hard "$start"; return 1; }
      else $G revert --skip >/dev/null 2>&1 || { $G revert --abort >/dev/null 2>&1; $G reset -q --hard "$start"; return 1; }; fi
    fi; done
  [ "$($G rev-parse HEAD)" != "$start" ] || return 1
  body=$(printf '%s\n\n%s\n\n' "Revert \"${subj}\"" "Taken back because the fork gate went red: $why. The change is not judged wrong here; a red gate is the reason, and a fix on the branch re-arms the close."
    for m in $ms; do printf 'This reverts commit %s.\n' "$m"; done
    [ -z "$q" ] || printf '\nQuarantined (a later commit changed them after the merge brought them, so they stay in the base):%s\n' "$q")
  $G reset -q --soft "$start" && printf '%s\n' "$body" | $G commit -q -F - ; }
# WHICH landed branch put the failing test in the candidate (#13). The candidate tree is what the gate just ran on, so the last
# commit that touched the file there is the one that added it, and the member whose branch carries that commit is the one to drop.
# Prints "<branch>\t<subject>\t<file>"; prints nothing -- leaving the reason in brwhy -- when the log names no test file, the file
# is not in the candidate tree, or no member of the batch carries the commit: the three shapes the caller has to fall back on.
batchculprit() { local f c b
  brwhy=""; f=$(redfile "$SG_STATE/tests/$1.log") || f=""
  [ -n "$f" ] || { brwhy="the gate log names no test file"; return 1; }
  $G cat-file -e "fc-batch:$f" 2>/dev/null || { brwhy="$f is not in the candidate tree, so no branch of the batch added it there"; return 1; }
  c=$($G log -1 --format=%H fc-batch -- "$f" 2>/dev/null); [ -n "$c" ] || { brwhy="no commit in the candidate touches $f"; return 1; }
  for b in $2; do $G merge-base --is-ancestor "$c" "$(brof "$b")" 2>/dev/null && { printf '%s\t%s\t%s\n' "$b" "$($G log -1 --format=%s "$c" 2>/dev/null)" "$f"; return 0; }; done
  brwhy="the commit $($G rev-parse --short=9 "$c") that added $f is in no branch of the batch"; return 1; }
# A red batch is the most informative signal the loop produces, and it used to discard it: the pass held the failing test (in the
# gate log it had just read), the commit that added it (in the candidate it still had checked out) and the branch that commit
# belongs to, and it printed "falling back to one branch at a time so the culprit is named" with none of them. Measured
# 2026-10-12: five ruff errors in one file of gui/1879 dropped six branches out of one batch and cost them their parallelism
# against max_parallel=10 (#13). So the red is attributed here first: the culprit is named, dropped, and the REST is re-gated as
# its own candidate in this same pass (FC_SET names the remainder, so there is no second landing path). The one-at-a-time
# fallback below stays for a red this cannot attribute -- a base that was already red, or a failure that names no test file.
batchpass() { local cf bl nn b2 key merged="" files="" suites first out rc id at cb cs cfr rest b0b c fb
  if [ -n "${FC_SET:-}" ]; then bl="$FC_SET"; else cf=$(mktemp); FC_COLLECT=$cf bash "$0"; bl=$(sort -un "$cf"); rm -f "$cf"; fi   # the re-gated remainder names its own set; otherwise the loop's own eligibility rules, whose notices (WAIT, ORPHAN) print here
  [ "$(wc -w <<<"$bl")" -ge 2 ] || return 0
  needbase "batch" || return 0   # a checkout left on a branch is returned to the base here, so the batch is not skipped for a pass (the loop below says WAIT when it cannot be)
  bs=$($G rev-parse --short=9 "$SG_FORK_BASE"); basegate "batch" || exit 0
  key=$(for nn in $bl; do $G rev-parse "$(brof $nn)"; done | cksum | cut -d' ' -f1); [ -e "$A/nobatch-$key" ] && return 0   # this exact set already went red as a batch
  b0b=$($G rev-parse "$SG_FORK_BASE")   # the base the candidate's own change is measured against (#15)
  $G checkout -q -b fc-batch "$SG_FORK_BASE" || return 0
  for nn in $bl; do b2=$(brof $nn)   # in order; a branch that conflicts with an earlier one stays out and gets its conflict fix from the loop below
    if $G merge --no-ff -q -m "Merge $b2 (Refs #$nn)" "$b2" >/dev/null 2>&1; then merged="$merged $nn"; else $G merge --abort 2>/dev/null; $G reset -q --hard HEAD; fi; done
  # (#14) A merge git made without a conflict can still drop a line both of its parents hold (a rename/delete it settled on its
  # own). Each merge the candidate just made is audited here, and the member it came from is dropped by name like a culprit -- the
  # rest of the batch is re-gated in this same pass instead of the whole set paying for one member's fabricated merge.
  for c in $($G rev-list --merges "$b0b..HEAD" 2>/dev/null); do
    auditmerge "$c" > "$A/batchfabrication.txt" 2>&1 || { sed 's/^/  /' "$A/batchfabrication.txt" >&2
      # the member behind this merge: the branch whose tip IS its second parent, or else the branch that carries the commit
      fb=""; for nn in $bl; do [ "$($G rev-parse "$(brof $nn)")" = "$($G rev-parse "$c^2" 2>/dev/null)" ] && fb=$nn; done
      [ -n "$fb" ] || for nn in $bl; do $G merge-base --is-ancestor "$c" "$(brof $nn)" 2>/dev/null && fb=$nn; done
      [ -n "$fb" ] || continue   # a merge the candidate made for no member of this batch: the one-at-a-time pass owns it
      rest=" ${merged# } "; rest=${rest// $fb / }; rest=${rest# }; rest=${rest% }
      $G checkout -q "$SG_FORK_BASE"; $G branch -q -D fc-batch
      echo "FORKCLOSE BATCH FABRICATION (${merged# }): the merge of $(brof "${fb:-?}") (#${fb:-?}) dropped a line both of its parents hold; dropping it and re-gating the rest"
      if [ -n "$fb" ] && [ "$(wc -w <<<"$rest")" -ge 2 ]; then FC_SET="${rest}" batchpass
      else touch "$A/nobatch-$key"; echo "FORKCLOSE BATCH PARKED (${rest}; the fabricated merge is the only member left): memoized nobatch-$key"; fi
      return 0; }; done
  if [ "$(wc -w <<<"$merged")" -lt 2 ]; then $G checkout -q "$SG_FORK_BASE"; $G branch -q -D fc-batch; return 0; fi
  files=$(ldiff "$b0b" HEAD)   # the candidate's own change to the base, the same rule the single landing uses (#15): what the merges did, never what each branch did at its own fork point
  suites=$(printf '%s\n' $files | suiteset); first=$(awk '{print $1}' <<<"$merged"); e2eprep "batch"
  id=test-$first-$(date +%Y%m%d-%H%M%S)   # fixed before the gate starts (#1470)
  out=$(TEST_ID=$id FG_SUITES=$suites "$here/test.sh" "$first" -C "$SG_FORK_DIR" -- bash "$here/fork-gate.sh" "$SG_FORK_TEST_CMD"); rc=$?
  # read off the candidate, which is still checked out here. Not `at=$(batchculprit ...)`: that runs in a subshell and takes the
  # brwhy it sets with it, so the fallback below would always print its default reason instead of the one this red has.
  at=""; brwhy=""; if [ $rc != 0 ]; then batchculprit "$id" "${merged# }" > "$A/batchculprit.txt" || true; at=$(cat "$A/batchculprit.txt"); fi
  $G checkout -q "$SG_FORK_BASE"
  if [ $rc = 0 ] && $G merge --ff-only -q fc-batch >/dev/null 2>&1; then   # the tree that was tested is the new base
    echo "$SG_STATE/tests/$id.log" > "$A/basetest-$($G rev-parse --short=9 HEAD)"; $G branch -q -D fc-batch
    echo "FORKCLOSE BATCH landed ${merged# } ($suites suites by the landing rule, $id green); each issue closes below"
  else $G branch -q -D fc-batch
    echo "FORKCLOSE BATCH RED (${merged# }; $id exit $rc): nothing landed"
    if [ -n "$at" ]; then   # attributable: name the culprit, drop it, and re-gate the rest in this same pass
      cb=${at%%$'\t'*}; rest=${at#*$'\t'}; cs=${rest%%$'\t'*}; cfr=${rest#*$'\t'}
      rest=" ${merged# } "; rest=${rest// $cb / }; rest=${rest# }; rest=${rest% }
      echo "FORKCLOSE BATCH CULPRIT (${merged# }) $cfr: $(brof "$cb") (#$cb), commit \"$cs\" (git log -1 --format=%s -- $cfr); dropping $(brof "$cb") and re-gating the rest (${rest}) on the same gate"
      if [ "$(wc -w <<<"$rest")" -lt 2 ]; then   # a candidate is built from at least two branches: re-gating one buys nothing, and retrying the set would spend this same red pass on every later pass
        touch "$A/nobatch-$key"
        echo "FORKCLOSE BATCH PARKED (${rest}; $(brof "$cb") (#$cb) was one of the two candidates, and a candidate is built from at least two): the set is memoized nobatch-$key so the same batch is not retried, and the loop below takes ${rest# } one branch at a time"
      else FC_SET="${rest# }" batchpass; fi
    else touch "$A/nobatch-$key"
      echo "FORKCLOSE BATCH FALLBACK (${merged# }; $suites gate, $id red): falling back to one branch at a time so the culprit is named -- ${brwhy:-the gate exited $rc without naming a failing test}"; fi
  fi; }
# Dispatch the fix request this pass just wrote. forkclose is the sole owner of the branch that request repairs (forkred-<n>-<sha>
# for a red gate, forkrebase-<n>-<sha> for a conflict), so the request is ABOUT that branch and must go past mkreq.sh's slice guard:
# the branch it has to fix, gui/<n>, is itself unmerged and touches exactly the files the fix edits, so the guard refuses it and
# nothing is ever launched (#1801). SG_SLICE_FORCE=1 is that guard's documented override (mkreq.sh) and it is set on this one call.
# A refusal is stopped work: it is recorded in cq/ naming the issue, and the caller must not print a "fix run" line for a run that
# was never launched. Returns non-zero on a refusal, having launched nothing.
dispatch() { local nm=$1 n=$2 kind=$3 mo="$SG_STATE/req/$nm.mkres" req rc
  req=$(SG_SLICE_FORCE=1 "$here/mkreq.sh" "$nm" "$SG_STATE/req/$nm.body" 2>"$mo"); rc=$?
  if [ $rc != 0 ]; then
    printf "Fork-only close: the %s fix request %s for issue #%s was refused by mkreq.sh and NO fix run was launched. The merge was reverted and the branch re-applied; the next pass retries the close. Nothing is waiting on a commit that cannot come.\n\`\`\`\n%s\n\`\`\`\n" "$kind" "$nm" "$n" "$(cat "$mo")" > "$(sg_cq_file "$n-slicerefused$$.md")"
    return $rc; fi
  (cd "$SG_CHECKOUT" && python3 -c 'import subprocess,sys;subprocess.Popen(["python3",sys.argv[1],sys.argv[2]],stdout=open(sys.argv[3],"w"),stderr=subprocess.STDOUT,start_new_session=True)' "$here/launch.py" "$req" "$SG_STATE/$nm.log"); }
ready=" $(jq -r '.items[]?|select((.st=="Code Complete" or .st=="Testing") and .s=="open")|.n' "${SG_HUB_DIR:-$SG_CHECKOUT/runs/.hub}/hub-board.json" 2>/dev/null | tr '\n' ' ') "
pat=$(sg_fork_branch_pattern); pat=${pat//%n/}; cc=$($G for-each-ref --format='%(refname:short)' "refs/heads/$pat*" | sed "s#^$pat##" | grep -E '^[0-9]+$')   # every fork branch, so a closed issue's fork half still lands
# The base LEFT ALREADY broken by an earlier merge of such a branch is the other half of #1823: local/next is ahead of
# origin/<base> with a tip nobody can push, every later pass reads the work as landed, and the run sees nothing. This pass
# never rewrites history on its own (the recovery is a graft, an operator's call), it only stops being silent about it.
if ob=$($G rev-parse -q --verify "refs/remotes/origin/$SG_FORK_BASE" 2>/dev/null) && [ "$($G rev-list --count "origin/$SG_FORK_BASE..$SG_FORK_BASE" 2>/dev/null)" != 0 ]; then
  if sb=$(shallowrange "origin/$SG_FORK_BASE..$SG_FORK_BASE"); then
    sc=${sb%% *}; sp=${sb#* }; pbm="$A/pushblocked-$($G rev-parse --short=9 "$SG_FORK_BASE")"
    if [ ! -e "$pbm" ]; then touch "$pbm"
      mkdir -p "$SG_STATE/cq"
      printf "Fork base is unpushable: %s is %s commit(s) ahead of origin/%s and its history carries %s, whose parent %s is not in this clone (shallow boundary; index-pack reports \`did not receive expected object %s\`). \`git push origin %s\` cannot succeed until that commit is grafted away, so the commits already merged stay local. Every branch that landed on this base since is in that state too.\n" "$SG_FORK_BASE" "$($G rev-list --count "origin/$SG_FORK_BASE..$SG_FORK_BASE")" "$SG_FORK_BASE" "$sc" "$sp" "$sp" "$SG_FORK_BASE" > "$SG_STATE/cq/pushblocked-${sc:0:9}$$.md"
      echo "FORKCLOSE PUSH-BLOCKED: $SG_FORK_BASE is ahead of origin/$SG_FORK_BASE and carries $sc whose parent $sp is missing from this clone: \`git push origin $SG_FORK_BASE\` cannot succeed (index-pack: did not receive expected object $sp) until it is grafted away. Report queued; no branch is merged into it in the meantime."
    fi
  fi
fi
[ -n "$cc" ] || exit 0
# The open PRs of the repository are ONE request (#5), and the projection is the same caller's jq it always was. A list
# that did not answer stops the pass instead of reading as "no open PRs": landing a branch whose PR is open is the merge
# this check exists to prevent, and a refusal is not an empty list (#1893).
if prs=$(gh_aggregate "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[]|(.title+" "+(.body//""))'); then
  refs=" $(printf '%s\n' "$prs" | grep -oE 'Refs #[0-9]+' | grep -oE '[0-9]+' | tr '\n' ' ')"
else echo "FORKCLOSE NO-PR-LIST: the open-PR list is not readable, so nothing is landed this pass (a branch whose PR is open would land behind it): $prs"; exit 0; fi
if oissues=$(gh_aggregate "repos/$SG_REPO/issues?state=open") && printf '%s' "$oissues" | jq -e 'type == "array"' >/dev/null 2>&1; then oissues_ok=1; ocomplete=1
  # A candidate the open list lacks is a closed issue whose fork half still lands: the issues of ALL such candidates are ONE
  # read by number, live because the close decision rests on it. A read that did not answer leaves each of them to its own.
  omiss=$(printf '%s' "$oissues" | jq -r --argjson w "[$(printf '%s\n' $cc | paste -sd, -)]" '[.[]?.number] as $o | $w[] | select(. as $n | $o | index($n) | not)' 2>/dev/null | paste -sd, -)
  if [ -n "$omiss" ]; then
    # a batch that names none of them is not proof they do not exist (a reader that cannot answer the batch answers an empty
    # list): that is read per candidate below, as before. The common case -- closed issues -- is answered by the batch.
    if ob=$(gh_api "repos/$SG_REPO/issues?numbers=$omiss") && [ "$(printf '%s' "$ob" | jq 'length' 2>/dev/null)" -gt 0 ] 2>/dev/null \
       && oissues=$(jq -c -n --argjson a "$oissues" --argjson b "$ob" '$a + $b' 2>/dev/null); then :; else ocomplete=""; fi; fi
else echo "FORKCLOSE NO-ISSUE-LIST: the repository's open issues are not readable in one request, so each candidate below pays its own read (or waits, if that read is refused too): $oissues"; fi
[ -n "${FC_DRY:-}${FC_NOBATCH:-}${FC_COLLECT:-}" ] || [ "$(wc -w <<<"$cc")" -lt 2 ] || batchpass
for n in $cc; do
  case "$refs" in *" $n "*) continue;; esac   # an open MesenAI PR: its fork half waits for that merge (the fork suite runs against main's build)
  # The issue's own facts, once per candidate (#5): from the pass's aggregate read, else from that issue's read. `st` is
  # what the PR-half decision below reads; the blocking edges come from the same object further down.
  ifact=$(issue_object "$n") || continue
  st=$(printf '%s' "$ifact" | jq -r .state 2>/dev/null); [ -n "$st" ] || continue
  br=$(sg_fork_branch "$n"); $G rev-parse -q --verify "refs/heads/$br" >/dev/null || continue
  # A branch that carries a shallow boundary is refused before anything else touches the base or the card: merging it is what
  # produces a $SG_FORK_BASE that cannot be pushed (#1823), and no later pass can undo that without rewriting history.
  if sb=$(shallowrange "$SG_FORK_BASE..$br"); then
    sc=${sb%% *}; sp=${sb#* }; sm="$A/shallow-$n-${sc:0:9}"
    # The recipe names the branch's OWN commits, never `rebase --onto $SG_FORK_BASE $sc $br`: $sc is the first commit of the
    # range with a missing parent, so it can be the branch tip, and `$sc..$br` is then empty -- a rebase that silently drops
    # the whole branch is a worse hand-off than the refusal itself (#1852).
    own=$($G rev-list --reverse "$SG_FORK_BASE..$br" 2>/dev/null | tr '\n' ' ')
    echo "FORKCLOSE SHALLOW #$n: $br carries $sc, whose parent $sp is not in this clone (shallow boundary, a lineage that was never published). Merging it would drag $sc into $SG_FORK_BASE and the push of $SG_FORK_BASE would be rejected (remote: fatal: did not receive expected object $sp). Not merging. Rebuild $br on $SG_FORK_BASE by cherry-picking its own commits onto a fresh branch: \`git -C $SG_FORK_DIR switch -c $br-rebuilt $SG_FORK_BASE && git -C $SG_FORK_DIR cherry-pick $own\` (its own commits are $own; a \`rebase --onto $SG_FORK_BASE $sc $br\` would take $sc..$br instead, which is empty when $sc is the tip -- it would drop the whole branch). The next pass lands the rebuilt branch."
    if [ ! -e "$sm" ]; then touch "$sm"
      mkdir -p "$SG_STATE/cq"
      printf "Fork-only close (#%s): %s did not land. The commit %s in its history has parent %s, which this clone never received (shallow boundary), so merging it would leave %s ahead of origin/%s with a tip the remote refuses (\`index-pack: did not receive expected object %s\`) and no later pass could undo that without rewriting history. Rebuild %s on %s and it lands on its own.\n" "$n" "$br" "$sc" "$sp" "$SG_FORK_BASE" "$SG_FORK_BASE" "$sp" "$br" "$SG_FORK_BASE" > "$(sg_cq_file "$n-shallow$$.md")"
    fi
    continue
  fi
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
    if [ -n "$lf" ]; then reland=1; else late=$($G log --format=%h -1 --grep="Merge $br" "$SG_FORK_BASE" 2>/dev/null)
      # A replayed landing is a fast-forward of the branch's own commits, so it carries no `Merge $br` subject: the record the
      # landing wrote ($A/landed-<head9>) is what still names the branch that produced this head (#11). Without it every later
      # pass reads "landed" with nothing to point at, and the close is lost.
      if [ -z "$late" ]; then lp=$($G rev-parse --short=9 "$br" 2>/dev/null); [ -n "$lp" ] && [ "$(cat "$A/landed-$lp" 2>/dev/null)" = "$br" ] && late=$lp; fi
      [ -n "$late" ] || continue; fi; fi
  [ -n "$late" ] && [ "$st" = closed ] && continue   # landed and the issue is already closed: the close is not late, it happened
  # The blocking edges are a fact of the same object: `blocked_by` is derived by the reader from the body, with the parse
  # the awk here used to carry -- the section is a LIST, it ends at the first line that is neither blank nor a bullet, so
  # a trailing prose line naming another issue is not an edge (#1803).
  blk=$(printf '%s' "$ifact" | jq -r '.blocked_by|tostring' 2>/dev/null)
  wait=""; for d in $(printf '%s' "$blk" | tr -d '[]' | tr ',' ' '); do   # a blocker whose fork half is not in the base yet: merging this one now only goes red (it imports the blocker's files)
    bb=$(sg_fork_branch "$d"); $G rev-parse -q --verify "refs/heads/$bb" >/dev/null && { [ "$($G rev-list --count "$SG_FORK_BASE..$bb")" -gt 0 ] || [ -n "$(lost "$bb")" ]; } && wait="$wait #$d"; done
  [ -z "$wait" ] || { wm="$A/forkwait-$n${wait// /-}"; [ -e "$wm" ] || { touch "$wm"; echo "FORKCLOSE WAIT #$n: the fork half of$wait is not in $SG_FORK_BASE yet (Blocked by)"; }; continue; }
  head=$($G rev-parse "$br"); mk="$A/fork-$n-${head:0:9}"
  if [ -e "$mk" ]; then   # "wait for a real fix commit" is a wait with no deadline unless the marker expires on its own
    age=$(( now - $(stat -f %m "$mk") ))
    [ "$age" -lt $(( ${SG_FORK_MARKER_MINUTES:-45} * 60 )) ] && continue
    rm -f "$mk"; echo "FORKCLOSE RETRY #$n: the marker for ${head:0:9} waited $(( age / 60 )) min with no new commit on $br and no fix run alive; retrying"
  fi
  # The pass merges the branch HEAD, so the branch HEAD has to BE the run's final state. A child that leaves its work uncommitted
  # in its own ~/wt-as-<n> worktree has a tip that is a mid-work commit: the merge lands that, git reports success and the issue
  # closes with the code still in the index and absent from $SG_FORK_BASE (#1821, #1809's source half was never on the base). Its
  # tree is never committed, checked out or reset from here -- the pass refuses and names the branch as unlanded, and the child
  # committing on the branch releases it on the next pass, exactly like the checkout wait above (FORKCLOSE UNLANDED, once per issue).
  if uw=$(dirtywt "$br"); then
    um="$A/forkunlanded-$n"; [ -e "$um" ] || { touch "$um"; echo "FORKCLOSE UNLANDED #$n: $br is checked out at $uw with uncommitted changes, so the branch tip ${head:0:9} is not the run's final state and merging it would close #$n with its code missing from $SG_FORK_BASE. Commit them on $br (or discard them); the pass resumes on its own."; }
    continue; fi
  rm -f "$A/forkunlanded-$n"
  # (#14) Every merge the branch carries is a conflict a CHILD resolved in its own worktree, and no pass saw it. The audit runs
  # here, before the branch is merged or gated: a resolution that dropped a line both of its parents hold fails this landing
  # alone, is named with the file and the line in the log and on the issue, and is not retried while the branch head stands.
  fm="$A/fabrication-$n-${head:0:9}"
  [ -e "$fm" ] && continue   # this head was already refused for a fabricated merge: the refusal is memoized, and a new commit on the branch releases it
  if [ -z "$late$reland" ] && ! auditbranch > "$A/fabrication-$n-${head:0:9}.txt" 2>&1; then
    touch "$fm"; mkdir -p "$SG_STATE/cq"
    sed 's/^/  /' "$A/fabrication-$n-${head:0:9}.txt" >&2
    printf 'Fork-only close (#%s): %s did not land. A merge it carries was made by hand and dropped a line both of its parents hold, so the merge is a fabrication, not a resolution (merge-audit.sh). The file and the line are below. Put the line back -- or re-make the merge keeping both sides -- and commit on %s: a new head releases this landing.\n\n```\n%s\n```\n' "$n" "$br" "$br" "$(cat "$A/fabrication-$n-${head:0:9}.txt")" > "$(sg_cq_file "$n-fabrication$$.md")"
    continue
  fi
  [ -n "${FC_COLLECT:-}" ] && { [ -z "$late$reland$mixed" ] && echo "$n" >> "$FC_COLLECT"; continue; }   # a plain open fork-only candidate
  # A fork-only fix has no PR body to carry its TDD RED output, so the child posts it on the issue ("TDD RED" first line, req-checklist-fork.txt). Missing: say so, once per issue; a warning only, the landing goes on (#1413).
  # The aggregate read carries whether a comment holds the report; only an issue read on its own has to ask for its comments.
  tdd=$(printf '%s' "$ifact" | jq -r '.tdd_red' 2>/dev/null)
  case "$tdd" in true|false) ;; *) if gh_get "repos/$SG_REPO/issues/$n/comments" --paginate -q '.[].body' 2>/dev/null | grep -q 'TDD RED'; then tdd=true; else tdd=false; fi;; esac
  if [ "$tdd" != true ]; then
    echo "FORKCLOSE WARN #$n: no TDD RED comment on the issue for $br (a fork-only fix records its failing test output there)"
    [ -n "${FC_DRY:-}" ] || [ -e "$A/redwarn-$n" ] || { touch "$A/redwarn-$n"; printf "Fork-only close: %s is landing but this issue has no \`TDD RED\` comment (test name and failure line, run against the base before the fix). A fork-only fix has no PR body to carry it; please post the RED and GREEN output here. The landing is not blocked.\n" "$br" > "$(sg_cq_file "$n-redwarn$$.md")"; }
  fi
  [ -n "${FC_DRY:-}" ] && { echo "FORKCLOSE would merge $br into $SG_FORK_BASE and test #$n"; continue; }
  needbase "#$n" || exit 0
  bs=$($G rev-parse --short=9 "$SG_FORK_BASE"); b0=$($G rev-parse "$SG_FORK_BASE")   # b0: the base this landing is measured against (#15)
  basegate "#$n" || continue
  # The fork-point rule, kept for the conflict request and as the answer when this pass makes no landing diff of its own; the landing
  # itself is judged by its own change to the base, printed below with which rule answered (#15). late/reland: doubt, full.
  fs=full; fsf=full; [ -z "$late$reland" ] && fsf=$(bdiff "$SG_FORK_BASE" "$br" | suiteset); fs=$fsf
  [ -z "$late" ] && touch "$mk"
  way=merge
  if [ -z "$late$reland" ] && [ "$($G rev-list --count "$br..$SG_FORK_BASE")" != 0 ]; then   # the base moved on after this branch forked: a fresh candidate is replayed before its gate, never merged stale (#11)
    echo "FORKCLOSE STALE #$n: $br (${head:0:9}) is behind $SG_FORK_BASE by $($G rev-list --count "$br..$SG_FORK_BASE") commit(s) it does not carry: a candidate that skipped its rebase is replayed onto the base before its gate, so the gate runs on the tree that lands"
    rebasestale; rs=$?
    if [ $rs = 0 ]; then head=$($G rev-parse "$br"); mk="$A/fork-$n-${head:0:9}"; way=rebase
      echo "FORKCLOSE REBASE #$n: $br replayed onto $SG_FORK_BASE ($($G rev-parse --short=9 "$br")); the gate below runs on the rebased tree"
    elif [ $rs = 2 ]; then
      echo "FORKCLOSE STALE #$n: $br is checked out with uncommitted changes, so its replay is refused by name: a live child owns those files and its branch is never rewritten under it. Commit them and the next pass replays it"
      continue
    else way=merge; echo "FORKCLOSE STALE-UNREBASABLE #$n: $br does not replay onto $SG_FORK_BASE cleanly; the merge path takes it (which way it went: merge, not rebase) and a conflict fix run is dispatched if it conflicts"; fi
  fi
  if [ -z "$late" ] && ! { if [ "$way" = rebase ]; then $G merge --ff-only -q "$br" || { way=merge; echo "FORKCLOSE STALE-MOVED #$n: $SG_FORK_BASE moved while $br was being replayed; the merge path takes it (which way it went: merge)"; }; elif [ -n "$reland" ]; then relandbr; else mergebr; fi; }; then
    $G merge --abort 2>/dev/null
    files=$($G merge-tree --write-tree --name-only "$SG_FORK_BASE" "$br" 2>/dev/null | sed -n '2,/^$/p' | grep . | tr '\n' ' ')
    nm="forkrebase-$n-${head:0:9}"; body="$SG_STATE/req/$nm.body"
    { echo "FORK-ONLY: all"   # mkreq.sh: fork DONE CHECK and checklist, no PR text (#1411)
      echo "IMPORTANT: your first and only action is to call the spawn tool for the one child below; ending the turn without a spawn call fails the run. Spawn the child with spend 8 (the router may not request more: spend_outside_budget). One child, a CONFLICT FIX on the local fork branch $br for GitHub issue #$n (repo $SG_REPO). en-US; no Co-Authored-By and no \"Generated with Claude Code\" line."
      echo "In $SG_FORK_DIR the branch $br (head ${head:0:9}) no longer merges into $SG_FORK_BASE: conflict in $files. Work in a worktree: \`git -C $SG_FORK_DIR worktree add ~/wt-fr-$n $br\` (or reuse the existing worktree of $br), then \`git merge $SG_FORK_BASE\` into $br and resolve every conflict KEEPING BOTH SIDES (sibling issues appended their own cases; never drop one). Run \`$SG_FORK_TEST_CMD\` there until it passes, commit the merge on $br, NEVER push, keep the worktree. Do not change anything beyond the conflict resolution."
      echo "Before you commit that merge: for every file you resolved, WHILE the merge is in progress and before \`git add\`, run \`bash $here/merge-audit.sh <file>\`. A line both parents hold that your resolution drops is a fabrication, and the landing refuses the branch for it (#14); putting the line back is the fix."
      echo "GitHub issue #$n: the issue is already implemented; only the conflict is in scope."; } > "$body"
    if ! dispatch "$nm" "$n" conflict; then
      echo "FORKCLOSE CONFLICT-REFUSED #$n $br into $SG_FORK_BASE ($files): mkreq.sh refused the fix request $nm; no fix run was launched, card left as it is"; continue; fi
    printf "Fork-only close: merging %s (%s) into %s conflicts (%s). A conflict-fix squad run (%s) merges the base into the branch keeping both sides; the loop retries the close when the branch head moves.\n" "$br" "${head:0:9}" "$SG_FORK_BASE" "$files" "$nm" > "$(sg_cq_file "$n-forkconflict$$.md")"
    echo "FORKCLOSE CONFLICT #$n $br into $SG_FORK_BASE ($files) -> fix run $nm"; continue; fi
  if [ "$way" = rebase ]; then printf '%s\n' "$br" > "$A/landed-$($G rev-parse --short=9 HEAD)"; fi   # the fast-forward landing has no `Merge $br` subject: this record is what names the branch it came from (#11)
  m=$([ -n "$late" ] && printf '%s' "$late" || $G rev-parse HEAD)
  if [ -z "$late" ]; then   # the landing is gated by its OWN change to the base (#15); a reland stays any-doubt, as it always was
    fs=full; [ -z "$reland" ] && fs=$(ldiff "$b0" HEAD | suiteset)
    echo "FORKCLOSE SUITES #$n: $fs by the landing rule (the landing's own change to $SG_FORK_BASE), the branch's fork-point diff answered $fsf"; fi
  if [ -n "$late" ] && [ -e "$A/basetest-$bs" ]; then   # the base that holds the branch is already tested green (a batch that just landed it, or the base test above): no second run
    rc=0; id=base-$bs; lg=$(cat "$A/basetest-$bs"); tl=$(tail -3 "${lg:-/dev/null}" 2>/dev/null)
  else e2eprep "#$n"
    id=test-$n-$(date +%Y%m%d-%H%M%S)   # fixed before the gate starts: a gate killed by a signal never prints it (#1470)
    out=$(TEST_ID=$id FG_SUITES=$fs "$here/test.sh" "$n" -C "$SG_FORK_DIR" -- bash "$here/fork-gate.sh" "$SG_FORK_TEST_CMD"); rc=$?; tl=$(tail -3 "$SG_STATE/tests/$id.log" 2>/dev/null)
    # the gate ran on the tree that is now the base head: record it as tested, so the next landing pays no second base test
    [ $rc = 0 ] && [ -z "$late" ] && [ "$($G rev-parse HEAD)" = "$m" ] && echo "$SG_STATE/tests/$id.log" > "$A/basetest-${m:0:9}"
  fi
  if [ $rc = 0 ] && [ -n "$mixed" ]; then
    printf "Fork half merged: %s at %s into %s as %s, fork suite green (test pass %s). The MesenAI half already landed through its PR.\n" "$br" "${head:0:9}" "$SG_FORK_BASE" "${m:0:9}" "$id" > "$(sg_cq_file "$n-forkhalf$$.md")"
    echo "FORKCLOSE #$n fork half merged ($br ${head:0:9} -> ${m:0:9}, $id green; PR still open)"
  elif [ $rc = 0 ]; then
    [ -n "$late" ] && landline="- $br was already in $SG_FORK_BASE as $late: the pass that merged it ended before its close step, and every later pass read the branch as landed and skipped it. The fork suite result below (a batch or base test that covers it) is the evidence that rule asks for." \
                   || landline="- $br at ${head:0:9} merged into $SG_FORK_BASE as ${m:0:9}."
    gh_send -X POST "repos/$SG_REPO/issues/$n/comments" -F body=@- >/dev/null <<EOT
Done (fork-only work; owner-approved rule: merged into the fork's $SG_FORK_BASE and the fork suite green there).
$landline
- Test pass $id: \`$SG_FORK_TEST_CMD\` → exit 0.
\`\`\`
$tl
\`\`\`
No MesenAI file changed, so no PR.
EOT
    gh_send -X PATCH "repos/$SG_REPO/issues/$n" -f state=closed -f state_reason=completed >/dev/null && $BOARD $SG_OWNER $SG_PROJECT Done $n >/dev/null
    [ -n "$late" ] && echo "FORKCLOSE #$n closed late ($br was already in $SG_FORK_BASE as $late; the close had been lost, $id green)" \
                   || echo "FORKCLOSE #$n closed ($br ${head:0:9} -> ${m:0:9}, $id green)"
  else
    [ -n "$late" ] && { printf "Fork-only close: %s was already in %s as %s and its issue #%s was still open, but the fork suite failed on the base itself (test pass %s, exit %s). Nothing of the branch was merged here and the branch has nothing to fix, so there is no revert and no fix run; the next pass retries.\n\`\`\`\n%s\n\`\`\`\n" "$br" "$SG_FORK_BASE" "$late" "$n" "$id" "$rc" "$tl" > "$(sg_cq_file "$n-latered$$.md")"
      echo "FORKCLOSE LATE-RED #$n ($id exit $rc): $br was already in $SG_FORK_BASE and the suite is red on the base itself; no revert, no fix run"; continue; }
    ek=""; { [ $rc = 143 ] || [ $rc = 137 ] || grep -q '"state": "running"' "$SG_STATE/tests/$id.json" 2>/dev/null; } && ek=1   # killed by a signal (or never finalized): the environment failed, not the code (#1470)
    ms="$m $(standing "$br" | grep -vx "$m" | tr '\n' ' ')"   # the tip merge first, then the earlier merges of the branch still standing: one set, both or neither (#16)
    if ! blk=$(unland "$ms" "test pass $id exit $rc"); then
      printf "Fork-only close: the fork suite failed after merging %s (test pass %s, exit %s) and the merge(s) of it (%s) cannot be taken back together: %s. NOTHING was reverted, because taking back only some of them would leave the others standing without the work on top of them. Owner: resolve the revert by hand (git revert -m 1 each merge, newest first); the base stays parked red until then.\n" "$br" "$id" "$rc" "$ms" "${blk##*$'\n'}" > "$(sg_cq_file "$n-revertrefused$$.md")"
      echo "FORKCLOSE NEEDS OWNER #$n ($id exit $rc): the merges of $br ($ms) cannot be reverted as a set (${blk##*$'\n'}); nothing was reverted, no fix run"; continue; fi
    [ -z "$blk" ] || echo "$blk"
    rv=$($G rev-parse HEAD)
    # the branch is now an ancestor of the base, so a later merge would be a no-op: re-apply its work on the branch
    # (merge the base in, then revert the revert) so the branch is ahead again and a fix commit re-arms the close
    bw=$($G worktree list --porcelain | awk -v b="refs/heads/$br" '/^worktree /{w=$2} $0=="branch "b{print w}')
    [ -n "$bw" ] && /usr/bin/git -C "$bw" merge -q --no-edit "$SG_FORK_BASE" >/dev/null 2>&1 && /usr/bin/git -C "$bw" revert --no-edit "$rv" >/dev/null 2>&1 \
      && { [ -n "$ek" ] || touch "$A/fork-$n-$(/usr/bin/git -C "$bw" rev-parse HEAD | cut -c1-9)"; }   # same content that just failed: wait for a real fix commit (a killed gate is retried instead)
    if [ -n "$ek" ]; then   # no fix run: there is nothing to fix. One retry on the next cycle; a second kill on the same head goes to the owner
      rm -f "$mk"; nh=$($G rev-parse "$br"); kf="$A/envkill-$n"; kc=0; [ "$(cut -d' ' -f2 "$kf" 2>/dev/null)" = "$nh" ] && kc=$(cut -d' ' -f1 "$kf"); kc=$(( kc + 1 )); echo "$kc $nh" > "$kf"
      if [ "$kc" -ge 2 ]; then touch "$A/fork-$n-${nh:0:9}"   # parked until the head moves or the marker expires
        printf "Fork-only close: the fork gate for %s was killed by a signal %s times in a row on the same head (test pass %s, exit %s, log %s/tests/%s.log). That is the environment, not the code, so no fix run was dispatched. Owner: check what kills the gate (a reaper, memory pressure, a stray pkill); the close resumes when the branch head moves.\n" "$br" "$kc" "$id" "$rc" "$SG_STATE" "$id" > "$(sg_cq_file "$n-envkill$$.md")"
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
      printf "Fork-only close: the fork suite failed after merging %s (%s) as %s (test pass %s, exit %s); the merge was reverted on %s. Card back to In Progress.\n\`\`\`\n%s\n\`\`\`\n" "$br" "${head:0:9}" "${m:0:9}" "$id" "$rc" "$SG_FORK_BASE" "$tl" > "$(sg_cq_file "$n-forkred$$.md")"
      echo "FORKCLOSE RED #$n ($id exit $rc, log $SG_STATE/tests/$id.log), merge reverted, branch re-applied, fix run $nm"
    else   # a refused request launched nothing: say so, and keep no park marker (nothing is coming to move that head)
      nh2=$($G rev-parse -q --verify "$br" 2>/dev/null); rm -f "$mk" "$A/fork-$n-${nh2:0:9}"
      echo "FORKCLOSE RED-REFUSED #$n ($id exit $rc, log $SG_STATE/tests/$id.log), merge reverted, branch re-applied: mkreq.sh refused the fix request $nm, so no fix run was launched (cq/${SG_REPO/\//__}#$n-slicerefused$$.md)"
    fi
  fi
done
