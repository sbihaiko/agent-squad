#!/bin/bash
# usage: acpr.sh <branch> -- <command...>   A squad run's acceptance command for work pushed to a work branch.
# The final gate (ac-verify) runs in the run's root workspace, which is the main checkout, not the child's worktree;
# so this checks out origin/<branch> into a throwaway detached worktree, runs the command there and removes it.
# Exit: the command's exit code; 2 when the branch cannot be fetched or checked out.
set -uo pipefail
. "$(dirname "$0")/env.sh"; b=${1:?usage: acpr.sh <branch> -- <command...>}; shift; [ "${1:-}" = -- ] && shift
G=/usr/bin/git; C=$SG_CHECKOUT
$G -C "$C" fetch -q origin "$b" || { echo "acpr: cannot fetch origin/$b" >&2; exit 2; }
t=$(mktemp -d "${TMPDIR:-/tmp}/acpr.XXXXXX"); rmdir "$t"
$G -C "$C" worktree add -q --detach "$t" "origin/$b" || { echo "acpr: cannot check out origin/$b" >&2; exit 2; }
trap '$G -C "$C" worktree remove --force "$t" 2>/dev/null' EXIT; trap 'exit 143' TERM INT HUP   # a gate timeout kills us: still drop the tree
echo "acpr: origin/$b @ $($G -C "$t" rev-parse --short=9 HEAD): $*"
(cd "$t" && bash -c "$*"); rc=$?
exit $rc
