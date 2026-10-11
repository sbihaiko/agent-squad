#!/bin/bash
# Regression test for mkreq.sh's dispatch-time slice gate (ADR-0274, Refs #1400). usage: bash test-mkreq.sh
# Offline: gh is a stub that reports one open PR touching scripts/shared.py; HOME is a throwaway directory.
d=$(dirname "$0"); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
S=$T/scripts; mkdir -p "$S" "$T/bin" "$T/home"
cp "$d/mkreq.sh" "$d/models.json" "$d/req-checklist.txt" "$d/req-checklist-fork.txt" "$S/"
echo 'export SG_REPO=o/r' > "$S/env.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
case "$*" in *"/files"*) echo scripts/shared.py;; *pulls\?state=open*) echo 7;; esac
GH
chmod +x "$T/bin/gh"
export HOME=$T/home PATH="$T/bin:$PATH"; R=$HOME/.cache/squad-goal/req
body() { printf 'IMPORTANT: your first and only action is X\nPANEL: %s\nEdit %s\n' "$1" "$2" > "$T/$3.body"; }
run() { bash "$S/mkreq.sh" "$1" "$T/$1.body" 2>"$T/$1.err" >"$T/$1.out"; }
# 1. a same-panel overlap refuses, names both requests and the shared path, and writes no request
body p1 scripts/a.py a; body p1 scripts/a.py b
run a || ko "first sibling refused"; [ -f "$R/a.txt" ] && ok "first sibling builds" || ko "no request for a"
run b && ko "same-panel overlap launched" || ok "same-panel overlap refuses"
/usr/bin/grep -q 'request b and request a' "$T/b.err" && /usr/bin/grep -q 'scripts/a.py' "$T/b.err" && ok "refusal names both requests and the path" || ko "refusal text: $(cat "$T/b.err")"
[ -f "$R/b.txt" ] && ko "a refused request left a file" || ok "refused request wrote no file"
# 2. a disjoint panel launches
body p2 scripts/c.py c; run c && ok "disjoint panel launches" || ko "disjoint refused: $(cat "$T/c.err")"
# 3. an overlap with an open PR outside the panel launches
body p3 scripts/shared.py e; run e && [ -f "$R/e.txt" ] && ok "open-PR overlap outside the panel launches" || ko "open-PR overlap refused: $(cat "$T/e.err")"
# 4. a different panel naming the same file as a pending request is not a sibling
body p4 scripts/a.py f; run f && ok "other panel's pending request is not a sibling" || ko "cross-panel refused"
# 5. fork-only checklist + emitted request tell the child to commit incrementally (Refs #1505)
printf 'IMPORTANT: your first and only action is X\nFork-only work: no PR.\nEdit scripts/x.py\n' > "$T/fork.body"
run fork && [ -f "$R/fork.txt" ] && ok "fork-only request builds" || ko "fork-only request: $(cat "$T/fork.err" 2>/dev/null)"
G=/usr/bin/grep
for w in "commit early and often" "as soon as a slice compiles or its test runs" "commit again after each further step" "squash"; do
  $G -qF -- "$w" "$S/req-checklist-fork.txt" && $G -qF -- "$w" "$R/fork.txt" && ok "fork request carries '$w'" || ko "fork request lacks '$w'"
done
# 6. a child that follows that instruction then dies at the spend cap leaves git log <base>..gui/<N> non-empty
F=$T/cap; /usr/bin/git init -q -b local/next "$F"
(cd "$F" && /usr/bin/git -c user.email=a@b -c user.name=n commit -q --allow-empty -m base \
  && /usr/bin/git checkout -q -b gui/42)
echo work > "$F/x.py"
if $G -qF -- "commit early and often" "$R/fork.txt" && $G -qF -- "as soon as a slice compiles or its test runs" "$R/fork.txt" && $G -qF -- "commit again after each further step" "$R/fork.txt"; then
  (cd "$F" && /usr/bin/git add x.py && /usr/bin/git -c user.email=a@b -c user.name=n commit -q -m slice)
fi
n=$(/usr/bin/git -C "$F" log --oneline local/next..gui/42 | wc -l | tr -d ' ')
[ "$n" -ge 1 ] && ok "cap-death: git log local/next..gui/42 is non-empty" || ko "cap-death: gui/42 empty after spend-cap death"
rm -rf "$T"; exit $fail
