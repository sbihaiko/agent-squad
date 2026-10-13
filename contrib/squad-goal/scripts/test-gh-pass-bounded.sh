#!/bin/bash
# Regression test for #45 (the loop stays under the GitHub user rate limit): a pass over N candidates costs a number of
# GitHub requests that does not grow with N. usage: bash test-gh-pass-bounded.sh
#
# The primary limit is per user and per hour (5000), and a pass that reads once per candidate spends it N times: on
# 2026-10-12 the landing pass hit the limit and every landing stopped (FORKCLOSE NO-PR-LIST). The two passes that read per
# candidate were forkclose.sh (an issue read, a `Blocked by` read and a comments read each) and watch.sh (a PR read for
# every PR it had not announced). Each now reads ONE aggregate for the whole set, so the count is the same for one
# candidate and for many. The proof is a stub `gh` that logs every request, run for N=1 and for N=6, and the two counts
# compared. Writes are not reads: landing an issue closes it, once per issue, and no read can avoid that.
# Nothing here touches the network, a model or a board.
for v in $(compgen -e | /usr/bin/grep -E '^(SG_|FC_)'); do unset "$v"; done
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git
MANY=6

# gh stub: one logged line per request, answers the three aggregate facts as GraphQL does and everything else as an empty object.
mkgh() { # mkgh <dir> -- the fixture's own files (pulls.json first-call.json issues.json numbers) are read from $FIX
  cat > "$1/gh" <<'J'
#!/bin/bash
a="${*//$'\n'/ }"
printf '%s\n' "$a" >> "$GHLOG"
case "$a" in
  *"pullRequests(states: OPEN"*)
    c=$(( $(cat "$FIX/pcalls" 2>/dev/null || echo 0) + 1 )); echo $c > "$FIX/pcalls"
    if [ "$c" = 1 ] && [ -f "$FIX/pulls-first.json" ]; then cat "$FIX/pulls-first.json"; else cat "$FIX/pulls.json"; fi;;
  *"issues(states: OPEN"*) cat "$FIX/issues.json";;
  *"issue(number:"*) cat "$FIX/numbers.json";;
  *"/comments"*) echo "TDD RED: stubbed";;
  *) echo '{}';;
esac
J
  chmod +x "$1/gh"; }

# --- forkclose: the eligible-branch pass ---------------------------------------------------------------------------------
# fork_pass <open numbers> <closed numbers>   one dry pass over a branch per number; prints the number of gh requests
fork_pass() {
  local op=$1 cl=$2 t; t=$(mktemp -d -p "$T"); local all="$op $cl" n
  mkdir -p "$t/s" "$t/st/auto" "$t/bin" "$t/co/runs/.hub" "$t/fork" "$t/fix"
  for f in forkclose.sh forkonly.sh gate-suites.sh ghread.py ghthrottle.py ghcall.sh; do [ -f "$d/$f" ] && cp "$d/$f" "$t/s/"; done
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$t/st" "$t/fork" "$t/co" > "$t/s/env.sh"
  g() { $G -C "$t/fork" -c user.email=a@b -c user.name=t "$@"; }
  g init -q -b local/next; echo 0 > "$t/fork/f"; g add f; g commit -q -m init
  for n in $all; do g checkout -q -b gui/$n local/next; echo $n > "$t/fork/f$n"; g add .; g commit -q -m "work $n"; done; g checkout -q local/next
  jq -n --arg ns "$op" '{items:[$ns|split(" ")[]|select(length>0)|tonumber|{n:.,st:"Code Complete",s:"open"}]}' > "$t/co/runs/.hub/hub-board.json"
  # the repository's only open PR names an issue no branch here belongs to, so it never holds a candidate back
  echo '{"data":{"repository":{"pullRequests":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":900,"title":"Refs #9000","body":"","headRefName":"x","headRefOid":"a","isDraft":false}]}}}}' > "$t/fix/pulls.json"
  jq -n --arg ns "$op" '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[$ns|split(" ")[]|select(length>0)|tonumber|{number:.,title:"t",body:"",state:"OPEN",labels:{nodes:[]},comments:{nodes:[{body:"TDD RED\nfailing line"}]}}]}}}}' > "$t/fix/issues.json"
  jq -n --arg ns "$cl" '{data:{repository:([$ns|split(" ")[]|select(length>0)|tonumber|{key:"i\(.)",value:{number:.,title:"t",body:"",state:"CLOSED",labels:{nodes:[]},comments:{nodes:[]}}}]|from_entries)}}' > "$t/fix/numbers.json"
  mkgh "$t/bin"
  : > "$t/gh.log"
  ( cd "$t" && PATH="$t/bin:$PATH" HOME="$t/home" GHLOG="$t/gh.log" FIX="$t/fix" FC_DRY=1 bash "$t/s/forkclose.sh" > "$t/out" 2>&1 )
  cp "$t/out" "$T/fork.out"; [ -n "${DBG:-}" ] && { echo "--- out:" >&2; cat "$t/out" >&2; echo "--- log:" >&2; cut -c1-120 "$t/gh.log" >&2; }; wc -l < "$t/gh.log" | tr -d ' '; }

nums() { seq -s ' ' 1 "$1"; }
c1=$(fork_pass "1" ""); o1=$(cat "$T/fork.out"); cm=$(fork_pass "$(nums $MANY)" ""); om=$(cat "$T/fork.out")
[ "$c1" = "$cm" ] && [ "$c1" -gt 0 ] && ok "forkclose, open issues: $c1 requests for 1 candidate, $cm for $MANY" || ko "forkclose, open issues: $c1 requests for 1 candidate, $cm for $MANY"
for n in $(nums $MANY); do case "$om" in *"would merge gui/$n "*) ;; *) ko "forkclose, open issues: gui/$n is not a candidate: $om"; break;; esac; done
case "$o1" in *"would merge gui/1 "*) ok "the one candidate still lands";; *) ko "the one candidate: $o1";; esac
c1=$(fork_pass "" "1"); cm=$(fork_pass "" "$(nums $MANY)"); om=$(cat "$T/fork.out")
[ "$c1" = "$cm" ] && [ "$c1" -gt 0 ] && ok "forkclose, closed issues: $c1 requests for 1 candidate, $cm for $MANY" || ko "forkclose, closed issues: $c1 requests for 1 candidate, $cm for $MANY"
for n in $(nums $MANY); do case "$om" in *"would merge gui/$n "*) ;; *) ko "forkclose, closed issues: gui/$n is not a candidate: $om"; break;; esac; done
c1=$(fork_pass "1" "2"); cm=$(fork_pass "1 2 3" "4 5 6"); om=$(cat "$T/fork.out")
[ "$c1" = "$cm" ] && ok "forkclose, open and closed: $c1 requests for 2 candidates, $cm for $MANY" || ko "forkclose, open and closed: $c1 requests for 2 candidates, $cm for $MANY"
case "$om" in *"would merge gui/6 "*) ok "a closed issue's fork half is still a candidate";; *) ko "closed candidate: $om";; esac

# --- watch: the cycle's PR announcements ---------------------------------------------------------------------------------
# watch_pass <n prs> [VAR=val..]   one cycle; the first read sees no PR and every later read sees n, so each one is news.
watch_pass() {
  local n=$1; shift; local t; t=$(mktemp -d -p "$T"); local S=$t/scripts ST=$t/st B=$t/bin
  mkdir -p "$S" "$ST/cq" "$B" "$t/co" "$t/fork" "$t/fix"
  for f in watch.sh single.sh models.json ghread.py ghthrottle.py ghcall.sh; do [ -f "$d/$f" ] && cp "$d/$f" "$S/"; done
  printf 'export SG_STATE=%s SG_STATE_BASE=%s SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_CHECKOUT=%s SG_FORK_DIR=%s SG_FRONTIER_LABEL=ready SG_BUG_LABEL=bug\n' "$ST" "$t/cache" "$t/co" "$t/fork" > "$S/env.sh"
  echo '{"data":{"repository":{"pullRequests":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}' > "$t/fix/pulls-first.json"
  jq -n --argjson n "$n" '{data:{repository:{pullRequests:{pageInfo:{hasNextPage:false},nodes:[range(0;$n)|{number:(100+.),title:"work \(.)",body:"Refs #\(500+.)",headRefName:"gui/\(500+.)",headRefOid:"a",isDraft:false}]}}}}' > "$t/fix/pulls.json"
  mkgh "$B"; printf '#!/bin/bash\necho "$@" >> "%s"\n[ "$1" -gt 100 ] 2>/dev/null && kill -TERM "$PPID"\nexit 0\n' "$t/sleeps" > "$B/sleep"; chmod +x "$B/sleep"
  : > "$ST/active-runs.txt"; : > "$t/gh.log"; : > "$t/sleeps"; cp "$t/sleeps" "$T/watch.sleeps"
  [ -n "${SEED_QUOTA:-}" ] && echo '{"core":{"remaining":'"$SEED_QUOTA"',"limit":5000,"reset":'"$(( $(date +%s) + 3000 ))"',"at":0}}' > "$ST/gh-ratelimit.json"
  for s in retryfix autohunt autopilot heartbeat wtclean janitor forkclose autofrontier autobug; do printf '#!/bin/bash\nexit 0\n' > "$S/$s.sh"; chmod +x "$S/$s.sh"; done
  for p in childcc forkrescue forkstrand reconcile; do : > "$S/$p.py"; done
  # the cycle's last step ends the run after one pass, so the case does not wait for the real cadence
  printf '#!/bin/bash\n[ -n "%s" ] || kill -TERM "$(/bin/ps -o ppid= -p "$PPID" | tr -d " ")" 2>/dev/null\nexit 0\n' "${SEED_QUOTA:-}" > "$S/vendor-guard.sh"   # a seeded quota runs on to the stretch, which ends the run itself
  env "$@" PATH="$B:$PATH" HOME="$t/home" GHLOG="$t/gh.log" FIX="$t/fix" bash "$S/watch.sh" > "$t/watch.out" 2>&1 & local p=$! i=0
  while kill -0 "$p" 2>/dev/null && [ $i -lt 300 ]; do /bin/sleep 0.1; i=$((i+1)); done
  kill -TERM "$p" 2>/dev/null; wait "$p" 2>/dev/null
  cp "$t/watch.out" "$T/watch.out"; cp "$t/sleeps" "$T/watch.sleeps"; ls "$ST/cq" | wc -l | tr -d ' ' > "$T/watch.cq"; wc -l < "$t/gh.log" | tr -d ' '; }

c1=$(watch_pass 1 SG_GH_AGGREGATE_WINDOW=0); cm=$(watch_pass $MANY SG_GH_AGGREGATE_WINDOW=0); om=$(cat "$T/watch.out"); cq=$(cat "$T/watch.cq")
[ "$c1" = "$cm" ] && [ "$c1" -gt 0 ] && ok "watch, no snapshot: $c1 requests for 1 new PR, $cm for $MANY" || ko "watch, no snapshot: $c1 requests for 1 new PR, $cm for $MANY"
[ "$(printf '%s\n' "$om" | /usr/bin/grep -c '^NEW PR #')" = "$MANY" ] && ok "every new PR is announced" || ko "announcements: $om"
[ "$cq" = "$MANY" ] && ok "and queued for the issue it names" || ko "queue: $cq files"
c1=$(watch_pass 1); cm=$(watch_pass $MANY)
[ "$c1" = "$cm" ] && [ "$c1" = 1 ] && ok "watch, snapshot: one request for the cycle, $c1 for 1 PR and $cm for $MANY" || ko "watch, snapshot: $c1 for 1 PR, $cm for $MANY (one expected)"

# the adaptive cycle: a healthy quota adds no wait, and 2 percent left (100 of 5000) waits 630 s more on a 90 s cycle
mkdir -p "$T/q"; ( . "$d/ghcall.sh"; export SG_STATE=$T/q
  [ "$(gh_stretch 90)" = 0 ] && echo "ok   a healthy quota adds no wait to the cycle" || echo "FAIL a healthy quota lengthened the cycle" ) | tee "$T/q.out"; /usr/bin/grep -q '^FAIL' "$T/q.out" && fail=1
SEED_QUOTA=100 watch_pass 1 > /dev/null; /usr/bin/grep -qx 630 "$T/watch.sleeps" && ok "under 20 percent of the limit left the cycle waits longer" || ko "no longer wait with 2 percent left: $(cat "$T/watch.sleeps")"

# --- the budget file only shrinks ----------------------------------------------------------------------------------------
for s in forkclose.sh watch.sh; do
  /usr/bin/grep -qE "^$s " "$d/gh-direct-calls.txt" && ko "$s is still on the migration budget" || ok "$s is off the migration budget"
  # the checker reads a call in command position at a line's start, so this scan also reads an indented one and a `! gh`
  /usr/bin/grep -nE '(^|[;|&(`{!]|then|do|else)[[:space:]]*gh[[:space:]]+(api|pr|issue)\b' "$d/$s" | /usr/bin/grep -vE '^[0-9]+:[[:space:]]*#' | /usr/bin/grep -q . \
    && ko "$s still calls gh itself" || ok "$s calls GitHub only through ghcall.sh"
done
total=$(sed 's/#.*//' "$d/gh-direct-calls.txt" | awk 'NF==2{s+=$2} END{print s+0}')
[ "$total" -le 89 ] && ok "the budget is $total reaches, at most the 89 left once these two callers moved" || ko "the budget grew to $total reaches"
out=$(bash "$d/check-gh-interface.sh" "$d/../../.." 2>&1); case "$out" in *SHRINK*) ko "a stale line stands: $out";; *) ok "no line of the budget is stale";; esac
exit $fail
