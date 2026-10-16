#!/bin/bash
# usage: wtclean.sh   (from watch.sh) Removes squad child worktrees under $SG_CHECKOUT/runs/*/ws once their run has a
# verdict (or the run is a zombie: no verdict, idle 20 min, no process inside), the tree is clean and HEAD is on a remote branch. The run folder and its dashboard server are left alone (the hub lists runs from live servers). A finished worktree
# whose HEAD is on no remote branch (a run that landed commits without pushing) is kept and reported once as WT UNPUSHED: the
# work is preserved and a person, or the next run, decides what to do with it.
# A tree that holds a checked out submodule is its own class: git refuses to remove such a tree whole (`fatal: working trees
# containing submodules cannot be moved or removed`, a line with no run id in it), so every removal here carries the flag that
# case needs, and the line names the run or the tree. One the flag still cannot free is reported once as WT KEPT submodule.
. "$(dirname "$0")/env.sh"; G=/usr/bin/git; seen="$SG_STATE/wt-unpushed.txt"; touch "$seen"
# The tally the hub's footer reads as `wt 10/21`: `held` per child tree this pass saw under the two child directories
# (the set the footer counts), `free` per one it removed. A `while read` behind a pipe is a subshell, so the count lives
# in a file and never in a variable.
tally="$SG_STATE/wt-tally.$$"; : > "$tally"
# in_use <path>: a live process is working in this tree or below it. Two rules the liveness tests need and a plain
# `grep -F "$w/"` does not keep: a process whose cwd IS the tree carries no trailing slash, and a substring test reads
# `wt-1` inside `wt-10`. `lsof` answers with resolved paths, so the caller resolves $SG_CHECKOUT first.
in_use() { printf '%s\n' "$cwds" | awk -v p="$1" '$0==p || index($0,p"/")==1 {f=1} END{exit !f}'; }
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# has_sub <tree>: the tree holds a submodule git has checked out — the one state in which `git worktree remove` refuses the
# whole tree whatever else is true of it: `fatal: working trees containing submodules cannot be moved or removed`.
# `git submodule status` marks an uninitialized one with a leading `-`, a populated one with anything else (a space at the
# recorded commit, `+` or `U` otherwise).
has_sub() { $G -C "$1" submodule status 2>/dev/null | grep -qE '^[ +U]'; }
# wt_remove <repo> <tree>: the one worktree removal, so that refusal is answered in one place. The caller has already proved
# the tree clean, unused and landed, and a submodule's own dirt reads as ` M sub` in that same status check, so the flag the
# populated case needs loses nothing. The class is read BEFORE the attempt: a removal that fails can take the worktree's own
# registration with it, and then the tree can no longer say what it is. Returns 0 when the tree is gone, 2 when a tree
# holding a checked out submodule would not go even with that flag, 1 otherwise; wt_forced says the flag was used.
wt_remove() { wt_forced=; wt_sub=0; has_sub "$2" && wt_sub=1
  $G -C "$1" worktree remove "$2" 2>/dev/null && return 0
  [ $wt_sub = 1 ] || return 1
  $G -C "$1" worktree remove --force "$2" 2>/dev/null || return 2
  wt_forced=1; return 0; }   # empty unless the flag was used: the lines test it with ${wt_forced:+...}
now=$(date +%s)
cd "$SG_CHECKOUT" || exit 0
SG_CHECKOUT=$(pwd -P)   # resolved: every liveness test below compares a path with a process cwd, and lsof answers with the resolved one. Under a symlinked checkout (/var -> /private/var, a linked home) the unresolved form never matched, so a tree a live process was working in read as unused.
cwds=$("$SG_LSOF" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')   # every live process's cwd, for the zombie test
$G worktree list --porcelain | sed -n 's#^worktree \(.*/runs/run-[^/]*/ws/.*\)#\1#p' | while read -r w; do
  run=${w%%/ws/*}   # only after the RUN's verdict: the run's land step reads every child workspace (removing one early fails it with land_workspace_missing)
  # no verdict yet: only a ZOMBIE run (record.jsonl idle >20 min and no process whose cwd is inside the run folder) is cleaned, under the same clean-tree / HEAD-on-a-remote-branch rules below
  grep -q '"event": *"verdict"' "$run/record.jsonl" 2>/dev/null || { [ -n "$(find "$run/record.jsonl" -mmin +20 2>/dev/null)" ] && ! printf '%s\n' "$cwds" | grep -qF "$run/" || continue; }
  [ -z "$($G -C "$w" status --porcelain 2>/dev/null)" ] || continue
  sha=$($G -C "$w" rev-parse HEAD 2>/dev/null) || continue
  own=$($G -C "$w" branch --show-current)
  # kept only when HEAD is on no remote branch and on no other local branch (a run that made no commit sits on its base)
  if [ -z "$($G branch -r --contains "$sha" 2>/dev/null | head -1)" ] && [ -z "$($G branch --contains "$sha" --format='%(refname:short)' 2>/dev/null | grep -vxF "${own:-//}" | head -1)" ]; then
    grep -qx "$w" "$seen" && continue
    echo "WT UNPUSHED ${run##*/} ${sha:0:9}: finished run with commits on no remote branch; kept"; echo "$w" >> "$seen"; continue; fi
  b=$($G -C "$w" branch --show-current)
  wt_remove "$SG_CHECKOUT" "$w"; rc=$?
  if [ $rc = 0 ]; then echo "WT CLEAN ${run##*/} ${b:-detached} ${sha:0:9}${wt_forced:+ (submodule tree: removed with --force)}"
    case $b in ""|feat/*|fix/*|docs/*) ;; *) $G branch -D "$b" >/dev/null 2>&1;; esac   # throwaway local names only, and only once the tree is gone
  # the tree a populated submodule kept alive: reported once with its own reason, never as git's own nameless fatal
  elif [ $rc = 2 ]; then grep -qx "sub $w" "$seen" || { echo "WT KEPT submodule ${run##*/} ${sha:0:9} ${w}: a checked out submodule git refuses to remove, even with --force; kept"; echo "sub $w" >> "$seen"; }; fi
done
# C: the child trees the loop holds at $SG_CHECKOUT/worktrees/child/* (and .worktrees/*) — the set the hub's footer counts
# as `wt N` and the one the reader is looking at when he asks what can be freed. No pass touched them until now, so a landed
# issue left its tree behind for good (measured 2026-10-13: 20 trees, 10 of them already in origin/main). A tree goes only
# when nothing can be lost and nothing is using it: no live process whose cwd is inside it, a clean tree, and HEAD in the
# base (its work landed). Anything else is held, and the tally below carries the ratio the footer shows.
for rel in worktrees/child .worktrees; do
  [ -d "$SG_CHECKOUT/$rel" ] || continue
  for w in "$SG_CHECKOUT/$rel"/*; do
    [ -e "$w/.git" ] || continue                 # what `git worktree add` writes: a stray directory is not a worktree
    echo held >> "$tally"
    in_use "$w" && continue
    [ -z "$($G -C "$w" status --porcelain 2>/dev/null)" ] || continue
    [ -n "$SG_BASE" ] || continue
    $G -C "$w" merge-base --is-ancestor HEAD "origin/$SG_BASE" 2>/dev/null || continue
    # An active run folder inside the child itself owns this tree, even before it starts a process. The janitor drops a
    # missing folder from active-runs, so this pass only preserves a folder that still exists and has no verdict.
    live=0
    if [ -f "$SG_STATE/active-runs.txt" ]; then while read -r r; do
      rf=$(cd "$r" 2>/dev/null && pwd -P) || continue
      case $rf in "$w"/*) f="$rf/record.jsonl"; [ -f "$f" ] && ! grep -q '"event": *"verdict"' "$f" 2>/dev/null && { live=1; break; };; esac
    done < "$SG_STATE/active-runs.txt"; fi
    # A run in flight that names this tree is the other case a merged tree is still in use: the child may not have started, so
    # no process sits inside it yet. Same test as the fork rule below, and the same lesson (gui/1227).
    if [ $live = 0 ]; then for r in "$SG_CHECKOUT"/runs/run-*; do
      f="$r/record.jsonl"; [ -f "$f" ] || continue
      grep -q '"event": *"verdict"' "$f" 2>/dev/null && continue
      [ $(( now - $(mtime "$f") )) -lt $(( ${SG_FORK_ORPHAN_MINUTES:-20} * 60 )) ] || continue
      grep -qF "${w##*/}" "$f" "$r/request.txt" 2>/dev/null && { live=1; break; }
    done; fi
    [ $live = 0 ] || continue
    b=$($G -C "$w" branch --show-current); sha=$($G -C "$w" rev-parse --short=9 HEAD 2>/dev/null)
    wt_remove "$SG_CHECKOUT" "$w"; rc=$?
    if [ $rc = 0 ]; then echo free >> "$tally"; echo "WT CLEAN child ${w##*/} ${b:-detached} ${sha:-}${wt_forced:+ (submodule tree: removed with --force)}"
    elif [ $rc = 2 ]; then grep -qx "sub $w" "$seen" || { echo "WT KEPT submodule child ${w##*/} ${sha:-} ${w}: a checked out submodule git refuses to remove, even with --force; kept"; echo "sub $w" >> "$seen"; }; fi
  done
done
# acceptance-command trees (acpr.sh) left by a killed gate: drop any older than 2 h
$G worktree list --porcelain | sed -n 's#^worktree \(.*/acpr\.[^/]*\)$#\1#p' | while read -r w; do
  [ -n "$(find "$w" -maxdepth 0 -mmin +120 2>/dev/null)" ] && $G worktree remove --force "$w" && echo "WT CLEAN acpr ${w##*/}"; done
# L: loose detached trees (acpr.* gate trees, ~/wt-* scratch trees; never the e2e trees): drop one that is clean, 10 min old, has no process inside and whose HEAD is on a remote branch (nothing is lost); a dirty or unpushed one is only reported once as WT LOOSE
$G worktree list --porcelain | awk '/^worktree /{w=$2} /^detached/{print w}' | while read -r w; do
  case $w in "$HOME"/wt-*|*/acpr.*) ;; *) continue;; esac
  case $w in "${SG_E2E_DIR:-//}"|"${SG_E2E_BUILD_DIR:-//}"|"$HOME"/wt-e2e*) continue;; esac   # the e2e trees are reused between verifications: an idle one is not a loose one (a run lost them mid-verification, 2026-10-10)
  [ -d "$w" ] || continue; [ -n "$(find "$w" -maxdepth 0 -mmin +10 2>/dev/null)" ] || continue
  in_use "$w" && continue
  sha=$($G -C "$w" rev-parse HEAD 2>/dev/null) || continue
  if [ -z "$($G -C "$w" status --porcelain 2>/dev/null)" ] && [ -n "$($G branch -r --contains "$sha" 2>/dev/null | head -1)" ]; then
    wt_remove "$SG_CHECKOUT" "$w" && echo "WT CLEAN loose ${w##*/} ${sha:0:9}${wt_forced:+ (submodule tree: removed with --force)}"
  else grep -qx "loose $w" "$seen" || { echo "WT LOOSE ${w##*/} ${sha:0:9}: detached, dirty or on no remote branch; kept"; echo "loose $w" >> "$seen"; }; fi
done
# fork-only issue worktrees ($SG_FORK_DIR, ~/wt-as-<n>, ~/wt-fr-<n>): once the issue is closed, the tree clean and its branch
# merged into $SG_FORK_BASE, remove the tree and delete the branch (git branch -d: refuses an unmerged one)
if [ -n "$SG_FORK_DIR" ] && [ -d "$SG_FORK_DIR" ]; then
  $G -C "$SG_FORK_DIR" worktree list --porcelain | sed -En 's#^worktree (.*/wt-(as|fr)-[0-9]+)$#\1#p' | while read -r w; do
    n=${w##*-}; b=$($G -C "$w" branch --show-current 2>/dev/null)
    # Which run owns this tree? The tree's own name is the wrong key twice over: a run in flight never writes "wt-as-<n>" to its
    # record (that only appears when the gate writes its outcome), and "some run has no verdict yet" is true of every tree at
    # once, because one live run anywhere parks them all. What a run does carry is the branch it was launched for, so the test
    # is the landing pass's: a process inside the tree, or a run whose record moved in the orphan window and whose request names
    # gui/<n> or wt-(as|fr)-<n>. The old record-grep deleted a live run's worktree the moment a sibling issue closed, and every
    # ac_command then failed with "cd: No such file or directory" (gui/1227, run 45a682, 2026-10-10).
    live=0
    in_use "$w" && live=1
    if [ $live = 0 ]; then for r in "$SG_CHECKOUT"/runs/run-*; do
      f="$r/record.jsonl"; [ -f "$f" ] || continue
      grep -q '"event": *"verdict"' "$f" 2>/dev/null && continue   # a run that has ended owns nothing
      [ $(( now - $(mtime "$f") )) -lt $(( ${SG_FORK_ORPHAN_MINUTES:-20} * 60 )) ] || continue
      grep -qE "(^|[^0-9])gui/$n([^0-9]|$)|wt-(as|fr)-$n([^0-9]|$)" "$f" "$r/request.txt" 2>/dev/null && { live=1; break; }
    done; fi
    [ $live = 0 ] || continue
    [ -z "$($G -C "$w" status --porcelain 2>/dev/null)" ] || continue
    [ "$(gh api repos/$SG_REPO/issues/$n -q .state 2>/dev/null)" = closed ] || continue
    [ -z "$b" ] || $G -C "$SG_FORK_DIR" merge-base --is-ancestor "$b" "$SG_FORK_BASE" || continue
    wt_remove "$SG_FORK_DIR" "$w" && { [ -n "$b" ] && $G -C "$SG_FORK_DIR" branch -d "$b" >/dev/null 2>&1; echo "WT CLEAN fork #$n ${b:-detached}${wt_forced:+ (submodule tree: removed with --force)}"; }
  done; $G -C "$SG_FORK_DIR" worktree prune
fi
# Loose worktrees under ~ (git worktree add ~/wt-<n>-*): drop once the branch is in the landing branch ($SG_LAND_BRANCH, default dev),
# the tree is clean and nothing is ahead of it. The fact is git's own: the branch tip is an ancestor of origin/<landing branch>, so the
# force delete loses no commit. No pull request is read.
land=${SG_LAND_BRANCH:-dev}
$G worktree list --porcelain | awk '/^worktree /{w=$2} /^branch /{sub("refs/heads/","",$2); print w, $2}' | while read -r w b; do
  case $w in "$HOME"/wt-*) ;; *) continue;; esac
  [ -z "$($G -C "$w" status --porcelain 2>/dev/null)" ] || continue
  $G rev-parse -q --verify "refs/remotes/origin/$land" >/dev/null 2>&1 || continue   # no landing branch to compare with
  if $G merge-base --is-ancestor "$b" "origin/$land" 2>/dev/null; then
    wt_remove "$SG_CHECKOUT" "$w" && $G branch -D "$b" >/dev/null 2>&1 && echo "WT CLEAN landed $b (in $land at $($G rev-parse --short=9 "origin/$land"))${wt_forced:+ (submodule tree: removed with --force)}"
  else grep -qx "kept $w" "$seen" || { echo "WT KEPT $b: commits not in $land yet; nothing removed"; echo "kept $w" >> "$seen"; }; fi
done
$G worktree prune
# What the hub's footer reads: the tree count this pass saw in the child directories and how many of them it freed. The file
# is written last, so a pass killed halfway leaves the previous reading in place instead of a half one.
printf '{"total": %s, "cleanable": %s, "ts": %s}\n' \
  "$(grep -c '^held$' "$tally" 2>/dev/null || echo 0)" "$(grep -c '^free$' "$tally" 2>/dev/null || echo 0)" "$now" > "$SG_STATE/wt.json"
rm -f "$tally"
