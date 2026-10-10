#!/bin/bash
# usage: wtclean.sh   (from watch.sh) Removes squad child worktrees under $SG_CHECKOUT/runs/*/ws once their run has a
# verdict, the tree is clean and HEAD is on a remote branch. The run folder and its dashboard server are left alone (the hub lists runs from live servers). A finished worktree
# whose HEAD is on no remote branch (a fix run that landed without pushing) is kept and reported once as WT UNPUSHED.
. "$(dirname "$0")/env.sh"; G=/usr/bin/git; seen="$SG_STATE/wt-unpushed.txt"; touch "$seen"
cd "$SG_CHECKOUT" || exit 0
$G worktree list --porcelain | sed -n 's#^worktree \(.*/runs/run-[^/]*/ws/.*\)#\1#p' | while read -r w; do
  run=${w%%/ws/*}; grep -q '"event": *"verdict"' "$run/record.jsonl" 2>/dev/null || continue
  [ -z "$($G -C "$w" status --porcelain 2>/dev/null)" ] || continue
  sha=$($G -C "$w" rev-parse HEAD 2>/dev/null) || continue
  own=$($G -C "$w" branch --show-current)
  # kept only when HEAD is on no remote branch and on no other local branch (a run that made no commit sits on its base)
  if [ -z "$($G branch -r --contains "$sha" 2>/dev/null | head -1)" ] && [ -z "$($G branch --contains "$sha" --format='%(refname:short)' 2>/dev/null | grep -vxF "${own:-//}" | head -1)" ]; then
    grep -qx "$w" "$seen" || { echo "WT UNPUSHED ${run##*/} ${sha:0:9}: finished run with commits on no remote branch; kept"; echo "$w" >> "$seen"; }; continue; fi
  b=$($G -C "$w" branch --show-current); $G worktree remove "$w" && echo "WT CLEAN ${run##*/} ${b:-detached} ${sha:0:9}"
  case $b in ""|feat/*|fix/*|docs/*) ;; *) $G branch -D "$b" >/dev/null 2>&1;; esac   # throwaway local names only
done
$G worktree prune
