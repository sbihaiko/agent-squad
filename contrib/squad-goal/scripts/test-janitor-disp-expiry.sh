#!/bin/bash
# Rule O, marker half (#1847): a dispatch marker expires when its run ended without landing -- and `failed` is not the
# only way a run ends. A run that recorded any other verdict (`needs-human`, `pass`) and landed nothing left its
# auto/disp-<n> behind, autofrontier.sh skips any issue holding that marker, and the frontier starved on a finished run.
#  (A) ended-nothing (verdict `needs-human`, no land event, no PR): marker dropped, card -> Blocked (ADR-0029: the owner is the next actor), announced once.
#      Rule R owns that write; rule O writes no loop-board card (#91). The board stub answers the items read from the last
#      status the pass itself wrote, as the live board does, so a second column written for the same card is a race, not a pass;
#  (B) a run that landed (a `land` event with outcome pass) keeps its marker and reconciles its card to Landing:
#      a local landing is not a merge into the base, so it does not make the card Done (#239);
#  (C) a live run (no verdict yet) keeps the marker of the issue it works and writes Run fields;
#  (D) a work branch naming the issue keeps the marker and moves the card to Landing.
#  A card is one column, written by one rule (#91): a needs-human run that landed nothing is not a completed run
#  (ADR-0029), so the column is the dispatch's (Todo) and the release rule O owns the write. The board stub answers a
#  read from the last status the pass itself wrote, as the live board does, so a second column for that card is a race
#  the suite names, never a pass: with a stub that always answers In Progress, R's own Landing write stays hidden.
# R (#1586): the owner's rearm clears the ledger and leaves auto/disp-<n> behind, and a run that left active-runs.txt with
#  its folder gone is read by no other rule, so the issue stayed `claimed` for ever.
#  (R1) a marker whose run is gone, with a rearm newer than the marker, is dropped with one line, so the frontier answers
#       the issue startable again (sg_dispatch_claimed reads the marker's existence);
#  (R2) a live run on the issue keeps its marker;
#  (R3) a rearm older than the marker keeps it;
#  (R4) a pending card write in the ledger keeps it (ADR-0165).
# X (#2142): the loop dispatched an issue and the issue closed, but its marker stayed. sg_dispatch_claimed reads only
#  the marker's existence, so a reopened issue read as already claimed and the frontier skipped it with no line.
#  (X1) auto/disp-<n> of an issue that reads closed is dropped, with one line naming the issue;
#  (X2) auto/bug-<n> of an issue that reads closed is dropped too;
#  (X3) a marker of an issue that reads open stays.
# Stubbed gh/board; a temp state dir, so the live watch.log, markers and board are never read or written.
# usage: bash test-janitor-disp-expiry.sh [janitor.py]
# The suite declares its own world (#393): the loop's own shell exports SG_* (SG_BOARD_VOCAB=shipped, SG_FORK_DIR, SG_LAND_BRANCH,
# ...), and those reached the janitor below, which then took the shipped path and its fork rules instead of the fixture's. A hand
# run went red on the owner's machine where shell-suites.sh -- which clears every SG_* for the gate -- stayed green.
for v in $(env | /usr/bin/grep -o '^SG_[A-Za-z0-9_]*'); do unset "$v"; done
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/issue_ledger.py" "$T/s/issue_ledger.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
cat > "$T/s/board.sh" <<'B'
#!/bin/bash
echo "$*" >> "CARDDIR/board.calls"
a=("$@")
case "$1" in
  --*) ;;   # a Run-fields or clear-run write moves no card
  *) printf '%s\t%s\n' "${a[$(($#-1))]}" "${a[*]:2:$(($#-3))}" >> "CARDDIR/board.status";;   # board.sh <owner> <project> <status> <n>
esac
B
sed -i '' "s|CARDDIR|$T|" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
card() { /usr/bin/awk -v n="$1" '$1==n{s=substr($0,index($0,"\t")+1)} END{print s}' "$T/board.status" 2>/dev/null; }
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  # the live API answers a POST to /search/issues with Not Found and a non-zero body, which a caller reading `.total_count` sees as "a PR exists" (#1981): the stub refuses it the same way
  *search/issues*) case "$*" in *"-X GET"*|*"--method GET"*) case "$*" in *"is:merged"*) echo 0;; *"Refs #904"*|*"Refs #908"*) echo 1;; *) echo 0;; esac;; *) printf '{\n  "message": "Not Found"\n}\n'; exit 1;; esac;;
  # a work branch names the issue (#382, #361: the janitor reads branches, never pull requests): #904 and #908 each have one, ahead of the landing branch
  *repos/x/y/branches*) printf 'fix/904-x sha904\nfix/908-x sha908\n';;
  *repos/x/y/compare/*) echo ahead;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/1/fields*) echo FID1;;
  *projectsV2/4/items*) for n in 901 902 903 904 905; do s=$(/usr/bin/grep "^o 4 .* $n\$" "TMPDIR/board.calls" 2>/dev/null | tail -1 | sed "s/^o 4 \(.*\) $n\$/\1/"); printf '%s\t%s\n' "$n" "${s:-In Progress}"; done;;   # one read, every card: the column the pass last wrote, In Progress before any write
  *projectsV2/1/items*) ;;
  *issues/90*) echo open;;
  *issues/1101*) echo closed;;
  *issues/1103*) echo closed;;
  *issues/1102*) echo open;;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s (bug; one PR):\n' "$2" > "$T/co/runs/$1/request.txt"; }
# A: the run recorded a verdict (needs-human) and landed nothing: not a completed run, so the card is the dispatch's
mk run-ended 901
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #901 on fix/901-x"}\n{"event": "outcome", "node": "router", "entry": 1, "outcome": "pass", "routed": "continue", "reasons": [], "pos": 3}\n{"event": "verdict", "verdict": "needs-human", "reasons": ["owner_decision"], "exit_code": 0}\n' > "$T/co/runs/run-ended/record.jsonl"
echo run-ended > "$T/st/auto/disp-901"
# B: the run landed a change before it ended
mk run-landed 902
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #902 on fix/902-x"}\n{"event": "land", "node": "route.router.1", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "merge_land", "head": "a7df8f1ecff77bdb5833721121a7a50be4066053"}}\n{"event": "verdict", "verdict": "pass", "reasons": [], "exit_code": 0}\n' > "$T/co/runs/run-landed/record.jsonl"
echo run-landed > "$T/st/auto/disp-902"
# F (#464): the run recorded a `land` pass whose merge moved the target's tip by nothing: the landing carries no work, so it
# is no delivery. The issue goes back to the queue and the marker is dropped, exactly as for a run that landed nothing.
mk run-empty-land 905
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #905 on fix/905-x"}\n{"event": "land", "node": "route.router.1", "parent": "route", "entry": 1, "outcome": "pass", "reasons": [], "effect": {"name": "merge_land", "head": "e1837f1c9a2c4ac65b1e5c1c6d0dd0e2c5f8a1b4", "before": "e1837f1c9a2c4ac65b1e5c1c6d0dd0e2c5f8a1b4"}}\n{"event": "verdict", "verdict": "completed", "reasons": [], "exit_code": 0}\n' > "$T/co/runs/run-empty-land/record.jsonl"
echo run-empty-land > "$T/st/auto/disp-905"
touch -t 202001010000 "$T/co/runs/run-empty-land/record.jsonl"
# C: a live run (no verdict yet) works its issue
mk run-live 903
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #903 on fix/903-x"}\n' > "$T/co/runs/run-live/record.jsonl"
echo run-live > "$T/st/auto/disp-903"
# D: the run ended needs-human without landing, and a work branch names the issue: the branch is stale, so the card stays Blocked (#660)
mk run-pr 904
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #904 on fix/904-x"}\n{"event": "verdict", "verdict": "needs-human", "reasons": ["label_missing"], "exit_code": 0}\n' > "$T/co/runs/run-pr/record.jsonl"
echo run-pr > "$T/st/auto/disp-904"
touch -t 202001010000 "$T/co/runs/run-ended/record.jsonl" "$T/co/runs/run-landed/record.jsonl" "$T/co/runs/run-pr/record.jsonl"
# E (#1981): the marker names the REQUEST that dispatched the issue (frontier-<id>), and rule E drops it when that
# request's own log says `verdict failed`, the issue is open and no work branch names it. The check is what broke: a bare
# `gh api search/issues -f ...` POSTs, the API answers Not Found, and a non-zero body read as "the issue is past
# dispatch" forever -- so the marker outlived every failed dispatch and autofrontier.sh skipped the issue for good.
printf 'verdict failed (exit 1)\nreasons: blocked_by_11\n' > "$T/st/frontier-old.log"
printf 'verdict pass (exit 0)\n' > "$T/st/frontier-ok.log"
echo frontier-old > "$T/st/auto/disp-906"; touch -t 202001010000 "$T/st/auto/disp-906" "$T/st/frontier-old.log"
echo frontier-ok > "$T/st/auto/disp-907"; touch -t 202001010000 "$T/st/auto/disp-907" "$T/st/frontier-ok.log"
echo frontier-old > "$T/st/auto/disp-908"; touch -t 202001010000 "$T/st/auto/disp-908"
# R (#1586): the owner's rearm is the fact that frees a dead dispatch's marker. No run helps: the marker names a run that left
# active-runs.txt and left no folder (R1, R3, R4), except R2, whose run is still live in its launch window. The ledger is
# written by its own writer, as the rearm writes it. The marker mtime is local 2020-01-01 00:00 (the touch below); the rearm
# times are 2020-01-02 and 2019-12-31 local, so the ordering holds in any timezone.
led() {   # <issue> <rearm ts|old> [pending]: the issue's ledger, with the owner's rearm at that time
  SG_STATE="$T/st" python3 -I - "$T/s" "x/y#$1" "$2" "${3:-}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import issue_ledger as L
rec = L.empty(sys.argv[2])
rec["rearmed"] = {"ts": float(sys.argv[3]), "runs": []}
if sys.argv[4] == "pending":
    rec["board"] = {"run": "frontier-pend", "column": "Todo", "instant": float(sys.argv[3]), "state": "pending", "req": "frontier-pend"}
L.save(sys.argv[2], rec)
PY
}
echo frontier-gone > "$T/st/auto/disp-1001"; touch -t 202001010000 "$T/st/auto/disp-1001"; led 1001 1577934000
mk frontier-live 1002
echo frontier-live > "$T/st/auto/disp-1002"; touch -t 202001010000 "$T/st/auto/disp-1002"; led 1002 1577934000
echo frontier-oldmark > "$T/st/auto/disp-1003"; touch -t 202001010000 "$T/st/auto/disp-1003"; led 1003 1577761200
echo frontier-pend > "$T/st/auto/disp-1004"; touch -t 202001010000 "$T/st/auto/disp-1004"; led 1004 1577934000 pending
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$T/st" SG_FORK_DIR= SG_FORK_BASE= JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1; }
o=$(run)
[ ! -e "$T/st/auto/disp-901" ] && ok "O: an ended run with no landed change drops its disp marker" || ko "O disp-901 kept (the frontier starves on it): $o"
/usr/bin/grep -q '^o 4 Blocked 901$' "$T/board.calls" 2>/dev/null && ok "R: a needs-human run with nothing landed -> card Blocked (the owner is the next actor)" || ko "R #901 not Blocked: $(cat "$T/board.calls" 2>/dev/null)"
[ "$(/usr/bin/grep -c '^o 4 [A-Za-z ]* 901$' "$T/board.calls" 2>/dev/null)" = 1 ] && ok "O: writes no loop-board card of its own (one column, one owner)" || ko "O raced R on #901: $(cat "$T/board.calls" 2>/dev/null)"
/usr/bin/grep -q 'DISPATCH-FAILED #901.*card is Blocked' "$T/st/cycle.log" 2>/dev/null && ok "O: the drop is announced with the column R wrote" || ko "O cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q 'needs-human' "$T/st/cycle.log" 2>/dev/null && ok "O: the announcement names the verdict it ended on" || ko "O announcement: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -l 'owner_decision' "$T/st/cq/"*901* >/dev/null 2>&1 && /usr/bin/grep -q 'run-ended' "$T/st/cq/"*901* && ok "O: comment names the run and the reason" || ko "O comment: $(ls "$T/st/cq" 2>/dev/null)"
[ -e "$T/st/auto/disp-902" ] && /usr/bin/grep -q '^o 4 Landing 902$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -q 'Done 902' "$T/board.calls" 2>/dev/null && ok "O: a run that landed stops at Landing, never Done, and keeps its marker" || ko "O #902 not reconciled: $(cat "$T/board.calls" 2>/dev/null)"
[ ! -e "$T/st/auto/disp-905" ] && ok "O: a landing that moved the tip by nothing drops its marker (no delivery)" || ko "O disp-905 kept although the land carried nothing: $o"
/usr/bin/grep -q '^o 4 Todo 905$' "$T/board.calls" 2>/dev/null && ok "R: a run that ended with nothing delivered leaves the card in the queue, never Landing" || ko "R #905 not back in Todo: $(cat "$T/board.calls" 2>/dev/null)"
[ -e "$T/st/auto/disp-903" ] && /usr/bin/grep -q -- '^--run-fields o 4 903 run-live ' "$T/board.calls" 2>/dev/null && ok "O: a live run keeps the marker and writes Run fields" || ko "O #903 not live-marked: $(cat "$T/board.calls" 2>/dev/null)"
[ -e "$T/st/auto/disp-904" ] && /usr/bin/grep -q '^o 4 Blocked 904$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -q 'Landing 904' "$T/board.calls" 2>/dev/null && ok "O: a needs-human run's stale work branch keeps its marker, and its card Blocked (#660)" || ko "O #904 not reconciled: $(cat "$T/board.calls" 2>/dev/null)"
[ ! -e "$T/st/auto/disp-906" ] && ok "E: a dispatch whose own request ended failed is dropped once it is old" || ko "E disp-906 kept: the frontier stays starved"
/usr/bin/grep -q 'stale disp-906' "$T/st/cycle.log" 2>/dev/null && ok "E: the drop is announced with the request that failed" || ko "E announcement: $(cat "$T/st/cycle.log" 2>/dev/null)"
[ -e "$T/st/auto/disp-907" ] && ok "E: a request that ended any other way keeps its marker" || ko "E disp-907 dropped by mistake"
[ -e "$T/st/auto/disp-908" ] && ok "E: a work branch naming the issue keeps its marker" || ko "E disp-908 dropped although a work branch names it"
# A failed dispatch whose issue a work branch names is past dispatch: the pass keeps the marker AND says so, naming the issue, so the
# operator can tell that decision from a dispatch that was released, and it writes no card of its own for the issue.
/usr/bin/grep -q 'DISPATCH-FAILED #908:.*a work branch names the issue' "$T/st/cycle.log" 2>/dev/null && ok "E: the pass names the issue whose dispatch stands" || ko "E #908 kept without a word: $(cat "$T/st/cycle.log" 2>/dev/null)"
[ "$(/usr/bin/grep -c '^o 4 [A-Za-z ]* 908$' "$T/board.calls" 2>/dev/null)" = 0 ] && ok "E: the card of the issue a work branch names is left alone" || ko "E #908 card written: $(cat "$T/board.calls" 2>/dev/null)"
# R1: the run is gone (not in active-runs.txt, no folder) and the rearm is newer than the marker: the marker goes, so
# sg_dispatch_claimed reads the issue startable again, and one line names the run.
[ ! -e "$T/st/auto/disp-1001" ] && ok "R: a rearm newer than a dead run's marker drops it (the frontier reads the issue startable)" || ko "R disp-1001 kept: the issue stays claimed for good: $o"
/usr/bin/grep -q "DISP MARKER DROPPED #1001: the owner's rearm is newer than run frontier-gone" "$T/st/cycle.log" 2>/dev/null && ok "R: the drop names the run the marker held" || ko "R line: $(cat "$T/st/cycle.log" 2>/dev/null)"
[ -e "$T/st/auto/disp-1002" ] && ok "R: a live run on the issue keeps its marker" || ko "R disp-1002 dropped although run frontier-live is live"
[ -e "$T/st/auto/disp-1003" ] && ok "R: a rearm older than the marker keeps it" || ko "R disp-1003 dropped although the rearm predates the marker"
[ -e "$T/st/auto/disp-1004" ] && ok "R: a pending card write keeps the marker (ADR-0165)" || ko "R disp-1004 dropped although its card write is pending"
run >/dev/null   # the announcement is once per run, not once per pass
[ "$(/usr/bin/grep -c 'DISPATCH-FAILED #901' "$T/st/cycle.log" 2>/dev/null)" = 1 ] && ok "O: the drop is announced once" || ko "O re-announced: $(cat "$T/st/cycle.log" 2>/dev/null)"
[ "$(/usr/bin/grep -c 'DISP MARKER DROPPED #1001' "$T/st/cycle.log" 2>/dev/null)" = 1 ] && ok "R: the marker is dropped and announced once" || ko "R re-announced or silent: $(cat "$T/st/cycle.log" 2>/dev/null)"
[ "$(/usr/bin/grep -c '^o 4 [A-Za-z ]* 901$' "$T/board.calls" 2>/dev/null)" = 1 ] && ok "R: a board that follows the writes is written once; the card does not flap" || ko "#901 flapped across passes: $(cat "$T/board.calls" 2>/dev/null)"
# L (#2127): a needs-human run that left active-runs.txt, with no wave open and no rearm, releases the marker its frontier request
# wrote (the marker holds the request name, not the run folder), so the frontier dispatches the issue again.
mkdir -p "$T/co/runs/run-left" "$T/st/req"; printf 'CHILD — GitHub issue(s) 909 (bug; one PR):\n' > "$T/co/runs/run-left/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #909 on fix/909-x"}\n{"event": "verdict", "verdict": "needs-human", "reasons": ["owner_decision"], "exit_code": 0}\n' > "$T/co/runs/run-left/record.jsonl"
cp "$T/co/runs/run-left/request.txt" "$T/st/req/frontier-20261117-061147.txt"
echo frontier-20261117-061147 > "$T/st/auto/disp-909"
touch -t 202001010000 "$T/co/runs/run-left/record.jsonl" "$T/co/runs/run-left/request.txt" "$T/st/req/frontier-20261117-061147.txt" "$T/st/auto/disp-909"
run >/dev/null
[ ! -e "$T/st/auto/disp-909" ] && ok "L: a needs-human run that left active-runs.txt releases its marker" || ko "L disp-909 kept: the issue stays claimed for good: $(cat "$T/st/cycle.log" 2>/dev/null)"
# M (#2127, ADR-0166): a taken-over run that left active-runs.txt keeps its marker while its auto/taken-<run> marker stands.
mkdir -p "$T/co/runs/run-taken"; printf 'CHILD — GitHub issue(s) 909 (bug; one PR):\n' > "$T/co/runs/run-taken/request.txt"
printf '{"event": "verdict", "verdict": "killed/taken-over", "reasons": ["stuck"], "exit_code": 1}\n' > "$T/co/runs/run-taken/record.jsonl"
echo run-taken > "$T/st/auto/disp-909"; echo "run run-taken" > "$T/st/auto/taken-run-taken"
touch -t 202001010000 "$T/co/runs/run-taken/record.jsonl" "$T/co/runs/run-taken/request.txt" "$T/st/auto/disp-909"
run >/dev/null
[ -e "$T/st/auto/disp-909" ] && ok "M: a taken-over run keeps its marker while auto/taken-<run> stands" || ko "M disp-909 dropped although the run is taken over"
# X (#2142): the marker of an issue that reads closed is a claim nothing releases. The issue closed, the marker stood,
# and a reopen read as already claimed: the frontier skipped it with no line. Open issues keep their markers.
echo frontier-close > "$T/st/auto/disp-1101"; echo frontier-close > "$T/st/auto/bug-1103"; echo frontier-open > "$T/st/auto/disp-1102"
touch -t 202001010000 "$T/st/auto/disp-1101" "$T/st/auto/bug-1103" "$T/st/auto/disp-1102"
run >/dev/null
[ ! -e "$T/st/auto/disp-1101" ] && ok "X: a closed issue's disp marker is dropped (a reopen dispatches again)" || ko "X disp-1101 kept: the reopened issue reads as claimed"
[ ! -e "$T/st/auto/bug-1103" ] && ok "X: a closed issue's bug marker is dropped" || ko "X bug-1103 kept: the bug board reads the issue claimed"
[ -e "$T/st/auto/disp-1102" ] && ok "X: an open issue's marker stays" || ko "X disp-1102 dropped although the issue reads open"
/usr/bin/grep -q 'DISP MARKER DROPPED #1101: its issue is closed' "$T/st/cycle.log" 2>/dev/null && ok "X: the drop names the issue and its cause" || ko "X line: $(cat "$T/st/cycle.log" 2>/dev/null)"
rm -rf "$T"; exit $fail
