#!/bin/bash
# Regression test for reconcile.py's scope: RECONCILE_MIN floors open issues only; a closed issue of any number reaches Done (stub gh and board.sh, no network). usage: bash test-reconcile.sh [path/to/reconcile.py]
src=${1:-$(dirname "$0")/reconcile.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/bin" "$T/st"; test -f "$(dirname "$src")/board_columns.py" || exit 1; cp "$src" "$T/s/reconcile.py"; cp "$(dirname "$src")/board_columns.py" "$T/s/board_columns.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"
printf '#!/bin/bash\necho "$*" >> "%s/moves"\n' "$T" > "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  "api users/x") echo '{"type":"User"}';;
  *"/fields"*) echo '[{"id":7,"name":"Status"}]';;
  *"/items"*) printf '1090\tTodo\thttps://api.github.com/repos/x/y\n1092\tTesting\thttps://api.github.com/repos/x/y\n1095\tTodo\thttps://api.github.com/repos/x/y\n1110\tTesting\thttps://api.github.com/repos/x/y\n';;   # the one Status read of the seam: number, state, repository url
  *"issues/1090") echo '{"state":"closed","body":""}';;
  *"issues/1092") echo '{"state":"closed","body":""}';;
  *"issues/1095") echo '{"state":"open","body":""}';;
  *"issues/1110") echo '{"state":"closed","body":""}';;
  *"pulls?state=open"*) echo '[]';;
  *"issues?state=open&labels=bug"*) echo '[]';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH" SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_STATE="$T/st" RECONCILE_MIN=1102
r=$(RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1); m=$(cat "$T/moves" 2>/dev/null)
case "$m" in *1090*) ok "closed #1090 below RECONCILE_MIN moves to Done";; *) ko "closed #1090 not moved: '$r'";; esac
case "$m" in *1092*) ok "closed #1092 below RECONCILE_MIN moves to Done";; *) ko "closed #1092 not moved: '$r'";; esac
case "$m" in *1110*) ok "closed #1110 above RECONCILE_MIN still moves to Done";; *) ko "closed #1110 not moved: '$r'";; esac
case "$r" in *"#1095"*) ko "open #1095 below RECONCILE_MIN was derived: '$r'";; *) ok "open issue below RECONCILE_MIN stays out of scope";; esac
rm -rf "$T"; exit $fail
