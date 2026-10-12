#!/bin/bash
# usage: prepush.sh <pr> [dir]            the loop, before revci: checks the PR's pushed head (dir = $SG_CHECKOUT)
#        prepush.sh --local [body-file]   a run, in its worktree, before every push and before opening the PR
# Catches the process-only findings that used to cost a whole review round:
#   refs     "Closes/Fixes/Resolves #N" in the title or body (the loop's rule is "Refs #N", never a closing keyword)
#   unpushed (--local) commits on HEAD that the PR branch on origin does not have yet: push them, or the review sees an old head
#   cited    a repo path the body cites (`UI/x.cs`, scripts/y.py) that does not exist on the head: a file the body promises
#            but the PR does not carry (#1277: the tests the body named were never committed); lines naming the fork,
#            another repo, a URL or a removal are skipped, and so is a path outside this repo's top-level dirs (squad/, tmp/)
#   checks   $SG_PREPUSH_CHECKS, when the profile sets it, run in the tree with the touched files as $PREPUSH_FILES,
#            the pushed range as $PREPUSH_RANGE and the deliverable body as $PREPUSH_BODY (check-tdd-red.sh reads all three)
# Prints one "- <rule>: <detail>" line per failure; exit 0 when clean, 1 otherwise.
. "$(dirname "$0")/env.sh" 2>/dev/null; G=/usr/bin/git; fails=()
PATHRE='(\b|`)([A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(cs|cpp|h|hpp|py|sh|axaml|json|md|yml|yaml|xml|js|ts)\b'
if [ "${1:-}" = --local ]; then
  dir=.; head=HEAD; br=$($G branch --show-current); body=$( [ -n "${2:-}" ] && cat "$2" ); title=""
  [ -z "$body" ] && [ -n "$br" ] && { title=$(gh pr view "$br" --repo "$SG_REPO" --json title -q .title 2>/dev/null); body=$(gh pr view "$br" --repo "$SG_REPO" --json body -q .body 2>/dev/null); }
  if [ -n "$br" ] && $G rev-parse -q --verify "origin/$br" >/dev/null; then
    n=$($G rev-list --count "origin/$br..HEAD" 2>/dev/null); [ "${n:-0}" -gt 0 ] && fails+=("unpushed: $n commit(s) on HEAD not on origin/$br; git push origin HEAD:$br")
  fi
  base=$($G merge-base HEAD "origin/${SG_BASE:-main}" 2>/dev/null)
else
  pr=$1; dir=${2:-$SG_CHECKOUT}
  read -r br sha < <(gh api repos/$SG_REPO/pulls/$pr -q '.head.ref+" "+.head.sha' 2>/dev/null) || { echo "- prepush: PR #$pr not readable"; exit 0; }
  title=$(gh api repos/$SG_REPO/pulls/$pr -q .title 2>/dev/null); body=$(gh api repos/$SG_REPO/pulls/$pr -q '.body // ""' 2>/dev/null)
  $G -C "$dir" cat-file -e "$sha^{commit}" 2>/dev/null || $G -C "$dir" fetch -q origin "$br" 2>/dev/null
  head=$sha; base=$($G -C "$dir" merge-base "$sha" "origin/${SG_BASE:-main}" 2>/dev/null)
fi
kw=$(printf '%s\n%s' "$title" "$body" | grep -oiE '\b(close[sd]?|fix(e[sd])?|resolve[sd]?) #[0-9]+' | sort -u | tr '\n' ' ')
[ -n "$kw" ] && fails+=("refs: closing keyword(s) ${kw% } in the title/body; use \"Refs #N\" (the loop closes the issue after e2e)")
if $G -C "$dir" cat-file -e "$head^{commit}" 2>/dev/null; then
  # lines about the fork, another repo or a removal cite paths that are rightly absent from this head
  tops=" $($G -C "$dir" ls-tree --name-only "$head" | tr '\n' ' ') "   # only paths under a top-level dir of this repo: squad/, tmp/ are another tree
  for p in $(printf '%s\n' "$body" | grep -viE 'fork|agent-squad|upstream|delet|remov|renam|https?://' | grep -oE "$PATHRE" | tr -d '`' | sort -u); do
    p=${p#./}; case "$tops" in *" ${p%%/*} "*) ;; *) continue;; esac
    $G -C "$dir" cat-file -e "$head:$p" 2>/dev/null || fails+=("cited: the body cites $p, which is not in the head ${head:0:9}; commit it or fix the body")
  done
  if [ -n "${SG_PREPUSH_CHECKS:-}" ] && [ -n "$base" ]; then
    files=$($G -C "$dir" diff --name-only "$base" "$head" | tr '\n' ' ')
    out=$(cd "$dir" && PREPUSH_FILES="$files" PREPUSH_RANGE="$base..$head" PREPUSH_BODY="$body" bash -c "$SG_PREPUSH_CHECKS" 2>&1) || fails+=("checks: \$SG_PREPUSH_CHECKS failed: $(printf '%s' "$out" | tail -3 | tr '\n' ' ')")
  fi
fi
[ ${#fails[@]} -eq 0 ] && exit 0
printf -- '- %s\n' "${fails[@]}"; exit 1
