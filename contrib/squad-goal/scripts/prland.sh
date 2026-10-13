#!/bin/bash
# usage: prland.sh   (watch.sh, every cycle, under one single.sh slot; PRLAND_DRY=1 prints the head it would merge)
#
# A pull request the loop opened is closed by the loop, on a failure set equal to a NAMED BASELINE (#83). The run's own
# land node closes its pull request while its gate is green; a head whose check is red for a constant reason -- tests
# that need an environment the CI runner does not have -- never reaches green, so its issue sits in Code Complete until a
# person merges it by hand. This pass is that person's step, with the one rule that keeps it safe: it merges on a failure
# set EQUAL to the baseline, never on checks green.
#
# The baseline is the file beside this script (prland-baseline.txt; SG_PR_BASELINE names another): one failing test a
# line, in the pipeline's own `FAILED <nodeid>` spelling, `#` starting a comment. A head whose failing tests are exactly
# that set is merged. A head that names a failure the baseline does not hold is REFUSED, and the failure is named -- that
# is the point of the named set: a red this pass cannot account for is a red a person has to read.
#
# Eligible: an open, non-draft pull request that names its issue (`Refs #N`, the spelling the loop's own request writes),
# whose head branch GitHub reports mergeable (never `dirty` or `unknown`) and which no live run names. Every check on the
# head is read, then the log of each failed check, whose `FAILED` lines are the failure set. A failed check whose log
# names no failing test counts as itself, so a failure this pass cannot attribute is refused like any other.
#
# The merge is the REST form of `gh pr merge --squash --delete-branch`: the pull's merge endpoint with
# merge_method=squash, then the head ref deleted, because that endpoint has no field that deletes a branch. Then one
# comment on the issue naming the merge, then the close, in that order. Every reach goes through the loop's one door to
# GitHub (ghcall.sh, #8, ADR-0277) and the throttled reader behind it (ghthrottle.py): this script keeps no call site of
# its own, and gh-direct-calls.txt counts it at 0.
#
# One PRLAND WAIT or PRLAND REFUSE line per head, never repeated: the head sha is in the marker's name, so a moved head
# speaks again and a head that did not move does not fill the log.
#
# exit 0: the pass ran, whatever it decided. A read that did not answer stops the pass and says so -- never with a merge.
. "$(dirname "$0")/env.sh"; here="$(cd "$(dirname "$0")" && pwd)"; A="$SG_STATE/auto"; mkdir -p "$A"
export SG_GH_PASS=prland   # the pass that spends the calls below, named in the call log (ADR-0277)
SG_GHREAD="python3 $here/ghthrottle.py"; . "$here/ghcall.sh"
[ -e "$A/off" ] && exit 0
: "${SG_PR_BASELINE:=$here/prland-baseline.txt}"
: "${SG_PR_LIVE_MINUTES:=20}"
now=$(date +%s)
# the sorted, unique names a set holds; an empty set is no line at all
bsort() { printf '%s\n' "$1" | grep . | sort -u; }
# say <marker> <line…>: the line once per marker. The marker is the head, so a WAIT or a REFUSE is never repeated.
say() { local m=$1; shift; [ -e "$m" ] && return 0; : > "$m"; echo "$*"; }
base=$(sed 's/#.*//' "$SG_PR_BASELINE" 2>/dev/null | tr -d ' \t' | grep . | sort -u)
[ -n "$base" ] || { echo "PRLAND NO-BASELINE: $SG_PR_BASELINE names no failing test, so no head equals it and nothing is merged this pass"; exit 0; }
# The branches a live run is working on: a child runs from its own run ws, so the run's request is what names its branch.
# A head a live run owns is not this pass's to merge -- the run's own land node closes it when its gate is green.
livebrs=" $(for r in $(ls -t "$SG_CHECKOUT/runs" 2>/dev/null | head -60); do
  f="$SG_CHECKOUT/runs/$r/record.jsonl"; [ -f "$f" ] || continue
  [ $(( now - $(stat -f %m "$f") )) -lt $(( SG_PR_LIVE_MINUTES * 60 )) ] || continue
  grep -oE '(gui|fix)/[0-9][0-9A-Za-z._/-]*' "$SG_CHECKOUT/runs/$r/request.txt" 2>/dev/null
done | sort -u | tr '\n' ' ') "
# The open pull requests are ONE read for the whole set, and a list that did not answer stops the pass: the merge is the
# one write here that cannot be taken back, and a refusal is not an empty list (#1893).
if ! prs=$(gh_api "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[]|[.number,.draft,.head.ref,.head.sha,((.title//"")+" "+(.body//""))]|map(tostring)|join("\u001f")'); then
  echo "PRLAND NO-PR-LIST: the open-PR list is not readable, so nothing is merged this pass: $prs"; exit 0; fi
while IFS=$'\x1f' read -r pr draft ref sha txt; do
  [ -n "$pr" ] || continue
  [ "$draft" = true ] && continue
  case "$livebrs" in *" $ref "*) continue;; esac
  n=$(printf '%s' "$txt" | grep -oE 'Refs #[0-9]+' | grep -oE '[0-9]+' | head -1)
  [ -n "$n" ] || continue   # a pull request that names no issue is not one the loop opened
  # The pull's own facts are a live read (it decides a write), never the snapshot (#6): the head may have moved since
  # the list was read, and a merge on a stale head is a merge onto a tree nobody gated.
  st=$(gh_api "repos/$SG_REPO/pulls/$pr" -q '.head.sha+" "+(.mergeable_state//"unknown")+" "+(.merged|tostring)+" "+(.draft|tostring)') || {
    say "$A/prland-wait-$pr-${sha:0:9}" "PRLAND WAIT #$pr (${sha:0:9}): the pull request is not readable, so nothing is merged this pass: $st"; continue; }
  read -r head ms merged dr <<<"$st"
  wm="$A/prland-wait-$pr-${head:0:9}"; rm="$A/prland-refuse-$pr-${head:0:9}"
  case "$merged$dr" in *true*) continue;; esac   # already merged (a race with this pass), or a draft
  case "$ms" in clean|unstable|blocked|has_hooks|behind) ;; *)
    say "$wm" "PRLAND WAIT #$pr (${head:0:9}): the head branch $ref is not clean (GitHub reports mergeable_state $ms); nothing is merged, and the next pass retries"; continue;; esac
  cr=$(gh_api "repos/$SG_REPO/commits/$head/check-runs?per_page=100" -q '[.check_runs[]|select((.name//"")!="CodeRabbit" and (.name//"")!="")|{n:.name,s:.status,c:(.conclusion//""),u:(.details_url//"")}]') || {
    say "$wm" "PRLAND WAIT #$pr (${head:0:9}): the head's checks are not readable, so nothing is merged this pass: $cr"; continue; }
  [ "$(printf '%s' "$cr" | jq 'length' 2>/dev/null || echo 0)" != 0 ] || {
    say "$wm" "PRLAND WAIT #$pr (${head:0:9}): the head carries no check run, so there is no failure set to weigh against $SG_PR_BASELINE"; continue; }
  [ "$(printf '%s' "$cr" | jq '[.[]|select(.s!="completed")]|length' 2>/dev/null || echo 1)" = 0 ] || {
    say "$wm" "PRLAND WAIT #$pr (${head:0:9}): a check on the head is still running; a merge waits for every check to finish"; continue; }
  # The failure set: every failed check's own log, through the door's read of a representation that is not JSON. A check
  # whose log names no failing test is counted as itself, so a failure nothing can attribute is refused like any other.
  fails=""; unatt=""
  while IFS=$'\t' read -r cn cu; do
    [ -n "$cn" ] || continue
    jid=${cu##*/job/}
    case "$jid" in ''|*[!0-9]*|"$cu") unatt="$unatt$cn"$'\n'; continue;; esac
    lg=$(gh_get "repos/$SG_REPO/actions/jobs/$jid/logs") || { unatt="$unatt$cn"$'\n'; continue; }
    names=$(printf '%s\n' "$lg" | grep -oE 'FAILED [^ ]+' | sed 's/^FAILED //')
    [ -n "$names" ] || { unatt="$unatt$cn"$'\n'; continue; }
    fails="$fails$names"$'\n'
  done <<< "$(printf '%s' "$cr" | jq -r '.[]|select(.c=="failure" or .c=="timed_out" or .c=="cancelled")|.n+"\t"+.u')"
  fails=$(bsort "$fails$unatt")
  extra=$(comm -23 <(bsort "$fails") <(bsort "$base"))
  if [ -n "$extra" ]; then
    say "$rm" "PRLAND REFUSE #$pr (${head:0:9}): $SG_REPO#$pr failed on $(printf '%s' "$extra" | tr '\n' ' '), which $SG_PR_BASELINE does not hold. Nothing is merged: a red this pass cannot account for is one a person has to read."
    continue; fi
  if [ "$fails" != "$base" ]; then
    if [ -z "$fails" ]; then say "$wm" "PRLAND WAIT #$pr (${head:0:9}): no check failed on the head, and this pass merges on the failure set $SG_PR_BASELINE names, never on checks green"
    else say "$wm" "PRLAND WAIT #$pr (${head:0:9}): the head does not fail $(comm -13 <(bsort "$fails") <(bsort "$base") | tr '\n' ' '), so its failure set is not the baseline; a head is merged on the set $SG_PR_BASELINE names"; fi
    continue; fi
  [ -n "${PRLAND_DRY:-}" ] && { echo "PRLAND would merge $SG_REPO#$pr (${head:0:9}) for issue #$n on the failure set $SG_PR_BASELINE names"; continue; }
  if ! m=$(gh_send -X PUT "repos/$SG_REPO/pulls/$pr/merge" -f sha="$head" -f merge_method=squash -q .sha); then
    say "$wm" "PRLAND WAIT #$pr (${head:0:9}): GitHub did not merge the pull request on the baseline; the next pass retries: $m"; continue; fi
  rg=$(gh_send -X DELETE "repos/$SG_REPO/git/refs/heads/$ref") \
    || echo "PRLAND WARN #$pr (${head:0:9}): the merge landed but the head branch $ref was not deleted: $rg"
  cmt=$(printf 'Closed by the loop, on the failure set the named baseline holds.\n\n- Pull request #%s merged as %s (squash, head branch `%s` deleted) in %s.\n- Every failing test on %s is in `%s`: the %s test(s) the CI runner cannot run without a MesenAI checkout beside the workspace (#36).\n- The check stays red on those tests until the runner has what they need, so this pull request would not have merged on green.\n' \
    "$pr" "${m:0:9}" "$ref" "$SG_REPO" "${head:0:9}" "$(basename "$SG_PR_BASELINE")" "$(printf '%s' "$base" | grep -c .)")
  o=$(gh_send -X POST "repos/$SG_REPO/issues/$n/comments" -F body=@- <<EOT
$cmt
EOT
) || echo "PRLAND WARN #$pr (${head:0:9}): the merge landed but no comment reached issue #$n: $o"
  gh_send -X PATCH "repos/$SG_REPO/issues/$n" -f state=closed -f state_reason=completed >/dev/null \
    || echo "PRLAND WARN #$pr (${head:0:9}): the merge landed but issue #$n was not closed"
  echo "PRLAND MERGED #$pr issue #$n: $SG_REPO#$pr ($ref ${head:0:9}) merged as ${m:0:9} on the baseline, head branch deleted, comment and close on #$n"
done <<<"$prs"
