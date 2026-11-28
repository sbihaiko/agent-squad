#!/bin/bash
# seconds: 12.50
# The floor never moves a card back (#550, ADR-0143): a card that has a column keeps it, whatever its labels, so a no-auto
# bug the owner put in Todo stays in Todo and a bug in Backlog stays in Backlog. Only an open issue with no card gets a
# column from the floor, once: Todo for a bug, Backlog for anything else (with the shipped vocabulary). A run parent gets no
# card at all (#500). Stubbed gh and board.sh; a temp state dir, so the live loop state and board are never read or written.
# usage: bash test-janitor-floor-startable.sh [janitor.py]
# The fixture copies the janitor into a temp folder, so that copy's own path names no repository. The janitor loads
# squad/procs.py from the tree its script sits in, then SG_SQUAD, SG_CHECKOUT, else the caller's cwd and its parents. The
# daily pass runs this suite from outside any checkout with every SG_* cleared, so every root was empty and the pass died
# before a rule (ADR-0149). The suite drops the inherited SG_* and names the tree it ships in (#3077).
for v in $(env | /usr/bin/grep -o '^SG_[A-Za-z0-9_]*'); do unset "$v"; done
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; here="$(cd "$(dirname "$src")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"   # the tree the copied janitor loads squad/procs.py, squad/active_runs.py and squad/handhold.py from
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/spec_fold.py" "$T/s/"
cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$1" in
  --run-fields|--clear-run) ;;
  --add) echo "ADD $2 $3 $4 $5" >> "TMPDIR/board.calls";;
  *) echo "STATUS $1 $2 $3 $4" >> "TMPDIR/board.calls";;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
# 2201 no label, parked in Backlog by the owner (#482); 2202 a no-auto bug the owner set in Code Complete (#466); 2203 a bug;
# 2204 the frontier label; 2205 debt; 2206 a run parent; 2207 the frontier label and a frontier skip label; 2209 a no-auto
# bug in Todo (#550). No card: 2210 a bug, 2211 no label, 2212 the frontier label
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"
case "$a" in
  *--include*search/issues*) echo '{"total_count":0,"items":[]}';;
  *--include*issues/*/comments*) echo '[]';;
  *search/issues*) echo 0;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)
    printf '2201\tBacklog\thttps://api.github.com/repos/x/y\n2202\tCode Complete\thttps://api.github.com/repos/x/y\n'
    for n in 2203 2204 2205 2206 2207 2208; do printf '%s\tBacklog\thttps://api.github.com/repos/x/y\n' "$n"; done
    printf '2209\tTodo\thttps://api.github.com/repos/x/y\n2213\tDoing\thttps://api.github.com/repos/x/y\n';;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 2201 2202 2203 2204 2205 2206 2207 2208 2209 2210 2211 2212 2213; do
      case $n in
        2201) l='';; 2202) l='"bug","no-auto"';; 2203) l='"bug"';; 2204) l='"ready-for-agent"';; 2205) l='"debt"';;
        2206) l='"run-parent","enhancement"';; 2207) l='"ready-for-agent","frozen:dependency"';; 2208) l='"bug","frozen:dependency"';;
        2209) l='"bug","no-auto"';; 2213) l='"ready-for-agent"';; 2210) l='"bug"';; 2211) l='';; 2212) l='"ready-for-agent"';;
      esac
      labs=$(printf '%s' "[$l]" | jq -c '{nodes:[.[]|{name:.}]}')
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":%s,"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}' "$sep" "$n" "$n" "$labs"; sep=,; done
    printf ']}}}}';;
  *issues/22*/labels*) echo '[]';;
  *issues/22*) echo $'open\t\tIssue';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { rm -f "$T/board.calls"; rm -rf "$T/st/gh-cache"; SG_SQUAD="$repo" SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" "$@" python3 -I "$T/s/janitor.py" 2>&1; }
has() { /usr/bin/grep -q "$1" "$T/board.calls" 2>/dev/null; }
bc() { cat "$T/board.calls" 2>/dev/null; }

o=$(run env SG_FRONTIER_LABEL=ready-for-agent)
! has ' 2201$' && ok "an issue in Backlog stays where the owner put it" || ko "#2201 moved: $(bc) $o"
! has ' 2202$' && ok "a no-auto bug in Code Complete keeps its column" || ko "#2202 moved: $(bc)"
! has ' 2203$' && ok "a bug in Backlog keeps its column: the floor never moves a card" || ko "#2203 moved: $(bc)"
# A bug card in Backlog is dispatched by no pass (autobug reads Todo only), so the janitor names it once in cycle.log and writes
# nothing: only the owner moves it (ADR-0142, ADR-0143 rule 1). A second pass over the same state repeats no line.
bl="janitor BUG-IN-BACKLOG #2203: a bug card sits in Backlog; no pass dispatches it until the owner moves it to Todo"
# The passes above already named it and left the once-marker: this block starts with no note state, so its first pass is the first.
rm -f "$T/st/cycle.log" "$T/st/auto/janitor-bugback-"*
o=$(run env SG_FRONTIER_LABEL=ready-for-agent)
n1=$(/usr/bin/grep -cF "$bl" "$T/st/cycle.log" 2>/dev/null); n1=${n1:-0}
[ "$n1" = 1 ] && ok "a bug card in Backlog is named once in cycle.log" || ko "BUG-IN-BACKLOG for #2203 printed $n1 times (want 1)"
! /usr/bin/grep -q '2203' "$T/board.calls" 2>/dev/null && ok "the BUG-IN-BACKLOG note writes no board fact for #2203" || ko "#2203 written by a note: $(bc)"
o=$(run env SG_FRONTIER_LABEL=ready-for-agent)
n2=$(/usr/bin/grep -cF "$bl" "$T/st/cycle.log" 2>/dev/null); n2=${n2:-0}
[ "$n2" = 1 ] && ok "a second pass over the same state prints the BUG-IN-BACKLOG note no more" || ko "BUG-IN-BACKLOG for #2203 printed $n2 times over two passes (want 1)"
! has ' 2204$' && ok "an issue with the frontier label in Backlog keeps its column" || ko "#2204 moved: $(bc)"
# #1034: a Doing card that no run holds and no marker claims is never released. A card a person placed and a stuck card look
# the same, so the janitor names it once and leaves it; only the owner moves it (ADR-0143 rule 1).
dl="janitor DOING-NO-RUN #2213: a card sits in Doing with no live run and no marker; the janitor leaves it and the owner moves it"
rm -f "$T/st/cycle.log" "$T/st/auto/janitor-doing-norun-"*
o=$(run env SG_FRONTIER_LABEL=ready-for-agent)
d1=$(/usr/bin/grep -cF "$dl" "$T/st/cycle.log" 2>/dev/null); d1=${d1:-0}
[ "$d1" = 1 ] && ok "a Doing card with no live run and no marker is named once" || ko "DOING-NO-RUN for #2213 printed $d1 times (want 1)"
! has ' 2213$' && ok "a Doing card with no live run and no marker is never moved" || ko "#2213 moved: $(bc)"
o=$(run env SG_FRONTIER_LABEL=ready-for-agent)
d2=$(/usr/bin/grep -cF "$dl" "$T/st/cycle.log" 2>/dev/null); d2=${d2:-0}
[ "$d2" = 1 ] && ok "a second pass over the same state prints the DOING-NO-RUN note no more" || ko "DOING-NO-RUN for #2213 printed $d2 times over two passes (want 1)"
! has ' 2213$' && ok "the second pass leaves the Doing card alone too" || ko "#2213 moved on the second pass: $(bc)"
! has ' 2205$' && ok "a debt issue in Backlog keeps its column" || ko "#2205 moved: $(bc)"
has '^STATUS --remove o 4 2206$' && ! has 'STATUS o 4 [A-Za-z ]* 2206$' && ok "a run parent gets no column, and its card is removed (#500)" || ko "#2206 not removed: $(bc)"
! has ' 2207$' && ok "the frontier label with a frontier skip label keeps its column" || ko "#2207 moved: $(bc)"
! has ' 2209$' && ok "a no-auto bug in Todo stays in Todo, never Backlog (#550)" || ko "#2209 moved: $(bc)"
has '^ADD o 4 Todo 2210$' && ok "a bug with no card is added in Todo" || ko "#2210 not added in Todo: $(bc)"
has '^ADD o 4 Backlog 2211$' && ok "an issue with no label and no card is added in Backlog" || ko "#2211 not added in Backlog: $(bc)"
has '^ADD o 4 Backlog 2212$' && ok "the go label alone adds no card in Todo: the owner moves it there" || ko "#2212 not added in Backlog: $(bc)"
# #537: a profile skip list without the frozen labels changes nothing on the floor
o=$(run env SG_FRONTIER_LABEL=ready-for-agent SG_BUG_SKIP_LABELS=needs-owner,no-auto,wontfix,question)
! has ' 2208$' && ok "a frozen:dependency bug keeps its column under a profile skip list" || ko "#2208 moved: $(bc)"

o=$(run env -u SG_FRONTIER_LABEL)
! has ' 2201$' && ! has ' 2209$' && ok "with no frontier label a card keeps its column too" || ko "a card moved without a frontier label: $(bc) $o"
has '^ADD o 4 Todo 2210$' && has '^ADD o 4 Backlog 2211$' && ok "with no frontier label a new card is Todo for a bug, Backlog otherwise" || ko "no-card floor wrong without a frontier label: $(bc)"

rm -rf "$T"; [ "$fail" = 0 ] && echo "PASS" || { echo "FAILED"; exit 1; }
