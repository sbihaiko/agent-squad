#!/bin/bash
# usage: promote-main.sh      OWNER COMMAND: fast-forward main to the stg tip (ADR-0145). No loop script runs it.
#
# Refuses, and changes nothing, when main is not an ancestor of stg: main then holds work stg lacks, and a fast-forward would
# lose it. The push is plain, never forced. Exit 0: main is at the stg tip. Exit 1: refused, with the reason on stderr.
# $SG_PROMOTE_REMOTE overrides the remote. The work happens in a throwaway clone.
remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}
export GIT_TERMINAL_PROMPT=0
W=$(mktemp -d) || exit 1; trap 'rm -rf "$W"' EXIT
g() { /usr/bin/git -C "$W" "$@"; }
no() { echo "promote-main: $*" >&2; exit 1; }
g init -q . 2>/dev/null; g remote add origin "$remote"
o=$(g fetch -q origin "+refs/heads/main:refs/remotes/origin/main" "+refs/heads/stg:refs/remotes/origin/stg" 2>&1) || no "fetch failed: $(printf '%s' "$o" | tail -1)"
tip=$(g rev-parse origin/stg) || no "no stg branch"
[ "$(g rev-parse origin/main)" = "$tip" ] && { echo "main is already at stg $tip"; exit 0; }
g merge-base --is-ancestor origin/main "$tip" || no "refused: main is not an ancestor of stg, so main holds work stg lacks"
o=$(g push -q origin "$tip:refs/heads/main" 2>&1) || no "the push to main was refused: $(printf '%s' "$o" | tail -1)"
echo "PROMOTED main $tip"
