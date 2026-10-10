#!/bin/bash
# One pass per watch cycle. Open PRs that name an issue ("Refs #N"):
#  1. new head sha, no review yet -> start revci.sh detached (review + CI wait)
#  2. review+CI done on the head sha: CHANGES, failed CI, merge conflict or unresolved review threads
#     -> autofix.sh (one agent-squad fix run on the PR branch, every problem in one request), up to AP_MAX_ROUNDS;
#     past the cap -> autopanel.sh (Opus ruling: one more round, or label needs-human + NEEDS OWNER on the issue)
#  3. APPROVE + CI green + mergeable clean + no unresolved threads -> squash-merge (SG_AUTO_MERGE=yes)
#  Stacked PRs (base = another PR's branch, see stack.sh): never merged while the base is not $SG_BASE; once the parent
#  is merged the PR is retargeted to $SG_BASE (RETARGET) and the parent's last sha is kept in auto/stack-<pr> for the rebase.
# Fix and panel runs go detached; their lines land in $SG_STATE/auto.log and are echoed here on the next pass.
# Kill switch: touch $SG_STATE/auto/off (everything) or $SG_STATE/auto/nofix (review+merge only). Hold a PR: label `hold` or `needs-human`.
# Events: AUTOREVIEW, AUTOFIX, AUTOPANEL, NEEDS OWNER, AUTOMERGE.
. "$(dirname "$0")/env.sh"; A="$SG_STATE/auto"; mkdir -p "$A"; [ -e "$A/off" ] && exit 0
L="$SG_STATE/auto.log"; touch "$L"; seen=$(cat "$A/.logseen" 2>/dev/null || echo 0); tot=$(wc -l < "$L" | tr -d ' ')
[ "$tot" -gt "$seen" ] && tail -n +$((seen+1)) "$L" | grep -E "^(AUTOFIX|AUTOPANEL|NEEDS OWNER)"; echo "$tot" > "$A/.logseen"
MAX=${AP_MAX_ROUNDS:-2}; MIN=${AP_MIN_ISSUE:-1090}; here="$(dirname "$0")"
bg() { nohup bash "$here/$1" "${@:2}" >> "$L" 2>&1 </dev/null & }
gh api "repos/$SG_REPO/pulls?state=open&per_page=100" 2>/dev/null \
 | jq -r '.[]|select(.draft==false)|[.number,.head.sha,(((.title+" "+(.body//""))|capture("Refs #(?<n>[0-9]+)")|.n) // ""),([.labels[].name]|join(",")),.base.ref]|join("\u001f")' \
 | while IFS=$'\x1f' read -r pr sha iss labels base; do   # unit separator: a tab IFS collapses empty fields (no labels) and shifts the rest
  [ -n "$iss" ] && [ "$iss" -ge "$MIN" ] 2>/dev/null || continue
  if [ -n "$base" ] && [ "$base" != "$SG_BASE" ]; then   # stacked: retarget once the parent PR (head = our base) is merged
    par=$(gh api "repos/$SG_REPO/pulls?state=closed&head=${SG_REPO%%/*}:$base&per_page=5" -q '[.[]|select(.merged_at!=null)][0]|select(.)|"\(.number) \(.head.sha)"' 2>/dev/null)
    if [ -n "$par" ] && [ "$par" != null ] && [ "$(cat "$A/stack-$pr" 2>/dev/null)" != "${par#* }" ]; then   # once per parent (the list API can lag)
      echo "${par#* }" > "$A/stack-$pr"; gh api -X PATCH repos/$SG_REPO/pulls/$pr -f base="$SG_BASE" >/dev/null 2>&1 \
        && { printf "PR #%s retargeted to %s: its parent PR #%s was merged. A conflict from the squash is fixed by replaying only this PR's commits onto %s.\n" $pr "$SG_BASE" "${par%% *}" "$SG_BASE" > "$SG_STATE/cq/$iss-retarget$pr.md"; echo "RETARGET #$pr -> $SG_BASE (parent #${par%% *} merged)"; }
    fi
  fi
  s9=${sha:0:9}; out="$SG_STATE/revci-$pr.out"
  case ",$labels," in *,hold,*|*,needs-human,*) grep -q "^#$pr $s9 .*APPROVE" "$out" 2>/dev/null && { [ -e "$A/hold-$pr-$s9-label" ] || { touch "$A/hold-$pr-$s9-label"; echo "HOLD #$pr @ $s9: label hold/needs-human ($labels)"; }; }; continue;; esac
  # an issue closed as a duplicate / not planned: its PR is superseded; no review, fix or merge (janitor I kills such runs,
  # so a fix dispatched here would die and be retried forever). Re-asked every 10 min per PR (an issue can be reopened).
  ck="$A/isschk-$pr"; [ -e "$A/dupiss-$pr-$iss" ] && [ -n "$(find "$ck" -mmin -10 2>/dev/null)" ] && continue
  if [ -z "$(find "$ck" -mmin -10 2>/dev/null)" ]; then touch "$ck"
    if bash "$here/autofix.sh" --dupissue $iss; then
      [ -e "$A/dupiss-$pr-$iss" ] || { touch "$A/dupiss-$pr-$iss"; printf "PR #%s is superseded: issue #%s is closed as a duplicate / not planned, so the loop will not review, fix or merge it. Close the PR, or point its Refs at the live issue.\n" $pr $iss > "$SG_STATE/cq/$iss-dupiss$pr.md"; echo "SUPERSEDED-ISSUE #$pr: issue #$iss closed as duplicate/not planned; no review, fix or merge"; }
      continue
    fi; rm -f "$A/dupiss-$pr-$iss"; fi
  line=$(grep "^#$pr $s9 " "$out" 2>/dev/null | tail -1)
  if [ -z "$line" ]; then
    /bin/ps -axo command= | grep -Eq "revci\.sh $pr( |\$)" && continue
    [ -e "$A/launched-$pr-$s9" ] && continue
    touch "$A/launched-$pr-$s9"; nohup bash "$here/revci.sh" $pr $iss >> "$out" 2>&1 </dev/null &
    echo "AUTOREVIEW #$pr @ $s9 (issue #$iss) revci started"; continue
  fi
  [[ $line == *SUPERSEDED* || $line == *CI_TIMEOUT* ]] && continue
  # unresolved threads: asked once per head sha after review+CI (CodeRabbit has posted by then), cached
  tf="$A/threads-$pr-$s9"; [ -e "$tf" ] || bash "$here/autofix.sh" --threads $pr > "$tf"; nt=$(cat "$tf"); nt=${nt:-0}
  ms=$(awk '{print $3}' <<<"$line"); ci=$(sed -n 's/.*CI=\([^ ]*\).*/\1/p' <<<"$line")
  prob=0 hard=0; [[ $line == *CHANGES* ]] && hard=1; [[ $ci == *failure* || $ci == *timed_out* || $ci == *cancelled* ]] && hard=1
  [ $hard = 1 ] || [ "$ms" = dirty ] || [ "$nt" -gt 0 ] && prob=1
  if [ $prob = 1 ]; then
    [ -e "$A/nofix" ] && continue
    [ -e "$A/queued-$pr-$s9" ] && continue; touch "$A/queued-$pr-$s9"
    n=$(grep -c . "$A/rounds-$pr" 2>/dev/null | head -1); n=${n:-0}
    # the round cap bounds review/CI ping-pong; threads- or conflict-only rounds don't count (own cap: 3 per PR)
    if [ $hard = 0 ]; then k=$(grep -c . "$A/softfix-$pr" 2>/dev/null | head -1); k=${k:-0}
      [ "$k" -lt 3 ] && { echo "$s9" >> "$A/softfix-$pr"; AF_SOFT=1 bg autofix.sh $pr $iss; echo "AUTOFIX queued #$pr @ $s9 (threads/conflict only)"; continue; }
      bg autopanel.sh $pr $iss; echo "ROUND CAP #$pr issue #$iss: 3 thread/conflict rounds spent -> panel"; continue; fi
    if [ "$n" -lt "$MAX" ]; then bg autofix.sh $pr $iss; echo "AUTOFIX queued #$pr @ $s9 (round $((n+1))/$MAX)"
    else bg autopanel.sh $pr $iss; echo "ROUND CAP #$pr issue #$iss: $n fix rounds spent -> panel"; fi
    continue
  fi
  [[ $line == *APPROVE* ]] || continue
  hold() { [ -e "$A/hold-$pr-$s9-$1" ] || { touch "$A/hold-$pr-$s9-$1"; echo "HOLD #$pr @ $s9: $2"; }; }   # an APPROVE that does not merge says why, once per head and reason
  [ "$SG_AUTO_MERGE" = yes ] || { hold auto "SG_AUTO_MERGE is not yes"; continue; }
  [ "$base" = "$SG_BASE" ] || { hold stacked "base is $base, not $SG_BASE (waits for the parent's merge and the retarget)"; continue; }
  [ -e "$A/merged-$pr" ] && continue
  body=$(gh api repos/$SG_REPO/pulls/$pr -q .body 2>/dev/null)
  for d in $(echo "$body" | grep -ioE "merge (after|order)[^.]*" | grep -oE "#[0-9]+" | tr -d '#'); do
    [ "$(gh api repos/$SG_REPO/pulls/$d -q .state 2>/dev/null)" = open ] && { hold order "its body says merge after #$d, which is still open"; continue 2; }; done
  st=$(gh api repos/$SG_REPO/pulls/$pr -q '.head.sha+" "+.mergeable_state')
  [ "$st" = "$sha clean" ] || { hold state "GitHub reports '${st#* }' (head ${st%% *}), not clean on $s9"; continue; }
  if [ "$(gh api repos/$SG_REPO/pulls/$pr -q .changed_files 2>/dev/null)" = 0 ]; then   # a fix round that undid everything: nothing to merge
    [ -e "$A/empty-$pr-${sha:0:9}" ] || { touch "$A/empty-$pr-${sha:0:9}"; printf "NEEDS OWNER: PR #%s @ %s has an empty diff against %s (a fix round undid the change); not merged.\n" $pr ${sha:0:9} "$SG_BASE" > "$SG_STATE/cq/$iss-empty$$.md"; echo "NEEDS OWNER #$pr issue #$iss: empty diff at ${sha:0:9}, not merged"; }
    continue; fi
  [ "$(bash "$here/autofix.sh" --threads $pr)" = 0 ] || { rm -f "$tf"; hold threads "unresolved review threads opened after the cache"; continue; }   # a thread opened after the cache: re-ask next pass
  if m=$(gh api -X PUT repos/$SG_REPO/pulls/$pr/merge -f merge_method=$SG_MERGE_METHOD -f sha=$sha -q .sha 2>&1); then
    touch "$A/merged-$pr"
    printf "PR #%s merged automatically (squash %s): review APPROVE and CI green on %s, mergeable clean, no open review threads. Follow-up: e2e on the merge SHA, close when AC are met.\n" $pr "${m:0:9}" $s9 > "$SG_STATE/cq/$iss-automerge$$.md"
    $BOARD $SG_OWNER $SG_PROJECT "Testing" $iss >/dev/null   # merged: the e2e on the merge SHA is the test pass
    echo "AUTOMERGE #$pr issue #$iss sha ${m:0:9} -> e2e, worktree cleanup, close if done"
  else echo "AUTOMERGE FAILED #$pr: $m"; touch "$A/merged-$pr"; fi
done
