#!/bin/bash
# Regression test for the review a loop run gives its own children (#601, ADR-0145 rule 9): runreview.sh is the command the
# loop workflow's review gate runs in a passed child's worktree, before the run merges it. It wraps childreview.sh, takes one
# `reviews` slot of the machine-wide budget (waiting, on one line), says "no review here" (exit 3) when the run has no wave,
# and on CHANGES or BLOCKED requeues the issue at once, with every blocking finding, and writes the marker childcc honors.
# Stubs: childreview.sh and wave.sh record their argv; the budget is a throwaway SQUAD_CAPS_DIR. No network, no model.
# usage: bash test-runreview.sh [scripts dir]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/squad"
cp "$d/runreview.sh" "$d/caps.sh" "$T/s/" 2>/dev/null
for x in squad config; do ln -s "$(cd "$d/../../.." && pwd)/$x" "$T/squad/$x"; done
export SQUAD_CAPS_DIR=$T/caps SQUAD_CAPS_POLL=0.05
cat > "$T/s/env.sh" <<EOF
export SG_STATE=$T/st SG_REPO=x/y SG_BASE=dev SG_SQUAD=$T/squad
EOF
# childreview.sh writes what the real one writes (the verdict, the head, the base) and records its argv.
cat > "$T/s/childreview.sh" <<EOF
#!/bin/bash
printf 'childreview %s\n' "\$*" >> "$T/rev.log"
br=\$(git symbolic-ref --quiet --short HEAD); key=\$(printf '%s' "\$br" | tr / _)
case "\${STUB_VERDICT:-APPROVE}" in
  NONE) exit 1;;
  APPROVE) printf 'VERDICT: APPROVE\n' > "$T/st/review-\$key.md";;
  *) printf '**VERDICT: %s**\n## Blocking\n- a.py:1 first finding\n- b.py:2 second finding\n## Nits\n- c.py:3 a nit\n' "\$STUB_VERDICT" > "$T/st/review-\$key.md";;
esac
git rev-parse HEAD > "$T/st/review-\$key.sha"; printf 'wave/%s\n' "\$1" > "$T/st/review-\$key.base"
echo "\$br \$(git rev-parse --short=9 HEAD) VERDICT: \${STUB_VERDICT:-APPROVE}"
EOF
printf '#!/bin/bash\nprintf "wave.sh %%s\\n" "$*" >> "%s/wave.log"\n' "$T" > "$T/s/wave.sh"
chmod +x "$T/s/childreview.sh" "$T/s/wave.sh"
$G init -q -b squad/run-1/target-route.1.1 "$T/wt"; (cd "$T/wt" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m "work (Refs #7)")
head=$($G -C "$T/wt" rev-parse HEAD)
rr() { (cd "$T/wt" && bash "$T/s/runreview.sh" "$@"); }
reset() { : > "$T/rev.log"; : > "$T/wave.log"; rm -f "$T/st/auto/requeued-"* "$T/st/review-"*; }

# A) the run has no wave: "no review here" (3), no review and no slot; the child lands in the run as today.
reset; out=$(rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" = 3 ] && ok "A: a run with no wave answers no review here (3)" || ko "A: exit $rc: $out"
[ ! -s "$T/rev.log" ] && ok "A: no review runs" || ko "A: a review ran: $(cat "$T/rev.log")"
echo wave/run-1 > "$T/st/auto/wave-run-1"

# B) no issue named: no review here.
reset; out=$(rr run-1 route.1.1 "" 2>&1); rc=$?
[ "$rc" = 3 ] && [ ! -s "$T/rev.log" ] && ok "B: a child with no issue answers no review here" || ko "B: exit $rc: $out"

# C) APPROVE: exit 0, one childreview call with the run and the issue, nothing requeued, no marker.
reset; out=$(rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" = 0 ] && ok "C: an approved child exits 0, so the run merges it" || ko "C: exit $rc: $out"
[ "$(cat "$T/rev.log")" = "childreview run-1 7" ] && ok "C: the review is childreview.sh, called with the run and the issue" || ko "C: rev.log '$(cat "$T/rev.log")'"
[ ! -s "$T/wave.log" ] && [ ! -e "$T/st/auto/requeued-run-1-route.1.1" ] && ok "C: nothing is requeued" || ko "C: wave.log '$(cat "$T/wave.log")'"

# D) CHANGES: requeue once with every blocking finding, the marker holds the head, exit non-zero (the engine holds the child).
reset; out=$(STUB_VERDICT=CHANGES rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" != 0 ] && [ "$rc" != 3 ] && ok "D: CHANGES exits non-zero, so the run holds the child" || ko "D: exit $rc: $out"
[ "$(head -1 "$T/wave.log")" = "wave.sh requeue 7 the review of its own diff in run run-1 asked for changes (VERDICT: CHANGES) at ${head:0:9}:" ] \
  && ok "D: the issue is requeued at once through wave.sh" || ko "D: wave.log '$(cat "$T/wave.log")'"
/usr/bin/grep -qx -- '- a.py:1 first finding' "$T/wave.log" && /usr/bin/grep -qx -- '- b.py:2 second finding' "$T/wave.log" \
  && ok "D: the requeue carries every blocking finding" || ko "D: findings missing: '$(cat "$T/wave.log")'"
! /usr/bin/grep -q 'a nit' "$T/wave.log" && ok "D: a nit is not a blocking finding" || ko "D: a nit reached the requeue"
[ "$(/usr/bin/grep -c '^wave.sh requeue' "$T/wave.log")" = 1 ] && ok "D: one requeue" || ko "D: requeues: $(/usr/bin/grep -c '^wave.sh requeue' "$T/wave.log")"
[ "$(cat "$T/st/auto/requeued-run-1-route.1.1" 2>/dev/null)" = "$head" ] && ok "D: the requeued marker holds the reviewed head" || ko "D: marker '$(cat "$T/st/auto/requeued-run-1-route.1.1" 2>/dev/null)'"
case "$out" in *"a.py:1 first finding"*"b.py:2 second finding"*) ok "D: the output (the run record's findings) carries the review";; *) ko "D: out '$out'";; esac
# the same head asked again (a resume): no second requeue
: > "$T/wave.log"; STUB_VERDICT=CHANGES rr run-1 route.1.1 7 >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && [ ! -s "$T/wave.log" ] && ok "D: the same head is never requeued twice" || ko "D: exit $rc, wave.log '$(cat "$T/wave.log")'"

# E) BLOCKED: requeued the same way.
reset; out=$(STUB_VERDICT=BLOCKED rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" != 0 ] && [ "$rc" != 3 ] && /usr/bin/grep -q '^wave.sh requeue 7 .*(VERDICT: BLOCKED)' "$T/wave.log" && [ -e "$T/st/auto/requeued-run-1-route.1.1" ] \
  && ok "E: BLOCKED requeues and writes the marker" || ko "E: exit $rc, wave.log '$(cat "$T/wave.log")'"

# F) a review that wrote no verdict (the chain was down): no review here, so childcc reviews the child later, as today.
reset; out=$(STUB_VERDICT=NONE rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" = 3 ] && [ ! -s "$T/wave.log" ] && ok "F: no verdict answers no review here and requeues nothing" || ko "F: exit $rc: $out"

# G) a full `reviews` budget: the command waits, says so on one line, and reviews once a slot frees.
reset; /bin/sleep 300 & HOLD=$!
PYTHONPATH="$T/squad" python3 -m squad.caps take reviews test-holder --count 99 --pid "$HOLD" >/dev/null 2>&1
(cd "$T/wt" && bash "$T/s/runreview.sh" run-1 route.1.1 7 > "$T/g.out" 2> "$T/g.err"; echo $? > "$T/g.rc") & W=$!
for _ in $(seq 1 100); do [ -s "$T/g.err" ] && break; /bin/sleep 0.05; done
[ ! -s "$T/rev.log" ] && ok "G: no review starts while the budget is full" || ko "G: a review ran on a full budget"
[ "$(/usr/bin/grep -c 'waiting' "$T/g.err")" = 1 ] && /usr/bin/grep -q 'runreview-run-1-route.1.1' "$T/g.err" \
  && ok "G: the wait is said on one line naming the review" || ko "G: stderr '$(cat "$T/g.err")'"
kill "$HOLD" 2>/dev/null; wait "$HOLD" 2>/dev/null; wait "$W"
[ "$(cat "$T/g.rc")" = 0 ] && [ "$(cat "$T/rev.log")" = "childreview run-1 7" ] && ok "G: the review runs once a slot frees" || ko "G: rc '$(cat "$T/g.rc")', rev.log '$(cat "$T/rev.log")'"
occ=$(PYTHONPATH="$T/squad" python3 -m squad.caps 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["occupancy"]["reviews"])')
[ "$occ" = 0 ] && ok "G: the slot is given back" || ko "G: reviews still held: $occ"

rm -rf "$T"; exit $fail
