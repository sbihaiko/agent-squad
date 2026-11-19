#!/bin/bash
# seconds: 4.06
# Regression test for the review a loop run gives its own children (#601, ADR-0145 rule 9): runreview.sh is the command the
# loop workflow's review gate runs in a passed child's worktree, before the run merges it. It wraps childreview.sh, takes one
# `reviews` slot of the machine-wide budget (waiting, on one line), says "no review here" (exit 3) when the run has no wave,
# and on CHANGES or BLOCKED requeues the issue at once, with every blocking finding, and writes the marker childcc honors.
# When its run offers a fix round (SQUAD_REVIEW_FIX=1, #570), a CHANGES whose findings are new or changed exits 4 ("fix") and
# requeues nothing; findings left as they were count toward the stall (ADR-0134), and at the cap the issue is requeued once.
# Every verdict is a `review` fact in the issue's ledger (#702, ADR-0151): three CHANGES in a row end the issue needs-human,
# frozen:owner with one status comment and no requeue; identical findings twice move the coder; an APPROVE resets the streak.
# Stubs: childreview.sh and wave.sh record their argv; the budget is a throwaway SQUAD_CAPS_DIR. No network, no model.
# usage: bash test-runreview.sh [scripts dir]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/squad" "$T/bin"
cp "$d/runreview.sh" "$d/issue_ledger.py" "$d/caps.sh" "$d/revformat.py" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/ghread.py" "$d/ghthrottle.py" "$T/s/" 2>/dev/null
for x in squad config; do ln -s "$(cd "$d/../../.." && pwd)/$x" "$T/squad/$x"; done
# The gh door: the freeze label and the status comment go through ghcall.sh, and this stub records the write.
printf '#!/bin/sh\nprintf "gh %%s\\\\n" "$*" >> "%s/gh.log"\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"; PATH="$T/bin:$PATH"; export PATH
export SQUAD_CAPS_DIR=$T/caps SQUAD_CAPS_POLL=0.05
cat > "$T/s/env.sh" <<EOF
export SG_STATE=$T/st SG_REPO=x/y SG_BASE=dev SG_SQUAD=$T/squad
sg_cq_file() { printf '%s/cq/x__y#%s\n' "\$SG_STATE" "\$1"; }
sg_cq_status() { sg_cq_file "\$1-status~\$2.md"; }
EOF
# childreview.sh writes what the real one writes (the verdict, the head, the base) and records its argv.
cat > "$T/s/childreview.sh" <<EOF
#!/bin/bash
printf 'childreview %s\n' "\$*" >> "$T/rev.log"
br=\$(git symbolic-ref --quiet --short HEAD); key=\$(printf '%s' "\$br" | tr / _)
case "\${STUB_VERDICT:-APPROVE}" in
  NONE) exit 1;;
  NOCHANGE) printf 'provisional: the fix for #7 already exists at this head\n' > "$T/st/reason-\$key"; echo "CHILDREVIEW \$br: no change past abc123def, nothing to review (#7)" >&2; exit 6;;
  APPROVE) printf 'VERDICT: APPROVE\n' > "$T/st/review-\$key.md";;
  *) printf '**VERDICT: %s**\n## Blocking\n- a.py:1 first finding\n- b.py:2 second finding%s\n## Nits\n- c.py:3 a nit\n' "\$STUB_VERDICT" "\${STUB_EXTRA:+
- \$STUB_EXTRA}" > "$T/st/review-\$key.md";;
esac
git rev-parse HEAD > "$T/st/review-\$key.sha"; printf 'wave/%s\n' "\$1" > "$T/st/review-\$key.base"
echo "\$br \$(git rev-parse --short=9 HEAD) VERDICT: \${STUB_VERDICT:-APPROVE}"
EOF
printf '#!/bin/bash\nprintf "wave.sh %%s\\n" "$*" >> "%s/wave.log"\n' "$T" > "$T/s/wave.sh"
chmod +x "$T/s/childreview.sh" "$T/s/wave.sh"
$G init -q -b squad/run-1/target-route.1.1 "$T/wt"; (cd "$T/wt" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m "work (Refs #7)")
head=$($G -C "$T/wt" rev-parse HEAD)
rr() { (cd "$T/wt" && bash "$T/s/runreview.sh" "$@"); }
reset() { : > "$T/rev.log"; : > "$T/wave.log"; rm -f "$T/st/auto/runfix-"* "$T/st/review-"*; rm -rf "$T/st/state"; }
lq() { SG_STATE=$T/st SG_REPO=x/y python3 "$T/s/issue_ledger.py" requeued "x/y#7" run-1 2>/dev/null; }   # the head the run requeued the issue on (ADR-0149)

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
[ ! -s "$T/wave.log" ] && [ -z "$(lq)" ] && ok "C: nothing is requeued" || ko "C: wave.log '$(cat "$T/wave.log")'"

# D) CHANGES: requeue once with every blocking finding, the marker holds the head, exit non-zero (the engine holds the child).
reset; out=$(STUB_VERDICT=CHANGES rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" != 0 ] && [ "$rc" != 3 ] && ok "D: CHANGES exits non-zero, so the run holds the child" || ko "D: exit $rc: $out"
[ "$(head -1 "$T/wave.log")" = "wave.sh requeue 7 the review of its own diff in run run-1 asked for changes (VERDICT: CHANGES) at ${head:0:9}:" ] \
  && ok "D: the issue is requeued at once through wave.sh" || ko "D: wave.log '$(cat "$T/wave.log")'"
/usr/bin/grep -qx -- '- a.py:1 first finding' "$T/wave.log" && /usr/bin/grep -qx -- '- b.py:2 second finding' "$T/wave.log" \
  && ok "D: the requeue carries every blocking finding" || ko "D: findings missing: '$(cat "$T/wave.log")'"
! /usr/bin/grep -q 'a nit' "$T/wave.log" && ok "D: a nit is not a blocking finding" || ko "D: a nit reached the requeue"
[ "$(/usr/bin/grep -c '^wave.sh requeue' "$T/wave.log")" = 1 ] && ok "D: one requeue" || ko "D: requeues: $(/usr/bin/grep -c '^wave.sh requeue' "$T/wave.log")"
[ "$(lq)" = "$head" ] && ok "D: the requeue fact holds the reviewed head" || ko "D: fact '$(lq)'"
case "$out" in *"a.py:1 first finding"*"b.py:2 second finding"*) ok "D: the output (the run record's findings) carries the review";; *) ko "D: out '$out'";; esac
# the same head asked again (a resume): no second requeue
: > "$T/wave.log"; STUB_VERDICT=CHANGES rr run-1 route.1.1 7 >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && [ ! -s "$T/wave.log" ] && ok "D: the same head is never requeued twice" || ko "D: exit $rc, wave.log '$(cat "$T/wave.log")'"

# E) BLOCKED: requeued the same way.
reset; out=$(STUB_VERDICT=BLOCKED rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" != 0 ] && [ "$rc" != 3 ] && /usr/bin/grep -q '^wave.sh requeue 7 .*(VERDICT: BLOCKED)' "$T/wave.log" && [ -n "$(lq)" ] \
  && ok "E: BLOCKED requeues and writes the marker" || ko "E: exit $rc, wave.log '$(cat "$T/wave.log")'"

# F) a review that wrote no verdict (the chain was down): "try again" (5, #635), so the run reviews the child again inside its
# deadline. Nothing is requeued.
reset; out=$(STUB_VERDICT=NONE rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" = 5 ] && [ ! -s "$T/wave.log" ] && ok "F: no verdict answers try again (5) and requeues nothing" || ko "F: exit $rc: $out"

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

# K) a slot wait past its bound (#635): "try again" (5), no review and nothing requeued.
reset; /bin/sleep 300 & HOLD=$!
PYTHONPATH="$T/squad" python3 -m squad.caps take reviews test-holder --count 99 --pid "$HOLD" >/dev/null 2>&1
out=$(cd "$T/wt" && SG_RUN_REVIEW_WAIT=0.2 bash "$T/s/runreview.sh" run-1 route.1.1 7 2>&1); rc=$?
kill "$HOLD" 2>/dev/null; wait "$HOLD" 2>/dev/null
[ "$rc" = 5 ] && [ ! -s "$T/rev.log" ] && [ ! -s "$T/wave.log" ] && ok "K: a slot wait past its bound answers try again (5)" || ko "K: exit $rc: $out"

# H) a fix round on offer (#570): a first CHANGES exits 4 with the full review and requeues nothing.
fx() { (cd "$T/wt" && SQUAD_REVIEW_FIX=1 STUB_VERDICT=${V:-CHANGES} SG_RUN_FIX_STALLS=2 bash "$T/s/runreview.sh" run-1 route.1.1 7 2>/dev/null); }
reset; out=$(fx); rc=$?
[ "$rc" = 4 ] && ok "H: a first CHANGES with a fix round on offer exits 4 (fix)" || ko "H: exit $rc"
[ ! -s "$T/wave.log" ] && [ -z "$(lq)" ] && ok "H: a fix round requeues nothing" || ko "H: wave.log '$(cat "$T/wave.log")'"
[ "$out" = "$(printf '**VERDICT: CHANGES**\n## Blocking\n- a.py:1 first finding\n- b.py:2 second finding\n## Nits\n- c.py:3 a nit')" ] \
  && ok "H: stdout is the full review, the fix round's input" || ko "H: out '$out'"
# the same findings again: one failed round with no change, under the cap of 2, so another fix round
out=$(fx); rc=$?
[ "$rc" = 4 ] && [ ! -s "$T/wave.log" ] && ok "H: one round with the same findings is under the stall cap" || ko "H: exit $rc, wave.log '$(cat "$T/wave.log")'"
# changed findings start the count again
out=$(STUB_EXTRA="d.py:9 a new finding" fx); rc=$?
[ "$rc" = 4 ] && ok "H: changed findings are a change" || ko "H: exit $rc"
out=$(STUB_EXTRA="d.py:9 a new finding" fx); rc=$?
[ "$rc" = 4 ] && ok "H: the count starts again after a change" || ko "H: exit $rc"
# a second round in a row with those findings reaches the cap: requeued once, exit 1
out=$(STUB_EXTRA="d.py:12 a new finding" fx); rc=$?
[ "$rc" = 1 ] && [ "$(/usr/bin/grep -c '^wave.sh requeue 7 ' "$T/wave.log")" = 1 ] && [ "$(lq)" = "$head" ] \
  && ok "H: at the stall cap the issue is requeued once (a moved line is no change)" || ko "H: exit $rc, wave.log '$(cat "$T/wave.log")'"

# I) BLOCKED with a fix round on offer: requeued, no fix round.
reset; out=$(V=BLOCKED fx); rc=$?
[ "$rc" = 1 ] && /usr/bin/grep -q '^wave.sh requeue 7 .*(VERDICT: BLOCKED)' "$T/wave.log" && ok "I: BLOCKED requeues with no fix round" || ko "I: exit $rc, wave.log '$(cat "$T/wave.log")'"

# J) no fix round on offer (the run cannot fund it, or an older engine): CHANGES requeues as before.
reset; out=$(cd "$T/wt" && SQUAD_REVIEW_FIX=0 STUB_VERDICT=CHANGES bash "$T/s/runreview.sh" run-1 route.1.1 7 2>/dev/null); rc=$?
[ "$rc" = 1 ] && [ "$(/usr/bin/grep -c '^wave.sh requeue 7 ' "$T/wave.log")" = 1 ] && ok "J: with no fix round on offer, CHANGES requeues" || ko "J: exit $rc"

# L) the review streak is a ledger fact (#702, ADR-0151): three CHANGES verdicts in a row end the issue needs-human for the
# owner, so the card carries frozen:owner, one status comment names the last findings, and the issue is not requeued again.
# Identical findings twice move the next fix round one place down the coder chain, and the ledger holds that too.
st() { SG_STATE=$T/st SG_REPO=x/y python3 "$T/s/issue_ledger.py" "$@"; }
reset; rm -f "$T/gh.log"; rm -rf "$T/st/cq"; mkdir -p "$T/st/cq"
for r in 1 2 3; do out=$(cd "$T/wt" && SQUAD_REVIEW_FIX=0 SQUAD_REVIEW_ROUND=$r STUB_VERDICT=CHANGES bash "$T/s/runreview.sh" run-1 route.1.1 7 2>&1); rc=$?; done
[ "$rc" = 1 ] && ok "L: the third CHANGES verdict holds the child (1)" || ko "L: exit $rc: $out"
[ "$(st read 'x/y#7' state)" = needs-human ] && ok "L: three CHANGES verdicts end the issue needs-human" || ko "L: state '$(st read 'x/y#7' state)'"
[ "$(st coder 'x/y#7')" = 1 ] && ok "L: identical findings twice move the next attempt down the coder chain" || ko "L: coder '$(st coder 'x/y#7')'"
[ "$(/usr/bin/grep -c 'issues/7/labels' "$T/gh.log" 2>/dev/null)" = 1 ] && /usr/bin/grep -q 'frozen:owner' "$T/gh.log" \
  && ok "L: the card is frozen for the owner once (frozen:owner, through the door)" || ko "L: label writes '$(cat "$T/gh.log" 2>/dev/null)'"
[ "$(/usr/bin/grep -c '^wave.sh requeue 7 ' "$T/wave.log")" = 1 ] && ok "L: the frozen issue adds no requeue of the head it already requeued" || ko "L: requeues '$(/usr/bin/grep -c '^wave.sh requeue 7 ' "$T/wave.log")'"
c=$(/bin/ls "$T/st/cq" | /usr/bin/grep 'reviewstreak'); /usr/bin/grep -q 'first finding' "$T/st/cq/$c" \
  && ok "L: one status comment names the last findings" || ko "L: comment '$(/bin/ls "$T/st/cq")'"
# a review already queued for the stopped issue (a fourth CHANGES on a head it never requeued) holds the child: no requeue,
# and no fix round on offer either, because the owner's freeze is the one decision left on it (ADR-0029, ADR-0151 rule 1).
(cd "$T/wt" && echo y > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "late work (Refs #7)")
: > "$T/wave.log"
out=$(cd "$T/wt" && SQUAD_REVIEW_FIX=0 SQUAD_REVIEW_ROUND=4 STUB_VERDICT=CHANGES STUB_EXTRA="d.py:9 a late finding" bash "$T/s/runreview.sh" run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" = 1 ] && [ ! -s "$T/wave.log" ] \
  && ok "L: a review queued after the freeze holds the child and requeues nothing" || ko "L: exit $rc, wave.log '$(cat "$T/wave.log")'"
out=$(cd "$T/wt" && SQUAD_REVIEW_FIX=1 SQUAD_REVIEW_ROUND=5 STUB_VERDICT=CHANGES STUB_EXTRA="e.py:1 another finding" bash "$T/s/runreview.sh" run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" = 1 ] && [ ! -s "$T/wave.log" ] \
  && ok "L: a frozen issue is offered no fix round" || ko "L: exit $rc, wave.log '$(cat "$T/wave.log")'"
# an approval resets the streak, so a fourth review is no freeze of its own
reset; rm -f "$T/gh.log"
(cd "$T/wt" && SQUAD_REVIEW_FIX=0 SQUAD_REVIEW_ROUND=1 STUB_VERDICT=CHANGES bash "$T/s/runreview.sh" run-1 route.1.1 7 >/dev/null 2>&1)
(cd "$T/wt" && SQUAD_REVIEW_FIX=0 SQUAD_REVIEW_ROUND=2 STUB_VERDICT=APPROVE bash "$T/s/runreview.sh" run-1 route.1.1 7 >/dev/null 2>&1)
[ "$(st read 'x/y#7' state)" = todo ] && [ "$(st coder 'x/y#7')" = 0 ] \
  && ok "L: an approval resets the review streak" || ko "L: after approve '$(st read 'x/y#7' review)'"

# M) #1406: a child whose head equals its base has nothing to review. childreview.sh exits 6, which is no "try again": the
# issue is requeued once with the no-change reason, the ledger holds the head, the run exits 1 and never retries the review.
reset; out=$(STUB_VERDICT=NOCHANGE rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" = 1 ] && ok "M: a no-change child exits 1, never try again (5)" || ko "M: exit $rc: $out"
[ "$(/usr/bin/grep -c '^wave.sh requeue 7 ' "$T/wave.log")" = 1 ] && /usr/bin/grep -q 'the child made no change past .* in run run-1: provisional: the fix for #7 already exists' "$T/wave.log" \
  && ok "M: one requeue, with the no-change reason" || ko "M: wave.log '$(cat "$T/wave.log")'"
mh=$($G -C "$T/wt" rev-parse HEAD)
[ "$(lq)" = "$mh" ] && ok "M: the requeue fact holds this head" || ko "M: fact '$(lq)'"
: > "$T/wave.log"; STUB_VERDICT=NOCHANGE rr run-1 route.1.1 7 >/dev/null 2>&1; rc=$?
[ "$rc" = 1 ] && [ ! -s "$T/wave.log" ] && ok "M: a second call on the same head requeues nothing" || ko "M: exit $rc, wave.log '$(cat "$T/wave.log")'"

# N) #1574: a probe child changes nothing by design. SG_CHILD_KIND=probe ends exit 7 before the requeue block: no requeue line in
# wave.log (the card move is wave.sh's job) and no ledger `requeue` fact. A coding child keeps exit 1, the requeue and the fact.
reset; out=$(SG_CHILD_KIND=probe STUB_VERDICT=NOCHANGE rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" = 7 ] && ok "N: a no-change probe child exits 7 (NOCHANGE_PROBE)" || ko "N: probe exit $rc: $out"
! /usr/bin/grep -q '^wave.sh requeue' "$T/wave.log" && ok "N: a probe is never requeued" || ko "N: wave.log '$(cat "$T/wave.log")'"
[ -z "$(lq)" ] && ok "N: a probe leaves no ledger requeue fact" || ko "N: fact '$(lq)'"
reset; out=$(SG_CHILD_KIND=coding STUB_VERDICT=NOCHANGE rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" = 1 ] && ok "N: a no-change coding child still exits 1" || ko "N: coding exit $rc: $out"
[ "$(/usr/bin/grep -c '^wave.sh requeue 7 ' "$T/wave.log")" = 1 ] && [ "$(lq)" = "$($G -C "$T/wt" rev-parse HEAD)" ] \
  && ok "N: a coding child is requeued once, with its ledger fact" || ko "N: wave.log '$(cat "$T/wave.log")' fact '$(lq)'"
reset; out=$(STUB_VERDICT=NOCHANGE rr run-1 route.1.1 7 2>&1); rc=$?
[ "$rc" = 1 ] && ok "N: a child of no named kind is a code child" || ko "N: unnamed kind exit $rc: $out"

rm -rf "$T"; exit $fail
