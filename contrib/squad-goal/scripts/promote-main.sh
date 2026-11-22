#!/bin/bash
# usage: promote-main.sh [<sha>]   fast-forward main to the stg tip, or to <sha> (ADR-0145, ADR-0173).
#   no argument: the owner command. main moves to the stg tip.
#   <sha>: the stg-to-main pass (promote-main-pass.sh) names the sha whose complete tier is green. The sha must be on the
#     first-parent chain of stg, so main only ever takes a commit stg itself stood at, and stg may already be ahead of it.
#     The script proves the sha itself before it pushes (#2039): it reads the sha's complete-tier check runs through
#     mainproof.sh, the same reader promote-main-pass.sh uses, and refuses the move unless every job is green, naming the
#     missing, red or unproven job. The pass's verdict is no substitute: a hand run trusts no caller. SG_PROMOTE_UNPROVEN=owner
#     is the only override, and the script prints the line that says it used it. The no-argument owner command reads no tier.
# Refuses, and changes nothing, when main is not an ancestor of the target: main then holds work the target lacks, and a
# fast-forward would lose it. The push is plain, never forced. Exit 0: main is at the target. Exit 1: refused, with the reason
# on stderr. Exit 3: this process cannot reach the credential the push needs, and the one line on stderr names it.
# The branches are the profile's: SG_MAIN_BRANCH (main) and SG_STG_BRANCH (stg). $SG_PROMOTE_REMOTE overrides the remote.
# The work happens in a throwaway clone.
remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}
main=${SG_MAIN_BRANCH:-main}; stg=${SG_STG_BRANCH:-stg}
export GIT_TERMINAL_PROMPT=0
d=$(cd "$(dirname "$0")" && pwd)
W=$(mktemp -d) || exit 1; trap 'rm -rf "$W"' EXIT
cred=${SG_PROMOTE_CREDENTIAL_HELPER:-osxkeychain}   # the fallback helper the push names (#2126)
own="!$d/promote-credential.sh"                    # the gh token, read by this process (#2126)
g() { /usr/bin/git -C "$W" -c credential.helper= -c "credential.helper=$own" -c "credential.helper=$cred" "$@"; }   # `=` resets the inherited helpers
no() { echo "promote-main: $*" >&2; exit 1; }
# (#2702) The promotion pass emits one release event when main moves: `prod_release`, which a subscribed channel announces.
# The record holds the event id and the items the release carries, and `voiced` reads it. A record that cannot be written costs
# the announcement alone: main already moved, so it never changes this exit code.
emit_release() {  # <event> <key> <from> <to>
  local event=$1 key=$2 args=() l out
  while IFS= read -r l; do [ -n "$l" ] && args+=(--item "$l"); done <<< "$(g log --no-merges --format='%h %s' "$3..$4" 2>/dev/null)"
  [ ${#args[@]} -gt 0 ] || args=(--item "$4")
  out=$(python3 "$d/../../voice/release_events.py" emit --event "$event" --key "$key" "${args[@]}" 2>&1) \
    || { echo "release: $event not emitted: $(printf '%s' "$out" | tail -1)" >&2; return 0; }
  echo "release: $out"
}
# The credential (#1896, #2126). The push names two helpers and no inherited one: the gh token that promote-credential.sh reads
# under this process's environment, then the fallback. A helper that answers nothing leaves git without a username, and
# GIT_TERMINAL_PROMPT=0 forbids the prompt, so the push dies with `could not read Username`. This script proves nothing about a
# credential before it uses one: a probe resolves the helpers a second time, under its own rules, and a probe that fails where
# the push would succeed invents an environment defect. The only proof is the push or fetch git itself reports as an
# authentication failure; then one line names both helpers and what could not be reached, on a code of its own. The caller
# knows the credential is the whole of it and leaves its work for a process that can reach it.
auth_failure() {  # <git's output>: git failed at the credential, never at the repository
  case "$1" in
    *"could not read Username"*|*"could not read Password"*|*"Authentication failed"*|*"terminal prompts disabled"*) return 0;;
  esac
  return 1
}
redacted() {  # <url>: scheme://host, with no userinfo, so a credential written into the URL never reaches the line
  case "$1" in
    *://*) local s=${1%%://*} r=${1#*://} hp
      hp=${r%%/*}; case "$hp" in *@*) hp=${hp#*@};; esac
      printf '%s://%s' "$s" "$hp";;
    *) printf '%s' "$1";;
  esac
}
no_credential() {  # <git's output>: one line that names the credential this process could not reach
  echo "promote-main: nothing was pushed: this process cannot reach the credential for $(redacted "$remote") (git's credential helpers: $own, then $cred), and git said: $(printf '%s' "$1" | tail -1); $main is where it was" >&2
  exit 3
}
g init -q . 2>/dev/null; g remote add origin "$remote"
o=$(g fetch -q origin "+refs/heads/$main:refs/remotes/origin/$main" "+refs/heads/$stg:refs/remotes/origin/$stg" 2>&1) \
  || { auth_failure "$o" && no_credential "$o"; no "fetch failed: $(printf '%s' "$o" | tail -1)"; }
tip=$(g rev-parse "origin/$stg") || no "no $stg branch"
what=$stg; named=
if [ -n "${1:-}" ]; then
  sha=$(g rev-parse --verify -q "$1^{commit}") || no "refused: $1 is not a commit of $stg"
  g rev-list --first-parent "origin/$stg" | /usr/bin/grep -qxF "$sha" || no "refused: $sha is not on the first-parent chain of $stg"
  tip=$sha; what=$sha; named=1
fi
old_main=$(g rev-parse "origin/$main") || no "no $main branch"
[ "$old_main" = "$tip" ] && { echo "$main is already at $what $tip"; exit 0; }
g merge-base --is-ancestor "origin/$main" "$tip" || no "refused: $main is not an ancestor of $what, so $main holds work $what lacks"
# The complete tier of the named sha (#2039, ADR-0173). The sha form is the pass's call, but a hand run is the same call,
# so the script proves the sha itself and trusts no caller: one reader, mainproof.sh, the one promote-main-pass.sh uses.
# A job that is missing, red or unproven holds main; the refusal names it. A read that fails proves nothing and holds main the
# same way. SG_PROMOTE_UNPROVEN=owner is the only override, and the script says on its own line that it used it.
if [ -n "$named" ]; then
  . "$d/ghcall.sh"; . "$d/mainproof.sh"
  rows=$(sg_complete_proof "$tip" 2>/dev/null); prc=$?
  bad=
  if [ "$prc" != 0 ]; then bad=${rows:-the complete tier of $tip proves nothing}
  else bad=$(printf '%s\n' "$rows" | awk -F'\t' 'NF && $2 != "green" { printf "%s%s (%s %s)", sep, $1, $2, ($3 != "" ? $3 : "no run"); sep = ", " }')
  fi
  if [ -n "$bad" ]; then
    if [ "${SG_PROMOTE_UNPROVEN:-}" = owner ]; then
      echo "promote-main: SG_PROMOTE_UNPROVEN=owner overrides the complete-tier proof of $tip: $bad"
    else
      no "refused: no green complete tier proves $tip: $bad; $main stays where it is"
    fi
  fi
fi
o=$(g push -q origin "$tip:refs/heads/$main" 2>&1) \
  || { auth_failure "$o" && no_credential "$o"; no "the push to $main was rejected: $(printf '%s' "$o" | tail -1)"; }
echo "PROMOTED $main $tip"
emit_release prod_release "prod_release:$tip" "$old_main" "$tip"
