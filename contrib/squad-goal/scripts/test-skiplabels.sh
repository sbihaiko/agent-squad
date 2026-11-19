#!/bin/bash
# seconds: 0.05
# Guard for skiplabels.sh (#1673): the holders and the freeze markers are a floor of both dispatch lanes, a profile's skip
# list adds labels and never removes one, and each lane keeps its own extra labels.
# usage: bash test-skiplabels.sh
d=$(cd "$(dirname "$0")" && pwd); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
FLOORS="ready-for-human ready-for-HAL9K needs-triage frozen:owner frozen:dependency"
has() { case ",$1," in *",$2,"*) return 0;; *) return 1;; esac; }
# skip <lane> <profile's list, or - for the default> [extra]: the lane's skip set, read the way a pass reads it
skip() {
  ( unset SG_FRONTIER_SKIP_LABELS SG_BUG_SKIP_LABELS
    [ "$2" = - ] || export "SG_$(printf '%s' "$1" | tr a-z A-Z)_SKIP_LABELS=$2"
    . "$d/skiplabels.sh"; sg_skip_labels "$1" "${3:-}" )
}
for lane in frontier bug; do
  set_=$(skip $lane -)
  for f in $FLOORS; do has "$set_" "$f" && ok "the $lane lane holds $f by default" || ko "the $lane lane does not hold $f by default: $set_"; done
  set_=$(skip $lane "spec,wontfix")
  for f in $FLOORS; do has "$set_" "$f" && ok "the $lane lane keeps $f under a profile list" || ko "a profile list removed $f from the $lane lane: $set_"; done
  set_=$(skip $lane "")
  for f in $FLOORS; do has "$set_" "$f" && ok "the $lane lane keeps $f under an empty profile list" || ko "an empty profile list removed $f from the $lane lane: $set_"; done
done
f=$(skip frontier - "bug,debt,run-parent"); has "$f" bug && has "$f" debt && has "$f" run-parent && ok "the frontier keeps its structural labels beside the floors" || ko "the frontier lost its structural labels: $f"
set_=$(skip frontier -); has "$set_" spec && has "$set_" needs-ratification && ok "the frontier still skips spec and needs-ratification" || ko "the frontier lost spec or needs-ratification: $set_"
has "$set_" no-auto && ko "the frontier skips the bug lane's no-auto: $set_" || ok "the frontier does not skip no-auto"
has "$set_" question && ko "the frontier skips the bug lane's question: $set_" || ok "the frontier does not skip question"
set_=$(skip bug -); has "$set_" no-auto && has "$set_" question && has "$set_" needs-info && ok "the bug lane still skips no-auto, question and needs-info" || ko "the bug lane lost one of its own labels: $set_"
for lane in frontier bug; do
  for list in - "wontfix,needs-info"; do
    dups=$(skip $lane "$list" | tr , '\n' | sort | uniq -d)
    [ -z "$dups" ] && ok "the $lane lane lists no label twice (profile list '$list')" || ko "the $lane lane lists $(echo $dups) twice (profile list '$list')"
  done
done
# A profile is sourced after this file, so a profile that sets a name this file uses must not lift a floor.
for lane in frontier bug; do
  set_=$( . "$d/skiplabels.sh"; _sg_floor=; sg_skip_labels "$lane" )
  for f in ready-for-human ready-for-HAL9K needs-triage; do has "$set_" "$f" && ok "a profile that sets _sg_floor keeps $f in the $lane lane" || ko "a profile that sets _sg_floor removed $f from the $lane lane: $set_"; done
done
has "$set_" spec && ko "the bug lane skips the frontier's spec: $set_" || ok "the bug lane does not skip spec"
exit $fail
