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
export SG_TAKEOVER_SECONDS=1000000000000   # these runs test other rules; their fixed start stamps are past the takeover bound (ADR-0166)
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
here="$(cd "$(dirname "$src")" && pwd)"; cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$T/s/"   # the janitor reaches the open-issue list through the door beside it (ghcall.sh)
cp "$here/board_columns.py" "$here/board_facts.py" "$here/issue_ledger.py" "$here/board-definition.json" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$here/spec_fold.py" "$T/s/"   # a refused card write queues its comment through the helper beside it (#141), and names it from the closed set (#142)
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$*" in
  *" 2009"*) echo "board refused #2009" >&2; exit 7;;
  *" 2110"*) echo "#2110: refused, an automated writer moves 'Abandoned' back to 'Todo'; the card keeps 'Abandoned'" >&2; exit 3;;
esac
case "$1" in
  --run-fields) echo "RUN $2 $3 $4 $5 $6" >> "TMPDIR/board.calls";;
  --clear-run) echo "CLEAR $2 $3 $4" >> "TMPDIR/board.calls";;
  --add) echo "ADD $2 $3 $4 $5" >> "TMPDIR/board.calls";;
  --remove) echo "REMOVE $2 $3 $4" >> "TMPDIR/board.calls";;
  *) echo "STATUS $1 $2 $3 $4" >> "TMPDIR/board.calls"; if [ -n "$SG_MOVE_REASON" ]; then echo "REASON $4 $SG_MOVE_REASON" >> "TMPDIR/board.calls"; fi;;
esac
J
sed -i '' "s|TMPDIR|$T|g" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
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
    for n in 2002 2003 2005 2006 2009 2022 2023 2055 2083 2084 2085 2086 2087 2088 2089 2098 2099 2110; do printf '%s\tIn Progress\thttps://api.github.com/repos/x/y\n' "$n"; done   # #2098 and #2099: the children of run-land-fail (#2015); #2055 and #2083 to #2089: the children of the retried, aborted and human runs (#2015); #2110: a Move a rule refuses (#2122)
    for n in 2001 2007 2008 2012; do printf '%s\tTodo\thttps://api.github.com/repos/x/y\n' "$n"; done
    for n in 2004 2010 2011 2032 2050; do printf '%s\tCode Complete\thttps://api.github.com/repos/x/y\n' "$n"; done   # #2050: a spec card a work rule wrongly moved forward (#665)
    printf '2051\tIn Progress\thttps://api.github.com/repos/x/y\n2052\tClosed\thttps://api.github.com/repos/x/y\n2053\tBacklog\thttps://api.github.com/repos/x/y\n2054\tBacklog\thttps://api.github.com/repos/x/y\n'   # #2050's children, and spec #2053 with its queued child
    printf '2014\t\thttps://api.github.com/repos/x/y\n'   # on the board, no Status yet
    printf '2030\tDone\thttps://api.github.com/repos/x/y\n2031\tAbandoned\thttps://api.github.com/repos/x/y\n2029\tClosed\thttps://api.github.com/repos/x/y\n'   # spec children, and a spec card already Closed
    printf '2041\tBacklog\thttps://api.github.com/repos/x/y\n'   # a run parent that already has a card
    printf '2042\tTodo\thttps://api.github.com/repos/x/y\n2043\tBacklog\thttps://api.github.com/repos/x/y\n2044\tBlocked\thttps://api.github.com/repos/x/y\n'   # the owner moved spec #2042 to Todo
    printf '2046\tBacklog\thttps://api.github.com/repos/x/y\n2047\tBacklog\thttps://api.github.com/repos/x/y\n'   # spec #2046 still sits in Backlog
    printf '2015\tBlocked\thttps://api.github.com/repos/x/y\n'   # stale Blocked card with no current blocking cause
    printf '2070\tBacklog\thttps://api.github.com/repos/x/y\n2071\tDoing\thttps://api.github.com/repos/x/y\n2072\tBacklog\thttps://api.github.com/repos/x/y\n2073\tTodo\thttps://api.github.com/repos/x/y\n2075\tDoing\thttps://api.github.com/repos/x/y\n2076\tBacklog\thttps://api.github.com/repos/x/y\n2077\tDone\thttps://api.github.com/repos/x/y\n'   # #942: parents that are not specs, and their children
    printf '2080\tTodo\thttps://api.github.com/repos/x/y\n2081\tIn Progress\thttps://api.github.com/repos/x/y\n2082\tBacklog\thttps://api.github.com/repos/x/y\n2090\tBacklog\thttps://api.github.com/repos/x/y\n2091\tTodo\thttps://api.github.com/repos/x/y\n2092\tBacklog\thttps://api.github.com/repos/x/y\n2093\tBacklog\thttps://api.github.com/repos/x/y\n2094\tIn Progress\thttps://api.github.com/repos/x/y\n2095\tCode Complete\thttps://api.github.com/repos/x/y\n';;   # #1131: a spec reads Todo while a ticket is in flight, and never a work column
  *projectItems*)   # the fallback read for an issue the REST items route omits (#1463): #2096's card sits on project 4
    case "$a" in     # in Doing, read back through GraphQL; every other omitted issue carries no card at all
      *number=2096*) printf '%s\n' '{"data":{"repository":{"issue":{"projectItems":{"nodes":[{"databaseId":2096,"project":{"number":4},"fieldValueByName":{"name":"Code Complete"}}]}}}}}' ;;
      *) printf '%s\n' '{"data":{"repository":{"issue":null}}}' ;;
    esac;;
  *graphql*)   # the open-issue list in ONE request: every open issue of the repository, with its labels
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 2001 2002 2003 2004 2005 2006 2007 2008 2009 2011 2012 2013 2014 2015 2016 2017 2018 2019 2020 2021 2027 2028 2029 2032 2040 2041 2042 2043 2044 2045 2046 2047 2050 2051 2052 2053 2054 2055 2060 2070 2071 2072 2073 2074 2075 2076 2077 2080 2081 2082 2083 2084 2085 2086 2087 2088 2089 2090 2091 2092 2093 2094 2095 2096 2097 2098 2099 2110; do
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
        # #2060 is a spec whose every sub-issue is closed, but a landing named #2062 outside its run's dispatch set (#775):
        # that close has no evidence, so the spec is held open, never closed off it.
        2060) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":2,"completed":2}'; kids='[{"number":2061},{"number":2062}]';;
        # #2042 sits in Todo with children in Backlog (#2043), with no card (#2045) and past Backlog (#2044): the pass lifts none
        # of them. #2046 still sits in Backlog, so #2047 stays.
        2042) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":3,"completed":0}'; kids='[{"number":2043},{"number":2044},{"number":2045}]';;
        2046) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":1,"completed":0}'; kids='[{"number":2047}]';;
        # A spec is never work (#665): its children's commits name it, but the fold alone sets its card. #2050's card was
        # wrongly at Code Complete while one child is still in flight; #2053 is still queued.
        2050) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":2,"completed":1}'; kids='[{"number":2051},{"number":2052}]';;
        2053) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":1,"completed":0}'; kids='[{"number":2054}]';;
        # #942: a parent that is not a spec folds its children's columns too. #2070 has a child at Doing; #2072's child is still
        # in Todo; #2074 is a run parent, never work; #2076's child is Done, so the parent is delivered by its own children but never closed.
        2070) labs='{"nodes":[]}'; subs='{"total":1,"completed":0}'; kids='[{"number":2071}]';;
        2072) labs='{"nodes":[]}'; subs='{"total":1,"completed":0}'; kids='[{"number":2073}]';;
        2074) labs='{"nodes":[{"name":"run-parent"}]}'; subs='{"total":1,"completed":0}'; kids='[{"number":2075}]';;
        2076) labs='{"nodes":[]}'; subs='{"total":1,"completed":1}'; kids='[{"number":2077}]';;
        2071|2073|2075|2077) labs='{"nodes":[]}';;
        # #1131: a spec reads Todo while any ticket is in flight, and it never follows one into a work column. #2080 is in
        # Todo with a ticket in progress; #2090 is in Backlog with a ticket in Todo, so the fold lifts it; #2092 sits in
        # Backlog with no ticket in flight, so it keeps the owner's column; #2094 sits in a work column and returns to Todo.
        2080) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":2,"completed":0}'; kids='[{"number":2081},{"number":2082}]';;
        2090) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":1,"completed":0}'; kids='[{"number":2091}]';;
        2092) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":1,"completed":0}'; kids='[{"number":2093}]';;
        2094) labs='{"nodes":[{"name":"spec"}]}'; subs='{"total":1,"completed":0}'; kids='[{"number":2095}]';;
        2081|2082|2091|2093|2095) labs='{"nodes":[{"name":"ready-for-agent"}]}';;
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
mk run-rule 2110
rec run-rule '{"event":"run_start","run_id":"run-rule","ts":"2026-10-13T08:03:30Z"}
{"event":"verdict","verdict":"failed","reasons":["red gate"],"exit_code":1}'
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
# #1463: the REST items route omits #2096, but its card is on the board (GraphQL projectItems carries it in
# Code Complete), so the read falls back once and the pass moves the found card -- it is never added again,
# never named absent. #2097 is omitted by both reads: the fallback answers no card, the omission stands,
# and the floor adds it.
mk run-omitted-card 2096
rec run-omitted-card '{"event":"run_start","run_id":"run-omitted-card","ts":"2026-10-13T08:12:30Z"}
{"event":"spawn_accepted","child":"c1","definition":"GitHub issue #2096 on fix/2096-x"}'
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
# #2015: the run landed through route.router.1, and route.router.2 PASSED its own entries while its own engine landing
# failed with merge_conflict. Its outcome word is `pass`, but its work never reached the target, so the note must name the
# landing failure and never call it a pass.
mk run-land-fail 2098
rec run-land-fail '{"event":"run_start","run_id":"run-land-fail","ts":"2026-10-13T08:13:30Z"}
{"event":"spawn_accepted","child":"route.router.1","definition":"GitHub issue #2099 on fix/2099-x"}
{"event":"spawn_accepted","child":"route.router.2","definition":"GitHub issue #2098 on fix/2098-x"}
{"event":"outcome","node":"route.router.2","entry":1,"outcome":"pass","reasons":["tests green"]}
{"event":"land","node":"route.router.2","entry":1,"outcome":"fail","reasons":["merge_conflict"],"effect":{"name":"merge_land","head":"b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0"}}
{"event":"land","node":"route.router.1","entry":1,"outcome":"pass","reasons":[],"effect":{"name":"merge_land","head":"a7df8f1ecff77bdb5833721121a7a50be4066053","held_out":"pass"}}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
git -C "$T/co" init -q
# A fork inherits its upstream's history, whose subjects end with the upstream's PR numbers (#759). SG_LAND_SCAN_FROM names
# the fork point, the last upstream commit, so the scan reads only commits made in this repository: #2019 stays in Backlog.
git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "An upstream pull request landed (#2019)"
export SG_LAND_SCAN_FROM=$(git -C "$T/co" rev-parse HEAD)
git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "Fix the thing by hand (#2032)"
# A spec's children land their own commits under the parent's number (#665): the subject names the spec, and no work rule
# may read that as the spec's own delivery.
git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "A child of the spec landed (#2050)"
git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "A queued child's fix is prepared (#2053)"
git -C "$T/co" update-ref refs/remotes/origin/dev HEAD
# #2022's child landing reached the landing branch: its head is in origin/dev, so the run's failure does not unland it (#691)
sed -i '' "s/a7df8f1ecff77bdb5833721121a7a50be4066053/$(git -C "$T/co" rev-parse HEAD)/" "$T/co/runs/run-mixed/record.jsonl"
# #2015: the LAST landing a node made is its final state. A landing that failed and was retried until it stood is no
# failure (#2084), an aborted landing is no failure (#2086), and a retried landing that moved nothing is no failure
# (#2088). A landing of the run's own stage is no child's failure either.
git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "A retried child's work reaches dev (#2084)"
retry2=$(git -C "$T/co" rev-parse HEAD)
git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "A child's work reaches dev (#2083)"
retry1=$(git -C "$T/co" rev-parse HEAD)
git -C "$T/co" update-ref refs/remotes/origin/dev HEAD
mk run-retry 2083,2084
rec run-retry '{"event":"run_start","run_id":"run-retry","ts":"2026-10-13T08:14:00Z"}
{"event":"spawn_accepted","child":"route.router.1","definition":"GitHub issue #2083 on fix/2083-x"}
{"event":"spawn_accepted","child":"route.router.2","definition":"GitHub issue #2084 on fix/2084-x"}
{"event":"outcome","node":"route.router.1","entry":1,"outcome":"pass","reasons":[]}
{"event":"outcome","node":"route.router.2","entry":1,"outcome":"pass","reasons":["tests green"]}
{"event":"land","node":"route","outcome":"fail","reasons":["merge_land unavailable"],"effect":{"name":"merge_land"}}
{"event":"land","node":"route.router.1","entry":1,"outcome":"pass","reasons":[],"effect":{"name":"merge_land","head":"REPL1","held_out":"pass"}}
{"event":"land","node":"route.router.2","entry":1,"outcome":"fail","reasons":["merge_conflict"],"effect":{"name":"merge_land","head":"REPL2"}}
{"event":"land","node":"route.router.2","entry":2,"outcome":"pass","reasons":[],"effect":{"name":"merge_land","head":"REPL2","held_out":"pass"}}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
sed -i '' "s/REPL1/$retry1/g; s/REPL2/$retry2/g" "$T/co/runs/run-retry/record.jsonl"
# #2086's child passed its own entries and its landing was aborted: the note never calls an aborted landing a failure.
mk run-abort 2085,2086
rec run-abort '{"event":"run_start","run_id":"run-abort","ts":"2026-10-13T08:15:00Z"}
{"event":"spawn_accepted","child":"route.router.1","definition":"GitHub issue #2085 on fix/2085-x"}
{"event":"spawn_accepted","child":"route.router.2","definition":"GitHub issue #2086 on fix/2086-x"}
{"event":"outcome","node":"route.router.1","entry":1,"outcome":"pass","reasons":[]}
{"event":"outcome","node":"route.router.2","entry":1,"outcome":"pass","reasons":["tests green"]}
{"event":"land","node":"route.router.1","entry":1,"outcome":"pass","reasons":[],"effect":{"name":"merge_land","head":"REPL1","held_out":"pass"}}
{"event":"land","node":"route.router.2","entry":1,"outcome":"abort","reasons":["the owner stopped the landing"],"effect":{"name":"merge_land","head":"REPL2"}}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
sed -i '' "s/REPL1/$retry1/g; s/REPL2/$retry2/g" "$T/co/runs/run-abort/record.jsonl"
# #2088's child failed to land and its retry moved nothing: the final landing is a pass, so the note names that pass.
mk run-nomove 2087,2088
rec run-nomove '{"event":"run_start","run_id":"run-nomove","ts":"2026-10-13T08:16:00Z"}
{"event":"spawn_accepted","child":"route.router.1","definition":"GitHub issue #2087 on fix/2087-x"}
{"event":"spawn_accepted","child":"route.router.2","definition":"GitHub issue #2088 on fix/2088-x"}
{"event":"outcome","node":"route.router.1","entry":1,"outcome":"pass","reasons":[]}
{"event":"outcome","node":"route.router.2","entry":1,"outcome":"pass","reasons":["tests green"]}
{"event":"land","node":"route.router.1","entry":1,"outcome":"pass","reasons":[],"effect":{"name":"merge_land","head":"REPL1","held_out":"pass"}}
{"event":"land","node":"route.router.2","entry":1,"outcome":"fail","reasons":["merge_conflict"],"effect":{"name":"merge_land","head":"REPL2"}}
{"event":"land","node":"route.router.2","entry":2,"outcome":"pass","reasons":[],"effect":{"name":"merge_land","before":"REPL2","head":"REPL2"}}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
sed -i '' "s/REPL1/$retry1/g; s/REPL2/$retry2/g" "$T/co/runs/run-nomove/record.jsonl"
# #2089's child needs a human and its landing failed: the note names the landing failure and the human need apart.
mk run-human 2055,2089
rec run-human '{"event":"run_start","run_id":"run-human","ts":"2026-10-13T08:17:00Z"}
{"event":"spawn_accepted","child":"route.router.1","definition":"GitHub issue #2055 on fix/2055-x"}
{"event":"spawn_accepted","child":"route.router.2","definition":"GitHub issue #2089 on fix/2089-x"}
{"event":"outcome","node":"route.router.1","entry":1,"outcome":"pass","reasons":[]}
{"event":"outcome","node":"route.router.2","entry":1,"outcome":"needs-human","reasons":["the acceptance needs an owner key"]}
{"event":"land","node":"route.router.1","entry":1,"outcome":"pass","reasons":[],"effect":{"name":"merge_land","head":"REPL1","held_out":"pass"}}
{"event":"land","node":"route.router.2","entry":1,"outcome":"fail","reasons":["merge_conflict"],"effect":{"name":"merge_land","head":"REPL2"}}
{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}'
sed -i '' "s/REPL1/$retry1/g; s/REPL2/$retry2/g" "$T/co/runs/run-human/record.jsonl"
printf '1\trun-x\t2062\twave/run-x\n' > "$T/st/wave-outside.tsv"   # a landing named #2062 outside its run's dispatch set (#775)
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
! /usr/bin/grep -q '^STATUS o 4 Closed 2004$' "$T/board.calls" 2>/dev/null && ok "a merged pull request moves no card to Closed" || ko "#2004 moved to Closed over a pull request"
# #127: a PR that names the issue but was merged into its parent branch carries the work nowhere, so the card stops at
# Code Complete -- Done over a merge the base never took is the false delivery #125 showed.
/usr/bin/grep -q '^ADD o 4 Code Complete 2018$' "$T/board.calls" 2>/dev/null && ok "a merge into a parent branch leaves the card at Code Complete" || ko "#2018 left the dispatch column: $(bc)"
! /usr/bin/grep -q 'Done 2018' "$T/board.calls" 2>/dev/null && ok "a merge the base never carried is not Done" || ko "#2018 read Done over work main does not carry: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Todo 2005$' "$T/board.calls" 2>/dev/null && ok "completed unlanded run: card goes back to the queue, never Code Complete" || ko "#2005 not Todo: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Todo 2006$' "$T/board.calls" 2>/dev/null && ok "failed unlanded run: card moves to Todo" || ko "#2006 not Todo: $(bc)"
# A card a run left behind moves back to Todo with the reason named, so board.py's one backward move (#1059) admits it.
/usr/bin/grep -q '^REASON 2005 no-landing$' "$T/board.calls" 2>/dev/null && ok "the move back to Todo names the reason a run that landed nothing gives (#1059)" || ko "#2005 moved without the reason: $(bc)"
[ "$(/usr/bin/grep -c '^REASON ' "$T/board.calls" 2>/dev/null)" = "$(/usr/bin/grep 'nothing landed' "$T/st/cycle.log" 2>/dev/null | /usr/bin/grep -vc '#2110')" ] && ok "only a move back to Todo carries the reason" || ko "reasons: $(/usr/bin/grep '^REASON ' "$T/board.calls") vs landed-nothing moves in cycle.log"
! /usr/bin/grep -q 'Code Complete 2006' "$T/board.calls" 2>/dev/null && ok "an older run's column never overrides the newest run's (#619)" || ko "#2006 took an older run's Code Complete: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Blocked 2007$' "$T/board.calls" 2>/dev/null && ok "owner label: card moves to Blocked" || ko "#2007 not Blocked: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Blocked 2012$' "$T/board.calls" 2>/dev/null && ok "ratification label: card moves to Blocked" || ko "#2012 not Blocked: $(bc)"
/usr/bin/grep -q 'BOARD-RECONCILE #2006: card -> Todo (run run-failed failed with nothing landed (red gate))' "$T/st/cycle.log" 2>/dev/null && ok "the pass names the reason for the move" || ko "reason not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q '^STATUS .* 2008$' "$T/board.calls" 2>/dev/null && ok "card already in the expected status is not written again" || ko "#2008 status was rewritten: $(bc)"
/usr/bin/grep -q '^CLEAR o 4 2005$' "$T/board.calls" 2>/dev/null && ok "leaving In Progress clears Run fields" || ko "#2005 Run fields not cleared: $(bc)"
/usr/bin/grep -q 'BOARD-WRITE-FAILED #2009' "$T/st/cycle.log" 2>/dev/null && ok "failed board write is named in cycle.log" || ko "board failure not logged: $(cat "$T/st/cycle.log" 2>/dev/null) $o"
# Every card write is a ledger event (#1841): the janitor records the column it writes before it calls the writer, and the
# writer's own proof ends the prediction. A write the board refused is unproven, so its event stays pending and the hub
# names it as a defect once its bound passes.
fact=$(SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" read "x/y#2001" board 2>/dev/null)
case "$fact" in *'"by": "janitor"'*'"column": "In Progress"'*'"state": "written"'*) ok "a landed card write is a ledger fact its writer proved";; *) ko "#2001's card write is no ledger fact: '$fact'";; esac
fact=$(SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" read "x/y#2009" board 2>/dev/null)
case "$fact" in *'"by": "janitor"'*'"state": "pending"'*) ok "a refused card write keeps its pending ledger event";; *) ko "#2009's refused write is no pending fact: '$fact'";; esac
# The same refusal is a comment on the issue the write was about (#141): the card says why it did not move, through the
# queue whose one flusher posts it, and the comment carries the refusal's name and GitHub's error.
cq="$T/st/cq/x__y#2009-refusal-board-write-failed.md"
[ -f "$cq" ] && ok "a refused card write queues one comment on that issue" || ko "no refusal comment: $(ls "$T/st/cq" 2>/dev/null) $o"
/usr/bin/grep -qF 'BOARD-WRITE-FAILED #2009' "$cq" 2>/dev/null && /usr/bin/grep -qF 'board refused #2009' "$cq" 2>/dev/null \
  && ok "the comment carries the refusal's name and its error" || ko "comment body: $(cat "$cq" 2>/dev/null)"
[ "$(ls "$T/st/cq" | /usr/bin/grep -c 'refusal')" = 1 ] && ok "one refused write is one comment" || ko "refusal comments: $(ls "$T/st/cq")"

# (#2122) A move a board rule refuses is settled, not a failure: board.py exits 3 for a backward move, and the card keeps its
# column (ADR-0143). The write can never land, so its pending ledger event ends here; one left pending holds every dispatch
# (ADR-0165), which is how the bug lane stood still for hours on #2052.
/usr/bin/grep -q 'BOARD-REJECTED #2110' "$T/st/cycle.log" 2>/dev/null && ok "a rule refusal is logged as rejected" || ko "rule refusal not logged: $(cat "$T/st/cycle.log" 2>/dev/null | tail -3)"
fact=$(SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" read "x/y#2110" board 2>/dev/null)
case "$fact" in *'"state": "pending"'*) ko "a rule refusal left its board event pending: '$fact'";; *) ok "a rule refusal ends its pending board event";; esac
[ -z "$(SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" pending 'x/y#2110' 2>/dev/null)" ] && ok "the refused move waits on no dispatch" || ko "the refused move still waits: $(SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" pending 'x/y#2110' 2>/dev/null)"
! ls "$T/st/cq" | /usr/bin/grep -q '2110' && ok "a rule refusal queues no comment" || ko "a rule refusal queued a comment: $(ls "$T/st/cq")"
/usr/bin/grep -q '^STATUS o 4 Done 2010$' "$T/board.calls" 2>/dev/null && ok "closed issue: card moves to Done" || ko "#2010 not Done: $(bc)"
# #271: a run that asked for children credits a landing to the child that made it, never to every issue it held.
# #2022's child landed its own work, so that card leaves Todo; #2023's child failed and committed nothing, so it has
# no branch and no PR and never reads Code Complete.
/usr/bin/grep -q '^STATUS o 4 Code Complete 2022$' "$T/board.calls" 2>/dev/null && ok "the child that landed moves its own card to Code Complete" || ko "#2022 not Code Complete: $(bc) $o"
# #666: the run's verdict is not the child's. A child that landed its own work reads Code Complete even when the run
# failed over a sibling: the sibling's failure does not unland commits that are in the branch.
/usr/bin/grep -q 'BOARD-RECONCILE #2022: card -> Code Complete (run run-mixed landed work locally)' "$T/st/cycle.log" 2>/dev/null && ok "a landing inside a failed run is still the child's delivery" || ko "#2022 landing lost to the run's verdict: $(/usr/bin/grep '2022' "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q 'Code Complete 2023' "$T/board.calls" 2>/dev/null && ok "a child that landed nothing is never credited the run's landing" || ko "#2023 read Code Complete over its sibling's landing: $(bc)"
/usr/bin/grep -q '^STATUS o 4 Todo 2023$' "$T/board.calls" 2>/dev/null && ok "a failed child with no branch and no PR stays in the loop's queue" || ko "#2023 did not stay in Todo: $(bc) $o"
/usr/bin/grep -q 'BOARD-RECONCILE #2023: card -> Todo (run run-mixed child route.router.2 fail with nothing landed (red gate))' "$T/st/cycle.log" 2>/dev/null && ok "the reason names the child and its own outcome" || ko "child reason not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
# #2015: a child whose own landing failed names that failure, never its `pass` outcome word.
/usr/bin/grep -q 'BOARD-RECONCILE #2098: card -> Todo (run run-land-fail child route.router.2 with nothing landed (its landing failed: merge_conflict))' "$T/st/cycle.log" 2>/dev/null && ok "a child whose landing failed names the landing failure" || ko "landing failure not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q 'run run-land-fail child route.router.2 pass with nothing landed' "$T/st/cycle.log" 2>/dev/null && ok "the note never calls a child whose work did not land a pass" || ko "the note says pass for a child that landed nothing: $(cat "$T/st/cycle.log" 2>/dev/null)"
# #2015: the last landing a node made decides. A failure its retry stood is no failure, so the card leaves the loop.
/usr/bin/grep -q '^STATUS o 4 Code Complete 2084$' "$T/board.calls" 2>/dev/null && ok "a child whose land failure was retried and stood still stays out of the loop" || ko "#2084 did not leave the loop: $(bc) $o"
! /usr/bin/grep -qE 'run run-retry .*its landing failed' "$T/st/cycle.log" 2>/dev/null && ok "a retried landing that stood names no landing failure" || ko "retried landing named a failure: $(/usr/bin/grep 'run-retry' "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q 'merge_land unavailable' "$T/st/cycle.log" 2>/dev/null && ok "a failed landing of the run's own stage is no child's failure" || ko "the run's own landing failure reached a child's note: $(/usr/bin/grep 'merge_land unavailable' "$T/st/cycle.log")"
# #2015: an aborted landing is not a failed one, and the note keeps the child's own outcome word.
! /usr/bin/grep -qE 'run run-abort .*its landing failed' "$T/st/cycle.log" 2>/dev/null && ok "an aborted landing is never named a landing failure" || ko "aborted landing named a failure: $(/usr/bin/grep 'run-abort' "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q 'BOARD-RECONCILE #2086: card -> Todo (run run-abort child route.router.2 pass with nothing landed (tests green))' "$T/st/cycle.log" 2>/dev/null && ok "an aborted landing keeps the child's own outcome in the note" || ko "#2086 note: $(/usr/bin/grep '2086' "$T/st/cycle.log" 2>/dev/null)"
# #2015: a retried landing that moved nothing is the child's final landing, never the failure before it.
/usr/bin/grep -q 'BOARD-RECONCILE #2088: card -> Todo (run run-nomove child route.router.2 pass with nothing landed (tests green))' "$T/st/cycle.log" 2>/dev/null && ok "a retried landing names its final state, never the earlier failure" || ko "#2088 note: $(/usr/bin/grep '2088' "$T/st/cycle.log" 2>/dev/null)"
# #2015: a child that needs a human and whose landing failed names the landing failure and the human need apart.
/usr/bin/grep -q 'BOARD-RECONCILE #2089: card -> Blocked (run run-human child route.router.2 its landing failed: merge_conflict and needs a human)' "$T/st/cycle.log" 2>/dev/null && ok "a needs-human child names its landing failure and the human need" || ko "#2089 note: $(/usr/bin/grep '2089' "$T/st/cycle.log" 2>/dev/null)"
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
[ "$(/usr/bin/grep -c 'subIssuesSummary' "$T/gh.calls")" = 1 ] && ok "the open issues are read once per pass, in one request" || ko "open issues read $(/usr/bin/grep -c 'subIssuesSummary' "$T/gh.calls") times"
# A spec is written, not scheduled (#117). A ticket /to-tickets cuts from it opens in Backlog, in ONE write when the card is
# missing (#86): Todo is the owner's go signal, so only the owner moves it there (#550). The child's `## Blocked by` line
# holds no column: the gate that holds a wave-2 ticket back until its blocker lands is the dispatch's own reading of those
# edges, never the column (#142).
/usr/bin/grep -q '^ADD o 4 Backlog 2017$' "$T/board.calls" 2>/dev/null && ok "a spec with no card is created in Backlog, a child of it is in flight (ADR-0142)" || ko "#2017 not added Open Specs: $(bc) $o"
! /usr/bin/grep -q 'Todo 2017' "$T/board.calls" 2>/dev/null && ok "a spec is never created in the queue column" || ko "#2017 read as work: $(bc)"
/usr/bin/grep -q 'BOARD-RECONCILE #2017: card added -> Backlog (open issue with no card)' "$T/st/cycle.log" 2>/dev/null && ok "the spec column is named in cycle.log" || ko "#2017 add not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q '^ADD o 4 Backlog 2019$' "$T/board.calls" 2>/dev/null && ok "a spec child with no card is created in Backlog" || ko "#2019 not added Backlog: $(bc) $o"
! /usr/bin/grep -q 'Code Complete 2019' "$T/board.calls" 2>/dev/null && ok "an upstream commit before the fork point names no issue of this repository" || ko "#2019 read Code Complete over an upstream subject: $(bc)"
! /usr/bin/grep -q 'Blocked 2019' "$T/board.calls" 2>/dev/null && ok "a child whose blocker is still open is not held out of Todo" || ko "#2019 read Blocked: $(bc)"
# A spec's column is a fold over its sub-issues, read off the relation the one open-issue read already carries and the
# cards the pass already holds: the floor opens a new spec in Backlog, and a spec never takes Doing from its children (ADR-0142,
# #1034). The children's terminal columns give it no column either (#1906): the card follows the spec's own issue, so a spec
# whose children are all closed keeps the owner's column here and reaches Closed on the pass that reads its own close.
# The pass also closes the spec issue when every child is closed.
for w in "ADD o 4 Backlog 2020" "ADD o 4 Backlog 2021" "ADD o 4 Backlog 2027" "ADD o 4 Backlog 2028"; do
  /usr/bin/grep -q "^$w\$" "$T/board.calls" 2>/dev/null && ok "spec fold: $w" || ko "spec fold: no '$w': $(/usr/bin/grep -E ' 20(20|21|27|28)$' "$T/board.calls")"
done
! /usr/bin/grep -q 'Open Specs' "$T/board.calls" 2>/dev/null && ok "no spec is written to the old Open Specs column" || ko "Open Specs written: $(/usr/bin/grep 'Open Specs' "$T/board.calls")"
# A spec with at least one sub-issue, every one closed, is closed by the loop with one comment that names the condition.
# A spec with an open sub-issue (#2017, #2027, #2028, #2029), or with none (#2021), stays open (the owner's rule).
/usr/bin/grep -q '^api -X PATCH repos/x/y/issues/2020 -f state=closed -f state_reason=completed$' "$T/gh.calls" 2>/dev/null && ok "a spec whose every sub-issue is closed is closed" || ko "#2020 not closed: $(/usr/bin/grep 'issues/2020' "$T/gh.calls")"
[ "$(/usr/bin/grep -c '^api repos/x/y/issues/2020/comments -f body=Closed by the loop: every sub-issue (2) is closed' "$T/gh.calls")" = 1 ] && ok "the close carries one comment that names the condition" || ko "#2020 comment: $(/usr/bin/grep 'issues/2020' "$T/gh.calls")"
/usr/bin/grep -q 'SPEC-CLOSED #2020: every sub-issue (2) is closed' "$T/st/cycle.log" 2>/dev/null && ok "the close is named in cycle.log" || ko "#2020 close not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q 'issues/20\(17\|21\|27\|28\)\(/comments\| -X PATCH\| -f state\)' "$T/gh.calls" 2>/dev/null && ok "a spec with an open sub-issue, or with none, stays open" || ko "a spec closed early: $(/usr/bin/grep -E 'issues/20(17|21|27|28)' "$T/gh.calls")"
# A spec whose sub-issue a landing named outside its run's dispatch set is held open (#775): the cascade never closes a
# parent off a close that has no evidence, and the hold is named.
! /usr/bin/grep -q 'issues/2060\(/comments\| -X PATCH\| -f state\)' "$T/gh.calls" 2>/dev/null && ok "a spec whose sub-issue was named outside a dispatch set stays open" || ko "#2060 closed: $(/usr/bin/grep 'issues/2060' "$T/gh.calls")"
/usr/bin/grep -q 'SPEC-HELD #2060: sub-issue #2062 was named outside the dispatch set of run run-x' "$T/st/cycle.log" 2>/dev/null && ok "the hold is named in cycle.log" || ko "#2060 hold not named: $(/usr/bin/grep 2060 "$T/st/cycle.log")"
# A spec card already Closed whose child reopened keeps Closed (#246) and gains the tag instead of a move back.
! /usr/bin/grep -q ' 2029$' "$T/board.calls" 2>/dev/null && ok "a Closed spec whose child reopened keeps its column" || ko "#2029 moved: $(/usr/bin/grep ' 2029$' "$T/board.calls")"
/usr/bin/grep -q '^api -X POST repos/x/y/issues/2029/labels -f labels\[\]=reverted$' "$T/gh.calls" 2>/dev/null && ok "it gains the reverted tag" || ko "#2029 not tagged: $(/usr/bin/grep 'issues/2029' "$T/gh.calls")"
/usr/bin/grep -q 'SPEC-REVERTED #2029' "$T/st/cycle.log" 2>/dev/null && ok "the kept column is named in cycle.log" || ko "#2029 not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
# A spec in Todo lifts none of its Backlog tickets; the owner's move of the spec carries them (#1909). The spec card keeps
# the owner's column: the fold writes nothing below Doing, so the two rules never fight.
! /usr/bin/grep -q ' 2043$' "$T/board.calls" 2>/dev/null && ok "a spec in Todo lifts no Backlog ticket (#1909)" || ko "#2043 moved with its spec: $(bc) $o"
! /usr/bin/grep -q '^ADD o 4 Todo 2045$' "$T/board.calls" 2>/dev/null && ok "a child with no card of a spec in Todo is not added in Todo (#1909)" || ko "#2045 added Todo: $(bc)"
/usr/bin/grep -q '^ADD o 4 Backlog 2045$' "$T/board.calls" 2>/dev/null && ok "a child with no card of a spec in Todo is added in Backlog" || ko "#2045 not added Backlog: $(bc) $o"
[ "$(/usr/bin/grep -c 'SPEC-HELD-IN-TODO #2042' "$T/st/cycle.log" 2>/dev/null)" = 1 ] && ok "a spec in Todo names its held Backlog tickets once" || ko "#2042 hold: $(/usr/bin/grep '2042' "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q ' 2044$' "$T/board.calls" 2>/dev/null && ok "a child past Backlog keeps its column" || ko "#2044 moved: $(/usr/bin/grep ' 2044$' "$T/board.calls")"
! /usr/bin/grep -q ' 204[26]$' "$T/board.calls" 2>/dev/null && ok "the spec card keeps the owner's column" || ko "a spec moved: $(/usr/bin/grep -E ' 204[26]$' "$T/board.calls")"
! /usr/bin/grep -q ' 2047$' "$T/board.calls" 2>/dev/null && ok "a child of a spec in Backlog stays in Backlog" || ko "#2047 moved: $(/usr/bin/grep ' 2047$' "$T/board.calls")"
# A spec is never work (#665): a commit subject that names it is its children's, so no work rule writes its card, and the
# fold alone sets it -- including the correction of a card a work rule moved forward while its children were in flight.
/usr/bin/grep -q '^STATUS o 4 Todo 2050$' "$T/board.calls" 2>/dev/null && ok "a spec card a work rule moved to Code Complete goes back to the owner's column (ADR-0143, #1034)" || ko "#2050 not returned to Todo: $(bc) $o"
/usr/bin/grep -q 'BOARD-RECONCILE #2050: card -> Todo (spec folds over its sub-issues)' "$T/st/cycle.log" 2>/dev/null && ok "the return is named in cycle.log" || ko "#2050 return not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q ' 2053$' "$T/board.calls" 2>/dev/null && ok "a commit that names a spec never moves its card forward" || ko "#2053 moved: $(/usr/bin/grep ' 2053$' "$T/board.calls")"
! /usr/bin/grep -q ' 2054$' "$T/board.calls" 2>/dev/null && ok "the correction leaves its queued child's card alone" || ko "#2054 moved: $(/usr/bin/grep ' 2054$' "$T/board.calls")"
# A spec reads Todo while any of its tickets is in flight, and it never follows one into a work column (#1131,
# ADR-0142 rule 5, ADR-0143 rule 1). The lane reports the count in each column, so the spec card itself never moves there.
! /usr/bin/grep -q ' 2080$' "$T/board.calls" 2>/dev/null && ok "a spec in Todo with a ticket in progress stays in Todo" || ko "#2080 moved: $(/usr/bin/grep ' 2080$' "$T/board.calls")"
! /usr/bin/grep -q 'In Progress 2080' "$T/board.calls" 2>/dev/null && ok "a spec never follows a ticket into a work column" || ko "#2080 read as work: $(bc)"
! /usr/bin/grep -q ' 2090$' "$T/board.calls" 2>/dev/null && ok "a spec in Backlog with a ticket in Todo is never lifted to Todo by the pass (#1909)" || ko "#2090 lifted: $(bc) $o"
[ "$(/usr/bin/grep -c 'SPEC-HELD-IN-BACKLOG #2090' "$T/st/cycle.log" 2>/dev/null)" = 1 ] && ok "the hold is named once in cycle.log" || ko "#2090 hold not named once: $(/usr/bin/grep 2090 "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q ' 2092$' "$T/board.calls" 2>/dev/null && ok "a spec with no ticket in flight keeps the column the owner gave it" || ko "#2092 moved: $(/usr/bin/grep ' 2092$' "$T/board.calls")"
/usr/bin/grep -q '^STATUS o 4 Todo 2094$' "$T/board.calls" 2>/dev/null && ok "a spec in a work column returns to Todo" || ko "#2094 not returned to Todo: $(bc) $o"
# #1463: the REST items read omits #2096; the fallback GraphQL read carries its card in Code Complete, so the
# pass sees the card, moves it to its expected column, and never reports a write failure or an absence.
/usr/bin/grep -q '^STATUS o 4 In Progress 2096$' "$T/board.calls" 2>/dev/null && ok "a card the REST read omits but GraphQL carries is moved, not added" || ko "#2096 not moved: $(bc) $o"
! /usr/bin/grep -q 'ADD .* 2096$' "$T/board.calls" 2>/dev/null && ok "a card the fallback found is never added again" || ko "#2096 added: $(bc)"
! /usr/bin/grep -q 'BOARD-WRITE-FAILED #2096' "$T/st/cycle.log" 2>/dev/null && ok "a card the fallback found is never named a write failure" || ko "#2096 named a write failure: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q 'BOARD-RECONCILE #2096: card -> In Progress (run run-omitted-card is live)' "$T/st/cycle.log" 2>/dev/null && ok "the move of the found card is named in cycle.log" || ko "#2096 move not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
[ "$(/usr/bin/grep -c 'number=2096' "$T/gh.calls" 2>/dev/null)" = 1 ] && ok "the fallback reads one omitted issue once per pass, never once per rule that names it" || ko "fallback reads for #2096: $(/usr/bin/grep -c 'number=2096' "$T/gh.calls" 2>/dev/null)"
# An issue both reads omit is an absence, not a failure: the floor adds its card, and no failure is named.
/usr/bin/grep -q '^ADD o 4 Backlog 2097$' "$T/board.calls" 2>/dev/null && ok "an issue both reads omit keeps the floor's add" || ko "#2097 not added: $(bc) $o"
! /usr/bin/grep -q 'BOARD-WRITE-FAILED #2097' "$T/st/cycle.log" 2>/dev/null && ok "an issue both reads omit is never named a write failure" || ko "#2097 named a write failure: $(cat "$T/st/cycle.log" 2>/dev/null)"
# The omitted issues are 2013 2016 2017 2018 2019 2020 2021 2027 2028 2040 2045 2060 2074 2096 2097: one fallback
# call each, and none for an issue the REST read carries.
[ "$(/usr/bin/grep -c 'number=' "$T/gh.calls" 2>/dev/null)" = 15 ] && ok "one fallback call per omitted issue, and only for the omitted ones" || ko "fallback calls: $(/usr/bin/grep 'projectItems' "$T/gh.calls" 2>/dev/null)"
# The fold adds no GitHub call: the children come with the open-issue read, never from a sub-issues route.
! /usr/bin/grep -q 'sub_issues' "$T/gh.calls" 2>/dev/null && ok "no sub-issues route is called" || ko "a sub-issues call: $(/usr/bin/grep sub_issues "$T/gh.calls")"
[ "$(/usr/bin/grep -c 'subIssuesSummary' "$T/gh.calls")" = 1 ] && ok "the fold rides the one open-issue read" || ko "reads: $(/usr/bin/grep -c 'subIssuesSummary' "$T/gh.calls")"
# A run parent (the run-parent label) is the roll-up of a run's children in GitHub, never work (#500): the pass adds no card for
# one (#2040), removes the card one already has (#2041), and leaves the issue itself alone. Both writes are sent only when the
# board differs: #2040 has no card to remove, and #2041 has one, so it is removed once and never written a status.
! /usr/bin/grep -q ' 2040$' "$T/board.calls" 2>/dev/null && ok "a run parent with no card is not added, and nothing is removed" || ko "#2040 written: $(/usr/bin/grep ' 2040$' "$T/board.calls")"
/usr/bin/grep -q '^REMOVE o 4 2041$' "$T/board.calls" 2>/dev/null && ok "a run parent that has a card loses it" || ko "#2041 card not removed: $(bc) $o"
[ "$(/usr/bin/grep -c ' 2041$' "$T/board.calls")" = 1 ] && ok "the card of a run parent is removed in one write, never given a status" || ko "#2041 writes: $(/usr/bin/grep ' 2041$' "$T/board.calls")"
/usr/bin/grep -q 'BOARD-RECONCILE #2041: card removed (run-parent' "$T/st/cycle.log" 2>/dev/null && ok "the removal is named in cycle.log" || ko "#2041 removal not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q 'issues/204[01]\(/comments\| -X \| -f \)' "$T/gh.calls" 2>/dev/null && ok "the roll-up stays in GitHub: its issue is neither closed nor changed" || ko "a run parent issue was written: $(/usr/bin/grep 'issues/204[01]' "$T/gh.calls")"
# A parent that is not a spec carries Doing when one child is at Doing (#942), and the control whose children are all below
# Doing is not written. A run parent with a child at Doing still gets no card (#500). A non-spec parent whose children are all
# closed is neither closed nor written: only a spec closes, and its Closed column is the spec vocabulary (#942).
/usr/bin/grep -q '^STATUS o 4 Doing 2070$' "$T/board.calls" 2>/dev/null && ok "a non-spec parent with a child in Doing carries Doing (#942)" || ko "#2070 not Doing: $(bc) $o"
/usr/bin/grep -q 'BOARD-RECONCILE #2070: card -> Doing (parent folds over its sub-issues)' "$T/st/cycle.log" 2>/dev/null && ok "the non-spec lift is named in cycle.log without the word spec" || ko "#2070 lift not named: $(/usr/bin/grep '2070' "$T/st/cycle.log" 2>/dev/null)"
! /usr/bin/grep -q ' 2072$' "$T/board.calls" 2>/dev/null && ok "a non-spec parent whose children are all below Doing is not written" || ko "#2072 written: $(/usr/bin/grep ' 2072$' "$T/board.calls")"
! /usr/bin/grep -q ' 2074$' "$T/board.calls" 2>/dev/null && ok "a run parent with a child in Doing gets no card" || ko "#2074 written: $(/usr/bin/grep ' 2074$' "$T/board.calls")"
! /usr/bin/grep -q ' 2076$' "$T/board.calls" 2>/dev/null && ok "a non-spec parent whose children are all closed is not written" || ko "#2076 written: $(/usr/bin/grep ' 2076$' "$T/board.calls")"
! /usr/bin/grep -q 'issues/2076\(/comments\| -X PATCH\| -f state\)' "$T/gh.calls" 2>/dev/null && ok "a non-spec parent is never closed" || ko "#2076 closed: $(/usr/bin/grep 'issues/2076' "$T/gh.calls")"
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
