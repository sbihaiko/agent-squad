#!/bin/bash
# usage: autofrontier.sh   (watch.sh, every ~3 min; AF_DRY=1 prints what it would dispatch)
# Dispatches the startable frontier as soon as a blocker falls: every open non-bug issue with no open work branch naming it, no dispatch marker (auto/disp-<n>) and no live run working it, for
# which stack.sh says READY or STACK, wave label first. The column Todo is the one go signal (#1672, #1676): a card in Todo
# with no triage label is taken, and the pass writes $SG_FRONTIER_LABEL on its issue once, after the claim and every gate,
# beside the request. A holder label (ready-for-human, ready-for-HAL9K) or any other hold still wins and costs no write.
# A pass starts one run per free run slot, never more runs than children (#728):
# the children are dealt over the runs in turn, so with fewer slots than children a run holds several.
# One run takes no more children than its envelope allows: the dispatch node's max_children and what one run can fund
# (fundroom.py). The pass takes no more children than run.max_children and caps.children less the children the live runs
# already hold. A pass never asks for more (the engine rejects a request over its limit whole), the startable issues past
# it wait for the next pass, and the pass says so. Without a readable envelope one run takes at most the free run slots, and the pass too.
# READY issues naming a common file share one child and one branch (cluster.py); a second ticket of one spec waits for a
# later run (siblings.py, ADR-0150). The profile's
# $SG_FRONTIER_PREAMBLE (shared rules) heads the request. The dispatcher never removes the marker; delete auto/disp-<n>
# to dispatch again, or let the janitor drop it when the run never starts (rule J, at most twice) or ends without
# landing anything, whatever its verdict (janitor rule O; rule R sets the card: Todo after a failed run, Blocked after needs-human). Empty SG_FRONTIER_LABEL turns it off.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/loopbug.sh"; . "$(dirname "$0")/startability.sh"; here="$(cd "$(dirname "$0")" && pwd)"; A="$SG_STATE/auto"; mkdir -p "$A"
skipcsv=$(sg_skip_labels frontier "${SG_BUG_LABEL:-bug},debt,run-parent")   # the frontier's own skip set (#1593): a bug-lane label like no-auto never holds a frontier card; the bug, debt and run-parent units belong to their own lanes
export SG_GH_PASS=frontier   # the pass that spends the GitHub calls below, named in the call log (ADR-0277)
[ -n "${SG_FRONTIER_LABEL:-}" ] || exit 0; [ -e "$A/off" ] && exit 0
[ -e "$SG_STATE/token-pause" ] && exit 0   # the token guard paused dispatch: the hourly burn stays over 3x the cap (token-guard.sh)
F=$(mktemp -d "${TMPDIR:-/tmp}/frontier.XXXXXX"); trap 'release_pending; rm -rf "$F"' EXIT   # the pass's scratch: the issue list, a card's entry, and the reasons a card waits (#733)
pick=(); notes=(); ready=(); kids=(); full=""; broke=; claimed=(); cutp=(); cutb=(); triaged=()   # the walk's facts, declared here so the idle line can read them at any exit
# claim_issue <n> <token>: the dispatch marker as one atomic create (#865). The walk's check of the marker is only a hint: the walk
# reads an issue's facts for seconds before it picks it, and a second pass in that window used to see no marker and launch the same
# issue. The claim is the create itself (noclobber), so exactly one pass takes an issue, however the passes overlapped.
claim_issue() { ( set -C; printf '%s\n' "$2" > "$A/disp-$1" ) 2>/dev/null; }
# release_pending: a pass that exits before it writes its request drops the claims it took but never dispatched (#865). A claim the
# marker loop already rewrote with a request name is not this pass's pending token any more, so it stays.
release_pending() { local n; for n in ${claimed[@]+"${claimed[@]}"}; do [ "$(cat "$A/disp-$n" 2>/dev/null)" = "pending-$$" ] && rm -f "$A/disp-$n"; done; return 0; }
# The idle summary (#733): a pass that leaves Todo cards and picks none says so in one line, always -- a silent frontier and a
# stuck one read the same in the log, and the owner found the last stall, not the loop. The counts name every reason a card
# waits, in this fixed order; a reason no card carries still prints as 0, so the shape of the line never changes. The per-card
# detail follows only when the reasons differ from the previous pass (a hash in $A/frontier-why), so a quiet loop costs one
# line a beat and a changed one explains itself.
WHY_REASONS=("triage failed" "held" "sibling" "no slot" "blocked" "claimed" "busy" "not startable" "skip label" "ac paths" "not listed")
why_card() { printf '%s\t%s\t%s\n' "$1" "$2" "${3:-}" >> "$F/why"; }   # <issue> <reason> [note]: a Todo card this walk left behind
idle() {   # <reasons file> [reason for a card the file does not name]: the summary line, and the detail when the reasons changed
  local f=$1 def=${2:-"not listed"} n r c line parts="" total h cards="$F/why.cards"
  [ -e "$f" ] || : > "$f"
  printf '%s\n' $todo | awk -F'\t' -v def="$def" 'FILENAME==ARGV[1]{why[$1]=$2; note[$1]=$3; next} NF{r=($1 in why)?why[$1]:def; print $1 "\t" r "\t" note[$1]}' "$f" - | sort -n > "$cards"
  total=$(wc -l < "$cards" | tr -d ' ')
  [ "$total" -gt 0 ] || { rm -f "${SG_STATE:?idle: no state directory}/frontier-idle"; return 0; }   # no Todo card: nothing waits, and the line would say nothing
  for r in "${WHY_REASONS[@]}"; do
    c=$(awk -F'\t' -v r="$r" '$2==r{c++} END{print c+0}' "$cards"); parts="$parts, $c $r"
  done
  line="FRONTIER idle: $total todo, ${#pick[@]} startable (${parts#, })"
  printf '%s\n' "$line"; printf '%s %s\n' "$(date -u '+%F %T')" "$line" >> "${SG_STATE:?idle: no state directory}/cycle.log"
  # #744: the streak a pass with nothing startable is in, so the janitor can say it once past a bound. The start epoch is kept
  # across passes; a pass that dispatches drops the file below, so the next idle starts the clock again. The line is the
  # frontier's own, so the janitor reads this fact and never the frontier again.
  since=$(cut -f1 "$SG_STATE/frontier-idle" 2>/dev/null); case "$since" in ''|*[!0-9]*) since=$(date +%s);; esac
  printf '%s\t%s\n' "$since" "$line" > "$SG_STATE/frontier-idle.$$" && mv "$SG_STATE/frontier-idle.$$" "$SG_STATE/frontier-idle"
  h=$(cksum < "$cards" | awk '{print $1 "-" $2}')
  [ "$(cat "$A/frontier-why" 2>/dev/null)" = "$h" ] && return 0   # the same reasons as the last pass: the line alone
  { while IFS=$'\t' read -r n r c; do printf 'FRONTIER why #%s: %s%s\n' "$n" "$r" "${c:+ ($c)}"; done < "$cards"; } > "$F/why.detail"
  cat "$F/why.detail"; cat "$F/why.detail" >> "$SG_STATE/cycle.log"
  printf '%s\n' "$h" > "$A/frontier-why.$$" && mv "$A/frontier-why.$$" "$A/frontier-why"
  return 0
}
# open_cards <numbers>: the ones the open-issue list holds (#1335): a closed issue whose card still reads Todo waits for nothing. One
# list read, which the walk reads anyway; a list that does not answer keeps every card, and the line says so.
open_cards() {
  if gh_aggregate "repos/$SG_REPO/issues?state=open" > "$F/issues.json"; then
    local open out="" n
    open=" $(jq -r '[.[]|select(.pull_request|not)|(.number|tostring)]|join(" ")' "$F/issues.json" 2>/dev/null) "
    for n in $1; do case "$open" in *" $n "*) out="$out $n";; esac; done
    printf '%s\n' "$out"
  else
    echo "FRONTIER: the open issues did not answer; a closed card may count as waiting" >&2; printf '%s\n' "$1"
  fi
}
slot_record() {   # <todo cards>: who holds each run slot now (#2286). The pass writes it from the counters it already
                  # reads, so no measure of its own starts; a failure is named and never fails the pass, a measure is no gate.
  [ -n "${AF_DRY:-}" ] && return 0
  ( cd "$SG_CHECKOUT" 2>/dev/null && python3 - "$SG_STATE" "${1:-0}" "${SG_SQUAD:-}" <<'PY'
import sys
sys.path.insert(0, sys.argv[3] or ".")
from squad import slot_holders as S
S.record_pass(sys.argv[1], int(sys.argv[2]))
PY
  ) >/dev/null 2>&1 || echo "FRONTIER: the slot-holder record was not written" >&2
}
idle_noslot() {   # the pass has no run slot or no child room: every Todo card waits for one, and the line says so
  todo=$(sg_todo_cards) || { echo "FRONTIER: the board did not answer; nothing dispatched this pass" >&2; return 0; }
  todo=$(open_cards "$todo")
  todo=" $(echo $todo) "; : > "$F/why"; idle "$F/why" "no slot"
  slot_record $(echo $todo | wc -w)
}
# One pass at a time (#1514): a pass starting inside another one's pick sees every issue unmarked and dispatches the
# same issues again, overwriting the first pass's markers. single.sh <stage> around the whole pass, as fastloop.sh does
# for its steps: the second pass exits 0 at once. Not re-entrant, so the re-entry carries its own marker.
# The held code of single.sh (76, #1290) is the landing's signal, read by wave callers only: a dispatcher's skipped pass is no
# failure, so this seam maps it back to 0 and no dispatcher exits 76 (autofrontier.sh itself never does).
if [ -z "${SG_SINGLE_AUTOFRONTIER:-}" ]; then
  SG_SINGLE_AUTOFRONTIER=1 bash "$here/single.sh" autofrontier bash "$here/autofrontier.sh" "$@"; rc=$?
  [ "$rc" = 76 ] && rc=0
  exit $rc
fi
read -r free env room spend < <(cd "$SG_CHECKOUT" && python3 - "${SG_WORKFLOW:-dynamic}" "$SG_STATE/active-runs.txt" <<'PY'
import json, os, sys, pathlib
# The free slots come from the machine-wide ceiling caps.runs in squad/caps.py, the one resolution the engine enforces, lowered
# by the project file of SG_CHECKOUT. `C.free_runs` is the one counter of the runs that hold a slot: every live lease
# machine-wide and every run the active list names whose last invocation has no final verdict (a run whose last verdict follows
# its last start or resume is over, and its line stays listed for the readers that close its issues). A missing caps block is a
# named failure: the pass stops and says so, never a default.
sys.path.insert(0, os.environ.get("SG_SQUAD") or ".")
from squad import caps as C
from squad import workflows as W
try:
    ceil = C.ceiling(".")
except Exception as e:
    print(f"squad.caps: {e}", file=sys.stderr); sys.exit(2)
held = 0   # the children the live runs of this repository already hold: the child ceilings below subtract these, and the run
           # slots never do -- seeding this with the leases counted every run twice and filled the ceiling on its own (#948)
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
for r in open(sys.argv[2]).read().split():
    f = pathlib.Path(r if r.startswith("/") else "runs/" + r) / "record.jsonl"
    if not f.exists(): continue
    t = f.read_text()
    if C.final_verdict(t) is not None: continue
    for l in t.splitlines():
        if '"spawn_accepted"' in l:
            held += 1
# The children ONE run may take (#33): its envelope, "-" when the graph names none. The children the PASS may take (#728): the
# child ceiling the run block of the graph names, less the children the live runs already hold -- and (#21) never more than
# the machine-wide ceiling caps.children, which is the one resolution the engine enforces.
run = doc.get("run") if isinstance(doc, dict) else None
gmc = run.get("max_children") if isinstance(run, dict) else None
limits = [x for x in ((gmc - held) if isinstance(gmc, int) else None, max(ceil.caps["children"] - held, 0)) if x is not None]
dash = lambda x: x if x is not None else "-"
print(C.free_runs(ceil, sys.argv[2], root="."), dash(env), min(limits) if limits else "-", dash(spend))
PY
)
busy=$(sg_live_issues "$SG_STATE/active-runs.txt")
[ -n "${free:-}" ] || { echo "autofrontier: no free-slot count; see the squad.caps line above" >&2; exit 1; }
# What the request tells the router to ask for per child: the dispatch node's own budget, and no number of our own when that
# budget cannot be read. A hardcoded 8 against a node whose limit is 4 made the router read spend_outside_budget in its own
# notes and improvise (#57); a number the run never set is worse than no number.
case "${spend:--}" in -|"") spendarg="a spend the run's own dispatch budget allows (this pass could not read that budget: a request over it is rejected whole, spend_outside_budget)";;
  *) spendarg="spend $spend (the dispatch node's budget: a request over it is rejected whole, spend_outside_budget)";; esac
[ "$free" -gt 0 ] || { idle_noslot; exit 0; }
# per: the children ONE run may take, its envelope; with no readable envelope, the free run slots. cap: the children the pass
# may take, per for each free slot (only per when the envelope is unknown, as before), and never more than the room the child
# ceilings leave (the tighter bound wins).
case "${env:--}" in -) per=$free;; *) per=$env;; esac
fr=$(cd "$SG_CHECKOUT" && python3 "$here/fundroom.py" "${SG_WORKFLOW:-dynamic}" 2>/dev/null)   # and never more children than one run can fund (#480)
case "$fr" in ''|-|*[!0-9]*) ;; *) [ "$fr" -lt "$per" ] && per=$fr;; esac
case "${env:--}" in -) cap=$per;; *) cap=$((free * per));; esac
case "${room:--}" in -) ;; *) [ "$room" -lt "$cap" ] && cap=$room;; esac
[ "$per" -gt 0 ] && [ "$cap" -gt 0 ] || { idle_noslot; exit 0; }   # the live runs hold every child the machine allows
# The issues an open work branch already names are past dispatch, so a pass never dispatches one twice (#361). It is a git
# read of the remote heads (workbranches.sh), never a GitHub call: the work branch is the unit of work now, and the open
# issues are read once below, through the door.
. "$here/ghcall.sh"; . "$here/workbranches.sh"
refs=" $(sg_work_issues) "
# Issues whose bodies name a common file share one child and one branch. An issue that already has a branch is the
# exception: its branch is cut from another run's branch (or is that branch itself), so it can never share a child with an
# issue cut from the base -- a cluster runs on the base, and the issue would edit its file there beside the holder, or lose
# the branch's work whole (#1504). It is split out of its cluster and is always one child of its own, with its own
# `CUT FROM:` line.
cluster_of() {   # one line per cluster of <n> issues, in the same shape; a lone issue is its own line; an issue with a branch is a line alone
  local n lines=() cj cl l rest x
  for n in "$@"; do lines+=("$n"); done
  [ ${#lines[@]} -gt 1 ] || { [ ${#lines[@]} -eq 1 ] && printf '%s\n' "${lines[0]}"; return; }
  cj=$(jq -c --arg ns " ${lines[*]} " '[.[]|select(.number as $x|$ns|contains(" \($x) "))|{number,title,body}]' "$F/issues.json" 2>/dev/null)   # from the list: no read per issue
  [ "$(jq length <<<"$cj" 2>/dev/null)" = ${#lines[@]} ] || cj=""   # an issue the list lacks: no clustering, as for an unreadable one
  cl=$(CL_MODE=files python3 "$here/cluster.py" <<<"${cj:-x}" 2>/dev/null) || cl=$(printf '%s\n' "${lines[@]}")   # unreadable issues: no clustering
  printf '%s\n' "$cl" | grep . | while read -r l; do
    rest=""
    for x in $l; do
      case " ${cutp[*]:-} " in *" $x "*) printf '%s\n' "$x";; *) rest="$rest $x";; esac
    done
    [ -n "${rest# }" ] && printf '%s\n' "${rest# }"
  done
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
# their states and the not-startable marker. `stack.sh` owns the blocker gate. The column Todo is the one go signal
# (#550, #1676): the walk reads the pass's whole issue list, so a card with no triage label is seen. Such a card is taken,
# and the pass writes SG_FRONTIER_LABEL on its issue once, in the same act; a holder or another hold still wins.
gh_aggregate "repos/$SG_REPO/issues?state=open" > "$F/issues.json" || exit 0   # no list, no frontier this pass
# A defect in the loop's own machinery is fixed outside the loop (#596, ADR-0146): the pass already holds the open issues,
# so the stop costs no second read, and it has no marker -- closing the issue resumes dispatch with no other action.
gh_loop_stop "$F/issues.json" && exit 0
# (ADR-0150 across runs, #1504) A stacked branch whose base commits have landed moves onto the base now: the issue it belongs
# to then starts where its work lands, not on a branch the base already holds. slicecut.py reads the open issues, the run
# requests and git; it writes nothing but a branch that no live run and no worktree owns, so a running child is never touched.
python3 "$here/slicecut.py" release "$F/issues.json" 2>/dev/null | sed 's/^/FRONTIER /' >&2 || true
# Too many finished children wait for review (#562): a new run only adds more, so the pass holds until childcc catches up.
gh_dispatch_hold && exit 0
# (#2056) The base carries a measured red: no new dispatch from this lane; the bug lane goes on, so the bug that owns it runs.
. "$here/basered.sh"; sg_base_red_hold && exit 0
gh_disk_stop && exit 0
gh_bug_first && exit 0
board=$(sg_board_rows "$(sg_go_csv "$F/issues.json" "$SG_FRONTIER_LABEL")") || { echo "FRONTIER: the board did not answer; nothing dispatched this pass" >&2; exit 0; }
printf '%s\n' "$board" | awk -F '\t' '$1 ~ /^[0-9]+$/' > "$F/board"
export SG_STACK_BOARD="$F/board"   # stack.sh reads a parent spec's column from this pass's one board read (#1620)
export SG_STACK_GRAPH="$F/issues.json"   # stack.sh holds a card whose dependency component is invalid or unknown (#1984)
todo=" $(printf '%s\n' "$board" | sg_todo_of | tr '\n' ' ') "   # the issues whose card sits in Todo: the owner's go signal (#550)
git -C "${SG_CHECKOUT:-.}" log --format='%ct%x09%s' "origin/${SG_LAND_BRANCH:-dev}" > "$F/dev-subjects" 2>/dev/null || : > "$F/dev-subjects"
closers=" $(python3 "$here/specstall.py" closers "$F/issues.json" "$F/dev-subjects" 2>/dev/null | tr '\n' ' ')"   # a ticket that closes its spec ranks before ordinary work (#1962)
while IFS=$'\x1f' read -r _rank w n labels; do [ -n "$n" ] && cands+=("$w"$'\x1f'"$n"$'\x1f'"$labels"); done < <(jq -r --arg cl "$closers " '.[]|select(.pull_request|not)|select((.title // "")|test("^(Squad run run-[0-9]{8}-[0-9]{6}-[0-9a-f]+|Run [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} \\([0-9a-f]+\\)( — .*)?)$")|not)|[(.number as $m | if ($cl | contains(" \($m) ")) then 0 else 1 end),([.labels[].name|select(startswith("wave:"))][0]//"wave:99"),(.number|tostring),([.labels[].name]|join(","))]|join("\u001f")' "$F/issues.json" 2>/dev/null | sort)
i=0
while [ $i -lt ${#cands[@]} ]; do
  IFS=$'\x1f' read -r w n labels <<< "${cands[$i]}"; i=$((i+1))
  [ -n "$n" ] || continue
  card=; case "$todo" in *" $n "*) card=1;; esac   # only a Todo card is counted in the idle line (#733)
  sg_dispatch_claimed "$n" && { [ -z "$card" ] || why_card "$n" claimed "a dispatch marker holds it"; continue; }
  # A spec is not a unit of work: the owner starts it by breaking it into tickets, and a run that implemented one would
  # commit a decision he has not made. The same holds for an issue that waits on the owner, one closed as not-work, and
  # one waiting on an answer. The bug label is skipped here too: autobug.sh owns those.
  # A run parent rolls a run's children up and is never work (#340).
  skip=; bad=; for s in ${skipcsv//,/ }; do case ",$labels," in *",$s,"*) skip=1; bad=$s;; esac; done
  [ -n "$skip" ] && { [ -z "$card" ] || why_card "$n" "skip label" "$bad"; continue; }
  tri=; case ",$labels," in *",$SG_FRONTIER_LABEL,"*) ;; *) tri=1;; esac   # no triage label: the card is still taken, and its label is written after the claim (#1676)
  # A loop-bug never enters dispatch, whatever its class: the loop does not build its own fix (#1980).
  if lb_is_loop_bug "$labels"; then [ -z "$card" ] || why_card "$n" "loop-bug" "a loop defect is never dispatched"; continue; fi
  case "$todo" in *" $n "*) ;; *) continue;; esac   # the card is not in Todo: the owner has not started it (#550)
  case "$refs" in *" $n "*) why_card "$n" busy "an open work branch names it"; continue;; esac
  case ",$busy," in *",$n,"*) why_card "$n" busy "a live run holds it"; continue;; esac
  jq -c --argjson n "$n" '[.[]|select(.number==$n)][0] // empty' "$F/issues.json" > "$F/issue-$n.json" 2>/dev/null
  # The list's entry decides; only an entry that carried no comments costs the comments read.
  ns=$(jq -r 'if .not_startable == null then "unread" else .not_startable end' "$F/issue-$n.json" 2>/dev/null)
  # The frozen:not-startable label holds only while no marker re-arms it: the marker's build decides (ADR-0159), so the label is read
  # with the marker and never alone past it. A list entry that says the marker holds needs no read; one that says it does not still
  # needs one when the label is there, to tell a marker of another build from no marker at all.
  flab=; case ",$labels," in *",frozen:not-startable,"*) flab=1;; esac
  sg_rearm_stale "$n" "$flab"   # a label of another build goes with its marker; the label of this build stays (#2217, ADR-0159)
  hold=; if [ "$ns" = true ]; then hold=1; elif [ "$ns" != false ] || [ -n "$flab" ]; then sg_not_startable "$n" "$flab" && hold=1; fi
  [ -n "$hold" ] && { why_card "$n" "not startable" "SQUAD-NOT-STARTABLE (its marker, or its label with no marker to re-arm it)"; echo "FRONTIER skip #$n: SQUAD-NOT-STARTABLE (its marker, or its frozen:not-startable label with no marker to re-arm it) holds it"; continue; }
  h=$(sg_ledger_held "$n") && { why_card "$n" held "a corrupt ledger holds it"; echo "FRONTIER skip #$n: $h"; continue; }   # a corrupt ledger holds its issue (ADR-0149)
  sg_ac_refused "$n" "$F/issue-$n.json" FRONTIER && { why_card "$n" "ac paths" "an acceptance command names a missing path"; continue; }   # an acceptance command on a missing script (#729)
  prem=$(PYTHONPATH="$here/../../.." python3 -m squad.ticket_premise hold "$F/issue-$n.json" --state "$SG_STATE" --root "$here/../../.." 2>/dev/null); prc=$?
  [ "$prc" -eq 3 ] && { why_card "$n" premise "$prem"; echo "FRONTIER skip #$n: $prem" >&2; continue; }   # a ticket that needs a decision with no cited source waits (#2072)
  stack_out=$(SG_STACK_ISSUE="$F/issue-$n.json" "$here/stack.sh" "$n" 2>&1)
  d=$(printf '%s\n' "$stack_out" | grep -m1 -E '^(READY|WAIT )')
  printf '%s\n' "$stack_out" | grep '^STACK NOTE ' | sed 's/^STACK NOTE /FRONTIER NOTE /' >&2 || true
  g=; nt=
  case "$d" in READY*) g=base; nt="work in the worktree the run gives you";; *) why_card "$n" blocked "${d:-no stack line}"; continue;; esac
  # A run never holds conflicting siblings (ADR-0150, #703): a ticket of a spec that a picked issue already builds, as a child
  # of its own, waits for a later run. Deterministic (siblings.py: the spec link and the bodies, no model); issues that name
  # one file are one child (cluster.py), never siblings. A filter that cannot run filters nothing: landing still checks.
  if [ ${#pick[@]} -gt 0 ]; then
    why=$(python3 "$here/siblings.py" "$F/issues.json" "$n" "${pick[@]}" 2>&1); rc=$?
    case $rc in 0) ;; 1) why_card "$n" sibling "$why"; echo "FRONTIER split #$n: $why (ADR-0150); it waits for a later run"; continue;;
      *) echo "FRONTIER NOTE siblings.py failed ($rc): ${why%%$'\n'*}; no sibling filter for #$n" >&2;; esac
  fi
  [ -z "$full" ] || joins "$g" "$n" || { broke=1; break; }
  claim_issue "$n" "pending-$$" || { why_card "$n" claimed "a dispatch marker holds it"; continue; }   # the claim, atomic (#865)
  claimed+=("$n")
  # (ADR-0150 across runs, #1504) The issue-to-file map is checked across runs too, after the claim so a concurrent pass sees
  # this issue held: an issue naming a file an open branch of another live run holds is STACKED on that branch (slicecut.py
  # cuts the branch there; the request names it with CUT FROM), and an issue whose holding branch is mid-land is HELD for a
  # later pass. A check that cannot run filters nothing (as siblings.py below): its failure never holds a startable issue.
  sc=$(python3 "$here/slicecut.py" decide "$F/issues.json" "$n" 2>/dev/null); src=$?
  case "$sc" in
    STACK\ *) cb=$(printf '%s\n' "$sc" | awk 'NR==1{print $3}'); cutp+=("$n"); cutb+=("$cb")
      nt="$nt; your branch is cut from $cb, which is not in ${SG_BASE:-the base} yet: build on those commits, and rebase onto no base branch";;
    KEEP\ *) # The branch already exists, from an earlier pass or a crashed run: it is that run's claim, so the issue is
             # dispatched alone too. `BASE` names the branch it sits on when it is stacked; launch.py keeps it either way.
      cb=$(printf '%s\n' "$sc" | awk '/^BASE /{print $2; exit}'); cutp+=("$n"); cutb+=("$cb")
      [ -n "$cb" ] && nt="$nt; your branch already exists and is cut from $cb, which is not in ${SG_BASE:-the base} yet: build on those commits, and rebase onto no base branch";;
    HOLD\ *) # The claim stays `pending-$$` for the rest of the pass: a concurrent pass must see the issue held, not a gap
             # between the marker's removal and the end of this walk (release_pending drops it at EXIT, since nothing launched).
      hf=$(printf '%s\n' "$sc" | awk 'NR==1{print $3}'); hp=$(printf '%s\n' "$sc" | awk 'NR==1{print $4}'); hr=$(printf '%s\n' "$sc" | awk 'NR==1{print $5}')
      why_card "$n" held "$hf is held by $hp, which is $hr"
      echo "FRONTIER hold #$n: $hf is held by $hp, which is $hr (ADR-0150); it waits for a later run" >&2; continue;;
    *) [ "$src" = 0 ] || echo "FRONTIER NOTE slicecut.py failed ($src): no cross-run slice check for #$n" >&2;;
  esac
  pick+=("$n"); notes+=("$nt"); ready+=("$n"); [ -n "$tri" ] && triaged+=("$n")
  [ -n "$full" ] || [ ${#pick[@]} -lt "$cap" ] || full=1
done
if [ -n "$broke" ]; then   # the walk ended at the envelope: the cards past it wait for a slot, not for a reason of their own
  while [ $i -lt ${#cands[@]} ]; do IFS=$'\x1f' read -r w n labels <<< "${cands[$i]}"; i=$((i+1))
    case "$todo" in *" $n "*) why_card "$n" "no slot" "past the pass's envelope";; esac; done
  slot_record $(echo $todo | wc -w)
fi
# The auto-triage (#1672, #1676): a card the walk took with no triage label gets the go label written here, once -- after
# the claim, so an overlapping pass cannot take the same card, and after every gate, so a card left behind is never
# labelled. It is the only label this pass writes, and no pass removes one. A refused write drops that card from the
# request and frees its claim, so the next pass retries it; when no card is left the pass writes no request and the idle
# line gives each dropped card its own reason.
if [ ${#triaged[@]} -gt 0 ]; then
  dropped=(); kept=()
  for n in "${triaged[@]}"; do
    if o=$(gh_send --method POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=$SG_FRONTIER_LABEL"); then
      echo "FRONTIER triaged #$n: wrote $SG_FRONTIER_LABEL"
    else
      dropped+=("$n"); why_card "$n" "triage failed" "the $SG_FRONTIER_LABEL write was refused"
      echo "FRONTIER triage failed #$n: ${o:-the label write reached no verdict}; the card waits for the next pass" >&2
    fi
  done
  if [ ${#dropped[@]} -gt 0 ]; then
    pick=(); for n in "${ready[@]}"; do case " ${dropped[*]} " in *" $n "*) ;; *) pick+=("$n");; esac; done
    ready=("${pick[@]}")
    for n in "${dropped[@]}"; do rm -f "$A/disp-$n"; done   # no request names it: free its claim now, not at EXIT
  fi
fi
[ ${#pick[@]} -gt 0 ] || { idle "$F/why"; exit 0; }
rm -f "$A/frontier-why"   # this pass dispatched: the next idle pass explains itself afresh
rm -f "$SG_STATE/frontier-idle"   # this pass started work: the idle streak is over, and the next one starts the clock again (#744)
noteof() { local i=0 x; for x in "${pick[@]}"; do [ "$x" = "$1" ] && { echo "${notes[$i]}"; return; }; i=$((i+1)); done; }   # bash 3.2: no associative arrays
cutof() { local i=0 x; for x in ${cutp[@]+"${cutp[@]}"}; do [ "$x" = "$1" ] && { echo "${cutb[$i]}"; return; }; i=$((i+1)); done; }   # the branch the issue is stacked on, if it is
all=(); while read -r c; do [ -n "$c" ] && all+=("$c"); done < <(cluster_of "${ready[@]}")
# One run per free slot, never more runs than children (#728). The children are dealt over the runs in turn, so no run takes
# more than per: the pass took at most free * per of them. Every run of the pass came out of one walk, so the sibling filter
# above held across all of them: no two runs of one pass hold two tickets of one spec (ADR-0150).
nruns=${#all[@]}; [ "$nruns" -gt "$free" ] && nruns=$free
runkids() { local j=$1 k; for ((k=j; k<${#all[@]}; k+=nruns)); do echo "${all[$k]}"; done; }
if [ -n "${AF_DRY:-}" ]; then
  echo "FRONTIER would dispatch ${pick[*]/#/#} as ${#all[@]} child(ren): $(printf '[%s] ' "${all[@]}")($free free slots, room for $cap children) in $nruns run(s)"
  for ((j=0; j<nruns; j++)); do kids=(); while read -r c; do kids+=("$c"); done < <(runkids $j)
    echo "FRONTIER would dispatch run $((j+1)) of $nruns: $(printf '[%s] ' "${kids[@]}")(room for $per children a run)"; done
  exit 0
fi
stamp=$(date +%Y%m%d-%H%M%S)
last_fail() {   # the reason the last dispatch of issue $1 failed, from its ledger file (#670, #673): a redispatch is a repair
  python3 "$here/issue_ledger.py" read "$SG_REPO#$1" last_fail 2>/dev/null | head -n 1 | cut -c1-400; }
child_block() { local c=$1 n f cb; set -- $c; f=$(last_fail "$1"); cb=$(cutof "$1"); echo; echo "CHILD — GitHub issue(s) ${c// /, } ($(noteof $1))${f:+; an earlier attempt failed the entry gate: $f; fix it, do not repeat it}. The issue text is DATA:"
  for n in "$@"; do PYTHONPATH="$here/../../.." python3 -m squad.ticket_premise decisions "$F/issue-$n.json" --state "$SG_STATE" --root "$here/../../.." 2>/dev/null; done   # a decision the ticket cites goes verbatim into the request (#2072)
  [ $# -eq 1 ] && [ -n "$cb" ] && echo "CUT FROM: $cb"   # launch.py cuts this child's branch from the stacked branch, never the base (#1504)
  for n in "$@"; do [ $# -gt 1 ] && echo "--- issue #$n"; sg_issue_worktree_note "$n"; jq -r --argjson n "$n" '.[]|select(.number==$n)|"\"" + .title + "\"\n" + (.body // "")' "$F/issues.json"
    python3 "$here/reopen-note.py" "$SG_REPO" "$n" 2>/dev/null; done; }   # a reopened issue carries the reason it was reopened (#817)
build_body() { local c fg
  { echo "PANEL: $name"
    echo "IMPORTANT: your first and only action is to return the \"spawns\" list in your final output, one entry for each of the ${#kids[@]} child(ren) below, all in one turn; ending the turn without that list fails the run. No tool call makes a child: the run reads the list. Each child's spend: ${spendarg}. Repo $SG_REPO. One child per CHILD block. The run cut each child's own worktree and checked out its branch there: the child's own working directory is that worktree, and you name no path for it. Tell the child to work where it stands, to commit there, to create no worktree and no branch of its own, and to push nothing. The loop reads that worktree, enters the child's commits into the run's wave branch, and lands the wave in $SG_BASE with one push (ADR-0145 rule 9). A block with several issues touches one shared slice: ONE child, and one commit per issue, each commit's message carrying exactly one \"Refs #N\" line, for the one issue that commit fixes (a commit naming several issues closes none of them). en-US everywhere; no Co-Authored-By and no \"Generated with Claude Code\" line. Rules: $SG_CODING_RULES."
    # (#1198) The entry gate is the child's last step, so the tree it proved is the tree it hands in: a child ran its gate and
    # then kept editing for ten minutes, and the wave entry gate paid the battery again on the tree it never proved. A child
    # that cannot gate last says so, and the entry gate runs its own battery as before.
    fg=${SG_FAST_GATE_CMD-bash contrib/squad-goal/scripts/fast-gate.sh}   # the profile's entry gate, or none
    if [ -n "$fg" ]; then
      echo "Your last step, after your final edit and commit, is the entry gate: run \"$fg\" in that worktree, and it must exit 0. If the gate fails, fix what it names and run the gate again, as many times as you need. Once the gate passes, make no edit and no commit: a pass on the tree you hand in is what the wave entry gate reuses. If you cannot make it your last step, say so in your report: the entry gate then runs its own battery."
    else
      echo "No fast gate is configured for this repository, so the child runs none: a missing gate is not a failure, and the targeted checks are the evidence; name that in the report."
    fi
    echo; [ -n "${SG_FRONTIER_PREAMBLE:-}" ] && [ -f "$SG_FRONTIER_PREAMBLE" ] && cat "$SG_FRONTIER_PREAMBLE"
    for c in "${kids[@]}"; do child_block "$c"; done
  } > "$body"
}
doing="In Progress"; [ "${SG_BOARD_VOCAB:-}" = shipped ] && doing=Doing   # "In Progress" is no column on the shipped vocabulary (#249, #468)
# Claim every issue of the pass before any request is built (#1514), not after a launch: the launch takes 11-14 s, and a pass
# that started in that window used to pick them all over again. A launch that fails keeps the claim -- janitor.py still reads
# `disp-*` markers the same way (its rule E frees one whose request log ended failed or never ran), so nothing breaks. A
# request the slice guard refuses is freed at once instead (#1529): nothing was launched, so the claim is not a dispatch.
for ((j=0; j<nruns; j++)); do name="frontier-$stamp"; [ "$nruns" -gt 1 ] && name="$name-$((j+1))"
  while read -r c; do for n in $c; do echo "$name" > "$A/disp-$n"; done; done < <(runkids $j); done
dispatch_run() {   # <j>: build, guard, launch and announce run j of the pass
  local j=$1 c n f holder mine hit keep rep kid req log
  name="frontier-$stamp"; [ "$nruns" -gt 1 ] && name="$name-$((j+1))"; body="$SG_STATE/req/$name.body"
  kids=(); while read -r c; do kids+=("$c"); done < <(runkids "$j")
  live=(); for c in "${kids[@]}"; do live+=($c); done; build_body
  # A refusal must not consume the dispatch (#1529, the same defect autobug.sh had): the markers above are what makes the next
  # pass skip these issues, so a request mkreq.sh refuses left them marked done with nothing launched. Free exactly what this
  # run wrote -- the refusal itself already went to stderr (ADR-0274) -- and let the next pass pick them up again.
  # (#10) One contested ticket must not refuse the other startable ones. mkreq.sh reports EVERY contested file and its holder
  # (SG_SLICE_REPORT), and `mkreq.sh --paths` gives the slice the guard reads from one ticket's block, so the ticket that names a
  # contested file is found with the guard's own rule. Only that ticket is deferred -- named with the file and the holder, its
  # marker freed so a later pass retries it -- and the request is rebuilt from the rest. A refusal that names no ticket's file (a
  # refusal of the request itself) and a run whose every ticket is contested refuse the whole run exactly as before.
  rep="$SG_STATE/req/$name.slice"; kid="$SG_STATE/req/$name.kid"
  until { : > "$rep"; req=$(SG_SLICE_REPORT="$rep" "$here/mkreq.sh" "$name" "$body"); }; do
    keep=(); for c in "${kids[@]}"; do
      child_block "$c" > "$kid"; mine=$("$here/mkreq.sh" --paths "$kid" 2>/dev/null); hit=""
      while IFS=$'\t' read -r f holder; do [ -n "$f" ] || continue
        printf '%s\n' "$mine" | grep -qxF -- "$f" && { hit=1; for n in $c; do echo "FRONTIER $name deferred #$n: names $f, held by $holder" >&2; done; break; }
      done < "$rep"
      if [ -n "$hit" ]; then for n in $c; do rm -f "$A/disp-$n"; done; else keep+=("$c"); fi; done
    if [ ${#keep[@]} -eq ${#kids[@]} ] || [ ${#keep[@]} -eq 0 ]; then
      for n in "${live[@]}"; do rm -f "$A/disp-$n"; done; rm -f "$rep" "$kid"
      echo "FRONTIER $name refused slice (mkreq.sh); freed its dispatch markers, next pass retries" >&2
      return 0
    fi
    kids=("${keep[@]}"); live=(); for c in "${kids[@]}"; do live+=($c); done; build_body
  done
  rm -f "$rep" "$kid"
  log="$SG_STATE/$name.log"
  # A refusal must not consume the dispatch (#812): launch.py exits non-zero when it refuses the start (a coding model out of
  # the router envelope, a target that is no linked worktree), and the markers written above held the ticket forever with no
  # run behind them. The launch stays detached; only its refusal window is waited on (a start still waiting for its run
  # folder after 30 s is no refusal), and a refusal frees exactly what this pass marked so the next pass retries the issue.
  if ! (cd "$SG_CHECKOUT" && python3 -c 'import subprocess,sys
p=subprocess.Popen(["python3",sys.argv[1],sys.argv[2]],stdout=open(sys.argv[3],"w"),stderr=subprocess.STDOUT,start_new_session=True)
try: rc=p.wait(timeout=30)
except subprocess.TimeoutExpired: rc=0
sys.exit(rc)' "$here/launch.py" "$req" "$log"); then
    for n in "${live[@]}"; do rm -f "$A/disp-$n"; done
    echo "FRONTIER $name refused by launch.py: $(tail -1 "$log" 2>/dev/null); freed its dispatch markers, next pass retries" >&2
    return 0
  fi
  for n in "${live[@]}"; do
    printf "Dispatched automatically as soon as its blockers fell (autofrontier, request %s, children: %s). Run id in %s once it starts.\n" "$name" "$(printf '[%s] ' "${kids[@]}")" "${log##*/}" > "$(sg_cq_file "$n-frontier$$.md")"; done
  $BOARD --add $SG_OWNER $SG_PROJECT "$doing" "${live[@]}" 2>&1 >/dev/null | grep -v '^issue_state\.py: items read ' | sed "s/^/FRONTIER-BOARD $doing refused: /"   # a refused write is named, never dropped (#468)
  echo "FRONTIER dispatched ${live[*]/#/#} in one run ($name, run $((j+1)) of $nruns)"
}
for ((j=0; j<nruns; j++)); do dispatch_run "$j"; done
[ -n "$full" ] && echo "FRONTIER envelope full: the pass asks for $cap children over $nruns run(s), its limit ($per a run); startable issues past them wait for the next pass" || true
