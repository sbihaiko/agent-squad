#!/bin/bash
# usage: wtclean.sh   (from watch.sh) Removes squad child worktrees under $SG_CHECKOUT/runs/*/ws once their run has a
# verdict (or the run is a zombie: no verdict, idle 20 min, no process inside), the tree is clean and HEAD is on a remote branch. The run folder and its dashboard server are left alone (the hub lists runs from live servers). A finished worktree
# whose HEAD is on no remote branch (a fix run that landed without pushing) is kept and reported once as WT UNPUSHED.
. "$(dirname "$0")/env.sh"; G=/usr/bin/git; seen="$SG_STATE/wt-unpushed.txt"; touch "$seen"
cd "$SG_CHECKOUT" || exit 0
cwds=$(/usr/sbin/lsof -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')   # every live process's cwd, for the zombie test
$G worktree list --porcelain | sed -n 's#^worktree \(.*/runs/run-[^/]*/ws/.*\)#\1#p' | while read -r w; do
  run=${w%%/ws/*}   # only after the RUN's verdict: the run's land step reads every child workspace (removing one early fails it with land_workspace_missing)
  # no verdict yet: only a ZOMBIE run (record.jsonl idle >20 min and no process whose cwd is inside the run folder) is cleaned, under the same clean-tree / HEAD-on-a-remote-branch rules below
  grep -q '"event": *"verdict"' "$run/record.jsonl" 2>/dev/null || { [ -n "$(find "$run/record.jsonl" -mmin +20 2>/dev/null)" ] && ! printf '%s\n' "$cwds" | grep -qF "$run/" || continue; }
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
# L: loose detached trees (acpr.* gate trees, ~/wt-* scratch trees; never the e2e trees): drop one that is clean, 10 min old, has no process inside and whose HEAD is on a remote branch (nothing is lost); a dirty or unpushed one is only reported once as WT LOOSE
$G worktree list --porcelain | awk '/^worktree /{w=$2} /^detached/{print w}' | while read -r w; do
  case $w in "$HOME"/wt-*|*/acpr.*) ;; *) continue;; esac
  case $w in "${SG_E2E_DIR:-//}"|"${SG_E2E_BUILD_DIR:-//}"|"$HOME"/wt-e2e*) continue;; esac   # the e2e trees are reused between verifications: an idle one is not a loose one (a run lost them mid-verification, 2026-10-10)
  [ -d "$w" ] || continue; [ -n "$(find "$w" -maxdepth 0 -mmin +10 2>/dev/null)" ] || continue
  ! printf '%s\n' "$cwds" | grep -qF "$w" || continue
  sha=$($G -C "$w" rev-parse HEAD 2>/dev/null) || continue
  if [ -z "$($G -C "$w" status --porcelain 2>/dev/null)" ] && [ -n "$($G branch -r --contains "$sha" 2>/dev/null | head -1)" ]; then
    $G worktree remove "$w" && echo "WT CLEAN loose ${w##*/} ${sha:0:9}"
  else grep -qx "loose $w" "$seen" || { echo "WT LOOSE ${w##*/} ${sha:0:9}: detached, dirty or on no remote branch; kept"; echo "loose $w" >> "$seen"; }; fi
done
# fork-only issue worktrees ($SG_FORK_DIR, ~/wt-as-<n>, ~/wt-fr-<n>): once the issue is closed, the tree clean and its branch
# merged into $SG_FORK_BASE, remove the tree and delete the branch (git branch -d: refuses an unmerged one)
if [ -n "$SG_FORK_DIR" ] && [ -d "$SG_FORK_DIR" ]; then
  $G -C "$SG_FORK_DIR" worktree list --porcelain | sed -En 's#^worktree (.*/wt-(as|fr)-[0-9]+)$#\1#p' | while read -r w; do
    n=${w##*-}; b=$($G -C "$w" branch --show-current 2>/dev/null)
    live=0; for r in "$SG_CHECKOUT"/runs/run-*/record.jsonl; do   # a live run's final gate still cds into it
      grep -q '"event": *"verdict"' "$r" || ! grep -qF "${w##*/}" "$r" || { live=1; break; }; done
    [ $live = 0 ] || continue
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
