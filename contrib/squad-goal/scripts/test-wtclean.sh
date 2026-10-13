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
$G worktree add -q "$T/hm/wt-as-7" gui/7
mkdir -p "$T/co/runs/run-live/ws"; echo '{"event": "call_start"}' > "$T/co/runs/run-live/record.jsonl"; echo "Work on gui/7 in ~/wt-as-7" > "$T/co/runs/run-live/request.txt"
printf 'export SG_CHECKOUT=%s SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_REPO=x/y SG_OWNER=x\n' "$T/co" "$T/st" "$T/fork" > "$T/s/env.sh"
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ -d "$T/hm/wt-as-7" ] && ok "F: fork tree of a run with no verdict kept (issue #7 is closed)" || ko "F: live run's fork tree was removed: $out"
echo '{"event": "verdict"}' > "$T/co/runs/run-live/record.jsonl"
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d "$T/hm/wt-as-7" ] && ok "F: fork tree removed once the run has a verdict" || ko "F: fork tree kept after the verdict: $out"
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
# C: the child trees under $SG_CHECKOUT/worktrees/child — the set the hub's footer counts as `wt N` and the one a reader
# asks to free. A tree is freed only when nothing can be lost and nothing is using it (clean, HEAD in the base, no process
# inside, no run in flight naming it); anything else is held, and the pass writes the tally the footer reads as `wt 1/4`.
cd "$T/co"; mkdir -p worktrees/child
$G worktree add -q worktrees/child/c-merged -b gui/c1 HEAD
$G worktree add -q worktrees/child/c-ahead -b gui/c2 HEAD; $G -C worktrees/child/c-ahead -c user.email=a@b -c user.name=t commit -q --allow-empty -m work
$G worktree add -q worktrees/child/c-dirty -b gui/c3 HEAD; echo x > worktrees/child/c-dirty/f
$G worktree add -q worktrees/child/c-busy -b gui/c4 HEAD
( cd worktrees/child/c-busy && exec sleep 90 ) & cpid=$!; sleep 1
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d worktrees/child/c-merged ] && ok "C: merged, clean, unused child tree removed" || ko "C: c-merged kept: $out"
[ -d worktrees/child/c-ahead ] && ok "C: child tree ahead of the base kept" || ko "C: c-ahead was removed: $out"
[ -d worktrees/child/c-dirty ] && ok "C: dirty child tree kept" || ko "C: c-dirty was removed: $out"
[ -d worktrees/child/c-busy ] && ok "C: child tree with a live process kept" || ko "C: c-busy was removed: $out"
case "$out" in *"WT CLEAN child c-merged"*) ok "C: the decision line names the class and the tree";; *) ko "C: no child decision line: $out";; esac
[ "$(jq -r '.total' "$T/st/wt.json" 2>/dev/null)" = 4 ] && [ "$(jq -r '.cleanable' "$T/st/wt.json" 2>/dev/null)" = 1 ] && ok "C: tally 1/4, the ratio the footer reads" || { ko "C: tally wrong: $(cat "$T/st/wt.json" 2>/dev/null)"; }
kill $cpid 2>/dev/null; for d in c-ahead c-dirty c-busy; do rm -rf "$T/co/worktrees/child/$d"; done   # S below measures the tally alone, so the C trees leave first
# S: a tree whose submodules are CHECKED OUT. `git worktree remove` refuses the whole tree — `fatal: working trees containing
# submodules cannot be moved or removed` — a line git prints with no run id and no class, so the tree was never freed and the
# footer's count never dropped for it (#102). The pass answers the class itself: it adds the flag git needs, after the tree is
# already proved clean, unused and landed (its submodule's own dirt reads as ` M sub` in the status check above, so the flag
# loses nothing), and names the run or the tree in the line. A tree it still cannot free is reported once with its own reason.
$G init -q -b main "$T/sub"; ( cd "$T/sub" && $G -c user.email=a@b -c user.name=t commit -q --allow-empty -m s )
$G fetch -q origin main; $G merge -q --ff-only origin/main   # the W cases above pushed their own commits to origin/main: the submodule commit rides on top of the base, never beside it
$G -c protocol.file.allow=always -c user.email=a@b -c user.name=t submodule add -q "$T/sub" sub >/dev/null 2>&1
$G -c user.email=a@b -c user.name=t commit -q -m submodule; $G push -q origin main 2>/dev/null
cd "$T/co"; mkdir -p runs/run-sub/ws .worktrees
$G worktree add -q --detach runs/run-sub/ws/w HEAD; ( cd runs/run-sub/ws/w && $G -c protocol.file.allow=always submodule update --init -q )
echo '{"event": "verdict"}' > runs/run-sub/record.jsonl
$G worktree add -q -b gui/csub .worktrees/c-sub HEAD; ( cd .worktrees/c-sub && $G -c protocol.file.allow=always submodule update --init -q )
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d runs/run-sub/ws/w ] && ok "S: run workspace whose submodule is checked out is freed" || ko "S: the submodule tree leaked: $out"
case "$out" in *"WT CLEAN run-sub"*submodule*) ok "S: the line names the run and the submodule class";; *) ko "S: no line names the run and the class: $out";; esac
[ ! -d .worktrees/c-sub ] && ok "S: child tree whose submodule is checked out is freed" || ko "S: the child submodule tree leaked: $out"
case "$out" in *"WT CLEAN child c-sub"*submodule*) ok "S: the child line names the tree and the class";; *) ko "S: no child line names the class: $out";; esac
case "$out" in *"cannot be moved or removed"*) ko "S: git's own refusal still reaches the log, naming nothing: $out";; *) ok "S: no bare git refusal reaches the log";; esac
[ "$(jq -r '.total' "$T/st/wt.json" 2>/dev/null)" = 1 ] && [ "$(jq -r '.cleanable' "$T/st/wt.json" 2>/dev/null)" = 1 ] && ok "S: the footer's count drops for the freed submodule tree (1/1)" || ko "S: the count does not agree: $(cat "$T/st/wt.json" 2>/dev/null)"
# S2: the tree the flag still cannot free — the same class, now with its parent directory unwritable. Silence was the other
# half of the defect: the pass says the class itself, names the tree, and says why, instead of leaving git's nameless fatal
# as the only word on it.
$G worktree add -q -b gui/clocked .worktrees/c-locked HEAD; ( cd .worktrees/c-locked && $G -c protocol.file.allow=always submodule update --init -q )
chmod 500 .worktrees; out=$(bash "$T/s/wtclean.sh" 2>&1); chmod 700 .worktrees
case "$out" in *"WT KEPT submodule child c-locked"*) ok "S2: a tree the flag cannot free is reported with its class and its name";; *) ko "S2: the pass kept it in silence: $out";; esac
case "$out" in *"cannot be moved or removed"*) ko "S2: git's nameless fatal is still the only word: $out";; *) ok "S2: no nameless refusal reaches the log";; esac
$G worktree prune 2>/dev/null; rm -rf .worktrees
rm -rf "$T"; exit $fail
