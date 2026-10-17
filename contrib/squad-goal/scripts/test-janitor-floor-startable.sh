#!/bin/bash
# The floor puts an open issue in Todo only when a dispatcher takes it (#488): the definition says Todo is startable. With
# the shipped vocabulary and a frontier label set, autobug takes a bug or debt issue with none of SG_BUG_SKIP_LABELS, and the
# frontier takes an issue that carries SG_FRONTIER_LABEL and none of its skip labels; every other open issue floors in
# Backlog. With no frontier label the floor is Todo, as before. Stubbed gh and board.sh; a temp state dir, so the live loop
# state and board are never read or written.
# usage: bash test-janitor-floor-startable.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; here="$(cd "$(dirname "$src")" && pwd)"
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/spec_fold.py" "$T/s/"
cp "$here/ghcall.sh" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
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
# 2204 the frontier label; 2205 debt; 2206 a run parent; 2207 the frontier label and a frontier skip label
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
    for n in 2203 2204 2205 2206 2207; do printf '%s\tBacklog\thttps://api.github.com/repos/x/y\n' "$n"; done;;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 2201 2202 2203 2204 2205 2206 2207; do
      case $n in
        2201) l='';; 2202) l='"bug","no-auto"';; 2203) l='"bug"';; 2204) l='"ready-for-agent"';; 2205) l='"debt"';;
        2206) l='"run-parent","enhancement"';; 2207) l='"ready-for-agent","run-parent"';;
      esac
      labs=$(printf '%s' "[$l]" | jq -c '{nodes:[.[]|{name:.}]}')
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":%s,"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}' "$sep" "$n" "$n" "$labs"; sep=,; done
    printf ']}}}}';;
  *issues/22*/labels*) echo '[]';;
  *issues/22*) echo $'open\t\tIssue';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { rm -f "$T/board.calls"; rm -rf "$T/st/gh-cache"; SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" "$@" python3 -I "$T/s/janitor.py" 2>&1; }
has() { /usr/bin/grep -q "$1" "$T/board.calls" 2>/dev/null; }
bc() { cat "$T/board.calls" 2>/dev/null; }

o=$(run env SG_FRONTIER_LABEL=ready-for-agent)
! has ' 2201$' && ok "an issue no dispatcher takes stays in Backlog, where the owner put it" || ko "#2201 moved: $(bc) $o"
has '^STATUS o 4 Backlog 2202$' && ok "a no-auto bug floors in Backlog, never Todo" || ko "#2202 not Backlog: $(bc)"
has '^STATUS o 4 Todo 2203$' && ok "a bug autobug takes floors in Todo" || ko "#2203 not Todo: $(bc)"
has '^STATUS o 4 Todo 2204$' && ok "an issue with the frontier label floors in Todo" || ko "#2204 not Todo: $(bc)"
has '^STATUS o 4 Todo 2205$' && ok "a debt issue autobug takes floors in Todo" || ko "#2205 not Todo: $(bc)"
! has ' 2206$' && ok "a run parent stays in Backlog" || ko "#2206 moved: $(bc)"
! has ' 2207$' && ok "the frontier label with a frontier skip label stays in Backlog" || ko "#2207 moved: $(bc)"

o=$(run env -u SG_FRONTIER_LABEL)
has '^STATUS o 4 Todo 2201$' && ok "with no frontier label the floor is Todo, as before" || ko "#2201 not Todo without a frontier label: $(bc) $o"

rm -rf "$T"; [ "$fail" = 0 ] && echo "PASS" || { echo "FAILED"; exit 1; }
