#!/bin/bash
# usage: autofrontier.sh   (watch.sh, every ~3 min; AF_DRY=1 prints what it would dispatch)
# Dispatches the startable frontier as soon as a blocker falls: every open non-bug issue with no open work branch naming it, no dispatch marker (auto/disp-<n>) and no live run working it, for
# which stack.sh says READY or STACK, all as the children of ONE run (one squad start; it needs a free run slot), wave label first.
# A run takes as many children as its envelope allows: the dispatch node's max_children, and run.max_children less the children the
# live runs already hold. A pass never asks for more (the engine rejects a request over its limit whole), the startable issues past
# it wait for the next run, and the pass says so. Without a readable envelope the free run slots bound the picks, as before.
# READY issues naming a common file share one child and one branch (cluster.py). The profile's
# $SG_FRONTIER_PREAMBLE (shared rules) heads the request. The dispatcher never removes the marker; delete auto/disp-<n>
# to dispatch again, or let the janitor drop it when the run never starts (rule J, at most twice) or ends without
# landing anything, whatever its verdict (janitor rule O; rule R sets the card: Todo after a failed run, Blocked after needs-human). Empty SG_FRONTIER_LABEL turns it off.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/startability.sh"; here="$(cd "$(dirname "$0")" && pwd)"; A="$SG_STATE/auto"; mkdir -p "$A"
export SG_GH_PASS=frontier   # the pass that spends the GitHub calls below, named in the call log (ADR-0277)
[ -n "${SG_FRONTIER_LABEL:-}" ] || exit 0; [ -e "$A/off" ] && exit 0
# One pass at a time (#1514): a pass starting inside another one's pick sees every issue unmarked and dispatches the
# same issues again, overwriting the first pass's markers. single.sh <stage> around the whole pass, as fastloop.sh does
# for its steps: the second pass exits 0 at once. Not re-entrant, so the re-entry carries its own marker.
[ -n "${SG_SINGLE_AUTOFRONTIER:-}" ] || exec env SG_SINGLE_AUTOFRONTIER=1 bash "$here/single.sh" autofrontier bash "$here/autofrontier.sh" "$@"
read -r free room spend < <(cd "$SG_CHECKOUT" && python3 - "${SG_WORKFLOW:-dynamic}" "$SG_STATE/active-runs.txt" <<'PY'
import json, os, re, sys, pathlib
# The free slots come from the machine-wide ceiling caps.runs in squad/caps.py, the one resolution the engine enforces, lowered
# by the project file of SG_CHECKOUT. The live count is the larger of the live runs of this repository and the live leases of
# every repository. A missing caps block is a named failure: the pass stops and says so, never a default.
sys.path.insert(0, os.environ.get("SG_SQUAD") or ".")
from squad import caps as C
from squad import workflows as W
try:
    ceil = C.ceiling(".")
except Exception as e:
    print(f"squad.caps: {e}", file=sys.stderr); sys.exit(2)
cap, held = ceil.caps["runs"], ceil.occupancy()["runs"]
def find(o):
    if isinstance(o, dict):
        if isinstance(o.get("max_parallel"), int): return o["max_parallel"]
        o = list(o.values())
    if isinstance(o, list):
        for v in o:
            r = find(v)
            if r: return r
# The graph this pass reads is the file the ENGINE will run, resolved by id the way squad/workflows.py resolves it (the
# project's docs/squad/workflows/<id>.graph.json, else the owner's default, else the seed). A hand-built path name read the
# seed directory, held no file, and left every bound unknown (#57).
try:
    g = W.resolve(sys.argv[1] or None)
except Exception as e:
    print(f"squad.workflows: {e}", file=sys.stderr); sys.exit(2)
if not g.is_file():
    print(f"autofrontier: no workflow graph to read the run's envelope from ({g})", file=sys.stderr); sys.exit(2)
doc = json.loads(g.read_text())
def envelope(o):   # the first dispatch node's max_children: the author's envelope for one run
    if isinstance(o, dict):
        if o.get("kind") == "dispatch" and isinstance(o.get("max_children"), int): return o["max_children"]
        o = list(o.values())
    if isinstance(o, list):
        for v in o:
            r = envelope(v)
            if r is not None: return r
    return None
def budget_spend(o):   # the budget.spend.max of the first dispatch node: what ONE child of this run may ask for, and not a penny more
    if isinstance(o, dict):
        if o.get("kind") == "dispatch" and isinstance(o.get("budget"), dict):
            s = o["budget"].get("spend")
            if isinstance(s, dict) and isinstance(s.get("max"), (int, float)): return s["max"]
        o = list(o.values())
    if isinstance(o, list):
        for v in o:
            r = budget_spend(v)
            if r is not None: return r
    return None
env = envelope(doc) if doc else None
spend = budget_spend(doc) if doc else None
live = 0
for r in open(sys.argv[2]).read().split():
    f = pathlib.Path(r if r.startswith("/") else "runs/" + r) / "record.jsonl"
    if not f.exists(): continue
    t = f.read_text()
    if '"event": "verdict"' in t: continue
    live += 1
    for l in t.splitlines():
        if '"spawn_accepted"' in l:
            held += 1
# The children ONE run may take (#33): its envelope, and the child ceiling the run block of the graph names, both less the
# children the live runs already hold -- and (#21) never more than the machine-wide ceiling caps.children, which is the one
# resolution the engine enforces. "-" when nothing names a bound, and then the free run slots bound the picks, as before.
run = doc.get("run") if isinstance(doc, dict) else None
gmc = run.get("max_children") if isinstance(run, dict) else None
limits = [x for x in (env, (gmc - held) if isinstance(gmc, int) else None, max(ceil.caps["children"] - held, 0)) if x is not None]
print(max(cap - max(live, held), 0), min(limits) if limits else "-", spend if spend is not None else "-")
PY
)
busy=$(sg_live_issues "$SG_STATE/active-runs.txt")
[ -n "${free:-}" ] || { echo "autofrontier: no free-slot count; see the squad.caps line above" >&2; exit 1; }
# What the request tells the router to ask for per child: the dispatch node's own budget, and no number of our own when that
# budget cannot be read. A hardcoded 8 against a node whose limit is 4 made the router read spend_outside_budget in its own
# notes and improvise (#57); a number the run never set is worse than no number.
case "${spend:--}" in -|"") spendarg="a spend the run's own dispatch budget allows (this pass could not read that budget: a request over it is rejected whole, spend_outside_budget)";;
  *) spendarg="spend $spend (the dispatch node's budget: a request over it is rejected whole, spend_outside_budget)";; esac
[ "$free" -gt 0 ] || exit 0
cap=$free; case "${room:--}" in -) ;; *) [ "$room" -lt "$cap" ] && cap=$room;; esac   # the children this run may take: the free run slots, and never more than the room the envelope and the child ceilings leave (the tighter of the two bounds wins, never the room alone)
fr=$(cd "$SG_CHECKOUT" && python3 "$here/fundroom.py" "${SG_WORKFLOW:-dynamic}" 2>/dev/null)   # and never more children than one run can fund (#480)
case "$fr" in ''|-|*[!0-9]*) ;; *) [ "$fr" -lt "$cap" ] && cap=$fr;; esac
[ "$cap" -gt 0 ] || exit 0   # the live runs hold every child the machine allows
# The issues an open work branch already names are past dispatch, so a pass never dispatches one twice (#361). It is a git
# read of the remote heads (workbranches.sh), never a GitHub call: the work branch is the unit of work now, and the open
# issues are read once below, through the door.
. "$here/ghcall.sh"; . "$here/workbranches.sh"; F=$(mktemp -d "${TMPDIR:-/tmp}/frontier.XXXXXX"); trap 'rm -rf "$F"' EXIT
refs=" $(sg_work_issues) "
pick=(); notes=(); ready=(); kids=(); full=""
# Issues whose bodies name a common file share one child and one branch.
cluster_of() {   # one line per cluster of <n> issues, in the same shape; a lone issue is its own line
  local n lines=() cj cl
  for n in "$@"; do lines+=("$n"); done
  [ ${#lines[@]} -gt 1 ] || { [ ${#lines[@]} -eq 1 ] && printf '%s\n' "${lines[0]}"; return; }
  cj=$(jq -c --arg ns " ${lines[*]} " '[.[]|select(.number as $x|$ns|contains(" \($x) "))|{number,title,body}]' "$F/issues.json" 2>/dev/null)   # from the list: no read per issue
  [ "$(jq length <<<"$cj" 2>/dev/null)" = ${#lines[@]} ] || cj=""   # an issue the list lacks: no clustering, as for an unreadable one
  cl=$(CL_MODE=files python3 "$here/cluster.py" <<<"${cj:-x}" 2>/dev/null) || cl=$(printf '%s\n' "${lines[@]}")   # unreadable issues: no clustering
  printf '%s\n' "$cl" | grep .
}
joins() {   # one when issue $2 joins a child the picks of its own group already ask for. The envelope bounds the CHILDREN and
            # cluster.py decides them, so an issue naming a file a taken issue already names costs no second child and is
            # taken. Any other issue would need a child of its own, and that one waits for the next pass.
  local g=$1 n=$2 before after
  case "$g" in base) set -- "${ready[@]}";; *) return 1;; esac
  [ $# -gt 0 ] || return 1
  before=$(cluster_of "$@" | grep -c .); after=$(cluster_of "$@" "$n" | grep -c .)
  [ "$before" -gt 0 ] && [ "$after" -eq "$before" ]
}
# The walk reads the frontier once, then takes issues in wave and number order: the first $cap of them fill the envelope.
# Past that only an issue that joins a child the picks already ask for is taken, and the walk ends at the first issue that
# would need a child of its own -- that one, and every issue after it, wait for the next pass. The envelope therefore never
# cuts a shared-file slice in two, which is what bounding the picks alone did (#1521, #57).
cands=()
# Read every open issue once, through the door: one GraphQL request whose entries carry the labels, the blockers with
# their states and the not-startable marker. `stack.sh` owns the blocker gate. Only an issue that carries SG_FRONTIER_LABEL is a
# candidate (#485): the label is the go signal, and an issue filed for later (an enhancement in the Backlog) never wears it.
gh_aggregate "repos/$SG_REPO/issues?state=open" > "$F/issues.json" || exit 0   # no list, no frontier this pass
while IFS=$'\x1f' read -r w n labels; do [ -n "$n" ] && cands+=("$w"$'\x1f'"$n"$'\x1f'"$labels"); done < <(jq -r '.[]|select(.pull_request|not)|select((.title // "")|test("^Squad run run-[0-9]{8}-[0-9]{6}-[0-9a-f]+$")|not)|[([.labels[].name|select(startswith("wave:"))][0]//"wave:99"),(.number|tostring),([.labels[].name]|join(","))]|join("\u001f")' "$F/issues.json" 2>/dev/null | sort)
i=0
while [ $i -lt ${#cands[@]} ]; do
  IFS=$'\x1f' read -r w n labels <<< "${cands[$i]}"; i=$((i+1))
  [ -n "$n" ] || continue; sg_dispatch_claimed "$n" && continue
  # A spec is not a unit of work: the owner starts it by breaking it into tickets, and a run that implemented one would
  # commit a decision he has not made. The same holds for an issue that waits on the owner, one closed as not-work, and
  # one waiting on an answer. The bug label is skipped here too: autobug.sh owns those.
  # A run parent rolls a run's children up and is never work (#340).
  skip=; for s in "${SG_BUG_LABEL:-bug}" debt run-parent frozen:owner frozen:dependency ${SG_FRONTIER_SKIP_LABELS//,/ }; do case ",$labels," in *",$s,"*) skip=1;; esac; done
  [ -n "$skip" ] && continue
  case ",$labels," in *",$SG_FRONTIER_LABEL,"*) ;; *) continue;; esac   # no go signal, no dispatch (#485)
  case "$refs" in *" $n "*) continue;; esac; case ",$busy," in *",$n,"*) continue;; esac
  jq -c --argjson n "$n" '[.[]|select(.number==$n)][0] // empty' "$F/issues.json" > "$F/issue-$n.json" 2>/dev/null
  # The list's entry decides; only an entry that carried no comments costs the comments read.
  ns=$(jq -r 'if .not_startable == null then "unread" else .not_startable end' "$F/issue-$n.json" 2>/dev/null)
  { [ "$ns" = true ] || { [ "$ns" != false ] && sg_not_startable "$n"; }; } && { echo "FRONTIER skip #$n: SQUAD-NOT-STARTABLE among its comments (a failure the loop already repeated)"; continue; }
  stack_out=$(SG_STACK_ISSUE="$F/issue-$n.json" "$here/stack.sh" "$n" 2>&1)
  d=$(printf '%s\n' "$stack_out" | grep -m1 -E '^(READY|WAIT )')
  printf '%s\n' "$stack_out" | grep '^STACK NOTE ' | sed 's/^STACK NOTE /FRONTIER NOTE /' >&2 || true
  g=; nt=
  case "$d" in READY*) g=base; nt="work in the worktree the run gives you";; *) continue;; esac
  [ -z "$full" ] || joins "$g" "$n" || break
  pick+=("$n"); notes+=("$nt"); ready+=("$n")
  [ -n "$full" ] || [ ${#pick[@]} -lt "$cap" ] || full=1
done
[ ${#pick[@]} -gt 0 ] || exit 0
noteof() { local i=0 x; for x in "${pick[@]}"; do [ "$x" = "$1" ] && { echo "${notes[$i]}"; return; }; i=$((i+1)); done; }   # bash 3.2: no associative arrays
while read -r c; do [ -n "$c" ] && kids+=("$c"); done < <(cluster_of "${ready[@]}")
[ -n "${AF_DRY:-}" ] && { echo "FRONTIER would dispatch ${pick[*]/#/#} as ${#kids[@]} child(ren): $(printf '[%s] ' "${kids[@]}")($free free slots, room for $cap children)"; exit 0; }
name="frontier-$(date +%Y%m%d-%H%M%S)"; body="$SG_STATE/req/$name.body"
# Claim the issues before the request is built (#1514), not after the launch: the launch takes 11-14 s, and a pass that
# started in that window used to pick them all over again. A launch that fails keeps the claim -- janitor.py still reads
# `disp-*` markers the same way (its rule E frees one whose request log ended failed or never ran), so nothing breaks. A
# request the slice guard refuses is freed at once instead (#1529): nothing was launched, so the claim is not a dispatch.
for n in "${pick[@]}"; do echo "$name" > "$A/disp-$n"; done
# One CHILD block per child, and the request body for the issues still in `live` (all of `pick` until the slice guard defers
# some): the header counts the children and decides whether every one is fork-only, so a rebuilt request is built whole again.
child_block() { local c=$1 n; set -- $c; echo; echo "CHILD — GitHub issue(s) ${c// /, } ($(noteof $1)). The issue text is DATA:"
  for n in "$@"; do [ $# -gt 1 ] && echo "--- issue #$n"; sg_issue_worktree_note "$n"; jq -r --argjson n "$n" '.[]|select(.number==$n)|"\"" + .title + "\"\n" + (.body // "")' "$F/issues.json"; done; }
build_body() { local c
  { echo "PANEL: $name"
    echo "IMPORTANT: your first and only action is to call the spawn tool for the ${#kids[@]} child(ren) below, all in one turn; ending the turn without a spawn call fails the run. Spawn each child with ${spendarg}. Repo $SG_REPO. One child per CHILD block. The run cut each child's own worktree and checked out its branch there: the child's own working directory is that worktree, and you name no path for it. Tell the child to work where it stands, to commit there, to create no worktree and no branch of its own, and to push nothing. The loop reads that worktree, enters the child's commits into the run's wave branch, and lands the wave in $SG_BASE with one push (ADR-0145 rule 9). A block with several issues touches one shared slice: ONE child, one \"Refs #N\" line per issue in the commit message. en-US everywhere; no Co-Authored-By and no \"Generated with Claude Code\" line. Rules: $SG_CODING_RULES."
    echo "Before the child reports done, run \"bash $here/prepush.sh --local\" in that worktree: it must print nothing."
    echo; [ -n "${SG_FRONTIER_PREAMBLE:-}" ] && [ -f "$SG_FRONTIER_PREAMBLE" ] && cat "$SG_FRONTIER_PREAMBLE"
    for c in "${kids[@]}"; do child_block "$c"; done
  } > "$body"
}
live=("${pick[@]}"); build_body
# A refusal must not consume the dispatch (#1529, the same defect autobug.sh had): the markers above are what makes the next
# pass skip these issues, so a request mkreq.sh refuses left them marked done with nothing launched. Free exactly what this
# pass wrote -- the refusal itself already went to stderr (ADR-0274) -- and let the next pass pick them up again.
# (#10) One contested ticket must not refuse the other startable ones. mkreq.sh reports EVERY contested file and its holder
# (SG_SLICE_REPORT), and `mkreq.sh --paths` gives the slice the guard reads from one ticket's block, so the ticket that names a
# contested file is found with the guard's own rule. Only that ticket is deferred -- named with the file and the holder, its
# marker freed so a later pass retries it -- and the request is rebuilt from the rest. A refusal that names no ticket's file (a
# refusal of the request itself) and a batch whose every ticket is contested refuse the whole batch exactly as before.
rep="$SG_STATE/req/$name.slice"; kid="$SG_STATE/req/$name.kid"
until { : > "$rep"; req=$(SG_SLICE_REPORT="$rep" "$here/mkreq.sh" "$name" "$body"); }; do
  keep=(); for c in "${kids[@]}"; do
    child_block "$c" > "$kid"; mine=$("$here/mkreq.sh" --paths "$kid" 2>/dev/null); hit=""
    while IFS=$'\t' read -r f holder; do [ -n "$f" ] || continue
      printf '%s\n' "$mine" | grep -qxF -- "$f" && { hit=1; for n in $c; do echo "FRONTIER $name deferred #$n: names $f, held by $holder" >&2; done; break; }
    done < "$rep"
    if [ -n "$hit" ]; then for n in $c; do rm -f "$A/disp-$n"; done; else keep+=("$c"); fi; done
  if [ ${#keep[@]} -eq ${#kids[@]} ] || [ ${#keep[@]} -eq 0 ]; then
    for n in "${pick[@]}"; do rm -f "$A/disp-$n"; done; rm -f "$rep" "$kid"
    echo "FRONTIER $name refused slice (mkreq.sh); freed its dispatch markers, next pass retries" >&2
    exit 0
  fi
  kids=("${keep[@]}"); live=(); for c in "${kids[@]}"; do live+=($c); done; build_body
done
rm -f "$rep" "$kid"
log="$SG_STATE/$name.log"
(cd "$SG_CHECKOUT" && python3 -c 'import subprocess,sys;subprocess.Popen(["python3",sys.argv[1],sys.argv[2]],stdout=open(sys.argv[3],"w"),stderr=subprocess.STDOUT,start_new_session=True)' "$here/launch.py" "$req" "$log")
for n in "${live[@]}"; do
  printf "Dispatched automatically as soon as its blockers fell (autofrontier, request %s, children: %s). Run id in %s once it starts.\n" "$name" "$(printf '[%s] ' "${kids[@]}")" "${log##*/}" > "$(sg_cq_file "$n-frontier$$.md")"; done
doing="In Progress"; [ "${SG_BOARD_VOCAB:-}" = shipped ] && doing=Doing   # "In Progress" is no column on the shipped vocabulary (#249, #468)
$BOARD --add $SG_OWNER $SG_PROJECT "$doing" "${live[@]}" 2>&1 >/dev/null | grep -v '^issue_state\.py: items read ' | sed "s/^/FRONTIER-BOARD $doing refused: /"   # a refused write is named, never dropped (#468)
echo "FRONTIER dispatched ${live[*]/#/#} in one run ($name)"
[ -n "$full" ] && echo "FRONTIER envelope full: $name asks for $cap children, its limit; startable issues past them wait for the next run" || true
