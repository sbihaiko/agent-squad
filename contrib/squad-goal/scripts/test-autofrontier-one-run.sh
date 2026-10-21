#!/bin/bash
# Regression test for autofrontier.sh: a pass starts one run per free run slot, at most one per child (#728); with fewer slots
# than children the children share the runs. The children of one run never pass the envelope the author set (the dispatch
# node's max_children), and the children of the pass never pass the machine-wide child ceiling (run.max_children less the
# children live runs already hold). The startable issues past the bounds wait for a later pass, and the pass says why.
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
q=""; p=""; prev=""; [ -n "$GHLOG" ] && echo "$*" >> "$GHLOG"
for a in "$@"; do
  [ "$prev" = "-q" ] && q="$a"
  case "$a" in repos/*) p="$a";; esac
  prev="$a"
done
case "$*" in *"issues(states: OPEN"*)   # the open-issue list is one GraphQL request through the door (#383): the REST fixture in its shape
  jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:(.labels//[])},blockedBy:{nodes:[]},subIssues:{nodes:[(.sub_issues//[])[]|{number:.}]},comments:{nodes:[(.comments//[])[]|{body:.}]}}]}}}}' "$GH_ISSUES"; exit 0;; esac
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
# mkreq.sh appends the body it was handed (one pass may build several requests, #728) and prints a request path (or refuses when MK_REFUSE is set); launch.py only counts
printf '#!/bin/bash\n[ -n "$MK_REFUSE" ] && { echo "refusing: stub" >&2; exit 1; }\n[ "$1" = --paths ] && exec bash "$(dirname "$0")/mkreq-real.sh" "$@"\n[ -n "$MK_REAL" ] && { bash "$(dirname "$0")/mkreq-real.sh" "$@" || exit 1; echo x >> "$MKLOG"; cat "$2" >> "$MKLOG.body"; exit 0; }\necho x >> "$MKLOG"; cat "$2" >> "$MKLOG.body"; echo "$HOME/req.txt"\n' > "$T/scripts/mkreq.sh"
printf 'open(__import__("os").environ["LAUNCHLOG"], "a").write("launch\\n")\n' > "$T/scripts/launch.py"
chmod +x "$T/scripts/mkreq.sh"
# board.sh keeps the call it was handed: <owner> <project> <column> <issue>... (#33: the card move was a bare `true`, so no case could say which cards moved)
printf '#!/bin/bash\necho "$*" >> "$BOARDLOG"\n' > "$T/board.sh"; chmod +x "$T/board.sh"
cp "$src" "$T/scripts/autofrontier.sh"; cp "$(dirname "$src")"/{ghcall.sh,ghread.py,ghthrottle.py,issue_ledger.py} "$T/scripts/"   # the pass reads the open-issue list through the door
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
  rm -f "$d/st/token-pause"; printf '[]' > "$GH_FRONTIER"
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
slots() { mkdir -p "$d/co/docs/squad"; printf 'caps:\n  runs: %s\n' "$1" > "$d/co/docs/squad/defaults.yaml"; }   # <n>: the project file lowers caps.runs, so the pass has n free run slots
reqs() { sort -u "$SG_STATE"/auto/disp-* 2>/dev/null | $G -c .; }   # the distinct requests the dispatch markers name
childblocks() { $G -c '^CHILD' "$MKLOG.body" 2>/dev/null; }

# 1) #728 AC1: three startable issues of three specs and free run slots: one pass starts three runs, one child each. Every
#    issue holds its dispatch marker, its own comment and its own card move.
scene three
issues '[{"number":21,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":22,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":23,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 3 ] && ok "three startable issues and free slots start three runs" || ko "three startable issues and free slots start three runs: $(lines "$LAUNCHLOG") launches: '$r'"
[ "$(lines "$MKLOG")" = 3 ] && ok "the pass builds one request per run" || ko "the pass builds one request per run: $(lines "$MKLOG")"
[ "$(sed -nE 's/.*call the spawn tool for the ([0-9]+) child.*/\1/p' "$MKLOG.body" | sort -u | tr -d '\n')" = 1 ] && ok "each request asks for one child" || ko "each request asks for one child: '$($G -o 'spawn tool for the [0-9]* child' "$MKLOG.body")'"
[ "$(childblocks)" = 3 ] && ok "the requests carry three CHILD blocks, one issue each" || ko "the requests carry three CHILD blocks: '$(childblocks)'"
[ "$(marked 21 22 23)" = 3 ] && ok "every dispatched issue holds its marker" || ko "every dispatched issue holds its marker: $(marked 21 22 23) of 3"
[ "$(reqs)" = 3 ] && ok "each run has its own request name" || ko "each run has its own request name: $(cat "$d"/st/auto/disp-*)"
[ "$(ls "$d/st/cq" | $G -c -- '^o__r#[0-9]*-frontier')" = 3 ] && ok "every dispatched issue gets its own comment" || ko "every dispatched issue gets its own comment: $(ls "$d/st/cq")"
case "$(cat "$d/st/auto/disp-21")" in frontier-*) ok "a marker names the request";; *) ko "a marker names the request";; esac
moved 21 22 23 && ok "every dispatched issue gets its own card move" || ko "every dispatched issue gets its own card move: '$(cat "$BOARDLOG")'"
added && ok "a dispatch adds each missing board card before its move" || ko "a dispatch does not add missing board cards: '$(cat "$BOARDLOG")'"

# 1c) #728 AC2: with one free slot, the three issues are the children of ONE run.
scene oneslot
slots 1
issues '[{"number":21,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":22,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":23,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 1 ] && ok "one free slot: one run" || ko "one free slot: one run: $(lines "$LAUNCHLOG") launches: '$r'"
[ "$(asked)" = 3 ] && [ "$(childblocks)" = 3 ] && ok "one free slot: the run asks for three children" || ko "one free slot: asked '$(asked)', $(childblocks) CHILD blocks"
[ "$(marked 21 22 23)" = 3 ] && [ "$(reqs)" = 1 ] && ok "one free slot: every marker names the one request" || ko "one free slot: $(marked 21 22 23) markers, $(reqs) requests"

# 1d) #728: never more runs than free slots. Three free slots and four startable issues: three runs, and every issue goes out.
scene threeslots
slots 3
issues '[{"number":21,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":22,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":23,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":24,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 3 ] && ok "three free slots: three runs for four issues" || ko "three free slots: $(lines "$LAUNCHLOG") launches: '$r'"
[ "$(marked 21 22 23 24)" = 4 ] && [ "$(childblocks)" = 4 ] && ok "three free slots: every issue is a child of one of them" || ko "three free slots: $(marked 21 22 23 24) markers, $(childblocks) CHILD blocks"

# 1e) #728 AC4: the token pause starts no run, whatever the free slots.
scene tokenpause
issues '[{"number":21,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":22,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":23,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
touch "$d/st/token-pause"; r=$(run)
[ "$(lines "$LAUNCHLOG")" = 0 ] && [ "$(marked 21 22 23)" = 0 ] && ok "the token pause starts no run" || ko "the token pause started $(lines "$LAUNCHLOG") run(s): '$r'"

# 1b) (#670) A redispatch is a repair, not a repeat: an issue whose last dispatch failed carries that reason on its own CHILD
#     line, so the child sees the failure it must fix. An issue with no failed dispatch carries none.
scene repair
issues '[{"number":41,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":42,"title":"a","body":"c","labels":[{"name":"wave:01"}]}]'
SG_STATE="$d/st" SG_REPO=o/r python3 "$T/scripts/issue_ledger.py" fail 'o/r#41' run-x '' 'the local gates failed: fast-gate: the base battery failed at ruff: tests/t.py:8:8 F401' >/dev/null   # the issue's ledger file (#673)
r=$(run)
line41=$($G -E '^CHILD' "$MKLOG.body" | $G ' 41 ')
case "$line41" in *"an earlier attempt failed the entry gate: the local gates failed: fast-gate: the base battery failed at ruff: tests/t.py:8:8 F401"*) ok "a redispatched issue carries its last gate failure on its CHILD line";; *) ko "the CHILD line for 41: '$line41' (pass: $r)";; esac
case "$($G -E '^CHILD' "$MKLOG.body" | $G ' 42 ')" in *"earlier attempt"*) ko "an issue with no failed dispatch carries a failure line";; *) ok "an issue with no failed dispatch carries no failure line";; esac

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
slots 1; five; r=$(run)
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
slots 1; five; r=$(run); sm=$(seedmax)
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

# Y1 (#641): `loop-bug:landing` names a loop defect in the landing path. It is a loop-bug too, so it halts dispatch as well.
scene loopbugland
issues '[{"number":93,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":641,"title":"wave.sh land drops a child","body":"b","labels":[{"name":"loop-bug:landing"}]}]'
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 0 ] && [ "$(marked 93)" = 0 ] && ok "Y1: a loop-bug:landing issue halts the frontier pass" || ko "Y1: the pass launched with a landing loop defect open: '$r'"
case "$r" in *"LOOP STOP: #641 is a loop defect; the fix lands outside the loop"*) ok "Y1: the stop names the issue";; *) ko "Y1: the stop is not named: '$r'";; esac

# Y2 (#615): under SG_MIN_FREE_GB GiB free on the volume of $SG_STATE or of $TMPDIR, the frontier pass dispatches nothing
# and names the volume in the loop log; at or over the floor it dispatches as before.
scene diskstop
issues '[{"number":92,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(SG_MIN_FREE_GB=999999 run)
[ "$(lines "$LAUNCHLOG")" = 0 ] && [ "$(marked 92)" = 0 ] && ok "Y2: a disk under the floor halts the frontier pass" || ko "Y2: the pass launched onto a full disk: '$r'"
$G -qE "^DISK STOP: " "$d/st/cycle.log" 2>/dev/null && ok "Y2: the stop is published to the loop log" || ko "Y2: cycle.log: $(cat "$d/st/cycle.log" 2>/dev/null)"
r=$(SG_MIN_FREE_GB=0 run)
[ "$(marked 92)" = 1 ] && ok "Y2: over the floor the pass dispatches" || ko "Y2: over the floor: '$r'"

# Y3 (#616): a bug goes first. autobug writes the startable bugs it left without a run to $SG_STATE/bugs-waiting; while
# that count is over 0 and fresh (30 min), the frontier pass dispatches nothing and says why. A count of 0, a missing file
# or a stale one holds nothing.
scene bugfirst
issues '[{"number":93,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
echo 2 > "$d/st/bugs-waiting"
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 0 ] && [ "$(marked 93)" = 0 ] && ok "Y3: a waiting bug halts the frontier pass" || ko "Y3: the pass dispatched a feature while a bug waits: '$r'"
case "$r" in *"BUG FIRST: 2 startable bug(s) wait for a run; no feature run this pass"*) ok "Y3: the hold names the count";; *) ko "Y3: no hold line: '$r'";; esac
$G -qxF "BUG FIRST: 2 startable bug(s) wait for a run; no feature run this pass" "$d/st/cycle.log" 2>/dev/null && ok "Y3: the hold is published to the loop log" || ko "Y3: cycle.log: $(cat "$d/st/cycle.log" 2>/dev/null)"
touch -t "$(date -r $(( $(date +%s) - 1860 )) +%Y%m%d%H%M.%S)" "$d/st/bugs-waiting"
r=$(run)
[ "$(marked 93)" = 1 ] && ok "Y3: a stale count holds nothing" || ko "Y3: a stale count held: '$r'"
scene bugfirst0
issues '[{"number":94,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
echo 0 > "$d/st/bugs-waiting"
r=$(run)
[ "$(marked 94)" = 1 ] && ok "Y3: no waiting bug holds nothing" || ko "Y3: a zero count held: '$r'"

# Z (#562): an interim limit until the review moves into the run. childcc writes the count of finished children that wait
# for review to $SG_STATE/childcc-waiting; over SG_DISPATCH_MAX_WAITING the frontier pass dispatches nothing and says why,
# and at the cap it dispatches as before. The other edges (no cap, no file, a stale file) are the shared helper's, in
# test-autobug.sh case 19.
scene hold
issues '[{"number":95,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
echo 4 > "$d/st/childcc-waiting"
r=$(SG_DISPATCH_MAX_WAITING=3 run)
[ "$(lines "$LAUNCHLOG")" = 0 ] && [ "$(marked 95)" = 0 ] && ok "Z: over the cap the frontier pass dispatches nothing" || ko "Z: the pass dispatched over the cap: '$r'"
$G -qxF "DISPATCH HOLD: 4 children wait for review (cap 3); no new run this pass" <<<"$r" && ok "Z: the hold names the count and the cap" || ko "Z: no hold line: '$r'"
echo 3 > "$d/st/childcc-waiting"
r=$(SG_DISPATCH_MAX_WAITING=3 run)
[ "$(marked 95)" = 1 ] && ok "Z: at the cap the frontier pass dispatches" || ko "Z: no dispatch at the cap: '$r'"

# S (#703, ADR-0150): a run never holds conflicting siblings. Two tickets of one spec, by the native sub-issue relation or a
#   "SPEC #N" line in the body, never go out as two children of one run: the first takes the run, the other waits for a
#   later pass, and the pass names the split and its reason. Two tickets that name one file, however the path is written,
#   are one child, never two siblings.
scene siblings 24
issues '[{"number":700,"title":"SPEC","body":"b","sub_issues":[101,102],"labels":[{"name":"spec"}]},{"number":101,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":102,"title":"a","body":"c","labels":[{"name":"wave:01"}]},{"number":103,"title":"a","body":"Part of SPEC #700, phase 02.","labels":[{"name":"wave:01"}]},{"number":104,"title":"a","body":"edit ./squad/a.py","labels":[{"name":"wave:01"}]},{"number":105,"title":"a","body":"edit squad/x/../a.py too","labels":[{"name":"wave:01"}]},{"number":106,"title":"a","body":"other","labels":[{"name":"wave:01"}]}]'
cp "$(dirname "$src")/cluster.py" "$T/scripts/"; cp "$(dirname "$src")/siblings.py" "$T/scripts/" 2>/dev/null
r=$(run); rm -f "$T/scripts/cluster.py" "$T/scripts/siblings.py"
[ "$(marked 101)" = 1 ] && [ "$(marked 102 103)" = 0 ] && ok "S: one run takes one ticket of a spec; its siblings wait" || ko "S: spec siblings: $(marked 101) of #101, $(marked 102 103) of #102 #103 marked: '$r'"
case "$r" in *"FRONTIER split #102: a sibling of #101 in spec #700 (ADR-0150); it waits for a later run"*) ok "S: the pass names a split of the native relation";; *) ko "S: no split line for #102: '$r'";; esac
case "$r" in *"FRONTIER split #103: a sibling of #101 in spec #700 (ADR-0150); it waits for a later run"*) ok "S: the pass names a split of a body spec line";; *) ko "S: no split line for #103: '$r'";; esac
[ "$(childblocks)" = 3 ] && $G -q '^CHILD — GitHub issue(s) 104, 105 ' "$MKLOG.body" && ok "S: two tickets that name one normalized path are one child" || ko "S: children: '$($G '^CHILD' "$MKLOG.body")'"
[ "$(marked 104 105 106)" = 3 ] && ok "S: the tickets with no conflict still go out" || ko "S: $(marked 104 105 106) of #104 #105 #106 marked: '$r'"
[ "$(lines "$LAUNCHLOG")" = 3 ] && ! $G -qE '^CHILD .*[ ,](102|103)[ ,]' "$MKLOG.body" && ok "S: three runs in one pass, and no run of the pass holds a sibling (#728)" || ko "S: $(lines "$LAUNCHLOG") runs; CHILD lines: '$($G '^CHILD' "$MKLOG.body")'"

# S2 (#724): the to-tickets body names its spec as a "## Parent" heading with the number on a later line, and the REST
#   open-issue list carries no sub_issues field. Two such tickets of one spec still never share a run.
scene siblings-heading 24
issues '[{"number":710,"title":"SPEC","body":"b","labels":[{"name":"spec"}]},{"number":201,"title":"a","body":"## Parent\n\n#710\n\n## What to build\n\nx","labels":[{"name":"wave:01"}]},{"number":202,"title":"a","body":"## Parent\n\n#710\n\n## What to build\n\ny","labels":[{"name":"wave:01"}]}]'
cp "$(dirname "$src")/cluster.py" "$T/scripts/"; cp "$(dirname "$src")/siblings.py" "$T/scripts/" 2>/dev/null
r=$(run); rm -f "$T/scripts/cluster.py" "$T/scripts/siblings.py"
[ "$(marked 201)" = 1 ] && [ "$(marked 202)" = 0 ] && ok "S2: a Parent heading links a ticket to its spec; the sibling waits" || ko "S2: $(marked 201) of #201, $(marked 202) of #202 marked: '$r'"
case "$r" in *"FRONTIER split #202: a sibling of #201 in spec #710 (ADR-0150); it waits for a later run"*) ok "S2: the pass names the split";; *) ko "S2: no split line for #202: '$r'";; esac

# L (ADR-0149 rule 4): a corrupt ledger holds dispatch for that issue, and only that one. The pass names the issue and the
# cause; it never reads the old counters in its place.
scene ledger
issues '[{"number":71,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":72,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
mkdir -p "$SG_STATE/state"; printf '{"own": {' > "$SG_STATE/state/o__r__71.json"
r=$(run)
[ "$(marked 71)" = 0 ] && case "$r" in *"FRONTIER skip #71: "*corrupt*) true;; *) false;; esac && ok "L: a corrupt ledger holds its issue, and the pass names the cause" || ko "L: #71 with a corrupt ledger: '$r'"
[ "$(marked 72)" = 1 ] && ok "L: an issue beside it goes out" || ko "L: #72 did not go out: '$r'"

# AC (#729): a ticket whose acceptance commands name a missing repository script is not dispatched. It gets one comment that
#   names the path, and needs-info; the pass logs the refusal. A path the ticket creates is not missing; no AC, no change.
scene acpaths
cp "$(dirname "$src")/acpaths.py" "$T/scripts/" 2>/dev/null; mkdir -p "$SG_CHECKOUT/scripts"; echo x > "$SG_CHECKOUT/scripts/here.sh"
export GHLOG=$d/gh.log
issues '[{"number":81,"title":"a","body":"## What to build\n\nx\n\n## Acceptance criteria\n\n- [ ] `python3 scripts/checks/gone.py` passes.\n- [ ] `bash scripts/here.sh` passes.","labels":[{"name":"wave:01"}]},{"number":82,"title":"a","body":"## What to build\n\nAdd `scripts/checks/made.py`.\n\n## Acceptance criteria\n\n- [ ] `python3 scripts/checks/made.py` passes.","labels":[{"name":"wave:01"}]},{"number":83,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 81)" = 0 ] && ok "AC: a ticket whose acceptance command names a missing script is not dispatched" || ko "AC: #81 was dispatched: '$r'"
[ "$(marked 82 83)" = 2 ] && ok "AC: a ticket that creates its script, and one with no acceptance command, are dispatched" || ko "AC: $(marked 82 83) of #82 #83 marked: '$r'"
case "$r" in *"FRONTIER refuse #81: "*"scripts/checks/gone.py"*needs-info*) ok "AC: the pass logs the refusal with the path";; *) ko "AC: no refusal line for #81: '$r'";; esac
cq81=$(cat "$d/st/cq/"*"#81-"* 2>/dev/null); nq=$(ls "$d/st/cq" | $G -c -- '#81-')
[ "$nq" = 1 ] && case "$cq81" in *scripts/checks/gone.py*needs-info*) true;; *) false;; esac && ok "AC: the issue gets one comment that names the path and needs-info" || ko "AC: comments for #81 ($nq): '$cq81'"
$G -q 'issues/81/labels.*needs-info' "$GHLOG" && ok "AC: the issue is labelled needs-info" || ko "AC: no needs-info label write: '$(cat "$GHLOG")'"
case "$cq81" in *scripts/here.sh*) ko "AC: the comment names a path the repository holds";; *) ok "AC: the comment names only the missing path";; esac
r=$(run)
[ "$(marked 81)" = 0 ] && [ "$(ls "$d/st/cq" | $G -c -- '#81-')" = 1 ] && ok "AC: a second pass dispatches nothing and adds no comment" || ko "AC: second pass: $(marked 81) marked, $(ls "$d/st/cq"): '$r'"
unset GHLOG; rm -f "$T/scripts/acpaths.py"

# I1 (#733): a pass that leaves Todo cards and picks none says so in one line, always. A silent frontier and a stuck one read
#     the same in the log, and the owner found the last stall, not the loop. The line names the count of every reason a card
#     waits; the per-card detail follows only when the reasons changed since the previous pass (a hash in auto/frontier-why),
#     so a quiet loop costs one line a beat and a changed one explains itself.
scene idle
issues '[{"number":301,"title":"a","body":"b","unready":true,"labels":[{"name":"enhancement"}]},{"number":302,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":303,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
mkdir -p "$SG_STATE/state"; printf '{"own": {' > "$SG_STATE/state/o__r__302.json"   # a corrupt ledger holds #302 (ADR-0149)
printf '#!/bin/bash\ncase "$1" in 303) echo "WAIT #1 open";; *) echo READY;; esac\n' > "$T/scripts/stack.sh"; chmod +x "$T/scripts/stack.sh"   # #303 waits on its blocker
r=$(run)
case "$r" in *"FRONTIER idle: 3 todo, 0 startable (1 no go label, 1 held, 0 sibling, 0 no slot, 1 blocked, "*) ok "I1: the idle line names the count of every reason";; *) ko "I1: no idle line: '$r'";; esac
[ "$($G -c '^FRONTIER idle:' <<<"$r")" = 1 ] && ok "I1: one idle line a pass" || ko "I1: $($G -c '^FRONTIER idle:' <<<"$r") idle line(s): '$r'"
$G -q '^FRONTIER why #301: no go label' <<<"$r" && $G -q '^FRONTIER why #302: held' <<<"$r" && $G -q '^FRONTIER why #303: blocked' <<<"$r" \
  && ok "I1: the first idle pass names every card, with its own reason" || ko "I1: detail: '$($G '^FRONTIER why #' <<<"$r")'"
$G -qxF "$($G -m1 '^FRONTIER idle:' <<<"$r")" "$d/st/cycle.log" && ok "I1: the line is published to the loop log" || ko "I1: cycle.log: $(cat "$d/st/cycle.log" 2>/dev/null)"
r=$(run)
[ "$($G -c '^FRONTIER idle:' <<<"$r")" = 1 ] && [ "$($G -c '^FRONTIER why #' <<<"$r")" = 0 ] \
  && ok "I1: the same reasons write the line again and the detail once" || ko "I1: the second pass: '$r'"
printf '#!/bin/bash\necho READY\n' > "$T/scripts/stack.sh"
issues '[{"number":301,"title":"a","body":"b","unready":true,"labels":[{"name":"enhancement"}]},{"number":302,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":303,"title":"a","body":"b","unready":true,"labels":[{"name":"enhancement"}]}]'
r=$(run)
case "$r" in *"FRONTIER idle: 3 todo, 0 startable (2 no go label, 1 held, "*)
  [ "$($G -c '^FRONTIER why #' <<<"$r")" = 3 ] && ok "I1: a changed reason set explains itself again" \
    || ko "I1: a changed reason set wrote $($G -c '^FRONTIER why #' <<<"$r") detail line(s)";;
  *) ko "I1: a changed reason set wrote no line: '$r'";; esac

# I2 (#733): a pass that dispatches writes no idle line.
scene idlego
issues '[{"number":311,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 311)" = 1 ] && ! $G -q '^FRONTIER idle:' <<<"$r" && ok "I2: a pass that dispatches writes no idle line" || ko "I2: '$r'"

# I3 (#733): a pass with no free run slot has nothing startable for a reason of its own, and says so.
scene noslot
slots 1; mkdir -p "$d/co/runs/live"; echo '{"event":"spawn_requested"}' > "$d/co/runs/live/record.jsonl"; echo live > "$d/st/active-runs.txt"   # the one slot is taken
issues '[{"number":321,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":322,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
case "$r" in *"FRONTIER idle: 2 todo, 0 startable ("*"2 no slot"*) ok "I3: a pass with no free run slot says so";; *) ko "I3: '$r'";; esac
[ "$(lines "$LAUNCHLOG")" = 0 ] && ok "I3: and starts nothing" || ko "I3: $(lines "$LAUNCHLOG") launch(es): '$r'"

# I4 (#733): a pass with no Todo card has nothing to report: the line is for a frontier that holds work and starts none.
scene notodo
issues '[{"number":331,"title":"a","body":"b","card":"Backlog","labels":[{"name":"wave:01"}]}]'
r=$(run)
! $G -q '^FRONTIER idle:' <<<"$r" && ok "I4: a pass with no Todo card writes no idle line" || ko "I4: '$r'"

rm -rf "$T"
[ "$fail" = 0 ] && { echo "autofrontier one-run: all checks green"; exit 0; }
echo "autofrontier one-run: FAILED"; exit 1
