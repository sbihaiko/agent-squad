#!/bin/bash
# Regression test for the mutual dispatch claim and live-run issue reading.
# Offline: stub gh. usage: bash test-dispatch-mutex.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR="$T/caps"
mkdir -p "$T/bin" "$T/st/auto" "$T/co/docs/squad/workflows" "$T/s" "$T/co/runs/live"
cp "$d/autobug.sh" "$d/autofrontier.sh" "$d/cluster.py" "$d/single.sh" "$d/startability.sh" "$d/ghcall.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/refusal-names.sh" "$d/issue_state.py" "$T/s/"
cp "$SG_SQUAD/docs/design/router.graph.json" "$T/co/docs/squad/workflows/default.graph.json"
printf 'caps:\n  runs: 2\n' > "$T/co/docs/squad/defaults.yaml"
printf 'export SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_STATE=%s SG_BASE=main SG_CHECKOUT=%s SG_FRONTIER_LABEL=ready-for-agent SG_BUG_LABEL=bug\n' "$T/st" "$T/co" > "$T/s/env.sh"
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"
cat > "$T/issues.json" <<'JSON'
[{"number":426,"title":"Mutual dispatcher claim","body":"gui/426","labels":["bug","ready-for-agent"]}]
JSON
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issues?state=open"*labels=bug*) printf '426\037bug,ready-for-agent\n' ;;
  *"issues?state=open"*) printf '426\037bug,ready-for-agent\n' ;;
  *issues/*) cat "$T/issues.json" | jq -c '.[0]' ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
: > "$T/st/active-runs.txt"

# A claim from the other dispatcher holds the issue too.
touch "$T/st/auto/disp-426"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG would dispatch"*) ko "a autobug took an autofrontier claim: '$r'";; *) ok "a autobug skips an autofrontier claim";; esac
rm -f "$T/st/auto/disp-426"
printf '%s\n' '[{"number":426,"title":"Mutual dispatcher claim","body":"gui/426","labels":["ready-for-agent"]}]' > "$T/issues.json"
rm -rf "$T/st/gh-cache"   # the open issues changed: case a's cached read (autobug's blocker gate) is stale
touch "$T/st/auto/bug-426"
r=$(AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1)
case "$r" in *"FRONTIER would dispatch"*) ko "b autofrontier took an autobug claim: '$r'";; *) ok "b autofrontier skips an autobug claim";; esac
rm -f "$T/st/auto/bug-426"

# A live child keeps its exact issue busy, whether its request says Issue #N or gui/N.
live() { printf '%s\n' "$1" > "$T/co/runs/live/record.jsonl"; printf '%s\n' "$T/co/runs/live" > "$T/st/active-runs.txt"; }
for text in 'Issue #426 is data' 'gui/426' 'Fix GitHub issue 426 in the repository' 'github issues 426, 427'; do
  live "{\"event\":\"spawn_accepted\",\"prompt\":\"$text\"}"
  r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
  case "$r" in *"AUTOBUG would dispatch"*) ko "c autobug missed live '$text': '$r'";; *) ok "c autobug reads live '$text'";; esac
  r=$(AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1)
  case "$r" in *"FRONTIER would dispatch"*) ko "c autofrontier missed live '$text': '$r'";; *) ok "c autofrontier reads live '$text'";; esac
done

# Issue #42 is not a claim on issue #426.
live '{"event":"spawn_accepted","prompt":"Issue #42 is data"}'
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[426]"*) ok "d autobug keeps #426 free after #42";; *) ko "d autobug treated #42 as #426: '$r'";; esac
r=$(AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1)
case "$r" in *"#426"*) ok "d autofrontier keeps #426 free after #42";; *) ko "d autofrontier treated #42 as #426: '$r'";; esac

rm -rf "$T"; exit $fail
