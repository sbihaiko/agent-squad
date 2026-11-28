#!/bin/bash
# seconds: 0.88
# Regression test for janitor rule O (#1513): a run that ended with a failed verdict and landed nothing releases its issues --
# rule R writes the loop-board card (Todo), rule O moves the bug card (To triage) and drops the dispatch marker, so the next cycle
# re-dispatches it. A run that landed work stops at Batching and keeps its marker (#239: a local landing is not a
# merge into the base, so it is not Done); a live run writes Run fields. Stubbed gh/board.
# The pass loads squad/procs.py from its own folder, then SG_SQUAD, then the caller's cwd and its parents. The fixture copies
# the janitor into a temp folder, so the first answer is empty; with no SG_SQUAD the root falls to the caller's cwd, and the
# daily pass runs this suite from outside the repository with every SG_* cleared, where the import fails before the first
# gh call. The suite names SG_SQUAD and runs the pass from the fixture folder, so its result never depends on where it runs (#3071).
# usage: bash test-janitor-failed-dispatch.sh [janitor.py]
unset SG_BOARD_VOCAB   # this suite reads the legacy option names, so the caller's vocabulary never decides it
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
here="$(cd "$(dirname "$src")" && pwd)"; repo=$(cd "$here/../../.." && pwd)   # the tree this suite sits in: the janitor reads squad/ from SG_SQUAD, never from the caller's cwd
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$(dirname "$src")/issue_ledger.py" "$T/s/"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$(dirname "$src")/board_facts.py" "$T/s/"
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
# N2: failed run that landed work (the gate had no acceptance command) -> card stops at Batching (ADR-0143: a landing is delivered work) and marker kept
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
SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" fail 'x/y#707' run-dup '' 'ended failed: x' >/dev/null
# #621: the run's own review of the child decides, not the reason text: a review that held its diff (CHANGES or BLOCKED) is
# the issue's own failure, passed as `own`. A run the loop ledger lists is not sent again either.
mk run-held; printf 'CHILD — GitHub issue(s) 708 (one PR):\n' > "$T/co/runs/run-held/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #708"}\n{"event": "land", "node": "c1", "outcome": "fail", "reasons": ["review_held"], "effect": {"name": "review", "review": {"verdict": "hold"}}}\n{"event": "verdict", "verdict": "failed", "reasons": ["review_held"], "exit_code": 1}\n' > "$T/co/runs/run-held/record.jsonl"
mk run-loopdup; printf 'CHILD — GitHub issue(s) 709 (one PR):\n' > "$T/co/runs/run-loopdup/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #709"}\n{"event": "verdict", "verdict": "failed", "reasons": ["adapter_error"], "exit_code": 1}\n' > "$T/co/runs/run-loopdup/record.jsonl"
SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" fail 'x/y#709' run-loopdup '' 'ended failed: adapter_error' loop >/dev/null
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
# #2217: a failed run whose artifact named an end class passes it as the fifth argument. No review gave a kind, so none is sent:
# the empty kind keeps the loop-or-own decision to wave.sh, and the class is never read as the kind.
mk run-oc; printf 'CHILD — GitHub issue #714 (one PR):\n' > "$T/co/runs/run-oc/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #714"}\n{"event": "verdict", "verdict": "failed", "reasons": ["owner-only: needs a credential"], "exit_code": 1, "end_class": "owner-only"}\n' > "$T/co/runs/run-oc/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-oc/record.jsonl"
# #1892: a run whose proof did not finish is no failed dispatch (ADR-0164): three unproven-only runs of one issue count nothing,
# and a red run that also stayed unproven counts once, with its red reason named.
for r in u1 u2 u3; do mk run-$r; printf 'CHILD — GitHub issue #712 (one PR):\n' > "$T/co/runs/run-$r/request.txt"
  printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #712"}\n{"event": "verdict", "verdict": "completed", "reasons": ["unproven:route.router.1"], "exit_code": 0}\n' > "$T/co/runs/run-$r/record.jsonl"
  touch -t 202001010000 "$T/co/runs/run-$r/record.jsonl"; done
mk run-r1; printf 'CHILD — GitHub issue #713 (one PR):\n' > "$T/co/runs/run-r1/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #713"}\n{"event": "verdict", "verdict": "failed", "reasons": ["ac_failed:exit 3", "unproven:route.router.1"], "exit_code": 1}\n' > "$T/co/runs/run-r1/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-r1/record.jsonl"
for r in u4 u5; do mk run-$r; printf 'CHILD — GitHub issue #713 (one PR):\n' > "$T/co/runs/run-$r/request.txt"
  printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #713"}\n{"event": "verdict", "verdict": "completed", "reasons": ["unproven:route.router.1"], "exit_code": 0}\n' > "$T/co/runs/run-$r/record.jsonl"
  touch -t 202001010000 "$T/co/runs/run-$r/record.jsonl"; done
# #2245: a run that changed no file and passed its land with review_nochange counts once as `nochange`, even though its reasons
# are unfinished proofs (an empty selection never runs a suite), so the issue never reaches a stop by repeated empty waves.
mk run-nc; printf 'CHILD — GitHub issue #715 (one PR):\n' > "$T/co/runs/run-nc/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #715"}\n{"event": "land", "node": "c1", "outcome": "pass", "reasons": ["review_nochange"], "effect": {"name": "review", "before": "a1b2c3", "head": "a1b2c3", "review": {"verdict": "no_change"}}}\n{"event": "verdict", "verdict": "completed", "reasons": ["unproven:route.router.1", "unproven:route"], "exit_code": 0}\n' > "$T/co/runs/run-nc/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-nc/record.jsonl"
# #2245: the cause belongs to one (child, issue). A second child's own review decides its own issue: the no-change child
# marks only the issue it names, and the sibling issue keeps its own kind.
mk run-nc2; printf 'CHILD — GitHub issue(s) 718, 719 (one PR):\n' > "$T/co/runs/run-nc2/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #718"}\n{"event": "spawn_accepted", "child": "c2", "definition": "GitHub issue #719"}\n{"event": "land", "node": "c1", "outcome": "pass", "reasons": ["review_nochange"], "effect": {"name": "review", "before": "a1b2c3", "head": "a1b2c3", "review": {"verdict": "no_change"}}}\n{"event": "land", "node": "c2", "outcome": "fail", "reasons": ["review_held"], "effect": {"name": "review", "review": {"verdict": "hold"}}}\n{"event": "verdict", "verdict": "completed", "reasons": ["review_held"], "exit_code": 0}\n' > "$T/co/runs/run-nc2/record.jsonl"
# A land that did not pass is no no-change run, whatever its review said: its reasons stay unfinished proofs, so nothing counts.
mk run-ncfail; printf 'CHILD — GitHub issue #716 (one PR):\n' > "$T/co/runs/run-ncfail/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #716"}\n{"event": "land", "node": "c1", "outcome": "fail", "reasons": ["review_nochange"], "effect": {"name": "review", "before": "a1b2c3", "head": "d4e5f6", "review": {"verdict": "no_change"}}}\n{"event": "verdict", "verdict": "completed", "reasons": ["unproven:route.router.1"], "exit_code": 0}\n' > "$T/co/runs/run-ncfail/record.jsonl"
# A verdict written in another case still names the no-change end (the verdict comparison is case-blind).
mk run-nccase; printf 'CHILD — GitHub issue #717 (one PR):\n' > "$T/co/runs/run-nccase/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #717"}\n{"event": "land", "node": "c1", "outcome": "pass", "reasons": ["review_nochange"], "effect": {"name": "review", "before": "a1b2c3", "head": "a1b2c3", "review": {"verdict": "No_Change"}}}\n{"event": "verdict", "verdict": "completed", "reasons": ["unproven:route.router.1"], "exit_code": 0}\n' > "$T/co/runs/run-nccase/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-nc2/record.jsonl" "$T/co/runs/run-ncfail/record.jsonl" "$T/co/runs/run-nccase/record.jsonl"
# A run that found the bug does not reproduce made no wrong work: it is no failed dispatch, so nothing reaches wave.sh fail,
# and the owner decides the issue (ADR-0029). One comment names the run and its evidence.
mk run-nr; printf 'CHILD — GitHub issue #720 (one PR):\n' > "$T/co/runs/run-nr/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #720"}\n{"event": "verdict", "verdict": "no-repro", "reasons": ["the defect does not reproduce at the tip"], "exit_code": 40, "end_class": "does-not-reproduce"}\n' > "$T/co/runs/run-nr/record.jsonl"
echo run-nr > "$T/st/auto/disp-720"
touch -t 202001010000 "$T/co/runs/run-nr/record.jsonl"
o=$(cd "$T" && SG_SQUAD="$repo" SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_BUG_DOING=Doing JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1)
/usr/bin/grep -q '^o 4 Todo 701$' "$T/board.calls" 2>/dev/null && ok "O: failed run -> card back to Todo" || ko "O #701 not demoted: $(cat "$T/board.calls" 2>/dev/null) $o"
[ ! -e "$T/st/auto/disp-701" ] && ok "O: failed run -> disp marker dropped" || ko "O disp-701 kept: $o"
/usr/bin/grep -q 'DISPATCH-FAILED #701' "$T/st/cycle.log" 2>/dev/null && ok "O: reported run + reason" || ko "O cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -l 'adapter_error' "$T/st/cq/"*701* >/dev/null 2>&1 && /usr/bin/grep -q 'run-fail' "$T/st/cq/"*701* && ok "O: comment names the run and the reason" || ko "O comment: $(ls "$T/st/cq" 2>/dev/null)"
# A failed run's own local landing moves no card forward (#660): its Todo stands, never Batching or Done, and it keeps its marker.
/usr/bin/grep -q '^o 4 Batching 702$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -Eq 'Todo 702|Done 702' "$T/board.calls" 2>/dev/null && [ -e "$T/st/auto/disp-702" ] && ok "O: a failed run that landed work stops at Batching, never Todo or Done, and keeps its marker (ADR-0143)" || ko "O #702 not reconciled: $(cat "$T/board.calls" 2>/dev/null)"
/usr/bin/grep -q -- '^--run-fields o 4 704 run-live ' "$T/board.calls" 2>/dev/null && [ -e "$T/st/auto/disp-704" ] && ok "O: an issue a live run works gets Run fields" || ko "O #704 not live-marked: $(cat "$T/board.calls" 2>/dev/null)"
/usr/bin/grep -q '^o 4 Todo 703$' "$T/board.calls" 2>/dev/null && /usr/bin/grep -q '^o 1 To triage 703$' "$T/board.calls" 2>/dev/null && [ ! -e "$T/st/auto/bug-703" ] && ok "O: autobug run -> loop board Todo + bug board To triage" || ko "O #703 not released: $(cat "$T/board.calls" 2>/dev/null)"
exp=$'fail 701 run-fail ended failed: adapter_error\nfail 703 run-bug ended failed: node_spend_cap\nfail 705 run-nomark ended completed: nothing landed\nfail 708 run-held ended failed: review_held own\nfail 710 run-chain ended failed: review_held loop\nfail 713 run-r1 ended failed: ac_failed:exit 3; unproven:route.router.1
fail 714 run-oc ended failed: owner-only: needs a credential  owner-only
fail 715 run-nc ended completed: unproven:route.router.1; unproven:route nochange
fail 717 run-nccase ended completed: unproven:route.router.1 nochange
fail 718 run-nc2 ended completed: review_held nochange
fail 719 run-nc2 ended completed: review_held own'
[ "$(sort "$T/wave.calls" 2>/dev/null)" = "$exp" ] && ok "O: each run that delivered nothing counts once through wave.sh fail; landed, live, needs-human and listed runs do not" || ko "O fail calls: '$(cat "$T/wave.calls" 2>/dev/null)'"
x715=$(/usr/bin/grep '^fail 715 ' "$T/wave.calls" 2>/dev/null | sort)
[ "$x715" = 'fail 715 run-nc ended completed: unproven:route.router.1; unproven:route nochange' ] && ok "O: a run that changed no file counts once as nochange, though its reasons are unproven (#2245)" || ko "O nochange run: '$(/usr/bin/grep '^fail 715 ' "$T/wave.calls" 2>/dev/null)'"
n716=$(/usr/bin/grep -c '^fail 716 ' "$T/wave.calls" 2>/dev/null)
[ "${n716:-0}" = 0 ] && ok "O: a land that did not pass is no no-change run, so its unproven run counts nothing (#2245 review)" || ko "O failed land counted: $n716 fail calls for #716"
x717=$(/usr/bin/grep '^fail 717 ' "$T/wave.calls" 2>/dev/null | sort)
[ "$x717" = 'fail 717 run-nccase ended completed: unproven:route.router.1 nochange' ] && ok "O: a no_change verdict in another case still marks the no-change end (#2245 review)" || ko "O case-varied verdict: '$(/usr/bin/grep '^fail 717 ' "$T/wave.calls" 2>/dev/null)'"
x718=$(/usr/bin/grep '^fail 718 ' "$T/wave.calls" 2>/dev/null | sort)
[ "$x718" = 'fail 718 run-nc2 ended completed: review_held nochange' ] && ok "O: the no-change child marks the issue it names (#2245 review)" || ko "O no-change child: '$(/usr/bin/grep '^fail 718 ' "$T/wave.calls" 2>/dev/null)'"
x719=$(/usr/bin/grep '^fail 719 ' "$T/wave.calls" 2>/dev/null | sort)
[ "$x719" = 'fail 719 run-nc2 ended completed: review_held own' ] && ok "O: a sibling child's own review decides its own issue, never the no-change child's (#2245 review)" || ko "O sibling issue: '$(/usr/bin/grep '^fail 719 ' "$T/wave.calls" 2>/dev/null)'"
n720=$(/usr/bin/grep -c '^fail 720 ' "$T/wave.calls" 2>/dev/null)
[ "${n720:-0}" = 0 ] && ok "O: a no-repro run is no failed dispatch, so wave.sh fail is never called (ADR-0029)" || ko "O no-repro counted: $n720 fail calls for #720"
/usr/bin/grep -q 'NO-REPRO #720: run run-nr found the bug does not reproduce' "$T/st/cycle.log" 2>/dev/null && ok "O: cycle.log names the no-repro run and the reason" || ko "O cycle.log no-repro: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -Eq '^--add o 4 Blocked 720$|^o 4 Blocked 720$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -q 'Todo 720' "$T/board.calls" 2>/dev/null && ok "O: a no-repro run leaves its card for the owner, never back in Todo" || ko "O #720 card: $(cat "$T/board.calls" 2>/dev/null)"
[ ! -e "$T/st/auto/disp-720" ] && ok "O: a no-repro run drops its dispatch marker" || ko "O disp-720 kept: $o"
nc720=$(/usr/bin/grep -l 'the defect does not reproduce at the tip' "$T/st/cq/"*720* 2>/dev/null | /usr/bin/grep -c .)
[ "$nc720" = 1 ] && /usr/bin/grep -q 'run-nr' "$T/st/cq/"*720* && ok "O: one comment names the run and the run's evidence" || ko "O no-repro comment: $(ls "$T/st/cq" 2>/dev/null)"
n712=$(/usr/bin/grep -c '^fail 712 ' "$T/wave.calls" 2>/dev/null)
[ "${n712:-0}" = 0 ] && ok "O: three unproven-only runs never count, so no SQUAD-NOT-STARTABLE marker (ADR-0164)" || ko "O unproven runs counted: $n712 fail calls for #712"
n713=$(/usr/bin/grep -c '^fail 713 ' "$T/wave.calls" 2>/dev/null)
[ "${n713:-0}" = 1 ] && /usr/bin/grep -q '^fail 713 run-r1 .*ac_failed:exit 3' "$T/wave.calls" && ok "O: a red run that stayed unproven counts once, and its red reason is named" || ko "O red+unproven: $n713 fail calls for #713: '$(/usr/bin/grep '^fail 713 ' "$T/wave.calls" 2>/dev/null)'"
rm -rf "$T"; exit $fail
