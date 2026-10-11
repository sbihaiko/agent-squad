#!/bin/bash
# usage: . forkonly.sh   (then sg_fork_only <issue>, sg_fork_preamble)   or   forkonly.sh <issue>  (exit 0 = fork-only)
# The ONE rule that decides fork-only work (#1411): the issue carries the `fork-only` label, or (fallback) its body has a
# "Fork-only work" line. Every request builder (autobug, autofrontier, autofix, forkclose's fix/conflict builders, mkreq)
# asks this rule, so a fork-only issue never gets the MesenAI preamble (base origin/<base>, fix/<N>, open the PR, prepush.sh).
sg_fork_only() { gh api "repos/$SG_REPO/issues/$1" 2>/dev/null | jq -e '([.labels[]?.name]|index("fork-only") != null) or ((.body // "")|contains("Fork-only work"))' >/dev/null 2>&1; }
# The fork preamble: all a fork-only child is told about where its work goes. Nothing in it mentions a PR to open, so nothing contradicts it.
sg_fork_preamble() { local br=${SG_FORK_BRANCH:-gui/%n}; br=${br//%n/<N>}
  echo "FORK-ONLY (label fork-only): this work lands in the agent-squad local fork, never in $SG_REPO. Create the worktree from ${SG_FORK_BASE:-local/next}: \`git -C ${SG_FORK_DIR:-<fork checkout>} worktree add ~/wt-as-<N> -b $br ${SG_FORK_BASE:-local/next}\` (N = the issue number) and work only there. Commit locally on $br; never push; never open a PR; never ask where a PR goes: the local commit on $br is the deliverable. Keep the worktree. End your result with the commands you ran as \`cd ~/wt-as-<N> && <command>\`."; }
[ "${BASH_SOURCE[0]}" = "$0" ] && { . "$(dirname "$0")/env.sh"; sg_fork_only "$1"; }
