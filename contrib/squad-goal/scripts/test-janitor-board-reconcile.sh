#!/bin/bash
# Board reconciliation: the janitor derives the expected card status from run truth and evidence, then writes only mismatches.
# Stubbed gh and board.sh; a temp state dir, so the live loop state and board are never read or written.
# usage: bash test-janitor-board-reconcile.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$*" in
  *" 2009"*) echo "board refused #2009" >&2; exit 7;;
esac
case "$1" in
  --run-fields) echo "RUN $2 $3 $4 $5 $6" >> "TMPDIR/board.calls";;
  --clear-run) echo "CLEAR $2 $3 $4" >> "TMPDIR/board.calls";;
  *) echo "STATUS $1 $2 $3 $4" >> "TMPDIR/board.calls";;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; n=$(printf '%s' "$a" | sed -n 's/.*content.number==\([0-9]*\).*/\1/p')
case "$a" in
  *search/issues*)
    case "$a" in *"-X GET"*|*"--method GET"*) ;; *) echo '{"message":"Not Found"}'; exit 1;; esac
    case "$a" in *"is:merged"*"Refs #2004"*) echo 1;; *) echo 0;; esac;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)
    case "$n" in
      2002|2005|2006|2009) echo "In Progress";;
      2001|2007|2012) echo "Todo";;
      2003|2004|2010|2011) echo "Code Complete";;
      2007) echo "Todo";;
      2008) echo "Code Complete";;
    esac;;
  *issues/2007*) echo $'open\t\tIssue 2007\tneeds-owner';;
  *issues/2010*) echo $'closed\t\tIssue 2010';;
  *issues/2012*) echo $'open\t\tIssue 2012\tneeds-ratification';;
  *issues/20*) echo $'open\t\tIssue';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s:\n' "$2" > "$T/co/runs/$1/request.txt"; }
rec() { printf '%s\n' "$2" > "$T/co/runs/$1/record.jsonl"; }
mk run-dispatch 2001
: > "$T/co/runs/run-dispatch/record.jsonl"; echo run-dispatch > "$T/st/auto/disp-2001"
mk run-accepted 2002
rec run-accepted '{"event":"run_start","run_id":"run-accepted","ts":"2026-10-13T08:00:00Z"}
{"event":"spawn_accepted","child":"c1","definition":"GitHub issue #2002 on fix/2002-x"}'
mk run-landed 2003
rec run-landed '{"event":"run_start","run_id":"run-landed","ts":"2026-10-13T08:01:00Z"}
{"event":"land","outcome":"pass"}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
mk run-merged-pr 2004
rec run-merged-pr '{"event":"run_start","run_id":"run-merged-pr","ts":"2026-10-13T08:01:30Z"}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
mk run-code-complete 2005
rec run-code-complete '{"event":"run_start","run_id":"run-code-complete","ts":"2026-10-13T08:02:00Z"}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
mk run-failed 2006
rec run-failed '{"event":"run_start","run_id":"run-failed","ts":"2026-10-13T08:03:00Z"}
{"event":"verdict","verdict":"failed","reasons":["red gate"],"exit_code":1}'
mk run-blocked 2007
rec run-blocked '{"event":"run_start","run_id":"run-blocked","ts":"2026-10-13T08:04:00Z"}'
mk run-correct 2008
rec run-correct '{"event":"run_start","run_id":"run-correct","ts":"2026-10-13T08:05:00Z"}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
mk run-write-fails 2009
rec run-write-fails '{"event":"run_start","run_id":"run-write-fails","ts":"2026-10-13T08:06:00Z"}
{"event":"verdict","verdict":"failed","reasons":["red gate"],"exit_code":1}'
mk run-closed 2010
rec run-closed '{"event":"run_start","run_id":"run-closed","ts":"2026-10-13T08:07:00Z"}'
mk run-fork 2011
rec run-fork '{"event":"run_start","run_id":"run-fork","ts":"2026-10-13T08:08:00Z"}'
mk run-ratify 2012
rec run-ratify '{"event":"run_start","run_id":"run-ratify","ts":"2026-10-13T08:09:00Z"}'
touch "$T/st/auto/fork-2011-abcdef123"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1)
bc() { cat "$T/board.calls" 2>/dev/null; }
/usr/bin/grep -q '^STATUS o 4 In Progress 2001$' "$T/board.calls" 2>/dev/null && ok "dispatch marker: live run card moves to In Progress" || ko "#2001 status not reconciled: $(bc) $o"
/usr/bin/grep -q '^RUN o 4 2001 run-dispatch ' "$T/board.calls" 2>/dev/null && ok "dispatch marker: Run fields are written" || ko "#2001 Run fields missing: $(bc)"
/usr/bin/grep -q '^RUN o 4 2002 run-accepted 2026-10-13T08:00:00Z$' "$T/board.calls" 2>/dev/null && ok "accepted child: Run fields carry run id and start time" || ko "#2002 Run fields wrong: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Done 2003$' "$T/board.calls" 2>/dev/null && ok "land event: card moves to Done" || ko "#2003 not Done: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Done 2004$' "$T/board.calls" 2>/dev/null && ok "merged PR: card moves to Done" || ko "#2004 not Done: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Code Complete 2005$' "$T/board.calls" 2>/dev/null && ok "completed unlanded run: card moves to Code Complete" || ko "#2005 not Code Complete: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Todo 2006$' "$T/board.calls" 2>/dev/null && ok "failed unlanded run: card moves to Todo" || ko "#2006 not Todo: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Blocked 2007$' "$T/board.calls" 2>/dev/null && ok "owner label: card moves to Blocked" || ko "#2007 not Blocked: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Blocked 2012$' "$T/board.calls" 2>/dev/null && ok "ratification label: card moves to Blocked" || ko "#2012 not Blocked: $(bc)"
/usr/bin/grep -q 'BOARD-RECONCILE #2006: card -> Todo (run run-failed failed with nothing landed (red gate))' "$T/st/cycle.log" 2>/dev/null && ok "the pass names the reason for the move" || ko "reason not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q '^STATUS .* 2008$' "$T/board.calls" 2>/dev/null && ok "card already in the expected status is not written again" || ko "#2008 status was rewritten: $(bc)"
/usr/bin/grep -q '^CLEAR o 4 2005$' "$T/board.calls" 2>/dev/null && ok "leaving In Progress clears Run fields" || ko "#2005 Run fields not cleared: $(bc)"
/usr/bin/grep -q 'BOARD-WRITE-FAILED #2009' "$T/st/cycle.log" 2>/dev/null && ok "failed board write is named in cycle.log" || ko "board failure not logged: $(cat "$T/st/cycle.log" 2>/dev/null) $o"
/usr/bin/grep -q '^STATUS o 4 Done 2010$' "$T/board.calls" 2>/dev/null && ok "closed issue: card moves to Done" || ko "#2010 not Done: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Done 2011$' "$T/board.calls" 2>/dev/null && ok "landed branch marker: card moves to Done" || ko "#2011 not Done: $(bc)"
rm -rf "$T"; exit $fail
