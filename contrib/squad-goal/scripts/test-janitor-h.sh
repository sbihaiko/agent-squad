#!/bin/bash
# Regression test for janitor rule H (RED-LOOP, STATE-REGRESSION from the issue's loop comments), stubbed gh. usage: bash test-janitor-h.sh [path/to/janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/auto" "$T/co/runs/.hub"; cp "$src" "$T/janitor.py"
cat > "$T/co/runs/.hub/hub-board.json" <<J
{"items":[{"n":1201,"st":"In Progress","s":"open"},{"n":1202,"st":"Blocked","s":"open"},{"n":1203,"st":"Done","s":"open"},{"n":1204,"st":"In Progress","s":"open"},{"n":1205,"st":"Code Complete","s":"open"}]}
J
cat > "$T/bin/gh" <<'J'
#!/bin/bash
red="Fork-only close: the fork suite failed after merging gui/x (abc) as def. Card back to In Progress."
case "$*" in
  *issues/1201/comments*) printf '2026-10-10T01:00:00Z\t%s\n' "$red" "$red" "$red";;
  *issues/1204/comments*) printf '2026-10-10T01:00:00Z\t%s\n' "$red" "$red" "Fork half merged: gui/x at abc into local/next as def, fork suite green (test pass t)." "$red";;
  *issues/1202/comments*) printf '2026-10-10T01:00:00Z\t%s\n' "Child c finished: pass. Card -> Code Complete (Testing starts later).";;
  *issues/1205/comments*) printf '2026-10-10T01:00:00Z\t%s\n' "Child c finished: pass. Card -> Code Complete (Testing starts later).";;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main JANITOR_DRY=1 python3 -I "$T/janitor.py" 2>&1; }
r=$(run)
case "$r" in *"RED-LOOP #1201: 3"*) ok "3 REDs with no green: RED-LOOP";; *) ko "no RED-LOOP for #1201: '$r'";; esac
case "$r" in *"RED-LOOP #1204"*) ko "a green close resets the count, #1204 reported";; *) ok "a green fork close resets the count";; esac
case "$r" in *"STATE-REGRESSION #1202: card is Blocked"*) ok "Blocked after reaching Code Complete: STATE-REGRESSION";; *) ko "no regression for #1202: '$r'";; esac
case "$r" in *"STATE-REGRESSION #1203: card is Done but"*) ok "Done card on an open issue: STATE-REGRESSION";; *) ko "no regression for #1203: '$r'";; esac
case "$r" in *"#1205"*) ko "Code Complete after Code Complete reported";; *) ok "a card still at Code Complete is fine";; esac
rm -rf "$T"; exit $fail
