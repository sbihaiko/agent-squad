#!/bin/bash
# seconds: 0.97
# Late board writes are reported by actor, and a verified correction keeps both instants (#1022, ADR-0164, ADR-0149).
# board_lag.py reads the board facts of the issue ledger: `rows <repo>` prints the Monitor payload, and `correct` adds one
# correction or refuses it as a conflict. Corrections are a versioned file with a backup: a lost file marks the instants approximate.
# usage: bash test-board-lag.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_STATE=$T/st SG_REPO=x/y SG_PROJECT=4
L() { python3 "$d/issue_ledger.py" "$@"; }
B() { python3 "$d/board_lag.py" "$@"; }
J() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }
STORE=$T/st/board-corrections.json

[ "$(B rows x/y | J 'd["store"]')" = absent ] && [ "$(B rows x/y | J 'd["late"]')" = 0 ] && ok "no ledger and no store: nothing late, the store is absent" \
  || ko "empty: $(B rows x/y)"

L board 'x/y#1' run-a Doing /req/a 100 >/dev/null && L boardw 'x/y#1' run-a Doing "autobug dispatch" >/dev/null
[ "$(B rows x/y | J 'd["late"]')" = 1 ] && [ "$(B rows x/y | J 'd["rows"][0]["actor"]')" = "autobug dispatch" ] \
  && ok "a late write is listed under the actor that wrote it" || ko "rows: $(B rows x/y)"
[ "$(B rows x/y | J 'd["approximate"]')" = 1 ] && ok "a late write with no correction is approximate" || ko "approx: $(B rows x/y)"

LANDED=$(L read 'x/y#1' board | J 'd["landed"]')
[ -n "$LANDED" ] && ok "the ledger holds the write instant of the landed write" || ko "no landed: $(L read 'x/y#1' board)"

B correct x/y 1 4 Status run-z/Doing "$LANDED" 100 "ledger event" >/dev/null 2>&1; [ $? = 1 ] && [ ! -e "$STORE" ] \
  && ok "a correction with no matching candidate is a conflict and writes nothing" || ko "no-candidate: $(B correct x/y 1 4 Status run-z/Doing "$LANDED" 100 e 2>&1)"

LATER=$(python3 -c "import sys; print(float(sys.argv[1]) + 1000)" "$LANDED")
B correct x/y 1 4 Status run-a/Doing "$LANDED" "$LATER" "ledger event" >/dev/null 2>&1; [ $? = 1 ] && [ ! -e "$STORE" ] \
  && ok "a correction that moves an instant after its write is refused" || ko "order: $(B correct x/y 1 4 Status run-a/Doing "$LANDED" "$LATER" e 2>&1)"

B correct x/y 1 4 Status run-a/Doing "$LANDED" 100 "" >/dev/null 2>&1; [ $? = 1 ] && [ ! -e "$STORE" ] \
  && ok "a correction without evidence is refused" || ko "evidence: '$(B correct x/y 1 4 Status run-a/Doing "$LANDED" 100 "" 2>&1)'"

[ "$(B correct x/y 1 4 Status run-a/Doing "$LANDED" 100 "ledger event")" = recorded ] && [ -e "$STORE" ] \
  && ok "a verified correction is recorded in the versioned store" || ko "correct: $(B correct x/y 1 4 Status run-a/Doing "$LANDED" 100 e 2>&1)"
[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$STORE")" = 1 ] \
  && ok "the store carries its version" || ko "version: $(cat "$STORE")"
[ "$(B rows x/y | J 'd["approximate"]')" = 0 ] && [ "$(B rows x/y | J 'd["store"]')" = ok ] \
  && ok "a correction makes its instant exact" || ko "corrected: $(B rows x/y)"

rm -f "$STORE"; [ -e "$STORE.bak" ] && [ "$(B rows x/y | J 'd["store"]')" = backup ] \
  && [ "$(B rows x/y | J 'd["approximate"]')" = 0 ] && ok "a lost store is read from its backup" || ko "backup: $(B rows x/y)"

rm -f "$STORE" "$STORE.bak"
[ "$(B rows x/y | J 'd["store"]')" = absent ] && [ "$(B rows x/y | J 'd["approximate"]')" = 1 ] \
  && ok "a lost store marks the late instant approximate" || ko "lost: $(B rows x/y)"

printf '{' > "$STORE"; printf '{' > "$STORE.bak"
[ "$(B rows x/y | J 'd["store"]')" = lost ] && [ "$(B rows x/y | J 'd["approximate"]')" = 1 ] \
  && ok "a corrupt store and a corrupt backup read lost, never a precise instant" || ko "corrupt: $(B rows x/y)"
B correct x/y 1 4 Status run-a/Doing "$LANDED" 100 "ledger event" >/dev/null 2>&1; [ "$(cat "$STORE")" = "{" ] \
  && ok "a correction never overwrites a corrupt store" || ko "overwrote: '$(cat "$STORE")'"

rm -rf "$T"; exit $fail
