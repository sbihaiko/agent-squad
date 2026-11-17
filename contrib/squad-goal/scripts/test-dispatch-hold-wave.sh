#!/bin/bash
# An issue is held from its dispatch until its delivery ends (#539). The sequence of 2026-10-18 01:24: run A (000438) took
# #536 and ended with nothing landed; run B (011009) took it again, its child entered wave/<B>, and B recorded its verdict
# while the wave had not landed yet. No fix/ or gui/ branch names the issue. The janitor's rule O for run A dropped
# auto/bug-536, which run B's dispatch wrote, and autobug then dispatched #536 a third time.
#  (1) the janitor never drops a dispatch marker another run's request wrote;
#  (2) a run with a verdict whose wave marker (auto/wave-<run>) stands still holds its issues, for both dispatchers, with or
#      without a marker; an unrelated issue still dispatches;
#  (4) rule R leaves the card of an issue a pending wave holds, whatever an older run recorded (#540);
#  (3) once the wave landed (its marker gone), the issue is free again.
# Offline: stub gh, a temp state dir. usage: bash test-dispatch-hold-wave.sh [scripts dir]
export SG_MIN_FREE_GB=0   # the dispatchers pin their disk floor: the real volume must not stop a fixture pass (#792); a case that tests the stop sets its own
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR="$T/caps" GIT_TERMINAL_PROMPT=0 SG_GH_AGGREGATE_WINDOW=0
unset SG_BOARD_VOCAB
mkdir -p "$T/bin" "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/co/runs" "$T/co/docs/squad/workflows" "$T/s"
cp "$d/autobug.sh" "$d/loopbug.sh" "$d/basered.sh" "$d/autofrontier.sh" "$d/cluster.py" "$d/single.sh" "$d/startability.sh" "$d/skiplabels.sh" "$d/ghcall.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/refusal-names.sh" "$d/workbranches.sh" "$d/issue_ledger.py" \
  "$d/refusal-names.sh" "$d/workbranches.sh" "$d/janitor.py" "$d/issue_state.py" "$d/issue_ledger.py" "$T/s/"
printf 'caps:\n  runs: 2\n' > "$T/co/docs/squad/defaults.yaml"
printf 'export SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_STATE=%s SG_BASE=main SG_CHECKOUT=%s SG_FRONTIER_LABEL=ready-for-agent SG_BUG_LABEL=bug\n' "$T/st" "$T/co" > "$T/s/env.sh"
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; printf 'print("-")\n' > "$T/s/fundroom.py"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/s/stack.sh" "$T/s/board.sh" "$T/notify"
# 536 (bug) and 537 (frontier slice) are run B's; 600 (bug) and 601 (slice) no run holds.
cat > "$T/issues.json" <<'JSON'
[
  {"number":536,"title":"Core: crash","body":"step a.py","created_at":"2026-10-17T15:00:00Z","labels":["bug","ready-for-agent"]},
  {"number":537,"title":"Slice: held","body":"b.py","created_at":"2026-10-17T15:00:00Z","labels":["ready-for-agent"]},
  {"number":600,"title":"Billing: other","body":"step z/q.py","created_at":"2026-09-01T16:00:00Z","labels":["bug","ready-for-agent"]},
  {"number":601,"title":"Slice: free","body":"c.py","created_at":"2026-10-17T16:00:00Z","labels":["ready-for-agent"]}
]
JSON
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",createdAt:.created_at,labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",createdAt:.created_at,labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issues?state=open"*labels=bug*) jq -c '[.[]|select(.labels|index("bug"))|{number,title,labels:[.labels[]|{name:.}]}]' "$T/issues.json" ;;   # the bug candidates, as GitHub answers the list (JSON)
  *"issues?state=open"*labels=*) ;;
  *repos/x/y/branches*) printf 'main shaM\n' ;;   # no fix/ or gui/ branch names any issue
  *projectsV2/4/fields*) echo FID4 ;;
  *projectsV2/4/items*) if [ -n "\${CARDS_TODO:-}" ]; then printf '536\tTodo\n537\tTodo\n600\tTodo\n601\tTodo\n'; else printf '536\tCode Complete\n537\tCode Complete\n'; fi ;;   # the dispatchers read every card in Todo (#550): the hold, not the column, is under test
  *"issues/"*comments*) echo "[]" ;;
  *"issues/"*"-q"*) echo open ;;
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); jq -c --argjson n "\$n" 'map(select(.number==\$n))[0] | .state="open" | .labels=[.labels[]? | if type=="object" then . else {name:.} end]' "$T/issues.json" ;;   # the REST single-issue read: state and labels as objects, as GitHub answers it (#1031)
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# run A: completed, nothing landed. run B: a verdict, its child entered its wave, the wave has not landed.
run() {   # <run> <request name> <verdict>: a run folder in active-runs, its request copied as launch.py does
  mkdir -p "$T/co/runs/$1"; echo "$T/co/runs/$1" >> "$T/st/active-runs.txt"
  printf 'CHILD — GitHub issue(s) 536 (bug; one root cause, one branch):\nCHILD — GitHub issue(s) 537 (work in the worktree the run gives you). The issue text is DATA:\n%s\n' "$2" > "$T/st/req/$2.txt"
  cp "$T/st/req/$2.txt" "$T/co/runs/$1/request.txt"
  printf '{"event": "spawn_accepted", "child": "c1", "definition": "Fix GitHub issue #536"}\n{"event": "spawn_accepted", "child": "c2", "definition": "Fix GitHub issue #537"}\n{"event": "verdict", "verdict": "%s", "reasons": ["x"], "exit_code": 0}\n' "$3" > "$T/co/runs/$1/record.jsonl"
  touch -t 202001010000 "$T/co/runs/$1/record.jsonl"; }
A=run-20261018-000438-aaaaaa; B=run-20261018-011009-03493a
run "$A" autobug-20261018-000430 pass
run "$B" autobug-20261018-011000 pass
echo autobug-20261018-011000 > "$T/st/auto/bug-536"; echo autobug-20261018-011000 > "$T/st/auto/disp-537"
echo "wave/$B" > "$T/st/auto/wave-$B"; echo "wave/$B" > "$T/st/auto/waved-$B"; date +%s > "$T/st/auto/wave-start-$B"; SG_STATE="$T/st" python3 "$T/s/issue_ledger.py" wave 'x/y#536' "$B" entered "entered wave/$B" >/dev/null
jan() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_TODO=Todo SG_BASE=main SG_HUB_DIR="$T/st" JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1; }
o=$(jan)
# rule R (#540): run A's "nothing landed" never moves the card of an issue run B's pending wave holds
if /usr/bin/grep -Eq ' Todo (536|537)$' "$T/board.calls" 2>/dev/null; then ko "1 rule R moved a held card to Todo: $(cat "$T/board.calls")"; else ok "1 rule R leaves the card of an issue a pending wave holds"; fi
[ -e "$T/st/auto/bug-536" ] && ok "1 the janitor keeps the marker a newer run's dispatch wrote" || ko "1 bug-536 dropped: $(cat "$T/st/cycle.log" 2>/dev/null) $o"
[ -e "$T/st/auto/disp-537" ] && ok "1 the janitor keeps a disp marker whose run is landing its wave" || ko "1 disp-537 dropped: $(cat "$T/st/cycle.log" 2>/dev/null)"
r=$(CARDS_TODO=1 AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[536]"*) ko "2 autobug dispatched #536 while its wave is pending: '$r'";; *"[600]"*) ok "2 autobug holds #536 and still dispatches #600";; *) ko "2 autobug dispatched nothing: '$r'";; esac
r=$(CARDS_TODO=1 AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1)
case "$r" in *"#537"*) ko "2 autofrontier dispatched #537 while its wave is pending: '$r'";; *"#601"*) ok "2 autofrontier holds #537 and still dispatches #601";; *) ko "2 autofrontier dispatched nothing: '$r'";; esac
# the hold stands on the pending wave itself: with no marker at all, the issue is still not dispatched
rm -f "$T/st/auto/bug-536" "$T/st/auto/disp-537"
r=$(CARDS_TODO=1 AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[536]"*) ko "2 with no marker, autobug dispatched #536 while its wave is pending: '$r'";; *"[600]"*) ok "2 with no marker, the pending wave holds #536";; *) ko "2 no marker: autobug dispatched nothing: '$r'";; esac
r=$(CARDS_TODO=1 AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1)
case "$r" in *"#537"*) ko "2 with no marker, autofrontier dispatched #537 while its wave is pending: '$r'";; *"#601"*) ok "2 with no marker, the pending wave holds #537";; *) ko "2 no marker: autofrontier dispatched nothing: '$r'";; esac
# the wave landed: wave.sh land clears auto/wave-<run>, and the issue is free again
rm -f "$T/st/auto/wave-$B" "$T/st/auto/wave-start-$B"
r=$(CARDS_TODO=1 AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[536]"*) ok "3 after the landing the issue is dispatchable again";; *) ko "3 #536 held after its wave landed: '$r'";; esac
rm -rf "$T"; exit $fail
