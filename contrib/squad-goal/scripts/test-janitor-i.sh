#!/bin/bash
# Regression test for janitor rule I (RUN-ON-CLOSED-ISSUES: a live run whose issues are all closed; duplicates/not_planned -> stop, otherwise report once), stubbed gh. usage: bash test-janitor-i.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/auto" "$T/co/runs"; cp "$src" "$T/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *issues/501*) printf 'closed\tnot_planned\tsome bug\n';;
  *issues/502*) printf 'closed\tcompleted\t[duplicate of #9] bug\n';;
  *issues/503*) printf 'closed\tcompleted\treal fix\n';;
  *issues/504*) printf 'open\t\tstill open\n';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; printf '%s\n' "{\"event\": \"spawn_accepted\", \"x\": \"GitHub issue #$2\"}" > "$T/co/runs/$1/record.jsonl"; [ -n "$3" ] && printf '%s\n' "{\"event\": \"spawn_accepted\", \"x\": \"GitHub issue #$3\"}" >> "$T/co/runs/$1/record.jsonl"; echo "$1" >> "$T/st/active-runs.txt"; }
mk run-a 501 502; mk run-b 503; mk run-c 501 504
# The router words a child in its own way ("GOAL (issue 501, SPEC 9 child)"); the dispatcher's `Refs #N` line is the link that holds (#551)
mkdir -p "$T/co/runs/run-d"; printf '%s\n' '{"event": "spawn_accepted", "child": "route.router.1", "definition": {"prompt": "Every commit message carries the line `Refs #501` on its own line.\\n\\nGOAL (issue 501, SPEC 9 child): fix it."}}' > "$T/co/runs/run-d/record.jsonl"; echo run-d >> "$T/st/active-runs.txt"
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main JANITOR_DRY=1 python3 -I "$T/janitor.py" 2>&1; }
o=$(run)
echo "$o" | /usr/bin/grep -q 'RUN-ON-CLOSED-ISSUES run-a.*stopping' && ok "all duplicate/not_planned -> stop" || ko "run-a not stopped: $o"
echo "$o" | /usr/bin/grep -q 'RUN-ON-CLOSED-ISSUES run-b.*not stopped' && ok "closed as completed -> report only" || ko "run-b: $o"
echo "$o" | /usr/bin/grep -q 'RUN-ON-CLOSED-ISSUES run-d.*stopping' && ok "a child named by its Refs line maps to its issue" || ko "run-d: the Refs #501 child mapped to no issue: $o"
echo "$o" | /usr/bin/grep -q 'RUN-ON-CLOSED-ISSUES run-c' && ko "run with an open issue reported" || ok "run with an open issue untouched"
# #585: a run keeps its folder in its own worktree (#580); active-runs.txt names it by its absolute path, and the stop reaches
# the processes inside that folder, never a <checkout>/runs/<name> that does not exist
T=$(cd "$T" && pwd -P); w="$T/co/worktrees/run/k1/runs/run-w"; mkdir -p "$w"
printf '%s\n' '{"event": "spawn_accepted", "x": "GitHub issue #502"}' > "$w/record.jsonl"; printf '%s\n' "$w" > "$T/st/active-runs.txt"
(cd "$w" && exec sleep 30) & pw=$!; disown "$pw"; sleep 0.3
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main python3 -I "$T/janitor.py" 2>&1)
for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pw" 2>/dev/null || break; sleep 0.2; done
kill -0 "$pw" 2>/dev/null && { ko "#585: the process inside a worktree run on closed duplicates survived: $o $(tail -3 "$T/st/cycle.log" 2>/dev/null)"; kill "$pw"; } || ok "#585: the stop reaches the processes inside a worktree run's folder"
rm -rf "$T"; exit $fail
