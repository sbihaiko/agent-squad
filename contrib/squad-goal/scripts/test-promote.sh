#!/bin/bash
# The promotion line (ADR-0145): the loop fast-forwards stg to the dev tip when the CI of that tip is green, and never
# otherwise (promote-stg.sh); the owner fast-forwards main to the stg tip with one command, and it refuses when main is
# not an ancestor of stg (promote-main.sh). No loop script pushes to main.
# Offline: a bare local remote and a stub CI reader, no gh. usage: bash test-promote.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git
export SG_STATE=$T/state SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0; mkdir -p "$SG_STATE"
git init -q --bare "$R"; git init -q "$T/w"; W="$G -C $T/w"
$W checkout -q -b main; $W commit -q --allow-empty -m base; $W remote add origin "$R"
for b in dev stg main; do $W push -q origin "main:refs/heads/$b"; done
tip() { git -C "$R" rev-parse "refs/heads/$1"; }
adv() { $W checkout -q -B "$1" "origin/$1" 2>/dev/null || $W checkout -q "$1"; $W commit -q --allow-empty -m "$2"; $W push -q origin "$1"; }
ci() { printf '#!/bin/bash\necho %s\n' "$1" > "$T/ci.sh"; chmod +x "$T/ci.sh"; export SG_CI_STATE_CMD=$T/ci.sh; }
base=$(tip main)

adv dev one; ci green; out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip stg)" = "$(tip dev)" ] && ok "a green dev tip is promoted to stg by fast-forward" || ko "green: rc $rc, stg $(tip stg), dev $(tip dev), out '$out'"
[ "$(tip main)" = "$base" ] && ok "the loop left main alone" || ko "the loop moved main"

for st in red running; do
  adv dev "two-$st"; ci $st; before=$(tip stg)
  bash "$d/promote-stg.sh" >/dev/null 2>&1; bash "$d/promote-stg.sh" >/dev/null 2>&1
  [ "$(tip stg)" = "$before" ] && ok "a $st dev tip is not promoted" || ko "a $st dev tip moved stg"
  n=$(grep -c "$(tip dev).*$st" "$SG_STATE/promote.log" 2>/dev/null)
  [ "$n" = 1 ] && ok "a $st tip: the reason is logged once over two passes" || ko "a $st tip: $n log lines"
done

ci green; bash "$d/promote-stg.sh" >/dev/null 2>&1
[ "$(tip stg)" = "$(tip dev)" ] && ok "a later green tip carries the earlier work" || ko "later green tip did not promote"

# stg holds work dev lacks: a fast-forward is impossible, so nothing moves.
adv stg stg-only; adv dev dev-only; ci green; before=$(tip stg)
bash "$d/promote-stg.sh" >/dev/null 2>&1
[ "$(tip stg)" = "$before" ] && ok "a dev tip that does not descend from stg is not promoted" || ko "stg moved without a fast-forward"
$W checkout -q -B stg origin/dev; $W push -q -f origin stg   # fixture: realign stg with dev

# The owner command.
bash "$d/promote-main.sh" >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && [ "$(tip main)" = "$(tip stg)" ] && ok "the owner command promotes stg to main" || ko "owner: rc $rc, main $(tip main), stg $(tip stg)"
$W checkout -q -B main origin/main; $W commit -q --allow-empty -m main-only; $W push -q origin main; before=$(tip main)
out=$(bash "$d/promote-main.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip main)" = "$before" ] && case "$out" in *ancestor*) true;; *) false;; esac && ok "the owner command refuses when main is not an ancestor of stg" || ko "owner divergence: rc $rc, out '$out'"

# No loop script pushes to main: only promote-main.sh names it, and a test fixture is no loop script.
bad=$(cd "$d" && grep -lE 'push[^#]*(refs/heads/main|origin main|HEAD:main)' *.sh *.py 2>/dev/null | grep -v -e '^test-' -e '^promote-main.sh$')
[ -z "$bad" ] && ok "no loop script pushes to main" || ko "pushes to main: $bad"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
