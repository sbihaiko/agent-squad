#!/bin/bash
# usage: branchreviewpass.sh   one pass over the pushed work branches: review each tip that has no review yet (#397).
#
# branchland.sh lands a work branch only when the review approved its tip, and branchreview.sh keeps that verdict and the sha it read
# under the branch key ($SG_STATE/review-<branch with / as _>.{md,sha}). This pass lists the work branches as branchland does
# (workbranches.sh: an open issue, no open pull request) and runs branchreview.sh once for each branch whose kept sha is not its tip.
# A tip with a kept review is not reviewed again. At most SG_REVIEW_PASS_MAX branches (default 3) are reviewed per pass; the rest wait
# for the next pass. watch.sh runs this under single.sh, so two passes never overlap.
# Up to SG_REVIEW_PARALLEL (default 3) branches are reviewed at once, each as a background job with its own result file and its own
# count; the lines print in list order after the jobs end. A stop (SG_REVIEW_TRIES failed tries at one tip) expires after
# SG_REVIEW_STOP_TTL seconds (default 1800): the marker and the count go, so a transient outage of the review chain heals (#426).
# A review takes one review slot of the machine-wide budget (caps.reviews, squad/caps.py, #567). A job waits for one, one line naming
# the full ceiling and its holder, for at most SG_REVIEW_SLOT_WAIT seconds (default 600); a job that gets none is skipped for this
# pass (BRANCHREVIEW SKIPPED), counts no failed try, and the next pass reviews the branch.
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
here="$(cd "$(dirname "$0")" && pwd)"; . "$here/caps.sh"
export SG_GH_PASS=branchreview
SG_GHREAD="python3 $here/ghthrottle.py"; . "$here/ghcall.sh"; . "$here/workbranches.sh"
. "$here/columnlimit.sh"; . "$here/startability.sh"; export SG_GH_PASS=branchreview   # the board's one read below, and the column limit it bounds (#2287)
remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}; max=${SG_REVIEW_PASS_MAX:-3}
export GIT_TERMINAL_PROMPT=0
cd "$SG_STATE" || exit 0
sg_work_branches "$remote" || { echo "BRANCHREVIEW: $wb_err"; exit 0; }
A="$SG_STATE/auto"; mkdir -p "$A"; tries=${SG_REVIEW_TRIES:-3}; ttl=${SG_REVIEW_STOP_TTL:-1800}; par=${SG_REVIEW_PARALLEL:-3}
[ "$par" -ge 1 ] 2>/dev/null || par=1
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
# why_failed <issue>: one `model: reason` per model, from the newest status files the review chain wrote for the issue
# (MODEL_FAIL, MODEL_FORMAT, MODEL_HOURS); a reset time the text names is kept even when the reason is cut.
why_failed() { local f l m r t seen=" " out=
  for f in $(ls -t "$SG_STATE"/cq/*"#$1-status~"mfail* "$SG_STATE"/cq/*"#$1-status~"mformat* "$SG_STATE"/cq/*"#$1-status~"mhours* 2>/dev/null); do
    l=$(head -1 "$f" | sed -nE 's/^MODEL_(FAIL|FORMAT|HOURS) review ([^ ]+) (skipped )?on branch [^:]+: (.*)$/\2\t\4/p'); [ -n "$l" ] || continue
    m=${l%%$'\t'*}; r=${l#*$'\t'}; case "$seen" in *" $m "*) continue;; esac; seen+="$m "
    r=$(printf '%s' "$r" | tr -s ' \t' ' ' | sed 's/ *$//')
    t=$(printf '%s' "$r" | grep -oiE '(resets?|try again|retry)( at| in| after| on)? [0-9A-Za-z:., -]{1,30}' | head -1 | sed 's/[ .,]*$//')
    [ "${#r}" -gt 100 ] && { r="${r:0:100}..."; [ -n "$t" ] && r="$r ($t)"; }
    out+="${out:+; }$m: $r"
  done; printf '%s' "$out"; }
# chain_summary: the review chain's own words, stderr kept. A resolver that cannot run is a named line, never an empty summary.
chain_summary() { local out rc
  out=$(python3 "$here/chainhealth.py" "$here/models.json" "${SG_SHARED:-$SG_STATE}" 2>&1); rc=$?
  printf '%s\n' "$out"
  [ "$rc" -eq 0 ] && return 0
  printf 'REVIEW_CHAIN: chainhealth failed (exit %s)\n' "$rc"
  printf '%s\n' "$out" >&2; return "$rc"; }
# review_one <sha> <branch> <issue> <key> <result file>: one review, its count, its lines. Every file it touches is named by the
# branch key, so jobs for two branches share nothing. A review whose kept sha is not the listed tip (a failed read, or a push
# that landed while it ran) is a failed try at the listed tip; the next pass lists the new tip, with a new count.
# kick_landing: a branchland pass now, so an approved tip lands without waiting for the next watch cycle (#955). It runs detached under
# its own single.sh lock, so a kick while a pass runs is a no-op; the review pass neither waits for it nor reads its output.
kick_landing() { [ -n "${SG_LAND_PAUSED:-}" ] && return 0
  # The kicked pass appends to a log the operator reads: its say-once WAIT, REFUSE and LANDED lines are often the only place
  # that line ever appears, since branchland keeps a marker per head. The review pass still neither waits for it nor reads it.
  ( bash "$here/single.sh" branchland bash "$here/branchland.sh" >> "${SG_STATE:-/tmp/sg-state}/branchland-kick.log" 2>&1 & ) >/dev/null 2>&1; }
review_one() { local sha=$1 br=$2 i=$3 key=$4 res=$5 c s n out
  c="$A/branchreview-tries-$key-${sha:0:9}"; s="$A/branchreview-stop-$key-${sha:0:9}"; n=$(cat "$c" 2>/dev/null || echo 0)
  local holder="branchreview-$key-$$-${BASHPID:-$$}" slot_err slot_rc
  slot_err=$(sg_caps take reviews "$holder" --pid "${BASHPID:-$$}" --target "$PWD" --wait "${SG_REVIEW_SLOT_WAIT:-600}" 2>&1 >/dev/null); slot_rc=$?
  [ -z "$slot_err" ] || echo "BRANCHREVIEW $slot_err" > "$res"
  [ $slot_rc = 0 ] || { echo "BRANCHREVIEW SKIPPED $br ${sha:0:9}: no review slot; the next pass reviews it" >> "$res"; return; }
  out=$(bash "$here/branchreview.sh" "$br" "$i" 2>&1 | tail -1); sg_caps release reviews "$holder"
  if [ "$(cat "$SG_STATE/review-$key.sha" 2>/dev/null)" = "$sha" ]; then rm -f "$c" "$s"; echo "BRANCHREVIEW $out" >> "$res"
    head -1 "$SG_STATE/review-$key.md" 2>/dev/null | tr -d '*' | grep -q '^VERDICT: APPROVE' && kick_landing
    return 0; fi
  n=$((n+1)); echo "$n" > "$c"; echo "BRANCHREVIEW FAILED $br ${sha:0:9} (try $n of $tries): $out" >> "$res"
  [ "$n" -ge "$tries" ] && [ ! -e "$s" ] && { touch "$s"
    echo "BRANCHREVIEW STOP $br: the review of ${sha:0:9} failed $n times ($(why_failed "$i")); a new push, or $ttl s, starts a new count" >> "$res"
    { echo "REVIEW_CHAIN STOP $br: the review of ${sha:0:9} failed $n times ($(why_failed "$i"))."; chain_summary; } > "$(sg_cq_status "$i" "mchain")" \
      || { echo "BRANCHREVIEW CHAINHEALTH $br: chainhealth.py failed (the model resolver is missing or unreadable)" >&2; : > "$A/chainhealth-failed"; }; }
}
# A card limit on the Review column (#2287): the pass starts no review while the column holds its maximum. The bound is
# the lower of the column limit and caps.reviews, the machine cap that also bounds Review. The count is this pass's single
# board read, through the same seam every dispatcher uses. An unread limit falls back to caps.reviews with one alert;
# only with no machine cap either is it a defect that starts no review.
reviews_cap=$(sg_caps value reviews --target "$SG_CHECKOUT" 2>/dev/null)
review_max=$(sg_board_limit_effective Review "$reviews_cap"); cm_rc=$?
[ "$cm_rc" -eq 0 ] || { echo "BRANCHREVIEW the Review column limit is unreadable; a defect, not no limit: no review starts this pass"; exit 0; }
case "$review_max" in ''|*[!0-9]*) ;; *)
  if rows=$(sg_board_rows 2>/dev/null); then
    review_n=$(printf '%s\n' "$rows" | awk -F '\t' '$2=="Review"||$2=="Code Review"{n++} END{print n+0}')
    [ "$review_n" -lt "$review_max" ] || { echo "BRANCHREVIEW column Review full: $review_n of $review_max card(s) in Review; no review starts this pass"; exit 0; }
  fi ;;
esac
done=0; now=$(date +%s); jobs_list=
while read -r sha br i; do
  [ -n "$br" ] && [ "$done" -lt "$max" ] || continue
  key=$(printf '%s' "$br" | tr '/' '_'); [ "$(cat "$SG_STATE/review-$key.sha" 2>/dev/null)" = "$sha" ] && continue
  # A review that keeps no sha at the tip failed. Its count is kept per tip (ADR-0134): after $tries failed tries at one tip the
  # branch stops, said once; a new tip has a new count, and the old tip's files go. A stop older than $ttl s is cleared with its count.
  c="$A/branchreview-tries-$key-${sha:0:9}"; s="$A/branchreview-stop-$key-${sha:0:9}"
  [ -e "$s" ] && [ $(( now - $(mtime "$s") )) -ge "$ttl" ] && rm -f "$c" "$s"
  n=$(cat "$c" 2>/dev/null || echo 0); [ "$n" -ge "$tries" ] && continue
  find "$A" -maxdepth 1 -name "branchreview-*-$key-*" ! -name "*-${sha:0:9}" -delete 2>/dev/null
  while [ "$(jobs -rp | wc -l | tr -d ' ')" -ge "$par" ]; do sleep 0.2; done
  res=$(mktemp "$A/branchreview-out-$key.XXXXXX"); jobs_list+="$res "; done=$((done+1))
  review_one "$sha" "$br" "$i" "$key" "$res" &
done <<EOT
$wb_list
EOT
wait
for res in $jobs_list; do cat "$res"; rm -f "$res"; done
[ -e "$A/chainhealth-failed" ] && { rm -f "$A/chainhealth-failed"; exit 1; }
exit 0
