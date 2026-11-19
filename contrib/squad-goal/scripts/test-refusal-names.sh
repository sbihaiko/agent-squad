#!/bin/bash
# seconds: 0.10
# Every refusal has ONE shape: the refusal names are a closed set in code, readable in one place (#142).
#
# A writer names its refusal through the helper that owns the set, so a new refusal is a deliberate act in one place,
# and a name outside the set is refused by the helper rather than filed under a guessed name. This suite reads the
# writers as text (a refusal named by hand is a name the set does not hold) and runs the helper as a program (it
# accepts a member and refuses a stranger, naming it). Offline: no gh, no network, no loop state.
# usage: bash test-refusal-names.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
trap 'rm -rf "$T"' EXIT
S=$T/s; mkdir -p "$S"
cp "$d/refusal-names.sh" "$S/" 2>/dev/null
# the scripts that name a refusal: the two helpers and the write door, plus the reconciler's own pass
WRITERS="refusal.sh autorefuse.sh autobug.sh janitor.py"
MEMBERS="BOARD-WRITE-FAILED DISPATCH-FAILED REFUSAL-COMMENT-FAILED BOARD-READ-FAILED"
holds() { [ -n "$1" ] && $G -qxF "$2" <<< "$1"; }

# 1. the set is readable in one place: one helper prints it, and printing it needs nothing else
names=$(bash "$S/refusal-names.sh" 2>&1)
if [ -n "$names" ]; then ok "one helper prints the closed set"; else ko "no helper prints the set: '$names'"; fi
for n in $MEMBERS; do
  holds "$names" "$n" && ok "the closed set holds $n" || ko "the set does not hold $n: '$names'"
done
$G -qxF CARD-WRITE-FAILED <<< "$names" && ko "the set holds the drift spelling CARD-WRITE-FAILED" || ok "the set holds no drift spelling"

# 2. the set is DEFINED in one place: a second definition is a second dialect
defs=$($G -l 'SG_REFUSAL_NAMES=' "$d/refusal-names.sh" "$d/refusal.sh" "$d/autorefuse.sh" "$d/autobug.sh" 2>/dev/null | wc -l | tr -d ' ')
[ "$defs" = 1 ] && ok "the set is defined in one place" || ko "the set is defined in $defs places"

# 3. the helper owns the shape: a member is accepted, a name outside the set is refused and named
bash "$S/refusal-names.sh" BOARD-WRITE-FAILED >/dev/null 2>&1 && ok "the helper accepts a name in the set" || ko "a name in the set was refused"
o=$(bash "$S/refusal-names.sh" CARD-WRITE-FAILED 2>&1); rc=$?
{ [ "$rc" -ne 0 ] && case "$o" in *CARD-WRITE-FAILED*) true;; *) false;; esac; } \
  && ok "the helper refuses a name outside the set, by name" || ko "a name outside the set got through (rc $rc): '$o'"
for n in $MEMBERS; do
  bash "$S/refusal-names.sh" "$n" >/dev/null 2>&1 || ko "the helper refused its own member $n"
done

# 4. every writer names a refusal through the helper: each names the one definition
for w in refusal.sh autorefuse.sh autobug.sh; do
  $G -q 'refusal-names\.sh' "$d/$w" 2>/dev/null && ok "the writer $w names a refusal through the helper" || ko "$w does not name the helper"
done

# 5. a refusal named by hand fails: no writer carries a name the set does not hold
hand=""
for w in $WRITERS; do
  for t in $($G -ohE '[A-Z][A-Z0-9-]*-FAILED' "$d/$w" 2>/dev/null | sort -u); do
    holds "$names" "$t" || hand="$hand $w:$t"
  done
done
[ -z "$hand" ] && ok "no writer carries a refusal name outside the set" || ko "hand-named refusals:$hand"

echo "--- shell suite: test-refusal-names.sh $([ "$fail" = 0 ] && echo green || echo RED)"
exit $fail
