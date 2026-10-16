#!/bin/bash
# The promotion line (ADR-0145, #439): promote-stg.sh moves stg to the dev tip only when the promotion proof is green on that
# tip, and never otherwise. SG_PROMOTE_PROOF names the proof.
#   ci (the default): the dev tip's CI. Green promotes; red, running or a missing run promotes nothing.
#   local: the promotion gate's own status line, $SG_STATE/gate-status, which the run that earns it writes through the existing
#     seam: `SG_GATE_KIND=full bash gate.sh bash -c '<the full suite>'` (gate.sh's header names the line and the kind). stg
#     moves only when that line says `full`, `pass`, and names the dev tip exactly.
# The owner fast-forwards main to the stg tip with one command, and it refuses when main is not an ancestor of stg
# (promote-main.sh). No loop script pushes to main.
# Offline: a bare local remote, a stub CI seam and a hand-written status line, no gh. usage: bash test-promote.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git
export SG_STATE=$T/state SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0; mkdir -p "$SG_STATE"
git init -q --bare "$R"; git init -q "$T/w"; W="$G -C $T/w"
$W checkout -q -b main; $W commit -q --allow-empty -m base; $W remote add origin "$R"
for b in dev stg main; do $W push -q origin "main:refs/heads/$b"; done
tip() { git -C "$R" rev-parse "refs/heads/$1"; }
adv() { $W fetch -q origin; $W checkout -q -B "$1" "origin/$1"; $W commit -q --allow-empty -m "$2"; $W push -q origin "$1"; }
# ci <green|red|running>: the stub CI seam. It logs the sha it was read for, so a test proves which proof the script used.
ci() { printf '#!/bin/bash\necho "ci read %s" >> "%s/ci.log"\necho "%s"\n' "$1" "$T" "$1" > "$T/ci"; chmod +x "$T/ci"; export SG_CI_STATE_CMD=$T/ci; }
# line <kind> <result> <sha>: the promotion gate's own status line, as gate.sh writes it.
line() { printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$1" "$2" "$3" > "$SG_STATE/gate-status"; }
base=$(tip main)

# The default proof is `ci`: with no SG_PROMOTE_PROOF set the CI seam decides, and a green CI promotes.
unset SG_PROMOTE_PROOF
adv dev one; ci green; rm -f "$T/ci.log"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ -s "$T/ci.log" ] && ok "the default proof is ci: the CI seam is read with SG_PROMOTE_PROOF unset" || ko "the default proof did not read the CI seam"
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "a dev tip whose CI is green is promoted to stg by fast-forward" || ko "green CI: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"
[ "$(tip main)" = "$base" ] && ok "the loop left main alone" || ko "the loop moved main"

# A missing CI run (no check runs) reads as running: nothing is promoted.
adv dev one-absent; before=$(tip stg); ci running
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "a missing CI run promotes nothing" || ko "running CI: rc $rc, stg $(tip stg), out '$out'"
case "$out" in *running*) ok "the refusal says the CI is running";; *) ko "the refusal does not say running: '$out'";; esac

# The local proof: the promotion gate's own status line, and nothing else.
export SG_PROMOTE_PROOF=local
adv dev two; before=$(tip stg); rm -f "$SG_STATE/gate-status"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "local: no status line promotes nothing" || ko "local no line: rc $rc, stg $(tip stg), out '$out'"
case "$out" in *"no local proof"*) ok "local: the refusal names the missing proof";; *) ko "local: the refusal does not name the missing proof: '$out'";; esac

other=$(tip stg)   # an older commit is not the dev tip
adv dev two-other; before=$(tip stg); line full pass "$other"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "local: a proof for another sha promotes nothing" || ko "local other sha: rc $rc, stg $(tip stg), out '$out'"
case "$out" in *"$other"*) ok "local: the refusal names the sha the proof has";; *) ko "local: the refusal does not name the sha it has: '$out'";; esac

adv dev three; before=$(tip stg); line full fail "$(tip dev)"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip stg)" = "$before" ] && ok "local: a failed proof for the dev tip promotes nothing" || ko "local fail: rc $rc, stg $(tip stg), out '$out'"

adv dev four; before=$(tip stg); line full pass "$(tip dev)"
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "local: a full pass for the dev tip promotes, and stg moves" || ko "local pass: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"

# stg holds work dev lacks: a fast-forward is impossible, so nothing moves, whatever the proof.
adv stg stg-only; adv dev dev-only; line full pass "$(tip dev)"; before=$(tip stg)
bash "$d/promote-stg.sh" >/dev/null 2>&1
[ "$(tip stg)" = "$before" ] && ok "a dev tip that does not descend from stg is not promoted" || ko "stg moved without a fast-forward"
$W checkout -q -B stg origin/dev; $W push -q -f origin stg   # fixture: realign stg with dev

# The owner command.
unset SG_PROMOTE_PROOF
bash "$d/promote-main.sh" >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && [ "$(tip main)" = "$(tip stg)" ] && ok "the owner command promotes stg to main" || ko "owner: rc $rc, main $(tip main), stg $(tip stg)"
adv main main-only; before=$(tip main)
out=$(bash "$d/promote-main.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip main)" = "$before" ] && case "$out" in *ancestor*) true;; *) false;; esac && ok "the owner command refuses when main is not an ancestor of stg" || ko "owner divergence: rc $rc, out '$out'"

# No loop script pushes to main: only promote-main.sh names it, and a test fixture is no loop script.
bad=$(cd "$d" && grep -lE 'push[^#]*(refs/heads/main|origin main|HEAD:main)' *.sh *.py 2>/dev/null | grep -v -e '^test-' -e '^promote-main.sh$')
[ -z "$bad" ] && ok "no loop script pushes to main" || ko "pushes to main: $bad"

# (#392) The promotion reads the machine-wide gate lock: with a holder it waits, says so on one line, then promotes.
export SG_PROMOTE_PROOF=local; adv dev lock; line full pass "$(tip dev)"; rm -f "$T/gl"
(bash "$d/serial.sh" gate sh -c "sleep 5; echo holder >> $T/gl") & for _ in $(seq 1 50); do [ -s "$SG_STATE/locks/gate/pid" ] && break; sleep 0.1; done
out=$(bash "$d/promote-stg.sh" 2>&1); wait
[ "$(printf '%s\n' "$out" | grep -c '^serial: waiting for lock gate held by pid')" = 1 ] && ok "the promotion waits for the holder, on one line" || ko "promotion wait lines: '$out'"
[ "$(tip stg)" = "$(tip dev)" ] && ok "the waiting promotion is not dropped: stg is promoted" || ko "the waiting promotion did not promote"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
