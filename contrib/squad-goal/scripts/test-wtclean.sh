#!/bin/bash
# seconds: 90.59
# Regression test for wtclean.sh's zombie-run rule (throwaway repos, no network). usage: bash test-wtclean.sh [path/to/wtclean.sh]
# A run with no verdict is cleaned only when it is a zombie: record.jsonl idle >20 min AND no process inside its folder; the tree must be clean and HEAD on a remote branch.
here=$(cd "$(dirname "$0")" && pwd -P); src=${1:-$here/wtclean.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export PYTEST_DEBUG_TEMPROOT="$T/pytmp"   # hermetic: no pass below may prune the real pytest temp trees (#1581)
mkdir -p "$T/s" "$T/st"; cp "$src" "$T/s/wtclean.sh"; printf 'export SG_CHECKOUT=%s SG_STATE=%s SG_FORK_DIR= SG_REPO=x/y SG_OWNER=x\n' "$T/co" "$T/st" > "$T/s/env.sh"
# the suite resolves lsof as the real env.sh does (#393): wtclean.sh reads every live cwd through SG_LSOF, and with it unset
# the read answers nothing, so a tree a live process works in reads as unused. The loop's shell exports it; a cleared shell does not.
export SG_LSOF=${SG_LSOF:-$(PATH="$PATH:/usr/sbin:/usr/bin" command -v lsof 2>/dev/null)}   # exported, so every stub env.sh below keeps it
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
# P (#2020): a finished run whose commits are on no remote branch is SALVAGED, never kept for good. The pass writes a local ref
# refs/salvage/wt/<tree path> at the tree's HEAD, removes the tree, and names the ref and the sha. The loop pushes no work of its
# own: a run pushes its own branch, and the auto-push that used to run here read the branch's pull request, which the loop no
# longer does. The ref keeps the work; the line names the push a person may make.
mkdir -p "$T/bin" "$T/st/cq" "$T/st/auto"; printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> "%s/ghlog"\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"; : > "$T/ghlog"
cd "$T/co"; $G push -q origin HEAD:refs/heads/fix/9-x 2>/dev/null; mkdir -p runs/run-landed/ws; $G worktree add -q -b fix/9-x runs/run-landed/ws/w origin/main 2>/dev/null || $G worktree add -q -B fix/9-x runs/run-landed/ws/w HEAD
$G -C runs/run-landed/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m fix; echo '{"event": "verdict"}' > runs/run-landed/record.jsonl
mkdir -p runs/run-moved/ws; $G worktree add -q -b fix/8-y runs/run-moved/ws/w HEAD; $G -C runs/run-moved/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m mine
$G -c user.email=a@b -c user.name=t commit -q --allow-empty -m other; $G push -q origin HEAD:refs/heads/fix/8-y 2>/dev/null; $G reset -q --hard HEAD~1; echo '{"event": "verdict"}' > runs/run-moved/record.jsonl
lr=$($G -C runs/run-landed/ws/w rev-parse HEAD); sl=${lr:0:9}   # read before the pass: a salvaged tree is gone after it
out=$(bash "$T/s/wtclean.sh" 2>&1)
case "$out" in *"WT SALVAGED run-landed w $sl"*) ok "P: a finished run with commits on no remote branch is salvaged";; *) ko "P: run-landed not salvaged: $out";; esac
[ ! -d runs/run-landed/ws/w ] && ok "P: the salvaged tree is removed" || ko "P: the tree with the unpushed work was kept"
[ "$($G rev-parse -q --verify refs/salvage/wt/runs_run-landed_ws_w 2>/dev/null)" = "$lr" ] && ok "P: the ref holds the tree's HEAD" || ko "P: no salvage ref at the tree's HEAD"
case "$out" in *"refs/salvage/wt/runs_run-landed_ws_w"*) ok "P: the line names the ref";; *) ko "P: the line names no ref: $out";; esac
remote=$($G ls-remote origin refs/heads/fix/9-x | cut -c1-9); [ "$remote" != "$sl" ] && ok "P: the loop pushed nothing" || ko "P: the loop pushed the run's branch"
[ ! -s "$T/ghlog" ] && ok "P: the pass reads nothing of GitHub" || ko "P: a gh call was made: $(cat "$T/ghlog")"
case "$out" in *"WT SALVAGED run-moved w "*) ok "P: a branch that moved on the remote is salvaged the same way, never force-pushed";; *) ko "P: run-moved not salvaged: $out";; esac
# #767: only a `fix/<n>-…` or `gui/<n>-…` branch is a work branch, so the line names the exact salvage push under that name.
case "$out" in *"git push origin $sl:refs/heads/fix/9-x"*) ok "#767: the line names the salvage push under the work-branch name";; *) ko "#767: no salvage command: $out";; esac
mv_line=$(printf '%s\n' "$out" | grep "WT SALVAGED run-moved")
case "$mv_line" in *"refs/heads/fix/8-y"*) ko "#767: a moved branch is named for a push the remote refuses: $mv_line";; *"refs/heads/fix/<n>-<slug>"*) ok "#767: a moved branch gets a fresh fix/<n>-<slug> name";; *) ko "#767: run-moved has no salvage command: $mv_line";; esac
# V (#464): the loop's wave entry reads the child's worktree after the run's VERDICT (ADR-0145 rule 9), so a run whose wave
# has not landed keeps its child trees: a tree removed here left the entry with nothing to read ("WAVE no entry for #N: the
# child's worktree ... is not there"), so every wave landed empty and no card left the queue. The marker `wave-<run>` exists
# from the cut to the landing, so it is the fact this pass reads; `waved-<run>` is the durable record that the entry is done.
cd "$T/co"; mkdir -p "$T/st/auto" runs/run-wave/ws; $G worktree add -q --detach runs/run-wave/ws/w HEAD
echo '{"event": "verdict"}' > runs/run-wave/record.jsonl; : > "$T/st/auto/wave-run-wave"
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ -d runs/run-wave/ws/w ] && ok "V: a run whose wave has not landed keeps its child tree" || ko "V: the tree went before the entry: $out"
case "$out" in *"WT HELD wave run-wave"*) ok "V: the hold names the run and its class";; *) ko "V: the hold is silent: $out";; esac
rm -f "$T/st/auto/wave-run-wave"   # the wave landed: the entry is done, or the wave took no child
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d runs/run-wave/ws/w ] && ok "V: once the wave landed the tree goes" || ko "V: the tree stayed after the landing: $out"
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
# W: the loose-tree rule (~/wt-<n>-*) force-deletes a branch, so it acts only on a branch that is in the landing branch (dev):
# an ancestor of origin/dev. It reads that fact from git and never reads a pull request: the gh stub logs every call, and a
# call to a pull route fails the case.
printf 'export SG_CHECKOUT=%s SG_STATE=%s SG_FORK_DIR= SG_REPO=x/y SG_OWNER=x SG_BASE=main\n' "$T/co" "$T/st" > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "$GH_LOG"\necho ""\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export GH_LOG="$T/gh.log"; : > "$GH_LOG"
cd "$T/co"; ci() { $G -C "$1" -c user.email=a@b -c user.name=t commit -q --allow-empty -m "$2"; }
for b in nopr partial done; do $G worktree add -q -b gui/$b "$T/hm/wt-18-$b" main; done
ci "$T/hm/wt-18-nopr" local-only                                       # ahead of dev, never landed
ci "$T/hm/wt-18-partial" first; A=$($G -C "$T/hm/wt-18-partial" rev-parse HEAD); ci "$T/hm/wt-18-partial" after-landing   # landed, then one more commit
ci "$T/hm/wt-18-done" shipped; D=$($G -C "$T/hm/wt-18-done" rev-parse HEAD)   # landed as is
$G push -q origin "$D:refs/heads/dev" 2>/dev/null
for b in nopr partial done; do touch -t 202001010000 "$($G -C "$T/hm/wt-18-$b" rev-parse --path-format=absolute --git-path logs/HEAD)"; done   # HEAD idle: a fresh tree at the base tip is never a landed one (#676)
alive() { [ -d "$T/hm/wt-18-$1" ] && $G rev-parse -q --verify "refs/heads/gui/$1" >/dev/null; }
out=$(bash "$T/s/wtclean.sh" 2>&1); out1=$out
alive nopr && ok "W: branch ahead of dev kept" || ko "W: a branch ahead of dev was removed: $out"
! alive done && ! [ -d "$T/hm/wt-18-done" ] && ok "W: branch in dev: tree removed and branch deleted" || ko "W: a landed branch was not cleaned: $out"
case "$out" in *"WT CLEAN landed gui/done"*"in dev"*) ok "W: the decision line names the branch and dev";; *) ko "W: decision line lacks branch or dev: $out";; esac
$G push -q -f origin "$A:refs/heads/dev" 2>/dev/null; out=$(bash "$T/s/wtclean.sh" 2>&1)
alive partial && ok "W: landed head plus a newer commit: tree and branch kept" || ko "W: unlanded commits were deleted: $out"
case "$out1" in *"WT KEPT gui/nopr"*) ok "W: the refusal names the branch";; *) ko "W: refusal does not name the branch: $out1";; esac
grep -q "pulls" "$GH_LOG" && ko "W: the pass read a pull request: $(cat "$GH_LOG")" || ok "W: no pull request is read"
# C: the child trees under $SG_CHECKOUT/worktrees/child — the set the hub's footer counts as `wt N` and the one a reader
# asks to free. A tree is freed only when nothing can be lost and nothing is using it (clean, HEAD in the base, no process
# inside, no run in flight naming it); anything else is held, and the pass writes the tally the footer reads as `wt 1/4`.
cd "$T/co"; mkdir -p worktrees/child
$G worktree add -q worktrees/child/c-merged -b gui/c1 HEAD
$G worktree add -q worktrees/child/c-ahead -b gui/c2 HEAD; $G -C worktrees/child/c-ahead -c user.email=a@b -c user.name=t commit -q --allow-empty -m work
$G worktree add -q worktrees/child/c-dirty -b gui/c3 HEAD; echo x > worktrees/child/c-dirty/f
$G worktree add -q worktrees/child/c-busy -b gui/c4 HEAD
$G worktree add -q worktrees/child/c-run -b gui/c5 HEAD; echo runs/ >> "$T/co/.git/info/exclude"; mkdir -p worktrees/child/c-run/runs/autobug-live; echo '{"event": "call_start"}' > worktrees/child/c-run/runs/autobug-live/record.jsonl; echo "$T/co/worktrees/child/c-run/runs/autobug-live" > "$T/st/active-runs.txt"
# V (#477): the hold on a wave run's trees is the run's wave marker, wherever the run folder sits. A target that holds the run
# folder reads clean (runs/ is ignored) and its HEAD may already be in the base, so between the run's verdict and its wave's
# landing only the marker keeps it. A wave that landed rebased its children, so their shas are on no remote: the patch, read
# by git cherry, is what says the work is in the base, and then the tree and its branch go. A hold whose wave started longer
# ago than the hold cap (SG_WAVE_HOLD_MAX_SECONDS) lapses: a marker no landing will clear never holds a tree for good.
mkdir -p "$T/st/auto"
$G worktree add -q worktrees/child/c-wave -b gui/c6 HEAD; mkdir -p worktrees/child/c-wave/runs/run-wave/ws
echo '{"event": "verdict"}' > worktrees/child/c-wave/runs/run-wave/record.jsonl; echo wave/run-wave > "$T/st/auto/wave-run-wave"; date +%s > "$T/st/auto/wave-start-run-wave"
$G worktree add -q worktrees/child/c-landed -b gui/c7 HEAD; echo landed > worktrees/child/c-landed/landed.txt
$G -C worktrees/child/c-landed add landed.txt; $G -C worktrees/child/c-landed -c user.email=a@b -c user.name=t commit -q -m "landed work"
mkdir -p runs/run-wl/ws; $G worktree add -q -b squad/run-wl/child runs/run-wl/ws/w HEAD; echo child > runs/run-wl/ws/w/child.txt
$G -C runs/run-wl/ws/w add child.txt; $G -C runs/run-wl/ws/w -c user.email=a@b -c user.name=t commit -q -m "child work"
echo '{"event": "verdict"}' > runs/run-wl/record.jsonl; echo wave/run-wl > "$T/st/auto/waved-run-wl"
# the wave landed: a rebase put both commits on the base under new shas, so neither tree's HEAD is on any remote
$G -c user.email=a@b -c user.name=t commit -q --allow-empty -m "the base moved"   # the picks get new parents, so new shas
$G -c user.email=a@b -c user.name=t cherry-pick "$($G -C worktrees/child/c-landed rev-parse HEAD)" "$($G -C runs/run-wl/ws/w rev-parse HEAD)" >/dev/null
$G -c user.email=a@b -c user.name=t commit -q --allow-empty -m "after the landing"; $G push -q origin main 2>/dev/null
$G worktree add -q worktrees/child/c-stale -b gui/c8 HEAD; mkdir -p worktrees/child/c-stale/runs/run-stale
echo '{"event": "verdict"}' > worktrees/child/c-stale/runs/run-stale/record.jsonl; echo wave/run-stale > "$T/st/auto/wave-run-stale"; echo 1000 > "$T/st/auto/wave-start-run-stale"
( cd worktrees/child/c-busy && exec sleep 90 ) & cpid=$!; sleep 1
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ -d worktrees/child/c-wave ] && ok "V: a target holding a run whose wave has not landed is kept after the run's verdict" || ko "V: c-wave was removed before its wave landed: $out"
case "$out" in *"WT HELD wave run-wave c-wave"*) ok "V: the held target's line names the run and the tree";; *) ko "V: no WT HELD line for c-wave: $out";; esac
[ ! -d worktrees/child/c-landed ] && ! $G rev-parse -q --verify refs/heads/gui/c7 >/dev/null && ok "V: a child tree whose rebased work is in the base goes with its branch" || ko "V: c-landed or gui/c7 kept after the landing: $out"
[ ! -d runs/run-wl/ws/w ] && ! $G rev-parse -q --verify refs/heads/squad/run-wl/child >/dev/null && ok "V: a landed wave run's child tree goes with its branch, its sha on no remote" || ko "V: run-wl's child kept after its wave landed: $out"
case "$out" in *"WT UNPUSHED run-wl"*) ko "V: a landed child is reported as unpushed: $out";; *) ok "V: a landed child is not reported as unpushed";; esac
[ ! -d worktrees/child/c-stale ] && ok "V: a hold past its age cap lapses, and the landed tree goes" || ko "V: c-stale is held past the cap: $out"
case "$out" in *"WT HOLD LAPSED run-stale"*) ok "V: the lapsed hold says so, naming the run";; *) ko "V: no lapse line for run-stale: $out";; esac
[ ! -d worktrees/child/c-merged ] && ok "C: merged, clean, unused child tree removed" || ko "C: c-merged kept: $out"
[ -d worktrees/child/c-ahead ] && ok "C: child tree ahead of the base kept" || ko "C: c-ahead was removed: $out"
[ -d worktrees/child/c-dirty ] && ok "C: dirty child tree kept" || ko "C: c-dirty was removed: $out"
[ -d worktrees/child/c-busy ] && ok "C: child tree with a live process kept" || ko "C: c-busy was removed: $out"
[ -d worktrees/child/c-run ] && ok "C: child tree with a live run folder kept" || ko "C: c-run was removed: $out"
case "$out" in *"WT CLEAN child c-merged"*) ok "C: the decision line names the class and the tree";; *) ko "C: no child decision line: $out";; esac
# a tree with no submodule is removed with no flag, and its line says no more (#393: every line claimed the --force removal)
case "$out" in *"WT CLEAN child c-merged"*"submodule tree"*) ko "C: a tree with no submodule is named a submodule tree: $out";; *) ok "C: a plain tree's line names no submodule";; esac
[ "$(jq -r '.total' "$T/st/wt.json" 2>/dev/null)" = 8 ] && [ "$(jq -r '.cleanable' "$T/st/wt.json" 2>/dev/null)" = 3 ] && ok "C: tally 3/8, the ratio the footer reads" || { ko "C: tally wrong: $(cat "$T/st/wt.json" 2>/dev/null)"; }
# X (#1637): a tree an in-flight dispatch cut is held. autobug writes auto/bug-<n> before the wave cuts the tree, and the run
# folder that would name the tree exists only after launch.py writes it: at the base tip, clean and with no run folder, the tree
# read as landed and unused and was culled under the dispatch, which then crashed and left the issue claimed for good. The hold
# lapses after its own cap, so a marker no pass frees never keeps a tree. A tree no marker names still goes.
$G worktree add -q worktrees/child/c-1635 -b gui/1635 HEAD; echo autobug-test > "$T/st/auto/bug-1635"
$G worktree add -q worktrees/child/c-1636 -b gui/1636 HEAD; echo autofrontier-test > "$T/st/auto/disp-1636"
$G worktree add -q worktrees/child/c-1637 -b gui/1637 HEAD; echo autobug-stale > "$T/st/auto/bug-1637"; touch -t 202001010000 "$T/st/auto/bug-1637"
$G worktree add -q worktrees/child/c-1638 -b gui/1638 HEAD
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ -d worktrees/child/c-1635 ] && ok "X: a tree an in-flight bug dispatch names is kept" || ko "X: c-1635 was culled under its dispatch: $out"
case "$out" in *"WT HELD dispatch #1635 c-1635"*) ok "X: the hold names the issue and the tree";; *) ko "X: no dispatch hold line: $out";; esac
[ -d worktrees/child/c-1636 ] && ok "X: a tree an in-flight frontier dispatch names is kept" || ko "X: c-1636 was culled under its dispatch: $out"
[ ! -d worktrees/child/c-1637 ] && ok "X: a dispatch hold past its age cap lapses, and the landed tree goes" || ko "X: c-1637 is held past the cap: $out"
[ ! -d worktrees/child/c-1638 ] && ok "X: a tree no dispatch names still goes" || ko "X: c-1638 kept with no marker: $out"
rm -rf worktrees/child/c-1635 worktrees/child/c-1636; rm -f "$T/st/auto/bug-1635" "$T/st/auto/disp-1636" "$T/st/auto/bug-1637"
kill $cpid 2>/dev/null; for d in c-ahead c-dirty c-busy c-run c-wave c-stale; do rm -rf "$T/co/worktrees/child/$d"; done   # S below measures the tally alone, so the C trees leave first
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
# U (#842): local branches keep only the exceptional trees, not ordinary work.
cd "$T/co"; mkdir -p runs/run-842-clean/ws; $G worktree add -q -b squad/run-842-clean/child runs/run-842-clean/ws/w HEAD
$G -C runs/run-842-clean/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m "child work"
$G branch -q squad/run/842-clean "$($G -C runs/run-842-clean/ws/w rev-parse HEAD)"; echo '{"event": "verdict"}' > runs/run-842-clean/record.jsonl
mkdir -p runs/run-842-refs/ws; $G worktree add -q -b squad/run-842-refs/child runs/run-842-refs/ws/w HEAD
$G -C runs/run-842-refs/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m "child work (Refs #842)"
$G branch -q squad/run/842-refs "$($G -C runs/run-842-refs/ws/w rev-parse HEAD)"; echo '{"event": "verdict"}' > runs/run-842-refs/record.jsonl
mkdir -p runs/run-842-empty/ws; $G worktree add -q -b squad/run-842-empty/child runs/run-842-empty/ws/w HEAD
$G -C runs/run-842-empty/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m "child work"
$G branch -q squad/run/842-empty "$($G -C runs/run-842-empty/ws/w rev-parse HEAD)"; echo '{"event": "verdict"}' > runs/run-842-empty/record.jsonl
printf 'issue 842\n' > "$T/st/auto/waved-run-842-empty"
rsha=$($G -C runs/run-842-refs/ws/w rev-parse HEAD); esha=$($G -C runs/run-842-empty/ws/w rev-parse HEAD)   # read before the pass
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d runs/run-842-clean/ws/w ] && ok "U: local-only branch without undelivered Refs is cleanable" || ko "U: ordinary local-only work was kept: $out"
[ ! -d runs/run-842-refs/ws/w ] && [ "$($G rev-parse -q --verify refs/salvage/wt/runs_run-842-refs_ws_w 2>/dev/null)" = "$rsha" ] && ok "U: undelivered Refs work goes with the tree and the ref keeps it" || ko "U: undelivered Refs work was kept or lost: $out"
[ ! -d runs/run-842-empty/ws/w ] && [ "$($G rev-parse -q --verify refs/salvage/wt/runs_run-842-empty_ws_w 2>/dev/null)" = "$esha" ] && ok "U: a run whose landed wave was empty is salvaged, not kept" || ko "U: empty-wave work was kept or lost: $out"
# SALVAGE (#2020): the ref is the preservation, so only a tree with nothing to lose and nothing using it goes. A dirty tree, a live
# process inside it, or another registered worktree inside it keeps it, and the pass writes no ref for it. The ref is named for the
# tree's path under the checkout: a tree's own name repeats (`w` here, the router's child name in the loop), and two runs' trees
# must never share one ref.
salv() { mkdir -p "runs/run-$1/ws"; $G worktree add -q --detach "runs/run-$1/ws/w" HEAD; }
salv salv; $G -C runs/run-salv/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m "salv work"; echo '{"event": "verdict"}' > runs/run-salv/record.jsonl
salv salv2; $G -C runs/run-salv2/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m "salv work too"; echo '{"event": "verdict"}' > runs/run-salv2/record.jsonl
salv salvd; $G -C runs/run-salvd/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m "dirty salv"; echo x > runs/run-salvd/ws/w/f; echo '{"event": "verdict"}' > runs/run-salvd/record.jsonl
salv salvb; $G -C runs/run-salvb/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m "busy salv"; echo '{"event": "verdict"}' > runs/run-salvb/record.jsonl
( cd runs/run-salvb/ws/w && exec sleep 90 ) & bpid=$!; sleep 1
b1=$($G -C runs/run-salv/ws/w rev-parse HEAD); b2=$($G -C runs/run-salv2/ws/w rev-parse HEAD)
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d runs/run-salv/ws/w ] && [ "$($G rev-parse -q --verify refs/salvage/wt/runs_run-salv_ws_w 2>/dev/null)" = "$b1" ] && ok "SALVAGE: the tree goes and its work is in its ref" || ko "SALVAGE: the first tree was kept or lost: $out"
[ ! -d runs/run-salv2/ws/w ] && [ "$($G rev-parse -q --verify refs/salvage/wt/runs_run-salv2_ws_w 2>/dev/null)" = "$b2" ] && ok "SALVAGE: two same-named trees get one ref each" || ko "SALVAGE: two trees shared one ref or one stayed: $out"
[ ! -d runs/run-salvd/ws/w ] && ko "SALVAGE: a dirty unpushed tree was removed: $out" || { $G rev-parse -q --verify refs/salvage/wt/runs_run-salvd_ws_w >/dev/null 2>&1 && ko "SALVAGE: a dirty tree got a ref" || ok "SALVAGE: a dirty unpushed tree is kept and gets no ref"; }
[ ! -d runs/run-salvb/ws/w ] && ko "SALVAGE: a tree a live process works in was removed: $out" || { $G rev-parse -q --verify refs/salvage/wt/runs_run-salvb_ws_w >/dev/null 2>&1 && ko "SALVAGE: a busy tree got a ref" || ok "SALVAGE: a live process keeps the tree and no ref is written"; }
kill $bpid 2>/dev/null
for d in salv salv2 salvd salvb; do $G worktree remove --force "runs/run-$d/ws/w" >/dev/null 2>&1 || rm -rf "runs/run-$d"; done
# Q: a finished tree under .claude/worktrees (one per workflow run, made by the agent tools) is culled once it is clean, unlocked,
# not in use and its HEAD is in the base. A locked tree is a live agent's, and a dirty one holds work, so both stay.
cd "$T/co"; mkdir -p .claude/worktrees
$G worktree add -q --detach .claude/worktrees/wf-done origin/main
$G worktree add -q --detach .claude/worktrees/wf-locked origin/main; $G worktree lock .claude/worktrees/wf-locked
$G worktree add -q --detach .claude/worktrees/wf-dirty origin/main; echo x > .claude/worktrees/wf-dirty/f
for d in wf-done wf-locked wf-dirty; do touch -t 202001010000 "$($G -C ".claude/worktrees/$d" rev-parse --path-format=absolute --git-path logs/HEAD)"; done
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d .claude/worktrees/wf-done ] && ok "Q: finished .claude tree in the base is removed" || ko "Q: finished .claude tree kept: $out"
[ -d .claude/worktrees/wf-locked ] && ok "Q: locked .claude tree is kept" || ko "Q: locked .claude tree was removed: $out"
[ -d .claude/worktrees/wf-dirty ] && ok "Q: dirty .claude tree is kept" || ko "Q: dirty .claude tree was removed: $out"
case "$out" in *"WT CLEAN claude wf-done"*) ok "Q: the decision line names the class and the tree";; *) ko "Q: no claude decision line: $out";; esac
# T (#1581): pytest temp trees a killed gate left behind. PYTEST_DEBUG_TEMPROOT points at a scratch root, as pytest reads it.
# Three trees: a .lock naming a dead pid, a .lock naming a live pid (this shell's), and an unlocked one a week old. The first
# and third go, the live one stays, and TMP PRUNED is printed once.
tr="$T/tmproot/pytest-of-${USER:-$(id -un)}"; export PYTEST_DEBUG_TEMPROOT="$T/tmproot"; mkdir -p "$tr/pytest-1" "$tr/pytest-2" "$tr/pytest-3" "$tr/garbage-4"
( exit 0 ) & dead=$!; wait $dead
printf '%s' "$dead" > "$tr/pytest-1/.lock"; printf '%s' "$$" > "$tr/pytest-2/.lock"; echo x > "$tr/pytest-1/f"
touch -t 202001010000 "$tr/pytest-3" "$tr/garbage-4"
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d "$tr/pytest-1" ] && ok "T: a tree whose lock names a dead pid is pruned" || ko "T: dead-pid tree kept: $out"
[ -d "$tr/pytest-2" ] && ok "T: a tree whose lock names a live pid is kept" || ko "T: live-pid tree was pruned"
[ ! -d "$tr/pytest-3" ] && ok "T: an old unlocked tree is pruned" || ko "T: old unlocked tree kept: $out"
[ ! -d "$tr/garbage-4" ] && ok "T: an old garbage-* tree is pruned" || ko "T: garbage tree kept"
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c '^TMP PRUNED ')" = 1 ] && case "$out" in *"TMP PRUNED 3 trees, "*) ok "T: TMP PRUNED is printed once with the count";; *) ko "T: wrong count: $out";; esac || ko "T: TMP PRUNED not once: $out"
mkdir -p "$tr/pytest-5"; : > "$tr/pytest-5/.lock"
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ -d "$tr/pytest-5" ] && ok "T: a fresh tree with an empty lock is kept" || ko "T: fresh empty-lock tree pruned"
touch -t 202001010000 "$tr/pytest-5"; bash "$T/s/wtclean.sh" >/dev/null 2>&1
[ ! -d "$tr/pytest-5" ] && ok "T: an old tree with an empty lock is pruned" || ko "T: old empty-lock tree kept"
out=$(bash "$T/s/wtclean.sh" 2>&1)
case "$out" in *"TMP PRUNED"*) ko "T: a second pass pruned again: $out";; *) ok "T: a second pass prunes nothing and prints nothing";; esac
# DISK STOP names the largest entries under the temp directory and the state directory (ghcall.sh).
mkdir -p "$T/dtmp/big" "$T/dtmp/small" "$T/dst"; head -c 3000000 /dev/zero > "$T/dtmp/big/f"; echo x > "$T/dtmp/small/f"
out=$(TMPDIR="$T/dtmp" SG_STATE="$T/dst" SG_MIN_FREE_GB=999999 bash -c '. "$1"; gh_disk_stop' _ "$here/ghcall.sh" 2>&1)
case "$out" in *"DISK STOP: "*"DISK TOP $T/dtmp/big: "*) ok "T: DISK STOP names the largest entries";; *) ko "T: no DISK TOP line: $out";; esac
# ARCH (#2574): a tree whose issues are all closed and that nothing moved for SG_WT_ARCHIVE_SECONDS goes, even with commits
# outside the base or tracked dirt. The pass first pushes HEAD to refs/archive/wt/<tree path>/<sha9>/head and the tracked dirt,
# as a `git stash create` commit, to .../dirty. It reads closed issues from $SG_STATE/issue-facts.json only, and it never
# archives an untracked file. An open issue, no issue, a stale facts file or a refused push keeps the tree. The origin is the
# local bare repository: SG_LAND_REMOTE names it, so no push reaches github.com.
cd "$T/co"; printf 'export SG_CHECKOUT=%s SG_STATE=%s SG_FORK_DIR= SG_REPO=x/y SG_OWNER=x SG_BASE=main SG_LAND_REMOTE=%s\n' "$T/co" "$T/st" "$T/remote.git" > "$T/s/env.sh"
$G fetch -q origin; RB="$T/remote.git"
facts() { python3 - "$T/st/issue-facts.json" "$1" <<'P'
import json, sys, time
s = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - int(sys.argv[2])))
f = lambda st: {"f": [st, "", "t", "", ""], "at": time.time()}
json.dump({"repo": "x/y", "synced": s, "issues": {"7001": f("closed"), "7002": f("open"), "7003": f("closed"), "7004": f("closed"),
    "7006": f("closed"), "7007": f("closed")}}, open(sys.argv[1], "w"))
P
}
old() { for p in "$@"; do touch -t 202001010000 "$p"; done; }
hl() { $G -C "$1" rev-parse --path-format=absolute --git-path logs/HEAD; }
# ach <tree>: a child tree with one commit outside the base, quiet for years
ach() { $G worktree add -q -b "gui/${1##*-}-a" "$1" origin/main; echo "$1" > "$1/own.txt"; $G -C "$1" add own.txt
  $G -C "$1" -c user.email=a@b -c user.name=t commit -q -m "own work ${1##*/}"; old "$(hl "$1")"; }
ach worktrees/child/gui-7001; ach worktrees/child/gui-7002; ach worktrees/child/gui-7003; ach worktrees/child/gui-7004; ach worktrees/child/c-noissue
echo changed > worktrees/child/gui-7004/own.txt; echo loose > worktrees/child/gui-7004/untracked.txt
# a closed top tree that holds an ended run, and that run's dirty child tree
ach worktrees/child/gui-7006; N=worktrees/child/gui-7006/runs/run-n7006; mkdir -p "$N/ws"
echo "CHILD — GitHub issue(s) 7006 (bug): work on it" > "$N/request.txt"; echo '{"event": "verdict", "verdict": "completed"}' > "$N/record.jsonl"
ach "$N/ws/w"; echo dirt > "$N/ws/w/own.txt"; old "$N/record.jsonl"
# a run tree whose run needs a human, for an issue that is now closed
$G worktree add -q -b squad/run/k7007 worktrees/run/k7007 origin/main; mkdir -p worktrees/run/k7007/runs/run-k7007
echo "GitHub issue #7007" > worktrees/run/k7007/runs/run-k7007/request.txt
echo '{"event": "verdict", "verdict": "needs-human", "reasons": [], "remains": []}' > worktrees/run/k7007/runs/run-k7007/record.jsonl
$G -C worktrees/run/k7007 -c user.email=a@b -c user.name=t commit -q --allow-empty -m "run work"; old worktrees/run/k7007/runs/run-k7007/record.jsonl "$(hl worktrees/run/k7007)"
# the origin refuses the archive push of one tree
printf '#!/bin/sh\nwhile read o n r; do case $r in *gui-7003*) echo refused >&2; exit 1;; esac; done; exit 0\n' > "$RB/hooks/pre-receive"; chmod +x "$RB/hooks/pre-receive"
s1=$($G -C worktrees/child/gui-7001 rev-parse HEAD); s4=$($G -C worktrees/child/gui-7004 rev-parse HEAD); s6=$($G -C "$N/ws/w" rev-parse HEAD)
aref() { $G -C "$RB" for-each-ref --format='%(refname) %(objectname)' "refs/archive/wt/$1/"; }
facts 7200; out=$(bash "$T/s/wtclean.sh" 2>&1)   # STALE: a facts file synced two hours ago is no proof
[ -d worktrees/child/gui-7001 ] && [ -z "$(aref worktrees_child_gui-7001)" ] && ok "ARCH: a stale facts file keeps the tree and pushes nothing" || ko "ARCH: a stale facts file freed gui-7001: $out"
case "$out" in *"WT KEPT child gui-7001:"*"stale"*) ok "ARCH: the kept line says the facts are stale";; *) ko "ARCH: no stale reason: $out";; esac
facts 300; out=$(bash "$T/s/wtclean.sh" 2>&1); o2=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d worktrees/child/gui-7001 ] && [ "$(aref worktrees_child_gui-7001)" = "refs/archive/wt/worktrees_child_gui-7001/${s1:0:9}/head $s1" ] && ok "ARCH: a closed issue's tree with commits outside the base is archived, then removed" || ko "ARCH: gui-7001 not archived and removed: $out | $(aref worktrees_child_gui-7001)"
case "$out" in *"WT CLEAN child gui-7001"*"archived"*) ok "ARCH: the line names the archive";; *) ko "ARCH: no archive line for gui-7001: $out";; esac
[ -d worktrees/child/gui-7002 ] && [ -z "$(aref worktrees_child_gui-7002)" ] && ok "ARCH: an open issue keeps the tree and pushes nothing" || ko "ARCH: gui-7002 went with its issue open: $out"
case "$out" in *"WT KEPT child gui-7002:"*"#7002"*) ok "ARCH: the kept line names the open issue";; *) ko "ARCH: the open issue is not named: $out";; esac
[ -d worktrees/child/gui-7003 ] && ok "ARCH: a refused push keeps the tree" || ko "ARCH: gui-7003 went with its push refused: $out"
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c 'WT KEPT.*gui-7003')" = 1 ] && case "$out" in *"WT KEPT child gui-7003:"*"archive push refused"*) ok "ARCH: one WT KEPT line names the refused push";; *) ko "ARCH: the refused push line has no reason: $out";; esac || ko "ARCH: not one WT KEPT line for gui-7003: $out"
case "$o2" in *gui-7003*) ko "ARCH: a second pass names the refused push again: $o2";; *) ok "ARCH: a second pass says nothing more of the refused push";; esac
d4=$($G -C "$RB" rev-parse -q --verify "refs/archive/wt/worktrees_child_gui-7004/${s4:0:9}/dirty" 2>/dev/null)
[ ! -d worktrees/child/gui-7004 ] && [ -n "$d4" ] && [ "$($G -C "$RB" show "$d4:own.txt" 2>/dev/null)" = changed ] && ok "ARCH: the tracked dirt reaches the dirty ref" || ko "ARCH: gui-7004's dirt is not archived: $out | $(aref worktrees_child_gui-7004)"
[ "$($G -C "$RB" rev-parse -q --verify "refs/archive/wt/worktrees_child_gui-7004/${s4:0:9}/head" 2>/dev/null)" = "$s4" ] && ok "ARCH: the dirty tree's HEAD reaches the head ref" || ko "ARCH: no head ref for gui-7004"
$G -C "$RB" rev-list --all --objects 2>/dev/null | /usr/bin/grep -q 'untracked.txt' && ko "ARCH: an untracked file reached a ref" || ok "ARCH: an untracked file reaches no ref"
[ -d worktrees/child/c-noissue ] && case "$out" in *"WT KEPT child c-noissue:"*"no issue"*) ok "ARCH: a tree no issue names stays, and says so";; *) ko "ARCH: c-noissue kept with no reason: $out";; esac || ko "ARCH: a tree no issue names was removed: $out"
[ ! -d "$N/ws/w" ] && [ ! -d worktrees/child/gui-7006 ] && [ -n "$(aref worktrees_child_gui-7006_runs_run-n7006_ws_w)" ] && ok "ARCH: a dirty ws tree under a closed top is archived and freed, then its top" || ko "ARCH: the nested ws tree or its top stayed: $out"
case "$out" in *"run-n7006 "*"archived"*"WT CLEAN child gui-7006"*) ok "ARCH: the ws tree goes before its top";; *) ko "ARCH: the ws tree did not go first: $out";; esac
[ ! -d worktrees/run/k7007 ] && [ -n "$(aref worktrees_run_k7007)" ] && ok "ARCH: a closed issue lifts a needs-human hold, and the run tree is archived and removed" || ko "ARCH: k7007 kept: $out"
rm -f "$RB/hooks/pre-receive"
rm -rf "$T"; exit $fail
