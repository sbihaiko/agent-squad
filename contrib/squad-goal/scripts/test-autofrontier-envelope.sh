#!/bin/bash
# Regression test for autofrontier.sh: the pass reads the run's own envelope, from the file the engine will run, and the
# request it builds carries those numbers. The spend it names was a hardcoded 8 while the dispatch node of the graph the
# engine runs allows 4, so every child request was over the limit: the router read `spend_outside_budget` in its notes,
# left the spend out, and the run spent its turns on the disagreement (#57). The graph is resolved by id the way
# squad/workflows.py resolves it -- the project's docs/squad/workflows/<id>.graph.json, else the seed the engine falls back
# to -- never by hand-built path, which read the seed dir and found nothing there, so no bound applied at all.
# Offline and hermetic: stub gh (fixtures through jq), stub stack.sh, forkonly.sh, mkreq.sh and launch.py, its own temp
# SG_STATE and HOME.
# usage: bash test-autofrontier-envelope.sh [path/to/autofrontier.sh]
src=${1:-$(dirname "$0")/autofrontier.sh}; T=$(mktemp -d); fail=0
repo=$(cd "$(dirname "$src")/../../.." && pwd)   # SG_SQUAD: squad/caps.py, and the seed squad/workflows.py falls back to
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
if [ ! -f "$src" ]; then echo "FAIL the request carries the run's envelope: $src not found"; rm -rf "$T"; exit 1; fi
for v in $(compgen -e | $G '^SG_'); do unset "$v"; done
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
printf '#!/bin/bash\nsg_fork_only() { return 1; }\nsg_not_startable() { return 1; }\nsg_fork_preamble() { :; }\nsg_fork_branch() { echo "gui/$1"; }\nsg_fork_branch_pattern() { echo "gui/%%n"; }\n' > "$T/scripts/forkonly.sh"
printf '#!/bin/bash\n[ "$1" = --paths ] && exec bash "$(dirname "$0")/mkreq-real.sh" "$@"\necho x >> "$MKLOG"; cp "$2" "$MKLOG.body"; echo "$HOME/req.txt"\n' > "$T/scripts/mkreq.sh"
cp "$(dirname "$src")/mkreq.sh" "$T/scripts/mkreq-real.sh"; cp "$(dirname "$src")"/{models.json,req-checklist.txt,req-checklist-fork.txt} "$T/scripts/"
printf '%%s\n' 'open(__import__("os").environ["LAUNCHLOG"], "a").write("launch\n")' > "$T/scripts/launch.py"
chmod +x "$T/scripts/mkreq.sh"
printf '#!/bin/bash\necho "$*" >> "$BOARDLOG"\n' > "$T/board.sh"; chmod +x "$T/board.sh"
cp "$src" "$T/scripts/autofrontier.sh"
scene() { # <name>: fresh state, its own env.sh; no graph written here -- each case writes the one it needs
  d=$T/$1; mkdir -p "$d/st/req" "$d/st/cq" "$d/co/docs/squad/workflows" "$d/co/runs" "$d/home"
  : > "$d/gh.log"; : > "$d/st/active-runs.txt"; : > "$d/mk.log"; : > "$d/launch.log"; : > "$d/board.log"
  cat > "$T/scripts/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$d/st; SG_CHECKOUT=$d/co; SG_BASE=main
SG_FRONTIER_LABEL=frontier; SG_BUG_LABEL=bug; SG_CODING_RULES=rules; BOARD=$T/board.sh
E
  export HOME=$d/home SG_STATE=$d/st SG_CHECKOUT=$d/co SG_REPO=o/r SG_FRONTIER_LABEL=frontier MKLOG=$d/mk.log LAUNCHLOG=$d/launch.log BOARDLOG=$d/board.log
  export GH_ISSUES=$d/issues.json GH_OPEN=$d/open.json GH_CLOSED=$d/closed.json GH_ISSUE1=$d/issue1.json GH_ISSUEDIR=$d/issue; mkdir -p "$GH_ISSUEDIR"
  printf '[]' > "$GH_OPEN"; printf '[]' > "$GH_CLOSED"
  printf '{"number":0,"title":"t","body":"","created_at":"2026-01-01T00:00:00Z"}' > "$GH_ISSUE1"
}
graph() { # <dir> <max_children> <spend_max>: one root loop holding one dispatch node, with the budget the node carries
  printf '{"run":{"max_parallel":20},"nodes":[{"name":"root","kind":"loop","nodes":[{"name":"route","kind":"dispatch","max_children":%s,"max_parallel":2,"budget":{"stalls":{"min":2,"max":5},"spend":{"min":0.05,"max":%s}}}]}]}' "$2" "$3" > "$1"
}
issues() { printf '%s' "$1" > "$GH_ISSUES"; }
four() { issues '[{"number":41,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":42,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":43,"title":"a","body":"b","labels":[{"name":"wave:01"}]},{"number":44,"title":"a","body":"b","labels":[{"name":"wave:01"}]}]'; }
run() { SG_SINGLE_AUTOFRONTIER=1 bash "$T/scripts/autofrontier.sh" 2>&1; /bin/sleep 0.6; }
spent() { sed -nE 's/.*spend ([0-9.]+).*/\1/p' "$MKLOG.body" | head -1; }
asked() { sed -nE 's/.*call the spawn tool for the ([0-9]+) child.*/\1/p' "$MKLOG.body" | head -1; }
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

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
