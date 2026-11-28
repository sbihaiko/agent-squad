#!/bin/bash
# seconds: 1.13
# Regression test for janitor rule I (RUN-ON-CLOSED-ISSUES: a live run whose issues are all closed; duplicates/not_planned -> stop, otherwise report once), stubbed gh. usage: bash test-janitor-i.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# SG_SQUAD names the tree this suite sits in: the janitor copy sits in a temp folder, so its own path answers no root, and the
# daily pass clears every SG_* and runs from a cwd outside the repository (#3085). Every pass names it, never the caller's cwd.
sq=$(cd "$(dirname "$src")/../../.." && pwd -P); export SG_SQUAD="$sq"
mkdir -p "$T/bin" "$T/st/auto" "$T/co/runs"; cp "$src" "$T/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$T/board_columns.py"; cp "$(dirname "$src")/board-definition.json" "$T/board-definition.json"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *issues/501*) printf 'closed\tnot_planned\tsome bug\n';;
  *issues/502*) printf 'closed\tcompleted\t[duplicate of #9] bug\n';;
  *issues/503*) printf 'closed\tcompleted\treal fix\n';;
  *issues/504*) printf 'open\t\tstill open\n';;
  *issues/505*) printf 'closed\tcompleted\treal fix\t\t%s\n' "$CLOSE505";;
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
# #1571: an issue closed as completed after the run started does not keep a run alive. When every accepted issue is
# closed, the run's record moved after the close, its record shows no landing for the issue and no branch of its own
# names it, the run is ended with a verdict that says why. A landing of the run, or a branch of the run that names the
# issue, keeps it: the work may still be landing.
ago() { date -u -v-"$1"M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "-$1 minutes" +%Y-%m-%dT%H:%M:%SZ; }
export CLOSE505=$(ago 25); t30=$(ago 30); t20=$(ago 20); t27=$(ago 27)
mkr() { name=$1; shift; mkdir -p "$T/co/runs/$name"; : > "$T/co/runs/$name/record.jsonl"; p=1; for l in "$@"; do printf '%s\n' "${l%\}},\"pos\":$p}" >> "$T/co/runs/$name/record.jsonl"; p=$((p+1)); done; echo "$name" >> "$T/st/active-runs.txt"; }
rs() { printf '{"event":"run_start","run_id":"%s","graph_name":"g","graph_hash":"h","engine_options":{},"root":"route","plan":["route"],"ts":"%s"}' "$1" "$2"; }
snn() { printf '{"event":"spawn_accepted","request_key":"k","child":"route.router.1","parent":"route","depth":1,"envelope":{},"reservation":{},"parent_entry":1,"ts":"%s","definition":{"prompt":"Fix GitHub issue #505 in the repository"}}' "$1"; }
pg() { printf '{"event":"progress","node":"route.router.1","entry":1,"item":null,"session":"s","messages":1,"context_tokens":1,"tool":"Bash","ts":"%s"}' "$1"; }
mkr run-e "$(rs run-e "$t30")" "$(snn "$t30")" "$(pg "$t20")"
mkr run-f "$(rs run-f "$t30")" "$(snn "$t30")" '{"event":"land","node":"route.router.1","parent":"route","entry":1,"outcome":"pass","reasons":[],"effect":{"before":"aaa","head":"bbb"}}' "$(pg "$t20")"
mkr run-g "$(rs run-g "$t30")" "$(snn "$t30")" "$(pg "$t27")"
mkr run-h "$(rs run-h "$t30")" "$(snn "$t30")" "$(pg "$t20")"
mkdir -p "$T/co/runs/run-h/ws/child-1"; git -C "$T/co/runs/run-h/ws/child-1" init -q 2>/dev/null
git -C "$T/co/runs/run-h/ws/child-1" -c user.email=a@b -c user.name=a commit -q --allow-empty -m "work for #505" 2>/dev/null
git -C "$T/co/runs/run-h/ws/child-1" checkout -q -b fix/505-real-fix 2>/dev/null
o=$(run)
echo "$o" | /usr/bin/grep -q 'RUN-ON-CLOSED-ISSUES run-e.*holds no landing.*stopping' && ok "#1571: closed after the start, no landing, no branch -> stop" || ko "#1571: run-e not stopped: $o"
echo "$o" | /usr/bin/grep -q 'RUN-ON-CLOSED-ISSUES run-f.*not stopped' && ok "#1571: a landing of the run keeps it" || ko "#1571: run-f stopped over its own landing: $o"
echo "$o" | /usr/bin/grep -q 'RUN-ON-CLOSED-ISSUES run-g.*not stopped' && ok "#1571: an issue closed after the run's last progress keeps it" || ko "#1571: run-g stopped too early: $o"
echo "$o" | /usr/bin/grep -q 'RUN-ON-CLOSED-ISSUES run-h.*not stopped' && ok "#1571: a branch of the run that names the issue keeps it" || ko "#1571: run-h stopped over its own branch: $o"
mkr run-i "$(rs run-i "$t30")" "$(snn "$t30")" "$(pg "$t20")"
(cd "$T/co/runs/run-i" && exec sleep 30) & pi=$!; disown "$pi"; sleep 0.3
printf '%s\n' run-i > "$T/st/active-runs.txt"
o3=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main SG_SQUAD="$sq" python3 -I "$T/janitor.py" 2>&1)
for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pi" 2>/dev/null || break; sleep 0.2; done
kill -0 "$pi" 2>/dev/null && { ko "#1571: the process holding the run's gate survived: $o3"; kill "$pi"; } || ok "#1571: the stop reaches the process holding the run's gate"
printf '%s\n' run-e > "$T/st/active-runs.txt"
o2=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main SG_SQUAD="$sq" python3 -I "$T/janitor.py" 2>&1)
tail -1 "$T/co/runs/run-e/record.jsonl" | /usr/bin/grep -q '"verdict".*closed_without_landing' && ok "#1571: the ended run carries a verdict that says why" || ko "#1571: no verdict for the ended run: $o2 $(tail -2 "$T/co/runs/run-e/record.jsonl")"
# #585: a run keeps its folder in its own worktree (#580); active-runs.txt names it by its absolute path, and the stop reaches
# the processes inside that folder, never a <checkout>/runs/<name> that does not exist
T=$(cd "$T" && pwd -P); w="$T/co/worktrees/run/k1/runs/run-w"; mkdir -p "$w"
printf '%s\n' '{"event": "spawn_accepted", "x": "GitHub issue #502"}' > "$w/record.jsonl"; printf '%s\n' "$w" > "$T/st/active-runs.txt"
(cd "$w" && exec sleep 30) & pw=$!; disown "$pw"; sleep 0.3
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main python3 -I "$T/janitor.py" 2>&1)
for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pw" 2>/dev/null || break; sleep 0.2; done
kill -0 "$pw" 2>/dev/null && { ko "#585: the process inside a worktree run on closed duplicates survived: $o $(tail -3 "$T/st/cycle.log" 2>/dev/null)"; kill "$pw"; } || ok "#585: the stop reaches the processes inside a worktree run's folder"
rm -rf "$T"; exit $fail
