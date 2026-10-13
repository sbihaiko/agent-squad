#!/bin/bash
# Board reconciliation: the janitor derives the expected card status from run truth and evidence, then writes only mismatches.
# A card the board does not carry is created with its expected status in the same write (--add); a board that does not
# answer at all (the items read fails) is told apart from one that answers "no such card": nothing is written, nothing is
# reported as a defect. The items are read once per pass, never once per issue.
# Every open issue the repository has carries a card, whether or not a run names it (#92): an issue with no run, no
# marker and no card -- the case the measured defect showed, an issue created while the loop runs -- is added in the
# dispatch column, and one that carries needs-owner or needs-ratification is added Blocked. The open issues are ONE read
# through the door (gh_aggregate, the throttle), never one call per issue.
# Stubbed gh and board.sh; a temp state dir, so the live loop state and board are never read or written.
# usage: bash test-janitor-board-reconcile.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"
here="$(cd "$(dirname "$src")" && pwd)"; cp "$here/ghcall.sh" "$here/ghread.py" "$here/ghthrottle.py" "$T/s/"   # the janitor reaches the open-issue list through the door beside it (ghcall.sh)
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$*" in
  *" 2009"*) echo "board refused #2009" >&2; exit 7;;
esac
case "$1" in
  --run-fields) echo "RUN $2 $3 $4 $5 $6" >> "TMPDIR/board.calls";;
  --clear-run) echo "CLEAR $2 $3 $4" >> "TMPDIR/board.calls";;
  --add) echo "ADD $2 $3 $4 $5" >> "TMPDIR/board.calls";;
  *) echo "STATUS $1 $2 $3 $4" >> "TMPDIR/board.calls";;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *search/issues*)
    case "$a" in *"-X GET"*|*"--method GET"*) ;; *) echo '{"message":"Not Found"}'; exit 1;; esac
    case "$a" in *"is:merged"*"Refs #2004"*) echo 1;; *) echo 0;; esac;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)   # one read answers every card: number, Status, repository
    [ -e "TMPDIR/board-down" ] && { echo "API rate limit exceeded" >&2; exit 1; }
    for n in 2002 2005 2006 2009; do printf '%s\tIn Progress\thttps://api.github.com/repos/x/y\n' "$n"; done
    for n in 2001 2007 2012; do printf '%s\tTodo\thttps://api.github.com/repos/x/y\n' "$n"; done
    for n in 2003 2004 2008 2010 2011; do printf '%s\tCode Complete\thttps://api.github.com/repos/x/y\n' "$n"; done
    printf '2014\t\thttps://api.github.com/repos/x/y\n';;   # on the board, no Status yet
  *graphql*)   # the open-issue list in ONE request: every open issue of the repository, with its labels
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 2001 2002 2003 2004 2005 2006 2007 2008 2009 2011 2012 2013 2014 2015 2016; do
      case $n in 2007|2016) labs='{"nodes":[{"name":"needs-owner"}]}';; 2012) labs='{"nodes":[{"name":"needs-ratification"}]}';; *) labs='{"nodes":[]}';; esac
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":%s,"comments":{"nodes":[]}}' "$sep" "$n" "$n" "$labs"; sep=,; done
    printf ']}}}}';;
  *issues/2007*) echo $'open\t\tIssue 2007\tneeds-owner';;
  *issues/2010*) echo $'closed\t\tIssue 2010';;
  *issues/2012*) echo $'open\t\tIssue 2012\tneeds-ratification';;
  *issues/20*) echo $'open\t\tIssue';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
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
mk run-nocard 2013   # created while the loop runs: no card on the board yet
rec run-nocard '{"event":"run_start","run_id":"run-nocard","ts":"2026-10-13T08:10:00Z"}'
mk run-nostatus 2014   # on the board, with no Status
rec run-nostatus '{"event":"run_start","run_id":"run-nostatus","ts":"2026-10-13T08:11:00Z"}'
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
/usr/bin/grep -q '^ADD o 4 In Progress 2013$' "$T/board.calls" 2>/dev/null && ok "no card: the item is created with its expected status in one write" || ko "#2013 not added: $(bc) $o"
! /usr/bin/grep -q '^STATUS o 4 In Progress 2013$' "$T/board.calls" 2>/dev/null && ok "no card: the status is not written a second time" || ko "#2013 written twice: $(bc)"
/usr/bin/grep -q 'BOARD-RECONCILE #2013: card added -> In Progress (run run-nocard is live)' "$T/st/cycle.log" 2>/dev/null && ok "the add is named in cycle.log" || ko "add not named: $(cat "$T/st/cycle.log")"
/usr/bin/grep -q '^STATUS o 4 In Progress 2014$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -q 'ADD .* 2014$' "$T/board.calls" && ok "a card with no Status is written, not added again" || ko "#2014: $(bc)"
! /usr/bin/grep -q 'ADD .* 2001$' "$T/board.calls" 2>/dev/null && ok "a card that exists is never added again" || ko "#2001 added: $(bc)"
[ "$(/usr/bin/grep -c 'projectsV2/4/items' "$T/gh.calls")" = 1 ] && ok "the items are read once per pass" || ko "items read $(/usr/bin/grep -c 'projectsV2/4/items' "$T/gh.calls") times"
# #92: an issue no run names, with no marker and no card, still carries one -- the open-issue list is ONE read
/usr/bin/grep -q '^ADD o 4 Todo 2015$' "$T/board.calls" 2>/dev/null && ok "an open issue no run names gets a card in the dispatch column" || ko "#2015 not added: $(bc) $o"
/usr/bin/grep -q 'BOARD-RECONCILE #2015: card added -> Todo' "$T/st/cycle.log" 2>/dev/null && ok "the add of an unclaimed issue is named in cycle.log" || ko "#2015 add not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q '^ADD o 4 Blocked 2016$' "$T/board.calls" 2>/dev/null && ok "an open issue carrying needs-owner gets a Blocked card" || ko "#2016 not added Blocked: $(bc) $o"
[ "$(/usr/bin/grep -c graphql "$T/gh.calls")" = 1 ] && ok "the open issues are read once per pass, in one request" || ko "open issues read $(/usr/bin/grep -c graphql "$T/gh.calls") times"
rm -f "$T/board.calls" "$T/st/cycle.log"; touch "$T/board-down"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1)
! /usr/bin/grep -q '^\(STATUS\|ADD\|RUN\|CLEAR\)' "$T/board.calls" 2>/dev/null && ok "board down: nothing is written on a guess" || ko "board down, written: $(bc)"
! /usr/bin/grep -q 'BOARD-WRITE-FAILED' "$T/st/cycle.log" 2>/dev/null && ok "board down: nothing is reported as a defect" || ko "board down, reported: $(cat "$T/st/cycle.log")"
rm -rf "$T"; exit $fail
