#!/bin/bash
# usage: acscan.sh <issue...>   (run from the target checkout)
# Writes a read-only brief to $SG_STATE/acscan-<ts>.md: issue bodies + cited ADR files + instructions
# to list AC conflicts. Prints the brief path.
. "$(dirname "$0")/env.sh"
[ $# -gt 0 ] || { echo "usage: acscan.sh <issue...>" >&2; exit 2; }
out="$SG_STATE/acscan-$(date +%Y%m%d-%H%M%S).md"; adrs=""
{
  echo "# Pre-dispatch spec-conflict scan"; echo
  for n in "$@"; do
    body=$(gh api "repos/$SG_REPO/issues/$n" -q '"## Issue #"+(.number|tostring)+": "+.title+"\n\n"+(.body//"")') || { echo "cannot read issue #$n" >&2; exit 1; }
    echo "$body"; echo
    for a in $(printf '%s' "$body" | grep -oE 'ADR-[0-9]{4}' | sort -u); do
      case " $adrs " in *" $a "*) ;; *) adrs="$adrs $a";; esac; done
  done
  for a in $adrs; do
    f=$(ls "$SG_CHECKOUT"/docs/adr/*/"${a#ADR-}"-*.md 2>/dev/null | head -1)
    [ -n "$f" ] && { echo "## $a ($(basename "$f"), first 200 lines)"; echo; head -n 200 "$f"; echo; }
  done
  cat <<'X'
## Instructions

Read-only. Using only the text above, list:
1. contradictions between acceptance criteria (ACs) of the same issue;
2. contradictions between an AC and a cited ADR decision;
3. ACs that cannot hold at a stated size or geometry.

Per finding: AC numbers, why, and the decision needed. If there is none, answer exactly `NO CONFLICTS`. At most 200 words.
X
} > "$out"
echo "$out"
