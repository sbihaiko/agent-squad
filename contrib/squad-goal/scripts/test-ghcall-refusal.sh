#!/bin/bash
# seconds: 1.16
# Regression test for ghcall.sh's refusal contract: a GraphQL `errors[]` body with no `data` (a rate limit comes back this
# way, with gh exit 0 or 1) is a refusal -- exit 2, GitHub's own message, logged as refused -- never "failed: no verdict".
# It runs the real door against a stub `gh`; nothing here touches the network. usage: bash test-ghcall-refusal.sh
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok() { echo "  ok: $1"; }
bad() { echo "  FAILED: $1"; fails=$((fails + 1)); }

for v in $(compgen -e | grep '^SG_'); do unset "$v"; done
mkdir -p "$T/bin" "$T/st" "$T/s"
export SG_STATE="$T/st" PATH="$T/bin:$PATH"
cp "$here/ghread.py" "$here/ghcall.sh" "$here/drainpolicy.py" "$T/s/"
LOG="$T/st/api.log"
printf '#!/bin/bash\nprintf "%%s" "$GQL_BODY"; exit "$GQL_RC"\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"
GQL='{"errors":[{"type":"RATE_LIMIT","code":"graphql_rate_limit","message":"API rate limit already exceeded for user ID 1."}]}'

echo "a GraphQL errors[] body is a refusal:"
for rc in 0 1; do
  rm -f "$LOG"
  o=$(. "$T/s/ghcall.sh"; GQL_BODY="$GQL" GQL_RC=$rc SG_GH_PASS=hub gh_get graphql -f query=x); got=$?
  [ "$got" -eq 2 ] && ok "gh_get exits 2 (gh exit $rc)" || bad "gh_get exit $got (gh exit $rc): $o"
  [ "$o" = "refused: GitHub refused: API rate limit already exceeded for user ID 1." ] && ok "the line carries GitHub's own message (gh exit $rc)" || bad "line (gh exit $rc): $o"
  tail -1 "$LOG" 2>/dev/null | grep -Eq " refused hub$" && ok "logged as refused (gh exit $rc)" || bad "log (gh exit $rc): $(tail -1 "$LOG" 2>/dev/null)"
done
o=$(. "$T/s/ghcall.sh"; GQL_BODY='{"data":{"x":1},"errors":[{"message":"partial"}]}' GQL_RC=0 gh_get graphql -f query=x); got=$?
[ "$got" -eq 0 ] && ok "an answer that carries data is still an answer" || bad "data answer exit $got: $o"

echo
[ "$fails" -eq 0 ] && { echo "ghcall-refusal: all cases passed"; exit 0; }
echo "ghcall-refusal: $fails case(s) FAILED"; exit 1
