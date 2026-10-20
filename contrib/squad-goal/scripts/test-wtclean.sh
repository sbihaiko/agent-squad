#!/bin/bash
# Regression test for wtclean.sh's zombie-run rule (throwaway repos, no network). usage: bash test-wtclean.sh [path/to/wtclean.sh]
# A run with no verdict is cleaned only when it is a zombie: record.jsonl idle >20 min AND no process inside its folder; the tree must be clean and HEAD on a remote branch.
src=${1:-$(dirname "$0")/wtclean.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
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
# P (#361): a finished run whose commits are on no remote branch is KEPT and reported WT UNPUSHED. The loop pushes no work of its
# own: a run pushes its own branch, and the auto-push that used to run here read the branch's pull request, which the loop no
# longer does.
mkdir -p "$T/bin" "$T/st/cq" "$T/st/auto"; printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> "%s/ghlog"\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"; : > "$T/ghlog"
cd "$T/co"; $G push -q origin HEAD:refs/heads/fix/9-x 2>/dev/null; mkdir -p runs/run-landed/ws; $G worktree add -q -b fix/9-x runs/run-landed/ws/w origin/main 2>/dev/null || $G worktree add -q -B fix/9-x runs/run-landed/ws/w HEAD
$G -C runs/run-landed/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m fix; echo '{"event": "verdict"}' > runs/run-landed/record.jsonl
mkdir -p runs/run-moved/ws; $G worktree add -q -b fix/8-y runs/run-moved/ws/w HEAD; $G -C runs/run-moved/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m mine
$G -c user.email=a@b -c user.name=t commit -q --allow-empty -m other; $G push -q origin HEAD:refs/heads/fix/8-y 2>/dev/null; $G reset -q --hard HEAD~1; echo '{"event": "verdict"}' > runs/run-moved/record.jsonl
out=$(bash "$T/s/wtclean.sh" 2>&1)
case "$out" in *"WT UNPUSHED run-landed"*) ok "P: a finished run with commits on no remote branch is kept and reported";; *) ko "P: run-landed not reported: $out";; esac
remote=$($G ls-remote origin refs/heads/fix/9-x | cut -c1-9); [ "$remote" != "$($G -C runs/run-landed/ws/w rev-parse HEAD | cut -c1-9)" ] && ok "P: the loop pushed nothing" || ko "P: the loop pushed the run's branch"
[ -d runs/run-landed/ws/w ] && ok "P: the tree with the unpushed work is kept" || ko "P: the tree with the unpushed work was removed"
[ ! -s "$T/ghlog" ] && ok "P: the pass reads nothing of GitHub" || ko "P: a gh call was made: $(cat "$T/ghlog")"
case "$out" in *"WT UNPUSHED run-moved"*) ok "P: a branch that moved on the remote is reported the same way, never force-pushed";; *) ko "P: run-moved not reported: $out";; esac
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
rm -rf "$T"; exit $fail
