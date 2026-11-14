#!/bin/bash
# usage: SG_REPO=owner/name triage-labels.sh [--check]   The loop's label bootstrap (#1673): creates each triage label the
# repository lacks and refreshes the colour and description of the rest, so a second run changes nothing it already holds.
# --check reads the repository and writes nothing: it names each missing label and exits 1 when one is missing.
# The names are the first table of docs/agents/triage-labels.md.
here="$(cd "$(dirname "$0")" && pwd)"
. "$here/ghcall.sh"
LABELS='ready-for-agent|0e8a16|Permission: the agent may take this card from Todo
ready-for-human|fbca04|Holder: a person implements this card, so no dispatch pass takes it
ready-for-HAL9K|1d76db|Holder: the HAL9K agent implements this card, so no dispatch pass takes it'
check=; [ "${1:-}" = --check ] && check=1
have=$(gh_pages "repos/$SG_REPO/labels?per_page=100" -q '.[].name') || { echo "triage-labels: labels unreadable: $have" >&2; exit 1; }
rc=0
while IFS='|' read -r name color desc <&3; do
  if printf '%s\n' "$have" | /usr/bin/grep -qxF "$name"; then
    [ -n "$check" ] && continue
    o=$(gh_send -X PATCH "repos/$SG_REPO/labels/$name" -f "color=$color" -f "description=$desc") || { echo "triage-labels: refresh of $name refused: $o" >&2; rc=1; }
  elif [ -n "$check" ]; then
    echo "label missing: $name"; rc=1
  else
    o=$(gh_send -X POST "repos/$SG_REPO/labels" -f "name=$name" -f "color=$color" -f "description=$desc") || { echo "triage-labels: create of $name refused: $o" >&2; rc=1; }
  fi
done 3<<< "$LABELS"
exit $rc
