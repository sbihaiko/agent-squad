#!/bin/bash
# Regression test for janitor rule N (#1513): a run that ended with a failed verdict and landed nothing releases its issues --
# the card goes back to Todo (loop board) / To triage (bug board) and its dispatch marker is dropped, so the next cycle
# re-dispatches it. A run that landed work keeps its card and its marker, and so does a live run, or a card another step
# already moved past In Progress. Stubbed gh/board. usage: bash test-janitor-failed-dispatch.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
n=$(printf '%s' "$*" | sed -n 's/.*content.number==\([0-9]*\).*/\1/p')
case "$*" in
  *search/issues*) echo 0;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/1/fields*) echo FID1;;
  *projectsV2/4/items*) case "$n" in 701|702|703|704) echo "In Progress";; esac;;
  *projectsV2/1/items*) case "$n" in 703) echo "Doing";; esac;;
  *issues/70*) echo open;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; echo "$1" > "$T/co/runs/$1/request.txt"; }
# N1: failed run, nothing landed -> everything released
mk run-fail; printf 'CHILD — GitHub issue(s) 701 (bug; one PR):\n' > "$T/co/runs/run-fail/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #701 on fix/701-x"}\n{"event": "verdict", "verdict": "failed", "reasons": ["adapter_error"], "exit_code": 1}\n' > "$T/co/runs/run-fail/record.jsonl"
echo run-fail > "$T/st/auto/disp-701"
# N2: failed run that landed work (the gate had no acceptance command) -> card and marker kept
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
touch -t 202001010000 "$T/co/runs/run-fail/record.jsonl" "$T/co/runs/run-land/record.jsonl" "$T/co/runs/run-bug/record.jsonl"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BUG_DOING=Doing JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1)
/usr/bin/grep -q '^o 4 Todo 701$' "$T/board.calls" 2>/dev/null && ok "N: failed run -> card back to Todo" || ko "N #701 not demoted: $(cat "$T/board.calls" 2>/dev/null) $o"
[ ! -e "$T/st/auto/disp-701" ] && ok "N: failed run -> disp marker dropped" || ko "N disp-701 kept: $o"
/usr/bin/grep -q 'DISPATCH-FAILED #701' "$T/st/cycle.log" 2>/dev/null && ok "N: reported run + reason" || ko "N cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -l 'adapter_error' "$T/st/cq/"*701* >/dev/null 2>&1 && /usr/bin/grep -q 'run-fail' "$T/st/cq/"*701* && ok "N: comment names the run and the reason" || ko "N comment: $(ls "$T/st/cq" 2>/dev/null)"
[ -e "$T/st/auto/disp-702" ] && ! /usr/bin/grep -q ' 702$' "$T/board.calls" 2>/dev/null && ok "N: a run that landed work keeps its card and marker" || ko "N #702 touched: $(cat "$T/board.calls" 2>/dev/null)"
! /usr/bin/grep -q ' 704$' "$T/board.calls" 2>/dev/null && [ -e "$T/st/auto/disp-704" ] && ok "N: an issue a live run works is left alone" || ko "N #704 touched: $(cat "$T/board.calls" 2>/dev/null)"
/usr/bin/grep -q '^o 4 Todo 703$' "$T/board.calls" 2>/dev/null && /usr/bin/grep -q '^o 1 To triage 703$' "$T/board.calls" 2>/dev/null && [ ! -e "$T/st/auto/bug-703" ] && ok "N: autobug run -> loop board Todo + bug board To triage" || ko "N #703 not released: $(cat "$T/board.calls" 2>/dev/null)"
rm -rf "$T"; exit $fail
