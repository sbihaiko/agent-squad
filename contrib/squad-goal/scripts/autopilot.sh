#!/bin/bash
# One pass per watch cycle. Open PRs that name an issue ("Refs #N") are reviewed, fixed and merged:
# a PR with no Refs #N is repaired when the issue can be derived — the branch (`gui/<N>`, fork work) or the run
# that produced the branch (its record names the branch and the issue) — by appending `Refs #N` to the body with
# one REST PATCH and logging REFSADDED, so the next pass routes it like any other PR. When it cannot be derived,
# the PR is not ignored silently: one skip comment on the PR and a NOREFS log line, once per head, so the next
# pass that still cannot map it stays quiet.
#  1. new head sha, no review yet -> prepush.sh gate (process-only problems become a CHANGES verdict with no model review),
#     else start revci.sh detached (review + CI wait)
#  2. review+CI done on the head sha: CHANGES, failed CI, merge conflict or unresolved review threads
#     -> autofix.sh (one agent-squad fix run on the PR branch, every problem in one request), up to AP_MAX_ROUNDS;
#     past the cap -> autopanel.sh (Opus ruling: one more round, or label needs-human + NEEDS OWNER on the issue)
#  3. APPROVE + CI green + mergeable clean + no unresolved threads -> squash-merge (SG_AUTO_MERGE=yes)
#  Stacked PRs (base = another PR's branch, see stack.sh): never merged while the base is not $SG_BASE; once the parent
#  is merged the PR is retargeted to $SG_BASE (RETARGET) and the parent's last sha is kept in auto/stack-<pr> for the rebase.
# Fix and panel runs go detached; their lines land in $SG_STATE/auto.log and are echoed here on the next pass.
# Age SLA: a PR's age is its own -- auto/seen-<pr> holds the epoch of the PR's created_at. Past
#  SG_AGE_MINUTES (30) the PR leads the queue (the list is sorted oldest-first) and its review runs on a short leash:
#  SG_REVIEW_CHAIN_DEPTH=1 model, SG_REVIEW_RETRY_BUDGET=1 try, SG_REVIEW_TIMEOUT=120 s. An old PR may not spend the whole
#  chain again while a fresh one waits. AGING fires once, STALLED every 15 min after, so a stuck PR is visible in watch.log.
# Kill switch: touch $SG_STATE/auto/off (everything) or $SG_STATE/auto/nofix (review+merge only). Hold a PR: label `hold` or `needs-human`; an owner comment `/release` (whole body) lifts it.
# Gate record: what the loop decided about a PR, in a file, so no one has to read auto.log to know why a green PR is still open.
#  auto/.pass is the epoch of the pass itself; auto/gate-<pr> is `<head sha><TAB><epoch><TAB><reason><TAB><what it means>`, written
#  at EVERY exit of the pass body (a bare `continue` as much as a HOLD), through a temp file + mv so a reader never sees half a
#  line. It is rewritten every pass, so it carries the latest reason only -- never an accumulated list of them.
#  The hold markers (auto/hold-<pr>-<sha9>-<reason>) are not this: they are written once per head and reason and never cleared,
#  so their mtime is the FIRST gate, not the last, and most exits write none at all.
# Events: AUTOREVIEW, AUTOFIX, AUTOPANEL, NEEDS OWNER, AUTOMERGE, AGING, STALLED, CI MISSING, NOREFS, REFSADDED.
. "$(dirname "$0")/env.sh"; A="$SG_STATE/auto"; mkdir -p "$A"; printf '%s\n' "$(date +%s)" > "$A/.pass"; [ -e "$A/off" ] && exit 0
L="$SG_STATE/auto.log"; touch "$L"; seen=$(cat "$A/.logseen" 2>/dev/null || echo 0); tot=$(wc -l < "$L" | tr -d ' ')
[ "$tot" -gt "$seen" ] && tail -n +$((seen+1)) "$L" | grep -E "^(AUTOFIX|AUTOPANEL|NEEDS OWNER)"; echo "$tot" > "$A/.logseen"
MAX=${AP_MAX_ROUNDS:-2}; MIN=${AP_MIN_ISSUE:-1090}; here="$(dirname "$0")"
bg() { nohup bash "$here/$1" "${@:2}" >> "$L" 2>&1 </dev/null & }
# gate <reason> <what it means>: the pass's decision about $pr, in auto/gate-$pr, overwritten here so the file names the
# latest reason and only it. $s9 is the head sha the decision was made on; the temp file + mv keeps a reader from ever
# seeing a half-written line (the dashboard reads this while the pass runs).
gate() { printf '%s\t%s\t%s\t%s\n' "$s9" "$(date +%s)" "$1" "$2" > "$A/gate-$pr.tmp" && mv "$A/gate-$pr.tmp" "$A/gate-$pr"; }
# The issue a PR delivers when its title and body do not say: the branch (`gui/<N>` is fork-only work) or the run
# that produced the branch — a record of $SG_CHECKOUT/runs that names the branch carries the issue it was dispatched
# for, the same `issue #N` / `Refs #N` shape hub.py reads. Prints N and returns 0, or returns 1 (nothing to repair).
derive_issue() {
  local ref=$1 n d
  case "$ref" in gui/[0-9]*) n=${ref#gui/}; n=${n%%[!0-9]*}; [ -n "$n" ] && { echo "$n"; return 0; };; esac
  [ -n "$ref" ] && [ -n "$SG_CHECKOUT" ] || return 1
  for d in "$SG_CHECKOUT"/runs/*/record.jsonl; do
    [ -f "$d" ] || continue
    grep -qF -- "$ref" "$d" 2>/dev/null || continue
    n=$(head -200 "$d" | grep -oE '(issue|Refs) #[0-9]{3,5}' | head -1 | grep -oE '[0-9]+')
    [ -n "$n" ] && { echo "$n"; return 0; }
  done
  return 1
}
now=$(date +%s); list=$(mktemp)
gh api "repos/$SG_REPO/pulls?state=open&per_page=100" 2>/dev/null \
 | jq -r '.[]|select(.draft==false)|[.number,.head.sha,(((.title+" "+(.body//""))|capture("Refs #(?<n>[0-9]+)")|.n) // ""),.head.ref,([.labels[].name]|join(",")),.base.ref,.created_at]|join("\u001f")' > "$list"
# oldest first. auto/seen-<pr> holds the PR's own birthday (the epoch of its created_at), so the age is the PR's and not
# the moment this loop happened to notice it. A PR with no marker yet keys as "now": it is new, not old, so it goes last.
while IFS= read -r l; do printf '%s\t%s\n' "$(cat "$A/seen-${l%%$'\x1f'*}" 2>/dev/null || echo "$now")" "$l"; done < "$list" | sort -n | cut -f2- > "$list.s"
while IFS=$'\x1f' read -r pr sha iss ref labels base created; do   # unit separator: a tab IFS collapses empty fields (no labels) and shifts the rest
  s9=${sha:0:9}   # the head this pass's decision is about: gate() writes it into auto/gate-$pr, so it is set before any exit
  if [ -z "$iss" ]; then   # no Refs #N: repair the PR when the issue is derivable, else say so once per head
    n=$(derive_issue "$ref"); [ -n "$n" ] && [ "$n" -ge "$MIN" ] 2>/dev/null || n=""
    if [ -n "$n" ]; then
      cur=$(gh api "repos/$SG_REPO/pulls/$pr" -q .body 2>/dev/null)
      gh api -X PATCH "repos/$SG_REPO/pulls/$pr" -f body="$(printf '%s\n\nRefs #%s' "$cur" "$n")" >/dev/null 2>&1 \
        && { gate refs-added "the PR said no issue; Refs #$n was derived from the branch $ref and written into its body"; echo "REFSADDED #$pr #$n (the PR said no issue; derived from the branch $ref)"; continue; }
      echo "REFSADDED FAILED #$pr: could not write Refs #$n into the body"
    fi
    [ -e "$A/norefs-$pr-$s9" ] && { gate no-refs "no Refs #N in the title or body: the loop cannot map the PR to an issue"; continue; }
    touch "$A/norefs-$pr-$s9"
    printf "PR #%s is skipped by the autopilot: its title and body have no \`Refs #N\`, so the loop cannot map it to an issue and will not review, fix or merge it. Add \`Refs #<issue>\` (the live issue this PR delivers) and the next pass will pick it up.\n" $pr > "$SG_STATE/cq/$pr-norefs$s9.md"
    gate no-refs "no Refs #N in the title or body: the loop cannot map the PR to an issue"
    echo "NOREFS #$pr @ $s9: no Refs #N; skipped (the loop cannot map it to an issue)"
    continue
  fi
  [ "$iss" -ge "$MIN" ] 2>/dev/null || { gate below-min-issue "issue #$iss is below the autopilot's minimum (#$MIN)"; continue; }
  if [ -n "$base" ] && [ "$base" != "$SG_BASE" ]; then   # stacked: retarget once the parent PR (head = our base) is merged
    par=$(gh api "repos/$SG_REPO/pulls?state=closed&head=${SG_REPO%%/*}:$base&per_page=5" -q '[.[]|select(.merged_at!=null)][0]|select(.)|"\(.number) \(.head.sha)"' 2>/dev/null)
    if [ -n "$par" ] && [ "$par" != null ] && [ "$(cat "$A/stack-$pr" 2>/dev/null)" != "${par#* }" ]; then   # once per parent (the list API can lag)
      echo "${par#* }" > "$A/stack-$pr"; gh api -X PATCH repos/$SG_REPO/pulls/$pr -f base="$SG_BASE" >/dev/null 2>&1 \
        && { printf "PR #%s retargeted to %s: its parent PR #%s was merged. A conflict from the squash is fixed by replaying only this PR's commits onto %s.\n" $pr "$SG_BASE" "${par%% *}" "$SG_BASE" > "$SG_STATE/cq/$iss-retarget$pr.md"; echo "RETARGET #$pr -> $SG_BASE (parent #${par%% *} merged)"; }
    fi
  fi
  out="$SG_STATE/revci-$pr.out"
  # owner release: a comment whose whole body is `/release` on the PR or its issue lifts the hold (like /revalidate on a community pack). Only a command
  # counts: the loop's own text ("Reply /release ...") is never the whole body. Each comment id is honored once, so a later re-hold stays held.
  case ",$labels," in *,hold,*|*,needs-human,*)
    for cid in $( { gh api "repos/$SG_REPO/issues/$pr/comments?per_page=100" 2>/dev/null; gh api "repos/$SG_REPO/issues/$iss/comments?per_page=100" 2>/dev/null; } | jq -r '.[]|select((.body//"")|gsub("^\\s+|\\s+$";"")=="/release")|.id' 2>/dev/null); do
      [ -e "$A/released-$cid" ] && continue; touch "$A/released-$cid"
      for lb in hold needs-human; do case ",$labels," in *,$lb,*) gh api -X DELETE "repos/$SG_REPO/issues/$pr/labels/$lb" >/dev/null 2>&1;; esac; done
      rm -f "$A"/hold-$pr-*-label; labels=""; echo "RELEASE #$pr: owner replied /release (comment $cid); hold lifted, the next pass reviews and merges it"; break
    done;; esac
  case ",$labels," in *,hold,*|*,needs-human,*) gate hold-label "the PR carries the label $labels, so the loop holds it until the owner lifts it"
    grep -q "^#$pr $s9 .*APPROVE" "$out" 2>/dev/null && { [ -e "$A/hold-$pr-$s9-label" ] || { touch "$A/hold-$pr-$s9-label"; echo "HOLD #$pr @ $s9: label hold/needs-human ($labels)"; }; }; continue;; esac
  sf="$A/seen-$pr"
  if [ -s "$sf" ]; then age=$(( (now - $(cat "$sf")) / 60 ))
  else c=$(TZ=UTC date -j -f "%Y-%m-%dT%H:%M:%S" "${created%Z}" +%s 2>/dev/null); echo "${c:-$now}" > "$sf"; age=$(( (now - ${c:-$now}) / 60 )); fi
  aging=""; [ "$age" -ge "${SG_AGE_MINUTES:-30}" ] && aging=1
  if [ -n "$aging" ]; then
    if [ ! -e "$A/aging-$pr" ]; then touch "$A/aging-$pr"
      echo "AGING #$pr (issue #$iss): open ${age} min; from now on it leads the queue and its review runs on a short leash (1 model, 1 try, ${SG_REVIEW_TIMEOUT:-120}s), so it cannot spend the whole chain again"
    elif [ -z "$(find "$A/stalled-$pr" -mmin -15 2>/dev/null)" ]; then touch "$A/stalled-$pr"
      echo "STALLED #$pr (issue #$iss): still open after ${age} min"; fi
  fi
  # an issue closed as a duplicate / not planned: its PR is superseded; no review, fix or merge (janitor I kills such runs,
  # so a fix dispatched here would die and be retried forever). Re-asked every 10 min per PR (an issue can be reopened).
  ck="$A/isschk-$pr"; [ -e "$A/dupiss-$pr-$iss" ] && [ -n "$(find "$ck" -mmin -10 2>/dev/null)" ] \
    && { gate issue-duplicate "issue #$iss is closed as a duplicate / not planned, so the PR is superseded and is not reviewed, fixed or merged"; continue; }
  if [ -z "$(find "$ck" -mmin -10 2>/dev/null)" ]; then touch "$ck"
    if bash "$here/autofix.sh" --dupissue $iss; then
      [ -e "$A/dupiss-$pr-$iss" ] || { touch "$A/dupiss-$pr-$iss"; printf "PR #%s is superseded: issue #%s is closed as a duplicate / not planned, so the loop will not review, fix or merge it. Close the PR, or point its Refs at the live issue.\n" $pr $iss > "$SG_STATE/cq/$iss-dupiss$pr.md"; echo "SUPERSEDED-ISSUE #$pr: issue #$iss closed as duplicate/not planned; no review, fix or merge"; }
      gate issue-duplicate "issue #$iss is closed as a duplicate / not planned, so the PR is superseded and is not reviewed, fixed or merged"
      continue
    fi; rm -f "$A/dupiss-$pr-$iss"; fi
  # The run's result is its summary line: the `#<pr> <sha9> ` namespace carries that line and nothing else (revci.sh
  # prints CI_DISPATCH/SUPERSEDED/CI_TIMEOUT as `NOTICE ` lines), and it always carries `CI=`. Requiring both means a
  # notice can never be read as a result -- which used to leave the head with no `CI=` and no verdict, so it was never
  # queued for a review again (#1767).
  line=$(grep -E "^#$pr $s9 .*CI=" "$out" 2>/dev/null | tail -1)
  if [ -z "$line" ]; then
    /bin/ps -axo command= | grep -Eq "revci\.sh $pr( |\$)" && { gate reviewing "a review of this head is already in flight (revci.sh $pr)"; continue; }
    [ -e "$A/launched-$pr-$s9" ] && { gate reviewing "a review of this head was launched on an earlier pass and has not written its line yet"; continue; }
    touch "$A/launched-$pr-$s9"
    # A head that already conflicts with its base cannot merge as it is, so it will move before it can: reviewing it now
    # spends a model on a diff that is about to change, and the conflict round that follows pushes a new head that is
    # reviewed a second time. Fix the conflict first, and let the review see the rebased head. (This is how PR #1285 was
    # reviewed against a head that had been doomed for minutes: #1391, its sibling, had merged on top of the same files.)
    if [ "$(gh api repos/$SG_REPO/pulls/$pr -q .mergeable_state 2>/dev/null)" = dirty ]; then
      [ -e "$A/queued-$pr-$s9" ] && { gate conflict "the head conflicts with its base and a fix round is already running"; continue; }
      touch "$A/queued-$pr-$s9"; AF_SOFT=1 bg autofix.sh $pr $iss
      gate conflict "the head conflicts with its base; a fix round runs before the review (a review of this diff would be spent on one about to move)"
      echo "AUTOFIX queued #$pr @ $s9 (conflict before review: the head conflicts with its base, so a review of it would be spent on a diff that is about to move)"; continue
    fi
    # prepush gate: process-only problems (closing keyword, a cited file missing from the head, profile checks) skip the
    # model review and go straight to a fix round as a CHANGES verdict (autofix reads review-<pr>.md as usual)
    pp=; if [ "${SG_PREPUSH_GATE:-yes}" = yes ] && pp=$(bash "$here/prepush.sh" $pr 2>/dev/null; exit $?) ; then :; elif [ -n "$pp" ]; then
      printf 'Prepush gate (no model review ran)\nVERDICT: CHANGES\n%s\n' "$pp" > "$SG_STATE/review-$pr.md"
      echo "#$pr $s9 $(gh api repos/$SG_REPO/pulls/$pr -q .mergeable_state 2>/dev/null || echo unknown) CI=skipped VERDICT: CHANGES (prepush)" >> "$out"
      gate prepush "$(printf '%s' "$pp" | wc -l | tr -d ' ') process problem(s): a fix round runs without a model review"
      echo "PREPUSH #$pr @ $s9: $(printf '%s' "$pp" | wc -l | tr -d ' ') process problem(s), fix round without a model review"; continue; fi
    aenv=""; [ -n "$aging" ] && aenv="SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1 SG_REVIEW_TIMEOUT=${SG_REVIEW_TIMEOUT:-120}"
    nohup env $aenv bash "$here/revci.sh" $pr $iss >> "$out" 2>&1 </dev/null &
    gate reviewing "no review of this head yet: revci.sh $pr was started (review + CI wait)"
    echo "AUTOREVIEW #$pr @ $s9 (issue #$iss) revci started${aging:+ (aging: short leash)}"; continue
  fi
  # The run's terminal notices say this head is done with, but they are not results: they live outside the summary
  # namespace, so they are read by their own prefix -- and each still records the pass's gate like every other exit.
  grep -qE "^NOTICE #$pr $s9 SUPERSEDED" "$out" 2>/dev/null && { gate superseded "the review marked this head SUPERSEDED"; continue; }
  grep -qE "^NOTICE #$pr $s9 CI_TIMEOUT" "$out" 2>/dev/null && { gate ci-timeout "the review of this head hit CI_TIMEOUT"; continue; }
  # unresolved threads: asked once per head sha after review+CI (CodeRabbit has posted by then), cached
  tf="$A/threads-$pr-$s9"; [ -e "$tf" ] || bash "$here/autofix.sh" --threads $pr > "$tf"; nt=$(cat "$tf"); nt=${nt:-0}
  ms=$(awk '{print $3}' <<<"$line"); ci=$(sed -n 's/.*CI=\([^ ]*\).*/\1/p' <<<"$line")
  # BLOCKED means no model in the chain could answer at all (a spent quota, a window that has not opened, a model that
  # hit its cap): the PR itself is not accused of anything, so an autofix would spend a round on nothing. Re-run the
  # review instead, at most once per head per SG_REREVIEW_MINUTES, so a transient outage heals without moving the head.
  if [[ $line == *BLOCKED* ]] && [[ $ci != *failure* && $ci != *timed_out* && $ci != *cancelled* ]] && [ "$ms" != dirty ]; then
    rf="$A/rereview-$pr-$s9"
    [ -e "$rf" ] && [ $(( $(date +%s) - $(stat -f %m "$rf") )) -lt $(( ${SG_REREVIEW_MINUTES:-20} * 60 )) ] \
      && { gate reviewing "every review model failed (BLOCKED) and the re-review is not due yet"; continue; }
    /bin/ps -axo command= | grep -Eq "revci\.sh $pr( |\$)" && { gate reviewing "a review of this head is already in flight (revci.sh $pr)"; continue; }   # two would race on review-<pr>.md
    aenv=""; [ -n "$aging" ] && aenv="SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1 SG_REVIEW_TIMEOUT=${SG_REVIEW_TIMEOUT:-120}"
    # #1501: a BLOCKED that is only the leash timeout must not be re-queued with that same leash — the
    # next pass would kill the only reachable model again. Drop the timeout clamp; keep depth 1 / 1 try.
    if [ -n "$aging" ]; then
      leash=${SG_REVIEW_TIMEOUT:-120}
      last=$(awk -F'\t' -v p="$pr" '$2==p {row=$0} END{print row}' "$SG_STATE/review-times.tsv" 2>/dev/null)
      sec=$(printf '%s\n' "$last" | awk -F'\t' '{print $4}')
      lrc=$(printf '%s\n' "$last" | awk -F'\t' '{print $5}')
      [ "$lrc" = 142 ] && [ -n "$sec" ] && [ "$sec" -ge "$leash" ] && aenv="SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1"
    fi
    touch "$rf"; nohup env $aenv bash "$here/revci.sh" $pr $iss >> "$SG_STATE/revci-$pr.out" 2>&1 </dev/null &
    gate reviewing "every review model failed (BLOCKED) and nothing else is wrong with the head: the review was re-queued"
    echo "REREVIEW #$pr @ $s9: every review model failed and nothing else is wrong with it; the review is re-queued (next attempt no sooner than ${SG_REREVIEW_MINUTES:-20} min)"
    continue
  fi
  # ADR-0273 Decision 3/5: a CHANGES whose review has no BLOCKING line (and at least one DEBT line) does not cost a round -- the
  # debt is filed and the merge path below runs with every guard. Never for a (prepush) CHANGES, failed CI, a conflict or open
  # threads, and never once the open `debt` ledger is past SG_DEBT_MAX.
  soft=0; rv="$SG_STATE/review-$pr.md"; tag='^[^[:alnum:]]*([0-9]+[.)][[:space:]]*)?'
  if [ "${SG_DEBT_SOFT:-yes}" = yes ] && [[ $line == *CHANGES* && $line != *"(prepush)"* && $ci != *failure* && $ci != *timed_out* && $ci != *cancelled* ]] \
     && [ "$ms" != dirty ] && [ "$nt" -eq 0 ] && [ -f "$rv" ] && ! grep -Eiq "${tag}BLOCKING" "$rv" && grep -Eiq "${tag}DEBT" "$rv"; then
    od=$(gh api --paginate "repos/$SG_REPO/issues?labels=debt&state=open&per_page=100" -q '.[].number' 2>/dev/null | grep -c .)
    [ "${od:-0}" -le "${SG_DEBT_MAX:-15}" ] && soft=1
  fi
  prob=0 hard=0; [[ $line == *CHANGES* && $line != *"(prepush)"* && $soft = 0 ]] && hard=1; [[ $line == *"(prepush)"* ]] && prob=1; [[ $ci == *failure* || $ci == *timed_out* || $ci == *cancelled* ]] && hard=1
  [ $hard = 1 ] || [ "$ms" = dirty ] || [ "$nt" -gt 0 ] && prob=1   # a prepush CHANGES is a soft round (process, not code)
  if [ $prob = 1 ]; then
    [ -e "$A/nofix" ] && { gate nofix "a problem needs a fix round and the fix switch is off (auto/nofix)"; continue; }
    [ -e "$A/queued-$pr-$s9" ] && { gate round-queued "a fix round for this head is already queued"; continue; }
    touch "$A/queued-$pr-$s9"
    n=$(grep -c . "$A/rounds-$pr" 2>/dev/null | head -1); n=${n:-0}
    # the round cap bounds review/CI ping-pong; threads- or conflict-only rounds don't count (own cap: 3 per PR)
    if [ $hard = 0 ]; then k=$(grep -c . "$A/softfix-$pr" 2>/dev/null | head -1); k=${k:-0}
      [ "$k" -lt 3 ] && { echo "$s9" >> "$A/softfix-$pr"; AF_SOFT=1 bg autofix.sh $pr $iss; gate fixing-threads "fixing: threads $nt ($((k+1))/3), a soft round"; echo "AUTOFIX queued #$pr @ $s9 (threads/conflict only)"; continue; }
      bg autopanel.sh $pr $iss; gate panel "the 3 thread/conflict rounds are spent: the panel decides"; echo "ROUND CAP #$pr issue #$iss: 3 thread/conflict rounds spent -> panel"; continue; fi
    if [ "$n" -lt "$MAX" ]; then bg autofix.sh $pr $iss; gate fix-round "fix round $((n+1))/$MAX is running: ${ci:+CI=$ci }$ms${nt:+ threads=$nt}"
      echo "AUTOFIX queued #$pr @ $s9 (round $((n+1))/$MAX)"
    else bg autopanel.sh $pr $iss; gate panel "the $MAX fix rounds are spent: the panel decides"; echo "ROUND CAP #$pr issue #$iss: $n fix rounds spent -> panel"; fi
    continue
  fi
  [[ $line == *APPROVE* ]] || [ $soft = 1 ] || { gate no-approval "the review of this head is not an APPROVE (${line#*VERDICT: })"; continue; }
  # A head no workflow has run on is not a green head. `CI=none` reaches this point untouched, and an empty check list used to
  # read as "nothing is wrong" -- so a PR would merge having never run the five contexts the `main` ruleset requires. Ask
  # GitHub for the head's check runs directly (the revci line is a snapshot from when the review finished) and re-dispatch
  # the workflow rather than merging. PR #1285 sat at `CI=none` on a head whose checks had never been created at all.
  ck=$(gh api "repos/$SG_REPO/commits/$sha/check-runs" -q '[.check_runs[].conclusion//.status]|join(",")' 2>/dev/null)
  if [ -z "$ck" ]; then
    gate ci-missing "the head has no check run at all: a green head cannot be claimed${SG_CI_WORKFLOW:+ and $SG_CI_WORKFLOW was re-dispatched}"
    if [ ! -e "$A/noci-$pr-$s9" ]; then touch "$A/noci-$pr-$s9"
      [ -n "${SG_CI_WORKFLOW:-}" ] && gh workflow run "$SG_CI_WORKFLOW" --repo "$SG_REPO" --ref "$(gh api repos/$SG_REPO/pulls/$pr -q .head.ref 2>/dev/null)" >/dev/null 2>&1
      printf "PR #%s carries review APPROVE at %s but GitHub has no check run for that head at all, so the required checks of the %s ruleset never reported and a green head cannot be claimed. The merge is held and %s was re-dispatched. If no run appears, start it by hand: gh workflow run %s --repo %s --ref <head branch>.\n" $pr $s9 "$SG_BASE" "${SG_CI_WORKFLOW:-the CI workflow}" "${SG_CI_WORKFLOW:-the CI workflow}" "$SG_REPO" > "$SG_STATE/cq/$iss-noci$$.md"
      echo "CI MISSING #$pr @ $s9: no check run exists for this head; merge held${SG_CI_WORKFLOW:+, $SG_CI_WORKFLOW re-dispatched}"; fi
    continue; fi
  # an APPROVE that does not merge says why, once per head and reason in auto.log, and always in auto/gate-$pr (the file
  # carries the latest reason, so the marker cannot stand in for it: the marker is written once per head and reason)
  hold() { gate "hold-$1" "$2"; [ -e "$A/hold-$pr-$s9-$1" ] || { touch "$A/hold-$pr-$s9-$1"; echo "HOLD #$pr @ $s9: $2"; }; }
  [ "$SG_AUTO_MERGE" = yes ] || { hold auto "SG_AUTO_MERGE is not yes"; continue; }
  [ "$base" = "$SG_BASE" ] || { hold stacked "base is $base, not $SG_BASE (waits for the parent's merge and the retarget)"; continue; }
  [ -e "$A/merged-$pr" ] && { gate merged "this PR was already merged by the loop"; continue; }
  body=$(gh api repos/$SG_REPO/pulls/$pr -q .body 2>/dev/null)
  for d in $(echo "$body" | grep -ioE "merge (after|order)[^.]*" | grep -oE "#[0-9]+" | tr -d '#'); do
    [ "$(gh api repos/$SG_REPO/pulls/$d -q .state 2>/dev/null)" = open ] && { hold order "its body says merge after #$d, which is still open"; continue 2; }; done
  st=$(gh api repos/$SG_REPO/pulls/$pr -q '.head.sha+" "+.mergeable_state')
  [ "$st" = "$sha clean" ] || { hold state "GitHub reports '${st#* }' (head ${st%% *}), not clean on $s9"; continue; }
  if [ "$(gh api repos/$SG_REPO/pulls/$pr -q .changed_files 2>/dev/null)" = 0 ]; then   # a fix round that undid everything: nothing to merge
    gate empty-diff "the diff against $SG_BASE is empty (a fix round undid the change): nothing to merge"
    [ -e "$A/empty-$pr-${sha:0:9}" ] || { touch "$A/empty-$pr-${sha:0:9}"; printf "NEEDS OWNER: PR #%s @ %s has an empty diff against %s (a fix round undid the change); not merged.\n" $pr ${sha:0:9} "$SG_BASE" > "$SG_STATE/cq/$iss-empty$$.md"; echo "NEEDS OWNER #$pr issue #$iss: empty diff at ${sha:0:9}, not merged"; }
    continue; fi
  [ "$(bash "$here/autofix.sh" --threads $pr)" = 0 ] || { rm -f "$tf"; hold threads "unresolved review threads opened after the cache"; continue; }   # a thread opened after the cache: re-ask next pass
  if [ $soft = 1 ]; then   # the merge cannot happen unless the filing succeeded; filing is idempotent per finding, so a retry files nothing twice
    fl=${SG_DEBT_FILER:-env PYTHONPATH=${SG_FORK:-$HOME/VSCodeProjects/agent-squad} python3 -m squad.debt_filing}
    $fl --repo "$SG_REPO" --pr $pr --issue $iss --head $sha --review "$rv" >> "$A/debt-$pr.out" 2>&1 || { hold debtfile "debt filing failed (see auto/debt-$pr.out); the debt-only CHANGES is not merged"; continue; }
    echo "DEBT FILED #$pr @ $s9: debt-only CHANGES, debt filed, merge guards next"
  fi
  if m=$(gh api -X PUT repos/$SG_REPO/pulls/$pr/merge -f merge_method=$SG_MERGE_METHOD -f sha=$sha -q .sha 2>&1); then
    touch "$A/merged-$pr"; gate merged "merged as ${m:0:9}: APPROVE + CI green + clean + no open threads"
    printf "PR #%s merged automatically (squash %s): review APPROVE and CI green on %s, mergeable clean, no open review threads. Follow-up: e2e on the merge SHA, close when AC are met.\n" $pr "${m:0:9}" $s9 > "$SG_STATE/cq/$iss-automerge$$.md"
    $BOARD $SG_OWNER $SG_PROJECT "Testing" $iss >/dev/null   # merged: the e2e on the merge SHA is the test pass
    echo "AUTOMERGE #$pr issue #$iss sha ${m:0:9} -> e2e, worktree cleanup, close if done"
  else echo "AUTOMERGE FAILED #$pr: $m"; gate merge-failed "the merge call failed: $m"; touch "$A/merged-$pr"; fi
done < "$list.s"
rm -f "$list" "$list.s"
