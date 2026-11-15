#!/bin/bash
# usage: landstatus.sh <sha> <pending|success|failure> <description> [context]
#
# The landing's own commit status (#1836). A landing marks the sha it starts to land and the sha it landed, so the time it
# took sits on the commit beside the checks it waited for: GitHub keeps `created_at` and `updated_at` for every status, and
# a status left pending is a landing that died. The context defaults to squad/landing; a wave landing names squad/wave-landing.
#
# The status is a report, never a gate: a refused write prints one line and exits 0, the same rule every other reporter in
# this directory follows. It spends one call of the landing pass (SG_GH_PASS), and SG_LAND_STATUS=off turns it off.
[ "${SG_LAND_STATUS:-on}" = off ] && exit 0
sha=${1:-}; state=${2:-}; desc=${3:-}; ctx=${4:-squad/landing}
case "$sha" in ''|*[!0-9a-fA-F]*) exit 0;; esac   # no sha is no status; a landing goes on
case "$state" in pending|success|failure|error) ;; *) exit 0;; esac
[ -n "${SG_REPO:-}" ] || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
. "$here/ghcall.sh"
export SG_GH_PASS=${SG_GH_PASS:-landing}
url=${SG_LAND_TARGET_URL:-https://github.com/$SG_REPO/actions}
out=$(gh_send -X POST "repos/$SG_REPO/statuses/$sha" -f "state=$state" -f "context=$ctx" \
  -f "description=$(printf '%s' "${desc:-landing}" | cut -c1-140)" -f "target_url=$url" 2>&1) || {
  echo "LANDSTATUS $state on ${sha:0:9} refused: $(printf '%s' "$out" | tail -1)"; exit 0; }
echo "LANDSTATUS $state on ${sha:0:9} ($ctx)"
exit 0
