#!/bin/bash
# usage: prepush.sh --local [body-file]   a run, in its worktree, before every push
# Catches the process-only findings that used to cost a whole review round:
#   refs     "Closes/Fixes/Resolves #N" in the body (the loop's rule is "Refs #N", never a closing keyword)
#   unpushed commits on HEAD that origin/<branch> does not have yet: push them, or the review sees an old head
#   cited    a repo path the body cites (`UI/x.cs`, scripts/y.py) that does not exist on the head: a file the body promises
#            but the branch does not carry (#1277: the tests the body named were never committed); lines naming the fork,
#            another repo, a URL or a removal are skipped, and so is a path outside this repo's top-level dirs (squad/, tmp/)
#   checks   $SG_PREPUSH_CHECKS, when the profile sets it, run in the tree with the touched files as $PREPUSH_FILES
#   ruff     a tree with squad/ and tests/ (the base battery's paths) runs `ruff check --fix`, then `ruff check squad tests`:
#            a red, or a fix nobody committed, blocks the hand-in here, not at the wave's entry gate (#674)
# Prints one "- <rule>: <detail>" line per failure; exit 0 when clean, 1 otherwise.
. "$(dirname "$0")/env.sh" 2>/dev/null; . "$(dirname "$0")/caps.sh"; G=/usr/bin/git; fails=()
PATHRE='(\b|`)([A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(cs|cpp|h|hpp|py|sh|axaml|json|md|yml|yaml|xml|js|ts)\b'
[ "${1:-}" = --local ] || { echo "usage: prepush.sh --local [body-file]" >&2; exit 2; }
dir=.; head=HEAD; br=$($G branch --show-current); body=$( [ -n "${2:-}" ] && cat "$2" )
if [ -n "$br" ] && $G rev-parse -q --verify "origin/$br" >/dev/null; then
  n=$($G rev-list --count "origin/$br..HEAD" 2>/dev/null); [ "${n:-0}" -gt 0 ] && fails+=("unpushed: $n commit(s) on HEAD not on origin/$br; git push origin HEAD:$br")
fi
base=$($G merge-base HEAD "origin/${SG_BASE:-dev}" 2>/dev/null)
kw=$(printf '%s\n' "$body" | grep -oiE '\b(close[sd]?|fix(e[sd])?|resolve[sd]?) #[0-9]+' | sort -u | tr '\n' ' ')
[ -n "$kw" ] && fails+=("refs: closing keyword(s) ${kw% } in the body; use \"Refs #N\" (the loop closes the issue after e2e)")
if $G -C "$dir" cat-file -e "$head^{commit}" 2>/dev/null; then
  # lines about the fork, another repo or a removal cite paths that are rightly absent from this head
  tops=" $($G -C "$dir" ls-tree --name-only "$head" | tr '\n' ' ') "   # only paths under a top-level dir of this repo: squad/, tmp/ are another tree
  for p in $(printf '%s\n' "$body" | grep -viE 'fork|agent-squad|upstream|delet|remov|renam|https?://' | grep -oE "$PATHRE" | tr -d '`' | sort -u); do
    p=${p#./}; case "$tops" in *" ${p%%/*} "*) ;; *) continue;; esac
    $G -C "$dir" cat-file -e "$head:$p" 2>/dev/null || fails+=("cited: the body cites $p, which is not in the head ${head:0:9}; commit it or fix the body")
  done
  if [ -n "${SG_PREPUSH_CHECKS:-}" ] && [ -n "$base" ]; then
    files=$($G -C "$dir" diff --name-only "$base" "$head" | tr '\n' ' ')
    out=$(cd "$dir" && PREPUSH_FILES="$files" bash -c "$SG_PREPUSH_CHECKS" 2>&1) || fails+=("checks: \$SG_PREPUSH_CHECKS failed: $(printf '%s' "$out" | tail -3 | tr '\n' ' ')")
  fi
fi
if [ -d squad ] && [ -d tests ]; then
  py=$(sg_gate_python)   # the one interpreter lookup (#1086), never a second copy of it here
  pre=$($G status --porcelain -- squad tests); "$py" -m ruff check --fix squad tests >/dev/null 2>&1
  out=$("$py" -m ruff check squad tests 2>&1) || fails+=("ruff: $(printf '%s' "$out" | grep -vE '^ *$' | head -4 | tr '\n' ' ')")
  post=$($G status --porcelain -- squad tests)
  [ "$pre" = "$post" ] || fails+=("ruff: --fix changed $(printf '%s\n' "$post" | grep -vxF -- "$pre" | cut -c4- | tr '\n' ' '); commit them")
fi
[ ${#fails[@]} -eq 0 ] && exit 0
printf -- '- %s\n' "${fails[@]}"; exit 1
