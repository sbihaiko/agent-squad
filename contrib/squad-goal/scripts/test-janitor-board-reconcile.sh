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
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"
here="$(cd "$(dirname "$src")" && pwd)"; cp "$here/ghcall.sh" "$here/ghread.py" "$here/ghthrottle.py" "$T/s/"   # the janitor reaches the open-issue list through the door beside it (ghcall.sh)
cp "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$here/spec_fold.py" "$T/s/"   # a refused card write queues its comment through the helper beside it (#141), and names it from the closed set (#142)
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
    # A merge is delivery only when it landed on the base (#127). #2004's merge is on the base; #2018's names the issue but
    # went into its parent branch, so the query scoped to the base answers 0 and the unscoped one answers 1.
    case "$a" in
      *"base:"*"Refs #2018"*) echo 0;;
      *"is:merged"*"Refs #2018"*) echo 1;;
      *"is:merged"*"Refs #2004"*) echo 1;;
      *"Refs #2018"*) echo 1;;
      *) echo 0;; esac;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)   # one read answers every card: number, Status, repository
    [ -e "TMPDIR/board-down" ] && { echo "API rate limit exceeded" >&2; exit 1; }
    for n in 2002 2003 2005 2006 2009 2022 2023; do printf '%s\tIn Progress\thttps://api.github.com/repos/x/y\n' "$n"; done
    for n in 2001 2007 2012; do printf '%s\tTodo\thttps://api.github.com/repos/x/y\n' "$n"; done
    for n in 2004 2008 2010 2011; do printf '%s\tCode Complete\thttps://api.github.com/repos/x/y\n' "$n"; done
    printf '2014\t\thttps://api.github.com/repos/x/y\n'   # on the board, no Status yet
    printf '2030\tDone\thttps://api.github.com/repos/x/y\n2031\tAbandoned\thttps://api.github.com/repos/x/y\n2029\tClosed\thttps://api.github.com/repos/x/y\n'   # spec children, and a spec card already Closed
    printf '2015\tBlocked\thttps://api.github.com/repos/x/y\n';;   # stale Blocked card with no current blocking cause
  *graphql*)   # the open-issue list in ONE request: every open issue of the repository, with its labels
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 2001 2002 2003 2004 2005 2006 2007 2008 2009 2011 2012 2013 2014 2015 2016 2017 2018 2019 2020 2021 2027 2028 2029; do
      body=""; subs='{"total":0,"completed":0}'; kids='[]'
      case $n in
        2007|2016) labs='{"nodes":[{"name":"needs-owner"}]}';;
        2012) labs='{"nodes":[{"name":"needs-ratification"}]}';;
        # #2017 is a spec and #2019 one of its children, published the way /to-tickets publishes them (#117): the parent
        # carries the spec label, the child carries its wave beside the triage label and names its blocker in its body.
        2017) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":2,"completed":1}'; kids='[{"number":2019},{"number":2011}]';;
        # #2020 is a spec whose every sub-issue is closed: it is done, and the pass closes it. #2021 is a spec with no
        # sub-issue: nothing proves it done, so it stays open.
        2020) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":2,"completed":2}'; kids='[{"number":2030},{"number":2031}]';;
        # the fold's other cases: every child abandoned, a child still queued, and a Closed spec whose child reopened
        2027) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":1,"completed":0}'; kids='[{"number":2031}]';;
        2028) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":2,"completed":1}'; kids='[{"number":2030},{"number":2012}]';;
        2029) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":2,"completed":1}'; kids='[{"number":2030},{"number":2002}]';;
        2021) labs='{"nodes":[{"name":"spec"}]}';;
        2019) labs='{"nodes":[{"name":"ready-for-agent"},{"name":"wave:02"}]}'; body='## Blocked by\n\n- #2006 the shape lands first';;
        *) labs='{"nodes":[]}';;
      esac
      printf '%s{"number":%s,"title":"Issue %s","body":"%s","state":"OPEN","labels":%s,"subIssuesSummary":%s,"subIssues":{"nodes":%s},"comments":{"nodes":[]}}' "$sep" "$n" "$n" "$body" "$labs" "$subs" "$kids"; sep=,; done
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
# #2011 carries a landed-branch marker and nothing else: the marker is not a merged PR into the base, so it does not
# make the card Done (#239). The card falls to the loop floor.
touch "$T/st/auto/fork-2011-abcdef123"
mk run-ratify 2012
rec run-ratify '{"event":"run_start","run_id":"run-ratify","ts":"2026-10-13T08:09:00Z"}'
mk run-nocard 2013   # created while the loop runs: no card on the board yet
rec run-nocard '{"event":"run_start","run_id":"run-nocard","ts":"2026-10-13T08:10:00Z"}'
mk run-nostatus 2014   # on the board, with no Status
rec run-nostatus '{"event":"run_start","run_id":"run-nostatus","ts":"2026-10-13T08:11:00Z"}'
mk run-stacked-pr 2018   # its PR names the issue, but it was merged into the parent branch, not the base
rec run-stacked-pr '{"event":"run_start","run_id":"run-stacked-pr","ts":"2026-10-13T08:12:00Z"}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
# ONE run, TWO children, and only one of them landed (#271): the run's `land` event names the child that made
# it, and the failed child committed nothing -- no branch, no PR. The run's landing is that child's, never
# every issue the run held, so #2022 leaves Todo and #2023 stays in the loop's queue.
mk run-mixed 2022
printf 'CHILD — GitHub issue(s) 2022, 2023:\n' > "$T/co/runs/run-mixed/request.txt"
rec run-mixed '{"event":"run_start","run_id":"run-mixed","ts":"2026-10-13T08:13:00Z"}
{"event":"spawn_accepted","child":"route.router.1","definition":"GitHub issue #2022 on fix/2022-x"}
{"event":"spawn_accepted","child":"route.router.2","definition":"GitHub issue #2023 on fix/2023-x"}
{"event":"outcome","node":"route.router.1","entry":1,"outcome":"pass","reasons":["tests green"]}
{"event":"land","node":"route.router.1","parent":"route","entry":1,"outcome":"pass","reasons":[],"effect":{"name":"merge_land","head":"a7df8f1ecff77bdb5833721121a7a50be4066053","held_out":"pass"}}
{"event":"outcome","node":"route.router.2","entry":1,"outcome":"fail","reasons":["red gate"]}
{"event":"verdict","verdict":"failed","reasons":["child route.router.2 failed"],"exit_code":1}'
printf '%s\n' '{"items":[{"n":2015,"st":"Done","s":"open"}]}' > "$T/st/hub-board.json"   # the cached board exposes the false Done card to rule H while R repairs the live card
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1)
bc() { cat "$T/board.calls" 2>/dev/null; }
/usr/bin/grep -q '^STATUS o 4 In Progress 2001$' "$T/board.calls" 2>/dev/null && ok "dispatch marker: live run card moves to In Progress" || ko "#2001 status not reconciled: $(bc) $o"
/usr/bin/grep -q '^RUN o 4 2001 run-dispatch ' "$T/board.calls" 2>/dev/null && ok "dispatch marker: Run fields are written" || ko "#2001 Run fields missing: $(bc)"
/usr/bin/grep -q '^RUN o 4 2002 run-accepted 2026-10-13T08:00:00Z$' "$T/board.calls" 2>/dev/null && ok "accepted child: Run fields carry run id and start time" || ko "#2002 Run fields wrong: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Code Complete 2003$' "$T/board.calls" 2>/dev/null && ok "local land event: card moves to Code Complete" || ko "#2003 not Code Complete: $(bc)"
! /usr/bin/grep -q 'Done 2003' "$T/board.calls" 2>/dev/null && ok "local land event does not move the card to Done" || ko "#2003 read Done from a local landing: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Done 2004$' "$T/board.calls" 2>/dev/null && ok "merged PR: card moves to Done" || ko "#2004 not Done: $(bc)"
# #127: a PR that names the issue but was merged into its parent branch carries the work nowhere, so the card stops at
# Code Complete -- Done over a merge the base never took is the false delivery #125 showed.
/usr/bin/grep -q '^ADD o 4 Code Complete 2018$' "$T/board.calls" 2>/dev/null && ok "a merge into a parent branch leaves the card at Code Complete" || ko "#2018 left the dispatch column: $(bc)"
! /usr/bin/grep -q 'Done 2018' "$T/board.calls" 2>/dev/null && ok "a merge the base never carried is not Done" || ko "#2018 read Done over work main does not carry: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Code Complete 2005$' "$T/board.calls" 2>/dev/null && ok "completed unlanded run: card moves to Code Complete" || ko "#2005 not Code Complete: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Todo 2006$' "$T/board.calls" 2>/dev/null && ok "failed unlanded run: card moves to Todo" || ko "#2006 not Todo: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Blocked 2007$' "$T/board.calls" 2>/dev/null && ok "owner label: card moves to Blocked" || ko "#2007 not Blocked: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Blocked 2012$' "$T/board.calls" 2>/dev/null && ok "ratification label: card moves to Blocked" || ko "#2012 not Blocked: $(bc)"
/usr/bin/grep -q 'BOARD-RECONCILE #2006: card -> Todo (run run-failed failed with nothing landed (red gate))' "$T/st/cycle.log" 2>/dev/null && ok "the pass names the reason for the move" || ko "reason not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q '^STATUS .* 2008$' "$T/board.calls" 2>/dev/null && ok "card already in the expected status is not written again" || ko "#2008 status was rewritten: $(bc)"
/usr/bin/grep -q '^CLEAR o 4 2005$' "$T/board.calls" 2>/dev/null && ok "leaving In Progress clears Run fields" || ko "#2005 Run fields not cleared: $(bc)"
/usr/bin/grep -q 'BOARD-WRITE-FAILED #2009' "$T/st/cycle.log" 2>/dev/null && ok "failed board write is named in cycle.log" || ko "board failure not logged: $(cat "$T/st/cycle.log" 2>/dev/null) $o"
# The same refusal is a comment on the issue the write was about (#141): the card says why it did not move, through the
# queue whose one flusher posts it, and the comment carries the refusal's name and GitHub's error.
cq="$T/st/cq/x__y#2009-refusal-board-write-failed.md"
[ -f "$cq" ] && ok "a refused card write queues one comment on that issue" || ko "no refusal comment: $(ls "$T/st/cq" 2>/dev/null) $o"
/usr/bin/grep -qF 'BOARD-WRITE-FAILED #2009' "$cq" 2>/dev/null && /usr/bin/grep -qF 'board refused #2009' "$cq" 2>/dev/null \
  && ok "the comment carries the refusal's name and its error" || ko "comment body: $(cat "$cq" 2>/dev/null)"
[ "$(ls "$T/st/cq" | /usr/bin/grep -c 'refusal')" = 1 ] && ok "one refused write is one comment" || ko "refusal comments: $(ls "$T/st/cq")"
/usr/bin/grep -q '^STATUS o 4 Done 2010$' "$T/board.calls" 2>/dev/null && ok "closed issue: card moves to Done" || ko "#2010 not Done: $(bc)"
# #271: a run that asked for children credits a landing to the child that made it, never to every issue it held.
# #2022's child landed its own work, so that card leaves Todo; #2023's child failed and committed nothing, so it has
# no branch and no PR and never reads Code Complete.
/usr/bin/grep -q '^STATUS o 4 Code Complete 2022$' "$T/board.calls" 2>/dev/null && ok "the child that landed moves its own card to Code Complete" || ko "#2022 not Code Complete: $(bc) $o"
! /usr/bin/grep -q 'Code Complete 2023' "$T/board.calls" 2>/dev/null && ok "a child that landed nothing is never credited the run's landing" || ko "#2023 read Code Complete over its sibling's landing: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Todo 2023$' "$T/board.calls" 2>/dev/null && ok "a failed child with no branch and no PR stays in the loop's queue" || ko "#2023 did not stay in Todo: $(bc) $o"
/usr/bin/grep -q 'BOARD-RECONCILE #2023: card -> Todo (run run-mixed child route.router.2 fail with nothing landed (red gate))' "$T/st/cycle.log" 2>/dev/null && ok "the reason names the child and its own outcome" || ko "child reason not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q '^STATUS o 4 Todo 2011$' "$T/board.calls" 2>/dev/null && ok "landed branch marker alone returns an open card to Todo" || ko "#2011 did not leave Code Complete: $(bc)"
! /usr/bin/grep -q 'Done 2011' "$T/board.calls" 2>/dev/null && ok "landed branch marker alone is not Done" || ko "#2011 read Done from a landed branch marker: $(bc)"
/usr/bin/grep -q '^ADD o 4 In Progress 2013$' "$T/board.calls" 2>/dev/null && ok "no card: the item is created with its expected status in one write" || ko "#2013 not added: $(bc) $o"
! /usr/bin/grep -q '^STATUS o 4 In Progress 2013$' "$T/board.calls" 2>/dev/null && ok "no card: the status is not written a second time" || ko "#2013 written twice: $(bc)"
/usr/bin/grep -q 'BOARD-RECONCILE #2013: card added -> In Progress (run run-nocard is live)' "$T/st/cycle.log" 2>/dev/null && ok "the add is named in cycle.log" || ko "add not named: $(cat "$T/st/cycle.log")"
/usr/bin/grep -q '^STATUS o 4 In Progress 2014$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -q 'ADD .* 2014$' "$T/board.calls" && ok "a card with no Status is written, not added again" || ko "#2014: $(bc)"
! /usr/bin/grep -q 'ADD .* 2001$' "$T/board.calls" 2>/dev/null && ok "a card that exists is never added again" || ko "#2001 added: $(bc)"
[ "$(/usr/bin/grep -c 'projectsV2/4/items' "$T/gh.calls")" = 1 ] && ok "the items are read once per pass" || ko "items read $(/usr/bin/grep -c 'projectsV2/4/items' "$T/gh.calls") times"
# An open issue with no live run, marker, PR, or owner label returns to the loop floor; Blocked only expresses a current human action.
/usr/bin/grep -q '^STATUS o 4 Todo 2015$' "$T/board.calls" 2>/dev/null && ok "an unclaimed open issue leaves stale Blocked for the dispatch column" || ko "#2015 did not leave stale Blocked: $(bc) $o"
/usr/bin/grep -q 'BOARD-RECONCILE #2015: card -> Todo' "$T/st/cycle.log" 2>/dev/null && ok "the stale Blocked reconciliation is named in cycle.log" || ko "#2015 reconciliation not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q 'STATE-REGRESSION #2015: card is Done but the issue is still open; repaired to Todo' "$T/st/cycle.log" 2>/dev/null && ok "Done on an open issue is reported and repaired" || ko "#2015 Done regression not repaired: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q '^ADD o 4 Blocked 2016$' "$T/board.calls" 2>/dev/null && ok "an open issue carrying needs-owner gets a Blocked card" || ko "#2016 not added Blocked: $(bc) $o"
[ "$(/usr/bin/grep -c graphql "$T/gh.calls")" = 1 ] && ok "the open issues are read once per pass, in one request" || ko "open issues read $(/usr/bin/grep -c graphql "$T/gh.calls") times"
# A spec is written, not scheduled, so its card opens in Open Specs (#117); the tickets /to-tickets cuts from it are work,
# so every child opens in the loop's queue column, Todo, in ONE write when the card is missing (#86). The child's wave and
# its `## Blocked by` line do not move it off Todo: the column carries the loop floor, and the gate that holds a wave-2
# ticket back until its blocker lands is the dispatch's own reading of those edges, never the column (#142).
/usr/bin/grep -q '^ADD o 4 Doing 2017$' "$T/board.calls" 2>/dev/null && ok "a spec with no card is created in Doing, a child of it is in flight" || ko "#2017 not added Open Specs: $(bc) $o"
! /usr/bin/grep -q 'Todo 2017' "$T/board.calls" 2>/dev/null && ok "a spec is never created in the queue column" || ko "#2017 read as work: $(bc)"
/usr/bin/grep -q 'BOARD-RECONCILE #2017: card added -> Doing (spec folds over its sub-issues)' "$T/st/cycle.log" 2>/dev/null && ok "the spec column is named in cycle.log" || ko "#2017 add not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q '^ADD o 4 Todo 2019$' "$T/board.calls" 2>/dev/null && ok "a spec child with no card is created in Todo" || ko "#2019 not added Todo: $(bc) $o"
! /usr/bin/grep -q 'Blocked 2019' "$T/board.calls" 2>/dev/null && ok "a child whose blocker is still open is not held out of Todo" || ko "#2019 read Blocked: $(bc)"
# A spec's column is a fold over its sub-issues, read off the relation the one open-issue read already carries and the
# cards the pass already holds: Backlog with none (or none past the queue), Doing once a child reached Doing, Closed when
# every child is terminal and one is Closed, Abandoned when every child is terminal and none is Closed. The pass no longer
# closes the spec issue itself.
for w in "ADD o 4 Closed 2020" "ADD o 4 Backlog 2021" "ADD o 4 Abandoned 2027" "ADD o 4 Backlog 2028"; do
  /usr/bin/grep -q "^$w\$" "$T/board.calls" 2>/dev/null && ok "spec fold: $w" || ko "spec fold: no '$w': $(/usr/bin/grep -E ' 20(20|21|27|28)$' "$T/board.calls")"
done
! /usr/bin/grep -q 'Open Specs' "$T/board.calls" 2>/dev/null && ok "no spec is written to the old Open Specs column" || ko "Open Specs written: $(/usr/bin/grep 'Open Specs' "$T/board.calls")"
! /usr/bin/grep -q 'issues/20\(17\|20\|21\|27\|28\|29\)\(/comments\| -X PATCH\| -f state\)' "$T/gh.calls" 2>/dev/null && ok "the fold replaces the close rule: no spec issue is closed or commented" || ko "a spec issue was written: $(/usr/bin/grep -E 'issues/20(17|20|21|27|28|29)' "$T/gh.calls")"
# A spec card already Closed whose child reopened keeps Closed (#246) and gains the tag instead of a move back.
! /usr/bin/grep -q ' 2029$' "$T/board.calls" 2>/dev/null && ok "a Closed spec whose child reopened keeps its column" || ko "#2029 moved: $(/usr/bin/grep ' 2029$' "$T/board.calls")"
/usr/bin/grep -q '^api -X POST repos/x/y/issues/2029/labels -f labels\[\]=reverted$' "$T/gh.calls" 2>/dev/null && ok "it gains the reverted tag" || ko "#2029 not tagged: $(/usr/bin/grep 'issues/2029' "$T/gh.calls")"
/usr/bin/grep -q 'SPEC-REVERTED #2029' "$T/st/cycle.log" 2>/dev/null && ok "the kept column is named in cycle.log" || ko "#2029 not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
# The fold adds no GitHub call: the children come with the open-issue read, never from a sub-issues route.
! /usr/bin/grep -q 'sub_issues' "$T/gh.calls" 2>/dev/null && ok "no sub-issues route is called" || ko "a sub-issues call: $(/usr/bin/grep sub_issues "$T/gh.calls")"
[ "$(/usr/bin/grep -c graphql "$T/gh.calls")" = 1 ] && ok "the fold rides the one open-issue read" || ko "reads: $(/usr/bin/grep -c graphql "$T/gh.calls")"
rm -f "$T/board.calls" "$T/st/cycle.log"; touch "$T/board-down"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1)
! /usr/bin/grep -q '^\(STATUS\|ADD\|RUN\|CLEAR\)' "$T/board.calls" 2>/dev/null && ok "board down: nothing is written on a guess" || ko "board down, written: $(bc)"
! /usr/bin/grep -q 'BOARD-WRITE-FAILED' "$T/st/cycle.log" 2>/dev/null && ok "board down: nothing is reported as a defect" || ko "board down, reported: $(cat "$T/st/cycle.log")"
# The reconciler names its refusal through the closed set (#142): BOARD-WRITE-FAILED is a member of the one set the
# helper beside refusal.sh holds, and the name the queue entry carries is that member, not a spelling of its own.
names=$(bash "$T/s/refusal-names.sh" 2>/dev/null)
/usr/bin/grep -qxF BOARD-WRITE-FAILED <<< "$names" && ok "the reconciler's refusal name is a member of the closed set" || ko "the closed set does not hold the reconciler's name: '$names'"
/usr/bin/grep -qxF 'Refused write: BOARD-WRITE-FAILED #2009' "$cq" 2>/dev/null && ok "the comment names the member of the set, not a spelling beside it" || ko "the comment's refusal name: $(/usr/bin/grep -m1 'Refused write' "$cq" 2>/dev/null)"
rm -rf "$T"; exit $fail
