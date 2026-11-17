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
export SG_MIN_FREE_GB=0   # the dispatchers pin their disk floor: the real volume must not stop a fixture pass (#792); a case that tests the stop sets its own
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
  jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:(.labels//[])},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),subIssues:{nodes:[(.sub_issues//[])[]|{number:.}]},comments:{nodes:[(.comments//[])[]|{body:.}]}}]}}}}' "$GH_ISSUES"; exit 0;; esac
case "$p" in
  */pulls?state=open*)    f=$GH_OPEN;;
  */pulls?state=closed*)  f=$GH_CLOSED;;
  */issues?state=open*)   f=$GH_ISSUES; [ -n "$GH_FRONTIER_ONLY" ] && [[ "$p" == *"labels=frontier"* ]] && f=$GH_FRONTIER;;
  */issues/*/comments*)   # the comments of one fixture issue, in the list read's shape (#1297): the per-issue marker read runs for a labelled issue
    n=$(printf '%s' "$p" | sed -n 's#.*/issues/\([0-9]*\)/comments.*#\1#p')
    jq -c --argjson n "$n" '[.[]|select(.number==$n)][0].comments // [] | map({body:.})' "$GH_ISSUES" > "$GH_ISSUES.comments.json"; f=$GH_ISSUES.comments.json;;
  */issues/*)             f=$GH_ISSUE1; [ -f "$GH_ISSUEDIR/${p##*/}.json" ] && f=$GH_ISSUEDIR/${p##*/}.json;;   # one fixture per issue when a case wrote it
  *) exit 1;;
esac
if [ -n "$GH_LABEL_FAIL" ]; then   # GH_LABEL_FAIL=<n>: the label write on issue n is refused (#1676)
  n=$(printf '%s' "$p" | sed -n 's#.*/issues/\([0-9]*\)/labels.*#\1#p')
  [ "$n" = "$GH_LABEL_FAIL" ] && { echo '{"message":"the label write was refused","status":500}'; exit 1; }
fi
if [ -n "$q" ]; then jq -r "$q" "$f"; else cat "$f"; fi
GH
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\necho READY\n' > "$T/scripts/stack.sh"; chmod +x "$T/scripts/stack.sh"
# The dispatchers share one claim namespace and one read of the live requests (startability.sh, #428), so the stand-in
# carries the real file's own reads and fakes only what this suite fixes by hand.
cp "$(dirname "$src")/startability.sh" "$T/scripts/startability-real.sh"; cp "$(dirname "$src")/skiplabels.sh" "$T/scripts/skiplabels.sh"; cp "$(dirname "$src")/issue_ledger.py" "$T/scripts/"; cp "$(dirname "$src")/liveholds.py" "$T/scripts/"
printf '#!/bin/bash\n. "$(dirname "$0")/startability-real.sh"\nsg_fork_only() { return 1; }\nsg_fork_preamble() { :; }\nsg_fork_branch() { echo "gui/$1"; }\nsg_fork_branch_pattern() { echo "gui/%%n"; }\nsg_issue_worktree_note() { :; }\n' > "$T/scripts/startability.sh"
# mkreq.sh appends the body it was handed (one pass may build several requests, #728) and prints a request path (or refuses when MK_REFUSE is set); launch.py only counts
printf '#!/bin/bash\n[ -n "$MK_REFUSE" ] && { echo "refusing: stub" >&2; exit 1; }\n[ "$1" = --paths ] && exec bash "$(dirname "$0")/mkreq-real.sh" "$@"\n[ -n "$MK_REAL" ] && { bash "$(dirname "$0")/mkreq-real.sh" "$@" || exit 1; echo x >> "$MKLOG"; cat "$2" >> "$MKLOG.body"; exit 0; }\necho x >> "$MKLOG"; cat "$2" >> "$MKLOG.body"; echo "$HOME/req.txt"\n' > "$T/scripts/mkreq.sh"
printf 'import os,sys\nopen(os.environ["LAUNCHLOG"], "a").write("launch\\n")\nif os.environ.get("LAUNCH_REFUSE"): print("refused: the first coding model x is not in the router envelope", file=sys.stderr); sys.exit(1)\n' > "$T/scripts/launch.py"
chmod +x "$T/scripts/mkreq.sh"
# board.sh keeps the call it was handed: <owner> <project> <column> <issue>... (#33: the card move was a bare `true`, so no case could say which cards moved)
printf '#!/bin/bash\necho "$*" >> "$BOARDLOG"\n' > "$T/board.sh"; chmod +x "$T/board.sh"
cp "$src" "$T/scripts/autofrontier.sh"; cp "$(dirname "$src")"/{ghcall.sh,ghread.py,ghthrottle.py,issue_ledger.py,workbranches.sh,loopbug.sh,watchd.sh,basered.sh} "$T/scripts/"   # the pass reads the open-issue list through the door
# The issues an open work branch names (#361, workbranches.sh): the real read is `git ls-remote` against github.com, which no
# fixture may reach, so the stand-in answers WORK_ISSUES (none unless a case sets it) (#756).
printf '#!/bin/bash\nsg_work_issues() { echo "${WORK_ISSUES:-}"; }\n' > "$T/scripts/workbranches.sh"
# The seam's door, stubbed at the script's own name (#550): one `<issue>\t<state>` line per card. A fixture issue's card is
# in Todo unless it names its own "card" ("" is no card); CARDS_DOWN is a board that does not answer.
cat > "$T/scripts/issue_state.py" <<'PY'
import json, os, sys
if sys.argv[1:2] not in ([], ["--go"]) or os.environ.get("CARDS_DOWN"): print("no board", file=sys.stderr); sys.exit(1)
for i in json.load(open(os.environ["GH_ISSUES"])):
    if i.get("card", "Todo"): print(f"{i['number']}\t{i.get('card', 'Todo')}")
for n in os.environ.get("CLOSED_CARDS", "").split():   # #1335: a closed issue whose card still reads Todo (the board names no state)
    print(f"{n}\tTodo")
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
run() { SG_SINGLE_AUTOFRONTIER=1 bash "$T/scripts/autofrontier.sh" 2>&1 | /usr/bin/tee -a "$T/pass.out"; /bin/sleep 0.6; }   # the launch is detached: give it time to count itself; pass.out keeps every pass for the fixture check below
lines() { local c; c=$($G -c . "$1" 2>/dev/null); echo "${c:-0}"; }
marked() { local n c=0; for n in "$@"; do [ -e "$SG_STATE/auto/disp-$n" ] && c=$((c+1)); done; echo $c; }
asked() { sed -nE 's/.*one entry for each of the ([0-9]+) child.*/\1/p' "$MKLOG.body" | head -1; }
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
[ "$(sed -nE 's/.*one entry for each of the ([0-9]+) child.*/\1/p' "$MKLOG.body" | sort -u | tr -d '\n')" = 1 ] && ok "each request asks for one child" || ko "each request asks for one child: '$($G -o 'entry for each of the [0-9]* child' "$MKLOG.body")'"
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

# G1 (#1198) A child's own fast gate is its LAST step, after the final edit and commit, so the tree it proved is the tree
#     the child hands in. A child that cannot gate last says so, because the wave entry gate then runs its own battery.
scene gatelast 24
issues '[{"number":551,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run); body=$(cat "$MKLOG.body" 2>/dev/null)
case "$body" in *'Your last step, after your final edit and commit, is the entry gate: run "bash contrib/squad-goal/scripts/fast-gate.sh"'*) ok "G1: the request makes the fast gate the child's last step, after the final edit and commit";; *) ko "G1: the brief does not make the fast gate the last step: '$($G 'entry gate' "$MKLOG.body")'";; esac
case "$body" in *"make no edit and no commit"*) ok "G1: the request forbids an edit after the gate";; *) ko "G1: the request does not forbid an edit after the gate";; esac
case "$body" in *"If the gate fails, fix what it names and run the gate again"*) ok "G1: a red gate is repaired and re-run, so a child never hands in a red tree";; *) ko "G1: the brief forbids repairing a red gate";; esac
case "$body" in *"Once the gate passes, make no edit and no commit"*) ok "G1: the no-edit rule binds to a passing gate";; *) ko "G1: the no-edit rule is not bound to a passing gate";; esac
case "$body" in *"cannot make it your last step"*"say so"*"own battery"*) ok "G1: a child that cannot gate last says so, and the entry gate falls back to its own battery";; *) ko "G1: no fallback line: '$($G 'last step' "$MKLOG.body")'";; esac

# G2 (#1198) A repository with no fast gate names none: a missing gate is not a failure, and the child runs none.
scene gateless 24
issues '[{"number":552,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
export SG_FAST_GATE_CMD=
r=$(run); unset SG_FAST_GATE_CMD; body=$(cat "$MKLOG.body" 2>/dev/null)
case "$body" in *fast-gate.sh*) ko "G2: a repository with no fast gate still names fast-gate.sh";; *) ok "G2: no fast gate configured names no script";; esac
case "$body" in *"No fast gate is configured"*"not a failure"*) ok "G2: the request says no fast gate is configured and that it is not a failure";; *) ko "G2: no no-fast-gate line: '$body'";; esac

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

# 4d) (#1829) A live dispatch run holds every issue its request names from the start, and a record written in the router's
#     own words holds its children too. The frontier read only `spawn_accepted`, and only in the forms "GitHub issue #N" and
#     "GitHub issues N, M": a run whose router declined one child, and whose accepted children are written "issue o/r #N" and
#     "Refs #N", held nothing, so the pass sent the declined issue out on every cycle and the start refused it every time.
scene heldrequest 24 4
mkdir -p "$d/co/runs/live"
printf 'CHILD - GitHub issue(s) 1734 (bug; one root cause, one branch):\n--- issue #1734\nheld by the router\n\nCHILD - GitHub issue(s) 1750:\n--- issue #1750\n\nCHILD - GitHub issue(s) 1798:\n--- issue #1798\n' > "$d/co/runs/live/request.txt"
printf '{"event":"spawn_accepted","child":"c1","definition":"work on issue o/r #1750"}\n{"event":"spawn_accepted","child":"c2","definition":"Refs #1798"}\n' > "$d/co/runs/live/record.jsonl"
echo live > "$d/st/active-runs.txt"
issues '[{"number":1734,"title":"a","body":"b"},{"number":1750,"title":"a","body":"b"},{"number":1798,"title":"a","body":"b"},{"number":12,"title":"a","body":"b"}]'
r=$(run)
[ "$(marked 1734 1750 1798)" = 0 ] && ok "the issues a live run's request names are not dispatched again" || ko "a live run's request names 1734, 1750 and 1798 and the pass dispatched $(marked 1734 1750 1798) of them"
[ "$(marked 12)" -gt 0 ] && ok "the issue no live run names still goes out" || ko "no other startable issue went out: '$r'"

# 4c) (#948) A live run's accepted children never count against the run slots. The leases of every repository and the
#     children the live runs of this one hold are two different counts, and the pass read the first as both: three runs
#     with one child each looked like six holders against a ceiling of six, so every Todo card waited on a slot that
#     was free and the pass said "no slot" for all of them.
scene leasespawn 4
five
slots 6   # the ceiling of six runs: three leases plus three accepted children filled it while three slots stood free
mkdir -p "$T/caps/runs"
for i in 1 2 3; do
  printf '{"holder":"run-%s","pid":%s,"since":%s}' "$i" "$$" "$(date +%s)" > "$T/caps/runs/run-$i.json"
  mkdir -p "$d/co/runs/run-$i"
  printf '{"event":"spawn_accepted","child":"c%s","task":"GitHub issue #90%s"}\n' "$i" "$i" > "$d/co/runs/run-$i/record.jsonl"
  echo "$d/co/runs/run-$i" >> "$d/st/active-runs.txt"
done
r=$(run)
[ "$(reqs)" -gt 0 ] && ok "three live runs holding one child each still leave the run slots free" || ko "three live runs holding one child each took every run slot: $(echo "$r" | head -2)"
rm -rf "$T/caps/runs"

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

# 9) #1593: the bug lane's own labels (no-auto, question) are a hold for autobug.sh alone. The frontier still starts a card
#    that carries one beside its go label, and health.sh reads the same lane, so that card is never hidden as startable work.
scene bugskip 24
issues '[{"number":53,"title":"a","body":"b","labels":[{"name":"no-auto"},{"name":"wave:01"}]},{"number":54,"title":"a","body":"b","labels":[{"name":"question"},{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 53 54)" = 2 ] && ok "a bug-lane label never holds a frontier card" || ko "the frontier skipped a bug-lane label: $(marked 53 54) marker(s): '$r'"

# 9c) (#1673) a holder or needs-triage label holds a Todo card, with or without the go label: the frontier never takes it.
scene holders 24
issues '[{"number":55,"title":"a","body":"b","labels":[{"name":"frontier"},{"name":"ready-for-human"},{"name":"wave:01"}]},{"number":56,"title":"a","body":"b","labels":[{"name":"frontier"},{"name":"ready-for-HAL9K"},{"name":"wave:01"}]},{"number":57,"title":"a","body":"b","labels":[{"name":"frontier"},{"name":"needs-triage"},{"name":"wave:01"}]},{"number":59,"title":"a","body":"b","labels":[{"name":"ready-for-human"},{"name":"wave:01"}]},{"number":58,"title":"a","body":"b","labels":[{"name":"frontier"},{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 55 56 57 59)" = 0 ] && ok "a holder or needs-triage label keeps a frontier card out, beside or without the go label" || ko "a holder card was dispatched: $(marked 55 56 57 59) marker(s): '$r'"
[ "$(marked 58)" = 1 ] && ok "a go-labelled card beside the holders is dispatched" || ko "the card beside the holders was not dispatched: '$r'"

# 9d) (#1673) the floors hold under a profile list that names none of them: no holder card is dispatched, with or without ready-for-agent.
scene floors 24
sed -i.bak 's/SG_FRONTIER_SKIP_LABELS=[^;]*;/SG_FRONTIER_SKIP_LABELS=wontfix;/' "$T/scripts/env.sh" && rm -f "$T/scripts/env.sh.bak"
issues '[{"number":55,"title":"a","body":"b","labels":[{"name":"ready-for-human"},{"name":"wave:01"}]},{"number":56,"title":"a","body":"b","labels":[{"name":"ready-for-HAL9K"},{"name":"wave:01"}]},{"number":57,"title":"a","body":"b","labels":[{"name":"needs-triage"},{"name":"wave:01"}]},{"number":58,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":60,"title":"a","body":"b","labels":[{"name":"ready-for-agent"},{"name":"ready-for-HAL9K"},{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 55 56 57 60)" = 0 ] && [ "$(marked 58)" = 1 ] && ok "a profile list that names no floor still holds the holder cards, and the plain card is dispatched" || ko "a profile list lifted a floor: $(marked 55 56 57 60) holder marker(s), $(marked 58) plain: '$r'"

# 9e) (#1696) needs-triage alone is the hold, under a profile list that names its own labels and no floor: a card that also
#     carries ready-for-agent stays out, and the plain card beside it still goes out.
scene triagefloor 24
sed -i.bak 's/SG_FRONTIER_SKIP_LABELS=[^;]*;/SG_FRONTIER_SKIP_LABELS=spec,needs-owner,needs-ratification,needs-info,frozen:owner,frozen:dependency,wontfix;/' "$T/scripts/env.sh" && rm -f "$T/scripts/env.sh.bak"
issues '[{"number":55,"title":"a","body":"b","labels":[{"name":"ready-for-agent"},{"name":"needs-triage"},{"name":"wave:01"}]},{"number":58,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 55)" = 0 ] && ok "needs-triage alone holds a card that also carries ready-for-agent, under the profile's own list" || ko "the frontier dispatched a needs-triage card: $(marked 55) marker(s): '$r'"
[ "$(marked 58)" = 1 ] && ok "the plain card beside a needs-triage hold is dispatched" || ko "the plain card beside a needs-triage hold was not dispatched: '$r'"

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

# LB (#1297): the label is a hold only while its marker holds. A label beside a marker another build wrote does not hold the issue
#    (the marker re-arms it); a label beside the current build's marker holds it. The build is the checkout's HEAD.
scene labelbuild
git -C "$d/co" init -q && git -C "$d/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
cur=$(git -C "$d/co" rev-parse --short=12 HEAD)
issues "[{\"number\":55,\"title\":\"a\",\"body\":\"b\",\"labels\":[{\"name\":\"wave:01\"},{\"name\":\"frozen:not-startable\"}],\"comments\":[\"SQUAD-NOT-STARTABLE: build=000000000000 3 runs failed\"]},{\"number\":56,\"title\":\"a\",\"body\":\"b\",\"labels\":[{\"name\":\"wave:01\"},{\"name\":\"frozen:not-startable\"}],\"comments\":[\"SQUAD-NOT-STARTABLE: build=$cur 3 runs failed\"]}]"
r=$(run)
[ "$(marked 55)" = 1 ] && ok "LB: a label beside another build's marker goes out" || ko "LB: #55 held by its label beside another build's marker: '$r'"
[ "$(marked 56)" = 0 ] && ok "LB: a label beside the current build's marker still holds" || ko "LB: #56 went out beside the current build's marker: '$r'"

# Y: the frozen:not-startable label is the visible marker: an issue that carries it is skipped as the comment is, with no comment on it.
scene frozennotstartable
issues '[{"number":53,"title":"a","body":"b","labels":[{"name":"wave:01"},{"name":"frozen:not-startable"}],"comments":["a note"]},{"number":54,"title":"a","body":"b","labels":[{"name":"wave:01"}],"comments":["a note"]}]'
r=$(run)
[ "$(marked 53)" = 0 ] && ok "Y: an issue carrying frozen:not-startable is skipped" || ko "Y: the labelled #53 went out: '$r'"
[ "$(marked 54)" = 1 ] && ok "Y: an issue without the hold label goes out" || ko "Y: #54 did not go out: '$r'"

# V (#468): on the shipped vocabulary the dispatch writes Doing. "In Progress" is no column there, and the board refused it in
# silence, so a dispatched card stayed on Todo until the janitor's next pass.
scene vocab
issues '[{"number":61,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(SG_BOARD_VOCAB=shipped run)
$G -qE "^(--add )?o 1 Doing( [0-9]+)* 61( |\$)" "$BOARDLOG" && ok "V: a dispatch on the shipped vocabulary writes Doing" || ko "V: the dispatch wrote $(cat "$BOARDLOG")"
$G -q "In Progress" "$BOARDLOG" && ko "V: the dispatch still writes In Progress on the shipped vocabulary" || ok "V: no In Progress write on the shipped vocabulary"

# W (#485, re-anchored by #1676): the label is no longer the permission (#485's rule is superseded). A Todo card with
# no triage label is taken and labelled by the pass; the column still decides, so the same card in the Backlog is never
# taken, and the pass writes no label for it.
scene label
export GHLOG=$d/gh.log
issues '[{"number":71,"title":"a","body":"b","unready":true,"labels":[{"name":"enhancement"}]},{"number":72,"title":"a","body":"b","card":"Backlog","unready":true,"labels":[{"name":"enhancement"}]},{"number":73,"title":"a","body":"b","labels":[{"name":"enhancement"}]}]'
r=$(run)
[ "$(marked 71)" = 1 ] && ok "W: an untriaged Todo card is taken" || ko "W: the untriaged #71 stayed: '$r'"
[ "$($G -c 'issues/71/labels' "$GHLOG")" = 1 ] && ok "W: the pass writes its label once" || ko "W: label writes for #71: '$($G 'issues/71/labels' "$GHLOG")'"
[ "$(marked 72)" = 0 ] && ok "W: an untriaged card outside Todo is never taken" || ko "W: the Backlog #72 went out: '$r'"
[ "$($G -c 'issues/72/labels' "$GHLOG")" = 0 ] && ok "W: a card outside Todo costs no label write" || ko "W: the Backlog #72 was labelled"
[ "$(marked 73)" = 1 ] && [ "$($G -c 'issues/73/labels' "$GHLOG")" = 0 ] && ok "W: a card that already carries the label costs no write" || ko "W: the labelled #73: $(marked 73) marker(s), '$($G 'issues/73/labels' "$GHLOG")'"
unset GHLOG

# T (#1676): the frontier takes a Todo card with no triage label, writes the label once in the same act and writes the
#    request for it. The write lands after the claim and after every gate, and the pass prints one line for the card.
scene triage
export GHLOG=$d/gh.log
issues '[{"number":501,"title":"a","body":"b","unready":true,"labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 501)" = 1 ] && ok "T: the untriaged Todo card is claimed and dispatched" || ko "T: #501: $(marked 501) marker(s): '$r'"
[ "$($G -c 'issues/501/labels' "$GHLOG")" = 1 ] && ok "T: the pass writes the label exactly once" || ko "T: label writes for #501: '$($G 'issues/501/labels' "$GHLOG")'"
$G -q 'labels\[\]=frontier' "$GHLOG" && ok "T: the write carries the pass's own label" || ko "T: the label write: '$(cat "$GHLOG")'"
$G -q '^FRONTIER triaged #501: ' <<<"$r" && ok "T: the pass prints one line for the card it triaged" || ko "T: no triage line: '$r'"
$G -q '^CHILD — GitHub issue(s) 501 ' "$MKLOG.body" && ok "T: the request is written for the triaged card" || ko "T: no CHILD block: '$(cat "$MKLOG.body")'"
unset GHLOG

# T2 (#1676): a card carrying a holder label dispatches nothing and costs no label write; neither does a card outside Todo.
scene triagehold
export GHLOG=$d/gh.log
issues '[{"number":521,"title":"a","body":"b","unready":true,"labels":[{"name":"ready-for-human"},{"name":"wave:01"}]},{"number":522,"title":"a","body":"b","unready":true,"labels":[{"name":"ready-for-HAL9K"},{"name":"wave:01"}]},{"number":523,"title":"a","body":"b","unready":true,"card":"Backlog","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 521 522 523)" = 0 ] && [ "$($G -c '/labels' "$GHLOG")" = 0 ] && ok "T2: a holder card or a card outside Todo is never taken and costs no write" || ko "T2: $(marked 521 522 523) marker(s), writes: '$(cat "$GHLOG")'"
unset GHLOG

# T3 (#1676): a refused label write drops that card from the request and frees its claim; the other cards still go out,
#    and the next pass retries the card.
scene triagepartial
export GHLOG=$d/gh.log GH_LABEL_FAIL=531
issues '[{"number":531,"title":"a","body":"b","unready":true,"labels":[{"name":"wave:01"}]},{"number":532,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 531)" = 0 ] && ok "T3: the card whose write failed keeps no claim" || ko "T3: #531 kept $(marked 531) marker(s)"
[ "$(marked 532)" = 1 ] && $G -q '^CHILD — GitHub issue(s) 532 ' "$MKLOG.body" && ok "T3: the other card still enters the request" || ko "T3: #532: $(marked 532) marker(s), body: '$(cat "$MKLOG.body")'"
$G -q ' 531' "$MKLOG.body" && ko "T3: the card whose write failed is named in the request" || ok "T3: the request names no failed card"
case "$r" in *"FRONTIER triage failed #531:"*) ok "T3: the pass says the write failed and the card waits";; *) ko "T3: no failure line: '$r'";; esac
unset GH_LABEL_FAIL
r=$(run)
[ "$(marked 531)" = 1 ] && [ "$(lines "$LAUNCHLOG")" = 2 ] && ok "T3: the next pass retries the card and dispatches it" || ko "T3: retry: $(marked 531) marker(s), $(lines "$LAUNCHLOG") launch(es): '$r'"
unset GHLOG

# T4 (#1676): when every label write of the burst fails, the pass writes no request and releases its claims; the idle
#    line gives the card its own reason, the one the tuning report reads.
scene triageburst
export GH_LABEL_FAIL=541
issues '[{"number":541,"title":"a","body":"b","unready":true,"labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 0 ] && [ "$(lines "$MKLOG")" = 0 ] && [ "$(marked 541)" = 0 ] && ok "T4: a failed burst writes no request and releases its claim" || ko "T4: $(lines "$LAUNCHLOG") launch(es), $(lines "$MKLOG") request(s), $(marked 541) marker(s): '$r'"
case "$r" in *"FRONTIER idle: 1 todo, 0 startable (1 triage failed, "*) ok "T4: the idle line gives the card its own reason";; *) ko "T4: no idle line with the triage reason: '$r'";; esac
unset GH_LABEL_FAIL

# X (#550, #1676): the column Todo is the owner's go signal, and it is the only one. A labelled issue in Backlog, or one
# with no card, waits for the owner; a card in Todo goes out with the label or without it (the pass writes it then).
# The cards come from one board read a pass, never one per issue; a board that does not answer dispatches nothing.
scene todo
issues '[{"number":81,"title":"a","body":"b","card":"Backlog","labels":[{"name":"wave:01"}]},{"number":82,"title":"a","body":"b","card":"","labels":[{"name":"wave:01"}]},{"number":83,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":84,"title":"a","body":"b","unready":true,"labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 81)" = 0 ] && ok "X: a labelled issue whose card is in Backlog is not dispatched" || ko "X: #81 in Backlog went out: '$r'"
[ "$(marked 82)" = 0 ] && ok "X: a labelled issue with no card is not dispatched" || ko "X: #82 with no card went out: '$r'"
[ "$(marked 83)" = 1 ] && ok "X: a labelled issue whose card is in Todo goes out" || ko "X: #83 in Todo did not go out: '$r'"
[ "$(marked 84)" = 1 ] && ok "X: a card in Todo without the frontier label goes out too" || ko "X: the untriaged #84 stayed: '$r'"
scene todo-down
issues '[{"number":85,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(CARDS_DOWN=1 run)
[ "$(marked 85)" = 0 ] && [ "$(lines "$LAUNCHLOG")" = 0 ] && ok "X: a board that does not answer dispatches nothing" || ko "X: #85 went out with no board read: '$r'"
case "$r" in *"board"*) ok "X: the pass says the board did not answer";; *) ko "X: the pass is silent about the board: '$r'";; esac

# X2 (#1620, ADR-0142 rule 6): a ticket of a spec is work only once the owner starts the spec. The board's automation adds
# a linked sub-issue to the Project with Status Todo by itself, so the ticket's own column proves nothing about the spec's:
# a spec whose card sits in Backlog holds its ticket, whatever the ticket's card reads, and the pass names the parent spec.
# No card moves. The blocker gate here is the real stack.sh, because that WAIT is the fact the case reads.
scene specbacklog 24
cp "$(dirname "$src")/stack.sh" "$(dirname "$src")/deadlock.py" "$T/scripts/"
issues '[{"number":700,"title":"SPEC","body":"b","card":"Backlog","sub_issues":[801],"labels":[{"name":"spec"}]},{"number":801,"title":"a","body":"b","parent":700,"labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 801)" = 0 ] && [ "$(lines "$LAUNCHLOG")" = 0 ] && ok "X2: a ticket whose spec card sits in Backlog is not dispatched" || ko "X2: #801 went out beside a spec in Backlog: '$r'"
case "$r" in *"spec #700"*"Backlog"*) ok "X2: the wait names the parent spec and its column";; *) ko "X2: the wait does not name the parent spec: '$r'";; esac
[ ! -s "$BOARDLOG" ] && ok "X2: no card moves for a ticket whose spec is not started" || ko "X2: a card moved: '$(cat "$BOARDLOG")'"
printf '#!/bin/bash\necho READY\n' > "$T/scripts/stack.sh"; chmod +x "$T/scripts/stack.sh"

# Y (#596, ADR-0146): an open issue carrying `loop-bug:dispatch` is a defect in the loop's dispatch path. The loop cannot dispatch
# its own fix, so the frontier pass dispatches nothing, names the issue exactly once per pass, and exits 0. The stop is read
# from the open issues the pass already reads, so it costs no second read, and it has no marker: closing the issue resumes
# dispatch at the next pass with no other action.
scene loopbug
issues '[{"number":91,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":596,"title":"The loop cannot land its own fix","body":"b","labels":[{"name":"wave:01"},{"name":"loop-bug:dispatch"}]}]'
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 0 ] && ok "Y: a dispatch-path loop defect halts the frontier pass" || ko "Y: the pass launched with a loop defect open: '$r'"
[ "$(marked 91 596)" = 0 ] && ok "Y: no startable issue is dispatched while the defect is open" || ko "Y: markers: $(marked 91 596)"
case "$r" in *"LOOP STOP: #596 is a loop defect; the fix lands outside the loop"*) ok "Y: the stop names the issue and where the fix lands";; *) ko "Y: the stop is not named: '$r'";; esac
$G -qxF "LOOP STOP: #596 is a loop defect; the fix lands outside the loop" "$d/st/cycle.log" 2>/dev/null && ok "Y: the stop is published to the loop log" || ko "Y: cycle.log: $(cat "$d/st/cycle.log" 2>/dev/null)"
$G -qxF "LOOP STOP: #596 is a loop defect; the fix lands outside the loop" "$d/st/cq/o__r#596-status~loopstop.md" 2>/dev/null && ok "Y: the stop is the issue's line on the board" || ko "Y: the board line is missing: $(ls "$d/st/cq" 2>/dev/null)"
issues '[{"number":91,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 91)" = 1 ] && ok "Y: closing the issue resumes dispatch with no other action" || ko "Y: dispatch did not resume: '$r'"

# Y0 (#1708, ADR-0146): any open loop-bug drains the loop, a bare one too: the pass names it and dispatches nothing. A label
# that only resembles one stops nothing.
scene loopbugbare
issues '[{"number":91,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":596,"title":"The loop cannot land its own fix","body":"b","labels":[{"name":"wave:01"},{"name":"loop-bug"}]}]'
r=$(run)
[ "$(marked 91)" = 0 ] && ok "Y0: a bare loop-bug dispatches nothing" || ko "Y0: the pass dispatched under a bare loop-bug: '$r'"
case "$r" in *"LOOP STOP: #596 is a loop defect; the fix lands outside the loop"*) ok "Y0: the stop names the bare loop-bug";; *) ko "Y0: the stop is not named: '$r'";; esac
scene loopbuglike
issues '[{"number":91,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":596,"title":"x","body":"b","labels":[{"name":"wave:01"},{"name":"loop-buggy"}]}]'
r=$(run)
[ "$(marked 91)" = 1 ] && ok "Y0: a label that only resembles loop-bug stops nothing" || ko "Y0: a lookalike label held the pass: '$r'"

# Y1 (#641): `loop-bug:landing` names a loop defect in the landing path. It is a loop-bug too, so it halts dispatch as well.
scene loopbugland
issues '[{"number":93,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":641,"title":"wave.sh land drops a child","body":"b","labels":[{"name":"loop-bug:landing"}]}]'
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 0 ] && [ "$(marked 93)" = 0 ] && ok "Y1: a loop-bug:landing issue halts the frontier pass" || ko "Y1: the pass launched with a landing loop defect open: '$r'"
case "$r" in *"LOOP STOP: #641 is a loop defect; the fix lands outside the loop"*) ok "Y1: the stop names the issue";; *) ko "Y1: the stop is not named: '$r'";; esac

# Y1b (ADR-0146 decision 2): `loop-bug:infra` names a defect in the loop's infrastructure. The label is the switch that
# outlives a restart, so the frontier pass dispatches nothing while it is open, and resumes when it closes.
scene loopbuginfra
issues '[{"number":94,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":1374,"title":"A fix run proves in the target","body":"b","labels":[{"name":"loop-bug:infra"}]}]'
r=$(run)
[ "$(lines "$LAUNCHLOG")" = 0 ] && [ "$(marked 94)" = 0 ] && ok "Y1b: a loop-bug:infra issue halts the frontier pass" || ko "Y1b: the pass launched with an infrastructure loop defect open: '$r'"
case "$r" in *"LOOP STOP: #1374 is a loop defect; the fix lands outside the loop"*) ok "Y1b: the stop names the issue";; *) ko "Y1b: the stop is not named: '$r'";; esac
issues '[{"number":94,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(run)
[ "$(marked 94)" = 1 ] && ok "Y1b: closing the issue resumes dispatch" || ko "Y1b: dispatch did not resume: '$r'"

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
$G -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} DISPATCH HOLD: 4 children wait for review \(cap 3\); no new run this pass$' <<<"$r" && ok "Z: the hold names the count and the cap, stamped in UTC" || ko "Z: no hold line: '$r'"
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

# P (#1941, ADR-0150): two tickets whose bodies name no file, and whose diffs shared a path in an earlier wave, never share a run
#   while both are open. The wave that refused the second child recorded the pair with the shared paths, and the filter refuses
#   the second and names the pair and the paths. A closed ticket clears its pair. A pair with no record still joins one run.
scene sharedpair 24
issues '[{"number":1798,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":1856,"title":"a","body":"c","labels":[{"name":"wave:01"}]}]'
printf '[{"issues":[1798,1856],"paths":["contrib/squad-goal/scripts/gate.sh","docs/loop-sequence.md"]}]' > "$d/st/shared-pairs.json"
cp "$(dirname "$src")/cluster.py" "$T/scripts/"; cp "$(dirname "$src")/siblings.py" "$T/scripts/" 2>/dev/null
r=$(run); rm -f "$T/scripts/cluster.py" "$T/scripts/siblings.py"
[ "$(marked 1798)" = 1 ] && [ "$(marked 1856)" = 0 ] && ok "P: a recorded shared-path pair never joins one run" || ko "P: $(marked 1798) of #1798, $(marked 1856) of #1856 marked: '$r'"
case "$r" in *"FRONTIER split #1856: a shared-path pair with #1798 (both change contrib/squad-goal/scripts/gate.sh, docs/loop-sequence.md) (ADR-0150); it waits for a later run"*) ok "P: the refusal names the pair and its paths";; *) ko "P: no pair line for #1856: '$r'";; esac
scene sharedpair-none 24
issues '[{"number":1798,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":1856,"title":"a","body":"c","labels":[{"name":"wave:01"}]}]'
cp "$(dirname "$src")/cluster.py" "$T/scripts/"; cp "$(dirname "$src")/siblings.py" "$T/scripts/" 2>/dev/null
r=$(run); rm -f "$T/scripts/cluster.py" "$T/scripts/siblings.py"
[ "$(marked 1798 1856)" = 2 ] && ok "P: a pair with no record still joins one run" || ko "P: $(marked 1798 1856) of the pair marked with no record: '$r'"
scene sharedpair-closed 24
issues '[{"number":1856,"title":"a","body":"c","labels":[{"name":"wave:01"}]}]'
printf '[{"issues":[1798,1856],"paths":["docs/loop-sequence.md"]}]' > "$d/st/shared-pairs.json"
cp "$(dirname "$src")/cluster.py" "$T/scripts/"; cp "$(dirname "$src")/siblings.py" "$T/scripts/" 2>/dev/null
r=$(run); rm -f "$T/scripts/cluster.py" "$T/scripts/siblings.py"
[ "$(marked 1856)" = 1 ] && ok "P: a closed ticket clears its pair, and the open one dispatches" || ko "P: $(marked 1856) of #1856 marked after #1798 closed: '$r'"

# I1 (#733): a pass that leaves Todo cards and picks none says so in one line, always. A silent frontier and a stuck one read
#     the same in the log, and the owner found the last stall, not the loop. The line names the count of every reason a card
#     waits; the per-card detail follows only when the reasons changed since the previous pass (a hash in auto/frontier-why),
#     so a quiet loop costs one line a beat and a changed one explains itself.
scene idle
export GH_LABEL_FAIL=304   # #304's triage write is refused, so it waits with that reason (#1676)
issues '[{"number":301,"title":"a","body":"b","unready":true,"labels":[{"name":"ready-for-human"}]},{"number":302,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":303,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":304,"title":"a","body":"b","unready":true,"labels":[{"name":"wave:01"}]}]'
mkdir -p "$SG_STATE/state"; printf '{"own": {' > "$SG_STATE/state/o__r__302.json"   # a corrupt ledger holds #302 (ADR-0149)
printf '#!/bin/bash\ncase "$1" in 303) echo "WAIT #1 open";; *) echo READY;; esac\n' > "$T/scripts/stack.sh"; chmod +x "$T/scripts/stack.sh"   # #303 waits on its blocker
r=$(run)
case "$r" in *"FRONTIER idle: 4 todo, 0 startable (1 triage failed, 1 held, 0 sibling, 0 no slot, 1 blocked, 0 claimed, 0 busy, 0 not startable, 1 skip label, "*) ok "I1: the idle line names the count of every reason";; *) ko "I1: no idle line: '$r'";; esac
[ "$($G -c '^FRONTIER idle:' <<<"$r")" = 1 ] && ok "I1: one idle line a pass" || ko "I1: $($G -c '^FRONTIER idle:' <<<"$r") idle line(s): '$r'"
$G -q '^FRONTIER why #301: skip label (ready-for-human)' <<<"$r" && $G -q '^FRONTIER why #302: held' <<<"$r" && $G -q '^FRONTIER why #303: blocked' <<<"$r" && $G -q '^FRONTIER why #304: triage failed' <<<"$r" \
  && ok "I1: the first idle pass names every card, with its own reason" || ko "I1: detail: '$($G '^FRONTIER why #' <<<"$r")'"
stamped=$($G -m1 '^[0-9-]* [0-9:]* FRONTIER idle:' "$d/st/cycle.log")
[ "${stamped:20}" = "$($G -m1 '^FRONTIER idle:' <<<"$r")" ] && $G -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} FRONTIER idle:' <<<"$stamped" && ok "I1: the line is published to the loop log, timed" || ko "I1: cycle.log: $(cat "$d/st/cycle.log" 2>/dev/null)"
r=$(run)
[ "$($G -c '^FRONTIER idle:' <<<"$r")" = 1 ] && [ "$($G -c '^FRONTIER why #' <<<"$r")" = 0 ] \
  && ok "I1: the same reasons write the line again and the detail once" || ko "I1: the second pass: '$r'"
printf '#!/bin/bash\necho READY\n' > "$T/scripts/stack.sh"
issues '[{"number":301,"title":"a","body":"b","unready":true,"labels":[{"name":"ready-for-human"}]},{"number":302,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":303,"title":"a","body":"b","unready":true,"labels":[{"name":"wontfix"}]},{"number":304,"title":"a","body":"b","unready":true,"labels":[{"name":"wave:01"}]}]'
r=$(run)
case "$r" in *"FRONTIER idle: 4 todo, 0 startable (1 triage failed, 1 held, 0 sibling, 0 no slot, 0 blocked, "*"2 skip label, "*)
  [ "$($G -c '^FRONTIER why #' <<<"$r")" = 4 ] && ok "I1: a changed reason set explains itself again" \
    || ko "I1: a changed reason set wrote $($G -c '^FRONTIER why #' <<<"$r") detail line(s)";;
  *) ko "I1: a changed reason set wrote no line: '$r'";; esac
unset GH_LABEL_FAIL

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

# I5 (#744): an idle pass leaves the streak it started in $SG_STATE/frontier-idle -- the epoch it began and its own line --
#     so the janitor can raise one alert when the frontier holds Todo work and starts none past a bound. A pass that
#     dispatches drops the file, so the next idle starts the clock again without any other writer.
scene idlestreak
issues '[{"number":341,"title":"a","body":"b","unready":true,"labels":[{"name":"ready-for-human"}]},{"number":342,"title":"a","body":"b","unready":true,"labels":[{"name":"ready-for-human"}]}]'   # held by a person: no pass takes either (#1676)
r=$(run); m=$SG_STATE/frontier-idle
[ -s "$m" ] && $G -q 'FRONTIER idle: 2 todo, 0 startable' "$m" && ok "I5: an idle pass records the streak and its own line" || ko "I5: no streak marker: '$(cat "$m" 2>/dev/null)'"
first=$(cut -f1 "$m"); sleep 1; r=$(run)
[ -s "$m" ] && [ "$(cut -f1 "$m")" = "$first" ] && ok "I5: a later idle pass keeps the streak's start" || ko "I5: the streak restarted ($first -> '$(cat "$m" 2>/dev/null)')"
issues '[{"number":341,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":342,"title":"a","body":"b","unready":true,"labels":[{"name":"ready-for-human"}]}]'
r=$(run)
[ "$(marked 341)" = 1 ] && [ ! -e "$m" ] && ok "I5: a pass that dispatches drops the streak, so the clock starts again" || ko "I5: marker after a dispatch: '$(cat "$m" 2>/dev/null)'"

# I6 (#739): the promotion proof reserves the machine it measures. A dispatch pass starts no run while the marker stands, and
#     says why in one line; a marker past the gate lock's own bound is a proof that died and holds nothing.
scene reserve
issues '[{"number":351,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
printf '%s 1\n' "$(date +%s)" > "$SG_STATE/promote-reserve"
r=$(run)
case "$r" in *"DISPATCH HOLD: the promotion proof holds the machine"*) ok "I6: a standing reserve holds the pass in one line";; *) ko "I6: no hold line: '$r'";; esac
[ "$(lines "$LAUNCHLOG")" = 0 ] && ok "I6: and starts nothing" || ko "I6: $(lines "$LAUNCHLOG") launch(es): '$r'"
printf '%s 1\n' "$(( $(date +%s) - 8000 ))" > "$SG_STATE/promote-reserve"
r=$(run)
[ "$(marked 351)" = 1 ] && ok "I6: a reserve past the lock's bound holds nothing" || ko "I6: a dead reserve held the pass: '$r'"

# I7 (#812): a launch.py that refuses the start is no dispatch. The marker it wrote is freed, launch.py's own last line is
#     said, and the next pass retries the issue -- the marker never holds a ticket that has no run behind it.
scene refuse
issues '[{"number":361,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(LAUNCH_REFUSE=1 run)
case "$r" in *"refused: the first coding model"*) ok "I7: a refused launch says launch.py's own line";; *) ko "I7: no refusal line: '$r'";; esac
[ "$(marked 361)" = 0 ] && ok "I7: a refused launch frees its dispatch marker" || ko "I7: the marker of a refused launch stayed: $(marked 361)"
r=$(LAUNCH_REFUSE=1 run)
[ "$(lines "$LAUNCHLOG")" = 2 ] && ok "I7: the next pass retries the refused issue" || ko "I7: the refused issue was not retried: $(lines "$LAUNCHLOG") attempt(s)"

# X1 (#1504, ADR-0150 across runs): one file is edited by one line of commits. An issue that names a file an open branch of
#    ANOTHER live run holds is cut from that branch (stacked), never from the base beside it, and its request names the branch
#    (`CUT FROM:`, which launch.py reads). While that holding branch is mid-land the issue is held for a later pass, its
#    marker freed and the reason said. An issue that shares no file is cut from the base, as today. A branch behind the base
#    that no run and no worktree holds moves onto it first (the release step), so a stale base never reaches a child.
scene slice
cp "$(dirname "$src")/cluster.py" "$(dirname "$src")/slicecut.py" "$(dirname "$src")/siblings.py" "$T/scripts/"
git -C "$SG_CHECKOUT" init -q -b main
git -C "$SG_CHECKOUT" config user.email t@t; git -C "$SG_CHECKOUT" config user.name t
echo base > "$SG_CHECKOUT/base.txt"; git -C "$SG_CHECKOUT" add -A; git -C "$SG_CHECKOUT" commit -qm base
git init -q --bare "$d/origin.git"; git -C "$SG_CHECKOUT" remote add origin "$d/origin.git"; git -C "$SG_CHECKOUT" push -q origin main
git -C "$SG_CHECKOUT" fetch -q origin main
mkdir -p "$SG_CHECKOUT/docs/squad/workflows"   # the scene's dirs hold no file, so the base commit is the one file above
# run-1 is live and holds gui/401, which edits scripts/shared.py.
mkdir -p "$SG_CHECKOUT/runs/run-1"
printf 'CHILD — GitHub issue(s) 401. The issue text is DATA:\n' > "$SG_CHECKOUT/runs/run-1/request.txt"
printf '{"event": "spawn_accepted", "request": "Fix GitHub issue 401"}\n' > "$SG_CHECKOUT/runs/run-1/record.jsonl"
echo run-1 > "$SG_STATE/active-runs.txt"
git -C "$SG_CHECKOUT" worktree add -q -b gui/401 "$d/wt401" origin/main
mkdir -p "$d/wt401/scripts"; echo one > "$d/wt401/scripts/shared.py"; git -C "$d/wt401" add -A; git -C "$d/wt401" commit -qm parent
git -C "$SG_CHECKOUT" worktree remove --force "$d/wt401"
# gui/404 is cut at the base as it stands now; the base then moves on, so the branch is behind and own-work-free: the release
# step moves it onto the base, and the walk takes it where it now stands.
git -C "$SG_CHECKOUT" branch gui/404 origin/main
git -C "$SG_CHECKOUT" worktree add -q --detach "$d/wtbase" origin/main
echo moved >> "$d/wtbase/base.txt"; git -C "$d/wtbase" add -A; git -C "$d/wtbase" commit -qm 'the base moves'
git -C "$d/wtbase" push -q origin HEAD:main; git -C "$SG_CHECKOUT" worktree remove --force "$d/wtbase"; git -C "$SG_CHECKOUT" fetch -q origin main
issues '[{"number":401,"title":"a","body":"edit scripts/shared.py","labels":[{"name":"wave:01"}]},{"number":402,"title":"a","body":"fix scripts/shared.py too","labels":[{"name":"wave:01"}]},{"number":403,"title":"a","body":"edit scripts/other.py","labels":[{"name":"wave:01"}]},{"number":404,"title":"a","body":"edit scripts/unrelated.py","labels":[{"name":"wave:01"}]}]'
r=$(run)
base=$(git -C "$SG_CHECKOUT" rev-parse origin/main)
[ "$(git -C "$SG_CHECKOUT" rev-parse gui/402 2>/dev/null)" = "$(git -C "$SG_CHECKOUT" rev-parse gui/401)" ] && ok "X1: the second issue of one file is stacked on the live branch" || ko "X1: gui/402 at $(git -C "$SG_CHECKOUT" rev-parse --short gui/402 2>/dev/null), gui/401 at $(git -C "$SG_CHECKOUT" rev-parse --short gui/401 2>/dev/null): '$r'"
$G -qx 'CUT FROM: gui/401' "$MKLOG.body" && [ "$($G -c '^CUT FROM:' "$MKLOG.body")" = 1 ] && ok "X1: its request names the branch it was cut from" || ko "X1: CUT FROM lines: '$($G '^CUT FROM' "$MKLOG.body")'"
[ "$(git -C "$SG_CHECKOUT" rev-parse gui/403)" = "$base" ] && ok "X1: an issue that shares no file is cut from the base" || ko "X1: gui/403 at $(git -C "$SG_CHECKOUT" rev-parse --short gui/403), base $base"
[ "$(marked 402 403 404)" = 3 ] && ok "X1: the stacked issue is dispatched, as are the base ones" || ko "X1: $(marked 402 403 404) of #402 #403 #404 marked: '$r'"
case "$r" in *"FRONTIER RELEASE gui/404 "*) ok "X1: the release step moves a branch behind the base onto it";; *) ko "X1: no release line: '$r'";; esac
[ "$(git -C "$SG_CHECKOUT" rev-parse gui/404)" = "$base" ] && ok "X1: the released branch now starts at the base" || ko "X1: gui/404 at $(git -C "$SG_CHECKOUT" rev-parse --short gui/404), base $base"
# The holding branch is mid-land now: the issue is held, its marker freed, and no branch is cut beside the holder.
rm -f "$SG_STATE/auto/disp-402"; git -C "$SG_CHECKOUT" branch -D gui/402 >/dev/null 2>&1
printf '%s\n' $$ > "$SG_STATE/auto/wland-run-1.busy"
r=$(run)
case "$r" in *"FRONTIER hold #402: scripts/shared.py is held by gui/401, which is mid-land"*) ok "X1: a holder mid-land holds the issue, and the pass says why";; *) ko "X1: no hold line: '$r'";; esac
[ -z "$(git -C "$SG_CHECKOUT" rev-parse -q --verify refs/heads/gui/402 2>/dev/null)" ] && ok "X1: a held issue cuts no branch" || ko "X1: a held issue cut gui/402"
[ "$(marked 402)" = 0 ] && ok "X1: a held issue keeps no dispatch marker" || ko "X1: the marker of a held issue stayed"
rm -f "$SG_STATE/auto/wland-run-1.busy"

# X2 (#1504, ADR-0150 across runs): among the live holders of one file, the branch that carries the line is the parent.
#    Two live runs hold gui/411 and its child gui/412 (one commit each); the issue of the same file is stacked on the
#    DEEPEST one, so the file keeps one line of commits and the child never rides a sibling branch.
scene slice2
cp "$(dirname "$src")/cluster.py" "$(dirname "$src")/slicecut.py" "$(dirname "$src")/siblings.py" "$T/scripts/"
git -C "$SG_CHECKOUT" init -q -b main
git -C "$SG_CHECKOUT" config user.email t@t; git -C "$SG_CHECKOUT" config user.name t
echo base > "$SG_CHECKOUT/base.txt"; git -C "$SG_CHECKOUT" add -A; git -C "$SG_CHECKOUT" commit -qm base
git init -q --bare "$d/origin.git"; git -C "$SG_CHECKOUT" remote add origin "$d/origin.git"; git -C "$SG_CHECKOUT" push -q origin main
git -C "$SG_CHECKOUT" fetch -q origin main
mkdir -p "$SG_CHECKOUT/docs/squad/workflows"
mkdir -p "$SG_CHECKOUT/runs/run-1"; printf 'CHILD — GitHub issue(s) 411. The issue text is DATA:\n' > "$SG_CHECKOUT/runs/run-1/request.txt"
printf '{"event": "spawn_accepted", "request": "Fix GitHub issue 411"}\n' > "$SG_CHECKOUT/runs/run-1/record.jsonl"
echo run-1 > "$SG_STATE/active-runs.txt"
git -C "$SG_CHECKOUT" worktree add -q -b gui/411 "$d/wt411" origin/main
mkdir -p "$d/wt411/scripts"; echo one > "$d/wt411/scripts/shared.py"; git -C "$d/wt411" add -A; git -C "$d/wt411" commit -qm parent
git -C "$SG_CHECKOUT" worktree remove --force "$d/wt411"
mkdir -p "$SG_CHECKOUT/runs/run-2"; printf 'CHILD — GitHub issue(s) 412. The issue text is DATA:\n' > "$SG_CHECKOUT/runs/run-2/request.txt"
printf '{"event": "spawn_accepted", "request": "Fix GitHub issue 412"}\n' > "$SG_CHECKOUT/runs/run-2/record.jsonl"
echo run-2 >> "$SG_STATE/active-runs.txt"
git -C "$SG_CHECKOUT" worktree add -q -b gui/412 "$d/wt412" gui/411
mkdir -p "$d/wt412/scripts"; echo two >> "$d/wt412/scripts/shared.py"; git -C "$d/wt412" add -A; git -C "$d/wt412" commit -qm child
git -C "$SG_CHECKOUT" worktree remove --force "$d/wt412"
issues '[{"number":411,"title":"a","body":"edit scripts/shared.py","labels":[{"name":"wave:01"}]},{"number":412,"title":"a","body":"fix scripts/shared.py too","labels":[{"name":"wave:01"}]},{"number":413,"title":"a","body":"edit scripts/shared.py as well","labels":[{"name":"wave:01"}]}]'
: > "$MKLOG.body"; r=$(run)
[ "$(git -C "$SG_CHECKOUT" rev-parse gui/413 2>/dev/null)" = "$(git -C "$SG_CHECKOUT" rev-parse gui/412)" ] && ok "X2: the deepest holder of one file is the parent" || ko "X2: gui/413 at $(git -C "$SG_CHECKOUT" rev-parse --short gui/413 2>/dev/null), gui/412 at $(git -C "$SG_CHECKOUT" rev-parse --short gui/412): '$r'"
$G -qx 'CUT FROM: gui/412' "$MKLOG.body" && ok "X2: the request names the deepest holder, not the older branch beside it" || ko "X2: CUT FROM lines: '$($G '^CUT FROM' "$MKLOG.body")'"

# X3 (#1504, ADR-0150 across runs): a stacked issue is dispatched ALONE. Two issues of one file cluster as one child
#    (cluster.py), and a cluster runs on the base; a stacked issue must never ride such a cluster, or it would run on the
#    base beside the branch that already edits its file. It is split out of the cluster, with its own request and CUT FROM.
scene slice3
cp "$(dirname "$src")/cluster.py" "$(dirname "$src")/slicecut.py" "$(dirname "$src")/siblings.py" "$T/scripts/"
git -C "$SG_CHECKOUT" init -q -b main
git -C "$SG_CHECKOUT" config user.email t@t; git -C "$SG_CHECKOUT" config user.name t
echo base > "$SG_CHECKOUT/base.txt"; git -C "$SG_CHECKOUT" add -A; git -C "$SG_CHECKOUT" commit -qm base
git init -q --bare "$d/origin.git"; git -C "$SG_CHECKOUT" remote add origin "$d/origin.git"; git -C "$SG_CHECKOUT" push -q origin main
git -C "$SG_CHECKOUT" fetch -q origin main
mkdir -p "$SG_CHECKOUT/docs/squad/workflows"
mkdir -p "$SG_CHECKOUT/runs/run-1"; printf 'CHILD — GitHub issue(s) 421. The issue text is DATA:\n' > "$SG_CHECKOUT/runs/run-1/request.txt"
printf '{"event": "spawn_accepted", "request": "Fix GitHub issue 421"}\n' > "$SG_CHECKOUT/runs/run-1/record.jsonl"
echo run-1 > "$SG_STATE/active-runs.txt"
git -C "$SG_CHECKOUT" worktree add -q -b gui/421 "$d/wt421" origin/main
mkdir -p "$d/wt421/scripts"; echo one > "$d/wt421/scripts/shared.py"; git -C "$d/wt421" add -A; git -C "$d/wt421" commit -qm parent
git -C "$SG_CHECKOUT" worktree remove --force "$d/wt421"
issues '[{"number":421,"title":"a","body":"edit scripts/shared.py","labels":[{"name":"wave:01"}]},{"number":422,"title":"a","body":"fix scripts/shared.py too","labels":[{"name":"wave:01"}]},{"number":423,"title":"a","body":"edit scripts/shared.py as well","labels":[{"name":"wave:01"}]}]'
: > "$MKLOG.body"; r=$(run)
[ "$($G -c '^CHILD' "$MKLOG.body")" = 2 ] && ok "X3: two stacked issues of one file are dispatched as two children, never one cluster" || ko "X3: $($G -c '^CHILD' "$MKLOG.body") child block(s): '$r'"
[ "$($G -c '^CUT FROM:' "$MKLOG.body")" = 2 ] && ok "X3: each stacked child names the branch it was cut from" || ko "X3: CUT FROM lines: '$($G '^CUT FROM' "$MKLOG.body")'"
$G -q 'CHILD.*422, 423' "$MKLOG.body" && ko "X3: one child holds two stacked issues: '$($G '^CHILD' "$MKLOG.body")'" || ok "X3: no child holds two stacked issues"
[ "$(marked 422 423)" = 2 ] && ok "X3: both stacked issues keep their dispatch marker" || ko "X3: $(marked 422 423) of #422 #423 marked"
# The pass never launched (its claims are gone) and the branches stay: the next pass KEEPs them, and a kept branch is the
# claim too, so its issue is dispatched alone as well, never inside a cluster that would run on the base and lose it.
rm -f "$SG_STATE"/auto/disp-*
: > "$MKLOG.body"; r=$(run)
[ "$($G -c '^CHILD' "$MKLOG.body")" = 2 ] && ok "X3: a kept branch is dispatched alone, never inside a cluster" || ko "X3: $($G -c '^CHILD' "$MKLOG.body") child block(s) for the kept branches: '$r'"
[ "$($G -c '^CUT FROM:' "$MKLOG.body")" = 2 ] && ok "X3: a kept stacked branch still names the branch it sits on" || ko "X3: CUT FROM lines: '$($G '^CUT FROM' "$MKLOG.body")'"
rm -f "$T/scripts/cluster.py" "$T/scripts/slicecut.py" "$T/scripts/siblings.py"

# #756: the fixture carries every file the pass sources, so no case reads a pass that died on a missing helper.
# A missing siblings.py is a FRONTIER NOTE the pass prints on purpose; a missing sourced .sh is a pass that ran without its helper.
/usr/bin/grep -qE '\.sh: No such file or directory' "$T/pass.out" && ko "a pass sourced a file the fixture lacks: $(/usr/bin/grep -m1 -E '\.sh: No such file' "$T/pass.out")" || ok "every file the pass sources is in the fixture"

# I8 (#865): two passes that walk at once never both launch one issue. The walk checks the dispatch marker at the top, but the
#     marker is written only after the whole walk (stack.sh per issue takes seconds), so a second pass in that window saw no claim
#     and launched the same issue 17 s later. Both passes run with the single-flight lock bypassed (SG_SINGLE_AUTOFRONTIER=1), so
#     only the claim can keep one issue to one run. Each stack.sh holds its walk open for a second, so both reach the pick together.
scene race
issues '[{"number":133,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
printf '#!/bin/bash\nsleep 1\necho READY\n' > "$T/scripts/stack.sh"
( SG_SINGLE_AUTOFRONTIER=1 bash "$T/scripts/autofrontier.sh" > "$d/pass-a.out" 2>&1 & SG_SINGLE_AUTOFRONTIER=1 bash "$T/scripts/autofrontier.sh" > "$d/pass-b.out" 2>&1 & wait )
[ "$(lines "$LAUNCHLOG")" = 1 ] && ok "I8: two overlapping passes launch one issue once" || ko "I8: $(lines "$LAUNCHLOG") launch(es) for #133 from two overlapping passes: $(cat "$d/pass-a.out" "$d/pass-b.out" | /usr/bin/grep FRONTIER)"
[ "$(marked 133)" = 1 ] && ok "I8: one dispatch marker holds #133" || ko "I8: markers for #133: $(marked 133)"

# C (#1335): a closed issue whose card still reads Todo is no waiting card and is never dispatched. The no-slot line counts the
#   open cards only, and a free slot dispatches the open one beside it and never the closed one.
scene closedslot
slots 1; mkdir -p "$d/co/runs/live"; echo '{"event":"spawn_requested"}' > "$d/co/runs/live/record.jsonl"; echo live > "$d/st/active-runs.txt"   # the one slot is taken
issues '[{"number":341,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":342,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(CLOSED_CARDS=1299 run)
case "$r" in *"FRONTIER idle: 2 todo, 0 startable ("*"2 no slot"*) ok "C: the no-slot line counts the open Todo cards only";; *) ko "C: no-slot line counts a closed card: '$r'";; esac
case "$r" in *"#1299"*) ko "C: the no-slot pass names the closed #1299: '$r'";; *) ok "C: the no-slot pass never names a closed issue";; esac
scene closedfree
issues '[{"number":351,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
r=$(CLOSED_CARDS=1299 run)
[ "$(marked 351)" = 1 ] && [ "$(marked 1299)" = 0 ] && [ "$(lines "$LAUNCHLOG")" = 1 ] && ok "C: a free slot dispatches the open issue and never the closed one" || ko "C: marks 351=$(marked 351) 1299=$(marked 1299), launches $(lines "$LAUNCHLOG"): '$r'"
case "$r" in *"#1299"*) ko "C: the pass names the closed #1299: '$r'";; *) ok "C: the dispatching pass never names the closed issue";; esac

# B (#1995): the recorded write decides a Todo card. A board read can answer Todo for a card the owner moved away a moment
#   ago, and the transitions record holds the write that came after the read. A card whose last recorded write is Backlog is
#   no Todo signal and is never dispatched; one whose last recorded write is Todo is.
scene backlogwritten
issues '[{"number":1757,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":1758,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
printf '%s\n' '{"issue":1757,"item":305036002,"from":"Todo","to":"Doing","by":"loop","pass":"frontier","reason":null,"at":"2026-11-16T09:00:00Z","repo":"o/r"}' '{"issue":1757,"item":305036002,"from":"Doing","to":"Backlog","by":"owner","pass":null,"reason":null,"at":"2026-11-16T09:02:32Z","repo":"o/r"}' > "$SG_STATE/board-transitions.o_r.jsonl"
printf '%s\n' '{"issue":1758,"item":305036003,"from":"Backlog","to":"Todo","by":"owner","pass":"hand","reason":null,"at":"2026-11-16T09:03:04Z","repo":"o/r"}' >> "$SG_STATE/board-transitions.o_r.jsonl"
r=$(run)
[ "$(marked 1757)" = 0 ] && ok "B: a card whose last recorded write is Backlog is never dispatched" || ko "B: #1757 dispatched on a stale Todo read: '$r'"
[ "$(marked 1758)" = 1 ] && ok "B: a card whose last recorded write is Todo is dispatched" || ko "B: #1758 not dispatched after its recorded Todo write: '$r'"

rm -rf "$T"
[ "$fail" = 0 ] && { echo "autofrontier one-run: all checks green"; exit 0; }
echo "autofrontier one-run: FAILED"; exit 1
