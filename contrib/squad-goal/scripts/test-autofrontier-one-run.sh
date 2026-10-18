#!/bin/bash
# Regression test for autofrontier.sh: startable issues go out as the children of ONE run, and the number of children a pass
# asks for never passes the envelope the author set (the dispatch node's max_children) or the machine-wide child ceiling
# (run.max_children less the children live runs already hold). A second run opens only in a later pass, and the pass says why.
# Offline and hermetic: stub gh (fixtures served through jq), stack.sh, startability.sh, mkreq.sh and launch.py, with its own state and HOME.
# usage: bash test-autofrontier-one-run.sh [path/to/autofrontier.sh]
src=${1:-$(dirname "$0")/autofrontier.sh}; T=$(mktemp -d); fail=0
repo=$(cd "$(dirname "$src")/../../.." && pwd)   # SG_SQUAD: squad/caps.py lives in the tooling repo the fixture is testing (#21)
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
if [ ! -f "$src" ]; then echo "FAIL three startable issues open one run: $src not found"; rm -rf "$T"; exit 1; fi
for v in $(compgen -e | $G '^SG_'); do unset "$v"; done   # the live loop exports its own SG_*; a leaked one would walk past a stub
export SG_SQUAD=$repo SQUAD_CAPS_DIR=$T/caps   # (#21) the pass reads the machine-wide ceiling through squad.caps, exported so the reader process sees it; the leases are sandboxed
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
  jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:(.labels//[])},blockedBy:{nodes:[]},comments:{nodes:[(.comments//[])[]|{body:.}]}}]}}}}' "$GH_ISSUES"; exit 0;; esac
case "$p" in
  */pulls?state=open*)    f=$GH_OPEN;;
  */pulls?state=closed*)  f=$GH_CLOSED;;
  */issues?state=open*)   f=$GH_ISSUES; [ -n "$GH_FRONTIER_ONLY" ] && [[ "$p" == *"labels=frontier"* ]] && f=$GH_FRONTIER;;
  */issues/*)             f=$GH_ISSUE1; [ -f "$GH_ISSUEDIR/${p##*/}.json" ] && f=$GH_ISSUEDIR/${p##*/}.json;;   # one fixture per issue when a case wrote it
  *) exit 1;;
esac
if [ -n "$q" ]; then jq -r "$q" "$f"; else cat "$f"; fi
GH
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\necho READY\n' > "$T/scripts/stack.sh"; chmod +x "$T/scripts/stack.sh"
# The dispatchers share one claim namespace and one read of the live requests (startability.sh, #428), so the stand-in
# carries the real file's own reads and fakes only what this suite fixes by hand.
cp "$(dirname "$src")/startability.sh" "$T/scripts/startability-real.sh"
printf '#!/bin/bash\n. "$(dirname "$0")/startability-real.sh"\nsg_fork_only() { return 1; }\nsg_not_startable() { return 1; }\nsg_fork_preamble() { :; }\nsg_fork_branch() { echo "gui/$1"; }\nsg_fork_branch_pattern() { echo "gui/%%n"; }\nsg_issue_worktree_note() { :; }\n' > "$T/scripts/startability.sh"
# mkreq.sh keeps the body it was handed and prints a request path (or refuses when MK_REFUSE is set); launch.py only counts
printf '#!/bin/bash\n[ -n "$MK_REFUSE" ] && { echo "refusing: stub" >&2; exit 1; }\n[ "$1" = --paths ] && exec bash "$(dirname "$0")/mkreq-real.sh" "$@"\n[ -n "$MK_REAL" ] && { bash "$(dirname "$0")/mkreq-real.sh" "$@" || exit 1; echo x >> "$MKLOG"; cp "$2" "$MKLOG.body"; exit 0; }\necho x >> "$MKLOG"; cp "$2" "$MKLOG.body"; echo "$HOME/req.txt"\n' > "$T/scripts/mkreq.sh"
printf 'open(__import__("os").environ["LAUNCHLOG"], "a").write("launch\\n")\n' > "$T/scripts/launch.py"
chmod +x "$T/scripts/mkreq.sh"
# board.sh keeps the call it was handed: <owner> <project> <column> <issue>... (#33: the card move was a bare `true`, so no case could say which cards moved)
printf '#!/bin/bash\necho "$*" >> "$BOARDLOG"\n' > "$T/board.sh"; chmod +x "$T/board.sh"
cp "$src" "$T/scripts/autofrontier.sh"; cp "$(dirname "$src")"/{ghcall.sh,ghread.py,ghthrottle.py} "$T/scripts/"   # the pass reads the open-issue list through the door
# The seam's door, stubbed at the script's own name (#550): one `<issue>\t<state>` line per card. A fixture issue's card is
# in Todo unless it names its own "card" ("" is no card); CARDS_DOWN is a board that does not answer.
cat > "$T/scripts/issue_state.py" <<'PY'
import json, os, sys
if sys.argv[1:] or os.environ.get("CARDS_DOWN"): print("no board", file=sys.stderr); sys.exit(1)
for i in json.load(open(os.environ["GH_ISSUES"])):
    if i.get("card", "Todo"): print(f"{i['number']}\t{i.get('card', 'Todo')}")
PY
scene() { # <name> [max_children_of_the_dispatch_node [run_max_children]]: fresh state, its own env.sh and workflow graph
  d=$T/$1; mkdir -p "$d/st/req" "$d/st/cq" "$d/co/docs/squad/workflows" "$d/co/runs" "$d/home"
  : > "$d/gh.log"; : > "$d/st/active-runs.txt"; : > "$d/mk.log"; : > "$d/launch.log"; : > "$d/board.log"
  cat > "$T/scripts/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$d/st; SG_CHECKOUT=$d/co; SG_BASE=main
SG_FRONTIER_LABEL=frontier; SG_BUG_LABEL=bug; SG_FRONTIER_SKIP_LABELS=spec,needs-owner,needs-ratification,wontfix,needs-info; SG_CODING_RULES=rules; BOARD=$T/board.sh
. "$(dirname "$src")/address.sh"   # the writers name a queued comment through the seam, as the real env.sh loads it
E
  if [ -n "$2" ]; then   # the author's graph: a root loop that holds one dispatch node, and a run block
    rb='{"max_parallel":20}'; [ -n "$3" ] && rb="{\"max_parallel\":20,\"max_children\":$3}"
    printf '{"run":%s,"nodes":[{"name":"root","kind":"loop","nodes":[{"name":"route","kind":"dispatch","max_children":%s,"max_parallel":2}]}]}' "$rb" "$2" > "$d/co/docs/squad/workflows/dynamic.graph.json"
  fi
  export SG_GH_AGGREGATE_WINDOW=0   # a case rewrites the list between passes: no snapshot answers the second one
  export HOME=$d/home SG_STATE=$d/st SG_CHECKOUT=$d/co SG_REPO=o/r SG_FRONTIER_LABEL=frontier MKLOG=$d/mk.log LAUNCHLOG=$d/launch.log BOARDLOG=$d/board.log
  export GH_ISSUES=$d/issues.json GH_FRONTIER=$d/frontier.json GH_OPEN=$d/open.json GH_CLOSED=$d/closed.json GH_ISSUE1=$d/issue1.json GH_ISSUEDIR=$d/issue; mkdir -p "$GH_ISSUEDIR"
  printf '[]' > "$GH_FRONTIER"
  printf '[]' > "$GH_OPEN"; printf '[]' > "$GH_CLOSED"
  printf '{"number":0,"title":"t","body":"","created_at":"2026-01-01T00:00:00Z"}' > "$GH_ISSUE1"
}
five() { issues '[{"number":11,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":12,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":13,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":14,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":15,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'; }
issues() { printf '%s' "$1" | jq -c '[.[]|if .unready then . else .labels=((.labels//[])+[{"name":"frontier"}]) end]' > "$GH_ISSUES"; }   # a fixture issue carries the frontier label (#485) unless it says "unready"
issuebody() { printf '{"number":%s,"title":"t","body":"%s","created_at":"2026-01-01T00:00:00Z"}' "$1" "$2" > "$GH_ISSUEDIR/$1.json"; }   # <n> <body>: what the pass pastes into the request for that issue
real() { MK_REAL=1 SG_FORK_DIR=$FK SG_FORK_BASE=main "$@"; }   # the real slice guard over the throwaway fork
run() { SG_SINGLE_AUTOFRONTIER=1 bash "$T/scripts/autofrontier.sh" 2>&1; /bin/sleep 0.6; }   # the launch is detached: give it time to count itself
lines() { local c; c=$($G -c . "$1" 2>/dev/null); echo "${c:-0}"; }
marked() { local n c=0; for n in "$@"; do [ -e "$SG_STATE/auto/disp-$n" ] && c=$((c+1)); done; echo $c; }
asked() { sed -nE 's/.*call the spawn tool for the ([0-9]+) child.*/\1/p' "$MKLOG.body" | head -1; }
moved() { local n; for n in "$@"; do $G -qE "^(--add )?o 1 In Progress( [0-9]+)* $n( |\$)" "$BOARDLOG" || return 1; done; }   # every issue named is in the card move the pass made
added() { $G -qE '^--add o 1 In Progress( [0-9]+)*$' "$BOARDLOG"; }   # a dispatch adds a missing board card before its status is written
seedmax() { python3 - "$repo" <<'PY'
import json, pathlib, sys
d = json.loads((pathlib.Path(sys.argv[1]) / "docs/design/router.graph.json").read_text())
def walk(o):
    if isinstance(o, dict):
        if o.get("kind") == "dispatch" and isinstance(o.get("max_children"), int): return o["max_children"]
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
childblocks() { $G -c '^CHILD' "$MKLOG.body" 2>/dev/null; }

# 1) AC1, AC2, AC3: three startable issues open ONE run; its request asks for three children, one issue each; every
#    issue holds its dispatch marker, its own comment and its own card move
scene three
issues '[{"number":21,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":22,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":23,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 1 ] && ok "three startable issues open one run" || ko "three startable issues open one run: $(lines "$LAUNCHLOG") launches: '$r'"
[ "$(lines "$MKLOG")" = 1 ] && ok "the pass builds one request" || ko "the pass builds one request: $(lines "$MKLOG")"
[ "$(asked)" = 3 ] && ok "the request asks for three children" || ko "the request asks for three children: '$(asked)'"
[ "$(childblocks)" = 3 ] && ok "the request carries three CHILD blocks, one issue each" || ko "the request carries three CHILD blocks: '$(childblocks)'"
[ "$(marked 21 22 23)" = 3 ] && ok "every dispatched issue holds its marker" || ko "every dispatched issue holds its marker: $(marked 21 22 23) of 3"
[ "$(ls "$d/st/cq" | $G -c -- '^o__r#[0-9]*-frontier')" = 3 ] && ok "every dispatched issue gets its own comment" || ko "every dispatched issue gets its own comment: $(ls "$d/st/cq")"
case "$(cat "$d/st/auto/disp-21")" in frontier-*) ok "a marker names the request";; *) ko "a marker names the request";; esac
moved 21 22 23 && ok "every dispatched issue gets its own card move" || ko "every dispatched issue gets its own card move: '$(cat "$BOARDLOG")'"
added && ok "a dispatch adds each missing board card before its move" || ko "a dispatch does not add missing board cards: '$(cat "$BOARDLOG")'"

# 1a) A ticket whose blockers have all closed may carry only its wave label. The wave orders the
#     frontier; it does not grant permission to start. A label-gated read leaves it invisible.
scene wave-only
export GH_FRONTIER_ONLY=1
issues '[{"number":24,"title":"a","body":"b","labels":[{"name":"wave:02"}]}]'
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 1 ] && [ "$(marked 24)" = 1 ] && ok "an unblocked wave-only issue joins the frontier" || ko "an unblocked wave-only issue stayed invisible: '$r'"
unset GH_FRONTIER_ONLY

# 1b) #33: a child with SEVERAL issues is one slice, one request line and one PR, and each of its issues still holds its marker,
#     its own comment and its own card move -- the claim the comment above makes for one-issue children, asserted for the bundle
scene bundle
issues '[{"number":31,"title":"a","body":"fix scripts/shared_fix.py","labels":[{"name":"wave:01"}]},{"number":32,"title":"a","body":"also scripts/shared_fix.py","labels":[{"name":"wave:01"}]},{"number":34,"title":"a","body":"unrelated","labels":[{"name":"wave:01"}]}]'   # #383: the bodies come from the list, never a read per issue
cp "$(dirname "$src")/cluster.py" "$T/scripts/"   # the real files rule bundles two tickets that name one file; the other cases leave it out, their issues share one fixture number
r=$(run); rm -f "$T/scripts/cluster.py"
[ "$(childblocks)" = 2 ] && $G -q '^CHILD — GitHub issue(s) 31, 32 ' "$MKLOG.body" && ok "two issues that name one file are ONE child; the third is its own" || ko "two issues that name one file are one child: '$($G '^CHILD' "$MKLOG.body")'"
[ "$(marked 31 32 34)" = 3 ] && ok "every issue of the bundle holds its marker" || ko "every issue of the bundle holds its marker: $(marked 31 32 34) of 3"
[ "$(ls "$d/st/cq" | $G -c -- '-frontier')" = 3 ] && ok "every issue of the bundle gets its own comment" || ko "every issue of the bundle gets its own comment: $(ls "$d/st/cq")"
moved 31 32 34 && ok "every issue of a multi-issue child moves its own card" || ko "every issue of a multi-issue child moves its own card: '$(cat "$BOARDLOG")'"

# 2) AC5: the envelope stays the author's. Five startable issues, a dispatch node that allows two children: the request asks
#    for two, never five (the engine rejects a request over the limit whole), and the rest stay unmarked for the next run
scene envelope 2
five; r=$(run)
[ "$(asked)" = 2 ] && ok "the request never asks for more children than the envelope allows" || ko "the request asks for '$(asked)' children against an envelope of 2: '$r'"
[ "$(lines "$LAUNCHLOG")" = 1 ] && ok "a full envelope still opens one run in this pass" || ko "a full envelope still opens one run in this pass: $(lines "$LAUNCHLOG")"
[ "$(marked 11 12)" = 2 ] && [ "$(marked 13 14 15)" = 0 ] && ok "the issues past the envelope stay unmarked" || ko "the issues past the envelope stay unmarked: $(marked 11 12) of 2 marked, $(marked 13 14 15) of 3 past it"

# 3) AC6: a pass that leaves startable issues for a later run says why
case "$r" in *"envelope full"*) ok "the pass says the envelope is full";; *) ko "the pass says the envelope is full: '$r'";; esac

# 4) AC5: the machine-wide child ceiling still applies. run.max_children is 4 and a live run already holds three accepted
#    children, so this pass has room for one
scene ceiling 24 4
mkdir -p "$d/co/runs/live"; printf '{"event":"spawn_accepted","child":"c1","task":"GitHub issue #901"}\n{"event":"spawn_accepted","child":"c2","task":"GitHub issue #902"}\n{"event":"spawn_accepted","child":"c3","task":"GitHub issue #903"}\n' > "$d/co/runs/live/record.jsonl"
echo live > "$d/st/active-runs.txt"
five; r=$(run)
[ "$(asked)" = 1 ] && ok "the request fits the room the machine ceiling leaves" || ko "the request asks for '$(asked)' children with room for 1: '$r'"

# 4b) The router writes the issue it works down without the hash -- "Fix GitHub issue 11 in the repository" is what the live
#     runs of this repository carry. The busy test read the hashed form only, so the issue looked untouched, the pass sent
#     it a second time, and the second run ended needs-human ("the required gui/11 branch is already checked out in a
#     different worktree") while the first one was still building it (#66). A run in a live record holds what it accepted,
#     however it is written.
scene heldplain 24 4
mkdir -p "$d/co/runs/live"; printf '{"event":"spawn_accepted","child":"c1","definition":"Fix GitHub issue 11 in the repository. Work only in worktrees/child/gui-11 on branch gui/11."}\n' > "$d/co/runs/live/record.jsonl"
echo live > "$d/st/active-runs.txt"
five; r=$(run)
[ "$(marked 11)" = 0 ] && ok "an issue a live run holds with no hash is not dispatched a second time" || ko "an issue a live run holds with no hash was dispatched again: $(marked 11) marker(s)"
[ "$(marked 12 13 14 15)" -gt 0 ] && ok "the issues no live run holds still go out" || ko "no other startable issue went out"

# 5) AC6: a request the slice guard refuses says so, frees every marker and launches nothing
scene refused 24
five; r=$(MK_REFUSE=1 run)
[ "$(marked 11 12 13 14 15)" = 0 ] && ok "a refused slice frees every marker" || ko "a refused slice frees every marker: $(marked 11 12 13 14 15) held"
[ "$(lines "$LAUNCHLOG")" = 0 ] && ok "a refused slice launches nothing" || ko "a refused slice launches nothing"
case "$r" in *"refused slice"*) ok "the pass says a slice was refused";; *) ko "the pass says a slice was refused: '$r'";; esac

# 6) #57: a project with no graph of its own runs the seed squad/workflows.py falls back to, so the pass reads THAT file's
#    envelope -- the envelope the engine will enforce -- instead of reading nothing and leaving the bound to free run slots.
#    The bound was re-anchored when the pass started reading the file the engine runs: the seed allows four children, not five.
scene nograph
five; r=$(run); sm=$(seedmax)
[ "$(asked)" = "$sm" ] && ok "no graph of its own: the request fits the seed's envelope" || ko "no graph of its own: asked for '$(asked)' against the seed's '$sm': '$r'"

# 7) #208: a spec is a document until its owner starts it, and an issue that waits on him is not a unit of work either. The
#    pass reads every open issue, so the labels SG_FRONTIER_SKIP_LABELS names -- the spec, beside the bug label autobug.sh
#    already owns -- never become a child: a run that implemented one would commit a decision the owner has not made.
scene skip 24
issues '[{"number":41,"title":"a","body":"b","labels":[{"name":"spec"},{"name":"wave:01"}]},{"number":40,"title":"a","body":"b","labels":[{"name":"needs-owner"},{"name":"wave:01"}]},{"number":42,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 42)" = 1 ] && ok "the startable ticket beside them goes out" || ko "the ticket beside a spec did not go out: '$r'"
[ "$(marked 41)" = 0 ] && ok "a spec the owner has not started is left alone" || ko "the pass dispatched a spec: $(marked 41) marker(s)"
[ "$(marked 40)" = 0 ] && ok "an issue that waits on the owner is left alone" || ko "the pass dispatched an issue waiting on the owner: $(marked 40) marker(s)"
[ "$(childblocks)" = 1 ] && ok "the request asks for the one ticket alone" || ko "the request asks for $(childblocks) children: '$($G '^CHILD' "$MKLOG.body")'"

# 8) #238: freeze markers are holds, not columns. Both reasons, and the tracker owner words, keep their
# card out of the dispatch pass; no board write moves a frozen card. Once the marker is removed, the same
# item is startable again.
scene freeze 24
issues '[{"number":43,"title":"a","body":"b","labels":[{"name":"frozen:owner"},{"name":"wave:01"}]},{"number":44,"title":"a","body":"b","labels":[{"name":"frozen:dependency"},{"name":"wave:01"}]},{"number":45,"title":"a","body":"b","labels":[{"name":"needs-ratification"},{"name":"wave:01"}]},{"number":46,"title":"a","body":"b","labels":[{"name":"needs-info"},{"name":"wave:01"}]},{"number":47,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 43 44 45 46)" = 0 ] && ok "both freeze markers and owner words hold dispatch" || ko "a frozen item was dispatched: $(marked 43 44 45 46) marker(s): '$r'"
[ "$(marked 47)" = 1 ] && ok "an unfrozen item beside holds is dispatched" || ko "an unfrozen item beside holds was not dispatched"
! $G -qE ' (43|44|45|46)( |$)' "$BOARDLOG" && ok "a freeze does not change the card column" || ko "a frozen card had a board write: '$(cat "$BOARDLOG")'"
issues '[{"number":43,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":44,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 43 44)" = 2 ] && ok "removing either freeze makes the item startable" || ko "removing a freeze did not start both items: '$r'"

# 10) #340: a run parent issue is a roll-up of a run's children, never work. It carries the run-parent label, and the pass
#     skips it whatever SG_FRONTIER_SKIP_LABELS says.
scene parent
issues '[{"number":48,"title":"Squad run run-1","body":"b","labels":[{"name":"run-parent"}]},{"number":49,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 48)" = 0 ] && ok "a run parent is never dispatched" || ko "the run parent #48 was dispatched: '$r'"
[ "$(marked 49)" = 1 ] && ok "an issue beside a run parent is dispatched" || ko "the issue beside the run parent was not dispatched: '$r'"

# 11) #383: the not-startable marker is read from the open-issue list's entry, never from a comments read per candidate.
#     The stub sg_not_startable says "startable", so only the list's comments can keep #51 out.
scene notstartable
issues '[{"number":51,"title":"a","body":"b","labels":[{"name":"wave:01"}],"comments":["SQUAD-NOT-STARTABLE: failed 3 times"]},{"number":52,"title":"a","body":"b","labels":[{"name":"wave:01"}],"comments":["a note"]}]'
r=$(run)
[ "$(marked 51)" = 0 ] && case "$r" in *"FRONTIER skip #51: SQUAD-NOT-STARTABLE"*) true;; *) false;; esac && ok "an entry whose comments carry the marker is skipped" || ko "the marked #51 was not skipped from the list: '$r'"
[ "$(marked 52)" = 1 ] && ok "an entry without the marker goes out" || ko "#52 did not go out: '$r'"
$G -qx 'b' "$MKLOG.body" && ok "the request pastes the body from the list" || ko "the request has no body from the list: '$(cat "$MKLOG.body")'"

# V (#468): on the shipped vocabulary the dispatch writes Doing. "In Progress" is no column there, and the board refused it in
# silence, so a dispatched card stayed on Todo until the janitor's next pass.
scene vocab
issues '[{"number":61,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(SG_BOARD_VOCAB=shipped run)
$G -qE "^(--add )?o 1 Doing( [0-9]+)* 61( |\$)" "$BOARDLOG" && ok "V: a dispatch on the shipped vocabulary writes Doing" || ko "V: the dispatch wrote $(cat "$BOARDLOG")"
$G -q "In Progress" "$BOARDLOG" && ko "V: the dispatch still writes In Progress on the shipped vocabulary" || ok "V: no In Progress write on the shipped vocabulary"

# W (#485): the frontier takes only an issue that carries its label. #482 (an enhancement kept in the Backlog, no
# ready-for-agent) went out on the first pass after a loop start, because the list read selected on no label at all.
scene label
issues '[{"number":71,"title":"a","body":"b","unready":true,"labels":[{"name":"enhancement"}]},{"number":72,"title":"a","body":"b","labels":[{"name":"enhancement"}]}]'
r=$(run)
[ "$(marked 71)" = 0 ] && ok "W: an issue without the frontier label is never dispatched" || ko "W: the unlabelled #71 went out: '$r'"
[ "$(marked 72)" = 1 ] && ok "W: an issue with the label still goes out" || ko "W: the labelled #72 did not go out: '$r'"

# X (#550): the column Todo is the owner's go signal. The frontier takes an issue only when its card sits in Todo AND it
# carries the frontier label: a labelled issue in Backlog, or one with no card, waits for the owner. The cards come from
# one board read a pass, never one per issue; a board that does not answer dispatches nothing.
scene todo
issues '[{"number":81,"title":"a","body":"b","card":"Backlog","labels":[{"name":"wave:01"}]},{"number":82,"title":"a","body":"b","card":"","labels":[{"name":"wave:01"}]},{"number":83,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":84,"title":"a","body":"b","unready":true,"labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 81)" = 0 ] && ok "X: a labelled issue whose card is in Backlog is not dispatched" || ko "X: #81 in Backlog went out: '$r'"
[ "$(marked 82)" = 0 ] && ok "X: a labelled issue with no card is not dispatched" || ko "X: #82 with no card went out: '$r'"
[ "$(marked 83)" = 1 ] && ok "X: a labelled issue whose card is in Todo goes out" || ko "X: #83 in Todo did not go out: '$r'"
[ "$(marked 84)" = 0 ] && ok "X: a card in Todo without the frontier label is not dispatched" || ko "X: the unlabelled #84 went out: '$r'"
scene todo-down
issues '[{"number":85,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(CARDS_DOWN=1 run)
[ "$(marked 85)" = 0 ] && [ "$(lines "$LAUNCHLOG")" = 0 ] && ok "X: a board that does not answer dispatches nothing" || ko "X: #85 went out with no board read: '$r'"
case "$r" in *"board"*) ok "X: the pass says the board did not answer";; *) ko "X: the pass is silent about the board: '$r'";; esac

# Y (#596, ADR-0146): an open issue carrying `loop-bug` is a defect in the loop's own machinery. The loop cannot dispatch
# its own fix, so the frontier pass dispatches nothing, names the issue exactly once per pass, and exits 0. The stop is read
# from the open issues the pass already reads, so it costs no second read, and it has no marker: closing the issue resumes
# dispatch at the next pass with no other action.
scene loopbug
issues '[{"number":91,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":596,"title":"The loop cannot land its own fix","body":"b","labels":[{"name":"wave:01"},{"name":"loop-bug"}]}]'
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 0 ] && ok "Y: a loop defect halts the frontier pass" || ko "Y: the pass launched with a loop defect open: '$r'"
[ "$(marked 91 596)" = 0 ] && ok "Y: no startable issue is dispatched while the defect is open" || ko "Y: markers: $(marked 91 596)"
case "$r" in *"LOOP STOP: #596 is a loop defect; the fix lands outside the loop"*) ok "Y: the stop names the issue and where the fix lands";; *) ko "Y: the stop is not named: '$r'";; esac
$G -qxF "LOOP STOP: #596 is a loop defect; the fix lands outside the loop" "$d/st/cycle.log" 2>/dev/null && ok "Y: the stop is published to the loop log" || ko "Y: cycle.log: $(cat "$d/st/cycle.log" 2>/dev/null)"
$G -qxF "LOOP STOP: #596 is a loop defect; the fix lands outside the loop" "$d/st/cq/o__r#596-status~loopstop.md" 2>/dev/null && ok "Y: the stop is the issue's line on the board" || ko "Y: the board line is missing: $(ls "$d/st/cq" 2>/dev/null)"
issues '[{"number":91,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 91)" = 1 ] && ok "Y: closing the issue resumes dispatch with no other action" || ko "Y: dispatch did not resume: '$r'"

rm -rf "$T"
[ "$fail" = 0 ] && { echo "autofrontier one-run: all checks green"; exit 0; }
echo "autofrontier one-run: FAILED"; exit 1
