#!/bin/bash
# usage: promote-main-pass.sh   The stg-to-main promotion pass (ADR-0145, ADR-0173): the watch cycle runs it once per cycle
# under `single.sh promote-main`. It asks for the complete CI tier on the stg tip, by the `promote` label on the one stg -> main
# pull request, and moves main to the proven sha by a fast-forward (promote-main.sh <sha>). The pull request is never merged:
# it carries the check, and main never gets a merge commit.
# R is the sha in $SG_STATE/promote-main-requested, the head the pull request answered when the label was sent. Every check
# read is of R, never of the stg tip, and by job name: the complete tier is the jobs the workflow starts on the label
# (promote-pass.sh --ci-complete-job-names). One pass does one thing:
#   1. main holds the stg tip: nothing, and a request left behind is cleared.
#   2. R is set: its complete tier answers. Only a run that started at or after the request time answers: a run of an
#      older label event on the same sha is no evidence of this request.
#        answered    R is the recorded red sha: that red was answered before a kill took the pass, so the request is
#                    cleared and nothing is read or written again.
#        in flight   a name has a run that has not completed, or no run yet inside the grace (300 s): nothing, up to
#                    SG_PROMOTE_MAIN_FLIGHT_S (3600) from the request; past it R is unproven.
#        green       every name's newest run ended success: promote-main.sh R. The request is cleared whatever it answers;
#                    a refusal is one alert, and a move clears the bug record and the hold. A push this process cannot
#                    authenticate (#1896) is no verdict on the tier and no refusal: one alert names the credential it could
#                    not reach, no hold is written, and the request stays, so the next pass that reaches the credential
#                    moves main by it. The pass exits 3 for it, on a code of its own, so a caller never reads it as the
#                    refusal above.
#        red         a job ran a step and failed (the base check's refinement): main stays. One bug is open at a time,
#                    `bug` and `ready-for-agent`, never `loop-bug`: the record holds its number, a later red comments on
#                    it, and a recorded bug that is closed gives a new one. The hold time and the red sha are recorded.
#        unproven    cancelled, timed out, never started, or past the flight bound: main stays, no bug, one alerts.log
#                    line per tip that names the cause. The hold time is recorded.
#   3. R is empty: main must be an ancestor of the stg tip (else one alert per main sha), and the tip must not be the sha of
#      the last red verdict; neither hold yields to a later green run. Then a stg tip whose own complete tier has already
#      completed green is promoted at once, with no wait for the window: the window names a tip so a burst earns one
#      request, and it never blocks a tip whose proof is already in, whichever rule opened the read; the request record is
#      a convenience, never the proof (ADR-0164). Otherwise promote-stg.sh opens or reuses the pull request, removes the
#      label, sends it, and writes R only after the label is accepted.
# The rule reads the first-parent chain of stg by committer date, with no local record:
#   quiet    now - committer date of the stg tip >= SG_PROMOTE_MAIN_QUIET_S (300)
#   ceiling  now - committer date of the oldest first-parent commit main lacks >= SG_PROMOTE_MAIN_MAX_S (14400)
# After a red, an unproven verdict or a refused move, the next request also waits SG_PROMOTE_MAIN_REARM_S (3600) from the
# hold time: the re-arm is its own wait, not the short quiet window. A red's hold names the sha it measured (#1889), the
# same sha promote-main-red records: it stops applying as soon as the stg tip is another sha, because the tier measured
# that content alone. An unproven or refused-move hold names no sha and keeps its own wait.
# A workflow that names no complete tier job proves nothing: one alert, no request, never green.
# The live copy is published from stg and never waits for this pass. SG_PROMOTE=off does nothing. Exit 0: nothing to do, a
# wait, a request or a move. Exit 1: main was held or a write failed; the reason is in promote.log or alerts.log. Exit 3:
# this process cannot reach the credential the main push needs; the request stays and the one line in promote.log names it.
d=$(cd "$(dirname "$0")" && pwd)
[ "${SG_PROMOTE:-on}" = off ] && exit 0           # a profile that delivers by another path promotes nothing: no fetch, no line
[ -n "${SG_STATE:-}" ] || { echo "promote-main-pass: SG_STATE is not set; the pass never reads the machine-wide default" >&2; exit 2; }
state=$SG_STATE; export SG_STATE; mkdir -p "$state/auto"; log=$state/promote.log
main=${SG_MAIN_BRANCH:-main}; stg=${SG_STG_BRANCH:-stg}   # both named by the profile (#1646)
remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}; tree=${SG_PROMOTE_MAIN_TREE:-$state/promote-main-tree}
export GIT_TERMINAL_PROMPT=0 SG_GH_PASS=${SG_GH_PASS:-promote-main}   # the pass the call log names (ADR-0277)
rec=$state/promote-main-requested; bugrec=$state/promote-main-bug; hold=$state/promote-main-hold; redrec=$state/promote-main-red
secs() { case "$1" in ''|*[!0-9]*) echo "$2";; *) echo "$1";; esac; }   # <value> <default>: a setting that is not a number is the default
quiet=$(secs "${SG_PROMOTE_MAIN_QUIET_S:-}" 300); ceiling=$(secs "${SG_PROMOTE_MAIN_MAX_S:-}" 14400)
rearm=$(secs "${SG_PROMOTE_MAIN_REARM_S:-}" 3600)
flight=$(secs "${SG_PROMOTE_MAIN_FLIGHT_S:-}" 3600); grace=$(secs "${SG_PROMOTE_MAIN_GRACE_S:-}" 300)
cred=${SG_PROMOTE_CREDENTIAL_HELPER:-osxkeychain}   # the fallback helper the fetch names (#2126)
own="!$d/promote-credential.sh"                    # the gh token, read by this process (#2126)
g() { /usr/bin/git -C "$tree" -c credential.helper= -c "credential.helper=$own" -c "credential.helper=$cred" "$@"; }
say() { echo "$(date -u +%FT%TZ) promote-main-pass: $*" >> "$log"; echo "promote-main-pass: $*"; }
put() { printf '%s\n' "$2" > "$1.$$" && mv -f "$1.$$" "$1"; }   # <file> <line>: one rename, so a kill leaves the old line or the new one
alert_once() {  # <key> <text>: one alerts.log line per key
  local m=$state/auto/promote-main-$1
  [ -e "$m" ] && return 0
  : > "$m"; printf '%s PROMOTE MAIN %s\n' "$(date '+%F %T')" "$2" >> "$state/alerts.log"; }
held() {  # [<sha>]: the hold time, and the sha a red measured; an unproven or refused-move hold names no sha
  local t; t=$(date +%s)
  if [ -n "${1:-}" ]; then put "$hold" "$(printf '%s\t%s' "$t" "$1")"; else put "$hold" "$t"; fi; }
. "$d/ghcall.sh"; . "$d/basecheck.sh"
[ -d "$tree/.git" ] || { mkdir -p "$tree" && g init -q . && g remote add origin "$remote"; } || exit 1
g remote set-url origin "$remote"
o=$(g fetch -q origin "+refs/heads/$main:refs/remotes/origin/$main" "+refs/heads/$stg:refs/remotes/origin/$stg" 2>&1) \
  || { echo "promote-main-pass: fetch failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
stip=$(g rev-parse "origin/$stg") || exit 1; mtip=$(g rev-parse "origin/$main") || exit 1
# 1. main holds the stg tip: nothing to prove, and a request for a tip main already holds asks for nothing.
if g merge-base --is-ancestor "$stip" "$mtip"; then rm -f "$rec"; exit 0; fi
# The complete tier's names, from the workflow of the checkout the profile names (as promote-pass.sh reads it).
case "${SG_CI_WORKFLOW:-}" in
  "") ci_file=${SG_CHECKOUT:-$SG_SQUAD}/.github/workflows/ci.yml;;
  */*) ci_file=$SG_CI_WORKFLOW;;
  *) ci_file=${SG_CHECKOUT:-$SG_SQUAD}/.github/workflows/$SG_CI_WORKFLOW;;
esac
names=$(bash "$d/promote-pass.sh" --ci-complete-job-names "$ci_file" 2>/dev/null | jq -Rnc '[inputs | select(. != "")]' 2>/dev/null)
names=${names:-[]}
if [ "$names" = "[]" ]; then
  alert_once nonames "$ci_file names no complete tier job, so nothing proves $stg: $main stays where it is, and the owner moves it"
fi
R=; at=; [ -s "$rec" ] && IFS=$'\t' read -r R at < "$rec"; at=$(secs "${at:-}" 0)
now=$(date +%s)
if [ -n "${R:-}" ]; then
  # 2. The request answers, on R alone.
  if g merge-base --is-ancestor "$R" "$mtip" 2>/dev/null; then rm -f "$rec"; exit 0; fi   # main already holds R
  # The red sha is recorded before the request is cleared, so a pass killed between the two leaves both behind. The record
  # says that red was already answered: the bug was filed or commented on, and the hold written, so this pass clears the
  # request alone and never comments on the same sha a second time.
  if [ "$(cat "$redrec" 2>/dev/null)" = "$R" ]; then rm -f "$rec"; say "$R red already answered: the request is cleared"; exit 0; fi
  # The one unproven set (promote-pass.sh --ci-unproven-conclusions): the workflow's guard reads the same one, so a run the
  # pass calls unproven is a run the guard starts the tier over.
  unp=$(bash "$d/promote-pass.sh" --ci-unproven-conclusions | jq -Rnc '[inputs | select(. != "")]')
  v=flight; why="its check runs are not read"
  cr=
  [ "$names" = "[]" ] || cr=$(gh_api "repos/$SG_REPO/commits/$R/check-runs?per_page=100&filter=all") || cr=
  if [ -n "$cr" ] && printf '%s' "$cr" | jq -e '.check_runs | type == "array"' >/dev/null 2>&1; then
    # Only the runs that started at or after the request answer it: a run an older label event started on the same sha
    # says nothing of this request. A run GitHub reports no start for keeps its created time; one with neither counts as
    # before the request, so a silence never reads as a proof.
    cr2=$(printf '%s' "$cr" | jq -c --argjson at "$at" \
      '.check_runs |= map(select((((.started_at // .created_at // "") | fromdateiso8601? // 0)) >= $at))' 2>/dev/null) \
      || cr2='{"check_runs":[]}'
    # One row per name: its state, then the detail. A skipped run is no evidence of the tier (another event's skip).
    rows=$(printf '%s' "$cr2" | jq -r --argjson names "$names" --argjson unp "$unp" '
      .check_runs as $r | $names[] as $n
      | [$r[] | select(.name == $n) | select(.conclusion != "skipped")] as $mine
      | ($mine | map(select(.status != "completed"))) as $open
      | if ($open | length) > 0 then "\($n)\tflight\t\($open[0].status)"
        elif ($mine | length) == 0 then "\($n)\tmissing\t"
        else ($mine | sort_by(.id) | last) as $l
          | if $l.conclusion == "success" then "\($n)\tgreen\t"
            elif any($unp[]; . == $l.conclusion) then "\($n)\tunproven\t\($l.conclusion)"
            else "\($n)\tred\t\($l.conclusion)" end
        end')
    pick() { printf '%s\n' "$rows" | awk -F'\t' -v s="$1" '$2 == s { printf "%s%s", sep, $1 ($3 != "" ? " (" $3 ")" : ""); sep = ", " }'; }
    fl=$(pick flight); ms=$(pick missing); up=$(pick unproven); rd=$(pick red)
    if [ -n "$fl" ]; then
      v=flight; why="$fl did not complete in ${flight}s of the request: no runner finished the job (a queued job is one no runner took)"
    elif [ -n "$ms" ] && [ $(( now - at )) -lt "$grace" ]; then
      v=flight; why="$ms has no run yet"
    elif [ -n "$rd" ]; then
      # A red is a red only when a job ran a step and failed (#1629): the newest run of each name is what the refinement reads.
      newest=$(printf '%s' "$cr2" | jq -c --argjson names "$names" '{check_runs: [.check_runs as $r | $names[] as $n
        | [$r[] | select(.name == $n) | select(.status == "completed") | select(.conclusion != "skipped")] | sort_by(.id) | last | select(. != null)]}')
      ref=$(sg_ci_refine "$newest" 2>/dev/null) || ref=
      case "$ref" in
        red\ *) v=red; why=${ref#red };;
        unproven\ *) v=unproven; why="$rd failed, and ${ref#unproven }";;
        *) v=unproven; why="$rd failed, and nothing measured the fail";;
      esac
    elif [ -n "$ms" ]; then
      v=unproven; why="the request started no run of $ms: the label event started nothing, or its run was skipped"
    elif [ -n "$up" ]; then
      v=unproven; why="$up measured nothing"
    else
      v=green
    fi
  elif [ "$names" = "[]" ]; then why="the workflow names no complete tier job"
  fi
  if [ "$v" = flight ]; then
    [ $(( now - at )) -le "$flight" ] && exit 0   # in flight: nothing, and nothing else waits on it
    v=unproven
  fi
  case "$v" in
    green)
      o=$(SG_MAIN_BRANCH=$main SG_STG_BRANCH=$stg bash "$d/promote-main.sh" "$R" 2>&1); rc=$?
      if [ $rc = 3 ]; then
        # This process cannot reach the credential the push needs, and promote-main.sh named it (#1896): the request
        # stays, and no hold is written, so the next pass that can reach the credential moves main to R by this same
        # request. The hook the CI event fires runs in a launchd service session, which carries no login keychain; the
        # watch cycle runs in the owner's session and reaches it. One alert names what could not be reached, and the
        # pass exits 3, so a caller that reads only the status never takes this for the refusal below.
        alert_once credential "$R: the complete tier is green and $main did not move: $(printf '%s' "$o" | tail -1)"
        say "$R not promoted: $(printf '%s' "$o" | tail -1)"; exit 3
      fi
      rm -f "$rec"   # cleared whatever the move answered: a later pass asks again by the rule
      if [ $rc = 0 ]; then
        rm -f "$bugrec" "$hold" "$redrec"
        say "$R promoted: the complete tier is green, $main is at ${R:0:9}"; exit 0
      fi
      held
      alert_once "refused-${R:0:12}" "the complete tier is green on ${R:0:9} and $main did not move: $(printf '%s' "$o" | tail -1)"
      say "$R not promoted: $(printf '%s' "$o" | tail -1)"; exit 1;;
    red)
      # One bug at a time. The record is written before the request is cleared, and a filing GitHub did not take leaves the
      # request in place, so the next pass files it: a red is never dropped in silence.
      n=$(cat "$bugrec" 2>/dev/null); n=$(secs "$n" "")
      if [ -n "$n" ]; then
        st=$(gh_api "repos/$SG_REPO/issues/$n" -q '.state // empty' 2>/dev/null | tr 'A-Z' 'a-z') || st=
        case "$st" in
          open) ;;
          closed) n=;;   # the recorded bug is closed: this red is a new bug
          *) say "$R red ($why): bug #$n could not be read; retried on the next pass"; exit 1;;
        esac
      fi
      text="The complete CI tier is red on the $stg tip $R: $why.

$main stays at $mtip. The loop does not ask for the complete tier on $R again: a new $stg tip is needed. Land the fix in the work branch; the promotion carries it to $stg, and the next request proves the new tip.

Filed by promote-main-pass.sh."
      body=$(mktemp) || exit 1; printf '%s\n' "$text" > "$body"
      if [ -n "$n" ]; then
        o=$(gh_send -X POST "repos/$SG_REPO/issues/$n/comments" -F "body=@$body" 2>&1); rc=$?
        rm -f "$body"
        [ $rc = 0 ] || { say "$R red ($why): the comment on bug #$n was refused; retried on the next pass"; exit 1; }
      else
        o=$(gh_send -X POST "repos/$SG_REPO/issues" -f "title=[SQUAD] the complete tier is red on $stg at ${R:0:9}: $why" \
          -F "body=@$body" -f "labels[]=bug" -f "labels[]=ready-for-agent" -q '.number // empty' 2>&1); rc=$?
        rm -f "$body"
        n=$(secs "$o" "")
        { [ $rc = 0 ] && [ -n "$n" ]; } || { say "$R red ($why): the bug could not be filed; retried on the next pass"; exit 1; }
        put "$bugrec" "$n"
        # Bug first (ADR-0148): the card goes to Todo through the board door. A refused add is the janitor's to repair.
        ${SG_BOARD_CMD:-bash "$d/board.sh"} --add "${SG_OWNER:-}" "${SG_PROJECT:-}" Todo "$n" >/dev/null 2>&1 || true
      fi
      held "$R"; put "$redrec" "$R"; rm -f "$rec"
      say "$R red: $why; $main stays at ${mtip:0:9}, bug #$n"; exit 1;;
    unproven)
      alert_once "unproven-${R:0:12}" "the complete tier proved nothing on $stg at ${R:0:9}: $why; $main stays at ${mtip:0:9}, and the next request waits ${rearm}s"
      held; rm -f "$rec"
      say "$R unproven: $why; $main stays at ${mtip:0:9}"; exit 1;;
  esac
  exit 0
fi
# 3. No request is open: the rule decides, on the first-parent chain of stg alone. A work commit a merge brought in is not
# on that chain, so an old work commit never fires the ceiling by itself.
[ "$names" = "[]" ] && exit 0
# tier_green <sha>: true when every complete-tier job of the sha has a completed, non-skipped, green run. It reads one row
# per name the way the dev pass does (promote-pass.sh ci_tier_green, one rule for the same check runs): the newest completed
# run that is not a skip answers for the name, and a name with no such run, or a last one that is not success, is no proof.
tier_green() {
  local cr
  cr=$(gh_api "repos/$SG_REPO/commits/$1/check-runs?per_page=100&filter=all" 2>/dev/null) || return 1
  printf '%s' "$cr" | jq -e --argjson names "$names" '
    .check_runs as $r
    | [ $names[] as $n
        | ([$r[] | select(.name == $n) | select(.status == "completed") | select(.conclusion != "skipped")] | sort_by(.id) | last) as $l
        | select(($l == null) or ($l.conclusion != "success"))
        | $n ] | length == 0
  ' >/dev/null 2>&1
}
oldest=$(g rev-list --first-parent "origin/$main..origin/$stg" | tail -n 1); [ -n "$oldest" ] || exit 0
tage=$(( now - $(g show -s --format=%ct "$stip") )); oage=$(( now - $(g show -s --format=%ct "$oldest") ))
win=0; { [ "$tage" -ge "$quiet" ] || [ "$oage" -ge "$ceiling" ]; } && win=1
green=-1   # not read yet: a closed window needs the proof to open, and an open one still reads it for the move below
[ "$win" = 1 ] || { tier_green "$stip" && green=1 || exit 0; }
hraw=$(cat "$hold" 2>/dev/null); h=$(secs "${hraw%%[!0-9]*}" 0)
if [ $(( now - h )) -lt "$rearm" ]; then   # a red, an unproven verdict or a refused move re-arms only after its own wait
  # A red's hold names the sha it measured (#1889): it stops applying as soon as the stg tip is another sha, and the quiet
  # window alone gates the new tip. An unproven or refused-move hold names no sha and keeps its own wait.
  hs=; case "$hraw" in *$'\t'*) hs=${hraw#*$'\t'};; esac
  [ -n "$hs" ] && [ "$hs" != "$stip" ] || exit 0
fi
if ! g merge-base --is-ancestor "$mtip" "$stip"; then
  alert_once "diverged-${mtip:0:12}" "$main at ${mtip:0:9} is not an ancestor of $stg at ${stip:0:9}: $main holds work $stg lacks, so no complete tier is requested; the owner resolves it"
  exit 0
fi
# The red verdict and the diverged main hold whatever a later run of the same sha says: they are read before the move below.
red=$(cat "$redrec" 2>/dev/null)
[ -n "$red" ] && [ "$red" = "$stip" ] && exit 0   # the tip of the last red verdict is not asked twice: a new tip is needed
[ "$green" = -1 ] && { tier_green "$stip" && green=1 || green=0; }
if [ "$green" = 1 ]; then
  # The event is the trigger and this pass is its reader: the complete tier of the tip already ended green, so main takes
  # the tip now instead of requesting a second run for it, whichever rule opened the read (ADR-0164).
  o=$(SG_MAIN_BRANCH=$main SG_STG_BRANCH=$stg bash "$d/promote-main.sh" "$stip" 2>&1); rc=$?
  if [ $rc = 3 ]; then   # the credential is out of this process's reach (#1896): no hold, the next pass that reaches it moves main
    alert_once credential "$stip: the complete tier is green and $main did not move: $(printf '%s' "$o" | tail -1)"
    say "$stip not promoted: $(printf '%s' "$o" | tail -1)"; exit 3
  fi
  if [ $rc = 0 ]; then
    rm -f "$bugrec" "$hold" "$redrec"
    say "$stip promoted: the complete tier is green on the $stg tip, $main is at ${stip:0:9}"; exit 0
  fi
  held
  alert_once "refused-tip-${stip:0:12}" "the complete tier is green on the $stg tip ${stip:0:9} and $main did not move: $(printf '%s' "$o" | tail -1)"
  say "$stip not promoted: $(printf '%s' "$o" | tail -1)"; exit 1
fi
# The pull request route is promote-stg.sh's alone. It compares the last red sha with the head sha the pull request answers,
# so a tip that moved under this read is still never asked twice, and it writes R only after the label is accepted.
o=$(SG_PROMOTE_OPEN_ONLY=1 SG_PROMOTE_OPEN_HEAD=$stg SG_PROMOTE_OPEN_BASE=$main SG_PROMOTE_OPEN_LABEL=promote \
  SG_PROMOTE_OPEN_RECORD=${rec##*/} SG_PROMOTE_OPEN_SKIP=$red bash "$d/promote-stg.sh" 2>&1); rc=$?
if [ $rc != 0 ]; then say "$stip not requested: $(printf '%s' "$o" | tail -1)"; exit 1; fi
[ -s "$rec" ] && say "$(cut -f1 "$rec") requested: the complete tier on the $stg tip ($([ "$tage" -ge "$quiet" ] && echo "quiet ${tage}s" || echo "ceiling ${oage}s"))"
exit 0
