#!/bin/bash
# Regression test for #45 (the loop stays under the GitHub user rate limit): a pass over N candidates costs a number of
# GitHub requests that does not grow with N. usage: bash test-gh-pass-bounded.sh
#
# The primary limit is per user and per hour (5000), and a pass that reads once per candidate spends it N times: on
# 2026-10-12 the landing pass hit the limit and every landing stopped. The frontier pass reads ONE open-issue list for the
# whole set, so the count is the same for one candidate and for many. The proof is a stub `gh` that logs every request, run
# for N=1 and for N=6, and the two counts compared (#361: the pull-request readers are gone, so the passes it once bounded
# are not here any more). Writes are not reads: landing an issue closes it, once per issue, and no read can avoid that.
# Nothing here touches the network, a model or a board.
for v in $(compgen -e | /usr/bin/grep -E '^(SG_|FC_)'); do unset "$v"; done
export SG_MIN_FREE_GB=0   # the dispatchers pin their disk floor: the real volume must not stop a fixture pass (#792); a case that tests the stop sets its own
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git
MANY=6

# --- the adaptive cycle --------------------------------------------------------------------------------------------------
# A healthy quota adds no wait; under 20 percent of the limit the cycle waits longer (gh_stretch, the door's own reading).
q=$(mktemp -d -p "$T")
( . "$d/ghcall.sh"; export SG_STATE=$q
  [ "$(gh_stretch 90)" = 0 ] && echo "ok   a healthy quota adds no wait to the cycle" || echo "FAIL a healthy quota lengthened the cycle" ) | tee "$T/q.out"
/usr/bin/grep -q '^FAIL' "$T/q.out" && fail=1
echo '{"core":{"remaining":100,"limit":5000,"reset":'"$(( $(date +%s) + 3000 ))"',"at":0}}' > "$q/gh-ratelimit.json"
( . "$d/ghcall.sh"; export SG_STATE=$q
  [ "$(gh_stretch 90)" = 630 ] && echo "ok   under 20 percent of the limit left the cycle waits longer" || echo "FAIL no longer wait with 2 percent left: $(gh_stretch 90)" ) | tee "$T/q2.out"
/usr/bin/grep -q '^FAIL' "$T/q2.out" && fail=1

# --- autofrontier: the frontier's candidate walk (#383) ------------------------------------------------------------------
# frontier_pass <n issues>   one dry pass (AF_DRY) of the real autofrontier.sh, stack.sh and startability.sh over n open
# issues with no blocker and no open work branch; prints the number of gh requests. The pass read each candidate's comments
# (sg_not_startable) and the issue again (stack.sh), every 120 s: 915 requests an hour, and GitHub refused the rest.
frontier_pass() {
  local n=$1 t; t=$(mktemp -d -p "$T")
  mkdir -p "$t/s" "$t/st/auto" "$t/bin" "$t/co/docs/squad/workflows" "$t/fix"
  for f in autofrontier.sh stack.sh startability.sh skiplabels.sh workbranches.sh ghcall.sh drainpolicy.py ghread.py ghthrottle.py single.sh cluster.py address.sh issue_ledger.py; do cp "$d/$f" "$t/s/"; done
  printf 'export SG_REPO=x/y SG_STATE=%s SG_BASE=main SG_CHECKOUT=%s SG_FRONTIER_LABEL=ready-for-agent SG_BUG_LABEL=bug\n' "$t/st" "$t/co" > "$t/s/env.sh"
  printf '{"run":{"max_parallel":20},"nodes":[{"name":"root","kind":"loop","nodes":[{"name":"route","kind":"dispatch","max_children":24,"max_parallel":2}]}]}' > "$t/co/docs/squad/workflows/dynamic.graph.json"
  printf 'caps:\n  runs: 10\n' > "$t/co/docs/squad/defaults.yaml"
  : > "$t/st/active-runs.txt"
  # each carries SG_FRONTIER_LABEL: the frontier takes only an issue that wears it (#485)
  jq -n --argjson n "$n" '[range(1;$n+1)|{number:.,title:"work \(.)",body:"Work \(.).",labels:[{name:"wave:01"},{name:"ready-for-agent"}]}]' > "$t/fix/rest.json"
  jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:.labels},blockedBy:{nodes:[]},comments:{nodes:[]}}]}}}}' "$t/fix/rest.json" > "$t/fix/issues.json"
  cat > "$t/bin/gh" <<'J'
#!/bin/bash
a="${*//$'\n'/ }"
printf '%s\n' "$a" >> "$GHLOG"
q=""; p=""; prev=""
for x in "$@"; do [ "$prev" = "-q" ] && q="$x"; case "$x" in repos/*) p="$x";; esac; prev="$x"; done
case "$a" in
  *"issues(states: OPEN"*) cat "$FIX/issues.json"; exit 0;;
  *"issue(number:"*) n=$(printf '%s' "$a" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+$' | head -1)
    jq --argjson n "$n" '{data:{repository:{("i\($n)"):(.data.repository.issues.nodes[]|select(.number==$n))}}}' "$FIX/issues.json"; exit 0;;
esac
case "$p" in
  */issues?state=open*) f=$FIX/rest.json;;
  */issues/*/comments*|*/pulls?*) echo '[]'; exit 0;;
  */issues/*) n=${p##*/}; jq --argjson n "$n" '.[]|select(.number==$n)' "$FIX/rest.json"; exit 0;;
  *) echo '{}'; exit 0;;
esac
if [ -n "$q" ]; then jq -r "$q" "$f"; else cat "$f"; fi
J
  chmod +x "$t/bin/gh"; : > "$t/gh.log"
  cat > "$t/s/issue_state.py" <<'PY'
import json, os, sys   # the seam's door, stubbed (#550): one board read a pass, every card in Todo
if sys.argv[1:2] not in ([], ["--go"]): sys.exit(1)   # the plain read and the go-list read (#800) both answer
for i in json.load(open(os.environ["FIX"] + "/rest.json")): print(f"{i['number']}\tTodo")
PY
  ( cd "$t" && env SG_SINGLE_AUTOFRONTIER=1 AF_DRY=1 SG_GH_AGGREGATE_WINDOW=0 SG_SQUAD="$(cd "$d/../../.." && pwd)" SQUAD_CAPS_DIR="$t/caps" \
      PATH="$t/bin:$PATH" HOME="$t/home" GHLOG="$t/gh.log" FIX="$t/fix" bash "$t/s/autofrontier.sh" > "$t/out" 2>&1 )
  cp "$t/out" "$T/frontier.out"; [ -n "${DBG:-}" ] && { echo "--- out:" >&2; cat "$t/out" >&2; echo "--- log:" >&2; cut -c1-120 "$t/gh.log" >&2; }; wc -l < "$t/gh.log" | tr -d ' '; }
c1=$(frontier_pass 1); o1=$(cat "$T/frontier.out"); cm=$(frontier_pass $MANY); om=$(cat "$T/frontier.out")
[ "$c1" = "$cm" ] && [ "$c1" -gt 0 ] && ok "autofrontier, one pass: $c1 requests for 1 open issue, $cm for $MANY" || ko "autofrontier, one pass: $c1 requests for 1 open issue, $cm for $MANY"
case "$o1" in *"FRONTIER would dispatch #1 "*) ok "the one open issue is picked";; *) ko "the one open issue: $o1";; esac
case "$om" in *"FRONTIER would dispatch #1 #2 #3 #4 #5 #6 "*) ok "every open issue is picked";; *) ko "the $MANY open issues: $om";; esac

# --- the budget file only shrinks ----------------------------------------------------------------------------------------
for s in startability.sh watch.sh; do
  /usr/bin/grep -qE "^$s " "$d/gh-direct-calls.txt" && ko "$s is still on the migration budget" || ok "$s is off the migration budget"
  # the checker reads a call in command position at a line's start, so this scan also reads an indented one and a `! gh`
  /usr/bin/grep -nE '(^|[;|&(`{!]|then|do|else)[[:space:]]*gh[[:space:]]+(api|pr|issue)\b' "$d/$s" | /usr/bin/grep -vE '^[0-9]+:[[:space:]]*#' | /usr/bin/grep -q . \
    && ko "$s still calls gh itself" || ok "$s calls GitHub only through ghcall.sh"
done
total=$(sed 's/#.*//' "$d/gh-direct-calls.txt" | awk 'NF==2{s+=$2} END{print s+0}')
[ "$total" -le 23 ] && ok "the budget is $total reaches, at most the 23 left after #361" || ko "the budget grew to $total reaches"
out=$(bash "$d/check-gh-interface.sh" "$d/../../.." 2>&1); case "$out" in *SHRINK*) ko "a stale line stands: $out";; *) ok "no line of the budget is stale";; esac
exit $fail
