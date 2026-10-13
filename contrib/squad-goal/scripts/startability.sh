#!/bin/bash
# Shared read for dispatchers: an issue the loop marked not startable is not dispatched again.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ghcall.sh"
sg_not_startable() {
  local out rc
  out=$(gh_api "repos/$SG_REPO/issues/$1/comments?per_page=100" -q '.[].body'); rc=$?
  [ "$rc" -eq 0 ] || return 1
  printf '%s\n' "$out" | /usr/bin/grep -q '^SQUAD-NOT-STARTABLE:'
}
