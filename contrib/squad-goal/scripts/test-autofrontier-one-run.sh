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
case "$p" in
  */pulls?state=open*)    f=$GH_OPEN;;
  */pulls?state=closed*)  f=$GH_CLOSED;;
  */issues?state=open*)   f=$GH_ISSUES;;
  */issues/*)             f=$GH_ISSUE1; [ -f "$GH_ISSUEDIR/${p##*/}.json" ] && f=$GH_ISSUEDIR/${p##*/}.json;;   # one fixture per issue when a case wrote it
  *) exit 1;;
esac
if [ -n "$q" ]; then jq -r "$q" "$f"; else cat "$f"; fi
GH
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\necho READY\n' > "$T/scripts/stack.sh"; chmod +x "$T/scripts/stack.sh"
printf '#!/bin/bash\nsg_fork_only() { return 1; }\nsg_not_startable() { return 1; }\nsg_fork_preamble() { :; }\nsg_fork_branch() { echo "gui/$1"; }\nsg_fork_branch_pattern() { echo "gui/%%n"; }\n' > "$T/scripts/startability.sh"
# mkreq.sh keeps the body it was handed and prints a request path (or refuses when MK_REFUSE is set); launch.py only counts
printf '#!/bin/bash\n[ -n "$MK_REFUSE" ] && { echo "refusing: stub" >&2; exit 1; }\n[ "$1" = --paths ] && exec bash "$(dirname "$0")/mkreq-real.sh" "$@"\n[ -n "$MK_REAL" ] && { bash "$(dirname "$0")/mkreq-real.sh" "$@" || exit 1; echo x >> "$MKLOG"; cp "$2" "$MKLOG.body"; exit 0; }\necho x >> "$MKLOG"; cp "$2" "$MKLOG.body"; echo "$HOME/req.txt"\n' > "$T/scripts/mkreq.sh"
printf 'open(__import__("os").environ["LAUNCHLOG"], "a").write("launch\\n")\n' > "$T/scripts/launch.py"
chmod +x "$T/scripts/mkreq.sh"
# board.sh keeps the call it was handed: <owner> <project> <column> <issue>... (#33: the card move was a bare `true`, so no case could say which cards moved)
printf '#!/bin/bash\necho "$*" >> "$BOARDLOG"\n' > "$T/board.sh"; chmod +x "$T/board.sh"
cp "$src" "$T/scripts/autofrontier.sh"
scene() { # <name> [max_children_of_the_dispatch_node [run_max_children]]: fresh state, its own env.sh and workflow graph
  d=$T/$1; mkdir -p "$d/st/req" "$d/st/cq" "$d/co/docs/squad/workflows" "$d/co/runs" "$d/home"
  : > "$d/gh.log"; : > "$d/st/active-runs.txt"; : > "$d/mk.log"; : > "$d/launch.log"; : > "$d/board.log"
  cat > "$T/scripts/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$d/st; SG_CHECKOUT=$d/co; SG_BASE=main
SG_FRONTIER_LABEL=frontier; SG_BUG_LABEL=bug; SG_CODING_RULES=rules; BOARD=$T/board.sh
. "$(dirname "$src")/address.sh"   # the writers name a queued comment through the seam, as the real env.sh loads it
E
  if [ -n "$2" ]; then   # the author's graph: a root loop that holds one dispatch node, and a run block
    rb='{"max_parallel":20}'; [ -n "$3" ] && rb="{\"max_parallel\":20,\"max_children\":$3}"
    printf '{"run":%s,"nodes":[{"name":"root","kind":"loop","nodes":[{"name":"route","kind":"dispatch","max_children":%s,"max_parallel":2}]}]}' "$rb" "$2" > "$d/co/docs/squad/workflows/dynamic.graph.json"
  fi
  export HOME=$d/home SG_STATE=$d/st SG_CHECKOUT=$d/co SG_REPO=o/r SG_FRONTIER_LABEL=frontier MKLOG=$d/mk.log LAUNCHLOG=$d/launch.log BOARDLOG=$d/board.log
  export GH_ISSUES=$d/issues.json GH_OPEN=$d/open.json GH_CLOSED=$d/closed.json GH_ISSUE1=$d/issue1.json GH_ISSUEDIR=$d/issue; mkdir -p "$GH_ISSUEDIR"
  printf '[]' > "$GH_OPEN"; printf '[]' > "$GH_CLOSED"
  printf '{"number":0,"title":"t","body":"","created_at":"2026-01-01T00:00:00Z"}' > "$GH_ISSUE1"
}
five() { issues '[{"number":11,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":12,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":13,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":14,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":15,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'; }
issues() { printf '%s' "$1" > "$GH_ISSUES"; }
issuebody() { printf '{"number":%s,"title":"t","body":"%s","created_at":"2026-01-01T00:00:00Z"}' "$1" "$2" > "$GH_ISSUEDIR/$1.json"; }   # <n> <body>: what the pass pastes into the request for that issue
real() { MK_REAL=1 SG_FORK_DIR=$FK SG_FORK_BASE=main "$@"; }   # the real slice guard over the throwaway fork
run() { SG_SINGLE_AUTOFRONTIER=1 bash "$T/scripts/autofrontier.sh" 2>&1; /bin/sleep 0.6; }   # the launch is detached: give it time to count itself
lines() { local c; c=$($G -c . "$1" 2>/dev/null); echo "${c:-0}"; }
marked() { local n c=0; for n in "$@"; do [ -e "$SG_STATE/auto/disp-$n" ] && c=$((c+1)); done; echo $c; }
asked() { sed -nE 's/.*call the spawn tool for the ([0-9]+) child.*/\1/p' "$MKLOG.body" | head -1; }
moved() { local n; for n in "$@"; do $G -qE "^o 1 In Progress( [0-9]+)* $n( |\$)" "$BOARDLOG" || return 1; done; }   # every issue named is in the card move the pass made
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

# 1b) #33: a child with SEVERAL issues is one slice, one request line and one PR, and each of its issues still holds its marker,
#     its own comment and its own card move -- the claim the comment above makes for one-issue children, asserted for the bundle
scene bundle
issues '[{"number":31,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":32,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":34,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'
issuebody 31 "fix scripts/shared_fix.py"; issuebody 32 "also scripts/shared_fix.py"; issuebody 34 "unrelated"
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

rm -rf "$T"
[ "$fail" = 0 ] && { echo "autofrontier one-run: all checks green"; exit 0; }
echo "autofrontier one-run: FAILED"; exit 1
