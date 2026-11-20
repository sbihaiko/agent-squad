#!/bin/bash
# usage: . "$(dirname "$0")/ghcall.sh"      then: gh_api <route> [-q <jq filter>] | gh_get <gh api args> | gh_send <gh api args>
#
# The loop's one door to GitHub (#8, ADR-0277; the read half landed as #1893). Every script reaches GitHub through one
# of the three functions below and never through `gh` itself, and `check-gh-interface.sh` refuses a live script that
# does otherwise, naming the script and the route.
#
# A read has three outcomes and they are never mixed (`ghread.py` decides them): the answer on stdout with exit 0, a
# refusal with exit 2 whose line carries GitHub's own message and request id, and a failure before GitHub reached a
# verdict with exit 1. `gh api` writes its error body to *stdout* and exits non-zero and ignores `-q` on a failed
# request, so a bare `o=$(gh api …)` used to read the words of a rate limit where a JSON list belonged; the exit code
# is the contract here because a script cannot see a Python return value.
#
# A write is the half with the worse consequence, and it is the reason `gh_send` exists beside `gh_api`: a refused read
# looks like an answer, but a refused write looks like *success*. `gh api -X PATCH … && touch moved` marks the card
# moved while GitHub refused it, and the loop reads that marker as state. `gh_send` returns 0 only when GitHub accepted
# the write, so `if gh_send -X PATCH …; then touch moved; fi` cannot be fooled. It is not a throttle -- the token
# bucket and the cooldown are a separate clause of ADR-0277 (#4) -- it is the outcome contract, which is what a caller
# can be wrong about.
#
# A read that may be answered from the snapshot (#6) has its own door, `gh_snapshot`, beside `gh_api` and never instead of
# it: `gh_api` is always live, and it is what a read that decides a write calls -- is the branch still eligible, is the base
# red, is the work branch still open. `gh_snapshot` goes through ghthrottle.py, which answers a repeated read inside
# `SG_GH_CACHE_WINDOW` seconds from `$SG_STATE/gh-cache` with no request, and which reads live every route that is not one
# of the few it classifies as cheap or keyed -- so a gating route handed to `gh_snapshot` by mistake is still live.
#
# The spend is visible (ADR-0277): every door appends one line to $SG_STATE/api.log -- timestamp, transport, route, cache hit or
# miss, outcome and the spending pass (`SG_GH_PASS`, which each pass script exports for itself; a caller that names none is
# `unattributed`). gh_api and gh_snapshot log through ghread.py / ghthrottle.py; gh_get and gh_send reach `gh` themselves, so they
# log through `ghread.py --log`, with the outcome the call had. health.sh reads the log (ghspend.py).
#
# `-q`/`--jq` is the caller's projection and is applied HERE, on the answer, with `jq -r`, so the transport always
# speaks raw JSON: a filtering transport cannot tell a refusal apart from an answer, and `gh` cannot either.

ghcall_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${SG_GHREAD:=python3 $ghcall_here/ghread.py}"   # the interface module: one place to point at another build of it
: "${SG_GHSNAP:=python3 $ghcall_here/ghthrottle.py}"   # the throttled, snapshotting reader behind gh_snapshot

# _gh_refused <body>: 0 when the body is GitHub's own refusal shape: a REST message beside a status, or a GraphQL `errors[]`
# list with no `data` (a rate limit comes back that way, with gh exit 0 or 1). Any body that is not JSON, or is JSON without
# either shape, is not a refusal -- `jq -e` exits non-zero for both.
# _gh_log_as <cache> <outcome> <gh api args…>: one call-log line for a call this file makes itself. The route is the one argument
# that is neither a flag nor the value of one; the transport is REST, or GraphQL for the `graphql` route. A write logs cache `write`.
_gh_log_as() {
  local cache=$1 outcome=$2 route="" a transport=REST; shift 2
  while [ $# -gt 0 ]; do
    case "$1" in
      -H|--header|-X|--method|-f|--raw-field|-F|--field|-q|--jq|--input|--cache|-t|--template|--hostname) shift 2;;
      -*) shift;;
      *) route=$1; break;;
    esac
  done
  [ -n "$route" ] || return 0
  [ "$route" = graphql ] && transport=GraphQL
  $SG_GHREAD --log "$transport" "$route" "$cache" "$outcome" >/dev/null 2>&1 || true
}
_gh_log() { _gh_log_as miss "$@"; }   # a read: the snapshot answers it or it is a miss

# _gh_child_merge <gh args…>: 0 when a child session asks to merge a PR, by `pr merge` or by `PUT .../pulls/N/merge`. A child
# session is a call the engine started (`SG_CHILD_SESSION`, exported by the call environment): the loop merges, so the door
# refuses the merge there and names the refusal. The loop's own mergers run outside a call and never carry the marker.
_gh_child_merge() {
  [ -n "${SG_CHILD_SESSION:-}" ] || return 1
  local words=() put=0 a
  for a in "$@"; do
    case "$a" in
      -X=PUT|--method=PUT) put=1;;
      PUT) put=1;;
      -*) ;;
      *) words+=("$a");;
    esac
  done
  [ "${words[0]:-}" = pr ] && [ "${words[1]:-}" = merge ] && return 0
  [ "$put" -eq 1 ] && [[ " ${words[*]} " =~ /pulls/[0-9]+/merge ]] && return 0
  return 1
}

_gh_child_merge_line() {
  printf 'refused: CHILD-MERGE-REFUSED: a child session never merges a PR; the loop merges it once review and CI pass\n'
}

_gh_refused() {
  printf '%s' "$1" | jq -e 'type == "object" and (((.message | type == "string") and has("status"))
    or ((.errors | type == "array") and (.errors | length > 0) and (.data == null)))' >/dev/null 2>&1
}

_gh_refusal_line() {   # the one line a caller logs when GitHub refused: GitHub's own words, and nothing invented
  printf 'refused: GitHub refused: %s\n' "$(printf '%s' "$1" | jq -r '.message // .errors[0].message // "no message"' 2>/dev/null)"
}

# sg_loop_build   The loop's build: the sha of the checkout it publishes from. A SQUAD-NOT-STARTABLE marker names the
# build that wrote it, and a marker written by another build no longer stops a dispatch (owner answer B2, ADR-0134): a
# landing may be the fix, and a count from before it is not evidence about the code that runs now. Empty when the
# checkout cannot be read; a marker then keeps its stop, because an unknown build proves nothing.
sg_loop_build() {
  local sha
  sha=$(/usr/bin/git -C "${SG_CHECKOUT:-.}" rev-parse --short=12 HEAD 2>/dev/null) || true
  [ -n "$sha" ] && printf '%s\n' "$sha"
}

# gh_loop_stop <issues.json>   A defect in the loop's own machinery is fixed outside the loop (#596, ADR-0146): while an
# open issue carries a defect in the dispatch path, the landing path or the loop's infrastructure, a dispatch pass
# dispatches nothing, because the loop cannot deliver its own fix. Those are `loop-bug:dispatch`, `loop-bug:landing` and
# `loop-bug:infra`. Any open loop-bug, bare or prefixed, also drains the loop (#1708): the pass hands the list it already
# read to `watchd.sh loopbug`, the one label rule and the one writer of the drain, and dispatches nothing. The live runs
# and the held gate finish, then the loop is off until no loop-bug is open. A list that rule cannot read asks for nothing.
# The stop is read from the open-issue list the pass already reads, never from a marker, so closing the issue
# resumes dispatch at the next pass with no other action.
# Returns 0 and prints the one line when such an issue is present, 1 when there is none. The line is published to the loop
# log and to the issue's line on the board, not only to stdout. `loop-bug:landing` stops the landing as well (gh_land_stop, #641).
gh_loop_stop() {
  local j=${1:?gh_loop_stop: an issues file is required} n line f lb
  n=$(python3 "$ghcall_here/drainpolicy.py" dispatch < "$j" 2>/dev/null); rc=$?
  [ "$rc" = 2 ] && return 1
  [ "$rc" = 0 ] || n=""
  # SG_LOOPBUG_CMD is the seam a suite names; a copy of the scripts with no watchd.sh asks for no drain and keeps the halt.
  if [ -n "${SG_LOOPBUG_CMD:-}" ] || [ -f "$ghcall_here/watchd.sh" ]; then
    lb=$(${SG_LOOPBUG_CMD:-bash "$ghcall_here/watchd.sh" loopbug} "$j" 2>/dev/null) && [ -z "$n" ] && n=$lb
  fi
  # The stop is also one marker the hub reads without a query (#956): the page shows Loop:OFF while dispatch is held, and the
  # marker goes with the label, so closing the issue reads ON again at the same pass.
  [ -n "$n" ] || { rm -f "${SG_STATE:?gh_loop_stop: no state directory}"/auto/loop-stop-* 2>/dev/null; return 1; }
  mkdir -p "$SG_STATE/auto" 2>/dev/null; : > "$SG_STATE/auto/loop-stop-$n"
  line="LOOP STOP: #$n is a loop defect; the fix lands outside the loop"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "${SG_STATE:?gh_loop_stop: no state directory}/cycle.log"   # the loop log
  if declare -F sg_cq_status >/dev/null 2>&1; then   # the board's one status line for the issue, edited in place by cqflush (#328)
    f=$(sg_cq_status "$n" loopstop 2>/dev/null) || f=""
    [ -z "$f" ] || { mkdir -p "${f%/*}" 2>/dev/null; printf '%s\n' "$line" > "$f" 2>/dev/null; }
  fi
  return 0
}

# gh_land_stop   An open `loop-bug` stops intake only: a wave whose children are all approved and gated still lands (#641,
# ADR-0146 decision 3). An issue labelled `loop-bug:landing` names a defect in the landing path (wave.sh land, branchland), so
# no landing starts while it is open. One read of the open issues through the aggregate door; a read that fails or is refused
# proves nothing and stops nothing. Returns 0 and prints the one line (also to the loop log) when such an issue is open.
gh_land_stop() {
  local o n line
  o=$(gh_aggregate "repos/$SG_REPO/issues?state=open") || return 1
  n=$(printf '%s' "$o" | python3 "$ghcall_here/drainpolicy.py" landing 2>/dev/null); rc=$?
  [ "$rc" = 2 ] && return 1
  [ "$rc" = 0 ] || n=""
  [ -n "$n" ] || return 1
  line="LOOP STOP: #$n is a loop defect in the landing path; no wave lands until it closes"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "${SG_STATE:?gh_land_stop: no state directory}/cycle.log"
  return 0
}

# gh_dispatch_hold   An interim limit until the review moves into the run (#562): childcc writes the count of finished
# children that wait for review to $SG_STATE/childcc-waiting on every pass. When SG_DISPATCH_MAX_WAITING is set (a
# non-negative integer) and the count is greater than it, a dispatch pass dispatches nothing, because a new run only adds
# children that outlive the wave age cap unreviewed. Returns 0 and prints the one line on a hold, 1 when the pass may go on.
# No cap, a cap that is not a number, a missing or unreadable count, or a count older than 30 min (a stale childcc) never
# holds: the loop is never blocked on a file it cannot trust.
# The nightly full suite decides the same way (#704): a red record at $SG_STATE/nightly-result (nightly.sh) is a report,
# never a hold (owner, 2026-11-05; ADR-0153): each landing carries its own local gate and the promotion still needs its
# full proof, so the pass goes on and one note names the test, once per record.
# A bounded record is an attempt the suite never finished: the tip is unproven, never red, so it holds nothing and the pass
# names the stopped leg on one line, once per record (#1183, ADR-0164). A missing or unreadable record never holds, by the
# same rule.
# The promotion proof reserves the machine it measures (#739): promote-pass.sh writes $SG_STATE/promote-reserve while it
# holds the gate lock, and a dispatch pass starts nothing while a marker younger than SERIAL_HOLD_SECONDS stands, so the
# suite is not measured against the next wave landing. A marker past that bound is a proof that died and never holds.

# sg_nightly_mode   A nightly record's mode field: `pass`, `fail`, `fail:signal-<n>` for a red leg a signal also stopped,
# or `bounded:<signal|exit>-<n>` for a leg that ended without naming a failing test (#1183). Anything else is a value the
# loop cannot trust.
sg_nightly_mode() {
  case "$1" in pass|fail|bounded:window) return 0;; esac   # bounded:window: an hour that closed with no run (ADR-0153 rule 1b)
  case "$1" in fail:signal-[0-9]*|bounded:signal-[0-9]*|bounded:exit-[0-9]*) ;; *) return 1;; esac
  case "${1##*-}" in ''|*[!0-9]*) return 1;; esac   # the number after signal- or exit-, and nothing else
  return 0
}
# sg_nightly_red   A mode a failing test made red: `fail`, or `fail:signal-<n>` when a signal also stopped that leg. A red
# names a defect whatever else stopped it; the tag only carries the stop reason.
sg_nightly_red() { case "$1" in fail|fail:signal-[0-9]*) return 0;; esac; return 1; }

# sg_nightly_state   What a nightly result record says about delivery. The record is one line, `<epoch> TAB
# <pass|fail|bounded> TAB <sha> TAB <parallel> TAB <serial> TAB <ids>` (nightly.sh, #704, #1183), where `<ids>` is ONE JSON
# array of strings, `[]` when none: a pytest node id may carry a space, so the ids are never split at whitespace. A line the
# loop cannot trust is no reading and holds nothing (ADR-0153): not six fields, a time that is not a number, an empty sha,
# a result or a mode outside the three (a red leg a signal also stopped is `fail:signal-<n>`, still a red), ids that are not
# one array of strings, or a result that contradicts the modes. The record is read whole: a file that carries anything after
# its one line is not that record either, because a first line alone never proves the file whole. A bounded result names no
# failing test, because the leg that was stopped measured none: a bounded record that carries an id is no reading. The hub
# drops the same lines, so the two readers never disagree about which record they are looking at. Answers 0 and prints the
# failing ids when the record is RED; 2 and prints the two legs when it is BOUNDED, an attempt the suite never finished, so
# it holds nothing (ADR-0164); 3 when it is PASS, a reading with no delivery fact to say (#2005); 1 when there is no reading.
sg_nightly_state() {
  local rec nf ts res sha par ser ids ids_read
  [ -r "$1" ] || return 1
  rec=$(cat -- "$1" 2>/dev/null; printf x) || return 1   # `x` keeps the trailing newlines a substitution would strip
  rec=${rec%x}; rec=${rec%$'\n'}                          # the one line may end in a newline, and nothing else follows
  case "$rec" in ''|*$'\n'*) return 1;; esac
  nf=$(printf '%s\n' "$rec" | awk -F'\t' '{print NF}')
  [ "$nf" = 6 ] || return 1
  ts=$(printf '%s\n' "$rec" | cut -f1); res=$(printf '%s\n' "$rec" | cut -f2); sha=$(printf '%s\n' "$rec" | cut -f3)
  par=$(printf '%s\n' "$rec" | cut -f4); ser=$(printf '%s\n' "$rec" | cut -f5); ids=$(printf '%s\n' "$rec" | cut -f6)
  case "$ts" in ''|*[!0-9]*) return 1;; esac
  [ -n "$sha" ] || return 1
  sg_nightly_mode "$par" || return 1
  sg_nightly_mode "$ser" || return 1
  # The result follows its modes: pass only when both finished green, fail when a leg named tests, and bounded when a leg
  # was stopped and none was red. A result its modes contradict is no reading.
  case "$res" in
    pass) [ "$par" = pass ] && [ "$ser" = pass ] || return 1;;
    fail) { sg_nightly_red "$par" || sg_nightly_red "$ser"; } || return 1;;
    bounded) { ! sg_nightly_red "$par" && ! sg_nightly_red "$ser"; } || return 1
             { [ "$par" != pass ] || [ "$ser" != pass ]; } || return 1;;
    *) return 1;;
  esac
  # The ids are one JSON array of strings, read as that array and never as words: a node id may carry a space.
  ids_read=$(printf '%s' "$ids" | python3 -c 'import json,sys
try: v=json.load(sys.stdin)
except Exception: sys.exit(1)
if type(v) is not list or not all(type(x) is str for x in v): sys.exit(1)
print(", ".join(v) if v else "")' 2>/dev/null) || return 1
  if [ "$res" = fail ]; then printf '%s\n' "${ids_read:-no test named}"; return 0; fi
  # (#2005) A pass measured both modes green: there is nothing to say, and it is never a stopped note. It answers 3, a
  # reading that carries no delivery fact, so the caller says nothing and the pass goes on.
  [ "$res" = pass ] && return 3
  # A bounded leg measured nothing, so it names no failing test. An id here contradicts the mode, and it is no reading.
  [ -z "$ids_read" ] || return 1
  printf 'parallel %s, serial %s\n' "$par" "$ser"
  return 2
}

# sg_obligation_red <dir>   The settled-red proof obligations (nightly.sh, spec 1163), one line per settled entry in
# `<epoch> TAB <repository> TAB <tree sha> TAB <base> TAB <scope> TAB <checks> TAB <green|red> TAB <run>`. It prints
# `<epoch> TAB <sha> TAB <checks> TAB <run>` for every readable red record and returns 0 when it printed at least one; a
# green or an unreadable record holds nothing (ADR-0153, the same rule sg_nightly_red applies to its record). The caller
# compares the sha with the tip: a red is credited to the tree it measured, never to another (ADR-0163). The epoch lets a
# pass that cannot read the tip hold only for a red from this nightly period.
sg_obligation_red() {
  local dir=$1 f line nf ts repo sha base scope checks res run n=0
  [ -d "$dir" ] || return 1
  for f in "$dir"/*; do
    [ -f "$f" ] || continue
    line=$(cat -- "$f" 2>/dev/null) || continue
    case "$line" in ''|*$'\n'*) continue;; esac
    nf=$(printf '%s' "$line" | awk -F'\t' '{print NF}')
    [ "$nf" = 8 ] || continue
    IFS=$'\t' read -r ts repo sha base scope checks res run <<<"$line"
    case "$ts" in ''|*[!0-9]*) continue;; esac
    [ -n "$sha" ] && [ "$res" = red ] || continue
    printf '%s\t%s\t%s\t%s\n' "$ts" "$sha" "$checks" "${run:--}"; n=1
  done
  [ "$n" = 1 ]
}

gh_dispatch_hold() {
  local m=${SG_DISPATCH_MAX_WAITING:-} f="${SG_STATE:?gh_dispatch_hold: no state directory}/childcc-waiting" n t line nids nrc r ts o osha ochecks orun
  local rsha rhash key seen
  # The promotion proof reserves the machine it measures (#739): promote-pass.sh writes $SG_STATE/promote-reserve while it
  # holds the gate lock, and it removes the file on exit, INT and TERM. A marker older than the lock's own bound is a proof
  # that died, and it never holds, by the same rule as the other guards here.
  # (#1975, #2527, ADR-0153) The nightly owns the machine in its hour. No run starts from SG_NIGHTLY_DRAIN_MINUTES before
  # the hour, so the live runs end before it opens, and none starts while the nightly runs. The hold ends when the day has
  # its nightly or its missed record, and once the hour has less time left than the suite needs (SG_NIGHTLY_NEED_MINUTES):
  # the dispatch never idles for a nightly that can no longer finish. The hold ends with the hour in any case.
  local nhour=$((10#${SG_NIGHTLY_HOUR:-04})) np nwhy= nh nm ndrain=$((10#${SG_NIGHTLY_DRAIN_MINUTES:-40})) nneed=$((10#${SG_NIGHTLY_NEED_MINUTES:-45})) nday nbv nbvs nvkey
  if [ -z "${SG_NIGHTLY_OFF:-}" ]; then
    nh=$((10#$(TZ=America/Sao_Paulo date +%H))); nm=$(TZ=America/Sao_Paulo date +%M:%S); nm=$((10#${nm%%:*}))
    [ "$ndrain" -gt 59 ] && ndrain=59
    nday=$(date +%F)
    if [ "$nh" -eq $(( (nhour + 23) % 24 )) ] && [ "$nm" -ge $((60 - ndrain)) ] && [ "$ndrain" -gt 0 ]; then
      [ "$nhour" -eq 0 ] && nday=$(date -v+1d +%F 2>/dev/null || date -d tomorrow +%F 2>/dev/null)
      [ -e "$SG_STATE/auto/nightly-$nday" ] || [ -e "$SG_STATE/auto/nightly-missed-$nday" ] \
        || nwhy="the nightly drain is open: the window opens at $(printf '%02d' "$nhour"):00"
    elif [ "$nh" -eq "$nhour" ]; then
      np=$(cat "$SG_STATE/locks/nightly/pid" 2>/dev/null)
      if [ -n "$np" ] && kill -0 "$np" 2>/dev/null; then nwhy="the nightly runs (pid $np)"
      elif [ ! -e "$SG_STATE/auto/nightly-$nday" ] && [ ! -e "$SG_STATE/auto/nightly-missed-$nday" ] && [ $((60 - nm)) -ge "$nneed" ]; then
        nwhy="the nightly window is open"; fi
    fi
  fi
  # (#2593) A nightly that ended bounded proved nothing, so its next window holds no dispatch: the runs keep the machine and
  # the nightly shares it under the gate lock. The verdict names its local half in the fourth field, `bounded` for a suite a
  # bound or a signal stopped (nightly.sh, ADR-0164). A pass, a fail or a verdict the loop cannot read holds the window as
  # before. The reason is one line, said once per verdict -- keyed by the whole record -- so a bounded verdict that stands
  # until the next nightly does not bury cycle.log.
  if [ -n "$nwhy" ] && [ -r "$SG_STATE/nightly-verdict" ]; then
    IFS=$'\t' read -r _ _ nbvs nbv _ _ < "$SG_STATE/nightly-verdict" 2>/dev/null
    if [ "$nbv" = bounded ]; then
      nvkey=$(cksum < "$SG_STATE/nightly-verdict" 2>/dev/null) || nvkey=
      seen="$SG_STATE/nightly-bounded-release-noted"
      if [ "$(cat -- "$seen" 2>/dev/null)" != "$nvkey" ]; then
        if printf '%s\n' "$nvkey" > "$seen.$$" 2>/dev/null; then mv -f "$seen.$$" "$seen" 2>/dev/null || rm -f "$seen.$$"; fi
        line="$(date -u '+%F %T') DISPATCH NOTE: the last nightly at ${nbvs:0:7} ended bounded: its next window holds no dispatch, and the nightly runs beside the runs"
        printf '%s\n' "$line"; printf '%s\n' "$line" >> "$SG_STATE/cycle.log"
      fi
      nwhy=
    fi
  fi
  if [ -n "$nwhy" ]; then
    line="$(date -u '+%F %T') DISPATCH HOLD: $nwhy; no new run this pass"
    printf '%s\n' "$line"; printf '%s\n' "$line" >> "$SG_STATE/cycle.log"
    return 0
  fi
  if [ -r "$SG_STATE/promote-reserve" ]; then
    read -r ts _ < "$SG_STATE/promote-reserve" 2>/dev/null
    case "$ts" in
      ''|*[!0-9]*) ;;
      *) if [ $(( $(date +%s) - ts )) -lt "${SERIAL_HOLD_SECONDS:-7200}" ]; then
           line="$(date -u '+%F %T') DISPATCH HOLD: the promotion proof holds the machine (since $(date -r "$SG_STATE/promote-reserve" +%T 2>/dev/null || echo "$ts")); no new run this pass"
           printf '%s\n' "$line"; printf '%s\n' "$line" >> "$SG_STATE/cycle.log"
           return 0
         fi;;
    esac
  fi
  # (spec 1163) A settled-red proof obligation holds the tip it measured, like any red: the nightly settled a defect at
  # that tree. A red credited to another tree holds nothing, by the same rule as a stale nightly record (#747). A pass that
  # cannot read the tip holds only for a red from this nightly period: a red from weeks ago at a long-dead tree never stops
  # every dispatch, and the nightly prunes it by name (SG_NIGHTLY_PERIOD_SECONDS, one day by default).
  if o=$(sg_obligation_red "$SG_STATE/obligation-settled"); then
    tip=
    if [ -n "${SG_CHECKOUT:-}" ]; then
      tip=$(/usr/bin/git -C "$SG_CHECKOUT" rev-parse --verify -q origin/dev 2>/dev/null) \
        || tip=$(/usr/bin/git -C "$SG_CHECKOUT" rev-parse --verify -q HEAD 2>/dev/null) || tip=
    fi
    while IFS=$'\t' read -r ots osha ochecks orun; do
      [ -n "$osha" ] || continue
      if [ -n "$tip" ]; then
        [ "${osha:0:12}" != "${tip:0:12}" ] && continue
      elif [ $(( $(date +%s) - ots )) -gt "${SG_NIGHTLY_PERIOD_SECONDS:-86400}" ]; then
        continue
      fi
      line="$(date -u '+%F %T') DISPATCH HOLD: a red proof obligation at ${osha:0:12} (${ochecks:-no check named}, run ${orun:--}) measured a defect; no new run at that tree"
      printf '%s\n' "$line"; printf '%s\n' "$line" >> "$SG_STATE/cycle.log"; return 0
    done <<< "$o"
  fi
  # (ADR-0153 rule 1d) A red of the nightly's GitHub half is a red like a local one: a report, never a hold. One note names
  # the sha the GitHub half tested, once per verdict, and the pass goes on. A local red says its own note below.
  if [ -r "$SG_STATE/nightly-verdict" ]; then
    local vres vloc vgh vgsha
    IFS=$'\t' read -r _ vres _ vloc vgh vgsha < "$SG_STATE/nightly-verdict" 2>/dev/null
    if [ "$vres" = fail ] && [ "$vgh" = fail ] && [ "$vloc" != fail ]; then
      seen="$SG_STATE/nightly-gh-red-noted"
      if [ "$(cat -- "$seen" 2>/dev/null)" != "$vgsha" ]; then
        if printf '%s\n' "$vgsha" > "$seen.$$" 2>/dev/null; then mv -f "$seen.$$" "$seen" 2>/dev/null || rm -f "$seen.$$"; fi
        line="$(date -u '+%F %T') DISPATCH NOTE: the nightly GitHub half is red at ${vgsha:0:7}; the pass goes on, and the promotion still needs its proof"
        printf '%s\n' "$line"; printf '%s\n' "$line" >> "$SG_STATE/cycle.log"
        return 1
      fi
    fi
  fi
  nids=$(sg_nightly_state "$SG_STATE/nightly-result"); nrc=$?
  if [ "$nrc" = 2 ]; then
    # (#1183, ADR-0164) The suite never finished: it names no failing test, so the tip is unproven and never red. One line
    # says which leg was stopped and how, and the pass goes on. The line is said once per record -- keyed by the whole
    # record the line is made of -- because a bounded record stands until the next nightly, and one line per poll would bury
    # cycle.log with the same sentence all day. A sha and the record's mtime in whole seconds are no identity: two records
    # that land in the same second read as one record, and the second note went silent (#2004).
    rsha=$(cut -f3 "$SG_STATE/nightly-result" 2>/dev/null)
    rhash=$(cksum < "$SG_STATE/nightly-result" 2>/dev/null) || rhash=
    key="$rsha ${rhash:-no-content}"; seen="$SG_STATE/nightly-bounded-noted"
    [ "$(cat -- "$seen" 2>/dev/null)" = "$key" ] && return 1
    if printf '%s\n' "$key" > "$seen.$$" 2>/dev/null; then mv -f "$seen.$$" "$seen" 2>/dev/null || rm -f "$seen.$$"; fi
    line="$(date -u '+%F %T') DISPATCH NOTE: the nightly full suite at ${rsha:0:7} was stopped before it measured anything (${nids}); the tip is unproven, not red, so the pass goes on"
    printf '%s\n' "$line"; printf '%s\n' "$line" >> "$SG_STATE/cycle.log"
    return 1
  fi
  if [ "$nrc" = 0 ]; then
    # (#747, ADR-0153) The record is red for the sha it ran at, not for whatever the tip became, and it is a report either
    # way: the pass goes on. A tip the nightly has not seen is unproven, one line names both shas, and the next nightly
    # proves the new tip. A red at the tip itself names the test it measured; each landing still carries its own local
    # gate, and the promotion still needs its full proof. The line is said once for the record and the tip it is read
    # against -- the two facts the sentence is made of -- so a red that stands until the next nightly does not bury
    # cycle.log. A sha and the record's mtime in whole seconds are no identity: a record that lands in the same second as
    # an earlier one read as the same record, and the note of the second one went silent (#2004).
    rsha=$(cut -f3 "$SG_STATE/nightly-result" 2>/dev/null); tip=
    if [ -n "${SG_CHECKOUT:-}" ]; then
      tip=$(/usr/bin/git -C "$SG_CHECKOUT" rev-parse --verify -q origin/dev 2>/dev/null) \
        || tip=$(/usr/bin/git -C "$SG_CHECKOUT" rev-parse --verify -q HEAD 2>/dev/null) || tip=
    fi
    rhash=$(cksum < "$SG_STATE/nightly-result" 2>/dev/null) || rhash=
    key="$rsha ${rhash:-no-content} $tip"; seen="$SG_STATE/nightly-red-noted"
    [ "$(cat -- "$seen" 2>/dev/null)" = "$key" ] && return 1
    if [ -n "$tip" ] && [ "${rsha:0:12}" != "${tip:0:12}" ]; then
      line="$(date -u '+%F %T') DISPATCH NOTE: the nightly red names $rsha, not the dev tip $tip; the tip is unproven, the pass goes on, and the next nightly proves it"
    else
      line="$(date -u '+%F %T') DISPATCH NOTE: the nightly full suite is red at ${rsha:0:7} (${nids:-no test named}); the pass goes on, and the promotion still needs its proof"
    fi
    if printf '%s\n' "$key" > "$seen.$$" 2>/dev/null; then mv -f "$seen.$$" "$seen" 2>/dev/null || rm -f "$seen.$$"; fi
    printf '%s\n' "$line"; printf '%s\n' "$line" >> "$SG_STATE/cycle.log"
    return 1
  fi
  [ -n "$m" ] || return 1
  case "$m" in *[!0-9]*) echo "$(date -u '+%F %T') DISPATCH HOLD: SG_DISPATCH_MAX_WAITING='$m' is not a non-negative integer; no limit"; return 1;; esac
  [ -r "$f" ] || return 1
  n=$(head -n 1 "$f" 2>/dev/null | tr -d '[:space:]')
  case "$n" in ''|*[!0-9]*) return 1;; esac
  t=$(date -r "$f" +%s 2>/dev/null) || return 1
  if [ $(( $(date +%s) - t )) -gt 1800 ]; then
    echo "$(date -u '+%F %T') DISPATCH HOLD skipped: the childcc count is older than 30 min (stale childcc); no limit this pass"; return 1
  fi
  [ $((10#$n)) -gt $((10#$m)) ] || return 1
  line="$(date -u '+%F %T') DISPATCH HOLD: $((10#$n)) children wait for review (cap $((10#$m))); no new run this pass"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$SG_STATE/cycle.log"
  return 0
}

# gh_disk_stop   A full disk fails every run a pass starts, and the owner's machine with it (#615). Under SG_MIN_FREE_GB
# GiB free (default 20) on the volume of $SG_STATE or of $TMPDIR, a dispatch pass dispatches nothing. Returns 0 and prints
# the one line on a stop, 1 when the pass may go on. A floor that is not a non-negative integer is the default.
gh_disk_stop() {
  local m=${SG_MIN_FREE_GB:-20} p k line
  case "$m" in ''|*[!0-9]*) m=20;; esac
  for p in "${SG_STATE:?gh_disk_stop: no state directory}" "${TMPDIR:-/tmp}"; do
    k=$(df -Pk "$p" 2>/dev/null | awk 'NR==2{print $4}')
    case "$k" in ''|*[!0-9]*) continue;; esac
    [ $((k / 1048576)) -lt $((10#$m)) ] || continue
    line="DISK STOP: $(df -Pk "$p" | awk 'NR==2{print $6}') has $((k / 1048576)) GiB free, under the floor of $((10#$m)) GiB; nothing dispatched"
    printf '%s\n' "$line"
    printf '%s\n' "$line" >> "$SG_STATE/cycle.log"
    gh_disk_top | tee -a "$SG_STATE/cycle.log"
    return 0
  done
  return 1
}

# gh_disk_top   Where the space went (#1581): one DISK TOP line for each of the three largest entries under the temp
# directory and under the state directory, so a stop explains itself. Reads only; a directory that cannot be read prints nothing.
gh_disk_top() {
  local d
  for d in "${TMPDIR:-/tmp}" "${PYTEST_DEBUG_TEMPROOT:-}" "$SG_STATE"; do
    [ -n "$d" ] && [ -d "$d" ] || continue
    ( cd "$d" 2>/dev/null && perl -e 'alarm 10; exec @ARGV' du -sk -- * .[!.]* 2>/dev/null | sort -rn | head -n 3 ) |   # bounded: 10 s a directory
      while IFS=$'\t' read -r k n; do printf 'DISK TOP %s/%s: %s MiB\n' "${d%/}" "$n" $((k / 1024)); done
  done
  return 0
}

# gh_bug_first   A bug goes first (#616): autobug writes the startable bugs it left without a run to $SG_STATE/bugs-waiting.
# While that count is over 0 and fresh (30 min), the frontier pass dispatches nothing. Returns 0 and prints the one line on a
# hold, 1 when the pass may go on. A missing, unreadable or stale count never holds.
gh_bug_first() {
  local f="${SG_STATE:?gh_bug_first: no state directory}/bugs-waiting" n t line
  [ -r "$f" ] || return 1
  n=$(head -n 1 "$f" 2>/dev/null | tr -d '[:space:]')
  case "$n" in ''|*[!0-9]*) return 1;; esac
  [ $((10#$n)) -gt 0 ] || return 1
  t=$(date -r "$f" +%s 2>/dev/null) || return 1
  [ $(( $(date +%s) - t )) -le 1800 ] || return 1
  line="BUG FIRST: $((10#$n)) startable bug(s) wait for a run; no feature run this pass"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$SG_STATE/cycle.log"
  return 0
}

# gh_api <route> [-q <jq filter>]   A read. Answers on stdout with exit 0; a refusal is exit 2 and its line is not an
# answer; a failure is exit 1. A caller that only ever learns from an answer keeps what it already knew.
gh_api() {
  local route=${1:?gh_api: a route is required}; shift || true
  _gh_read "$SG_GHREAD" "$route" "$@"
}

# gh_pages <route> [-q <jq filter>]   A list read, every page of it (#437). The route names `per_page=N`; the door asks `&page=1`,
# `&page=2`, ... through `gh_api` (so each page keeps the three outcomes and the call log) and stops at the first page shorter
# than N. The pages are one JSON array to the filter. A page that is refused or fails ends the read with that page's outcome.
gh_pages() {
  local route=${1:?gh_pages: a route is required}; shift || true
  local q="" size p=1 o rc all="[]" n
  [ "${1:-}" = "-q" ] && q=${2:-}
  size=$(printf '%s' "$route" | sed -n 's/.*per_page=\([0-9][0-9]*\).*/\1/p'); size=${size:-30}
  while :; do
    o=$(gh_api "$route&page=$p"); rc=$?
    [ "$rc" -eq 0 ] || { printf '%s\n' "$o"; return "$rc"; }
    all=$(printf '%s\n%s' "$all" "$o" | jq -s 'add') || return 1
    n=$(printf '%s' "$o" | jq 'length') || return 1
    [ "$n" -ge "$size" ] || break
    p=$((p+1))
  done
  if [ -n "$q" ]; then printf '%s' "$all" | jq -r "$q"; else printf '%s' "$all"; fi
}

# gh_snapshot <route> [-q <jq filter>]   A read that may repeat: the same three outcomes as gh_api, and a repeat inside
# the window is answered from the snapshot. Never for a read that decides a write (see above).
gh_snapshot() {
  local route=${1:?gh_snapshot: a route is required}; shift || true
  _gh_read "$SG_GHSNAP" "$route" "$@"
}

# gh_aggregate <route> [-q <jq filter>]   The open-issue or open-work-branch list, from the snapshot while it is younger than
# `SG_GH_AGGREGATE_WINDOW` (60 to 120 s, default 90): the same three outcomes as `gh_api`, and a repeat inside the window
# sends no request (#45). It is the caller's explicit choice, for a pass that reads the list ONCE for its whole set and
# tolerates an answer a window old -- the work-branch list a dispatch pass reads once for its whole set. A read that
# decides one write about one issue stays `gh_api`.
gh_aggregate() {
  local route=${1:?gh_aggregate: a route is required}; shift || true
  _gh_read "$SG_GHSNAP --aggregate" "$route" "$@"
}

# gh_stretch <base seconds>   The extra seconds a loop cycle of that length waits, 0 while the quota is healthy (#45): once
# less than 20 percent of a pool is left, the cycle gets longer. Never fails: a reading that cannot be had is no stretch.
gh_stretch() { local o; o=$($SG_GHSNAP --stretch "${1:?gh_stretch: a base is required}" 2>/dev/null) && [ "$o" -ge 0 ] 2>/dev/null && echo "$o" || echo 0; }

_gh_read() {   # _gh_read <reader command> <route> [-q <jq filter>]
  local reader=$1 route=$2; shift 2
  local q=""
  [ "${1:-}" = "-q" ] && q=${2:-}
  local o rc
  o=$($reader "$route"); rc=$?
  if [ "$rc" -eq 0 ]; then
    if [ -n "$q" ]; then printf '%s' "$o" | jq -r "$q"; else printf '%s' "$o"; fi
    return 0
  fi
  printf '%s\n' "$o"
  return "$rc"
}

# gh_get <gh api args…>   A read GitHub answers with something other than JSON (`-H "Accept: …diff"`). The body is
# passed through verbatim, and the three outcomes are the same: a caller can tell an empty diff from a refusal.
gh_get() {
  local o rc
  if _gh_child_merge "$@"; then _gh_log refused "$@"; _gh_child_merge_line; return 2; fi
  o=$(gh api "$@" 2>/dev/null); rc=$?
  if [ "$rc" -eq 0 ] && ! _gh_refused "$o"; then _gh_log answer "$@"; printf '%s' "$o"; return 0; fi
  if _gh_refused "$o"; then _gh_log refused "$@"; _gh_refusal_line "$o"; return 2; fi
  _gh_log failed "$@"
  printf 'failed: no verdict (gh exited %s)\n' "$rc"
  return 1
}

# gh_send <gh api args…> [-q <jq filter>]   A write. Returns 0 only when GitHub accepted it; a refusal is exit 2 with
# GitHub's own message, a local failure exit 1. `-q` projects the answer the same way `gh_api` does.
gh_send() {
  local q="" args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      -q|--jq) q=${2:-}; shift 2;;
      *) args+=("$1"); shift;;
    esac
  done
  local o rc
  if _gh_child_merge "${args[@]}"; then _gh_log_as write refused "${args[@]}"; _gh_child_merge_line; return 2; fi
  o=$(gh api "${args[@]}" 2>/dev/null); rc=$?
  if _gh_refused "$o"; then _gh_log_as write refused "${args[@]}"; _gh_refusal_line "$o"; return 2; fi
  if [ "$rc" -ne 0 ]; then _gh_log_as write failed "${args[@]}"; printf 'failed: the write reached no verdict (gh exited %s)\n' "$rc"; return 1; fi
  _gh_log_as write done "${args[@]}"
  if [ -n "$q" ]; then printf '%s' "$o" | jq -r "$q" || return 1; else printf '%s' "$o"; fi
  return 0
}
