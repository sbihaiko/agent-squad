#!/bin/bash
# seconds: 0.13
# Regression test for envelope-spend.sh: the phrase a dispatch request carries as the spend of one child is the dispatch
# node's own budget, read from the file the engine will run.
#
# The pass that builds the request used to write a number of its own -- "spend 8" -- while the dispatch node of the graph
# the engine runs allows 4. The envelope refuses the whole request (`spend_outside_budget`), the router never spawns, and
# the run ends `no_dispatch`: a conflict-fix run for gui/82, gui/84 and gui/85 was born dead this way, and the branches
# behind it waited for a pass that could not start. autofrontier.sh was fixed first (#57); the passes beside it were
# not, so this suite guards the helper and every caller of it. The fork-close spawner was one of those callers and left
# with the local land route (#115); the three below are every spawner that remains.
#
# Offline and hermetic: its own temp SG_CHECKOUT holding the project's own workflow file, its own temp SG_SQUAD whose
# squad/ package is not there so the unreadable path can be exercised, no network.
# usage: bash test-envelope-spend.sh [path/to/envelope-spend.sh]
src=${1:-$(dirname "$0")/envelope-spend.sh}; T=$(mktemp -d); fail=0
here=$(cd "$(dirname "$src")" && pwd)
repo=$(cd "$here/../../.." && pwd)   # SG_SQUAD: the seed squad/workflows.py falls back to
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
if [ ! -f "$src" ]; then echo "FAIL the request carries the run's budget: $src not found"; rm -rf "$T"; exit 1; fi
for v in $(compgen -e | $G '^SG_'); do unset "$v"; done
export SG_SQUAD=$repo
graph() { # <dir> <spend_max>: the project's own dynamic workflow, one dispatch node carrying the budget
  mkdir -p "$1/docs/squad/workflows"
  printf '{"schema_version":1,"name":"dynamic","root":"root","nodes":[{"name":"root","kind":"loop","children":["route"]},{"name":"route","kind":"dispatch","max_children":4,"deadline":7200,"budget":{"stalls":{"min":2,"max":5},"spend":{"min":0.05,"max":%s}}}]}' "$2" \
    > "$1/docs/squad/workflows/dynamic.graph.json"
}
run() { SG_CHECKOUT=$1 bash "$src" "${2:-dynamic}" 2>/dev/null; }

graph "$T/c4" 4
got=$(run "$T/c4")
[ "$got" = "spend 4 (the dispatch node's budget: a request over it is rejected whole, spend_outside_budget)" ] \
  && ok "the number is the dispatch node's own budget" || ko "budget 4 read as: $got"

graph "$T/c8" 8
got=$(run "$T/c8")
[ "$got" = "spend 8 (the dispatch node's budget: a request over it is rejected whole, spend_outside_budget)" ] \
  && ok "a higher budget is carried as it stands, not clamped to a number of ours" || ko "budget 8 read as: $got"

# No number is invented when the budget cannot be read: a request over the limit is rejected whole, and a phrase naming no
# number is what lets the router ask for its own default.
mkdir -p "$T/cnone/docs/squad/workflows"
printf '{"schema_version":1,"name":"dynamic","root":"root","nodes":[{"name":"root","kind":"loop"}]}' \
  > "$T/cnone/docs/squad/workflows/dynamic.graph.json"
got=$(run "$T/cnone")
case "$got" in *spend\ [0-9]*) ko "a graph with no dispatch budget still printed a number: $got";;
  *"could not read that budget"*) ok "an unreadable budget is named, never guessed";;
  *) ko "unreadable budget read as: $got";; esac

# The guard that matters: no pass builds the request with a number of its own again.
bad=""
for s in autobug.sh branchfixpass.sh autohunt.sh; do
  $G -q 'spend 8 (the router may not request more' "$here/$s" && bad="$bad $s"
  $G -q 'envelope-spend.sh' "$here/$s" || bad="$bad $s(no-call)"
done
[ -z "$bad" ] && ok "every pass that spawns reads the budget from the envelope" \
  || ko "these passes still name a spend of their own:$bad"

rm -rf "$T"
[ "$fail" = 0 ] && echo "test-envelope-spend: PASS" || echo "test-envelope-spend: FAIL"
exit "$fail"
