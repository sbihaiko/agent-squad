#!/bin/bash
# The promotion line (ADR-0145): the loop fast-forwards stg to the dev tip when the full suite, in both modes, passed on that
# tip under the machine lock, and never otherwise (promote-stg.sh); the owner fast-forwards main to the stg tip with one command, and it refuses when main is
# not an ancestor of stg (promote-main.sh). No loop script pushes to main.
# Offline: a bare local remote and a stub gate command, no gh. usage: bash test-promote.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git
export SG_STATE=$T/state SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0; mkdir -p "$SG_STATE"
git init -q --bare "$R"; git init -q "$T/w"; W="$G -C $T/w"
$W checkout -q -b main; $W commit -q --allow-empty -m base; $W remote add origin "$R"
for b in dev stg main; do $W push -q origin "main:refs/heads/$b"; done
tip() { git -C "$R" rev-parse "refs/heads/$1"; }
adv() { $W fetch -q origin; $W checkout -q -B "$1" "origin/$1"; $W commit -q --allow-empty -m "$2"; $W push -q origin "$1"; }
# gate <green|red>: the stub full gate. It logs where it ran (the working directory's HEAD) and whether the machine lock was held.
gate() { printf '#!/bin/bash\necho "$(git rev-parse HEAD) lock=$([ -d "%s/locks/gate" ] && echo held || echo free)" >> "%s/gate.log"\n[ %s = green ]\n' "$SG_STATE" "$T" "$1" > "$T/gate.sh"; chmod +x "$T/gate.sh"; export SG_PROMOTE_GATE_CMD=$T/gate.sh; }
ci() { gate "$1"; }
base=$(tip main)

adv dev one; ci green; out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "a dev tip whose full gate is green is promoted to stg by fast-forward" || ko "green: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"
[ "$(cat "$T/gate.log" 2>/dev/null)" = "$(tip dev) lock=held" ] && ok "the full gate ran once, at the dev tip, under the machine lock" || ko "gate log: '$(cat "$T/gate.log" 2>&1)'"
IFS=$'\t' read -r _ kind res at < "$SG_STATE/gate-status" 2>/dev/null
[ "$kind $res $at" = "full pass $(tip dev)" ] && ok "the status line names the full gate, its result and the dev tip" || ko "status line: '$(cat "$SG_STATE/gate-status" 2>&1)'"
[ "$(tip main)" = "$base" ] && ok "the loop left main alone" || ko "the loop moved main"

for st in red; do
  adv dev "two-$st"; ci $st; before=$(tip stg); rm -f "$T/gate.log"
  bash "$d/promote-stg.sh" >/dev/null 2>&1; bash "$d/promote-stg.sh" >/dev/null 2>&1
  [ "$(tip stg)" = "$before" ] && ok "a dev tip whose full gate is $st is not promoted" || ko "a $st dev tip moved stg"
  n=$(grep -c "$(tip dev).*$st" "$SG_STATE/promote.log" 2>/dev/null)
  [ "$n" = 1 ] && ok "a $st tip: the reason is logged once over two passes" || ko "a $st tip: $n log lines"
  [ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 1 ] && ok "a $st tip: the full gate ran once over two passes" || ko "a $st tip: gate runs: $(cat "$T/gate.log")"
  IFS=$'\t' read -r _ kind res at < "$SG_STATE/gate-status" 2>/dev/null
  [ "$kind $res $at" = "full fail $(tip dev)" ] && ok "a $st tip: the status line says fail at the dev tip" || ko "status line: '$(cat "$SG_STATE/gate-status" 2>&1)'"
done

# (#438) $SG_STATE/promote.seen is bounded: old lines are pruned, and the dedupe holds for the latest red tip.
for i in $(seq 1 100); do echo "dead$i gate red" >> "$SG_STATE/promote.seen"; done
adv dev pruned; ci red; rm -f "$T/gate.log"; bash "$d/promote-stg.sh" >/dev/null 2>&1; bash "$d/promote-stg.sh" >/dev/null 2>&1
[ "$(wc -l < "$SG_STATE/promote.seen" | tr -d ' ')" -le 20 ] && ok "promote.seen is pruned to the last few lines" || ko "promote.seen has $(wc -l < "$SG_STATE/promote.seen" | tr -d ' ') lines"
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 1 ] && grep -qxF "$(tip dev) gate red" "$SG_STATE/promote.seen" && ok "after the prune the latest red tip is still not gated again" || ko "pruned dedupe: gate runs '$(cat "$T/gate.log" 2>&1)'"

adv dev later; ci green; bash "$d/promote-stg.sh" >/dev/null 2>&1
[ "$(tip stg)" = "$(tip dev)" ] && ok "a later green tip carries the earlier work" || ko "later green tip did not promote"

# stg holds work dev lacks: a fast-forward is impossible, so nothing moves.
adv stg stg-only; adv dev dev-only; ci green; before=$(tip stg); rm -f "$T/gate.log"
bash "$d/promote-stg.sh" >/dev/null 2>&1
[ "$(tip stg)" = "$before" ] && ok "a dev tip that does not descend from stg is not promoted" || ko "stg moved without a fast-forward"
[ ! -e "$T/gate.log" ] && ok "no full gate runs for a tip that cannot fast-forward" || ko "the gate ran for a tip with no fast-forward"
$W checkout -q -B stg origin/dev; $W push -q -f origin stg   # fixture: realign stg with dev

# The default full gate: both modes, in this order, and stg moves only when both pass.
unset SG_PROMOTE_GATE_CMD; printf '#!/bin/bash\necho "$*" >> "%s/py.log"\ncase "$*" in *"${PYFAIL:-@none@}"*) exit 1;; esac\n' "$T" > "$T/py"; chmod +x "$T/py"; export SG_GATE_PY=$T/py
$W checkout -q -B stg origin/dev; $W push -q -f origin stg; adv dev three; before=$(tip stg); rm -f "$T/py.log"; PYFAIL="-p no:xdist" bash "$d/promote-stg.sh" >/dev/null 2>&1
[ "$(tip stg)" = "$before" ] && ok "the serial run red: stg does not move" || ko "stg moved with a red serial run"
[ "$(cat "$T/py.log")" = "-m pytest -n auto -p no:deepeval
-m pytest -p no:xdist -p no:deepeval" ] && ok "the full gate runs -n auto, then -p no:xdist" || ko "gate modes: '$(cat "$T/py.log")'"
adv dev four; before=$(tip stg); rm -f "$T/py.log"; PYFAIL="-n auto" bash "$d/promote-stg.sh" >/dev/null 2>&1
[ "$(tip stg)" = "$before" ] && [ "$(wc -l < "$T/py.log" | tr -d ' ')" = 1 ] && ok "the parallel run red: stg does not move and the serial run is skipped" || ko "parallel red: '$(cat "$T/py.log")'"
adv dev five; rm -f "$T/py.log"; PYFAIL=none-at-all bash "$d/promote-stg.sh" >/dev/null 2>&1
[ "$(tip stg)" = "$(tip dev)" ] && ok "both modes green: stg moves to the dev tip" || ko "both green did not promote"

# The owner command.
bash "$d/promote-main.sh" >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && [ "$(tip main)" = "$(tip stg)" ] && ok "the owner command promotes stg to main" || ko "owner: rc $rc, main $(tip main), stg $(tip stg)"
adv main main-only; before=$(tip main)
out=$(bash "$d/promote-main.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip main)" = "$before" ] && case "$out" in *ancestor*) true;; *) false;; esac && ok "the owner command refuses when main is not an ancestor of stg" || ko "owner divergence: rc $rc, out '$out'"

# No loop script pushes to main: only promote-main.sh names it, and a test fixture is no loop script.
bad=$(cd "$d" && grep -lE 'push[^#]*(refs/heads/main|origin main|HEAD:main)' *.sh *.py 2>/dev/null | grep -v -e '^test-' -e '^promote-main.sh$')
[ -z "$bad" ] && ok "no loop script pushes to main" || ko "pushes to main: $bad"
# (#392) The promotion gate takes the machine-wide gate lock: with a holder it waits, says so on one line, then promotes.
adv dev three; ci green; rm -f "$T/gl"
(bash "$d/serial.sh" gate sh -c "sleep 10; echo holder >> $T/gl") & for _ in $(seq 1 50); do [ -s "$SG_STATE/locks/gate/pid" ] && break; sleep 0.1; done
out=$(bash "$d/promote-stg.sh" 2>&1); wait
[ "$(printf '%s\n' "$out" | grep -c '^serial: waiting for lock gate held by pid')" = 1 ] && ok "the promotion gate waits for the holder, on one line" || ko "promotion wait lines: '$out'"
[ "$(tip stg)" = "$(tip dev)" ] && ok "the waiting promotion gate is not dropped: stg is promoted" || ko "the waiting promotion did not promote"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
