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
# autobug: clusters become children. single.sh and forkonly.sh travel with the dispatchers: both run the pass under
# single.sh (#1514) and source forkonly.sh, so a sandbox without them cannot start a pass at all.
mkdir -p "$T/bin" "$T/st/auto" "$T/co/docs/squad/workflows" "$T/s"; cp "$d/autobug.sh" "$d/autofrontier.sh" "$d/cluster.py" "$d/single.sh" "$d/forkonly.sh" "$T/s/"; : > "$T/st/active-runs.txt"
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
# One fork-only slice that shares a file with another fork-only pick is ONE child / ONE branch, exactly as the base-branch
# picks above (#1521): gui/1497, gui/1498 and gui/1502 each landed their own branch on contrib/squad-goal/scripts/hub.py,
# and the three of them stopped the merge until a hand resolved them.
FK='[{"number":1500,"title":"Hub: cost panel","body":"contrib/squad-goal/scripts/hub.py in the spend header","labels":[{"name":"fork-only"}],"created_at":"2026-10-11T09:00:00Z"},
{"number":1501,"title":"Hub: run badge","body":"also contrib/squad-goal/scripts/hub.py, the badge","labels":[{"name":"fork-only"}],"created_at":"2026-10-11T09:01:00Z"},
{"number":1502,"title":"Hub: another panel","body":"nothing to do with the others","labels":[{"name":"fork-only"}],"created_at":"2026-10-11T09:02:00Z"}]'
printf '%s' "$FK" > "$T/issues.json"
r=$(AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1)
case "$r" in *"[1500 1501]"*) ok "frontier: fork-only issues naming one file share one child";; *) ko "fork-only cluster: '$r'";; esac
case "$r" in *"2 child(ren): [1500 1501] [1502]"*) ok "frontier: an unrelated fork-only pick keeps its own child";; *) ko "fork-only alone: '$r'";; esac
# The body carries the launch's own PANEL id, so mkreq.sh's slice_guard refuses a second child of this panel naming a file
# this one names (ADR-0274, subsumed by the wider in-flight rule of #1519): the overlap is caught before a run is spent.
mkdir -p "$T/home/.cache/squad-goal/req"; cp "$d/mkreq.sh" "$d/models.json" "$d/req-checklist.txt" "$d/req-checklist-fork.txt" "$T/s/" 2>/dev/null
printf 'PANEL: p1\nIMPORTANT: your first and only action is to call the spawn tool\nCHILD\ntouches scripts/hub.py\n' > "$T/b1.body"
printf 'PANEL: p1\nIMPORTANT: your first and only action is to call the spawn tool\nCHILD\nalso scripts/hub.py\n' > "$T/b2.body"
HOME="$T/home" bash "$T/s/mkreq.sh" c1 "$T/b1.body" >/dev/null 2>&1 && ok "mkreq: the first child of a panel is accepted" || ko "mkreq: the first child was refused"
HOME="$T/home" bash "$T/s/mkreq.sh" c2 "$T/b2.body" >"$T/mk.err" 2>&1 && ko "mkreq: a sibling naming the same file was launched anyway" || { /usr/bin/grep -q "same panel p1" "$T/mk.err" && ok "mkreq: a sibling naming the same file is refused (slice_guard)" || ko "mkreq: refused for another reason: $(cat $T/mk.err)"; }
rm -rf "$T"; exit $fail
