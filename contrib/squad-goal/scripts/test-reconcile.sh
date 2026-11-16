#!/bin/bash
# Regression test for reconcile.py's scope: RECONCILE_MIN floors open issues only; a closed issue of any number reaches Done (stub gh and board.sh, no network).
# And for its watermark (#246): a recomputed column lower in the shipped flow than the one the card carries is refused and reported
# by name, never written; a card behind the recomputed column moves forward as it always did; a revert keeps the column and adds the tag.
# usage: bash test-reconcile.sh [path/to/reconcile.py]
unset SG_GH_PASS   # the reconciler names its own reads; a pass name the enclosing loop exported is not this suite's
src=${1:-$(dirname "$0")/reconcile.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/bin" "$T/st"; test -f "$(dirname "$src")/board_columns.py" || exit 1; cp "$src" "$T/s/reconcile.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/issue_state.py" "$T/s/"; cp "$(dirname "$src")/board-definition.json" "$T/s/"   # the shipped axis the flow order reads lives in the pinned definition beside the module
cp "$(dirname "$src")/ghread.py" "$(dirname "$src")/ghthrottle.py" "$(dirname "$src")/board_archive.py" "$T/s/"   # the door reader, the quota floor reader and the archive pass reconcile imports (#847, #1931)
cp "$(dirname "$src")/issue_ledger.py" "$T/s/"   # the one ledger a card write is recorded in before it lands (#1841)
printf '#!/bin/bash\necho "$*" >> "%s/moves"\n' "$T" > "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
echo "$*" >> "TMPDIR/ghcalls"   # every gh call of a pass, for the call count (#853)
case "$*" in
  "api users/x") echo '{"type":"User"}';;
  *"/fields"*) echo '[{"id":7,"name":"Status"}]';;
  *"/items"*) printf '1090\tTodo\thttps://api.github.com/repos/x/y\n1092\tTesting\thttps://api.github.com/repos/x/y\n1095\tTodo\thttps://api.github.com/repos/x/y\n1110\tTesting\thttps://api.github.com/repos/x/y\n1120\tTodo\thttps://api.github.com/repos/x/y\n1130\tBlocked\thttps://api.github.com/repos/x/y\n1140\tDone\thttps://api.github.com/repos/x/y\n1150\tTodo\thttps://api.github.com/repos/x/y\n1160\tTodo\thttps://api.github.com/repos/x/y\n1170\tClosed\thttps://api.github.com/repos/x/y\n1180\tTodo\thttps://api.github.com/repos/x/y\n1185\tTodo\thttps://api.github.com/repos/x/y\n1190\tAbandoned\thttps://api.github.com/repos/x/y\n';;   # the one Status read of the seam: number, state, repository url (#1932: the three close-reason cases)
  *"--method POST"*"/labels"*) echo "$*" >> "TMPDIR/tags"; echo '[]';;   # the tag a refused move back writes: the tag plus a new issue, never a card moved back
  *"--method DELETE"*"/labels"*) echo "$*" >> "TMPDIR/deletes"; echo '{}';;   # a holder-label clear: the reconciler sends none (#1678)
  *"/labels"*) echo "$*" >> "TMPDIR/tagreads"; if [ -n "$HAS_TAG" ]; then echo '[{"name":"reverted"}]'; else echo '[]'; fi;;   # the labels the issue carries now
  *"issue(number:"*) b=''; sep=''
    for n in $(echo "$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+'); do r=$("$0" api "repos/x/y/issues/$n" 2>/dev/null)
      b="$b$sep\"i$n\":$(jq -c --argjson n "$n" --argjson r "$r" '{number:$n,title:"t",body:($r.body//""),state:(($r.state//"open")|ascii_upcase),stateReason:(if $r.state_reason then ($r.state_reason|ascii_upcase) else null end),createdAt:null,labels:{nodes:($r.labels//[])},blockedBy:{nodes:[]},subIssuesSummary:{total:0,completed:0},subIssues:{nodes:[]},comments:{nodes:[]}}' <<<"{}")"; sep=','; done
    echo "{\"data\":{\"repository\":{$b}}}" ;;   # the GraphQL fact: one alias per issue asked, whether the route named one issue or a numbers= list (#1648)   # the single-issue read is the GraphQL fact: its state, body and labels are the REST answer below
  *"issues(states: OPEN"*) if [ -n "$OPEN_GRAPHQL" ]; then cat "$OPEN_GRAPHQL"; else echo '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":1093,"title":"t","body":"","state":"OPEN"},{"number":1095,"title":"t","body":"","state":"OPEN"},{"number":1120,"title":"t","body":"## Blocked by\n\n- #1093\n","state":"OPEN"},{"number":1140,"title":"t","body":"","state":"OPEN"},{"number":1150,"title":"t","body":"","state":"OPEN"},{"number":1160,"title":"t","body":"","state":"OPEN"}]}}}}'; fi;;   # the open-issue aggregate, one GraphQL request (#853); a case overrides it to carry labels (#1678)
  *"issues/1090") if [ -n "$ISSUE_1090" ]; then cat "$ISSUE_1090"; else echo '{"state":"closed","body":""}'; fi;;
  *"issues/1092") echo '{"state":"closed","body":""}';;
  *"issues/1093") echo '{"state":"open","body":""}';;
  *"issues/1095") echo '{"state":"open","body":""}';;
  *"issues/1110") echo '{"state":"closed","body":""}';;
  *"issues/1120") echo '{"state":"open","body":"## Blocked by\n\n- #1093\n"}';;
  *"issues/1130") echo '{"state":"closed","body":""}';;
  *"issues/1140") echo '{"state":"open","body":""}';;
  *"pulls?state=open"*) echo "$*" >> "TMPDIR/pullreads"; echo '[{"number":9,"title":"x","body":"Refs #1160"}]';;   # an open PR names #1160: it must move no card
  *"repos/x/y/branches"*) echo '[{"name":"fix/1150-x","commit":{"sha":"sha1150"}},{"name":"gui/1170-x","commit":{"sha":"sha1150"}},{"name":"main","commit":{"sha":"shamain"}}]';;   # a work branch names #1150 and is ahead of dev; #1170 shares its tip
  *"compare/dev...sha1150"*) echo '{"status":"ahead"}';;
  *"issues/1150") echo '{"state":"open","body":""}';;
  *"issues/1160") echo '{"state":"open","body":""}';;
  *"issues/1170") echo '{"state":"closed","body":""}';;
  # The close reason a terminal card follows (#1932): #1180 was closed as not planned, so it was never delivered and its
  # card reads Abandoned; #1185 was closed as completed, so its card reads Closed; #1190 is the abandoned card already.
  *"issues/1180") echo '{"state":"closed","state_reason":"not_planned","body":""}';;
  *"issues/1185") echo '{"state":"closed","state_reason":"completed","body":""}';;
  *"issues/1190") echo '{"state":"closed","state_reason":"not_planned","body":""}';;
  *"issues?state=open&labels=bug"*) echo '[]';;
  *"issues?state=open"*) echo "${OPEN_ISSUES:-[]}";;
esac
J
unset SG_BOARD_VOCAB; sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH" SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_STATE="$T/st" RECONCILE_MIN=1102
r=$(RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1); m=$(cat "$T/moves" 2>/dev/null)
case "$m" in *1090*) ok "a closed issue #1090 below RECONCILE_MIN moves to Done";; *) ko "a closed issue #1090 not moved: '$r'";; esac
case "$m" in *1092*) ok "a closed issue #1092 below RECONCILE_MIN moves to Done";; *) ko "a closed issue #1092 not moved: '$r'";; esac
case "$m" in *1110*) ok "a closed issue #1110 above RECONCILE_MIN still moves to Done";; *) ko "a closed issue #1110 not moved: '$r'";; esac
case "$r" in *"#1095"*) ko "open #1095 below RECONCILE_MIN was derived: '$r'";; *) ok "open issue below RECONCILE_MIN stays out of scope";; esac
# A card never moves backwards (#246): the card's own column is the watermark, and the shipped flow order says which way is back.
# (1) #1120 sits in Todo, its recomputed column is Blocked, one step back in the flow: it is not moved, and the attempt is named.
case "$m" in *1120*) ko "the backward move of #1120 was written: '$m'";; *) ok "a card one step ahead of the recomputed column is not moved";; esac
case "$r" in *"BACKWARD #1120: Todo -> Blocked"*) ok "the refused move names the card and both columns";; *) ko "the backward attempt of #1120 is not reported by name: '$r'";; esac
# (2) #1130 sits in Blocked and the issue closed: Done is ahead of the card, so it moves forward as before.
case "$m" in *"Done 1130"*) ok "a card behind the recomputed column moves forward";; *) ko "closed #1130 did not move forward: '$m'";; esac
case "$r" in *"BACKWARD #1130"*) ko "a forward move was reported as backwards: '$r'";; *) ok "a forward move is never reported as backwards";; esac
# (3) #1140 reached Done and the issue is open again, so the merge was taken back: the card keeps its column and carries the tag.
case "$m" in *1140*) ko "a revert moved #1140 back: '$m'";; *) ok "a revert keeps the column";; esac
case "$r" in *"BACKWARD #1140: Done -> Todo"*) ok "the revert is reported by name";; *) ko "the revert of #1140 is not reported by name: '$r'";; esac
case "$(cat "$T/tags" 2>/dev/null)" in *"issues/1140/labels"*reverted*|*reverted*"issues/1140/labels"*) ok "a revert adds the tag";; *) ko "a revert added no tag: '$(cat "$T/tags" 2>/dev/null)'";; esac
case "$(cat "$T/tags" 2>/dev/null)" in *"issues/1120"*) ko "a plain backward attempt tagged the card: '$(cat "$T/tags" 2>/dev/null)'";; *) ok "only a revert carries the tag";; esac
# (4) a reconcile run on an issue that already carries the tag reads the labels and sends no label write (#328, criterion c).
rm -f "$T/tags" "$T/tagreads"; r=$(HAS_TAG=1 RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1)
case "$(cat "$T/tagreads" 2>/dev/null)" in *"issues/1140/labels"*) ok "the labels are read before the tag is added";; *) ko "the labels of #1140 were not read: '$(cat "$T/tagreads" 2>/dev/null)'";; esac
[ ! -s "$T/tags" ] && ok "an issue that already has the tag gets no label write" || ko "a label write was sent for a tagged issue: '$(cat "$T/tags")'"
rm -f "$T/tags"; r=$(RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1)
[ "$(grep -c 'issues/1140/labels' "$T/tags" 2>/dev/null)" = 1 ] && ok "an issue without the tag still gets it" || ko "the tag was not added to an untagged issue: '$(cat "$T/tags" 2>/dev/null)'"
# The branch is the fact (#358): an open work branch ahead of dev moves its card to Code Complete; an open pull request moves none, and no pull request is read.
case "$m" in *"Code Complete 1150"*) ok "an open work branch ahead of dev moves its card to Code Complete";; *) ko "#1150 not moved on its branch: '$m' '$r'";; esac
case "$m" in *1160*) ko "an open pull request moved #1160: '$m'";; *) ok "an open pull request moves no card";; esac
[ ! -s "$T/pullreads" ] && ok "no pull request is read" || ko "a pull request route was read: $(cat "$T/pullreads")"
# The vocabulary the loop writes (ADR-0141, #454): the Squad Board carries the shipped names, and SG_BOARD_VOCAB=shipped tells
# every writer so. This pass wrote the internal names instead, so the carrier refused each write ("no Status option 'Done'") and
# the pass swallowed it -- the MOVE line it filed was a write that never landed, week after week.
rm -f "$T/moves"; r=$(SG_BOARD_VOCAB=shipped RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1); m=$(cat "$T/moves" 2>/dev/null)
case "$m" in *"Done 1090"*) ko "the shipped board was written the internal name Done: '$m'";; *) ok "no internal name reaches the writer under the shipped vocabulary";; esac
case "$m" in *"Closed 1090"*) ok "a closed issue moves to the shipped Closed";; *) ko "no shipped Closed for #1090 in the write: '$m' '$r'";; esac
# The writer takes four fields in one order -- `<owner> <project> <status> <n>` -- and the stub here echoes them all, so a
# caller that drops one is caught here rather than by the carrier: the pass that shipped #454 called `board.sh Closed 1090`,
# board.py read the status as the owner, and every write of the pass failed with an IndexError it named as a refusal (#457).
# The stub's own argv IS the contract, so the whole line is compared and not just the status that happens to sit in it.
case "$m" in *"x 4 Closed 1090"*) ok "the writer is called with owner, project, status and number, in that order";;
  *) ko "the write does not carry the writer's four fields: '$m'";; esac
case "$m" in *"x 4 Code Complete 1150"*) ok "a forward move carries the same four fields";;
  *) ko "a forward move lost a field of the writer's contract: '$m'";; esac
# A card already in the column its facts earn draws no line and no writer call (#458): #1170 is closed and sits in Closed, so the
# shipped pass reports nothing for it, with or without apply; #1090 is closed and sits in Todo, so it keeps its line and its call.
# One pass over one stub board holds both cases, and the writer's argv is compared whole.
dry=$(SG_BOARD_VOCAB=shipped python3 "$T/s/reconcile.py" 2>&1)
case "$dry" in *"#1170"*) ko "a dry pass filed a line for #1170, already in Closed: '$dry'";; *) ok "a card in its earned column draws no line in a dry pass";; esac
case "$dry" in *"WOULD MOVE #1090 Todo -> Closed (issue closed)"*) ok "a card in another column draws its line in a dry pass";; *) ko "no WOULD MOVE line for #1090: '$dry'";; esac
rm -f "$T/moves"; ap=$(SG_BOARD_VOCAB=shipped RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1); m=$(cat "$T/moves" 2>/dev/null)
case "$ap" in *"#1170"*) ko "an apply pass filed a line for #1170, already in Closed: '$ap'";; *) ok "a card in its earned column draws no line under apply";; esac
case "$m" in *1170*) ko "the no-op card #1170 reached the writer: '$m'";; *) ok "a card in its earned column sends no writer call";; esac
case "$ap" in *"MOVE #1090 Todo -> Closed (issue closed)"*) ok "a card in another column draws its line under apply";; *) ko "no MOVE line for #1090: '$ap'";; esac
[ "$(printf '%s\n' "$m" | /usr/bin/grep -cx 'x 4 Closed 1090')" = 1 ] && ok "the writer's recorded argv for #1090 is the whole contract line" || ko "the writer argv for #1090 differs: '$m'"
# A terminal column follows the close reason (#1932, spec #132): an issue closed as not planned was never delivered, so its
# card reads Abandoned and never Closed; a completed close reads Closed; and a card already in Abandoned for such an issue
# draws no line and no write, so a later pass can never walk it to Closed.
case "$m" in *"x 4 Abandoned 1180"*) ok "an issue closed as not planned moves its card to Abandoned";;
  *) ko "the not-planned close of #1180 did not move to Abandoned: '$m'";; esac
case "$ap" in *"MOVE #1180 Todo -> Abandoned (issue closed as not_planned)"*) ok "the move names the not-planned close";;
  *) ko "the move of #1180 does not name its close reason: '$ap'";; esac
case "$m" in *"x 4 Closed 1185"*) ok "an issue closed as completed moves its card to Closed";;
  *) ko "the completed close of #1185 did not move to Closed: '$m'";; esac
case "$ap" in *"MOVE #1185 Todo -> Closed (issue closed)"*) ok "the completed move reads as a plain closed issue";;
  *) ko "the move of #1185 does not read as a completed close: '$ap'";; esac
case "$ap" in *"#1190"*) ko "a card already in Abandoned drew a line: '$ap'";; *) ok "a card already in Abandoned draws no line";; esac
case "$m" in *1190*) ko "the Abandoned card #1190 reached the writer: '$m'";; *) ok "a card already in Abandoned sends no writer call";; esac
case "$ap" in *"moves: 7"*) ok "the final count is the moves made";; *) ko "the final count is not the seven moves made: '$ap'";; esac
# A write the carrier refused is named on the pass's stream and ends the pass non-zero, never swallowed.
printf '#!/bin/bash\necho "no Status option $3" >&2; exit 1\n' > "$T/s/board.sh"; chmod +x "$T/s/board.sh"
r=$(RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1); rc=$?
case "$r" in *"WRITE REFUSED #1090"*"no Status option"*) ok "a refused write is named with the carrier's own text";; *) ko "a refused write was swallowed: '$r'";; esac
[ "$rc" != 0 ] && ok "a pass whose write was refused ends non-zero" || ko "the refused write still ended the pass 0"
# Every card write is a ledger event (#1841): the reconciler records the column it writes before it calls the writer, and
# the writer's own proof ends the prediction. A write the carrier refused is unproven, so its event stays pending, and the
# hub names it as a defect once its bound passes.
fact=$(SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" read "x/y#1090" board 2>/dev/null)
case "$fact" in *'"by": "reconcile"'*'"column": "Done"'*'"state": "pending"'*) ok "a refused card write keeps its pending ledger event";; *) ko "a refused card write has no pending fact: '$fact'";; esac
printf '#!/bin/bash\necho "$*" >> "%s/moves"\n' "$T" > "$T/s/board.sh"; chmod +x "$T/s/board.sh"
rm -rf "$T/st/state"; r=$(RECONCILE=apply python3 "$T/s/reconcile.py" 2>&1)
fact=$(SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" read "x/y#1090" board 2>/dev/null)
case "$fact" in *'"by": "reconcile"'*'"column": "Done"'*'"state": "written"'*) ok "a card write that landed is a ledger fact its writer proved";; *) ko "a landed card write is no written fact: '$fact'";; esac
[ -z "$(SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" pending x/y 2>/dev/null)" ] && ok "a proven card write leaves no pending event" || ko "a proven card write stayed pending"
# The call count of one dry pass (#853): the open issues come from one aggregate read, an open card the aggregate covers is never
# read one by one (below RECONCILE_MIN or not), and two cards on one branch tip cost one compare. A closed card is not in the
# open aggregate, so its state is still read one card at a time.
rm -f "$T/ghcalls"; SG_BOARD_VOCAB=shipped python3 "$T/s/reconcile.py" >/dev/null 2>&1; calls=$(cat "$T/ghcalls" 2>/dev/null)
echo "info gh calls in one pass: $(printf '%s\n' "$calls" | /usr/bin/grep -c '^api ')"
[ "$(printf '%s\n' "$calls" | /usr/bin/grep -c 'issues(states: OPEN')" = 1 ] && ok "one pass reads the open-issue aggregate once" || ko "the open-issue aggregate was not read once: '$calls'"
cov=$(printf '%s\n' "$calls" | /usr/bin/grep -E 'issue\(number: (1093|1095|1120|1140|1150|1160)[ )]|repos/x/y/issues/(1093|1095|1120|1140|1150|1160)$')
[ -z "$cov" ] && ok "no open card the aggregate covers is read one by one, below RECONCILE_MIN or not" || ko "an open card was read one by one: '$cov'"
[ "$(printf '%s\n' "$calls" | /usr/bin/grep -c 'compare/dev\.\.\.sha1150')" = 1 ] && ok "two cards on one branch tip cost one compare" || ko "the shared branch tip was compared $(printf '%s\n' "$calls" | /usr/bin/grep -c 'compare/dev\.\.\.sha1150') times"
# The cards the open list lacks are read in ONE batched request, never one per card (#1648): a closed card cost its own
# read on every pass, and seven hours of that walk emptied the GraphQL quota while the core quota was untouched.
rm -f "$T/ghcalls" "$T/st/api.log" "$T/st/gh-cooldown.json"; SG_BOARD_VOCAB=shipped python3 "$T/s/reconcile.py" >/dev/null 2>&1; log=$(cat "$T/st/api.log" 2>/dev/null)
one=$(printf '%s\n' "$log" | /usr/bin/grep -cE ' issues/(1090|1092|1110|1130|1170)( |$)')
[ "$one" = 0 ] && ok "no card the open list lacks is read one by one" || ko "a closed card was read one by one: $(printf '%s\n' "$log" | /usr/bin/grep -E ' issues/(1090|1092|1110|1130|1170)( |$)' | head -2)"
batch=$(printf '%s\n' "$log" | /usr/bin/grep -c 'issues?numbers=')
[ "$batch" = 1 ] && ok "the cards the open list lacks are asked in one batched read" || ko "the batched read ran $batch times"
nums=$(printf '%s\n' "$log" | /usr/bin/grep -o 'numbers=[0-9,]*' | head -1)
for n in 1090 1092 1110 1130 1170; do
  case "$nums" in *"$n"*) true;; *) ko "the batched read does not name #$n: '$nums'";; esac
done
printf '%s\n' "$nums" | /usr/bin/grep -q '1090.*1170' && ok "one list names every open-list miss" || ko "the batched route does not carry the numbered list: '$nums'"
# The spend is attributed (#1647's sibling): a pass that names itself in the call log can be read in the log that spends it.
if [ -s "$T/st/api.log" ]; then
  u=$(/usr/bin/grep -c ' unattributed$' "$T/st/api.log"); a=$(/usr/bin/grep -c ' reconcile$' "$T/st/api.log")
  [ "$u" = 0 ] && [ "$a" -gt 0 ] && ok "every read of the pass is logged under its own name" || ko "the pass spent $u unattributed and $a attributed reads"
else ko "the pass wrote no call log line"; fi
# A cooldown that still holds ends the pass before its first request (#1648): retrying into a refusal renews the limit.
printf '{"until": %s, "at": 0, "message": "API rate limit already exceeded", "pass": "other"}\n' "$(( $(date +%s) + 600 ))" > "$T/st/gh-cooldown.json"
rm -f "$T/ghcalls"; r=$(SG_BOARD_VOCAB=shipped python3 "$T/s/reconcile.py" 2>&1); rc=$?
if [ -s "$T/ghcalls" ] && /usr/bin/grep -q 'repos/x/y' "$T/ghcalls"; then ko "a cooling pass still read the repository: $(/usr/bin/grep 'repos/x/y' "$T/ghcalls" | head -2)"; else ok "a pass under a cooldown sends no repository request"; fi
case "$r" in *cooldown*) ok "the cooling pass names the cooldown" ;; *) ko "the cooling pass did not name it: '$r'";; esac
[ "$rc" = 0 ] && ok "a cooling pass ends clean" || ko "a cooling pass ended $rc"
# The two faces of a hold (#1678): the hold file is the janitor's read, the holder label is the pass's read. The hold
# module writes both in one act; the reconciler reports a disagreement as a defect and rewrites neither face.
rm -f "$T/st/gh-cooldown.json"   # the case above leaves a cooldown that would skip this pass before its first read
mkdir -p "$T/st/auto"
printf 'by manual\nsince 2026-01-01T00:00:00Z\nuntil 2026-02-01T00:00:00Z\n' > "$T/st/auto/hold-1090"
rm -f "$T/deletes" "$T/issue-1090"
r=$(python3 "$T/s/reconcile.py" 2>&1)
case "$r" in *"HOLD DISAGREEMENT #1090"*"ready-for-human"*) ok "a hold file with no holder label is reported as a defect";; *) ko "the missing label of #1090 is not reported: '$r'";; esac
[ ! -s "$T/deletes" ] && ok "the reconciler removes no holder label on a disagreement" || ko "a label clear was sent: '$(cat "$T/deletes")'"
printf '{"state":"open","body":"","labels":[{"name":"ready-for-human"}]}' > "$T/issue-1090"
r=$(ISSUE_1090=$T/issue-1090 python3 "$T/s/reconcile.py" 2>&1)
case "$r" in *"HOLD DISAGREEMENT #1090"*) ko "an agreeing file and label were reported: '$r'";; *) ok "a file and its label that agree are no defect";; esac
rm -f "$T/st/auto/hold-1090" "$T/issue-1090"
printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":1095,"title":"t","body":"","state":"OPEN","labels":{"nodes":[{"name":"ready-for-HAL9K"}]}}]}}}}' > "$T/open-graphql"
r=$(OPEN_GRAPHQL=$T/open-graphql python3 "$T/s/reconcile.py" 2>&1)
case "$r" in *"HOLD DISAGREEMENT #1095"*"ready-for-HAL9K"*) ok "a holder label with no hold file is reported as a defect";; *) ko "the holder label of #1095 with no file is not reported: '$r'";; esac
[ ! -s "$T/deletes" ] && ok "the reconciler leaves a label without its file in place" || ko "a label clear was sent: '$(cat "$T/deletes")'"
printf 'by hal9k\nsince 2026-01-01T00:00:00Z\nuntil 2026-02-01T00:00:00Z\n' > "$T/st/auto/hold-1090"
printf '{"state":"open","body":"","labels":[{"name":"ready-for-HAL9K"}]}' > "$T/issue-1090"
r=$(ISSUE_1090=$T/issue-1090 OPEN_GRAPHQL=$T/open-graphql python3 "$T/s/reconcile.py" 2>&1)
case "$r" in *"HOLD DISAGREEMENT #1090"*) ko "an agreeing hal9k file and label were reported: '$r'";; *) ok "a hal9k file and its label that agree are no defect";; esac
rm -f "$T/st/auto/hold-1090" "$T/issue-1090" "$T/open-graphql"
[ $fail = 0 ] && echo "PASS" || echo "TDD RED"
rm -rf "$T"; exit $fail
