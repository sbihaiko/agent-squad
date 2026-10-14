#!/bin/bash
# Regression test for janitor rule G (READY-NOT-MERGED: APPROVE + clean + old, not merged -> one autopilot pass), stubbed gh and autopilot. usage: bash test-janitor-g.sh [path/to/janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/auto" "$T/co"; cp "$src" "$T/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"; printf '#!/bin/bash\necho x >> %s/kicks\n' "$T" > "$T/autopilot.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"pulls?state=open"*) printf '%s\n' "10 aaaaaaaaaaaa main" "11 bbbbbbbbbbbb main" "12 cccccccccccc main" "13 dddddddddddd main" "14 eeeeeeeeeeee main" "15 ffffffffffff feat";;
  *pulls/14*) echo blocked;;
  *pulls/*) echo clean;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
o() { printf '%s\n' "#$1 ${2:0:9} clean CI=success:9 VERDICT: $3" > "$T/st/revci-$1.out"; [ "$4" = old ] && touch -t 202001010000 "$T/st/revci-$1.out"; }
o 10 aaaaaaaaaaaa APPROVE old; o 11 bbbbbbbbbbbb CHANGES old; o 12 cccccccccccc APPROVE new; o 13 dddddddddddd APPROVE old; touch "$T/st/auto/merged-13"; o 14 eeeeeeeeeeee APPROVE old; o 15 ffffffffffff APPROVE old
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main python3 -I "$T/janitor.py" 2>&1; }
run >/dev/null
[ "$(wc -l < "$T/kicks" 2>/dev/null | tr -d ' ')" = 1 ] && ok "one autopilot pass for the single stuck PR" || ko "kicks: $(cat "$T/kicks" 2>/dev/null | wc -l)"
/usr/bin/grep -q 'READY-NOT-MERGED #10' "$T/st/cycle.log" && ok "#10 (APPROVE, clean, old) reported" || ko "#10 not reported"
/usr/bin/grep -qE 'READY-NOT-MERGED #(11|12|13|14|15)' "$T/st/cycle.log" && ko "reported CHANGES/fresh/merged/blocked/stacked PR" || ok "CHANGES, fresh, merged, blocked and stacked PRs left alone"
run >/dev/null; [ "$(wc -l < "$T/kicks" | tr -d ' ')" = 1 ] && ok "second pass on the same head: no second kick" || ko "kicked again"
rm -rf "$T"; exit $fail
