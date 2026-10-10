#!/bin/bash
# Regression test for root-cause clustering (cluster.py) and its two callers' dispatch plans (AB_DRY / AF_DRY), stubbed gh.
# usage: bash test-cluster.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
I='[{"number":1228,"title":"play-pad-only-home: A on Open a ROM never reaches the library","body":"step home.open-library","created_at":"2026-10-10T15:00:00Z"},
{"number":1233,"title":"play-pad-only-home: A on Open a ROM never reaches the library","body":"same","created_at":"2026-10-10T15:20:00Z"},
{"number":1300,"title":"play-pad-only-home: old batch","body":"x","created_at":"2026-10-09T01:00:00Z"},
{"number":1301,"title":"Core: injected key","body":"touches UI/Logic/TestHook/TestHookScreens.cs","created_at":"2026-10-01T00:00:00Z"},
{"number":1302,"title":"Docs: something","body":"see UI/Logic/TestHook/TestHookScreens.cs too","created_at":"2026-10-05T00:00:00Z"},
{"number":1303,"title":"Unrelated","body":"nothing","created_at":"2026-10-10T15:00:00Z"}]'
r=$(python3 "$d/cluster.py" <<<"$I")
case "$r" in *"1228 1233"*) ok "area + window: one batch's symptoms cluster";; *) ko "area: '$r'";; esac
echo "$r" | /usr/bin/grep -qx 1300 && ok "same area outside the window stays apart" || ko "window: '$r'"
echo "$r" | /usr/bin/grep -qx "1301 1302" && ok "shared file path clusters" || ko "files: '$r'"
echo "$r" | /usr/bin/grep -qx 1303 && ok "unrelated issue alone" || ko "alone: '$r'"
r=$(CL_MODE=files python3 "$d/cluster.py" <<<"$I"); echo "$r" | /usr/bin/grep -qx 1228 && ok "CL_MODE=files ignores the area rule" || ko "files mode: '$r'"
# autobug: clusters become children
mkdir -p "$T/bin" "$T/st/auto" "$T/co/docs/squad/workflows" "$T/s"; cp "$d/autobug.sh" "$d/autofrontier.sh" "$d/cluster.py" "$T/s/"; : > "$T/st/active-runs.txt"
echo '{"max_parallel": 10}' > "$T/co/docs/squad/workflows/dynamic.graph.json"
printf 'SG_REPO=x/y; SG_STATE=%s; SG_BASE=main; SG_CHECKOUT=%s; SG_FRONTIER_LABEL=ready-for-agent\n' "$T/st" "$T/co" > "$T/s/env.sh"
printf '%s' "$I" > "$T/issues.json"; printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*wave*) python3 -c 'import json;[print("wave:01\x1f"+str(i["number"])+"\x1fready-for-agent") for i in json.load(open("$T/issues.json"))]' ;;
  *"issues?state=open"*) python3 -c 'import json;[print(str(i["number"])+"\x1f"+"bug") for i in json.load(open("$T/issues.json"))]' ;;
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); python3 -c 'import json,sys;print(json.dumps([i for i in json.load(open("$T/issues.json")) if i["number"]==int(sys.argv[1])][0]))' "\$n" ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1); case "$r" in *"4 cluster(s)"*"[1228 1233]"*"[1301 1302]"*) ok "autobug: one child per cluster";; *) ko "autobug: '$r'";; esac
printf 'SG_REPO=x/y; SG_STATE=%s; SG_BASE=main; SG_CHECKOUT=%s; SG_FRONTIER_LABEL=ready-for-agent; SG_BUG_LABEL=nobug\n' "$T/st" "$T/co" > "$T/s/env.sh"
r=$(AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1); case "$r" in *"[1301 1302]"*) case "$r" in *"[1228 1233]"*) ko "frontier: area rule applied: '$r'";; *) ok "frontier: shared-file issues share one child, area ignored";; esac;; *) ko "frontier: '$r'";; esac
rm -rf "$T"; exit $fail
