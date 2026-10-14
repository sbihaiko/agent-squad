#!/bin/bash
# Regression test for childcc.py (stub gh + board, no network). usage: bash test-childcc.sh [path/to/childcc.py]
# Live pass moves at once; a finished run is backfilled only when the card is Todo/In Progress/Blocked, never past Code Complete.
src=${1:-$(dirname "$0")/childcc.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/childcc.py"; cp "$(dirname "$src")/issue_state.py" "$T/"
printf '#!/bin/bash\necho "$@" >> %s/moves\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
a="$*"
case $a in
  *"-q .state"*) echo open;;
  *"POST"*"/sub_issues"*) echo "link $a" >> "$GH_LOG"; cat >/dev/null;;
  *"/sub_issues"*) echo $GH_KIDS | tr ' ' '\n' | head -n "$(cat "$GH_LOG" 2>/dev/null | grep -c '^link')" || true;;   # the sub-issues read: the children the carrier carries so far, one per link written
  *"POST"*"repos/x/y/issues"*) echo "create $(cat)" >> "$GH_LOG"; echo '{"number":900}';;
  *"issues?state=all"*) ;;
  *"repos/x/y/issues/"*) echo '{"id":1}';;
  *"/fields?"*) echo 7;;
  *"/items?"*) for kv in $GH_NUMS; do printf '%s\t%s\n' "${kv%%:*}" "${kv#*:}"; done | tr '_' ' ';;   # the one Status read: number, the state the card carries
esac
GH
chmod +x "$T/bin/gh"
run() { # name issue verdict
  mkdir -p "$T/co/runs/$1"; { echo '{"event": "spawn_accepted", "child": "c", "definition": "GitHub issue #'$2'"}'; echo '{"event": "outcome", "node": "c", "outcome": "pass", "reasons": ["ok"]}'; [ "$3" = v ] && echo '{"event": "verdict"}'; } > "$T/co/runs/$1/record.jsonl"; echo "$1" >> "$T/st/active-runs.txt"; }
# name child definition-json-string outcome
run_def() {
  mkdir -p "$T/co/runs/$1"
  python3 -c 'import json,sys; n,c,d,o=sys.argv[1:5]; open(sys.argv[5],"w").write(json.dumps({"event":"spawn_accepted","child":c,"definition":d})+"\n"+json.dumps({"event":"outcome","node":c,"outcome":o,"reasons":["ok"]})+"\n")' "$1" "$2" "$3" "$4" "$T/co/runs/$1/record.jsonl"
  echo "$1" >> "$T/st/active-runs.txt"
}
run_two() { # name: a run of two children, issues 2000 and 2001, and a run of one child, issue 2002
  mkdir -p "$T/co/runs/$1"; { echo '{"event": "spawn_accepted", "child": "a", "definition": "GitHub issue #2000"}'; echo '{"event": "spawn_accepted", "child": "b", "definition": "GitHub issue #2001"}'; } > "$T/co/runs/$1/record.jsonl"; echo "$1" >> "$T/st/active-runs.txt"; }
run live 1 n; run_two two; run_two fin-two; echo '{"event": "verdict"}' >> "$T/co/runs/fin-two/record.jsonl"; run solo 2002 n; run fin-todo 2 v; run fin-testing 3 v; run fin-prog 4 v
run_def nohash c "FORK-ONLY work for GitHub issue 1487 on the agent-squad local fork." pass
run_def issues c "FORK-ONLY work for GitHub issues 1491 on the fork." pass
run_def branch c "Create the worktree: git worktree add ~/wt-as-1492 -b gui/1492 local/next" pass
run_def hashfirst c "GitHub issue #1500 and GitHub issue 1487" pass
run_def unmapped c "a child whose prompt names no issue at all" pass
export GH_LOG=$T/gh.log GH_KIDS="2000 2001"; export SG_STATE=$T/st SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 PATH="$T/bin:$PATH" GH_NUMS="2:Todo 3:Testing 4:In_Progress"
cp "$T/childcc.py" "$T/childcc.py.run"; sed -i.bak "s#^board = .*#board = '$T/board.sh'#" "$T/childcc.py.run"
out=$(cd "$T/co" && python3 "$T/childcc.py.run" 2>&1)
echo "$out" | tail -20 >&2
m=$(cat "$T/moves" 2>/dev/null)
echo "$m" | /usr/bin/grep -q ' 1$' && ok "live pass: moved" || ko "live pass not moved"
echo "$m" | /usr/bin/grep -q ' 2$' && ok "finished run, card Todo: backfilled" || ko "Todo card not backfilled"
echo "$m" | /usr/bin/grep -q ' 4$' && ok "finished run, card In Progress: backfilled" || ko "In Progress card not backfilled"
echo "$m" | /usr/bin/grep -q ' 3$' && ko "card past Code Complete was moved" || ok "finished run, card Testing: left alone"
echo "$m" | /usr/bin/grep -q ' 1487$' && ok "GitHub issue N (no hash): moved" || ko "GitHub issue N (no hash) not moved"
ls "$T/st/cq"/*#1487-cc-*.md >/dev/null 2>&1 && ok "GitHub issue N (no hash): comment queued" || ko "GitHub issue N (no hash): no comment"
echo "$m" | /usr/bin/grep -q ' 1491$' && ok "GitHub issues N: moved" || ko "GitHub issues N not moved"
echo "$m" | /usr/bin/grep -q ' 1492$' && ok "gui/N / wt-as-N: moved" || ko "gui/N / wt-as-N not moved"
echo "$m" | /usr/bin/grep -q ' 1500$' && ok "hash pattern still wins first" || ko "hash pattern lost first-match"
echo "$out" | /usr/bin/grep -q 'CHILDCC UNMAPPED unmapped c' && ok "unmapped child logged once" || ko "unmapped child not logged: $out"
echo "$out" | /usr/bin/grep -c 'CHILDCC UNMAPPED unmapped c' | /usr/bin/grep -qx 1 && ok "unmapped child logged once only" || ko "unmapped child logged more than once"
grep -c '^create' "$T/gh.log" | grep -qx 1 && ok "two children: one parent created, none for the one-child run" || ko "parent creates: $(cat "$T/gh.log" 2>/dev/null)"
[ "$(grep -c '^link' "$T/gh.log")" = 2 ] && ok "two children: both linked as sub-issues" || ko "links: $(cat "$T/gh.log" 2>/dev/null)"
echo "$out" | /usr/bin/grep -q 'RUN PARENT #900 (two)' && ok "the run's parent is reported" || ko "no parent report: $out"
grep '^create' "$T/gh.log" | grep -q '"labels": \["run-parent"\]' && ok "the parent carries the run-parent label, so no pass dispatches it (#340)" || ko "parent create body: $(grep '^create' "$T/gh.log")"
echo "$out" | /usr/bin/grep -q 'RUN PARENT .*(fin-two)' && ko "a finished run got a parent (#340)" || ok "a finished run gets no parent (#340)"
rm -rf "$T"; exit $fail
