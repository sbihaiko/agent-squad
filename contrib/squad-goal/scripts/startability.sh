#!/bin/bash
# usage: . startability.sh   (then sg_not_startable <issue>, sg_fork_branch <issue>, sg_issue_worktree_note <issue>)
# The shared read and helpers of every dispatcher: an issue the loop marked not startable is not dispatched again, the
# branch an issue's work lives on, and the note a request builder hands a child whose worktree an earlier run left behind.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ghcall.sh"
export SG_GH_PASS=landing   # the pass that spends the GitHub calls below, named in the call log (ADR-0277); a dispatcher that sources this file exports its own pass name after it
# The branch an issue's work lives on: ONE setting, SG_FORK_BRANCH (%n = the issue). The dispatchers, stack.sh and the
# launch that makes the issue worktree all name it through this function, so the branch a child is given and the branch
# the worktree is made on cannot come from two sources.
sg_fork_branch() { local br=${SG_FORK_BRANCH:-gui/%n}; echo "${br//%n/$1}"; }
sg_fork_branch_pattern() { echo "${SG_FORK_BRANCH:-gui/%n}"; }
# An old run can leave the issue worktree behind. The dispatcher owns that path, so it tells the next child when the
# exact expected checkout is safe to reuse. Every other state stays intact and is reported as a blocker (#65).
sg_issue_worktree_note() {   # <issue>: print child instructions only when worktrees/child/gui-<issue> already exists
  local n=$1 path branch root current state
  path="${SG_CHECKOUT%/}/worktrees/child/gui-$n"
  branch=$(sg_fork_branch "$n")
  [ -e "$path" ] || return 0
  path=$(cd "$path" 2>/dev/null && pwd -P) || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is unreadable. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "${SG_CHECKOUT%/}/worktrees/child/gui-$n"; return
  }
  root=$(git -C "$path" rev-parse --show-toplevel 2>/dev/null) || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is unreadable. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path"; return
  }
  root=$(cd "$root" 2>/dev/null && pwd -P) || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is unreadable. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path"; return
  }
  [ "$root" = "$path" ] || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is not its own checkout. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path"; return
  }
  current=$(git -C "$path" symbolic-ref --quiet --short HEAD 2>/dev/null) || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is detached; expected branch %s. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path" "$branch"; return
  }
  state=$(git -C "$path" status --porcelain --untracked-files=all 2>/dev/null) || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s cannot be checked; expected branch %s. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path" "$branch"; return
  }
  [ "$current" = "$branch" ] && [ -z "$state" ] && {
    printf 'REUSE ISSUE WORKTREE: Use the clean existing worktree at %s on branch %s. Do not create a fresh worktree. Work in it without deleting or resetting it.\n' "$path" "$branch"; return
  }
  printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is on branch %s; expected branch %s%s. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path" "$current" "$branch" "${state:+ and it is dirty}"
}
# An issue the loop itself has marked not startable (#1858): a run that ended failed on it SG_HEALTH_REPEAT_COUNT times running
# leaves a `SQUAD-NOT-STARTABLE:` line among its comments, and every dispatch path reads that line before it starts -- so the loop
# cannot feed itself the same input again. health.sh writes it; autobug.sh and autofrontier.sh read it, once per candidate that got
# past their cheap filters. Absent the marker, a comments read costs one request and changes nothing.
sg_not_startable() {
  local out rc
  out=$(gh_api "repos/$SG_REPO/issues/$1/comments?per_page=100" -q '.[].body'); rc=$?
  # A refused read is not "no marker": it is no answer at all, and the caller's own guards still apply (#1855). Reading the
  # refusal as a negative here would be the same defect the wrapper in autobug.sh exists to stop.
  [ "$rc" -eq 0 ] || return 1
  printf '%s\n' "$out" | /usr/bin/grep -q '^SQUAD-NOT-STARTABLE:'
}
