#!/bin/bash
# seconds: 2.95
# Regression test for autofrontier.sh: the pass reads the run's own envelope, from the file the engine will run, and the
# request it builds carries those numbers. The spend it names was a hardcoded 8 while the dispatch node of the graph the
# engine runs allows 4, so every child request was over the limit: the router read `spend_outside_budget` in its notes,
# left the spend out, and the run spent its turns on the disagreement (#57). The graph is resolved by id the way
# squad/workflows.py resolves it -- the project's docs/squad/workflows/<id>.graph.json, else the seed the engine falls back
# to -- never by hand-built path, which read the seed dir and found nothing there, so no bound applied at all.
# Offline and hermetic: stub gh (fixtures through jq), stub stack.sh, startability.sh, mkreq.sh and launch.py, its own temp
# SG_STATE and HOME.
# usage: bash test-autofrontier-envelope.sh [path/to/autofrontier.sh]
src=${1:-$(dirname "$0")/autofrontier.sh}; T=$(mktemp -d); fail=0
repo=$(cd "$(dirname "$src")/../../.." && pwd)   # SG_SQUAD: squad/caps.py, and the seed squad/workflows.py falls back to
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
if [ ! -f "$src" ]; then echo "FAIL the request carries the run's envelope: $src not found"; rm -rf "$T"; exit 1; fi
for v in $(compgen -e | $G '^SG_'); do unset "$v"; done
export SG_MIN_FREE_GB=0   # the dispatchers pin their disk floor: the real volume must not stop a fixture pass (#792); a case that tests the stop sets its own
export SG_NIGHTLY_OFF=1   # the dispatch hold reads the wall clock: the nightly hour must not hold a fixture pass (#2397); a case that tests the hold sets its own hour
export SG_SQUAD=$repo SQUAD_CAPS_DIR=$T/caps
mkdir -p "$T/bin" "$T/scripts"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
q=""; p=""; prev=""
for a in "$@"; do
  [ "$prev" = "-q" ] && q="$a"
  case "$a" in repos/*) p="$a";; esac
  prev="$a"
done
case "$*" in *"issues(states: OPEN"*)   # the open-issue list is one GraphQL request through the door (#383): the REST fixture in its shape
  jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:(.labels//[])},blockedBy:{nodes:[]},comments:{nodes:[]}}]}}}}' "$GH_ISSUES"; exit 0;; esac
case "$p" in
  */pulls?state=open*)    f=$GH_OPEN;;
  */pulls?state=closed*)  f=$GH_CLOSED;;
  */issues?state=open*)   f=$GH_ISSUES;;
  */issues/*)             f=$GH_ISSUE1; [ -f "$GH_ISSUEDIR/${p##*/}.json" ] && f=$GH_ISSUEDIR/${p##*/}.json;;
  *) exit 1;;
esac
if [ -n "$q" ]; then jq -r "$q" "$f"; else cat "$f"; fi
GH
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\necho READY\n' > "$T/scripts/stack.sh"; chmod +x "$T/scripts/stack.sh"
cp "$(dirname "$src")/startability.sh" "$T/scripts/startability-real.sh"; cp "$(dirname "$src")/skiplabels.sh" "$T/scripts/skiplabels.sh"   # the one skip set (#1593) and the shared reads (#428): the stub below keeps the real file's helpers and fixes only what this suite pins
printf '#!/bin/bash\n. "$(dirname "$0")/startability-real.sh"\nsg_fork_only() { return 1; }\nsg_not_startable() { return 1; }\nsg_ledger_held() { return 1; }\nsg_ac_refused() { return 1; }\nsg_fork_preamble() { :; }\nsg_fork_branch() { echo "gui/$1"; }\nsg_fork_branch_pattern() { echo "gui/%%n"; }\nsg_issue_worktree_note() { :; }\nsg_board_rows() { if [ -n "${GH_BOARD:-}" ]; then cat "$GH_BOARD"; else jq -r ".[]|\\"\\(.number)\\tTodo\\"" "$GH_ISSUES"; fi; }\n' > "$T/scripts/startability.sh"   # every fixture card sits in Todo (#550) unless a case writes its own rows
printf '#!/bin/bash\n[ "$1" = --paths ] && exec bash "$(dirname "$0")/mkreq-real.sh" "$@"\necho x >> "$MKLOG"; cp "$2" "$MKLOG.body"; echo "$HOME/req.txt"\n' > "$T/scripts/mkreq.sh"
cp "$(dirname "$src")/mkreq.sh" "$T/scripts/mkreq-real.sh"; cp "$(dirname "$src")"/{models.json,req-checklist.txt} "$T/scripts/"
printf '%s\n' 'open(__import__("os").environ["LAUNCHLOG"], "a").write("launch\n")' > "$T/scripts/launch.py"
chmod +x "$T/scripts/mkreq.sh"
printf '#!/bin/bash\necho "$*" >> "$BOARDLOG"\n' > "$T/board.sh"; chmod +x "$T/board.sh"
cp "$src" "$T/scripts/autofrontier.sh"; cp "$(dirname "$src")"/{ghcall.sh,drainpolicy.py,ghread.py,ghthrottle.py,workbranches.sh,issue_ledger.py,liveholds.py,siblings.py,cluster.py,loopbug.sh,basered.sh,columnlimit.sh} "$T/scripts/"   # the pass reads the open-issue list through the door
# The issues an open work branch names (#361, workbranches.sh): the real read is `git ls-remote` against github.com, which no
# fixture may reach, so the stand-in answers WORK_ISSUES (none unless a case sets it) (as in test-autofrontier-one-run.sh).
printf '#!/bin/bash\nsg_work_issues() { echo "${WORK_ISSUES:-}"; }\n' > "$T/scripts/workbranches.sh"
scene() { # <name>: fresh state, its own env.sh; no graph written here -- each case writes the one it needs
  d=$T/$1; mkdir -p "$d/st/req" "$d/st/cq" "$d/co/docs/squad/workflows" "$d/co/runs" "$d/home"
  : > "$d/gh.log"; : > "$d/st/active-runs.txt"; : > "$d/mk.log"; : > "$d/launch.log"; : > "$d/board.log"
  cat > "$T/scripts/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$d/st; SG_CHECKOUT=$d/co; SG_BASE=main
SG_FRONTIER_LABEL=frontier; SG_BUG_LABEL=bug; SG_CODING_RULES=rules; BOARD=$T/board.sh
sg_cq_file() { printf '%s/cq/o__r#%s\n' "$SG_STATE" "$1"; }
E
  export SG_GH_AGGREGATE_WINDOW=0   # a case rewrites the list between passes: no snapshot answers the second one
  export HOME=$d/home SG_STATE=$d/st SG_CHECKOUT=$d/co SG_REPO=o/r SG_FRONTIER_LABEL=frontier MKLOG=$d/mk.log LAUNCHLOG=$d/launch.log BOARDLOG=$d/board.log
  export GH_ISSUES=$d/issues.json GH_OPEN=$d/open.json GH_CLOSED=$d/closed.json GH_ISSUE1=$d/issue1.json GH_ISSUEDIR=$d/issue; mkdir -p "$GH_ISSUEDIR"
  printf 'caps:\n  runs: 1\n' > "$d/co/docs/squad/defaults.yaml"   # one free run slot: a pass with more slots deals the children over several runs (#728), and the envelope bounds each run
  printf '[]' > "$GH_OPEN"; printf '[]' > "$GH_CLOSED"
  printf '{"number":0,"title":"t","body":"","created_at":"2026-01-01T00:00:00Z"}' > "$GH_ISSUE1"
}
graph() { # <dir> <max_children> <spend_max>: one root loop holding one dispatch node, with the budget the node carries
  printf '{"run":{"max_parallel":20},"nodes":[{"name":"root","kind":"loop","nodes":[{"name":"route","kind":"dispatch","max_children":%s,"max_parallel":2,"budget":{"stalls":{"min":2,"max":5},"spend":{"min":0.05,"max":%s}}}]}]}' "$2" "$3" > "$1"
}
issues() { printf '%s' "$1" | jq -c '[.[]|if .unready then . else .labels=((.labels//[])+[{"name":"frontier"}]) end]' > "$GH_ISSUES"; }   # a fixture issue carries the frontier label (#485) unless it says "unready"
four() { issues '[{"number":41,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":42,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":43,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":44,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'; }
run() { SG_SINGLE_AUTOFRONTIER=1 bash "$T/scripts/autofrontier.sh" 2>&1; /bin/sleep 0.6; }
spent() { sed -nE 's/.*spend ([0-9.]+).*/\1/p' "$MKLOG.body" | head -1; }
asked() { sed -nE 's/.*one entry for each of the ([0-9]+) child.*/\1/p' "$MKLOG.body" | head -1; }
seedfield() { python3 - "$repo" "$1" <<'PY'
import json, pathlib, sys
d = json.loads((pathlib.Path(sys.argv[1]) / "docs/design/router.graph.json").read_text())
def walk(o):
    if isinstance(o, dict):
        if o.get("kind") == "dispatch":
            if sys.argv[2] == "max_children" and isinstance(o.get("max_children"), int): return o["max_children"]
            if sys.argv[2] == "spend" and isinstance(o.get("budget"), dict): return o["budget"]["spend"]["max"]
        for v in o.values():
            r = walk(v)
            if r is not None: return r
    if isinstance(o, list):
        for v in o:
            r = walk(v)
            if r is not None: return r
    return None
print(walk(d))
PY
}

# 1) The project's own graph, at the path the editor saves to: the request names the dispatch budget that graph carries.
scene owner
graph "$d/co/docs/squad/workflows/dynamic.graph.json" 2 0.75
four; run
[ "$(asked)" = 2 ] && ok "the request fits the children the project's graph allows" || ko "the request asks for '$(asked)' children against an envelope of 2"
[ "$(spent)" = 0.75 ] && ok "the request names the spend the graph allows" || ko "the request names '$(spent)', not the graph's 0.75"
$G -q 'spend 8' "$MKLOG.body" && ko "the request still carries the hardcoded spend 8" || ok "no hardcoded spend 8 survives"

# 2) No project graph: the pass follows the file the engine will run, the seed squad/workflows.py falls back to. This is the
#    layout of the tooling repository itself, where the path the pass built by hand held no file at all.
scene seed
rm -rf "$d/co/docs/squad/workflows"; seed=$(seedfield spend); seedk=$(seedfield max_children)
[ -n "$seed" ] && [ -n "$seedk" ] || { ko "the seed graph carries no dispatch budget to read"; seed=unreadable; seedk=unreadable; }
four; run
[ "$(asked)" = "$seedk" ] && ok "the seed's own envelope still bounds the request" || ko "the request asks for '$(asked)' children with the seed's envelope of '$seedk'"
[ "$(spent)" = "$seed" ] && ok "the request names the seed's dispatch budget" || ko "the request names '$(spent)', not the seed's '$seed'"
$G -q 'spend 8' "$MKLOG.body" && ko "the request still carries the hardcoded spend 8" || ok "no hardcoded spend 8 survives"

# 3) (#2286) A pass that refuses a startable card for "no slot" writes the slot-holder record from the counters it already
#    reads: the record names the slot count it left and the cards waiting in Todo, so the Loop page can split the wait.
scene slots
graph "$d/co/docs/squad/workflows/dynamic.graph.json" 2 0.75
four; run
rec=$d/st/slot-refusals.jsonl
if [ -f "$rec" ]; then
  [ "$(tail -1 "$rec" | jq -r '.todo')" = 4 ] && ok "the refusal writes the slot-holder record with the four cards waiting" || ko "the record names '$(tail -1 "$rec" | jq -r '.todo')' cards, not the four in Todo"
  [ "$(tail -1 "$rec" | jq -r '.cap')" = 1 ] && ok "the record names the run cap the pass read" || ko "the record names cap '$(tail -1 "$rec" | jq -r '.cap')', not the pass's 1"
else
  ko "a pass that refused a startable card for no slot wrote no slot-holder record"
fi

# 4) (#2286) The record names each holder of a run slot, resolved from the state dir and the checkout the pass names: one
#    live run holds the only slot, so every Todo card waits, and the record names that run and its kind.
scene holders
graph "$d/co/docs/squad/workflows/dynamic.graph.json" 2 0.75
mkdir -p "$d/co/runs/run-live"
printf '{"event":"run_start"}\n' > "$d/co/runs/run-live/record.jsonl"
printf 'CHILD — GitHub issue(s) 9999 (feature):\n' > "$d/co/runs/run-live/request.txt"
mkdir -p "$d/st/auto"; printf 'run-live\n' > "$d/st/auto/disp-9999"
printf 'run-live\n' > "$d/st/active-runs.txt"
four; run
rec=$d/st/slot-refusals.jsonl
if [ -f "$rec" ]; then
  [ "$(tail -1 "$rec" | jq -r '.holders|length')" = 1 ] && ok "the record names the one holder of the pass's only run slot" || ko "the record names '$(tail -1 "$rec" | jq -r '.holders|length')' holders, not the one live run"
  [ "$(tail -1 "$rec" | jq -r '.holders[0].name')" = run-live ] && ok "the holder is the run the state dir's active list names" || ko "the holder is '$(tail -1 "$rec" | jq -r '.holders[0].name')', not run-live"
  [ "$(tail -1 "$rec" | jq -r '.holders[0].kind')" = doing ] && ok "a live run that a card dispatch started holds the slot in Doing" || ko "the holder's kind is '$(tail -1 "$rec" | jq -r '.holders[0].kind')', not doing"
  [ "$(tail -1 "$rec" | jq -r '.holders[0].issue')" = 9999 ] && ok "the Doing holder names the issue its card dispatch holds" || ko "the holder names issue '$(tail -1 "$rec" | jq -r '.holders[0].issue')', not 9999"
  [ "$(tail -1 "$rec" | jq -r '.free')" = 0 ] && ok "the record names the zero free slots the holder leaves" || ko "the record names '$(tail -1 "$rec" | jq -r '.free')' free slots, not the holder's 0"
else
  ko "a pass that refused a startable card for no slot wrote no slot-holder record for its holder"
fi

# 5) (#2286) A record the pass cannot write is named on stderr and never fails the pass: a measure is no gate.
scene unwritable
graph "$d/co/docs/squad/workflows/dynamic.graph.json" 2 0.75
mkdir -p "$d/co/runs/run-live"; printf '{"event":"run_start"}\n' > "$d/co/runs/run-live/record.jsonl"; printf 'x\n' > "$d/co/runs/run-live/request.txt"; printf 'run-live\n' > "$d/st/active-runs.txt"   # the slot is held, so the pass refuses a card for "no slot" and writes the record
mkdir -p "$d/fk"; cp -R "$repo/squad" "$d/fk/squad"; cp -R "$repo/config" "$d/fk/config"
printf 'def record_pass(*a, **k):\n    raise RuntimeError("the record could not be written")\n' > "$d/fk/squad/slot_holders.py"
four
out=$(SG_SQUAD=$d/fk SG_SINGLE_AUTOFRONTIER=1 bash "$T/scripts/autofrontier.sh" 2>&1); rc=$?
case "$out" in *"FRONTIER: the slot-holder record was not written"*) ok "a failed record is named on stderr";; *) ko "a failed record is silent: '$out'";; esac
[ "$rc" = 0 ] && ok "a failed record never fails the pass" || ko "a failed record ended the pass with $rc"
# 6) A column limit: no card enters Doing while Doing holds its maximum, and the pass says so once (#2287). The board read
#    is the pass's own, so a card in Doing costs the pass no second read.
scene colfull
graph "$d/co/docs/squad/workflows/dynamic.graph.json" 2 0.75
four
{ for n in 41 42 43 44 45 46; do printf '%s\tDoing\n' "$n"; done; } > "$d/board.txt"; export GH_BOARD=$d/board.txt
printf 'caps:\n  runs: 10\ncolumns:\n  Doing:\n    max_cards: 2\n' > "$d/co/docs/squad/defaults.yaml"
r=$(run)
case "$r" in *"FRONTIER column Doing full: 6 of 2 card(s) in Doing; the cards unit stopped the pass; nothing dispatched this pass"*) ok "a full Doing column refuses the pass, naming the count, the limit and the cards unit";; *) ko "no column-full line: '$r'";; esac
[ "$(printf '%s' "$r" | $G -c 'FRONTIER column Doing full')" = 1 ] && ok "the refusal is said once a pass" || ko "the refusal printed $(printf '%s' "$r" | $G -c 'FRONTIER column Doing full') times"
[ ! -s "$MKLOG" ] && ok "no request is built while Doing is full" || ko "a request was built while Doing was full"
# 7) The same cards under a limit above the count: the column is not full, and the pass dispatches as it always did.
printf 'caps:\n  runs: 10\ncolumns:\n  Doing:\n    max_cards: 9\n' > "$d/co/docs/squad/defaults.yaml"
r=$(run)
case "$r" in *"FRONTIER column Doing full"*) ko "the pass called the column full under a limit of 9 with 6 cards";; *) ok "a column under its limit never refuses the pass";; esac

# 8) caps.runs never lowers the Doing card limit (#2430): 6 cards under a column limit of 9 is not full on the column, and
#    the run slots are another unit: a machine cap of 2 with both slots free does not refuse the pass on cards.
printf 'caps:\n  runs: 2\ncolumns:\n  Doing:\n    max_cards: 9\n' > "$d/co/docs/squad/defaults.yaml"
r=$(run)
case "$r" in *"FRONTIER column Doing full"*) ko "caps.runs lowered the Doing card limit: '$r'";; *) ok "caps.runs never lowers the Doing card limit";; esac
unset GH_BOARD

# 9) An unreadable limit is a defect, not no limit (#2287): when the module fails, the pass names it and dispatches nothing.
scene nolimitread
graph "$d/co/docs/squad/workflows/dynamic.graph.json" 2 0.75
four
printf '#!/bin/bash\nsg_column_limit() { return 2; }\n' > "$T/scripts/columnlimit.sh"
r=$(run)
case "$r" in *"FRONTIER the Doing column limit is unreadable"*) ok "an unreadable limit stops the pass";; *) ko "no unreadable-limit line: '$r'";; esac
[ ! -s "$MKLOG" ] && ok "nothing is dispatched with an unread limit" || ko "a request was built with an unread limit"

# 10) The run slots are their own unit (#2430): Doing sits far under its card limit, and the one run slot is held, so the
#     pass stops and its line names the run slots, never the cards.
scene noslots
cp "$(dirname "$src")/columnlimit.sh" "$T/scripts/columnlimit.sh"   # case 9 stubbed the seam in the shared scripts dir
graph "$d/co/docs/squad/workflows/dynamic.graph.json" 2 0.75
four
mkdir -p "$d/co/runs/run-live"; printf '{"event":"run_start"}\n' > "$d/co/runs/run-live/record.jsonl"; printf 'run-live\n' > "$d/st/active-runs.txt"
printf 'caps:\n  runs: 1\ncolumns:\n  Doing:\n    max_cards: 9\n' > "$d/co/docs/squad/defaults.yaml"
r=$(run)
case "$r" in *"FRONTIER the run slots are full: 0 of 1 run slot(s) free; the run slots unit stopped the pass; nothing dispatched this pass"*) ok "no free run slot stops the pass, and the line names the run slots unit";; *) ko "no run-slot refusal line: '$r'";; esac
case "$r" in *"FRONTIER column Doing full"*) ko "an idle Doing column was called full while the slot was held";; *) ok "the cards unit never refuses while Doing sits under its limit";; esac
[ ! -s "$MKLOG" ] && ok "no request is built with no free run slot" || ko "a request was built with no free run slot"

# 11) A hand hold counts in Doing (#2430): the owner's hand work takes its share of the column. Five Doing cards under a
#     limit of 6, and one held Todo card makes 6 of 6: the pass refuses. A hold on a card already in Doing adds nothing.
scene holds
graph "$d/co/docs/squad/workflows/dynamic.graph.json" 2 0.75
{ for n in 41 42 43 44 45; do printf '%s\tDoing\n' "$n"; done; } > "$d/board.txt"; export GH_BOARD=$d/board.txt
printf 'caps:\n  runs: 10\ncolumns:\n  Doing:\n    max_cards: 6\n' > "$d/co/docs/squad/defaults.yaml"
issues '[{"number":46,"title":"a","body":"b","labels":[{"name":"frontier"},{"name":"ready-for-human"}]}]'
r=$(run)
case "$r" in *"FRONTIER column Doing full: 6 of 6 card(s) in Doing; the cards unit stopped the pass"*) ok "a hand hold counts in Doing";; *) ko "a hand hold did not count in Doing: '$r'";; esac
[ "$(printf '%s' "$r" | $G -c 'FRONTIER column Doing full')" = 1 ] && ok "the hold refusal is said once a pass" || ko "the hold refusal printed $(printf '%s' "$r" | $G -c 'FRONTIER column Doing full') times"
[ ! -s "$MKLOG" ] && ok "no request is built while a held card makes Doing full" || ko "a request was built while a held card made Doing full"
# The held card is already a Doing card: it counts once, so 5 of 6 is under the limit and never refuses on cards.
issues '[{"number":41,"title":"a","body":"b","labels":[{"name":"frontier"},{"name":"ready-for-HAL9K"}]}]'
r=$(run)
case "$r" in *"FRONTIER column Doing full"*) ko "a hold on a Doing card was counted twice: '$r'";; *) ok "a hold on a card already in Doing counts once";; esac
unset GH_BOARD

# 12) (#2438) A full Batching column holds dispatch: the cards that wait for their landing already fill the column, so the
#     pass starts no run and names the count, the limit and the cards unit. One Todo card is startable and stays waiting.
scene batchfull
graph "$d/co/docs/squad/workflows/dynamic.graph.json" 2 0.75
four
{ printf '41\tTodo\n'; for n in 51 52 53; do printf '%s\tBatching\n' "$n"; done; } > "$d/board.txt"; export GH_BOARD=$d/board.txt
printf 'caps:\n  runs: 10\ncolumns:\n  Batching:\n    max_cards: 3\n' > "$d/co/docs/squad/defaults.yaml"
r=$(run)
case "$r" in *"FRONTIER column Batching full: 3 of 3 card(s) in Batching; the cards unit stopped the pass; nothing dispatched this pass"*) ok "a full Batching column refuses the pass, naming the count, the limit and the cards unit";; *) ko "no Batching-full line: '$r'";; esac
[ "$(printf '%s' "$r" | $G -c 'FRONTIER column Batching full')" = 1 ] && ok "the Batching refusal is said once a pass" || ko "the Batching refusal printed $(printf '%s' "$r" | $G -c 'FRONTIER column Batching full') times"
[ ! -s "$MKLOG" ] && ok "no request is built while Batching is full" || ko "a request was built while Batching was full"
[ ! -s "$LAUNCHLOG" ] && ok "no run starts while Batching is full" || ko "a run started while Batching was full"

# 13) (#2438) The same three Batching cards under a limit of 4 leave the column under its limit: the startable Todo card
#     dispatches as it always did.
printf 'caps:\n  runs: 10\ncolumns:\n  Batching:\n    max_cards: 4\n' > "$d/co/docs/squad/defaults.yaml"
: > "$d/mk.log"; : > "$d/launch.log"
r=$(run)
case "$r" in *"FRONTIER column Batching full"*) ko "the pass called Batching full under a limit of 4 with 3 cards";; *) ok "a Batching column under its limit never refuses the pass";; esac
[ -s "$MKLOG" ] && ok "a request is built while Batching sits under its limit" || ko "no request was built under a Batching limit of 4: '$r'"
[ -s "$LAUNCHLOG" ] && ok "a run starts while Batching sits under its limit" || ko "no run started under a Batching limit of 4: '$r'"

# 14) (#2438) No Batching limit: the column holds any number of cards, and the pass dispatches as it always did.
printf 'caps:\n  runs: 10\n' > "$d/co/docs/squad/defaults.yaml"
: > "$d/mk.log"; : > "$d/launch.log"; rm -f "$d/st"/auto/disp-*   # the pass before took issue 41: free its marker for a new start
r=$(run)
case "$r" in *"FRONTIER column Batching full"*) ko "the pass called Batching full with no limit set";; *) ok "Batching with no limit never refuses the pass";; esac
[ -s "$MKLOG" ] && ok "a request is built with no Batching limit" || ko "no request was built with no Batching limit: '$r'"
[ -s "$LAUNCHLOG" ] && ok "a run starts with no Batching limit" || ko "no run started with no Batching limit: '$r'"
unset GH_BOARD

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
