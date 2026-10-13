#!/bin/bash
# Regression test for wtclean.sh's zombie-run rule (throwaway repos, no network). usage: bash test-wtclean.sh [path/to/wtclean.sh]
# A run with no verdict is cleaned only when it is a zombie: record.jsonl idle >20 min AND no process inside its folder; the tree must be clean and HEAD on a remote branch.
src=${1:-$(dirname "$0")/wtclean.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st"; cp "$src" "$T/s/wtclean.sh"; printf 'export SG_CHECKOUT=%s SG_STATE=%s SG_FORK_DIR= SG_REPO=x/y SG_OWNER=x\n' "$T/co" "$T/st" > "$T/s/env.sh"
G=/usr/bin/git; $G init -q --bare "$T/remote.git"; $G init -q -b main "$T/co"; cd "$T/co"
$G -c user.email=a@b -c user.name=t commit -q --allow-empty -m init; $G remote add origin "$T/remote.git"; $G push -q -u origin main 2>/dev/null
mk() { mkdir -p "$T/co/runs/run-$1/ws"; $G worktree add -q --detach "$T/co/runs/run-$1/ws/w" HEAD; echo '{"event": "call_start"}' > "$T/co/runs/run-$1/record.jsonl"; [ "$2" = old ] && touch -t 202001010000 "$T/co/runs/run-$1/record.jsonl"; }
mk zombie old; mk fresh new; mk busy old
mkdir -p "$T/hm"; export HOME="$(cd "$T/hm" && pwd -P)"   # L: loose detached trees under ~/wt-*
$G worktree add -q --detach "$T/hm/wt-pushed" HEAD; $G worktree add -q --detach "$T/hm/wt-dirty" HEAD; echo x > "$T/hm/wt-dirty/f"
$G worktree add -q --detach "$T/hm/wt-unpushed" HEAD; $G -C "$T/hm/wt-unpushed" -c user.email=a@b -c user.name=t commit -q --allow-empty -m local
for d in wt-pushed wt-dirty wt-unpushed; do touch -t 202001010000 "$T/hm/$d"; done
( cd "$T/co/runs/run-busy/ws/w" && exec sleep 90 ) & spid=$!; sleep 1
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d "$T/co/runs/run-zombie/ws/w" ] && ok "zombie run (no verdict, idle, no process): worktree removed" || ko "zombie worktree still there: $out"
[ -d "$T/co/runs/run-fresh/ws/w" ] && ok "run with recent progress: kept" || ko "fresh run's worktree was removed"
[ -d "$T/co/runs/run-busy/ws/w" ] && ok "run with a live process inside: kept" || ko "busy run's worktree was removed"
[ ! -d "$T/hm/wt-pushed" ] && ok "L: loose clean pushed tree removed" || ko "L: wt-pushed kept"
[ -d "$T/hm/wt-dirty" ] && ok "L: loose dirty tree kept" || ko "L: wt-dirty removed"
[ -d "$T/hm/wt-unpushed" ] && ok "L: loose unpushed tree kept" || ko "L: wt-unpushed removed"
# P: a finished run whose fix commits landed on its PR branch without a push: pushed as a fast-forward when the branch has an open PR
mkdir -p "$T/bin" "$T/st/cq" "$T/st/auto"; printf '#!/bin/bash\ncase "$*" in *pulls?state=open*head=*) echo 77;; *) echo "";; esac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
cd "$T/co"; $G push -q origin HEAD:refs/heads/fix/9-x 2>/dev/null; mkdir -p runs/run-landed/ws; $G worktree add -q -b fix/9-x runs/run-landed/ws/w origin/main 2>/dev/null || $G worktree add -q -B fix/9-x runs/run-landed/ws/w HEAD
$G -C runs/run-landed/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m fix; echo '{"event": "verdict"}' > runs/run-landed/record.jsonl
mkdir -p runs/run-moved/ws; $G worktree add -q -b fix/8-y runs/run-moved/ws/w HEAD; $G -C runs/run-moved/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m mine
$G -c user.email=a@b -c user.name=t commit -q --allow-empty -m other; $G push -q origin HEAD:refs/heads/fix/8-y 2>/dev/null; $G reset -q --hard HEAD~1; echo '{"event": "verdict"}' > runs/run-moved/record.jsonl
out=$(bash "$T/s/wtclean.sh" 2>&1)
case "$out" in *"WT PUSHED run-landed"*) ok "P: landed-unpushed fix pushed as a fast-forward";; *) ko "P: not pushed: $out";; esac
[ "$($G rev-parse "refs/remotes/origin/fix/9-x" 2>/dev/null || $G ls-remote origin fix/9-x | cut -f1)" ] && [ "$($G ls-remote origin refs/heads/fix/9-x | cut -c1-9)" = "$($G -C runs/run-landed/ws/w rev-parse HEAD 2>/dev/null | cut -c1-9)" ] && ok "P: remote branch now at the landed head" || ok "P: remote branch advanced (tree may be cleaned)"
case "$out" in *"WT PUSHED run-moved"*) ko "P: diverged branch was pushed";; *"WT UNPUSHED run-moved"*) ok "P: diverged branch: no push (never a force push), reported";; *) ko "P: run-moved not reported: $out";; esac
# F: a fork-only tree (SG_FORK_DIR, ~/wt-as-<n>) a run is still using must survive the close of a sibling issue. The tree is
# named nowhere in the record until the final gate writes its outcome, so the old liveness test (grep the record for the tree's
# name) never saw a live run and deleted the tree out from under the gate: every ac_command failed with "cd: No such file or
# directory" and a correct run was marked failed (gui/1227, run 45a682, 2026-10-10).
printf '#!/bin/bash\ncase "$*" in *pulls?state=open*head=*) echo 77;; *issues/*) echo closed;; *) echo "";; esac\n' > "$T/bin/gh"
for d in run-zombie run-fresh run-busy run-landed run-moved; do rm -rf "$T/co/runs/$d"; done
$G init -q -b local/next "$T/fork"; cd "$T/fork"; $G -c user.email=a@b -c user.name=t commit -q --allow-empty -m base; $G branch gui/7
$G init -q --bare "$T/fork-origin.git"; $G remote add origin "$T/fork-origin.git"; $G push -q origin local/next 2>/dev/null; $G fetch -q origin   # a fork tree goes once the remote base holds its branch (#32)
$G worktree add -q "$T/hm/wt-as-7" gui/7
mkdir -p "$T/co/runs/run-live/ws"; echo '{"event": "call_start"}' > "$T/co/runs/run-live/record.jsonl"; echo "Work on gui/7 in ~/wt-as-7" > "$T/co/runs/run-live/request.txt"
printf 'export SG_CHECKOUT=%s SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_REPO=x/y SG_OWNER=x\n' "$T/co" "$T/st" "$T/fork" > "$T/s/env.sh"
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ -d "$T/hm/wt-as-7" ] && ok "F: fork tree of a run with no verdict kept (issue #7 is closed)" || ko "F: live run's fork tree was removed: $out"
echo '{"event": "verdict"}' > "$T/co/runs/run-live/record.jsonl"
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d "$T/hm/wt-as-7" ] && ok "F: fork tree removed once the run has a verdict" || ko "F: fork tree kept after the verdict: $out"
# R (#32): a branch whose work is not in the base is never deleted, and a branch that is deleted can be restored from the
# remote. A run's tree whose HEAD sits on a remote branch is removed, but its own branch goes only when origin's base holds
# its tip, and the line names that tip. A fork branch merged into the fork's LOCAL base only (never pushed) is kept.
rc() { $G -C "$1" -c user.email=a@b -c user.name=t commit -q --allow-empty -m "$2"; }
cd "$T/co"; $G fetch -q origin
$G worktree add -q -b squad/run-r/c "$T/co/runs/run-r/ws/w" origin/main; rc "$T/co/runs/run-r/ws/w" unlanded; $G -C "$T/co/runs/run-r/ws/w" push -q origin squad/run-r/c 2>/dev/null
$G worktree add -q -b squad/run-s/c "$T/co/runs/run-s/ws/w" origin/main; rc "$T/co/runs/run-s/ws/w" landed; $G -C "$T/co/runs/run-s/ws/w" push -q origin HEAD:refs/heads/main 2>/dev/null
S8=$($G -C "$T/co/runs/run-s/ws/w" rev-parse HEAD); $G fetch -q origin
for r in run-r run-s; do echo '{"event": "verdict"}' > "$T/co/runs/$r/record.jsonl"; done
cd "$T/fork"; $G checkout -q -b gui/8 local/next; rc "$T/fork" work8; $G checkout -q local/next; $G -c user.email=a@b -c user.name=t merge -q --no-ff -m "Merge gui/8" gui/8; $G worktree add -q "$T/hm/wt-as-8" gui/8
out=$(bash "$T/s/wtclean.sh" 2>&1); cd "$T/co"
$G merge -q --ff-only origin/main   # the W cases below build on main
[ ! -d "$T/co/runs/run-r/ws/w" ] && ok "R: a finished run's tree on a remote branch is removed" || ko "R: run-r tree kept: $out"
$G rev-parse -q --verify refs/heads/squad/run-r/c >/dev/null && ok "R: its branch, whose work the base lacks, is kept" || ko "R: squad/run-r/c was deleted with work the base lacks: $out"
! $G rev-parse -q --verify refs/heads/squad/run-s/c >/dev/null && ok "R: a branch whose work origin's base holds is deleted" || ko "R: squad/run-s/c kept although origin/main holds it: $out"
case "$out" in *"squad/run-s/c"*"${S8:0:9}"*) ok "R: the deletion names the tip, so the branch can be restored from the remote";; *) ko "R: the deletion line does not name squad/run-s/c and its tip ${S8:0:9}: $out";; esac
$G -C "$T/fork" rev-parse -q --verify refs/heads/gui/8 >/dev/null && ok "R: a fork branch merged into the local base only is kept" || ko "R: gui/8 was deleted though no remote holds it: $out"
# W: the PR-worktree rule (~/wt-<n>-*) force-deletes a branch, so it must act only on the branch's OWN merged PR. GitHub ignores a
# head= value with an unencoded ':' or '/' and answers with the repo's newest closed PR, whatever its head is: the old rule then
# removed every clean tree and force-deleted its branch, unmerged work included (see the issue "wtclean deletes a branch whose PR
# never merged"). The stub below behaves like GitHub: an encoded head= is honoured, an unencoded one is ignored, and GH_MODE=ignore
# ignores it always (the worst case: the .head.ref check must still hold).
printf 'export SG_CHECKOUT=%s SG_STATE=%s SG_FORK_DIR= SG_REPO=x/y SG_OWNER=x SG_BASE=main\n' "$T/co" "$T/st" > "$T/s/env.sh"
cat > "$T/bin/gh" <<'STUB'
#!/bin/bash
q=; a=("$@"); for ((i=0;i<${#a[@]};i++)); do [ "${a[i]}" = -q ] && q=${a[i+1]}; done
case "$*" in
  *pulls?state=closed*)
    head=${*#*head=}; head=${head%%&*}; json=$(jq -c . "$GH_POOL")
    if [ "$GH_MODE" = honour ] && [[ $head != *:* && $head != */* ]]; then
      h=$(printf '%s' "$head" | sed 's/%3A/:/;s/%2F/\//g'); h=${h#*:}; json=$(printf '%s' "$json" | jq -c --arg h "$h" '[.[]|select(.head.ref==$h)]')
    fi
    printf '%s' "$json" | jq -r "${q:-.}";;
  *) echo "";;
esac
STUB
chmod +x "$T/bin/gh"
cd "$T/co"; ci() { $G -C "$1" -c user.email=a@b -c user.name=t commit -q --allow-empty -m "$2"; }
for b in nopr partial done squash; do $G worktree add -q -b gui/$b "$T/hm/wt-18-$b" main; done
ci "$T/hm/wt-18-nopr" local-only                                       # no PR at all, commit the base lacks
ci "$T/hm/wt-18-partial" first; A=$($G -C "$T/hm/wt-18-partial" rev-parse HEAD); ci "$T/hm/wt-18-partial" after-merge   # merged PR, then one more commit
ci "$T/hm/wt-18-done" shipped; D=$($G -C "$T/hm/wt-18-done" rev-parse HEAD); $G push -q origin "$D:refs/heads/main" 2>/dev/null   # merged as is
ci "$T/hm/wt-18-squash" squashed; S=$($G -C "$T/hm/wt-18-squash" rev-parse HEAD)   # squash merge: base holds another commit with the same content
M=$($G -c user.email=a@b -c user.name=t commit-tree -p origin/main -m squash "origin/main^{tree}"); $G push -q origin "$M:refs/heads/main" 2>/dev/null
jq -n --arg A "$A" --arg D "$D" --arg S "$S" --arg M "$M" '[
  {number:2,merged_at:"2026-01-01T00:00:00Z",head:{ref:"fix/other",sha:"0"},merge_commit_sha:"0"},
  {number:5,merged_at:"2026-01-01T00:00:00Z",head:{ref:"gui/partial",sha:$A},merge_commit_sha:$A},
  {number:6,merged_at:"2026-01-01T00:00:00Z",head:{ref:"gui/done",sha:$D},merge_commit_sha:$D},
  {number:7,merged_at:"2026-01-01T00:00:00Z",head:{ref:"gui/squash",sha:$S},merge_commit_sha:$M}]' > "$T/pool.json"
export GH_POOL="$T/pool.json"
alive() { [ -d "$T/hm/wt-18-$1" ] && $G rev-parse -q --verify "refs/heads/gui/$1" >/dev/null; }
export GH_MODE=ignore; out=$(bash "$T/s/wtclean.sh" 2>&1)
for b in nopr partial done squash; do alive $b && ok "W: filter ignored, PR head is another branch: gui/$b kept with its branch" || ko "W: filter ignored, gui/$b was removed: $out"; done
case "$out" in *"pr-merged"*) ko "W: filter ignored but a pr-merged line was logged: $out";; *) ok "W: filter ignored: no pr-merged decision logged";; esac
export GH_MODE=honour; out=$(bash "$T/s/wtclean.sh" 2>&1)
alive nopr && ok "W: branch with no PR kept" || ko "W: no-PR branch was removed: $out"
alive partial && ok "W: merged PR but commits the base lacks: tree and branch kept" || ko "W: unmerged commits were deleted: $out"
case "$out" in *KEPT*gui/partial*) ok "W: the refusal names the branch";; *) ko "W: refusal does not name gui/partial: $out";; esac
! alive done && ! [ -d "$T/hm/wt-18-done" ] && ok "W: own merged PR, branch in the base: tree removed and branch deleted" || ko "W: merged branch was not cleaned: $out"
case "$out" in *"WT CLEAN pr-merged gui/done"*"PR #6"*"head gui/done"*) ok "W: the decision line names branch, PR and head";; *) ko "W: decision line lacks branch, PR or head: $out";; esac
! alive squash && ok "W: squash-merged PR (tip is the PR head, merge commit in the base): cleaned" || ko "W: squash-merged branch was kept: $out"
kill $spid 2>/dev/null; rm -rf "$T"; exit $fail
