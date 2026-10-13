#!/bin/bash
# Regression test for #14: a landing audits every merge the branch carries before it lands it. A merge the branch holds is a
# conflict a CHILD resolved in its own worktree, and no pass saw the resolution: a line both parents of that merge hold may be
# gone from it, and only the merge's two parents can say so. The fixture builds gui/1 with exactly that merge -- one hand
# resolution that dropped a line the parents agreed on -- and asserts the landing refuses it by name (file and line), records it
# on the issue, lands nothing, and does not retry the same head. Stub gh/test.sh, throwaway fork repo, offline.
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/forkclose.sh}; case $src in /*) ;; *) src="$PWD/$src";; esac
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
pass() { bash "$T/s/forkclose.sh" 2>&1; }
board() { printf '{"items":[%s]}\n' "$1" > "$T/co/runs/.hub/hub-board.json"; }
mkfix() {   # fresh state, stub gh/test.sh/env.sh, and an empty fork repo: each case builds the history it is about
  rm -rf "$T/s" "$T/st" "$T/co" "$T/bin" "$T/fork"; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/tests" "$T/st/req" "$T/bin" "$T/co/runs/.hub" "$T/fork"
  cp "$src" "$T/s/forkclose.sh"; cp "$d"/{forkonly.sh,fork-gate.sh,serial.sh,gate-suites.sh,merge-audit.sh} "$T/s/"; cp "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$T/s/"
  printf 'exit 0\n' > "$T/s/shell-suites.sh"; printf '#!/bin/bash\necho "pass-1"; exit 0\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_FORK_PY_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
  cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"pulls?state=open"*) ;;
  *"/comments"*) echo "TDD RED (test-x.sh, failure line): FAIL y";;
  *" -q .body"*) ;;
  *" -q .state"*) echo open;;
  *"-X PATCH"*"issues/"*) echo "$*" >> "$T/closed.log";;
  *"graphql"*) echo "[]";;
  *"--include"*) echo "{\"state\":\"open\"}";;
esac
J
  chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"; }
# gui/1 forks from the base, its own commit changes L2, and a sibling branch changes L2 too: the merge into gui/1 conflicts.
# The resolution keeps one side's L2 -- that is allowed -- and the question is L1 and L3, which BOTH parents hold.
fab() { g init -q -b local/next; printf 'L1\nL2\nL3\n' > "$T/fork/wat.sh"; g add .; g commit -q -m init
  g checkout -q -b side local/next; sed -i '' 's/^L2$/L2 side/' "$T/fork/wat.sh"; g commit -qam side
  g checkout -q -b gui/1 local/next; sed -i '' 's/^L2$/L2 one/' "$T/fork/wat.sh"; g commit -qam "work 1"
  g merge --no-commit side >/dev/null 2>&1 || true
  if [ "$1" = drop ]; then printf 'L1\nL2 one\n' > "$T/fork/wat.sh"; else printf 'L1\nL2 one\nL3\n' > "$T/fork/wat.sh"; fi
  g add wat.sh; g -c core.editor=true commit -q --no-edit; g rev-parse local/next; }

mkfix; b0=$(fab drop); board '{"n":1,"st":"Code Complete","s":"open"}'
r=$(pass)
case "$r" in *"FORKCLOSE FABRICATION #1"*) ok "the landing refuses the branch's own merge";; *) ko "no FORKCLOSE FABRICATION: '$r'";; esac
case "$r" in *"FORKCLOSE FABRICATION #1"*"gui/1"*) ok "and names the branch it came from";; *) ko "the branch is not named: '$r'";; esac
case "$r" in *"merge-audit: wat.sh:3:"*) ok "the audit names the file and the line the resolution dropped";; *) ko "no file:line from the audit: '$r'";; esac
case "$r" in *"L3"*) ok "and shows the dropped line";; *) ko "the dropped text is not shown: '$r'";; esac
[ "$(g rev-parse local/next)" = "$b0" ] && ok "nothing landed: the base is where it was" || ko "the fabricated merge reached the base"
[ ! -e "$T/closed.log" ] && ok "the issue was not closed" || ko "the issue was closed on a fabricated merge: $(cat "$T/closed.log")"
n=$(ls "$T/st/cq"/1-fabrication* 2>/dev/null | wc -l | tr -d ' ')
[ "$n" -ge 1 ] && ok "the fabrication is recorded on the issue" || ko "nothing was written to cq/ for the issue"
/usr/bin/grep -q 'wat.sh' "$T/st/cq"/1-fabrication*.md 2>/dev/null && ok "and the record carries the file the audit read" || ko "the record does not name the file"
r2=$(pass)
case "$r2" in *"FORKCLOSE FABRICATION"*) ko "the same head was retried on the next pass: '$r2'";; *) ok "the next pass does not retry the same head";; esac
[ "$(g rev-parse local/next)" = "$b0" ] && ok "and lands nothing for it" || ko "the base moved on the second pass"

# the same branch, its merge re-made keeping both sides: the refusal was the fabrication, not the branch
rm -rf "$T/fork"; mkdir -p "$T/fork"; b0=$(fab keep); board '{"n":1,"st":"Code Complete","s":"open"}'
r=$(pass)
case "$r" in *"FORKCLOSE FABRICATION"*) ko "a merge that keeps both sides was still refused: '$r'";; *) ok "a merge that keeps both sides is not a fabrication";; esac
case "$r" in *"FORKCLOSE #1 closed"*) ok "and the branch lands";; *) ko "the branch did not land: '$r'";; esac
g cat-file -e local/next:wat.sh 2>/dev/null && /usr/bin/grep -q '^L3$' <(g show local/next:wat.sh) && ok "the line both parents held is in the base" || ko "the landed tree lost the shared line"
rm -rf "$T"; exit $fail
