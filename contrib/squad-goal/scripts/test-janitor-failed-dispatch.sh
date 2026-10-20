#!/bin/bash
# Regression test for janitor rule O (#1513): a run that ended with a failed verdict and landed nothing releases its issues --
# rule R writes the loop-board card (Todo), rule O moves the bug card (To triage) and drops the dispatch marker, so the next cycle
# re-dispatches it. A run that landed work stops at Code Complete and keeps its marker (#239: a local landing is not a
# merge into the base, so it is not Done); a live run writes Run fields. Stubbed gh/board. usage: bash test-janitor-failed-dispatch.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$(dirname "$src")/issue_ledger.py" "$T/s/"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *search/issues*) echo 0;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/1/fields*) echo FID1;;
  *projectsV2/4/items*) for n in 701 702 703 704; do printf '%s\tIn Progress\n' "$n"; done;;   # one read, every card
  *projectsV2/1/items*) for n in 703; do printf '%s\tDoing\n' "$n"; done;;
  *issues/70*) echo open;;
  *issues/7*) echo open;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; echo "$1" > "$T/co/runs/$1/request.txt"; }
# N1: failed run, nothing landed -> everything released
mk run-fail; printf 'CHILD — GitHub issue(s) 701 (bug; one PR):\n' > "$T/co/runs/run-fail/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #701 on fix/701-x"}\n{"event": "verdict", "verdict": "failed", "reasons": ["adapter_error"], "exit_code": 1}\n' > "$T/co/runs/run-fail/record.jsonl"
echo run-fail > "$T/st/auto/disp-701"
# N2: failed run that landed work (the gate had no acceptance command) -> card Code Complete and marker kept
mk run-land; printf 'CHILD — GitHub issue(s) 702 (bug; one PR):\n' > "$T/co/runs/run-land/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #702 on fix/702-x"}\n{"event": "land", "outcome": "pass"}\n{"event": "verdict", "verdict": "failed", "reasons": ["ac-verify:no_ac_commands"], "exit_code": 1}\n' > "$T/co/runs/run-land/record.jsonl"
echo run-land > "$T/st/auto/disp-702"
# N3: failed autobug run -> both cards back (loop board Todo, bug board To triage), bug- marker dropped
mk run-bug; printf 'CHILD — GitHub issue #703 (bug; one PR):\n' > "$T/co/runs/run-bug/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #703 on fix/703-x"}\n{"event": "verdict", "verdict": "failed", "reasons": ["node_spend_cap"], "exit_code": 1}\n' > "$T/co/runs/run-bug/record.jsonl"
echo run-bug > "$T/st/auto/bug-703"
# N4: a live run (no verdict yet) owns its issue -> left alone
mk run-live; printf 'CHILD — GitHub issue(s) 704 (bug; one PR):\n' > "$T/co/runs/run-live/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #704 on fix/704-x"}\n' > "$T/co/runs/run-live/record.jsonl"
echo run-live > "$T/st/auto/disp-704"
# #609: each run that ended with nothing delivered is one failed dispatch, counted through `wave.sh fail` (stub records argv),
# with or without the marker; a needs-human run is the owner's (ADR-0029), and a run the ledger already lists is not sent again.
printf '#!/bin/bash\necho "$*" >> %s/wave.calls\n' "$T" > "$T/s/wave.sh"
mk run-nomark; printf 'CHILD — GitHub issue(s) 705 (one PR):\n' > "$T/co/runs/run-nomark/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #705"}\n{"event": "verdict", "verdict": "completed", "reasons": [], "exit_code": 0}\n' > "$T/co/runs/run-nomark/record.jsonl"
mk run-human; printf 'CHILD — GitHub issue(s) 706 (one PR):\n' > "$T/co/runs/run-human/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #706"}\n{"event": "verdict", "verdict": "needs-human", "reasons": ["ask"], "exit_code": 3}\n' > "$T/co/runs/run-human/record.jsonl"
mk run-dup; printf 'CHILD — GitHub issue(s) 707 (one PR):\n' > "$T/co/runs/run-dup/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #707"}\n{"event": "verdict", "verdict": "failed", "reasons": ["x"], "exit_code": 1}\n' > "$T/co/runs/run-dup/record.jsonl"
printf 'run-dup\t\tended failed: x\n' > "$T/st/auto/requeues-707.runs"; echo 1 > "$T/st/auto/requeues-707"
# #621: the run's own review of the child decides, not the reason text: a review that held its diff (CHANGES or BLOCKED) is
# the issue's own failure, passed as `own`. A run the loop ledger lists is not sent again either.
mk run-held; printf 'CHILD — GitHub issue(s) 708 (one PR):\n' > "$T/co/runs/run-held/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #708"}\n{"event": "land", "node": "c1", "outcome": "fail", "reasons": ["review_held"], "effect": {"name": "review", "review": {"verdict": "hold"}}}\n{"event": "verdict", "verdict": "failed", "reasons": ["review_held"], "exit_code": 1}\n' > "$T/co/runs/run-held/record.jsonl"
mk run-loopdup; printf 'CHILD — GitHub issue(s) 709 (one PR):\n' > "$T/co/runs/run-loopdup/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #709"}\n{"event": "verdict", "verdict": "failed", "reasons": ["adapter_error"], "exit_code": 1}\n' > "$T/co/runs/run-loopdup/record.jsonl"
printf 'run-loopdup\t\tended failed: adapter_error\n' > "$T/st/auto/loopqueues-709.runs"; echo 1 > "$T/st/auto/loopqueues-709"
mk run-chain; printf 'CHILD — GitHub issue(s) 710 (one PR):\n' > "$T/co/runs/run-chain/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #710"}\n{"event": "land", "node": "c1", "outcome": "fail", "reasons": ["review_held"], "effect": {"name": "review", "review": {"verdict": "hold", "findings": "VERDICT: BLOCKED (every review model failed)\\n"}}}\n{"event": "verdict", "verdict": "failed", "reasons": ["review_held"], "exit_code": 1}\n' > "$T/co/runs/run-chain/record.jsonl"
# #673: the janitor reads the issue's ledger file through its module; a run that file lists is not sent again either.
mk run-jdup; printf 'CHILD — GitHub issue(s) 711 (one PR):\n' > "$T/co/runs/run-jdup/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #711"}\n{"event": "verdict", "verdict": "failed", "reasons": ["x"], "exit_code": 1}\n' > "$T/co/runs/run-jdup/record.jsonl"
SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" fail 'x/y#711' run-jdup '' 'ended failed: x' >/dev/null
touch -t 202001010000 "$T/co/runs/run-jdup/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-chain/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-held/record.jsonl" "$T/co/runs/run-loopdup/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-fail/record.jsonl" "$T/co/runs/run-land/record.jsonl" "$T/co/runs/run-bug/record.jsonl" "$T/co/runs/run-nomark/record.jsonl" "$T/co/runs/run-human/record.jsonl" "$T/co/runs/run-dup/record.jsonl"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BUG_DOING=Doing JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1)
/usr/bin/grep -q '^o 4 Todo 701$' "$T/board.calls" 2>/dev/null && ok "O: failed run -> card back to Todo" || ko "O #701 not demoted: $(cat "$T/board.calls" 2>/dev/null) $o"
[ ! -e "$T/st/auto/disp-701" ] && ok "O: failed run -> disp marker dropped" || ko "O disp-701 kept: $o"
/usr/bin/grep -q 'DISPATCH-FAILED #701' "$T/st/cycle.log" 2>/dev/null && ok "O: reported run + reason" || ko "O cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -l 'adapter_error' "$T/st/cq/"*701* >/dev/null 2>&1 && /usr/bin/grep -q 'run-fail' "$T/st/cq/"*701* && ok "O: comment names the run and the reason" || ko "O comment: $(ls "$T/st/cq" 2>/dev/null)"
/usr/bin/grep -q '^o 4 Code Complete 702$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -q 'Done 702' "$T/board.calls" 2>/dev/null && [ -e "$T/st/auto/disp-702" ] && ok "O: a run that landed work stops at Code Complete, never Done, and keeps its marker" || ko "O #702 not reconciled: $(cat "$T/board.calls" 2>/dev/null)"
/usr/bin/grep -q -- '^--run-fields o 4 704 run-live ' "$T/board.calls" 2>/dev/null && [ -e "$T/st/auto/disp-704" ] && ok "O: an issue a live run works gets Run fields" || ko "O #704 not live-marked: $(cat "$T/board.calls" 2>/dev/null)"
/usr/bin/grep -q '^o 4 Todo 703$' "$T/board.calls" 2>/dev/null && /usr/bin/grep -q '^o 1 To triage 703$' "$T/board.calls" 2>/dev/null && [ ! -e "$T/st/auto/bug-703" ] && ok "O: autobug run -> loop board Todo + bug board To triage" || ko "O #703 not released: $(cat "$T/board.calls" 2>/dev/null)"
exp=$'fail 701 run-fail ended failed: adapter_error\nfail 703 run-bug ended failed: node_spend_cap\nfail 705 run-nomark ended completed: nothing landed\nfail 708 run-held ended failed: review_held own\nfail 710 run-chain ended failed: review_held loop'
[ "$(sort "$T/wave.calls" 2>/dev/null)" = "$exp" ] && ok "O: each run that delivered nothing counts once through wave.sh fail; landed, live, needs-human and listed runs do not" || ko "O fail calls: '$(cat "$T/wave.calls" 2>/dev/null)'"
rm -rf "$T"; exit $fail
