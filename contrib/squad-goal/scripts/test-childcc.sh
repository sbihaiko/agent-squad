#!/bin/bash
# Regression test for childcc.py (stub gh + board, no network). usage: bash test-childcc.sh [path/to/childcc.py]
# Live pass moves at once; a finished run is backfilled only when the card is Todo/In Progress/Blocked, never past Code Complete.
src=${1:-$(dirname "$0")/childcc.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/childcc.py"
printf '#!/bin/bash\necho "$@" >> %s/moves\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
a="$*"
case $a in
  *"-q .state"*) echo open;;
  *"/fields?"*) echo 7;;
  *"/items?"*) for kv in $GH_NUMS; do case $a in *"number==${kv%%:*})"*) echo "${kv#*:}" | tr '_' ' ';; esac; done;;
esac
GH
chmod +x "$T/bin/gh"
run() { # name issue verdict
  mkdir -p "$T/co/runs/$1"; { echo '{"event": "spawn_accepted", "child": "c", "definition": "GitHub issue #'$2'"}'; echo '{"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}'; [ "$3" = v ] && echo '{"event": "verdict"}'; } > "$T/co/runs/$1/record.jsonl"; echo "$1" >> "$T/st/active-runs.txt"; }
run live 1 n; run fin-todo 2 v; run fin-testing 3 v; run fin-prog 4 v
export SG_STATE=$T/st SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 PATH="$T/bin:$PATH" GH_NUMS="2:Todo 3:Testing 4:In_Progress"
cp "$T/childcc.py" "$T/childcc.py.run"; sed -i.bak "s#^board = .*#board = '$T/board.sh'#" "$T/childcc.py.run"
(cd "$T/co" && python3 "$T/childcc.py.run" 2>&1 | tail -5 >&2)
m=$(cat "$T/moves" 2>/dev/null)
echo "$m" | /usr/bin/grep -q ' 1$' && ok "live pass: moved" || ko "live pass not moved"
echo "$m" | /usr/bin/grep -q ' 2$' && ok "finished run, card Todo: backfilled" || ko "Todo card not backfilled"
echo "$m" | /usr/bin/grep -q ' 4$' && ok "finished run, card In Progress: backfilled" || ko "In Progress card not backfilled"
echo "$m" | /usr/bin/grep -q ' 3$' && ko "card past Code Complete was moved" || ok "finished run, card Testing: left alone"
rm -rf "$T"; exit $fail
