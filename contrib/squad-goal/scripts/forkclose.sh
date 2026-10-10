#!/bin/bash
# usage: forkclose.sh   (watch.sh, every loop; FC_DRY=1 prints the candidates)
# Fork-only work closes itself: an open issue whose card is Code Complete, with no open PR naming it and a fork branch
# ($SG_FORK_BRANCH, %n = issue) ahead of $SG_FORK_BASE in $SG_FORK_DIR, is merged into the base (--no-ff), the card goes to
# Testing and the fork suite ($SG_FORK_TEST_CMD) runs through test.sh. Green: the issue closes with the evidence and the
# card goes Done. Red: the merge is reverted (git revert -m 1, never a reset) and the card goes back to In Progress. A merge
# conflict aborts and is reported. One issue at a time (lock); the fork checkout must be clean and on the base branch.
# Owner-approved Done rule for fork-only issues (#1180, 2026-10-10). Unset SG_FORK_DIR turns it off.
. "$(dirname "$0")/env.sh"; here="$(cd "$(dirname "$0")" && pwd)"; A="$SG_STATE/auto"; mkdir -p "$A"
[ -n "${SG_FORK_DIR:-}" ] && [ -d "$SG_FORK_DIR" ] || exit 0; [ -e "$A/off" ] && exit 0
L="$A/forkclose.lock"; mkdir "$L" 2>/dev/null || exit 0   # a test pass is still running
trap 'rmdir "$L" 2>/dev/null' EXIT
G="/usr/bin/git -C $SG_FORK_DIR"
cc=$(jq -r '.items[]?|select(.st=="Code Complete" and .s=="open")|.n' "${SG_HUB_DIR:-$SG_CHECKOUT/runs/.hub}/hub-board.json" 2>/dev/null)
[ -n "$cc" ] || exit 0
refs=" $(gh api "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[]|(.title+" "+(.body//""))' 2>/dev/null | grep -oE 'Refs #[0-9]+' | grep -oE '[0-9]+' | tr '\n' ' ') "
for n in $cc; do
  case "$refs" in *" $n "*) continue;; esac
  br=${SG_FORK_BRANCH//%n/$n}; $G rev-parse -q --verify "refs/heads/$br" >/dev/null || continue
  [ "$($G rev-list --count "$SG_FORK_BASE..$br")" -gt 0 ] || continue
  head=$($G rev-parse "$br"); mk="$A/fork-$n-${head:0:9}"; [ -e "$mk" ] && continue
  [ -n "${FC_DRY:-}" ] && { echo "FORKCLOSE would merge $br into $SG_FORK_BASE and test #$n"; continue; }
  if [ "$($G branch --show-current)" != "$SG_FORK_BASE" ] || [ -n "$($G status --porcelain --untracked-files=no)" ]; then echo "FORKCLOSE WAIT #$n: $SG_FORK_DIR is not a clean $SG_FORK_BASE checkout"; exit 0; fi
  touch "$mk"
  if ! $G merge --no-ff -q -m "Merge $br (Refs #$n)" "$br" >/dev/null 2>&1; then
    $G merge --abort 2>/dev/null
    printf "Fork-only close: merging %s (%s) into %s conflicts; left in Code Complete for the coordinator.\n" "$br" "${head:0:9}" "$SG_FORK_BASE" > "$SG_STATE/cq/$n-forkconflict$$.md"
    echo "FORKCLOSE CONFLICT #$n $br into $SG_FORK_BASE"; continue; fi
  m=$($G rev-parse HEAD)
  [ -n "${SG_E2E_DIR:-}" ] && [ -d "$SG_E2E_DIR" ] && [ -z "$(/usr/bin/git -C "$SG_E2E_DIR" status --porcelain --untracked-files=no)" ] \
    && /usr/bin/git -C "$SG_E2E_DIR" fetch -q origin && /usr/bin/git -C "$SG_E2E_DIR" checkout -q --detach "origin/$SG_BASE"
  out=$("$here/test.sh" "$n" -C "$SG_FORK_DIR" -- sh -c "$SG_FORK_TEST_CMD"); rc=$?; id=$(awk '{print $NF}' <<<"$out")
  tl=$(tail -3 "$SG_STATE/tests/$id.log" 2>/dev/null)
  if [ $rc = 0 ]; then
    gh api -X POST repos/$SG_REPO/issues/$n/comments -F body=@- >/dev/null <<EOT
Done (fork-only work; owner-approved rule: merged into the fork's $SG_FORK_BASE and the fork suite green there).
- $br at ${head:0:9} merged into $SG_FORK_BASE as ${m:0:9}.
- Test pass $id: \`$SG_FORK_TEST_CMD\` → exit 0.
\`\`\`
$tl
\`\`\`
No MesenAI file changed, so no PR.
EOT
    gh api -X PATCH repos/$SG_REPO/issues/$n -f state=closed -f state_reason=completed >/dev/null && $BOARD $SG_OWNER $SG_PROJECT Done $n >/dev/null
    echo "FORKCLOSE #$n closed ($br ${head:0:9} -> ${m:0:9}, $id green)"
  else
    $G revert -m 1 --no-edit "$m" >/dev/null 2>&1
    $BOARD $SG_OWNER $SG_PROJECT "In Progress" $n >/dev/null
    printf "Fork-only close: the fork suite failed after merging %s (%s) as %s (test pass %s, exit %s); the merge was reverted on %s. Card back to In Progress.\n\`\`\`\n%s\n\`\`\`\n" "$br" "${head:0:9}" "${m:0:9}" "$id" "$rc" "$SG_FORK_BASE" "$tl" > "$SG_STATE/cq/$n-forkred$$.md"
    echo "FORKCLOSE RED #$n ($id exit $rc), merge reverted"
  fi
done
