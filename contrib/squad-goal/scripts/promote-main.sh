#!/bin/bash
# usage: promote-main.sh [<sha>]   fast-forward main to the stg tip, or to <sha> (ADR-0145, ADR-0173).
#   no argument: the owner command. main moves to the stg tip.
#   <sha>: the stg-to-main pass (promote-main-pass.sh) names the sha whose complete tier is green. The sha must be on the
#     first-parent chain of stg, so main only ever takes a commit stg itself stood at, and stg may already be ahead of it.
# Refuses, and changes nothing, when main is not an ancestor of the target: main then holds work the target lacks, and a
# fast-forward would lose it. The push is plain, never forced. Exit 0: main is at the target. Exit 1: refused, with the reason
# on stderr. Exit 3: this process cannot reach the credential the push needs, and the one line on stderr names it.
# The branches are the profile's: SG_MAIN_BRANCH (main) and SG_STG_BRANCH (stg). $SG_PROMOTE_REMOTE overrides the remote.
# The work happens in a throwaway clone.
remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}
main=${SG_MAIN_BRANCH:-main}; stg=${SG_STG_BRANCH:-stg}
export GIT_TERMINAL_PROMPT=0
W=$(mktemp -d) || exit 1; trap 'rm -rf "$W"' EXIT
g() { /usr/bin/git -C "$W" "$@"; }
no() { echo "promote-main: $*" >&2; exit 1; }
# The credential (#1896). git reads it from the credential helper of the profile, and a helper whose store this process
# cannot reach answers nothing: gh keeps its token in the login keychain, and the self-hosted runner that fires the CI hook
# is a launchd service job, whose security session carries no login keychain. GIT_TERMINAL_PROMPT=0 then forbids the
# prompt, and the push dies with a bare `could not read Username`. So the credential is asked for before the fetch, and a
# process that cannot reach it names it, in one line, on a code of its own: the caller knows the credential and the push
# are the whole of it, and leaves its work for a process that can reach it.
host=; case "$remote" in http://*|https://*) h=${remote#*://}; host=${h%%/*};; esac
helper=; [ -n "$host" ] && helper=$(/usr/bin/git config --get-urlmatch credential.helper "$remote" 2>/dev/null | tail -1)
[ -n "$helper" ] || helper=$(/usr/bin/git config --get-all credential.helper 2>/dev/null | tail -1)
no_credential() {
  echo "promote-main: nothing was pushed: this process cannot reach the $host credential the push to $main needs, and git's credential helper (${helper:-none configured}) answered none; $main is where it was" >&2
  exit 3
}
if [ -n "$host" ]; then
  c=$(printf 'url=%s\n\n' "$remote" | GIT_ASKPASS=/bin/false /usr/bin/git credential fill 2>/dev/null) || c=
  case "$c" in *password=*) ;; *) no_credential;; esac
fi
g init -q . 2>/dev/null; g remote add origin "$remote"
o=$(g fetch -q origin "+refs/heads/$main:refs/remotes/origin/$main" "+refs/heads/$stg:refs/remotes/origin/$stg" 2>&1) \
  || case "$o" in *"could not read Username"*|*"terminal prompts disabled"*) no_credential;; *) no "fetch failed: $(printf '%s' "$o" | tail -1)";; esac
tip=$(g rev-parse "origin/$stg") || no "no $stg branch"
what=$stg
if [ -n "${1:-}" ]; then
  sha=$(g rev-parse --verify -q "$1^{commit}") || no "refused: $1 is not a commit of $stg"
  g rev-list --first-parent "origin/$stg" | /usr/bin/grep -qxF "$sha" || no "refused: $sha is not on the first-parent chain of $stg"
  tip=$sha; what=$sha
fi
[ "$(g rev-parse "origin/$main")" = "$tip" ] && { echo "$main is already at $what $tip"; exit 0; }
g merge-base --is-ancestor "origin/$main" "$tip" || no "refused: $main is not an ancestor of $what, so $main holds work $what lacks"
o=$(g push -q origin "$tip:refs/heads/$main" 2>&1) \
  || case "$o" in *"could not read Username"*|*"could not read Password"*|*"Authentication failed"*|*"terminal prompts disabled"*) no_credential;; \
       *) no "the push to $main was rejected: $(printf '%s' "$o" | tail -1)";; esac
echo "PROMOTED $main $tip"
