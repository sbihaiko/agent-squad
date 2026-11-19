#!/bin/bash
# Regression test for root-cause clustering (cluster.py) and its two callers' dispatch plans (AB_DRY / AF_DRY), stubbed gh.
# usage: bash test-cluster.sh [scripts dir]
export SG_MIN_FREE_GB=0   # the dispatchers pin their disk floor: the real volume must not stop a fixture pass (#792); a case that tests the stop sets its own
export SG_NIGHTLY_OFF=1   # the dispatch hold reads the wall clock: the nightly hour must not hold a fixture pass (#2397); a case that tests the hold sets its own hour
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# The free slots come from the machine-wide caps (squad/caps.py) of this checkout, with leases in the sandbox, never the real ones.
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR="$T/caps"
export SG_GH_AGGREGATE_WINDOW=0   # the frontier's open-issue list changes between passes below: no snapshot answers a later one
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
# A bare file name that names one file of the checkout is that file's path (#2014): two bugs on one file, one written with
# its folder and one without, are one cluster. A bare name of two files, or of none, adds no path.
R=$(mktemp -d); git -C "$R" init -q; mkdir -p "$R/contrib/x" "$R/a" "$R/b"; : > "$R/contrib/x/test-nightly.sh"; : > "$R/a/dup.sh"; : > "$R/b/dup.sh"
git -C "$R" add -A; J='[{"number":1401,"title":"t","body":"run `bash contrib/x/test-nightly.sh`","created_at":"2026-10-01T00:00:00Z"},
{"number":1402,"title":"u","body":"The test-nightly.sh log shows this line.","created_at":"2026-10-02T00:00:00Z"},
{"number":1403,"title":"v","body":"dup.sh is slow","created_at":"2026-10-03T00:00:00Z"},
{"number":1404,"title":"w","body":"see a/dup.sh","created_at":"2026-10-04T00:00:00Z"},
{"number":1405,"title":"x","body":"ghost.sh fails","created_at":"2026-10-05T00:00:00Z"},
{"number":1406,"title":"y","body":"ghost.sh fails too","created_at":"2026-10-06T00:00:00Z"}]'
r=$(SG_CHECKOUT="$R" python3 "$d/cluster.py" <<<"$J")
echo "$r" | /usr/bin/grep -qx "1401 1402" && ok "a bare name of one checkout file clusters with its full path" || ko "bare name: '$r'"
echo "$r" | /usr/bin/grep -qx 1403 && ok "a bare name of two checkout files adds no path" || ko "ambiguous bare name: '$r'"
echo "$r" | /usr/bin/grep -qx 1405 && ok "a bare name of no checkout file adds no path" || ko "unknown bare name: '$r'"
rm -rf "$R"
# autobug: clusters become children. single.sh and startability.sh travel with the dispatchers: both run the pass under
# single.sh (#1514) and source startability.sh (which sources ghcall.sh beside it), so a sandbox without them cannot start
# a pass at all. envelope-spend.sh is what the request builder reads its child's spend from; address.sh is the seam.
mkdir -p "$T/bin" "$T/st/auto" "$T/co/docs/squad/workflows" "$T/s"; cp "$d/autobug.sh" "$d/loopbug.sh" "$d/basered.sh" "$d/autofrontier.sh" "$d/columnlimit.sh" "$d/cluster.py" "$d/single.sh" "$d/startability.sh" "$d/skiplabels.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/ghread.py" "$d/ghthrottle.py" "$d/address.sh" "$d/envelope-spend.sh" "$d/refusal-names.sh" "$d/workbranches.sh" "$d/issue_ledger.py" "$T/s/"; : > "$T/st/active-runs.txt"
printf 'caps:\n  runs: 10\n' > "$T/co/docs/squad/defaults.yaml"   # the project lowers the machine-wide caps.runs
printf 'export SG_REPO=x/y SG_STATE=%s SG_BASE=main SG_CHECKOUT=%s SG_FRONTIER_LABEL=ready-for-agent\n' "$T/st" "$T/co" > "$T/s/env.sh"   # exported, as the real env.sh does: a pass re-execs itself under single.sh, and an unexported SG_STATE is lost there
printf '%s' "$I" > "$T/issues.json"; printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"
cat > "$T/s/issue_state.py" <<PY
import json, sys   # the seam's door, stubbed (#550): every fixture issue's card sits in Todo, the owner's go signal
if sys.argv[1:2] not in ([], ["--go"]): sys.exit(1)   # the plain read and the go-list read (#800) both answer
for i in json.load(open("$T/issues.json")): print(f"{i['number']}\\tTodo")
PY
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",createdAt:.created_at,labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:(.labels//[{name:"ready-for-agent"}])},blockedBy:{nodes:[]},comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;   # the frontier's one list read (#383)
  *"issues?state=open"*wave*) jq -c '[.[]|{number,title:"t",labels:[{name:"wave:01"},{name:"ready-for-agent"}]}]' "$T/issues.json" ;;
  *"issues?state=open"*) jq -c '[.[]|{number,title:"t",labels:[{name:"bug"},{name:"ready-for-agent"}]}]' "$T/issues.json" ;;   # the candidate list, as GitHub answers it (JSON; the door applies -q)
  *issues/*/comments*) echo "[]" ;;   # no comment on any fixture issue
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
# Each carries SG_FRONTIER_LABEL: the frontier takes only an issue that wears it (#485).
FK='[{"number":1500,"title":"Hub: cost panel","body":"contrib/squad-goal/scripts/hub.py in the spend header","labels":[{"name":"squad-only"},{"name":"ready-for-agent"}],"created_at":"2026-10-11T09:00:00Z"},
{"number":1501,"title":"Hub: run badge","body":"also contrib/squad-goal/scripts/hub.py, the badge","labels":[{"name":"squad-only"},{"name":"ready-for-agent"}],"created_at":"2026-10-11T09:01:00Z"},
{"number":1502,"title":"Hub: another panel","body":"nothing to do with the others","labels":[{"name":"squad-only"},{"name":"ready-for-agent"}],"created_at":"2026-10-11T09:02:00Z"}]'
printf '%s' "$FK" > "$T/issues.json"
r=$(AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1)
case "$r" in *"[1500 1501]"*) ok "frontier: fork-only issues naming one file share one child";; *) ko "fork-only cluster: '$r'";; esac
case "$r" in *"2 child(ren): [1500 1501] [1502]"*) ok "frontier: an unrelated fork-only pick keeps its own child";; *) ko "fork-only alone: '$r'";; esac
# The machine-wide ceiling (#21): a project that lowers caps.runs to 1 dispatches one run, which takes as many children as its
# envelope allows (#728: the free slots bound the runs, the envelope the children of each); a ceiling filled by another
# repository's live runs dispatches none and fails nothing; a project that raises the pair stops the pass by name. The one
# child of that run holds the whole shared-file slice: the envelope bounds the CHILDREN, cluster.py decides them, and a
# ticket that names a file a taken ticket already names costs no second child (#1521). Bounding the tickets instead cut
# the pair in two, which is the conflict the clustering exists to stop.
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
r=$(AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1); case "$r" in *"as 2 child(ren): [1500 1501] [1502] (1 free slots, room for"*"in 1 run(s)"*) ok "caps: a project lowering caps.runs to 1 dispatches one run, its shared-file slice whole";; *) ko "caps.runs 1: '$r'";; esac
printf 'caps:\n  runs: 2\n' > "$T/co/docs/squad/defaults.yaml"; sleep 300 & held=$!
(cd "$SG_SQUAD" && python3 -c 'import sys; from squad import caps; c = caps.ceiling(); [c.try_take("runs", f"other-repo-{i}", pid=int(sys.argv[1])) for i in range(2)]' "$held")
r=$(AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1); rc=$?; case "$rc:$r" in 0:*"FRONTIER idle:"*"no slot"*) case "$r" in *"would dispatch"*) ko "caps full: a full ceiling dispatched: '$r'";; *) ok "caps: a ceiling full of another repository's runs dispatches nothing, fails nothing, and names the reason (#733)";; esac;; *) ko "caps full: rc=$rc '$r'";; esac   # #733: the pass says why it started nothing
kill "$held"; wait "$held" 2>/dev/null
r=$(AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1); case "$r" in *"(2 free slots, room for"*) ok "caps: the leases of a dead holder are reclaimed in one pass";; *) ko "caps reclaim: '$r'";; esac
printf 'caps:\n  runs: 11\n' > "$T/co/docs/squad/defaults.yaml"
r=$(AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1); rc=$?; case "$rc:$r" in 1:*"caps.runs"*"may only lower"*) ok "caps: a project raising caps.runs stops the pass by name";; *) ko "caps raise: rc=$rc '$r'";; esac
printf 'caps:\n  runs: 10\n' > "$T/co/docs/squad/defaults.yaml"
# The body carries the launch's own PANEL id, so mkreq.sh's slice_guard refuses a second child of this panel naming a file
# this one names (ADR-0274, subsumed by the wider in-flight rule of #1519): the overlap is caught before a run is spent.
mkdir -p "$T/home/.cache/squad-goal/req"; cp "$d/mkreq.sh" "$d/models.json" "$d/req-checklist.txt" "$T/s/"   # address.sh already staged above: mkreq.sh resolves its state directory through the seam
printf 'PANEL: p1\nIMPORTANT: your first and only action is to call the spawn tool\nCHILD\ntouches scripts/hub.py\n' > "$T/b1.body"
printf 'PANEL: p1\nIMPORTANT: your first and only action is to call the spawn tool\nCHILD\nalso scripts/hub.py\n' > "$T/b2.body"
HOME="$T/home" bash "$T/s/mkreq.sh" c1 "$T/b1.body" >/dev/null 2>&1 && ok "mkreq: the first child of a panel is accepted" || ko "mkreq: the first child was refused"
HOME="$T/home" bash "$T/s/mkreq.sh" c2 "$T/b2.body" >"$T/mk.err" 2>&1 && ko "mkreq: a sibling naming the same file was launched anyway" || { /usr/bin/grep -q "same panel p1" "$T/mk.err" && ok "mkreq: a sibling naming the same file is refused (slice_guard)" || ko "mkreq: refused for another reason: $(cat $T/mk.err)"; }
# ADR-0158: a files-cluster is capped, so one batch over one large file becomes several runs instead of one run that dies
# `out_of_budget`. The cap splits the cluster in ascending number order and drops no ticket; the default is two.
J='[{"number":1,"title":"a","created_at":"2026-10-31T00:00:00Z","body":"touches scripts/hub.py"},
    {"number":2,"title":"b","created_at":"2026-10-31T00:00:00Z","body":"touches scripts/hub.py"},
    {"number":3,"title":"c","created_at":"2026-10-31T00:00:00Z","body":"touches scripts/hub.py"},
    {"number":4,"title":"d","created_at":"2026-10-31T00:00:00Z","body":"touches scripts/hub.py"},
    {"number":5,"title":"e","created_at":"2026-10-31T00:00:00Z","body":"touches scripts/hub.py"}]'
r=$(printf '%s' "$J" | CL_MODE=files python3 "$d/cluster.py" | tr '\n' '|')
[ "$r" = "1 2|3 4|5|" ] && ok "cluster: the default cap splits a five-ticket file batch into runs of two" || ko "default cap: '$r'"
r=$(printf '%s' "$J" | CL_MODE=files CL_MAX=3 python3 "$d/cluster.py" | tr '\n' '|')
[ "$r" = "1 2 3|4 5|" ] && ok "cluster: a named cap of three splits the same batch in threes" || ko "named cap: '$r'"
r=$(printf '%s' "$J" | CL_MODE=files CL_MAX=0 python3 "$d/cluster.py" | tr '\n' '|')
[ "$r" = "1 2|3 4|5|" ] && ok "cluster: a cap of zero falls back to the default rather than dropping tickets" || ko "zero cap: '$r'"

rm -rf "$T"; exit $fail
