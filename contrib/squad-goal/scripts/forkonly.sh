#!/bin/bash
# usage: . forkonly.sh   (then sg_fork_only <issue>, sg_fork_preamble)   or   forkonly.sh <issue>  (exit 0 = fork-only)
# The ONE rule that decides fork-only work (#1411): the issue carries the `fork-only` label, or (fallback) its body has a
# MARKER LINE "Fork-only work" (#1765) - a line of its own, bare or bulleted/bolded/quoted. Prose that merely names the
# phrase is not a marker: matching it anywhere sent work that belonged to MesenAI down the fork route, where no PR is opened.
# Every request builder (autobug, autofrontier, autofix, forkclose's fix/conflict builders, mkreq)
# asks this rule, so a fork-only issue never gets the MesenAI preamble (base origin/<base>, fix/<N>, open the PR, prepush.sh).
# The label is `squad-only` (renamed from `fork-only` on 2026-10-12, when the work stopped being local-only): the old
# spelling still reads, so an issue filed before the rename keeps the route it was filed with instead of silently
# defaulting to a MesenAI PR. The body line reads both spellings for the same reason.
sg_fork_only() { gh api "repos/$SG_REPO/issues/$1" 2>/dev/null | jq -e '([.labels[]?.name]|(index("squad-only") != null) or (index("fork-only") != null)) or ((.body // "")|test("(?m)^[[:space:]>*+-]*\\**([Ff]ork-only|[Ss]quad-only) work"))' >/dev/null 2>&1; }
# The branch a fork-only child commits on: ONE setting, SG_FORK_BRANCH (%n = the issue, #1836). sg_fork_branch <N> prints the
# branch the land resolves -- forkclose.sh merges exactly this ref -- and the request builders, stack.sh and the preamble all
# name it through this function, so the name a child is given and the name the close pass looks for cannot come from two
# sources. The default lives here and nowhere else: with the setting unset, both sides read gui/<N> instead of the child
# creating gui/<N> while the land resolves an empty ref no pass ever merges.
# The address seam (sg_resolve_repo, sg_same_repo, sg_land_target) lives beside this file. A copy of this file made without
# it still loads, with no seam: `if` and not `&&`, so a caller under `set -e` is not aborted.
if [ -f "$(dirname "${BASH_SOURCE[0]}")/address.sh" ]; then . "$(dirname "${BASH_SOURCE[0]}")/address.sh"; fi
sg_fork_branch() { local br=${SG_FORK_BRANCH:-gui/%n}; echo "${br//%n/$1}"; }
# An issue the loop itself has marked not startable (#1858): a run that ended failed on it SG_HEALTH_REPEAT_COUNT times running
# leaves a `SQUAD-NOT-STARTABLE:` line among its comments, and every dispatch path reads that line before it starts -- so the loop
# cannot feed itself the same input again. health.sh writes it; autobug.sh and autofrontier.sh read it, once per candidate that got
# past their cheap filters. Absent the marker, a comments read costs one request and changes nothing.
sg_not_startable() {
  local out rc
  out=$(gh api "repos/$SG_REPO/issues/$1/comments?per_page=100" -q '.[].body' 2>/dev/null); rc=$?
  # A refused read is not "no marker": it is no answer at all, and the caller's own guards still apply (#1855). Reading the
  # refusal as a negative here would be the same defect the wrapper in autobug.sh exists to stop.
  [ "$rc" -eq 0 ] || return 1
  printf '%s\n' "$out" | /usr/bin/grep -q '^SQUAD-NOT-STARTABLE:'
}
sg_fork_branch_pattern() { echo "${SG_FORK_BRANCH:-gui/%n}"; }
# The file paths a fork-only commit may touch, ONE source for the passes that read a branch's diff (forkrescue, forkstrand;
# #1837). A trailing / is a directory prefix, anything else is that exact root file. Prints one per line.
sg_fork_paths() { printf '%s\n' contrib/squad-goal/ .claude-plugin/ CHANGELOG.md; }
# The fork preamble: all a fork-only child is told about where its work goes. One route (#32): the child pushes its branch to
# the remote the fork checkout names (origin), and the land (forkclose.sh -> prland.sh) runs the local gate, opens the PR
# that names the issue and merges it on a green check. The child pushes that one branch and nothing else.
sg_fork_preamble() { local br; br=$(sg_fork_branch '<N>')
  echo "FORK-ONLY (label fork-only): this work lands in the agent-squad repository, never in $SG_REPO. Create the worktree from ${SG_FORK_BASE:-main}: \`git -C ${SG_FORK_DIR:-<fork checkout>} worktree add ~/wt-as-<N> -b $br ${SG_FORK_BASE:-main}\` (N = the issue number) and work only there. Commit on $br and push it to the remote origin of that checkout: \`git push origin $br\` is the one push you may make (never the base ${SG_FORK_BASE:-main}, never a force push). The land runs the local gate, opens the pull request that names the issue and merges it on a green check; never ask where a PR goes. Keep the worktree. End your result with the commands you ran as \`cd ~/wt-as-<N> && <command>\`."; }
# `if` and not `A && B`: the test failing is a condition here, so a caller running under `set -e` (mkreq.sh) can source
# this file instead of being aborted by the last line.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then . "$(dirname "$0")/env.sh"; sg_fork_only "$1"; fi
