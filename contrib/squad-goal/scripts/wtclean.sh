#!/bin/bash
# usage: wtclean.sh   (from watch.sh) Removes squad child worktrees under $SG_CHECKOUT/runs/*/ws once their run has a
# verdict, the tree is clean and HEAD is on a remote branch. The run folder and its dashboard server are left alone (the hub lists runs from live servers). A finished worktree
# whose HEAD is on no remote branch (a fix run that landed without pushing) is kept and reported once as WT UNPUSHED.
. "$(dirname "$0")/env.sh"; G=/usr/bin/git; seen="$SG_STATE/wt-unpushed.txt"; touch "$seen"
cd "$SG_CHECKOUT" || exit 0
$G worktree list --porcelain | sed -n 's#^worktree \(.*/runs/run-[^/]*/ws/.*\)#\1#p' | while read -r w; do
  run=${w%%/ws/*}   # only after the RUN's verdict: the run's land step reads every child workspace (removing one early fails it with land_workspace_missing)
  grep -q '"event": *"verdict"' "$run/record.jsonl" 2>/dev/null || continue
  [ -z "$($G -C "$w" status --porcelain 2>/dev/null)" ] || continue
  sha=$($G -C "$w" rev-parse HEAD 2>/dev/null) || continue
  own=$($G -C "$w" branch --show-current)
  # kept only when HEAD is on no remote branch and on no other local branch (a run that made no commit sits on its base)
  if [ -z "$($G branch -r --contains "$sha" 2>/dev/null | head -1)" ] && [ -z "$($G branch --contains "$sha" --format='%(refname:short)' 2>/dev/null | grep -vxF "${own:-//}" | head -1)" ]; then
    grep -qx "$w" "$seen" || { echo "WT UNPUSHED ${run##*/} ${sha:0:9}: finished run with commits on no remote branch; kept"; echo "$w" >> "$seen"; }; continue; fi
  b=$($G -C "$w" branch --show-current); $G worktree remove "$w" && echo "WT CLEAN ${run##*/} ${b:-detached} ${sha:0:9}"
  case $b in ""|feat/*|fix/*|docs/*) ;; *) $G branch -D "$b" >/dev/null 2>&1;; esac   # throwaway local names only
done
# acceptance-command trees (acpr.sh) left by a killed gate: drop any older than 2 h
$G worktree list --porcelain | sed -n 's#^worktree \(.*/acpr\.[^/]*\)$#\1#p' | while read -r w; do
  [ -n "$(find "$w" -maxdepth 0 -mmin +120 2>/dev/null)" ] && $G worktree remove --force "$w" && echo "WT CLEAN acpr ${w##*/}"; done
# fork-only issue worktrees ($SG_FORK_DIR, ~/wt-as-<n>, ~/wt-fr-<n>): once the issue is closed, the tree clean and its branch
# merged into $SG_FORK_BASE, remove the tree and delete the branch (git branch -d: refuses an unmerged one)
if [ -n "$SG_FORK_DIR" ] && [ -d "$SG_FORK_DIR" ]; then
  $G -C "$SG_FORK_DIR" worktree list --porcelain | sed -En 's#^worktree (.*/wt-(as|fr)-[0-9]+)$#\1#p' | while read -r w; do
    n=${w##*-}; b=$($G -C "$w" branch --show-current 2>/dev/null)
    [ -z "$($G -C "$w" status --porcelain 2>/dev/null)" ] || continue
    [ "$(gh api repos/$SG_REPO/issues/$n -q .state 2>/dev/null)" = closed ] || continue
    [ -z "$b" ] || $G -C "$SG_FORK_DIR" merge-base --is-ancestor "$b" "$SG_FORK_BASE" || continue
    $G -C "$SG_FORK_DIR" worktree remove "$w" && { [ -n "$b" ] && $G -C "$SG_FORK_DIR" branch -d "$b" >/dev/null 2>&1; echo "WT CLEAN fork #$n ${b:-detached}"; }
  done; $G -C "$SG_FORK_DIR" worktree prune
fi
# PR worktrees under ~ (git worktree add ~/wt-<n>-*): drop once the branch's PR is merged and the tree clean
$G worktree list --porcelain | awk '/^worktree /{w=$2} /^branch /{sub("refs/heads/","",$2); print w, $2}' | while read -r w b; do
  case $w in "$HOME"/wt-*) ;; *) continue;; esac
  [ -z "$($G -C "$w" status --porcelain 2>/dev/null)" ] || continue
  [ "$(gh api "repos/$SG_REPO/pulls?state=closed&head=$SG_OWNER:$b&per_page=1" -q '.[0].merged_at // ""' 2>/dev/null)" != "" ] || continue
  $G worktree remove "$w" && $G branch -D "$b" >/dev/null 2>&1 && echo "WT CLEAN pr-merged $b"
done
$G worktree prune
