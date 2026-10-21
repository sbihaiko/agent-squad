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
unset SG_BOARD_VOCAB   # this suite reads the legacy option names; the loop exports the shipped vocabulary
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"
here="$(cd "$(dirname "$src")" && pwd)"; cp "$here/ghcall.sh" "$here/ghread.py" "$here/ghthrottle.py" "$T/s/"   # the janitor reaches the open-issue list through the door beside it (ghcall.sh)
cp "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$here/spec_fold.py" "$T/s/"   # a refused card write queues its comment through the helper beside it (#141), and names it from the closed set (#142)
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$*" in
  *" 2009"*) echo "board refused #2009" >&2; exit 7;;
esac
case "$1" in
  --run-fields) echo "RUN $2 $3 $4 $5 $6" >> "TMPDIR/board.calls";;
  --clear-run) echo "CLEAR $2 $3 $4" >> "TMPDIR/board.calls";;
  --add) echo "ADD $2 $3 $4 $5" >> "TMPDIR/board.calls";;
  --remove) echo "REMOVE $2 $3 $4" >> "TMPDIR/board.calls";;
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
  # a work branch names the issue (#382: the janitor reads branches, never pull requests). #2004's tip is in the landing
  # branch (behind: delivered); #2018's holds commits the landing branch lacks (ahead: merged into its parent branch only).
  *repos/x/y/branches*) printf 'fix/2004-x sha2004\nfix/2018-x sha2018\n';;
  *repos/x/y/compare/*sha2004*) echo behind;;
  *repos/x/y/compare/*) echo ahead;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)   # one read answers every card: number, Status, repository
    [ -e "TMPDIR/board-down" ] && { echo "API rate limit exceeded" >&2; exit 1; }
    for n in 2002 2003 2005 2006 2009 2022 2023; do printf '%s\tIn Progress\thttps://api.github.com/repos/x/y\n' "$n"; done
    for n in 2001 2007 2008 2012; do printf '%s\tTodo\thttps://api.github.com/repos/x/y\n' "$n"; done
    for n in 2004 2010 2011 2032 2050; do printf '%s\tCode Complete\thttps://api.github.com/repos/x/y\n' "$n"; done   # #2050: a spec card a work rule wrongly moved forward (#665)
    printf '2051\tIn Progress\thttps://api.github.com/repos/x/y\n2052\tClosed\thttps://api.github.com/repos/x/y\n2053\tBacklog\thttps://api.github.com/repos/x/y\n2054\tBacklog\thttps://api.github.com/repos/x/y\n'   # #2050's children, and spec #2053 with its queued child
    printf '2014\t\thttps://api.github.com/repos/x/y\n'   # on the board, no Status yet
    printf '2030\tDone\thttps://api.github.com/repos/x/y\n2031\tAbandoned\thttps://api.github.com/repos/x/y\n2029\tClosed\thttps://api.github.com/repos/x/y\n'   # spec children, and a spec card already Closed
    printf '2041\tBacklog\thttps://api.github.com/repos/x/y\n'   # a run parent that already has a card
    printf '2042\tTodo\thttps://api.github.com/repos/x/y\n2043\tBacklog\thttps://api.github.com/repos/x/y\n2044\tBlocked\thttps://api.github.com/repos/x/y\n'   # the owner moved spec #2042 to Todo
    printf '2046\tBacklog\thttps://api.github.com/repos/x/y\n2047\tBacklog\thttps://api.github.com/repos/x/y\n'   # spec #2046 still sits in Backlog
    printf '2015\tBlocked\thttps://api.github.com/repos/x/y\n';;   # stale Blocked card with no current blocking cause
  *graphql*)   # the open-issue list in ONE request: every open issue of the repository, with its labels
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 2001 2002 2003 2004 2005 2006 2007 2008 2009 2011 2012 2013 2014 2015 2016 2017 2018 2019 2020 2021 2027 2028 2029 2032 2040 2041 2042 2043 2044 2045 2046 2047 2050 2051 2052 2053 2054; do
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
        2040|2041) labs='{"nodes":[{"name":"run-parent"},{"name":"enhancement"}]}';;   # a run parent: the roll-up of a run's children, never work
        2021) labs='{"nodes":[{"name":"spec"}]}';;
        # The owner starts a spec by moving its card (#589): #2042 sits in Todo, so its children in Backlog (#2043) or with no
        # card (#2045) follow it; #2044 is past Backlog and keeps its column. #2046 still sits in Backlog, so #2047 stays.
        2042) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":3,"completed":0}'; kids='[{"number":2043},{"number":2044},{"number":2045}]';;
        2046) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":1,"completed":0}'; kids='[{"number":2047}]';;
        # A spec is never work (#665): its children's commits name it, but the fold alone sets its card. #2050's card was
        # wrongly at Code Complete while one child is still in flight; #2053 is still queued.
        2050) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":2,"completed":1}'; kids='[{"number":2051},{"number":2052}]';;
        2053) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":1,"completed":0}'; kids='[{"number":2054}]';;
        2043|2044|2045|2047|2051|2052|2054) labs='{"nodes":[{"name":"ready-for-agent"}]}';;
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
# An OLDER run landed #2006 before run-failed requeued it (#619): the card follows the newest run that holds the issue,
# so the old run's Code Complete never beats the newest run's Todo.
mk run-old-landed 2006
rec run-old-landed '{"event":"run_start","run_id":"run-old-landed","ts":"2026-10-13T07:00:00Z"}
{"event":"land","outcome":"pass"}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
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
# make the card Done (#239). The card keeps Code Complete: the floor never moves a card back (#550).
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
# every issue the run held, so #2022 leaves Todo once its landing reached origin/dev (#691), and #2023 stays in the loop's queue.
mk run-mixed 2022
printf 'CHILD — GitHub issue(s) 2022, 2023:\n' > "$T/co/runs/run-mixed/request.txt"
rec run-mixed '{"event":"run_start","run_id":"run-mixed","ts":"2026-10-13T08:13:00Z"}
{"event":"spawn_accepted","child":"route.router.1","definition":"GitHub issue #2022 on fix/2022-x"}
{"event":"spawn_accepted","child":"route.router.2","definition":"GitHub issue #2023 on fix/2023-x"}
{"event":"outcome","node":"route.router.1","entry":1,"outcome":"pass","reasons":["tests green"]}
{"event":"land","node":"route.router.1","parent":"route","entry":1,"outcome":"pass","reasons":[],"effect":{"name":"merge_land","head":"a7df8f1ecff77bdb5833721121a7a50be4066053","held_out":"pass"}}
{"event":"outcome","node":"route.router.2","entry":1,"outcome":"fail","reasons":["red gate"]}
{"event":"verdict","verdict":"failed","reasons":["child route.router.2 failed"],"exit_code":1}'
# #2032 was fixed by hand in the landing branch (#519): no run and no work branch, but a commit in origin/dev names it. The
# work reached dev, so the card keeps Code Complete; the floor never moves it back (ADR-0143).
git -C "$T/co" init -q; git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "Fix the thing by hand (#2032)"
# A spec's children land their own commits under the parent's number (#665): the subject names the spec, and no work rule
# may read that as the spec's own delivery.
git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "A child of the spec landed (#2050)"
git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "A queued child's fix is prepared (#2053)"
git -C "$T/co" update-ref refs/remotes/origin/dev HEAD
# #2022's child landing reached the landing branch: its head is in origin/dev, so the run's failure does not unland it (#691)
sed -i '' "s/a7df8f1ecff77bdb5833721121a7a50be4066053/$(git -C "$T/co" rev-parse HEAD)/" "$T/co/runs/run-mixed/record.jsonl"
printf '%s\n' '{"items":[{"n":2015,"st":"Done","s":"open"}]}' > "$T/st/hub-board.json"   # the cached board exposes the false Done card to rule H while R repairs the live card
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1)
bc() { cat "$T/board.calls" 2>/dev/null; }
/usr/bin/grep -q '^STATUS o 4 In Progress 2001$' "$T/board.calls" 2>/dev/null && ok "dispatch marker: live run card moves to In Progress" || ko "#2001 status not reconciled: $(bc) $o"
/usr/bin/grep -q '^RUN o 4 2001 run-dispatch ' "$T/board.calls" 2>/dev/null && ok "dispatch marker: Run fields are written" || ko "#2001 Run fields missing: $(bc)"
/usr/bin/grep -q '^RUN o 4 2002 run-accepted 2026-10-13T08:00:00Z$' "$T/board.calls" 2>/dev/null && ok "accepted child: Run fields carry run id and start time" || ko "#2002 Run fields wrong: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Code Complete 2003$' "$T/board.calls" 2>/dev/null && ok "local land event: card moves to Code Complete" || ko "#2003 not Code Complete: $(bc)"
! /usr/bin/grep -q 'Done 2003' "$T/board.calls" 2>/dev/null && ok "local land event does not move the card to Done" || ko "#2003 read Done from a local landing: $(bc)"
# #382 replaced #307's rule: no pull request is read, and a work branch whose tip is in the landing branch is the delivery. #2004's
# card reached Code Complete and its branch is in dev, so the card goes to Done, as test-janitor-branch-state.sh pins for the shipped board.
/usr/bin/grep -q '^STATUS o 4 Done 2004$' "$T/board.calls" 2>/dev/null && ok "a work branch in dev moves a Code Complete card to Done" || ko "#2004 not Done: $(bc)"
! /usr/bin/grep -q '^STATUS o 4 Testing 2004$' "$T/board.calls" 2>/dev/null && ok "a merged pull request moves no card to Testing" || ko "#2004 moved to Testing over a pull request"
# #127: a PR that names the issue but was merged into its parent branch carries the work nowhere, so the card stops at
# Code Complete -- Done over a merge the base never took is the false delivery #125 showed.
/usr/bin/grep -q '^ADD o 4 Code Complete 2018$' "$T/board.calls" 2>/dev/null && ok "a merge into a parent branch leaves the card at Code Complete" || ko "#2018 left the dispatch column: $(bc)"
! /usr/bin/grep -q 'Done 2018' "$T/board.calls" 2>/dev/null && ok "a merge the base never carried is not Done" || ko "#2018 read Done over work main does not carry: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Todo 2005$' "$T/board.calls" 2>/dev/null && ok "completed unlanded run: card goes back to the queue, never Code Complete" || ko "#2005 not Todo: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Todo 2006$' "$T/board.calls" 2>/dev/null && ok "failed unlanded run: card moves to Todo" || ko "#2006 not Todo: $(bc)"
! /usr/bin/grep -q 'Code Complete 2006' "$T/board.calls" 2>/dev/null && ok "an older run's column never overrides the newest run's (#619)" || ko "#2006 took an older run's Code Complete: $(bc)"
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
# #666: the run's verdict is not the child's. A child that landed its own work reads Code Complete even when the run
# failed over a sibling: the sibling's failure does not unland commits that are in the branch.
/usr/bin/grep -q 'BOARD-RECONCILE #2022: card -> Code Complete (run run-mixed child route.router.1 landed work locally)' "$T/st/cycle.log" 2>/dev/null && ok "a landing inside a failed run is still the child's delivery" || ko "#2022 landing lost to the run's verdict: $(/usr/bin/grep '2022' "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q 'Code Complete 2023' "$T/board.calls" 2>/dev/null && ok "a child that landed nothing is never credited the run's landing" || ko "#2023 read Code Complete over its sibling's landing: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Todo 2023$' "$T/board.calls" 2>/dev/null && ok "a failed child with no branch and no PR stays in the loop's queue" || ko "#2023 did not stay in Todo: $(bc) $o"
/usr/bin/grep -q 'BOARD-RECONCILE #2023: card -> Todo (run run-mixed child route.router.2 fail with nothing landed (red gate))' "$T/st/cycle.log" 2>/dev/null && ok "the reason names the child and its own outcome" || ko "child reason not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q ' 2032$' "$T/board.calls" 2>/dev/null && ok "a card whose fix is a commit in dev keeps Code Complete" || ko "#2032 moved: $(/usr/bin/grep ' 2032$' "$T/board.calls")"
! /usr/bin/grep -q ' 2011$' "$T/board.calls" 2>/dev/null && ok "landed branch marker alone leaves the card in Code Complete" || ko "#2011 moved: $(bc)"
! /usr/bin/grep -q 'Done 2011' "$T/board.calls" 2>/dev/null && ok "landed branch marker alone is not Done" || ko "#2011 read Done from a landed branch marker: $(bc)"
/usr/bin/grep -q '^ADD o 4 In Progress 2013$' "$T/board.calls" 2>/dev/null && ok "no card: the item is created with its expected status in one write" || ko "#2013 not added: $(bc) $o"
! /usr/bin/grep -q '^STATUS o 4 In Progress 2013$' "$T/board.calls" 2>/dev/null && ok "no card: the status is not written a second time" || ko "#2013 written twice: $(bc)"
/usr/bin/grep -q 'BOARD-RECONCILE #2013: card added -> In Progress (run run-nocard is live)' "$T/st/cycle.log" 2>/dev/null && ok "the add is named in cycle.log" || ko "add not named: $(cat "$T/st/cycle.log")"
/usr/bin/grep -q '^STATUS o 4 In Progress 2014$' "$T/board.calls" 2>/dev/null && ! /usr/bin/grep -q 'ADD .* 2014$' "$T/board.calls" && ok "a card with no Status is written, not added again" || ko "#2014: $(bc)"
! /usr/bin/grep -q 'ADD .* 2001$' "$T/board.calls" 2>/dev/null && ok "a card that exists is never added again" || ko "#2001 added: $(bc)"
[ "$(/usr/bin/grep -c 'projectsV2/4/items' "$T/gh.calls")" = 1 ] && ok "the items are read once per pass" || ko "items read $(/usr/bin/grep -c 'projectsV2/4/items' "$T/gh.calls") times"
# An open issue with no live run, marker, PR, or owner label keeps the column its card has: only the owner moves a card
# back to Todo (#550).
! /usr/bin/grep -q ' 2015$' "$T/board.calls" 2>/dev/null && ok "an unclaimed open issue keeps its card's column" || ko "#2015 moved: $(bc) $o"
/usr/bin/grep -q 'STATE-REGRESSION #2015: card is Done but the issue is still open$' "$T/st/cycle.log" 2>/dev/null && ok "Done on an open issue is reported, and the floor never moves it back" || ko "#2015 Done regression not reported as is: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q '^ADD o 4 Blocked 2016$' "$T/board.calls" 2>/dev/null && ok "an open issue carrying needs-owner gets a Blocked card" || ko "#2016 not added Blocked: $(bc) $o"
[ "$(/usr/bin/grep -c graphql "$T/gh.calls")" = 1 ] && ok "the open issues are read once per pass, in one request" || ko "open issues read $(/usr/bin/grep -c graphql "$T/gh.calls") times"
# A spec is written, not scheduled (#117). A ticket /to-tickets cuts from it opens in Backlog, in ONE write when the card is
# missing (#86): Todo is the owner's go signal, so only the owner moves it there (#550). The child's `## Blocked by` line
# holds no column: the gate that holds a wave-2 ticket back until its blocker lands is the dispatch's own reading of those
# edges, never the column (#142).
/usr/bin/grep -q '^ADD o 4 Doing 2017$' "$T/board.calls" 2>/dev/null && ok "a spec with no card is created in Doing, a child of it is in flight" || ko "#2017 not added Open Specs: $(bc) $o"
! /usr/bin/grep -q 'Todo 2017' "$T/board.calls" 2>/dev/null && ok "a spec is never created in the queue column" || ko "#2017 read as work: $(bc)"
/usr/bin/grep -q 'BOARD-RECONCILE #2017: card added -> Doing (spec folds over its sub-issues)' "$T/st/cycle.log" 2>/dev/null && ok "the spec column is named in cycle.log" || ko "#2017 add not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q '^ADD o 4 Backlog 2019$' "$T/board.calls" 2>/dev/null && ok "a spec child with no card is created in Backlog" || ko "#2019 not added Backlog: $(bc) $o"
! /usr/bin/grep -q 'Blocked 2019' "$T/board.calls" 2>/dev/null && ok "a child whose blocker is still open is not held out of Todo" || ko "#2019 read Blocked: $(bc)"
# A spec's column is a fold over its sub-issues, read off the relation the one open-issue read already carries and the
# cards the pass already holds: no write with none (or none past the queue), so the floor opens a new spec in Backlog; Doing once a child reached Doing or a child was
# delivered while another is open (#2028), Closed when every child is terminal and one is Closed, Abandoned when every
# child is terminal and none is Closed. The pass also closes the spec issue when every child is closed.
for w in "ADD o 4 Closed 2020" "ADD o 4 Backlog 2021" "ADD o 4 Abandoned 2027" "ADD o 4 Doing 2028"; do
  /usr/bin/grep -q "^$w\$" "$T/board.calls" 2>/dev/null && ok "spec fold: $w" || ko "spec fold: no '$w': $(/usr/bin/grep -E ' 20(20|21|27|28)$' "$T/board.calls")"
done
! /usr/bin/grep -q 'Open Specs' "$T/board.calls" 2>/dev/null && ok "no spec is written to the old Open Specs column" || ko "Open Specs written: $(/usr/bin/grep 'Open Specs' "$T/board.calls")"
# A spec with at least one sub-issue, every one closed, is closed by the loop with one comment that names the condition.
# A spec with an open sub-issue (#2017, #2027, #2028, #2029), or with none (#2021), stays open (the owner's rule).
/usr/bin/grep -q '^api -X PATCH repos/x/y/issues/2020 -f state=closed -f state_reason=completed$' "$T/gh.calls" 2>/dev/null && ok "a spec whose every sub-issue is closed is closed" || ko "#2020 not closed: $(/usr/bin/grep 'issues/2020' "$T/gh.calls")"
[ "$(/usr/bin/grep -c '^api repos/x/y/issues/2020/comments -f body=Closed by the loop: every sub-issue (2) is closed' "$T/gh.calls")" = 1 ] && ok "the close carries one comment that names the condition" || ko "#2020 comment: $(/usr/bin/grep 'issues/2020' "$T/gh.calls")"
/usr/bin/grep -q 'SPEC-CLOSED #2020: every sub-issue (2) is closed' "$T/st/cycle.log" 2>/dev/null && ok "the close is named in cycle.log" || ko "#2020 close not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q 'issues/20\(17\|21\|27\|28\)\(/comments\| -X PATCH\| -f state\)' "$T/gh.calls" 2>/dev/null && ok "a spec with an open sub-issue, or with none, stays open" || ko "a spec closed early: $(/usr/bin/grep -E 'issues/20(17|21|27|28)' "$T/gh.calls")"
# A spec card already Closed whose child reopened keeps Closed (#246) and gains the tag instead of a move back.
! /usr/bin/grep -q ' 2029$' "$T/board.calls" 2>/dev/null && ok "a Closed spec whose child reopened keeps its column" || ko "#2029 moved: $(/usr/bin/grep ' 2029$' "$T/board.calls")"
/usr/bin/grep -q '^api -X POST repos/x/y/issues/2029/labels -f labels\[\]=reverted$' "$T/gh.calls" 2>/dev/null && ok "it gains the reverted tag" || ko "#2029 not tagged: $(/usr/bin/grep 'issues/2029' "$T/gh.calls")"
/usr/bin/grep -q 'SPEC-REVERTED #2029' "$T/st/cycle.log" 2>/dev/null && ok "the kept column is named in cycle.log" || ko "#2029 not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
# The owner starts a spec by moving its card to Todo, and its children follow it there, forward only (#589). The spec card
# keeps the owner's column: the fold writes nothing below Doing, so the two rules never fight.
/usr/bin/grep -q '^STATUS o 4 Todo 2043$' "$T/board.calls" 2>/dev/null && ok "a Backlog child of a spec in Todo follows it to Todo" || ko "#2043 did not follow its spec: $(bc) $o"
/usr/bin/grep -q '^ADD o 4 Todo 2045$' "$T/board.calls" 2>/dev/null && ok "a child with no card of a spec in Todo is added in Todo" || ko "#2045 not added Todo: $(bc)"
/usr/bin/grep -q 'BOARD-RECONCILE #2043: card -> Todo (its spec #2042 sits in Todo: the owner started it)' "$T/st/cycle.log" 2>/dev/null && ok "the follow is named in cycle.log" || ko "#2043 follow not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q ' 2044$' "$T/board.calls" 2>/dev/null && ok "a child past Backlog keeps its column" || ko "#2044 moved: $(/usr/bin/grep ' 2044$' "$T/board.calls")"
! /usr/bin/grep -q ' 204[26]$' "$T/board.calls" 2>/dev/null && ok "the spec card keeps the owner's column" || ko "a spec moved: $(/usr/bin/grep -E ' 204[26]$' "$T/board.calls")"
! /usr/bin/grep -q ' 2047$' "$T/board.calls" 2>/dev/null && ok "a child of a spec in Backlog stays in Backlog" || ko "#2047 moved: $(/usr/bin/grep ' 2047$' "$T/board.calls")"
# A spec is never work (#665): a commit subject that names it is its children's, so no work rule writes its card, and the
# fold alone sets it -- including the correction of a card a work rule moved forward while its children were in flight.
/usr/bin/grep -q '^STATUS o 4 Doing 2050$' "$T/board.calls" 2>/dev/null && ok "a spec card a work rule moved forward is folded back to Doing (#665)" || ko "#2050 not corrected: $(bc) $o"
/usr/bin/grep -q 'BOARD-RECONCILE #2050: card -> Doing (spec folds over its sub-issues)' "$T/st/cycle.log" 2>/dev/null && ok "the correction is named in cycle.log" || ko "#2050 correction not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q ' 2053$' "$T/board.calls" 2>/dev/null && ok "a commit that names a spec never moves its card forward" || ko "#2053 moved: $(/usr/bin/grep ' 2053$' "$T/board.calls")"
! /usr/bin/grep -q ' 2054$' "$T/board.calls" 2>/dev/null && ok "the correction leaves its queued child's card alone" || ko "#2054 moved: $(/usr/bin/grep ' 2054$' "$T/board.calls")"
# The fold adds no GitHub call: the children come with the open-issue read, never from a sub-issues route.
! /usr/bin/grep -q 'sub_issues' "$T/gh.calls" 2>/dev/null && ok "no sub-issues route is called" || ko "a sub-issues call: $(/usr/bin/grep sub_issues "$T/gh.calls")"
[ "$(/usr/bin/grep -c graphql "$T/gh.calls")" = 1 ] && ok "the fold rides the one open-issue read" || ko "reads: $(/usr/bin/grep -c graphql "$T/gh.calls")"
# A run parent (the run-parent label) is the roll-up of a run's children in GitHub, never work (#500): the pass adds no card for
# one (#2040), removes the card one already has (#2041), and leaves the issue itself alone. Both writes are sent only when the
# board differs: #2040 has no card to remove, and #2041 has one, so it is removed once and never written a status.
! /usr/bin/grep -q ' 2040$' "$T/board.calls" 2>/dev/null && ok "a run parent with no card is not added, and nothing is removed" || ko "#2040 written: $(/usr/bin/grep ' 2040$' "$T/board.calls")"
/usr/bin/grep -q '^REMOVE o 4 2041$' "$T/board.calls" 2>/dev/null && ok "a run parent that has a card loses it" || ko "#2041 card not removed: $(bc) $o"
[ "$(/usr/bin/grep -c ' 2041$' "$T/board.calls")" = 1 ] && ok "the card of a run parent is removed in one write, never given a status" || ko "#2041 writes: $(/usr/bin/grep ' 2041$' "$T/board.calls")"
/usr/bin/grep -q 'BOARD-RECONCILE #2041: card removed (run-parent' "$T/st/cycle.log" 2>/dev/null && ok "the removal is named in cycle.log" || ko "#2041 removal not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q 'issues/204[01]\(/comments\| -X \| -f \)' "$T/gh.calls" 2>/dev/null && ok "the roll-up stays in GitHub: its issue is neither closed nor changed" || ko "a run parent issue was written: $(/usr/bin/grep 'issues/204[01]' "$T/gh.calls")"
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
