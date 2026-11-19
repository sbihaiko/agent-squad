#!/bin/bash
# seconds: 24.48
# The requeue cap suite reads its own checkout, never the caller's (#1486). `requeue()` counts an issue's failed dispatches
# in its ledger, keyed by the evolution key `landed_key` reads from `${SG_CHECKOUT:-...}`, so a caller that exports one --
# the loop does -- decides the key of every count. A case whose two failures were seeded under one key and read under
# another starts a fresh episode, and the third-failure stop never fires; the loop keeps landing, so the suite failed only
# under load. This runs the suite against a caller checkout whose base carries a `Refs #<n>` commit and requires the same
# answer, so a suite that reads the caller's tree again is caught.
# usage: bash test-wave-requeue-caller-checkout.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
H=$(mktemp -d)
/usr/bin/git init -q "$H"
/usr/bin/git -C "$H" -c user.name=t -c user.email=t@t commit -q --allow-empty -m start
/usr/bin/git -C "$H" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "work

Refs #9"
/usr/bin/git -C "$H" update-ref refs/remotes/origin/dev HEAD
if SG_CHECKOUT="$H" bash "$d/test-wave-requeue-cap.sh" > "$H/out" 2>&1; then
  echo "ok   the requeue cap suite answers the same with a caller's checkout that carries a Refs commit (#1486)"
  rm -rf "$H"; exit 0
fi
echo "FAIL the requeue cap suite read the caller's checkout (#1486): $(/usr/bin/grep -m1 '^FAIL' "$H/out")"
rm -rf "$H"; exit 1
