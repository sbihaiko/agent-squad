#!/bin/bash
# usage: hookfire.sh <dev|stg|both>   The CI event's entry into the loop (#1837).
#
# A self-hosted runner on the loop host runs one fixed command when a `ci` run completes, so a promotion starts on the
# event instead of on a timer. The watch cycle keeps running both passes every cycle, so the event is an accelerator and
# the cycle stays the redundancy: a lost event costs one cycle, never a delivery.
#
# What the hook never does: it checks out nothing, reads no branch, and trusts the event for no fact. The first argument
# names the branch the run reported, and that only picks the pass; the pass fetches the remote and reads its own facts.
# A branch this repository does not promote is refused by name, so a fork's run can never reach a pass.
#
# Each pass runs under its own single-flight lock, the one the watch cycle uses, so an event that arrives while a pass
# is in flight exits 76 and adds nothing (ADR-0149: one writer per loop fact). The pass's own lines go to promote.log,
# and one line per firing goes to cycle.log. Exit 0: a pass ran, or one already held the lock, or a pass could not reach its
# credential (exit 3): that pass alerts once and keeps its request, so a green proof never turns the run red (#2213).
# Exit 1: a pass failed. Exit 2: the branch or the profile is wrong.
here="$(cd "$(dirname "$0")" && pwd)"
[ $# -eq 1 ] || { echo "hookfire: usage: hookfire.sh <dev|stg|both>" >&2; exit 2; }
branch=$1
dev=${SG_BASE:-dev}; stg=${SG_STG_BRANCH:-stg}
case "$branch" in
  "$dev")      passes="promote";;
  "$stg")      passes="promote-main";;
  both)        passes="promote promote-main";;
  *) echo "hookfire: '$branch' is not a branch this repository promotes ($dev, $stg, both); no pass runs" >&2; exit 2;;
esac
# A resolved profile is taken as given, so the suite runs offline; the runner supplies none and env.sh resolves it.
if [ -z "${SG_STATE:-}" ] || [ -z "${SG_REPO:-}" ]; then . "$here/env.sh" || exit 2; fi
state=$SG_STATE; mkdir -p "$state/auto"
log=$state/promote.log; clog=$state/cycle.log
. "$here/promotequiet.sh"   # the dev-to-stg debounce the watch cycle reads, so an event promotes no earlier than a cycle would
fire() {  # <pass> <lock> <script>
  local pass=$1 lock=$2 script=$3 o rc
  o=$(SG_PROMOTE_PROOF=ci bash "$here/single.sh" "$lock" bash "$here/$script" 2>&1); rc=$?
  [ "$rc" = 76 ] || [ -z "$o" ] || printf '%s\n' "$o" >> "$log"
  printf '%s HOOK %s rc=%s\n' "$(date +%T)" "$pass" "$rc" >> "$clog"
  if [ "$rc" = 76 ]; then echo "hookfire: $pass is in flight; the event adds nothing"; return 0; fi
  [ "$rc" = 0 ] && return 0
  if [ "$rc" = 3 ]; then echo "hookfire: $pass cannot reach its credential; the pass alerted and the request waits"; return 0; fi
  echo "hookfire: $pass failed (rc $rc): $(printf '%s' "$o" | tail -n 1)" >&2
  return 1
}
rc=0
for p in $passes; do
  case "$p" in
    promote)
      # The dev tip's green check is the event, but the burst rule still decides when a proof starts: a landing stream
      # earns one proof window, exactly as the watch cycle reads it (#1837, ADR-0145 rule 3).
      if promote_quiet; then fire promote promote promote-pass.sh || rc=1
      else printf '%s HOOK promote deferred: the landing window is closed\n' "$(date +%T)" >> "$clog"; fi;;
    promote-main) fire promote-main promote-main promote-main-pass.sh || rc=1;;
  esac
done
exit $rc
