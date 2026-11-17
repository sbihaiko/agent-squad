#!/bin/bash
# Regression test for autobug ranking (bug first, then debt) and autofrontier skipping debt (Refs #1494).
# Offline: stub gh. usage: bash test-autobug.sh [scripts dir]
export SG_MIN_FREE_GB=0   # the dispatchers pin their disk floor: the real volume must not stop a fixture pass (#792); a case that tests the stop sets its own
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# The free slots come from the machine-wide caps (squad/caps.py) of this checkout, with leases in the sandbox, never the real ones.
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR="$T/caps"
G=/usr/bin/grep
mkdir -p "$T/bin" "$T/st/auto" "$T/co/docs/squad/workflows" "$T/s"
cp "$d/autobug.sh" "$d/loopbug.sh" "$d/basered.sh" "$d/autofrontier.sh" "$d/cluster.py" "$d/single.sh" "$d/startability.sh" "$d/skiplabels.sh" "$d/ghcall.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/refusal-names.sh" "$d/workbranches.sh" "$d/address.sh" "$d/issue_ledger.py" "$T/s/"
# The fixture reaches no network (#556): `sg_work_issues` runs `git ls-remote` on the repository's https remote,
# and an unset remote took every pass to github.com -- 0.7 s per pass at best, a stall at worst. git refuses the
# transport itself, so the read answers "no branch" at once, the same fact a 404 gives, and no pass leaves the machine.
export GIT_ALLOW_PROTOCOL=file
unset SG_BOARD_VOCAB SG_PROFILE SG_BOARD_CACHE   # fixture runs must not borrow an owner profile or live hub cache
export SG_GH_AGGREGATE_WINDOW=0   # the open-issue list changes between cases: no snapshot answers a later one
: > "$T/st/active-runs.txt"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"   # the project lowers the machine-wide caps.runs
# exported, as the real env.sh does: the stub mkreq.sh below is a child shell and must still see SG_STATE
printf 'export SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_STATE=%s SG_BASE=main SG_CHECKOUT=%s SG_FRONTIER_LABEL=ready-for-agent SG_BUG_LABEL=bug\n. "$(dirname "$0")/address.sh"   # the queued comments name their issue through the one seam, as the real env.sh does\n' "$T/st" "$T/co" > "$T/s/env.sh"
# 1228 is a bug; 1400 is debt (and still carries ready-for-agent, the old filer). 1401 is a frontier-only slice.
cat > "$T/issues.json" <<'JSON'
[
  {"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]},
  {"number":1400,"title":"Debt: retry has no backoff","body":"<!-- debt-key:abcd -->","created_at":"2026-10-10T16:00:00Z","labels":["debt","ready-for-agent"]},
  {"number":1401,"title":"Slice: a frontier ticket","body":"UI/Logic/Foo.cs","created_at":"2026-10-10T17:00:00Z","labels":["ready-for-agent"]}
]
JSON
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"
printf 'print("-")\n' > "$T/s/fundroom.py"
# The seam's door, stubbed at the script's own name (#550): the cards are the lines of cards.tsv, one `<issue>\t<state>`
# each, and a missing cards.tsv is a board that does not answer. The kinds are not read here (the label selects).
cat > "$T/s/issue_state.py" <<PY
import sys
if sys.argv[1:2] not in ([], ["--go"]): sys.exit(1)   # the plain read and the go-list read (#800) both answer
try: sys.stdout.write(open("$T/cards.tsv").read())
except OSError: print("no board", file=sys.stderr); sys.exit(1)
PY
cp "$T/s/issue_state.py" "$T/issue_state-stub.py"
todo() { local n; : > "$T/cards.tsv"; for n in "$@"; do printf '%s\tTodo\n' "$n" >> "$T/cards.tsv"; done; }   # <issue>...: their cards sit in Todo
todo 1228 1229 1400 1401 60 61 62 70 71 72 73   # the graph names no funding bound unless a case says so
printf '#!/bin/bash\nexit 0\n' > "$T/s/board.sh"; chmod +x "$T/s/board.sh"; export BOARD="$T/s/board.sh"   # the card door lands unless a case refuses it
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;   # the frontier's one list read (#383)
  *"issues?state=open"*labels=bug*) jq -c '[.[]|select((.labels // [])|index("bug"))|.labels=[(.labels//[])[]|{name:.}]]' "$T/issues.json" ;;   # the door applies -q itself, so the stub answers gh's JSON, labels as objects (#847)
  *"issues?state=open"*labels=debt*) jq -c '[.[]|select((.labels // [])|index("debt"))|.labels=[(.labels//[])[]|{name:.}]]' "$T/issues.json" ;;
  *"issues?state=open"*wave*|*labels=ready-for-agent*) jq -c '[.[]|select((.labels // [])|index("ready-for-agent"))|.labels=[(.labels//[])[]|{name:.}]]' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;   # the door's one-issue query, as GraphQL (#847)
  *issues/*comments*) echo "[]" ;;   # a comments list is a list: the issue stub below is for the issue itself
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); jq -c --argjson n "\$n" '.[]|select(.number==\$n)' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then jq -c '.state="closed"'; else cat; fi } ;;
  *) echo "[]" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"

# 1) an open bug and an open debt, one free slot: bug first, debt not dispatched
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[1228]"*) case "$r" in *"[1400]"*) ko "1 bug+debt one slot still dispatched debt: '$r'";; *) ok "1 bug+debt one slot: bug first, debt held";; esac;; *) ko "1 bug not dispatched: '$r'";; esac

# 2) two free slots and a startable bug: still no debt (debt only when no bug is startable)
printf 'caps:\n  runs: 2\n' > "$T/co/docs/squad/defaults.yaml"   # the project lowers the machine-wide caps.runs
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[1400]"*) ko "2 extra slot dispatched debt while a bug is startable: '$r'";; *"[1228]"*) ok "2 extra slot: bug only, debt held";; *) ko "2 bug not dispatched: '$r'";; esac

# 2b) (#479) the bug lane asks stack.sh, with the issue's own aggregate entry, and dispatches only on READY: a bug whose
# blocker is open is held, its WAIT logged once, and it is dispatched once the blocker closes.
cat > "$T/s/stack.sh" <<'S'
#!/bin/bash
jq -e --argjson n "$1" '.number == $n' "$SG_STACK_ISSUE" >/dev/null 2>&1 || { echo "WAIT no aggregate entry for #$1"; exit 0; }
[ -e "$SG_STATE/blocker-open" ] && { echo "WAIT blocker #9 is open: its work is not in main yet"; exit 0; }
echo READY
S
chmod +x "$T/s/stack.sh"; touch "$T/st/blocker-open"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ko "2b a bug whose blocker is open was dispatched: '$r'";; *) ok "2b a bug whose blocker is open is not dispatched";; esac
case "$r" in *"AUTOBUG skip #1228: WAIT blocker #9 is open"*) ok "2b the wait is said, with its reason";; *) ko "2b the wait is silent: '$r'";; esac
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG skip #1228"*) ko "2b the same wait is logged again on the next pass: '$r'";; *) ok "2b the same wait is logged once";; esac
rm -f "$T/st/blocker-open"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ok "2b the bug is dispatched once its blocker closed (stack.sh read its aggregate entry)";; *) ko "2b the bug stayed held after its blocker closed: '$r'";; esac
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"

# 2b2 (#1620, ADR-0142 rule 6): a bug that is a ticket of a spec is not dispatched while the spec's card sits in Backlog,
# whatever the bug's own card reads. The board's automation puts a linked sub-issue in Todo by itself, so the ticket's own
# column proves nothing about the spec's, and the owner starts a spec before its tickets. The wait names the parent spec.
cp "$d/stack.sh" "$d/deadlock.py" "$T/s/"
python3 - "$T/issues.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d.append({"number": 1250, "title": "Core: a crash under SPEC 700", "body": "step a.py",
          "created_at": "2026-09-02T15:00:00Z", "labels": ["bug", "ready-for-agent"], "parent": 700})
json.dump(d, open(p, "w"))
PY
printf '700\tBacklog\n1250\tTodo\n' >> "$T/cards.tsv"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG skip #1250: WAIT ticket of spec #700"*) ok "2b2 the wait names the parent spec in Backlog";; *) ko "2b2 no wait naming the parent spec: '$r'";; esac
case "$r" in *"[1250]"*) ko "2b2 a bug whose spec card sits in Backlog was dispatched: '$r'";; *) ok "2b2 a bug whose spec card sits in Backlog is not dispatched";; esac
python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); json.dump([i for i in d if i["number"]!=1250], open(p,"w"))' "$T/issues.json"
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"

# 2c) (#480) a run funds only so many children: two bugs, two free slots, and a graph that funds one child -> one child.
python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d.append({"number":1229,"title":"Core: other crash","body":"step zz/q.py","created_at":"2026-09-01T15:30:00Z","labels":["bug","ready-for-agent"]}); json.dump(d, open(p,"w"))' "$T/issues.json"
printf 'print(1)\n' > "$T/s/fundroom.py"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"would dispatch 1 cluster(s)"*) ok "2c the pass asks for no more children than one run funds";; *) ko "2c the pass asked past the funded room: '$r'";; esac
printf 'print("-")\n' > "$T/s/fundroom.py"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"would dispatch 2 cluster(s)"*) ok "2c with no funding bound the free slots decide";; *) ko "2c no funding bound, yet not two children: '$r'";; esac
python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); json.dump([i for i in d if i["number"]!=1229], open(p,"w"))' "$T/issues.json"

# 3) no startable bug: debt is dispatched
python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); json.dump([i for i in d if i["number"]!=1228], open(p,"w"))' "$T/issues.json"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"   # the project lowers the machine-wide caps.runs
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[1400]"*) ok "3 no startable bug: debt dispatched";; *) ko "3 debt ignored when no bug: '$r'";; esac

# 3b) (#1529) a request mkreq.sh refuses must not consume the dispatch. The marker is what makes the next pass skip the
# issue, and it is written BEFORE the request is built (#1514, the launch window), so a refusal used to mark the issue done
# with nothing launched and no later pass retried it. mkreq.sh refuses here (stub, exit 1): the marker has to go back.
mkdir -p "$T/st/req" "$T/st/cq" "$T/st/locks"
printf '#!/bin/bash\necho "refusing: request x names scripts/a.py, which open PR #7 already changes." >&2; exit 1\n' > "$T/s/mkreq.sh"
printf 'import os\nopen(os.path.join("%s", "launched"), "w").close()\n' "$T" > "$T/s/launch.py"
chmod +x "$T/s/mkreq.sh" "$T/s/launch.py"
r=$(bash "$T/s/autobug.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "3b a refused dispatch exits 0" || ko "3b refused dispatch exited $rc: '$r'"
[ -e "$T/st/auto/bug-1400" ] && ko "3b a refused dispatch left auto/bug-1400 behind: the issue is never retried" || ok "3b a refused dispatch frees the marker it wrote"
[ -e "$T/st/auto/bug-1228" ] && ko "3b a refused dispatch left auto/bug-1228 behind" || ok "3b no stale marker for another issue either"
[ -e "$T/launched" ] && ko "3b a refused dispatch launched a run" || ok "3b a refused dispatch launches no run"

# 3c) (#664) with no profile the request must not prescribe a check that cannot run. This suite clears every SG_*
# variable, so SG_PROFILE is unset: the body says the check is unavailable and names why.
rm -rf "$T/st/req"; mkdir -p "$T/st/req" "$T/st/cq" "$T/st/locks"
printf '#!/bin/bash\nmkdir -p "$SG_STATE/req"; echo "$SG_STATE/req/$1.txt"; cat "$2" > "$SG_STATE/req/$1.txt"\n' > "$T/s/mkreq.sh"
chmod +x "$T/s/mkreq.sh"
r=$(bash "$T/s/autobug.sh" 2>&1)
body=$(cat "$T/st/req/"*.txt 2>/dev/null)
case "$body" in *'"bash contrib/squad-goal/scripts/prepush.sh --local"'*) ko "3c no profile, yet the request prescribes prepush.sh --local";; *) ok "3c no profile: the request does not prescribe prepush.sh --local";; esac
case "$body" in *prepush*) ko "3c the request names a prepush check with no profile";; *) ok "3c the request names no prepush check with no profile";; esac

# 3d) (#740) the prepush line names the profile's command, or none. A child of wz-zesty-agent was told to run
# contrib/squad-goal/scripts/prepush.sh, which that repository does not hold, and its run ended failed with correct work.
# The profile resolves here (a stub profile file), and env.sh's sg_gate_cmds resolves what the profile left unset.
mkdir -p "$T/profiles"; : > "$T/profiles/stub.sh"; cp -R "$T/st/auto" "$T/auto.keep"
abody() { rm -rf "$T/st/req" "$T/st/auto"; cp -R "$T/auto.keep" "$T/st/auto"; rm -f "$T/st/auto/bug-"*; mkdir -p "$T/st/req"
  ( export SG_PROFILE=stub SG_PROFILES_DIR="$T/profiles" "$@"; bash "$T/s/autobug.sh" ) >/dev/null 2>&1; cat "$T/st/req/"*.txt 2>/dev/null; }
printf '. "$(dirname "$0")/address.sh"; type sg_gate_cmds >/dev/null 2>&1 && sg_gate_cmds   # env.sh resolves the gate commands after the profile (#740)\n' >> "$T/s/env.sh"
# 3d-1 (#1761) a checkout that holds prepush.sh still gets no prepush run: the fast gate is the child's one proof
mkdir -p "$T/co/contrib/squad-goal/scripts"; touch "$T/co/contrib/squad-goal/scripts/prepush.sh"
body=$(abody SG_GATE_740=1)
case "$body" in *prepush*) ko "3d the child request still names prepush";; *) ok "3d the child request names no prepush run";; esac
# 3d-2 a checkout without prepush.sh and a profile that defines none: no script is named either
rm -rf "$T/co/contrib"
body=$(abody SG_GATE_740=1)
[ -n "$body" ] || ko "3d no request was built"
case "$body" in *prepush*) ko "3d a checkout without prepush.sh names prepush";; *) ok "3d a checkout without prepush.sh names no prepush";; esac
# 3d-3 (#1761) a profile that defines its prepush command does not put it in the request either: the fast gate is the one proof
body=$(abody 'SG_PREPUSH_CMD=make prepush')
case "$body" in *prepush*) ko "3d the request names the profile's prepush command";; *) ok "3d the request names no profile prepush command";; esac
# 3e (#1198) the fast gate is the child's last step, after the final edit and commit, so the tree it proved is the tree the
# child hands in. A child that cannot gate last says so: the wave entry gate then runs its own battery. No gate, no script.
body=$(abody SG_FAST_GATE_CMD=mygate)
case "$body" in *'Your last step, after your final edit and commit, is the entry gate: run "mygate"'*) ok "3e the request makes the fast gate the child's last step";; *) ko "3e the brief does not make the fast gate the last step: '$body'";; esac
case "$body" in *"make no edit and no commit"*) ok "3e the request forbids an edit after the gate";; *) ko "3e the request does not forbid an edit after the gate: '$body'";; esac
case "$body" in *"cannot make it your last step"*"say so"*"own battery"*) ok "3e a child that cannot gate last says so, and the entry gate falls back to its own battery";; *) ko "3e no fallback line: '$body'";; esac
# 3f (#1198) a red gate is fixed and the gate is re-run, as many times as it takes; the no-edit rule binds only after a
# pass. Before this, a run read "after it, make no edit" as "do not repair a red gate" and ended dead.
case "$body" in *'If the gate fails, fix what it names and run the gate again'*) ok "3f a red gate is repaired and the gate is re-run";; *) ko "3f a red gate is not repaired and re-run: '$body'";; esac
case "$body" in *'Once the gate passes, make no edit and no commit'*) ok "3f the brief keeps the no-edit-after-a-pass rule";; *) ko "3f a pass still allows an edit: '$body'";; esac
body=$(abody SG_FAST_GATE_CMD=)
case "$body" in *mygate*|*fast-gate.sh*) ko "3e a repository with no fast gate still names a script";; *) ok "3e no fast gate configured names no script";; esac
case "$body" in *"No fast gate is configured"*"not a failure"*) ok "3e the request says no fast gate is configured and that it is not a failure";; *) ko "3e no no-fast-gate line: '$body'";; esac
rm -rf "$T/st/auto"; mv "$T/auto.keep" "$T/st/auto"

# 4) autofrontier never dispatches a debt issue (1400 has ready-for-agent + debt; 1401 is a real slice)
r=$(AF_DRY=1 bash "$T/s/autofrontier.sh" 2>&1)
case "$r" in *1400*) ko "4 autofrontier dispatched debt #1400: '$r'";; *1401*) ok "4 autofrontier skips debt, keeps the slice";; *) ko "4 autofrontier dispatched nothing: '$r'";; esac

# 5) the #1493 hunt request lists open debt issues as its first source
if [ -f "$d/autohunt.sh" ]; then
  cp "$d/autohunt.sh" "$T/s/"
  printf '#!/bin/bash\n[ -n "${AB_DRY:-}" ] && exit 0\n' > "$T/s/autobug.sh"
  printf '#!/bin/bash\n[ -n "${AF_DRY:-}" ] && exit 0\n' > "$T/s/autofrontier.sh"
  printf '#!/bin/bash\nmkdir -p "$SG_STATE/req"; echo "$SG_STATE/req/$1.txt"; cat "$2" > "$SG_STATE/req/$1.txt"\n' > "$T/s/mkreq.sh"
  chmod +x "$T/s/autobug.sh" "$T/s/autofrontier.sh" "$T/s/mkreq.sh" "$T/s/autohunt.sh"
  : > "$T/s/launch.py"
  mkdir -p "$T/st/req" "$T/st/cq" "$T/st/locks"
  export SG_AUTO_HUNT=yes SG_HUNT_IDLE_MINUTES=0 SG_HUNT_COOLDOWN_MINUTES=0 SG_HUNT_PER_DAY=6
  echo $(( $(date +%s) - 3600 )) > "$T/st/auto/hunt-activity"
  r=$(AH_DRY=1 bash "$T/s/autohunt.sh" 2>&1)
  body=$(cat "$T/st/req/"*.body "$T/st/req/"*.txt 2>/dev/null)
  case "$r" in *"HUNT started"*) ok "5 hunt starts when no startable bug";; *) ko "5 hunt did not start: '$r'";; esac
  printf '%s' "$body" | $G -qi "open debt" && printf '%s' "$body" | $G -q "1400" \
    && ok "5 hunt request lists open debt issues as its first source" \
    || ko "5 hunt request lacks open debt as first source"
else
  ko "5 autohunt.sh missing (hunt request cannot list open debt)"
fi

# 6) (#1855) a refused gh api is a refusal, never a candidate list. On 2026-10-12 the account was rate-limited on
# 2026-10-12 05:12:10 UTC, gh wrote the 403 body to stdout and exited 1, `2>/dev/null` hid the one line that said so, the
# body became the whole candidate list, and `pick+=($c)` (unquoted) then split it on whitespace: every word of the
# message became an "issue number" and a `bug-<word>` marker was written for each of the ~50 tokens. Three children were
# spawned on that text. The fixture keeps the exact shape of that body: many words, digits inside them, one long line.
cp "$d/autobug.sh" "$d/loopbug.sh" "$T/s/"   # test 5 stubbed it out; put the real pass back
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto"
cat > "$T/refusal.json" <<'JSON'
{"message":"API rate limit exceeded for user ID 122891372. If you reach out to GitHub Support for help, please include the request ID 06F8:34CAED:22CD6B6:3FC4397:6ACC6C2A and timestamp 2026-10-12 05:12:10 UTC. For more on scraping GitHub and how it may affect your rights, please review our Terms of Service (https://docs.github.com/en/site-policy/github-terms/github-terms-of-service)","documentation_url":"https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api#rate-limiting","status":"403"}
JSON
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues?state=open"*) cat "$T/refusal.json"; exit 1 ;;                 # the rate limit: the error body on stdout, exit 1
  *issues/*) echo '{}' ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf 'import os, sys\nif os.environ.get("AB_DRY"): sys.exit(0)\nopen(os.path.join("%s", "launched"), "w").close()\n' "$T" > "$T/s/launch.py"
printf '#!/bin/bash\ntouch %s/req-built\n' "$T" > "$T/s/mkreq.sh"; chmod +x "$T/s/mkreq.sh" "$T/s/launch.py"
rm -f "$T/launched"   # the launch stub of the earlier cases (now a real run) left its marker
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>"$T/err"); rc=$?
[ "$rc" = 0 ] && ok "6 a refused gh api exits 0" || ko "6 a refused gh api exited $rc: '$r'"
$G -q "AUTOBUG would dispatch" <<<"$r" && ko "6 a refused gh api dispatched candidates: '$r'" || ok "6 a refused gh api dispatches nothing"
m=($(find "$T/st/auto" -name 'bug-*' 2>/dev/null)); [ ${#m[@]} -eq 0 ] && ok "6 a refused gh api writes no bug-* marker" || ko "6 a refused gh api wrote ${#m[@]} marker(s), e.g. ${m[0]##*/}: words of the error body read as issue numbers"
[ -e "$T/req-built" ] && ko "6 a refused selection built a request" || ok "6 a refused selection builds no request"
[ -e "$T/launched" ] && ko "6 a refused selection launched a run" || ok "6 a refused selection launches no run"
# a non-zero gh whose stdout is not a body at all (empty): same refusal, no candidate
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in *pulls?state=open*) echo "" ;; *"issues?state=open"*) exit 1 ;; *issues/*) echo '{}' ;; *) echo "" ;; esac
J
chmod +x "$T/bin/gh"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>/dev/null)
$G -q "AUTOBUG would dispatch" <<<"$r" && ko "6b an empty refused body dispatched: '$r'" || ok "6b a refused gh api with an empty stdout dispatches nothing"

# 6c) a gh api that answers 0 but whose line is not an issue number: dropped and logged, never a candidate. One line of
# words is still the shape #1855 was about -- the reader must not fall back to word-splitting it.
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*) printf '%s\n' 'API rate limit exceeded for user ID 122891372' ;;
  *) echo '{}' ;;
esac
J
chmod +x "$T/bin/gh"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>"$T/err2")
$G -q "AUTOBUG would dispatch" <<<"$r" && ko "6c words from a zero-exit line were dispatched: '$r'" || ok "6c words from a zero-exit line dispatch nothing"
$G -q "candidate list is not readable" "$T/err2" && ok "6c the unreadable list is named (a body that is not JSON is no answer)" || ko "6c the dropped line was not logged: '$(cat "$T/err2")'"
m=($(find "$T/st/auto" -name 'bug-*' 2>/dev/null)); [ ${#m[@]} -eq 0 ] && ok "6c no marker for a word" || ko "6c a word became a marker: ${m[0]##*/}"

# 7) (#1858) an issue the loop already marked not startable is not dispatched again. The health pass writes
# `SQUAD-NOT-STARTABLE:` on an issue whose runs keep ending failed with the same reason; every dispatch path reads that
# line first, so the loop cannot hand itself the same input twice. The marker is read only after the cheap filters, and a
# refused comments read is not a marker (no answer at all), so a throttle never turns into a silent skip.
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"   # the project lowers the machine-wide caps.runs
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
cp "$d/autobug.sh" "$d/loopbug.sh" "$T/s/"
printf 'import os, sys\nif os.environ.get("AB_DRY"): sys.exit(0)\nopen(os.path.join("%s", "launched"), "w").close()\n' "$T" > "$T/s/launch.py"; chmod +x "$T/s/launch.py"
held() {   # $1: the body of the issue's comments, one line each
  local cj; cj=$(if [ -n "$1" ]; then jq -Rs '[{body:.}]' <<<"$1"; else echo '[]'; fi)   # gh's list shape (#847)
  cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues?state=open"*labels=bug*) jq '[.[]|{number,title,body,state:"open",labels:[(.labels//[])[]|{name:(if type=="string" then . else .name end)}]}]' "$T/issues.json" ;;
  *"issues?state=open"*) echo "[]" ;;
  *issues/*/comments*) echo '$cj' ;;
  *issues/*) printf '{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z"}\n' ;;
  *) echo "" ;;
esac
J
  chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
}
held 'SQUAD-NOT-STARTABLE: 3 run(s) ended failed with the same reason `scope`; a person is needed.\n'
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
$G -q "AUTOBUG would dispatch" <<<"$r" && ko "7 a held issue was dispatched anyway: '$r'" || ok "7 a held issue is not dispatched"
# ... and the identical fixture with no marker does dispatch, so the assertion above is about the marker and not the fixture
held ''
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
$G -q "AUTOBUG would dispatch.*\[1228\]" <<<"$r" && ok "7 the same candidate dispatches once the marker is gone" || ko "7 the fixture dispatches nothing even unmarked: '$r'"
# 7b) (#1297) a frozen:not-startable label holds an issue only while its marker holds: beside a marker another build wrote, the
# issue is a candidate; beside the current build's marker it is not. The label is never read alone past the marker's build.
git -C "$T/co" init -q 2>/dev/null; git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base 2>/dev/null
cur=$(git -C "$T/co" rev-parse --short=12 HEAD)
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent","frozen:not-startable"]}]
JSON
held 'SQUAD-NOT-STARTABLE: build=000000000000 3 run(s) ended failed.'
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
$G -q "AUTOBUG would dispatch.*\[1228\]" <<<"$r" && ok "7b a label beside another build's marker dispatches the bug" || ko "7b a label beside another build's marker held the bug: '$r'"
held "SQUAD-NOT-STARTABLE: build=$cur 3 run(s) ended failed."
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
$G -q "AUTOBUG would dispatch" <<<"$r" && ko "7b a label beside the current build's marker dispatched: '$r'" || ok "7b a label beside the current build's marker still holds the bug"
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
held ''   # back to the unmarked issue the cases after 7b read

# 8) (#145) a write the loop cannot prove is a defect the loop files. The card move at the end of this pass threw its
# output away, so a refused write looked like a moved card. The pass that hit the refusal asks the filer itself, in that
# same pass: ONE naming helper builds the refusal's name and fingerprint (the title and the cycle-log line carry both, so
# a refusal named by hand would carry a fingerprint the log does not), the body carries the evidence inside one fence,
# and the issue arrives through the filer's own contract -- the [SQUAD] prefix, the "Filed by" line, the bug kind and the
# startable label. No process, no queue reader, no timer: the refused write itself asks.
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$d/loopbug.sh" "$d/autohunt.sh" "$T/s/"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> %s/board.calls\necho "#1228: GitHub refused the card write (HTTP 403)" >&2\nexit 1\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho %s/req.json\n' "$T" > "$T/s/mkreq.sh"
printf 'import os\nopen(os.path.join("%s", "launched"), "w").close()\n' "$T" > "$T/s/launch.py"
chmod +x "$T/s/board.sh" "$T/s/mkreq.sh" "$T/s/launch.py"
: > "$T/filed"; : > "$T/st/cycle.log"
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues?state=open"*labels=bug*) echo '[{"number":1228,"title":"Core: null deref","body":"step a.py","state":"open","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}]' ;;
  *"issues?state=open"*) echo "[]" ;;
  *issues/*/comments*) echo "[]" ;;
  *"--method POST"*) printf '%s\n' "\$@" >> "$T/filed"
    for a in "\$@"; do case "\$a" in body=@*) cat "\${a#body=@}" >> "$T/filed";; esac; done
    echo '{"number":900}' ;;
  *issues/*) printf '{"number":1228,"title":"Core: null deref","body":"step a.py"}\n' ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
export BOARD="$T/s/board.sh"
r=$(bash "$T/s/autobug.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "8 the pass that hit the refusal finishes" || ko "8 the pass exited $rc: '$r'"
$G -q '^--add o 4 In Progress 1228$' "$T/board.calls" && ok "8 the dispatch adds a missing card before moving it" || ko "8 board call does not add the card: $(cat "$T/board.calls" 2>/dev/null)"
# Re-anchored (#1021, criterion 2: "Dispatch does not start a run while its board write is pending or failed"): the refused
# card write starts no run, frees its claim, and leaves its event pending for the next pass. The old anchor, "the dispatch
# still happens", is superseded.
case "$r" in *"AUTOBUG dispatched"*) ko "8 a refused card write still dispatched: '$r'" ;; *) ok "8 a refused card write starts no run" ;; esac
[ ! -e "$T/launched" ] && ok "8 no launch while the card write is refused" || ko "8 a run launched past a refused card write"
[ ! -e "$T/st/auto/bug-1228" ] && ok "8 the refused dispatch frees its claim" || ko "8 the claim stays after a refused card write"
pend=$(SG_STATE=$T/st SG_REPO=x/y python3 "$T/s/issue_ledger.py" pending "x/y#1228" 2>/dev/null | cut -f1-3)
case "$pend" in "1228	autobug-"*"	Doing") ok "8 the refused write stays pending in the ledger";; *) ko "8 no pending Doing event: '$pend'";; esac
rm -rf "$T/st/state"   # case 8's pending event is its own: the cases after it start with no ledger
filed=$($G -c '^title=' "$T/filed")
[ "$filed" = 1 ] && ok "8 a refused write opens exactly one bug" || ko "8 a refused write opened $filed issue(s): $(cat "$T/filed")"
TITLE=$($G '^title=' "$T/filed" | head -1)
case "$TITLE" in "title=[SQUAD] refused "*) ok "8 the bug carries the [SQUAD] prefix and the helper's name" ;; *) ko "8 title: '$TITLE'";; esac
FP=$(printf '%s' "$TITLE" | $G -oE '\[[0-9a-f]{12}\]$')
[ -n "$FP" ] && ok "8 the title carries the naming helper's fingerprint" || ko "8 no fingerprint in '$TITLE'"
$G -qF "Filed by autobug" "$T/filed" && ok "8 the bug carries the Filed by line of the pass that hit the refusal" || ko "8 no Filed by line: $(cat "$T/filed")"
$G -qF 'labels[]=bug' "$T/filed" && $G -qF 'labels[]=ready-for-agent' "$T/filed" \
  && ok "8 the bug arrives with the bug kind and the startable label" || ko "8 labels: $(cat "$T/filed")"
extra=$($G -oE '^labels\[\]=[^ ]+' "$T/filed" | $G -cv -e 'labels\[\]=bug' -e 'labels\[\]=ready-for-agent' -e 'labels\[\]=frozen:not-startable' || true)   # the publish hold (#1978)
[ "$filed" = 1 ] && [ "${extra:-0}" -eq 0 ] && ok "8 no other label rides along" || ko "8 $extra other label(s): $(cat "$T/filed")"
[ "$($G -c '^```$' "$T/filed")" = 2 ] && ok "8 the evidence sits inside one fence" || ko "8 fence lines: $($G -c '^```$' "$T/filed"): $(cat "$T/filed")"
f=1
for k in "operation: " "address: " "exit code: " "concerns: " "guard that should have caught it: "; do
  $G -qF "$k" "$T/filed" || { ko "8 the fenced evidence lacks '$k'"; f=; }
done
[ -n "$f" ] && ok "8 the fence names the operation, the address, the exit code, the subject and the guard"
$G -qF 'GitHub refused the card write (HTTP 403)' "$T/filed" && ok "8 the error text is in the body verbatim" || ko "8 error text not verbatim: $(cat "$T/filed")"
$G -qi 'never parsed' "$T/filed" && ok "8 the body says the remote text is quoted, never parsed" || ko "8 the body does not say how it treats remote text"
[ "$(grep -c '^--add o 4 In Progress 1228$' "$T/board.calls")" = 1 ] && ok "8 the dispatch adds its selected card before moving it" || ko "8 card move does not add the selected card: $(cat "$T/board.calls" 2>/dev/null)"
[ "$($G -c 'REFUSED' "$T/st/cycle.log")" = 1 ] && ok "8 the refusal is named once in the cycle log" || ko "8 cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
$G -qE '^[0-9]{2}:[0-9]{2}:[0-9]{2} autobug REFUSED refused ' "$T/st/cycle.log" && ok "8 the cycle-log line keeps its one shape" || ko "8 cycle.log line: $(cat "$T/st/cycle.log" 2>/dev/null)"
LOG=$($G 'REFUSED' "$T/st/cycle.log")
case "$LOG" in *"$FP"*) [ -n "$FP" ] && ok "8 the log line and the title carry the same fingerprint, from one helper" || ko "8 nothing was named, so nothing shares a fingerprint" ;; *) ko "8 log '$LOG' against title '$TITLE'";; esac
# 8b) (#140) the pass that filed it is named, and the issue it filed is one line of THAT pass's ledger under the
# cause's fingerprint -- so a storm of distinct refusals stops at the filer's ceiling, and the ceiling counts causes,
# never occurrences.
led=$(ls "$T/st/auto"/filed-autobug-* 2>/dev/null | head -1)
if [ -n "$led" ]; then
  [ "$(wc -l < "$led" | tr -d ' ')" = 1 ] && ok "8b the pass's ledger holds the one issue it filed" || ko "8b ledger lines: $(cat "$led")"
  fpbare=${FP#[}; fpbare=${fpbare%]}
  $G -qF "$fpbare" "$led" && ok "8b the ledger counts the cause's fingerprint, from the one helper" || ko "8b ledger lacks '$fpbare': $(cat "$led")"
else ko "8b the refused write left no ledger for the pass: $(ls "$T/st/auto" 2>/dev/null | tr '\n' ' ')"; fi
# 8c) (#403) a refused write whose address is an issue autobug itself filed is never filed again. The chain: autobug files
# a refusal bug, dispatches it, its card write fails, and the door files one more bug about that bug, about every 5 min.
# Both marks of an autobug issue are read: the "[SQUAD] refused" title and the "Filed by autobug" body line.
for mark in title body; do
  rm -rf "$T/st/auto"; mkdir -p "$T/st/auto"; : > "$T/filed"; : > "$T/st/cycle.log"; : > "$T/board.calls"
  if [ "$mark" = title ]; then tt='[SQUAD] refused BOARD-WRITE-FAILED o/4 #1227 -> In Progress [0123456789ab]'; bb='step a.py'
  else tt='Core: null deref'; bb='The loop could not prove a write. Filed by autobug'; fi
  cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) echo '{"data":{"repository":{"i1228":{"number":1228,"title":"$tt","body":"$bb","state":"OPEN","labels":{"nodes":[]},"blockedBy":{"nodes":[]},"comments":{"nodes":[]}}}}}' ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues?state=open"*labels=bug*) echo '[{"number":1228,"title":"Core: null deref","body":"step a.py","state":"open","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}]' ;;
  *"issues?state=open"*) echo "[]" ;;
  *issues/*/comments*) echo "[]" ;;
  *"--method POST"*) printf '%s\n' "\$@" >> "$T/filed"; echo '{"number":901}' ;;
  *issues/*) printf '{"number":1228,"title":"$tt","body":"$bb"}\n' ;;
  *) echo "" ;;
esac
J
  chmod +x "$T/bin/gh"
  r=$(bash "$T/s/autobug.sh" 2>&1); rc=$?
  [ "$rc" = 0 ] && ok "8c ($mark) the pass finishes" || ko "8c ($mark) the pass exited $rc: '$r'"
  [ -s "$T/board.calls" ] && ok "8c ($mark) the card write is still tried" || ko "8c ($mark) no card write"
  [ "$($G -c '^title=' "$T/filed")" = 0 ] && ok "8c ($mark) a refused write on an autobug issue files no bug" || ko "8c ($mark) filed: $(cat "$T/filed")"
  [ "$($G -c 'REFUSED' "$T/st/cycle.log")" = 1 ] && $G -q 'autobug-filed' "$T/st/cycle.log" \
    && ok "8c ($mark) one cycle-log line names it instead" || ko "8c ($mark) cycle.log: $(cat "$T/st/cycle.log")"
  rm -rf "$T/st/state"   # a refused card write leaves its event pending; the next iteration and case start with no ledger
done

# 9) (#188) the dispatch moves the cards of the issues it just requested, and an issue the loop filed itself carries no
# card until the board pass adds one. A plain move for a card the board does not carry is refused ("#N not on the
# board"), and through sg_write that refusal is filed as one more bug, whose own dispatch is refused the same way and
# files one more: the pass fed itself the same defect. A dispatch cannot assume the card: it moves its cards through the
# idempotent add path every other board writer uses (board.py --add, janitor rule R, reconcile.py), so a card the board
# does not carry is added with its status, and a card it already carries answers "already exists" and is moved.
# The stub below answers like board.py does: the add path succeeds, the plain move is refused.
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$d/loopbug.sh" "$d/autohunt.sh" "$T/s/"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
: > "$T/board.calls"
cat > "$T/s/board.sh" <<J
#!/bin/bash
echo "\$*" >> "$T/board.calls"
case " \$* " in *" --add "*) echo "#1228 -> In Progress" ;; *) echo "#1228 not on the board" >&2; exit 1 ;; esac
J
printf '#!/bin/bash\necho %s/req.json\n' "$T" > "$T/s/mkreq.sh"
cat > "$T/s/launch.py" <<J
import os
try: seen = open(os.path.join("$T", "board.calls")).read()
except OSError: seen = ""
open(os.path.join("$T", "launched"), "w").write(seen)   # what the board held the instant the launch started
J
chmod +x "$T/s/board.sh" "$T/s/mkreq.sh" "$T/s/launch.py"
: > "$T/filed"; : > "$T/st/cycle.log"
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues?state=open"*labels=bug*) echo '[{"number":1228,"title":"Core: null deref","body":"step a.py","state":"open","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}]' ;;
  *"issues?state=open"*) echo "[]" ;;
  *issues/*/comments*) echo "[]" ;;
  *"--method POST"*) printf '%s\n' "\$@" >> "$T/filed"
    for a in "\$@"; do case "\$a" in body=@*) cat "\${a#body=@}" >> "$T/filed";; esac; done
    echo '{"number":900}' ;;
  *issues/*) printf '{"number":1228,"title":"Core: null deref","body":"step a.py"}\n' ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
export BOARD="$T/s/board.sh" SG_BUG_PROJECT=4 SG_BOARD_VOCAB=shipped   # both dispatch writes: the loop board and the bug board; SG_BUG_DOING unset, so the default writes
r=$(bash "$T/s/autobug.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "9 the dispatch whose cards the board does not carry finishes" || ko "9 the pass exited $rc: '$r'"
# Re-anchored (#1021, ADR-0165): a card write that lands comes before the launch. The old anchor, "the dispatch happens",
# said nothing about the order the run depends on.
case "$r" in *"AUTOBUG dispatched"*) ok "9 a card write that lands is followed by the launch" ;; *) ko "9 no dispatch: '$r'";; esac
[ -e "$T/launched" ] && ok "9 the launch ran" || ko "9 no launch"
[ "$($G -c -- '--add o 4 Doing ' "$T/launched" 2>/dev/null)" = 1 ] \
  && ok "9 the launch saw its own card write already on the board" \
  || ko "9 the launch ran before the card write landed: $(cat "$T/launched" 2>/dev/null)"
filed=$($G -c '^title=' "$T/filed")
[ "$filed" = 0 ] && ok "9 the dispatch adds the missing card, it files no bug about it" \
  || ko "9 the dispatch filed $filed bug(s) about its own card move: $(cat "$T/filed")"
[ "$($G -c 'REFUSED' "$T/st/cycle.log")" = 0 ] && ok "9 no refusal is named in the cycle log" || ko "9 cycle.log: $(cat "$T/st/cycle.log")"
# both writes reached the door through the add path: a bug board that does not carry the card is repaired the same way
[ "$($G -c '^--add ' "$T/board.calls")" = 2 ] && ok "9 both card moves go through the add path" \
  || ko "9 the door was called: $(cat "$T/board.calls")"
# on the shipped vocabulary both writes name Doing, the column the janitor reads back: "In Progress" is no column there,
# so a write of it was refused on every dispatch
[ "$($G -c -- '--add o 4 Doing ' "$T/board.calls")" = 2 ] && ok "9 both writes name Doing on the shipped vocabulary" || ko "9 the door was called: $(cat "$T/board.calls")"
unset SG_BUG_PROJECT SG_BUG_DOING SG_BOARD_VOCAB

# 10) (#142) the refusal names are a closed set in code. Every writer this pass owns names its refusal through the one
# write door, the name it gives is a member of the set the helper owns, and the cycle log repeats that same name. A
# refusal named by hand -- a name the set does not hold -- is refused by the helper instead of filed under a guess.
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$d/loopbug.sh" "$d/autohunt.sh" "$T/s/"
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
: > "$T/board.calls"
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> %s/board.calls\ncase " $* " in *" 5 "*) echo "#1228: GitHub refused the card write (HTTP 403)" >&2; exit 1;; esac\nexit 0\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho %s/req.json\n' "$T" > "$T/s/mkreq.sh"
printf 'import os\nopen(os.path.join("%s", "launched"), "w").close()\n' "$T" > "$T/s/launch.py"
chmod +x "$T/s/board.sh" "$T/s/mkreq.sh" "$T/s/launch.py"
: > "$T/filed"; : > "$T/st/cycle.log"
# The loop board's card write comes before the launch and gates it (#1021), so a refusal there stops the pass before the
# bug board is written. This case refuses only the bug board (5): its write is after the launch, and its refusal is one filing.
export BOARD="$T/s/board.sh" SG_BUG_PROJECT=5 SG_BUG_DOING="In Progress"
bash "$T/s/autobug.sh" >/dev/null 2>&1
set=$(bash "$d/refusal-names.sh" 2>/dev/null)
[ -n "$set" ] && ok "10 the helper prints the closed set in one place" || ko "10 nothing prints the set"
filed=$($G -c '^title=' "$T/filed")
[ "$filed" = 1 ] && ok "10 the bug board's refusal is named through the door" || ko "10 $filed bug(s), not one card: $(cat "$T/filed")"
named=0; stray=""
while read -r t; do
  n=$($G -oE '[A-Z][A-Z0-9-]*-FAILED' <<< "$t" | head -1)
  if [ -n "$n" ] && $G -qxF "$n" <<< "$set"; then named=$((named+1)); else stray="$stray $n"; fi
done < <($G '^title=' "$T/filed")
[ "$named" = 1 ] && ok "10 the name the bug board's writer gives is a member of the set" || ko "10 names outside the set:$stray / $(cat "$T/filed")"
$G -qE '^[0-9]{2}:[0-9]{2}:[0-9]{2} autobug REFUSED refused BOARD-WRITE-FAILED ' "$T/st/cycle.log" \
  && [ "$($G -c 'REFUSED' "$T/st/cycle.log")" = 1 ] && ok "10 the cycle log repeats the name, in one shape" \
  || ko "10 cycle.log: $(cat "$T/st/cycle.log")"
[ "$($G -c '^--add o 4 In Progress 1228$' "$T/board.calls")" = 1 ] && [ "$($G -c '^--add o 5 In Progress 1228$' "$T/board.calls")" = 1 ] \
  && ok "10 both refusals came from the pass's own card moves, one per board" || ko "10 board calls: $(cat "$T/board.calls")"
unset SG_BUG_PROJECT SG_BUG_DOING
# 11) (#218) the request the pass builds carries each issue's own text. The block runs its jq program through the real
# jq -- the stub below serves `-q` the way `gh api` does -- so a program that does not parse prints nothing and the child
# reads a bare `--- issue #N` line, while the request itself tells it the text below is DATA. Every stub above answers
# without a `-q`, which is what let a malformed program go unnoticed: a program that never runs is never checked.
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$d/loopbug.sh" "$T/s/"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
printf '#!/bin/bash\ncp "$2" %s/req.body\necho %s/req.json\n' "$T" "$T" > "$T/s/mkreq.sh"
printf 'import os\nopen(os.path.join("%s", "launched"), "w").close()\n' "$T" > "$T/s/launch.py"
printf '#!/bin/bash\necho "#1228 -> In Progress"\n' > "$T/s/board.sh"
chmod +x "$T/s/mkreq.sh" "$T/s/launch.py" "$T/s/board.sh"
cat > "$T/bin/gh" <<J
#!/bin/bash
prog=; prev=; for a in "\$@"; do [ "\$prev" = "-q" ] && prog="\$a"; prev="\$a"; done
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues?state=open"*labels=bug*) echo '[{"number":1228,"title":"Core: null deref","body":"step a.py","state":"open","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}]' ;;
  *"issues?state=open"*) echo "[]" ;;
  *issues/*/comments*) echo "[]" ;;
  *issues/*)
    j='{"number":1228,"title":"Core: null deref","body":"step a.py"}'
    if [ -n "\$prog" ]; then printf '%s' "\$j" | jq -r "\$prog"; else printf '%s\n' "\$j"; fi ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
r=$(bash "$T/s/autobug.sh" 2>"$T/err218"); rc=$?
[ "$rc" = 0 ] && ok "11 the pass that pastes the issue text finishes" || ko "11 the pass exited $rc: '$r'"
[ -f "$T/req.body" ] && ok "11 the pass built a request" || ko "11 no request was built: '$r'"
blk=$(awk '/^--- issue #1228$/{f=1;next} /^CHILD /{f=0} f' "$T/req.body" 2>/dev/null)
$G -qF 'Core: null deref' <<<"$blk" && ok "11 the issue block carries the issue's title" || ko "11 the block carries no title: '$blk'"
$G -qF 'step a.py' <<<"$blk" && ok "11 the issue block carries the issue's body" || ko "11 the block carries no body: '$blk'"
$G -qE 'jq: error' "$T/err218" && ko "11 the pass logged a jq parse error: $(cat "$T/err218")" || ok "11 the pass logs no jq parse error"

# 12) (#226) a GitHub server error is not a refusal. HTTP 500 is GitHub failing to answer, not GitHub refusing the write,
# and the same request a moment later is answered. board.py read the issue of an --add once, took the 500 as final, and
# the door filed "refused BOARD-WRITE-FAILED ... gh: HTTP 500" as a bug. The real board.sh and board.py run here, and
# the stub answers the board's first issue read with HTTP 500: the card is still written, and nothing is filed.
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"; rm -f "$T"/st/owner-kind-* "$T"/st/rest-*
cp "$d/autobug.sh" "$d/loopbug.sh" "$d/autohunt.sh" "$d/board.sh" "$d/board.py" "$d/issue_state.py" "$d/board_columns.py" "$d/board-definition.json" "$T/s/"   # board.py imports the seam
printf '#!/bin/bash\necho %s/req.json\n' "$T" > "$T/s/mkreq.sh"
printf 'import os\nopen(os.path.join("%s", "launched"), "w").close()\n' "$T" > "$T/s/launch.py"
chmod +x "$T/s/mkreq.sh" "$T/s/launch.py"
: > "$T/filed"; : > "$T/st/cycle.log"; : > "$T/board.calls"; rm -f "$T/b500" "$T/bseen" "$T/bstatus"
cat > "$T/bin/gh" <<J
#!/bin/bash
a="\$*"
if [ "\${SG_GH_PASS:-}" = board ] || case "\$a" in *projectsV2*|"api users/o") true;; *) false;; esac; then   # the calls of the real board.py, and the pass's own card read (#550)
  echo "\$a" >> "$T/board.calls"
  case "\$a" in *"--input -"*) cat > "$T/board.in";; esac
  case "\$a" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;
    "api users/o") echo '{"type":"User"}' ;;
    *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_PROG","name":"In Progress"}]}]' ;;
    *items?per_page=100\&fields*) printf '1228\t%s\thttps://api.github.com/repos/x/y\n' "\$(cat "$T/bstatus" 2>/dev/null || echo Todo)"; exit 0 ;;   # the seam's tsv row: the owner's card in Todo until the pass moves it
    *items?per_page=100*) [ -e "$T/bseen" ] && printf '[1228,"ITEM1","https://api.github.com/repos/x/y"]\n'; exit 0 ;;
    *repos/x/y/issues/1228*)
      if [ ! -e "$T/b500" ]; then touch "$T/b500"; echo 'gh: HTTP 500' >&2; exit 1; fi   # GitHub's server failed once
      echo '{"id":3228}' ;;
    *"-X POST"*items*) touch "$T/bseen"; echo '{"id":"ITEM1"}' ;;
    *"-X PATCH"*items/ITEM1*) [ -e "$T/bnowrite" ] && { echo '{"id":"ITEM1"}'; exit 0; }; sed -n 's/.*"value": "\([^"]*\)".*/\1/p' "$T/board.in" > "$T/bstatus"; echo '{"id":"ITEM1"}' ;;
    *) echo "" ;;
  esac
  exit 0
fi
case "\$a" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues?state=open"*labels=bug*) echo '[{"number":1228,"title":"Core: null deref","body":"step a.py","state":"open","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}]' ;;
  *"issues?state=open"*) echo '[{"number":1228,"title":"Core: null deref","body":"step a.py","state":"open","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}]' ;;   # the unlabelled list the kinds route reads (#550: the seam now answers the kinds)
  *issues/*/comments*) echo "[]" ;;
  *"--method POST"*) printf '%s\n' "\$@" >> "$T/filed"
    for x in "\$@"; do case "\$x" in body=@*) cat "\${x#body=@}" >> "$T/filed";; esac; done
    echo '{"number":900}' ;;
  *issues/*) printf '{"number":1228,"title":"Core: null deref","body":"step a.py"}\n' ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
export BOARD="$T/s/board.sh" SG_BOARD_READ_BACKOFF=0
r=$(bash "$T/s/autobug.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "12 the pass whose board read met HTTP 500 finishes" || ko "12 the pass exited $rc: '$r'"
[ -e "$T/b500" ] && ok "12 the board's issue read met HTTP 500 once" || ko "12 the stub never served the 500: $(cat "$T/board.calls")"
[ "$(cat "$T/bstatus" 2>/dev/null)" = O_PROG ] && ok "12 the card is written In Progress after the server error" \
  || ko "12 the card was not written: $(cat "$T/board.calls")"
filed=$($G -c '^title=' "$T/filed")
[ "$filed" = 0 ] && ok "12 a server error files no bug" || ko "12 a server error filed $filed bug(s): $(cat "$T/filed")"
[ "$($G -c 'REFUSED' "$T/st/cycle.log")" = 0 ] && ok "12 no refusal is named in the cycle log" || ko "12 cycle.log: $(cat "$T/st/cycle.log")"
unset SG_BOARD_READ_BACKOFF

# 12b) (#432) a read that never catches up is not a refusal. GitHub accepted the card and the read carried no Status for
# the whole settle bound: board.py says "a read failure, not a written card", and that is the board failing to answer, not
# GitHub refusing the write -- the same as the HTTP 500 above. The door logged it by name and filed no bug; the next pass
# writes the card again. The stub accepts the PATCH and never shows the Status.
rm -rf "$T/st/auto" "$T/st/state"; mkdir -p "$T/st/auto"; rm -f "$T"/st/rest-* "$T/bseen" "$T/bstatus" "$T/launched" "$T/st/board-transitions.x_y.jsonl"; touch "$T/b500" "$T/bnowrite"   # no proved write of case 12 stands: this card's write is the one that never catches up (#1995)
: > "$T/filed"; : > "$T/st/cycle.log"
export SG_BOARD_READ_BACKOFF=0 SG_BOARD_SETTLE_SECONDS=0
r=$(bash "$T/s/autobug.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "12b the pass whose card read never caught up finishes" || ko "12b the pass exited $rc: '$r'"
filed=$($G -c '^title=' "$T/filed")
[ "$filed" = 0 ] && ok "12b a read failure files no bug" || ko "12b a read failure filed $filed bug(s): $(cat "$T/filed")"
$G -q 'BOARD-READ-FAILED' "$T/st/cycle.log" && ok "12b the read failure is named in the cycle log" || ko "12b cycle.log: $(cat "$T/st/cycle.log")"
rm -f "$T/bnowrite"; unset SG_BOARD_READ_BACKOFF SG_BOARD_SETTLE_SECONDS
rm -rf "$T/st/state"   # a board read failure is no proof of the write: its event stays pending, and the next case starts with no ledger
cp "$T/issue_state-stub.py" "$T/s/issue_state.py"   # the dry cases below read the cards from cards.tsv again

# 13) (#243) the bug pass takes items by the kind the seam answers, never by the platform's issue type. 1500 carries kind
# bug and no label; 1501 carries the bug label but kind task (the kind wins); 1502 carries the label and no kind (the
# label stays the fallback for an item no kind was written on); 1503 carries kind bug and a skip label. Its own sandbox.
K=$T/k; mkdir -p "$K/bin" "$K/st/auto" "$K/co/docs/squad" "$K/s"
cp "$d/autobug.sh" "$d/loopbug.sh" "$d/cluster.py" "$d/single.sh" "$d/startability.sh" "$d/skiplabels.sh" "$d/ghcall.sh" "$d/refusal-names.sh" "$d/workbranches.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/ghthrottle.py" "$K/s/"
: > "$K/st/active-runs.txt"; printf 'caps:\n  runs: 4\n' > "$K/co/docs/squad/defaults.yaml"
printf 'export SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_STATE=%s SG_BASE=main SG_CHECKOUT=%s SG_FRONTIER_LABEL=ready-for-agent SG_BUG_LABEL=bug\n' "$K/st" "$K/co" > "$K/s/env.sh"
printf '#!/bin/bash\necho READY\n' > "$K/s/stack.sh"; chmod +x "$K/s/stack.sh"
printf '1500\tbug\n1501\ttask\n1503\tbug\n' > "$K/kinds.tsv"
# the seam's door, stubbed at the script's own name: the pass asks `issue_state.py --kinds` and never names a carrier
cat > "$K/s/issue_state.py" <<PY
import os, sys
if not sys.argv[1:]: sys.stdout.write("".join(f"{n}\\tTodo\\n" for n in (1500, 1501, 1502, 1503))); sys.exit(0)   # every card in Todo (#550)
if sys.argv[1:] != ["--kinds"]: sys.exit(2)
if os.path.exists("$K/seam-down"): print("no board", file=sys.stderr); sys.exit(1)
sys.stdout.write(open("$K/kinds.tsv").read())
PY
cat > "$K/bin/gh" <<J
#!/bin/bash
list() { jq -c --arg l "\$1" '[.[]|select(\$l == "" or ((.labels // [])|index(\$l)))|.labels=[(.labels//[])[]|{name:.}]]' "$K/issues.json"; }
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$K/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$K/issues.json" ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$K/issues.json" ;;   # the blocker gate's one list read (#479)
  *"issues?state=open"*labels=bug*) list bug ;;
  *"issues?state=open"*labels=debt*) list debt ;;
  *"issues?state=open"*) list "" ;;
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); jq -c --argjson n "\$n" '.[]|select(.number==\$n)' "$K/issues.json" ;;
  *) echo "" ;;
esac
J
chmod +x "$K/bin/gh"
cat > "$K/issues.json" <<'JSON'
[
  {"number":1500,"title":"Core: a","body":"a.py","created_at":"2026-10-10T10:00:00Z","labels":["ready-for-agent"]},
  {"number":1501,"title":"Core: b","body":"b.py","created_at":"2026-10-10T11:00:00Z","labels":["bug","ready-for-agent"]},
  {"number":1502,"title":"Core: c","body":"c.py","created_at":"2026-10-10T12:00:00Z","labels":["bug","ready-for-agent"]},
  {"number":1503,"title":"Core: d","body":"d.py","created_at":"2026-10-10T13:00:00Z","labels":["no-auto","ready-for-agent"]}
]
JSON
r=$(PATH="$K/bin:$PATH" SG_SQUAD="$SG_SQUAD" AB_DRY=1 bash "$K/s/autobug.sh" 2>&1)
case "$r" in *1500*) ok "13 an item of kind bug with no bug label is taken";; *) ko "13 kind bug #1500 not taken: '$r'";; esac
case "$r" in *1501*) ko "13 the bug label won over kind task for #1501: '$r'";; *) ok "13 an item of another kind is not taken, label or not";; esac
case "$r" in *1502*) ok "13 an item with no kind keeps the label as its fallback";; *) ko "13 label fallback #1502 not taken: '$r'";; esac
case "$r" in *1503*) ko "13 a skip label did not hold kind bug #1503: '$r'";; *) ok "13 a skip label still holds an item of kind bug";; esac
touch "$K/seam-down"
r=$(PATH="$K/bin:$PATH" SG_SQUAD="$SG_SQUAD" AB_DRY=1 bash "$K/s/autobug.sh" 2>&1)
case "$r" in *"kinds are not read"*) ok "13 a seam that answers no kinds is reported, never silent";; *) ko "13 no report line when the seam is down: '$r'";; esac
case "$r" in *1501*1502*|*1502*) ok "13 with the seam down the pass falls back to the label";; *) ko "13 no fallback with the seam down: '$r'";; esac

# 14) A run parent ("Run <start> (<short id>) — <children>", or the old "Squad run <run id>") is a roll-up, never a bug to fix: the bug pass skips it by title, whatever its labels.
R=$T/runparent; mkdir -p "$R/bin"
cat > "$R/issues.json" <<'JSON'
[
  {"number":60,"title":"Run 2026-10-14 03:07 (11d7af) — #10, #11","labels":[{"name":"bug"},{"name":"ready-for-agent"}]},
  {"number":62,"title":"Squad run run-20261014-030735-11d7af","labels":[{"name":"bug"},{"name":"ready-for-agent"}]},
  {"number":61,"title":"Squad run report is wrong","body":"x.py","created_at":"2026-10-10T15:00:00Z","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}
]
JSON
cat > "$R/bin/gh" <<G
#!/bin/bash
q=""; prev=""; for a in "\$@"; do [ "\$prev" = "-q" ] && q="\$a"; prev="\$a"; done
case "\$*" in *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:.labels},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$R/issues.json";; *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:.labels},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$R/issues.json";; *"issues?state=open"*) cat "$R/issues.json";; *pulls?state=open*) echo "[]";; *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); jq -c --argjson n "\$n" '.[]|select(.number==\$n)' "$R/issues.json";; *) echo "";; esac
G
chmod +x "$R/bin/gh"
r=$(PATH="$R/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[60]"*) ko "14 the bug pass picked a run parent: '$r'";; *) ok "14 a run parent is not a bug candidate";; esac
case "$r" in *"[62]"*) ko "14 the bug pass picked an old-title run parent: '$r'";; *) ok "14 an old-title run parent is not a bug candidate";; esac
case "$r" in *"[61]"*) ok "14 an issue whose title only starts alike is still a candidate";; *) ko "14 a look-alike title was skipped: '$r'";; esac

# 15) A frozen issue waits on its owner or on a dependency outside the repo: the bug pass skips it, as autofrontier does (#530).
F=$T/frozen; mkdir -p "$F/bin"; todo 70 71 72 73
cat > "$F/issues.json" <<'JSON'
[
  {"number":70,"title":"Gateway drops a task","body":"x.py","created_at":"2026-10-10T15:00:00Z","labels":[{"name":"bug"},{"name":"ready-for-agent"},{"name":"frozen:dependency"}]},
  {"number":71,"title":"Owner must pick","body":"y.py","created_at":"2026-10-10T15:01:00Z","labels":[{"name":"bug"},{"name":"ready-for-agent"},{"name":"frozen:owner"}]},
  {"number":72,"title":"Plain bug","body":"z.py","created_at":"2026-10-10T15:02:00Z","labels":[{"name":"bug"},{"name":"ready-for-agent"}]},
  {"number":73,"title":"Bug with no go label","body":"w.py","created_at":"2026-10-10T15:03:00Z","labels":[{"name":"bug"}]}
]
JSON
sed "s#$R/issues.json#$F/issues.json#g" "$R/bin/gh" > "$F/bin/gh"; chmod +x "$F/bin/gh"
r=$(PATH="$F/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[70]"*) ko "15 the bug pass picked a frozen:dependency issue: '$r'";; *) ok "15 a frozen:dependency issue is not a bug candidate";; esac
case "$r" in *"[71]"*) ko "15 the bug pass picked a frozen:owner issue: '$r'";; *) ok "15 a frozen:owner issue is not a bug candidate";; esac
case "$r" in *"[72]"*) ok "15 a plain bug beside them is still a candidate";; *) ko "15 the plain bug was skipped: '$r'";; esac
NS=$T/notstartable; mkdir -p "$NS/bin"; todo 69 72
cat > "$NS/issues.json" <<'JSON'
[
  {"number":69,"title":"Bug the loop marked not startable","body":"v.py","created_at":"2026-10-10T14:59:00Z","labels":[{"name":"bug"},{"name":"ready-for-agent"},{"name":"frozen:not-startable"}]},
  {"number":72,"title":"Plain bug","body":"z.py","created_at":"2026-10-10T15:02:00Z","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}
]
JSON
sed "s#$R/issues.json#$NS/issues.json#g" "$R/bin/gh" > "$NS/bin/gh"; chmod +x "$NS/bin/gh"
r=$(PATH="$NS/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[69]"*) ko "15 the bug pass picked a frozen:not-startable issue: '$r'";; *) ok "15 a frozen:not-startable issue is not a bug candidate";; esac
case "$r" in *"[72]"*) ok "15 the plain bug beside a frozen:not-startable one is dispatched";; *) ko "15 the plain bug was not dispatched beside a frozen:not-startable one: '$r'";; esac
# 15b) (#537) a profile sets its own SG_BUG_SKIP_LABELS, and env.sh exports it: the frozen labels are a floor, never a default
# a profile list replaces. The live profile named the four old labels, so the pass dispatched a frozen:dependency issue.
r=$(PATH="$F/bin:$PATH" SG_BUG_SKIP_LABELS=needs-owner,no-auto,wontfix,question AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[70]"*) ko "15b a profile skip list let a frozen:dependency issue through: '$r'";; *) ok "15b a profile skip list keeps the frozen:dependency floor";; esac
case "$r" in *"[71]"*) ko "15b a profile skip list let a frozen:owner issue through: '$r'";; *) ok "15b a profile skip list keeps the frozen:owner floor";; esac
case "$r" in *"[72]"*) ok "15b a plain bug is still a candidate under a profile skip list";; *) ko "15b the plain bug was skipped: '$r'";; esac
# 15d) (#1673) a holder or needs-triage label holds a Todo bug beside its go label, with or without the go label, and a profile's list never lifts it.
H=$T/holders; mkdir -p "$H/bin"; todo 77 78 79 80
cat > "$H/issues.json" <<'JSON'
[
  {"number":77,"title":"Bug for a human","body":"a.py","created_at":"2026-10-10T15:04:00Z","labels":[{"name":"bug"},{"name":"ready-for-agent"},{"name":"ready-for-human"}]},
  {"number":78,"title":"Bug for HAL9K","body":"b.py","created_at":"2026-10-10T15:05:00Z","labels":[{"name":"bug"},{"name":"ready-for-agent"},{"name":"ready-for-HAL9K"}]},
  {"number":79,"title":"Bug nobody triaged","body":"c.py","created_at":"2026-10-10T15:06:00Z","labels":[{"name":"bug"},{"name":"ready-for-agent"},{"name":"needs-triage"}]},
  {"number":80,"title":"Bug held without go label","body":"e.py","created_at":"2026-10-10T15:08:00Z","labels":[{"name":"bug"},{"name":"ready-for-human"}]}
]
JSON
sed "s#$R/issues.json#$H/issues.json#g" "$R/bin/gh" > "$H/bin/gh"; chmod +x "$H/bin/gh"
r=$(SG_BUG_SKIP_LABELS=needs-owner,no-auto,wontfix,question PATH="$H/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[77]"*|*"[78]"*|*"[79]"*|*"[80]"*) ko "15d a holder or needs-triage label let a bug through: '$r'";; *) ok "15d a holder or needs-triage label keeps a bug out, beside or without ready-for-agent";; esac
P=$T/holders-plain; mkdir -p "$P/bin"; todo 81
cat > "$P/issues.json" <<'JSON'
[
  {"number":81,"title":"Plain bug","body":"d.py","created_at":"2026-10-10T15:07:00Z","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}
]
JSON
sed "s#$R/issues.json#$P/issues.json#g" "$R/bin/gh" > "$P/bin/gh"; chmod +x "$P/bin/gh"
r=$(SG_BUG_SKIP_LABELS=needs-owner,no-auto,wontfix,question PATH="$P/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[81]"*) ok "15d a plain bug is dispatched under the same profile list";; *) ko "15d the plain bug was skipped: '$r'";; esac
# 15c) (#729) a bug whose acceptance command names a missing repository script is not dispatched: the pass names the path and
# the needs-info refusal. A bug with no acceptance command is dispatched as before. needs-info is a skip floor, as frozen is.
# The stub's aggregate read carries each body, as the real one does.
M=$T/acmiss; mkdir -p "$M/bin"; cp "$d/acpaths.py" "$T/s/" 2>/dev/null
cat > "$M/issues.json" <<'JSON'
[
  {"number":74,"title":"Check fails","body":"## Acceptance criteria\n\n- [ ] `python3 scripts/checks/gone.py` passes.","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]},
  {"number":75,"title":"Plain bug","body":"z.py","created_at":"2026-10-10T15:02:00Z","labels":["bug","ready-for-agent"]},
  {"number":76,"title":"Waits on an answer","body":"q.py","created_at":"2026-10-10T15:03:00Z","labels":["bug","ready-for-agent","needs-info"]}
]
JSON
cat > "$M/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$M/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$M/issues.json" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$M/issues.json" ;;
  *"issues?state=open"*labels=bug*) jq -c '[.[]|select((.labels // [])|index("bug"))|.labels=[(.labels//[])[]|{name:.}]]' "$M/issues.json" ;;
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); jq -c --argjson n "\$n" '.[]|select(.number==\$n)' "$M/issues.json" ;;
  *) echo "" ;;
esac
J
chmod +x "$M/bin/gh"; todo 74 75 76
r=$(PATH="$M/bin:$PATH" SG_GH_CACHE_WINDOW=0 AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"would dispatch"*"[74]"*|*"would dispatch"*" 74"*) ko "15c a bug whose acceptance command names a missing script was dispatched: '$r'";; *) ok "15c a bug whose acceptance command names a missing script is not dispatched";; esac
case "$r" in *"AUTOBUG would refuse #74: "*"scripts/checks/gone.py"*needs-info*) ok "15c the pass names the missing path and needs-info";; *) ko "15c no refusal line for #74: '$r'";; esac
case "$r" in *"[75]"*) ok "15c a bug with no acceptance command is still a candidate";; *) ko "15c the plain bug was skipped: '$r'";; esac
case "$r" in *"[76]"*|*" 76"*) ko "15c a needs-info bug was picked: '$r'";; *) ok "15c a needs-info bug is not a candidate";; esac
todo 1228 1229 1400 1401 60 61 62 70 71 72 73

# 16) (#550) the column Todo is the owner's go signal: the bug pass takes a bug only when its card sits in Todo. A bug in
# Backlog, or with no card, waits for the owner. The cards come from one board read a pass; a board that does not answer
# dispatches nothing.
todo 92
r=$(PATH="$F/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[72]"*) ko "16 a bug with no card in Todo was dispatched: '$r'";; *) ok "16 a bug whose card is not in Todo is not dispatched";; esac
printf '72\tBacklog\n' > "$T/cards.tsv"
r=$(PATH="$F/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[72]"*) ko "16 a bug in Backlog was dispatched: '$r'";; *) ok "16 a bug in Backlog is not dispatched";; esac
todo 72 73; printf 'caps:\n  runs: 4\n' > "$T/co/docs/squad/defaults.yaml"   # room for both: the column alone decides
r=$(PATH="$F/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[72]"*) ok "16 a bug in Todo is dispatched";; *) ko "16 a bug in Todo was not dispatched: '$r'";; esac
# Re-anchored (#1677, ADR-0018): the column is the go signal, so an untriaged Todo bug is taken too; the pass writes its
# label in the same act. A holder label is the brake, and case 15d pins it.
case "$r" in *73*) ok "16 a bug in Todo with no triage label is dispatched too (#1677)";; *) ko "16 the untriaged Todo bug was not dispatched: '$r'";; esac
rm -f "$T/cards.tsv"
r=$(PATH="$F/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[72]"*) ko "16 a bug went out with no board read: '$r'";; *"board"*) ok "16 a board that does not answer dispatches nothing, and the pass says so";; *) ko "16 the pass is silent about the board: '$r'";; esac

# 17) (#596, ADR-0146) an open issue carrying `loop-bug:dispatch` is a defect in the loop's dispatch path: the loop cannot
# dispatch its own fix, so the pass dispatches nothing, names the issue and exits 0. The stop is read from the open issues the pass
# already reads for its blocker gate, published to the loop log and to the issue's line on the board, and it has no marker:
# closing the issue is the only action that resumes dispatch.
rm -rf "$T/st/auto" "$T/st/req" "$T/st/cq"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$d/loopbug.sh" "$T/s/"
printf 'caps:\n  runs: 4\n' > "$T/co/docs/squad/defaults.yaml"
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"
printf 'print("-")\n' > "$T/s/fundroom.py"
todo 1228 596
cat > "$T/issues.json" <<'JSON'
[
  {"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]},
  {"number":596,"title":"The loop cannot land its own fix","body":"step loop.py","created_at":"2026-10-18T08:00:00Z","labels":["bug","ready-for-agent","loop-bug:dispatch"]}
]
JSON
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issues?state=open"*labels=bug*) echo '[{"number":1228,"title":"Core: null deref","body":"step a.py","state":"open","labels":[{"name":"bug"},{"name":"ready-for-agent"}]},{"number":596,"title":"Core: null deref","body":"step a.py","state":"open","labels":[{"name":"bug"},{"name":"ready-for-agent"},{"name":"loop-bug:dispatch"}]}]' ;;
  *"issues?state=open"*) echo "[]" ;;
  *issues/*) printf '{"number":1228,"title":"Core: null deref","body":"step a.py"}\n' ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
: > "$T/st/cycle.log"; rm -f "$T/launched"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "17 a dispatch-path loop defect stops the bug pass with exit 0" || ko "17 the stopped pass exited $rc: '$r'"
case "$r" in *"LOOP STOP: #596 is a loop defect; the fix lands outside the loop"*) ok "17 the stop names the issue and where the fix lands";; *) ko "17 the stop is not named: '$r'";; esac
$G -q "AUTOBUG would dispatch" <<<"$r" && ko "17 the stopped pass dispatched: '$r'" || ok "17 the stopped pass dispatches nothing"
m=($(find "$T/st/auto" -name 'bug-*' 2>/dev/null)); [ ${#m[@]} -eq 0 ] && ok "17 the stopped pass writes no dispatch marker" || ko "17 markers: ${m[*]##*/}"
[ -e "$T/launched" ] && ko "17 the stopped pass launched a run" || ok "17 the stopped pass launches no run"
$G -qxF "LOOP STOP: #596 is a loop defect; the fix lands outside the loop" "$T/st/cycle.log" && ok "17 the stop is published to the loop log" || ko "17 cycle.log: $(cat "$T/st/cycle.log")"
$G -qxF "LOOP STOP: #596 is a loop defect; the fix lands outside the loop" "$T/st/cq/x__y#596-status~loopstop.md" 2>/dev/null && ok "17 the stop is the issue's line on the board" || ko "17 the board line is missing: $(ls "$T/st/cq" 2>/dev/null)"
# closing the issue is the only action: no marker to remove
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
todo 1228
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ok "17 closing the issue resumes dispatch with no other action";; *) ko "17 dispatch did not resume: '$r'";; esac

# 17b) (ADR-0146 decision 2) an open `loop-bug:infra` names a defect in the loop's infrastructure. The label outlives a
# restart, so the bug pass stops with it open and resumes when it closes, exactly as it does for the dispatch path.
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]},
 {"number":1374,"title":"A fix run proves in the target","body":"step loop.py","created_at":"2026-11-04T19:00:00Z","labels":["bug","loop-bug:infra"]}]
JSON
: > "$T/st/cycle.log"; rm -f "$T/launched"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"LOOP STOP: #1374 is a loop defect; the fix lands outside the loop"*) ok "17b an infrastructure loop defect stops the bug pass";; *) ko "17b the stop is not named: '$r'";; esac
$G -q "AUTOBUG would dispatch" <<<"$r" && ko "17b the stopped pass dispatched: '$r'" || ok "17b the stopped pass dispatches nothing"
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ok "17b closing the issue resumes dispatch";; *) ko "17b dispatch did not resume: '$r'";; esac

# 18) (#556) a selected issue is read once a pass. The validation read and the cluster read used to be two fetches of
# the same issue, and each fetch paid a Python startup plus a gh call. The stub records the plain issue routes it
# answers: the pass asks for the issue one time, and the number it validated is the JSON the cluster receives.
rm -rf "$T/st/auto" "$T/st/req" "$T/st/cq"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$d/loopbug.sh" "$T/s/"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"
printf 'print("-")\n' > "$T/s/fundroom.py"
todo 1228
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
: > "$T/reads"
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) echo "read" >> "$T/reads"; n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issues?state=open"*labels=bug*) echo '[{"number":1228,"title":"Core: null deref","body":"step a.py","state":"open","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}]' ;;
  *"issues?state=open"*) echo "[]" ;;
  *issues/*/comments*) echo "[]" ;;
  "api repos/x/y/issues/1228") echo "read" >> "$T/reads"; printf '{"number":1228,"title":"Core: null deref","body":"step a.py"}\n' ;;
  *issues/*) echo "" ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ok "18 the pass dispatches its candidate";; *) ko "18 no dispatch: '$r'";; esac
n=$($G -c '^read$' "$T/reads")
[ "$n" = 1 ] && ok "18 a selected issue is fetched once a pass" || ko "18 the issue was fetched $n time(s): validation and clustering each read it"

# 19) (#562) an interim limit until the review moves into the run: childcc writes the count of finished children that wait
# for review to $SG_STATE/childcc-waiting. Over SG_DISPATCH_MAX_WAITING the pass dispatches nothing and says why; at or
# under the cap, with no cap, with no count file or with a stale one (over 30 min), it dispatches as before.
hold() { # <count or -> <cap or -> [age in minutes]: one dry pass on fresh state
  rm -rf "$T/st/auto" "$T/st/req" "$T/st/cq"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"; : > "$T/st/cycle.log"
  rm -f "$T/st/childcc-waiting"; [ "$1" = - ] || echo "$1" > "$T/st/childcc-waiting"
  [ -z "${3:-}" ] || touch -t "$(date -r $(( $(date +%s) - $3 * 60 )) +%Y%m%d%H%M.%S 2>/dev/null || date -d "@$(( $(date +%s) - $3 * 60 ))" +%Y%m%d%H%M.%S)" "$T/st/childcc-waiting"
  if [ "$2" = - ]; then (unset SG_DISPATCH_MAX_WAITING; AB_DRY=1 bash "$T/s/autobug.sh" 2>&1); else SG_DISPATCH_MAX_WAITING=$2 AB_DRY=1 bash "$T/s/autobug.sh" 2>&1; fi
}
r=$(hold 3 2); rc=$?
[ "$rc" = 0 ] && ok "19 a hold exits 0" || ko "19 the held pass exited $rc: '$r'"
$G -q "AUTOBUG would dispatch" <<<"$r" && ko "19 over the cap the pass dispatched: '$r'" || ok "19 over the cap the pass dispatches nothing"
HOLD19='^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} DISPATCH HOLD: 3 children wait for review \(cap 2\); no new run this pass$'
$G -qE "$HOLD19" <<<"$r" && ok "19 the hold names the count and the cap, stamped in UTC" || ko "19 no hold line: '$r'"
$G -qE "$HOLD19" "$T/st/cycle.log" && ok "19 the hold is published to the loop log, stamped" || ko "19 cycle.log: $(cat "$T/st/cycle.log")"
r=$(hold 2 2); case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ok "19 at the cap the pass dispatches";; *) ko "19 at the cap: '$r'";; esac
r=$(hold 0 2); case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ok "19 under the cap the pass dispatches";; *) ko "19 under the cap: '$r'";; esac
r=$(hold 9 -); case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ok "19 no cap is no limit";; *) ko "19 no cap: '$r'";; esac
r=$(hold 9 ""); case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ok "19 an empty cap is no limit";; *) ko "19 an empty cap: '$r'";; esac
r=$(hold - 0); case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ok "19 a missing count file holds nothing";; *) ko "19 a missing count file: '$r'";; esac
r=$(hold 9 0 31)
case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ok "19 a stale count file holds nothing";; *) ko "19 a stale count file: '$r'";; esac
$G -q "stale childcc" <<<"$r" && ok "19 a stale count file is logged" || ko "19 the stale count is not logged: '$r'"

# 20) (#615) a full disk fails every run the pass would start, and the owner's machine with it. Under SG_MIN_FREE_GB GiB
# free on the volume of $SG_STATE or of $TMPDIR, the pass dispatches nothing, names the volume and its free space, and
# publishes the line to the loop log. At or over the floor it dispatches as before.
disk() { # <floor>: one dry pass on fresh state
  rm -rf "$T/st/auto" "$T/st/req" "$T/st/cq"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"; : > "$T/st/cycle.log"
  rm -f "$T/st/childcc-waiting"; SG_MIN_FREE_GB=$1 AB_DRY=1 bash "$T/s/autobug.sh" 2>&1
}
r=$(disk 999999); rc=$?
[ "$rc" = 0 ] && ok "20 a disk stop exits 0" || ko "20 the stopped pass exited $rc: '$r'"
$G -q "AUTOBUG would dispatch" <<<"$r" && ko "20 under the floor the pass dispatched: '$r'" || ok "20 under the floor the pass dispatches nothing"
$G -qE "^DISK STOP: .* has [0-9]+ GiB free, under the floor of 999999 GiB; nothing dispatched$" <<<"$r" && ok "20 the stop names the volume, its free space and the floor" || ko "20 no stop line: '$r'"
$G -qE "^DISK STOP: " "$T/st/cycle.log" && ok "20 the stop is published to the loop log" || ko "20 cycle.log: $(cat "$T/st/cycle.log")"
r=$(disk 0); case "$r" in *"AUTOBUG would dispatch"*"[1228]"*) ok "20 over the floor the pass dispatches";; *) ko "20 over the floor: '$r'";; esac

# 21) (#616) a bug goes first: the pass writes the startable bugs it leaves without a run to $SG_STATE/bugs-waiting, which
# the frontier pass reads. A pass that funds no child leaves every candidate waiting; a pass that takes them all leaves 0.
rm -rf "$T/st/auto" "$T/st/req" "$T/st/cq"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"; rm -f "$T/st/childcc-waiting" "$T/st/bugs-waiting"
printf 'print("0")\n' > "$T/s/fundroom.py"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
[ "$(cat "$T/st/bugs-waiting" 2>/dev/null)" = 1 ] && ok "21 a pass that funds no child leaves its candidate waiting" || ko "21 bugs-waiting '$(cat "$T/st/bugs-waiting" 2>/dev/null)': '$r'"
printf 'print("-")\n' > "$T/s/fundroom.py"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
[ "$(cat "$T/st/bugs-waiting" 2>/dev/null)" = 0 ] && ok "21 a pass that takes every candidate leaves 0 waiting" || ko "21 bugs-waiting '$(cat "$T/st/bugs-waiting" 2>/dev/null)': '$r'"

# 22) (#847) a rate limit at the blocker-gate read is said once, in words a person can act on, and dispatches nothing.
# Every call answers the 403 body, so the open-issue read is the first one refused. The pass names the reset (HH:MM UTC,
# or unknown) and files nothing: no refusal bug, no marker, no request. The old text named an empty candidate list.
rm -rf "$T/st/auto" "$T/st/req"; mkdir -p "$T/st/auto" "$T/st/req"; rm -f "$T/st/cycle.log"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
echo '{"message":"API rate limit exceeded for user ID 122891372.","documentation_url":"https://docs.github.com/rest/overview/rate-limits-for-the-rest-api","status":"403"}'; exit 1
J
chmod +x "$T/bin/gh"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>"$T/err22"); rc=$?
[ "$rc" = 0 ] && ok "22 a rate limit exits 0" || ko "22 a rate limit exited $rc"
$G -qE "^AUTOBUG: rate limited until ([0-9]{2}:[0-9]{2} UTC|unknown)" "$T/err22" && ok "22 the rate-limited line names the reset" || ko "22 no rate-limited line: '$(cat "$T/err22")'"
[ "$($G -c "rate limited until" "$T/err22")" = 1 ] && ok "22 the line is printed once per pass" || ko "22 the line repeats: '$(cat "$T/err22")'"
$G -q "candidate list is not a list\|not readable for the blocker gate" "$T/err22" && ko "22 the old refusal text is still printed: '$(cat "$T/err22")'" || ok "22 the old refusal text is gone"
$G -q "AUTOBUG would dispatch" <<<"$r" && ko "22 a rate limit dispatched: '$r'" || ok "22 a rate limit dispatches nothing"
m=($(find "$T/st/auto" -name 'bug-*' 2>/dev/null)); [ ${#m[@]} -eq 0 ] && ok "22 a rate limit writes no marker" || ko "22 a rate limit wrote ${m[0]##*/}"
[ -e "$T/st/cycle.log" ] && $G -q "REFUSED" "$T/st/cycle.log" && ko "22 a rate limit was filed as a refusal" || ok "22 a rate limit files no refusal"
[ -z "$(ls -A "$T/st/req" 2>/dev/null)" ] && ok "22 a rate limit builds no request" || ko "22 a rate limit built a request"

# 23) (#852) a launch the launcher refuses before any run id exists frees its claim and puts the card back to Todo, and
# names the issue and the refusal in alerts.log. Without this the claim stayed forever and the card stayed in Doing.
rm -rf "$T/st/auto" "$T/st/req"; mkdir -p "$T/st/auto" "$T/st/req"; rm -f "$T/st/cycle.log" "$T/st/alerts.log" "$T/board.calls"
todo 1228
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
rm -f "$T/st/gh-cooldown.json"   # case 22 left a rate-limit cooldown in the shared state: this case reads GitHub again
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;   # the frontier's one list read (#383)
  *"issues?state=open"*labels=bug*) jq -c '[.[]|select((.labels // [])|index("bug"))|.labels=[(.labels//[])[]|{name:.}]]' "$T/issues.json" ;;   # the door applies -q itself, so the stub answers gh's JSON, labels as objects (#847)
  *"issues?state=open"*labels=debt*) jq -c '[.[]|select((.labels // [])|index("debt"))|.labels=[(.labels//[])[]|{name:.}]]' "$T/issues.json" ;;
  *"issues?state=open"*wave*|*labels=ready-for-agent*) jq -c '[.[]|select((.labels // [])|index("ready-for-agent"))|.labels=[(.labels//[])[]|{name:.}]]' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then sed 's/"state":"OPEN"/"state":"CLOSED"/'; else cat; fi } ;;   # the door's one-issue query, as GraphQL (#847)
  *issues/*comments*) echo "[]" ;;   # a comments list is a list: the issue stub below is for the issue itself
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); jq -c --argjson n "\$n" '.[]|select(.number==\$n)' "$T/issues.json" | { if /usr/bin/grep -qx "\$n" "$T/closed" 2>/dev/null; then jq -c '.state="closed"'; else cat; fi } ;;
  *) echo "[]" ;;
esac
J
chmod +x "$T/bin/gh"
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> %s/board.calls\nexit 0\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho %s/req.json\n' "$T" > "$T/s/mkreq.sh"
printf 'import sys\nprint("refused: the first coding model gpt-6-luna is not in the router envelope, so the router would run another model.", file=sys.stderr)\nsys.exit(1)\n' > "$T/s/launch.py"
chmod +x "$T/s/board.sh" "$T/s/mkreq.sh" "$T/s/launch.py"
out=$(bash "$T/s/autobug.sh" 2>"$T/err23"); rc=$?
[ "$rc" = 0 ] && ok "23 a refused launch exits 0" || ko "23 a refused launch exited $rc: '$(cat "$T/err23")'"
[ ! -e "$T/st/auto/bug-1228" ] && ok "23 a refused launch frees auto/bug-1228" || ko "23 auto/bug-1228 kept after a refused launch"
[ ! -e "$T/st/auto/disp-1228" ] && ok "23 a refused launch frees auto/disp-1228" || ko "23 auto/disp-1228 kept after a refused launch"
$G -qE -- "--add .* Todo .*1228" "$T/board.calls" 2>/dev/null && ok "23 the card goes back to Todo" || ko "23 no card move to Todo: '$(cat "$T/board.calls" 2>/dev/null)'"
$G -q "#1228" "$T/st/alerts.log" 2>/dev/null && $G -q "router envelope" "$T/st/alerts.log" && ok "23 alerts.log names the issue and the refusal" || ko "23 alerts.log lacks the refusal: '$(cat "$T/st/alerts.log" 2>/dev/null)'"
$G -q "AUTOBUG dispatched" <<<"$out" && ko "23 a refused launch was reported as dispatched" || ok "23 a refused launch is not reported as dispatched"

# 24) (#868) the open list is read before a close lands: an issue closed after that list was read is not dispatched. The list
# still names #1500 open, its own read says closed, and the pass skips it: no dispatch, no marker.
python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d.append({"number":1500,"title":"Core: closed by hand","body":"step c.py","created_at":"2026-10-10T18:00:00Z","labels":["bug","ready-for-agent"]}); json.dump(d, open(p,"w"))' "$T/issues.json"
printf '1500\tTodo\n' >> "$T/cards.tsv"; printf '1500\n' > "$T/closed"
printf 'caps:\n  runs: 2\n' > "$T/co/docs/squad/defaults.yaml"   # two slots: a closed bug must not be passed over for the slot
printf 'import sys\nsys.exit(0)\n' > "$T/s/launch.py"   # a launch that starts: the pass keeps the claims it dispatches
r=$(bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG skip #1500: its issue is closed"*) ok "24 the skip is said, with the closed state";; *) ko "24 no closed-state skip line for #1500: '$r'";; esac
[ ! -e "$T/st/auto/bug-1500" ] && ok "24 no marker written for the closed issue" || ko "24 a marker was written for the closed issue"
[ -e "$T/st/auto/bug-1228" ] && ok "24 the open sibling is dispatched and keeps its marker" || ko "24 the open sibling has no marker: '$r'"
rm -f "$T/closed"

# 25) (#868, debt) the only bug candidate is closed: no bug waits for the frontier, and debt is still considered.
rm -rf "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"; rm -f "$T/st/bugs-waiting"
cat > "$T/issues.json" <<'JSON'
[{"number":1400,"title":"Debt: retry has no backoff","body":"<!-- debt-key:abcd -->","created_at":"2026-10-10T16:00:00Z","labels":["debt","ready-for-agent"]},
 {"number":1500,"title":"Core: closed by hand","body":"step c.py","created_at":"2026-10-10T18:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
todo 1400 1500; printf '1500\n' > "$T/closed"
printf 'caps:\n  runs: 2\n' > "$T/co/docs/squad/defaults.yaml"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
[ "$(cat "$T/st/bugs-waiting" 2>/dev/null)" = 0 ] && ok "25 a closed bug leaves no bug waiting" || ko "25 bugs-waiting '$(cat "$T/st/bugs-waiting" 2>/dev/null)': '$r'"
case "$r" in *"would dispatch"*"[1400]"*) ok "25 debt is considered when the only bug is closed";; *) ko "25 debt not dispatched: '$r'";; esac
rm -f "$T/closed"

# 26-29) (#1021) the card write is an event the ledger holds pending before the write, and dispatch waits for it. The
# event's write lands before the launch (26); a launch refusal after that write puts the card back to Todo through the
# same door (27); a crash between the event and its write is recovered by the next pass, which writes the card and
# launches the stored request, never a second run (28); a recovery write that is refused launches nothing and stays
# pending (29). The stubs log the order of the board writes and the launches.
ab_fresh() {   # the pass's state and stubs, one issue (#1228) in Todo, one run slot
  rm -rf "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks" "$T/st/state" "$T/launches" "$T/order" "$T/board.fail" "$T/launch.fail"
  mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"; : > "$T/board.calls"; : > "$T/st/cycle.log"; : > "$T/st/active-runs.txt"
  printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
  printf '[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]\n' > "$T/issues.json"
  rm -f "$T/closed"; todo 1228
}
cat > "$T/s/board.sh" <<J
#!/bin/bash
echo "board \$*" >> "$T/order"; echo "\$*" >> "$T/board.calls"
if [ -e "$T/board.fail" ]; then echo "#1228: GitHub refused the card write (HTTP 403)" >&2; exit 1; fi
case " \$* " in *" --add "*) echo "#1228 -> \$5" ;; *) echo "#1228 not on the board" >&2; exit 1 ;; esac
J
cat > "$T/s/mkreq.sh" <<J
#!/bin/bash
printf '{}' > "$T/st/req/\$1.json"; echo "$T/st/req/\$1.json"
J
cat > "$T/s/launch.py" <<J
import os, sys
open("$T/order", "a").write("launch " + sys.argv[1] + "\n")
open("$T/launches", "a").write(sys.argv[1] + "\n")
if os.path.exists("$T/launch.fail"): print("refused: test launch refusal"); sys.exit(1)
J
chmod +x "$T/s/board.sh" "$T/s/mkreq.sh" "$T/s/launch.py"
export BOARD="$T/s/board.sh"
L1() { SG_STATE=$T/st SG_REPO=x/y python3 "$T/s/issue_ledger.py" "$@"; }
ab_fresh
r=$(bash "$T/s/autobug.sh" 2>&1)
[ "$(sed -n 1p "$T/order" 2>/dev/null | cut -d' ' -f2)" = "--add" ] && [ "$(sed -n 2p "$T/order" 2>/dev/null | cut -d' ' -f1)" = launch ] \
  && ok "26 the card write lands before the launch" || ko "26 order: '$(cat "$T/order" 2>/dev/null)'"
[ "$(L1 read 'x/y#1228' board | python3 -c 'import json,sys; b=json.load(sys.stdin); print(b.get("column",""), b.get("state",""))')" = "Doing written" ] \
  && ok "26 the ledger holds the Doing event written after its write" || ko "26 board fact: '$(L1 read 'x/y#1228' board)'"
ab_fresh; touch "$T/launch.fail"
r=$(bash "$T/s/autobug.sh" 2>&1)
grep -qx -- "--add o 4 Todo 1228" "$T/board.calls" && ok "27 a launch refusal after the Doing write puts the card back to Todo" \
  || ko "27 no Todo write: '$(cat "$T/board.calls")'"
[ ! -e "$T/st/auto/bug-1228" ] && ok "27 the refused launch frees its claim" || ko "27 the claim stays after a refused launch"
[ -z "$(L1 pending 'x/y#1228')" ] && ok "27 no pending event is left after the card is back in Todo" || ko "27 pending left: '$(L1 pending 'x/y#1228')'"
ab_fresh
L1 board 'x/y#1228' autobug-crash Doing "$T/st/req/autobug-crash.json" >/dev/null; printf '{}' > "$T/st/req/autobug-crash.json"
r=$(bash "$T/s/autobug.sh" 2>&1)
grep -qx -- "--add o 4 In Progress 1228" "$T/board.calls" && ok "28 the crash recovery writes the pending card" || ko "28 no recovery write: '$(cat "$T/board.calls")'"
[ "$(cat "$T/launches" 2>/dev/null)" = "$T/st/req/autobug-crash.json" ] && ok "28 the crash recovery launches the stored request, once" \
  || ko "28 launches: '$(cat "$T/launches" 2>/dev/null)'"
[ -z "$(L1 pending 'x/y#1228')" ] && ok "28 the recovered event is written" || ko "28 still pending: '$(L1 pending 'x/y#1228')'"
ab_fresh
L1 board 'x/y#1228' autobug-refused Doing "$T/st/req/autobug-refused.json" >/dev/null; printf '{}' > "$T/st/req/autobug-refused.json"; touch "$T/board.fail"
r=$(bash "$T/s/autobug.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "29 a refused recovery write exits 0" || ko "29 exit $rc: '$r'"
[ ! -e "$T/launches" ] && ok "29 a refused recovery write launches nothing" || ko "29 launched: '$(cat "$T/launches")'"
[ "$(L1 pending 'x/y#1228' | cut -f2)" = autobug-refused ] && ok "29 the event stays pending for the next pass" || ko "29 pending: '$(L1 pending 'x/y#1228')'"

# 30) (#1021, review) a pending Doing event is written and launched only while its issue still passes the candidate gates.
# A closed issue, a frozen:owner issue and a card the owner moved out of Todo drop the event: nothing is written to Doing,
# nothing launches, and the card stays where it is. A card that already reads Doing (its write landed before the ledger
# was marked) still launches its stored request once.
pending_doing() {   # <run>: one pending Doing event for #1228, with its stored request
  L1 board 'x/y#1228' "$1" Doing "$T/st/req/$1.json" >/dev/null; printf '{}' > "$T/st/req/$1.json"
}
ab_fresh; pending_doing autobug-closed; echo 1228 > "$T/closed"
r=$(bash "$T/s/autobug.sh" 2>&1)
[ ! -e "$T/launches" ] && ok "30 a pending Doing event on a closed issue does not launch" || ko "30 a closed issue launched: '$(cat "$T/launches")'"
$G -q 'In Progress' "$T/board.calls" && ko "30 a closed issue's card was written to Doing" || ok "30 a closed issue's card is not written to Doing"
[ -z "$(L1 pending 'x/y#1228')" ] && ok "30 the closed issue's pending event is dropped" || ko "30 still pending: '$(L1 pending 'x/y#1228')'"
[ ! -e "$T/st/auto/bug-1228" ] && ok "30 the closed issue frees its claim" || ko "30 the claim stays for a closed issue"
ab_fresh; pending_doing autobug-frozen
jq '.[0].labels += ["frozen:owner"]' "$T/issues.json" > "$T/issues.tmp" && mv "$T/issues.tmp" "$T/issues.json"
r=$(bash "$T/s/autobug.sh" 2>&1)
[ ! -e "$T/launches" ] && ! $G -q 'In Progress' "$T/board.calls" && ok "30 a pending Doing event on a frozen:owner issue does not launch" \
  || ko "30 a frozen:owner issue launched or wrote Doing: '$(cat "$T/launches" 2>/dev/null)' / $(cat "$T/board.calls")"
[ -z "$(L1 pending 'x/y#1228')" ] && ok "30 the frozen issue's pending event is dropped" || ko "30 frozen still pending: '$(L1 pending 'x/y#1228')'"
ab_fresh; pending_doing autobug-moved; printf '1228\tBlocked\n' > "$T/cards.tsv"
r=$(bash "$T/s/autobug.sh" 2>&1)
[ ! -e "$T/launches" ] && ! $G -q 'In Progress\|Todo' "$T/board.calls" && ok "30 a card the owner moved out of Todo is not written and does not launch" \
  || ko "30 moved card: launches '$(cat "$T/launches" 2>/dev/null)', writes '$(cat "$T/board.calls")'"
[ -z "$(L1 pending 'x/y#1228')" ] && ok "30 the moved card's pending event is dropped" || ko "30 moved still pending: '$(L1 pending 'x/y#1228')'"
ab_fresh; pending_doing autobug-landed; printf '1228\tIn Progress\n' > "$T/cards.tsv"
r=$(bash "$T/s/autobug.sh" 2>&1)
[ "$(cat "$T/launches" 2>/dev/null)" = "$T/st/req/autobug-landed.json" ] && ok "30 a card already in Doing still launches its stored request, once" \
  || ko "30 a landed write did not launch: '$(cat "$T/launches" 2>/dev/null)' / $r"
[ -z "$(L1 pending 'x/y#1228')" ] && ok "30 the landed event is marked written" || ko "30 landed still pending: '$(L1 pending 'x/y#1228')'"
[ "$(L1 read 'x/y#1228' board | python3 -c 'import json,sys; print(json.load(sys.stdin).get("by",""))')" = "autobug resume" ] \
  && ok "30 the resume pass names itself as the actor of the landed write" || ko "30 landed write actor: '$(L1 read 'x/y#1228' board)'"
# The column plus no holder is the candidate rule (#1679, ADR-0142, ADR-0165): a pending Doing event whose Todo card
# carries no triage label and no holder still launches its stored request. The go label is the pass's record, never a
# gate the resume reads.
ab_fresh; pending_doing autobug-untriaged
jq '.[0].labels -= ["ready-for-agent"]' "$T/issues.json" > "$T/issues.tmp" && mv "$T/issues.tmp" "$T/issues.json"
r=$(bash "$T/s/autobug.sh" 2>&1)
[ "$(cat "$T/launches" 2>/dev/null)" = "$T/st/req/autobug-untriaged.json" ] \
  && ok "30 a pending Doing event on a Todo card with no holder launches, with no go label" \
  || ko "30 an untriaged Todo card did not launch: '$(cat "$T/launches" 2>/dev/null)' / $r"
[ -z "$(L1 pending 'x/y#1228')" ] && ok "30 the untriaged event is marked written" || ko "30 untriaged still pending: '$(L1 pending 'x/y#1228')'"

# 31) (#1021, review) a pick whose ledger records fail partway records nothing: the events already recorded are dropped, so
# no later resume launches part of the pick. The ledger refuses the second issue's event only (a flag file arms it).
ab_fresh; cp "$T/s/issue_ledger.py" "$T/s/issue_ledger-real.py"
printf '[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]},{"number":1229,"title":"Core: second null deref","body":"step b.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]\n' > "$T/issues.json"
printf '1228\tTodo\n1229\tTodo\n' > "$T/cards.tsv"; printf 'caps:\n  runs: 2\n' > "$T/co/docs/squad/defaults.yaml"
cat > "$T/s/issue_ledger.py" <<PY
import os, subprocess, sys
if sys.argv[1:3] == ["board", "x/y#1229"] and os.path.exists("$T/fail-1229"): sys.exit(3)
sys.exit(subprocess.call([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "issue_ledger-real.py")] + sys.argv[1:]))
PY
touch "$T/fail-1229"
r=$(bash "$T/s/autobug.sh" 2>&1)
[ ! -e "$T/launches" ] && ok "31 a pick whose ledger records fail partway launches nothing" || ko "31 launched a partial pick: '$(cat "$T/launches")'"
$G -q 'In Progress' "$T/board.calls" && ko "31 a partial pick wrote Doing" || ok "31 a partial pick writes no card to Doing"
[ "$(L1 read 'x/y#1228' board | python3 -c 'import json,sys; b=json.load(sys.stdin); print(b.get("column",""), b.get("state",""))')" = "Doing dropped" ] \
  && ok "31 the event recorded before the failure is dropped" || ko "31 recorded event: '$(L1 read 'x/y#1228' board)'"
[ -z "$(L1 pending 'x/y#1228')" ] && [ ! -e "$T/st/auto/bug-1228" ] && ok "31 no pending event and no claim are left behind" || ko "31 left pending or claimed"
rm -f "$T/fail-1229"; mv "$T/s/issue_ledger-real.py" "$T/s/issue_ledger.py"

# 32) (#1358, ADR-0171) a loop-bug candidate is built only after an approving plan review of its current body. With no review
# the pass names the issue and dispatches nothing; an approval of this body dispatches it; an approval of an older body is
# stale, and a CHANGES review holds it. The dry pass names what it would dispatch.
ab_fresh; . "$d/loopbug.sh"; printf 'caps:\n  runs: 4\n' > "$T/co/docs/squad/defaults.yaml"
cat > "$T/issues.json" <<'JSON'
[
  {"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]},
  {"number":596,"title":"The loop cannot build its own fix","body":"step loop.py","created_at":"2026-10-18T08:00:00Z","labels":["bug","ready-for-agent","loop-bug"]}
]
JSON
todo 1228 596
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$T/issues.json" ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues?state=open"*labels=bug*) jq -c '[.[]|select(.labels|index("bug"))|.labels=[.labels[]|{name:.}]]' "$T/issues.json" ;;
  *"issues?state=open"*) jq -c '[.[]|.labels=[.labels[]|{name:.}]]' "$T/issues.json" ;;
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); jq -c --argjson n "\$n" '.[]|select(.number==\$n)' "$T/issues.json" ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"
body_h=$(lb_body_hash "step loop.py")
rm -f "$T/st/review-x_y#596."*; r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG skip #596: loop-bug, no approving plan review"*) ok "32 a loop-bug with no plan review is named and held";; *) ko "32 no plan-review skip line: '$r'";; esac
case "$r" in *"[596]"*) ko "32 a loop-bug with no plan review was dispatched: '$r'";; *) ok "32 the held loop-bug is not dispatched";; esac
case "$r" in *"[1228]"*) ok "32 a bug without the loop-bug label is still dispatched";; *) ko "32 the plain bug was held: '$r'";; esac
printf 'VERDICT: APPROVE\n' > "$T/st/review-x_y#596.md"; printf '%s\n' "$body_h" > "$T/st/review-x_y#596.sha"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[596]"*) ok "32 an approval of the current body dispatches the loop-bug";; *) ko "32 the approved loop-bug was not dispatched: '$r'";; esac
printf '%s\n' "$(lb_body_hash "an older body")" > "$T/st/review-x_y#596.sha"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG skip #596: loop-bug, no approving plan review of its current body (stale)"*) ok "32 an approval of an older body is stale and holds the loop-bug";; *) ko "32 a stale approval did not hold: '$r'";; esac
printf 'VERDICT: CHANGES\n' > "$T/st/review-x_y#596.md"; printf '%s\n' "$body_h" > "$T/st/review-x_y#596.sha"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"(changes)"*) ok "32 a CHANGES plan review holds the loop-bug";; *) ko "32 a CHANGES review did not hold: '$r'";; esac
rm -f "$T/st/review-x_y#596."*
# 32b) (#1708, ADR-0146) with the loop switch beside the pass, any open loop-bug drains the loop: the pass asks for the drain
# through `watchd.sh loopbug`, names the issue and dispatches nothing, a plan approval included. The approval releases the
# hand build, never a dispatch.
cp "$d/watchd.sh" "$T/s/"; printf 'VERDICT: APPROVE\n' > "$T/st/review-x_y#596.md"; printf '%s\n' "$body_h" > "$T/st/review-x_y#596.sha"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"LOOP STOP: #596 is a loop defect; the fix lands outside the loop"*) ok "32b a bare loop-bug stops the bug pass and is named";; *) ko "32b no stop for a bare loop-bug: '$r'";; esac
case "$r" in *"[596]"*|*"[1228]"*) ko "32b the pass dispatched under an open loop-bug: '$r'";; *) ok "32b an approved loop-bug is not dispatched, and nothing else is";; esac
printf '#!/bin/bash\necho "$1" >> "%s/loopbug.calls"; exit 2\n' "$T" > "$T/bin/lbcmd"; chmod +x "$T/bin/lbcmd"
r=$(SG_LOOPBUG_CMD="$T/bin/lbcmd" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
[ -s "$T/loopbug.calls" ] && ok "32b the pass hands its issue list to the drain's one writer" || ko "32b the pass asked for no drain"
case "$r" in *"LOOP STOP"*) ko "32b an unknown answer stopped the pass: '$r'";; *) ok "32b a list the rule cannot read asks for nothing and stops nothing";; esac
rm -f "$T/s/watchd.sh" "$T/st/review-x_y#596."* "$T/loopbug.calls"

# 33) (#1516) the rate-limited line names the reset of the resource that IS exhausted, never core's. The read refuses with
# the rate-limit body, and gh api rate_limit answers a body whose core has thousands left while graphql and search sit at
# zero: the line must name the earliest reset among the zero ones (22:13 UTC here), not core's 08:00 UTC.
rm -rf "$T/st/auto" "$T/st/req"; mkdir -p "$T/st/auto" "$T/st/req"; rm -f "$T/st/cycle.log"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *rate_limit*) echo '{"resources":{"core":{"limit":5000,"remaining":4551,"reset":1800000000},"graphql":{"limit":5000,"remaining":0,"reset":1700000000},"search":{"limit":30,"remaining":0,"reset":1850000000}}}' ;;
  *) echo '{"message":"API rate limit exceeded for user ID 122891372.","documentation_url":"https://docs.github.com/rest/overview/rate-limits-for-the-rest-api","status":"403"}'; exit 1 ;;
esac
J
chmod +x "$T/bin/gh"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>"$T/err33"); rc=$?
[ "$rc" = 0 ] && ok "33 an exhausted resource exits 0" || ko "33 the pass exited $rc: '$(cat "$T/err33")'"
$G -qE "^AUTOBUG: rate limited until 22:13 UTC;" "$T/err33" && ok "33 the line names the earliest reset of an exhausted resource" || ko "33 wrong reset: '$(cat "$T/err33")'"

# 34) (#1582) every autobug pass writes exactly one line to cycle.log, so a stopped bug lane is not read as an idle one.
# The line names what the pass dispatched, or the first reason it dispatched nothing, in this order: loop off; no free
# run slot; a pending card event (ADR-0165); no startable bug. One line per pass, never one per issue.
PL=$T/passline; mkdir -p "$PL/bin" "$PL/st/auto" "$PL/co/docs/squad/workflows" "$PL/s" "$T/caps/runs"
cp "$d/autobug.sh" "$d/loopbug.sh" "$d/cluster.py" "$d/single.sh" "$d/startability.sh" "$d/skiplabels.sh" "$d/ghcall.sh" "$d/refusal-names.sh" "$d/workbranches.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/ghthrottle.py" "$PL/s/"
: > "$PL/st/active-runs.txt"
printf 'caps:\n  runs: 1\n' > "$PL/co/docs/squad/defaults.yaml"
printf 'export SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_STATE=%s SG_BASE=main SG_CHECKOUT=%s SG_FRONTIER_LABEL=ready-for-agent SG_BUG_LABEL=bug\n' "$PL/st" "$PL/co" > "$PL/s/env.sh"
printf '#!/bin/bash\necho READY\n' > "$PL/s/stack.sh"; chmod +x "$PL/s/stack.sh"
printf 'print("-")\n' > "$PL/s/fundroom.py"
printf '#!/bin/bash\necho "#%s -> In Progress"\n' "\$5" > "$PL/s/board.sh"; chmod +x "$PL/s/board.sh"
printf '#!/bin/bash\nmkdir -p "$SG_STATE/req"; echo "{}" > "$SG_STATE/req/$1.json"; echo "$SG_STATE/req/$1.json"\n' > "$PL/s/mkreq.sh"; chmod +x "$PL/s/mkreq.sh"
printf 'import sys\nsys.exit(0)\n' > "$PL/s/launch.py"; chmod +x "$PL/s/launch.py"
cat > "$PL/s/issue_state.py" <<PY
import sys
if sys.argv[1:2] not in ([], ["--go"]): sys.exit(1)
try: sys.stdout.write(open("$PL/cards.tsv").read())
except OSError: print("no board", file=sys.stderr); sys.exit(1)
PY
printf '1601\tTodo\n1602\tTodo\n' > "$PL/cards.tsv"
# two open bugs whose bodies name one shared file: one root cause, so one pass picks both (a per-issue line would be two)
cat > "$PL/issues.json" <<'JSON'
[
  {"number":1601,"title":"Core: null deref","body":"step src/core.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]},
  {"number":1602,"title":"Core: other crash","body":"step src/core.py","created_at":"2026-10-10T15:05:00Z","labels":["bug","ready-for-agent"]}
]
JSON
cat > "$PL/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$PL/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$PL/issues.json" ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues?state=open"*labels=bug*) jq -c 'map(select(.labels|index("bug"))|.labels=[.labels[]|{name:.}])' "$PL/issues.json" ;;
  *"issues?state=open"*) jq -c 'map(.labels=[.labels[]|{name:.}])' "$PL/issues.json" ;;
  *issues/*/comments*) echo "[]" ;;
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); jq -c --argjson n "\$n" '.[]|select(.number==\$n)' "$PL/issues.json" ;;
  *) echo "" ;;
esac
J
chmod +x "$PL/bin/gh"
pl_fresh() {   # the counter and the log of one pass
  rm -f "$PL/st/auto/pass"; : > "$PL/st/cycle.log"; rm -rf "$PL/st/auto/bug-"* "$PL/st/state"; : > "$T/caps/runs/.empty"
  rm -f "$T/caps/runs/p-full.json"
}
pl_run() { PATH="$PL/bin:$PATH" SG_SQUAD="$SG_SQUAD" BOARD="$PL/s/board.sh" bash "$PL/s/autobug.sh" 2>&1; }
# 34a: two open bugs, the run cap full: nothing is dispatched, and the pass says so with its first reason
pl_fresh; printf '{"pid":%s,"holder":"passline-slot"}\n' "$$" > "$T/caps/runs/p-full.json"
r=$(pl_run); rc=$?
[ "$rc" = 0 ] && ok "34a a full-cap pass exits 0" || ko "34a the pass exited $rc: '$r'"
$G -qxF 'AUTOBUG pass 1: none (no slot)' "$PL/st/cycle.log" && ok "34a a full-cap pass writes 'none (no slot)'" \
  || ko "34a cycle.log: '$(cat "$PL/st/cycle.log")'"
[ "$($G -c '^AUTOBUG pass ' "$PL/st/cycle.log")" = 1 ] && ok "34a the pass writes one line, never one per issue" \
  || ko "34a the line repeats: '$(cat "$PL/st/cycle.log")'"
# 34b: the same two bugs with a free slot: the pass dispatches, and names the issue it took
pl_fresh
r=$(pl_run); rc=$?
[ "$rc" = 0 ] && ok "34b a dispatching pass exits 0" || ko "34b the pass exited $rc: '$r'"
$G -qE '^AUTOBUG pass 1: dispatched #160[12]$' "$PL/st/cycle.log" && ok "34b a dispatching pass writes what it dispatched" \
  || ko "34b cycle.log: '$(cat "$PL/st/cycle.log")'"
[ "$($G -c '^AUTOBUG pass ' "$PL/st/cycle.log")" = 1 ] && ok "34b the pass writes one line for two issues, never one each" \
  || ko "34b the line repeats: '$(cat "$PL/st/cycle.log")'"
# 34c: no bug to take: the pass names that reason, not an idle silence
pl_fresh; printf '[]\n' > "$PL/issues.json"
r=$(pl_run)
$G -qxF 'AUTOBUG pass 1: none (no startable bug)' "$PL/st/cycle.log" && ok "34c an idle pass says no startable bug" \
  || ko "34c cycle.log: '$(cat "$PL/st/cycle.log")'"
# 34d: the loop switch off: the pass says loop off, and says it once
pl_fresh; : > "$PL/st/auto/off"
r=$(pl_run)
$G -qxF 'AUTOBUG pass 1: none (loop off)' "$PL/st/cycle.log" && ok "34d a pass with the loop off says loop off" \
  || ko "34d cycle.log: '$(cat "$PL/st/cycle.log")'"
[ "$($G -c '^AUTOBUG pass ' "$PL/st/cycle.log")" = 1 ] && ok "34d the loop-off line is written once" \
  || ko "34d the line repeats: '$(cat "$PL/st/cycle.log")'"
rm -f "$PL/st/auto/off"

# 35-39) (#1677, SPEC 1672) the bug pass follows the frontier's triage rule: the column Todo is the one go signal, so a bug
# with no triage label is claimed and dispatched, and the pass writes ready-for-agent on it once, before the pending Doing
# event (ADR-0165) -- a refused write leaves no event, and the next pass retries the bug. A holder label still holds and
# costs no write, a bug that already carries the label costs none either, and bugs-waiting counts an untriaged Todo bug.
# The fixture records one call order: every GitHub call, every ledger call and the launch land in the same file.
TR=$T/triage; mkdir -p "$TR/bin" "$TR/st/auto" "$TR/st/req" "$TR/st/cq" "$TR/st/locks" "$TR/co/docs/squad/workflows" "$TR/s"
cp "$d/autobug.sh" "$d/loopbug.sh" "$d/cluster.py" "$d/slicecut.py" "$d/liveholds.py" "$d/single.sh" "$d/startability.sh" "$d/skiplabels.sh" "$d/ghcall.sh" "$d/refusal-names.sh" "$d/workbranches.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/ghthrottle.py" "$TR/s/"
: > "$TR/st/active-runs.txt"
printf 'caps:\n  runs: 2\n' > "$TR/co/docs/squad/defaults.yaml"
printf 'export SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_STATE=%s SG_BASE=main SG_CHECKOUT=%s SG_FRONTIER_LABEL=ready-for-agent SG_BUG_LABEL=bug\n' "$TR/st" "$TR/co" > "$TR/s/env.sh"
printf '#!/bin/bash\necho READY\n' > "$TR/s/stack.sh"; chmod +x "$TR/s/stack.sh"
printf 'print("-")\n' > "$TR/s/fundroom.py"
cat > "$TR/s/issue_state.py" <<PY
import sys
if sys.argv[1:2] not in ([], ["--go"]): sys.exit(1)
try: sys.stdout.write(open("$TR/cards.tsv").read())
except OSError: print("no board", file=sys.stderr); sys.exit(1)
PY
# The ledger spy (#1677): every call to the one writer lands in the pass's call order, beside every GitHub call, so the
# order of the label write and the pending Doing event is read from what the pass did, never inferred.
mv "$TR/s/issue_ledger.py" "$TR/s/issue_ledger-real.py"
cat > "$TR/s/issue_ledger.py" <<PY
import os, subprocess, sys
_real = os.path.join(os.path.dirname(os.path.abspath(__file__)), "issue_ledger-real.py")
if __name__ != "__main__":   # imported by startability.sh and the pass: the real module answers, the spy writes nothing
    import importlib.util
    _spec = importlib.util.spec_from_file_location("issue_ledger_real", _real)
    _m = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_m)
    globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
else:
    open("$TR/calls", "a").write("ledger " + " ".join(sys.argv[1:]) + "\n")
    sys.exit(subprocess.call([sys.executable, _real] + sys.argv[1:]))
PY
printf '#!/bin/bash\necho "board $*" >> %s/calls\nexit 0\n' "$TR" > "$TR/s/board.sh"
printf '#!/bin/bash\nmkdir -p "$SG_STATE/req"; echo "{}" > "$SG_STATE/req/$1.json"; echo "$SG_STATE/req/$1.json"\n' > "$TR/s/mkreq.sh"
printf 'import sys\nopen("%s/calls", "a").write("launch " + sys.argv[1] + "\\n")\n' "$TR" > "$TR/s/launch.py"
chmod +x "$TR/s/board.sh" "$TR/s/mkreq.sh" "$TR/s/launch.py"
cat > "$TR/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$TR/issues.json" ;;
  *"issue(number:"*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issue\(number: [0-9]+' | /usr/bin/grep -oE '[0-9]+' | head -1); jq -c --arg k "i\$n" --argjson n "\$n" '{data:{repository:{(\$k): (.[]|select(.number==\$n)|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}})}}}' "$TR/issues.json" ;;
  *pulls?state=open*) echo "[]" ;;
  *issues/*/labels*) echo "labels \$*" >> "$TR/calls"; if [ -e "$TR/label.fail" ]; then printf '%s' '{"message":"Bad credentials","status":401}'; exit 1; fi; echo '[]' ;;
  *"issues?state=open"*labels=bug*) jq -c '[.[]|select((.labels // [])|index("bug"))|.labels=[(.labels//[])[]|{name:.}]]' "$TR/issues.json" ;;
  *"issues?state=open"*labels=debt*) jq -c '[.[]|select((.labels // [])|index("debt"))|.labels=[(.labels//[])[]|{name:.}]]' "$TR/issues.json" ;;
  *"issues?state=open"*) jq -c '[.[]|.labels=[(.labels//[])[]|{name:.}]]' "$TR/issues.json" ;;
  *issues/*/comments*) echo "[]" ;;   # a comments list is a list: the issue stub below is for the issue itself
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); jq -c --argjson n "\$n" '.[]|select(.number==\$n)' "$TR/issues.json" ;;
  *) echo "[]" ;;
esac
J
chmod +x "$TR/bin/gh"
tr_fresh() {   # this pass's state empty, and one open bug in Todo unless the case writes its own list
  rm -rf "$TR/st/auto" "$TR/st/req" "$TR/st/cq" "$TR/st/locks" "$TR/st/state"; mkdir -p "$TR/st/auto" "$TR/st/req" "$TR/st/cq" "$TR/st/locks"
  : > "$TR/calls"; rm -f "$TR/label.fail" "$TR/st/bugs-waiting"; : > "$TR/st/cycle.log"
  printf '1701\tTodo\n' > "$TR/cards.tsv"
  printf '[{"number":1701,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug"]}]\n' > "$TR/issues.json"
}
tr_run() { PATH="$TR/bin:$PATH" SG_SQUAD="$SG_SQUAD" BOARD="$TR/s/board.sh" bash "$TR/s/autobug.sh" 2>&1; }
tr_ledger() { SG_STATE=$TR/st SG_REPO=x/y python3 "$TR/s/issue_ledger-real.py" "$@"; }
tr_at() { $G -n "$1" "$TR/calls" | head -1 | cut -d: -f1; }   # the first place a call of this kind lands in the pass's order

tr_fresh
r=$(tr_run); rc=$?
[ "$rc" = 0 ] && ok "35 an untriaged bug pass exits 0" || ko "35 exit $rc: '$r'"
[ "$($G -c '^labels .*issues/1701/labels' "$TR/calls")" = 1 ] && ok "35 the untriaged Todo bug is dispatched with exactly one label write" \
  || ko "35 label writes: '$($G '^labels ' "$TR/calls")'"
$G -q '^labels .*labels\[\]=ready-for-agent' "$TR/calls" && ok "35 the one write carries ready-for-agent" || ko "35 the write's argument: '$($G '^labels ' "$TR/calls")'"
a=$(tr_at '^labels .*issues/1701/labels'); b=$(tr_at '^ledger board x/y#1701 .* Doing ')
[ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ] && ok "35 the label write lands before the pending Doing event (ADR-0165)" \
  || ko "35 order (label at ${a:-none}, Doing event at ${b:-none}): '$(cat "$TR/calls")'"
$G -q '^AUTOBUG triaged #1701: wrote ready-for-agent$' <<<"$r" && ok "35 the pass prints one line for the card it triaged" || ko "35 no triage line: '$r'"
$G -q '^CHILD — GitHub issue(s) 1701 ' "$TR/st/req/"*.body 2>/dev/null && ok "35 the request names the triaged bug" || ko "35 request: '$(cat "$TR/st/req/"*.body 2>/dev/null)'"
[ -e "$TR/st/auto/bug-1701" ] && [ -n "$($G '^launch ' "$TR/calls")" ] && ok "35 the triaged bug is claimed and its run launched" \
  || ko "35 no claim or launch for #1701: '$r'"

# 36) a Todo bug carrying a holder label dispatches nothing and issues no label write; a card outside Todo is taken by no
# pass, untriaged or not.
tr_fresh
printf '1711\tTodo\n1712\tTodo\n1713\tBacklog\n' > "$TR/cards.tsv"
printf '[{"number":1711,"title":"Bug for a person","body":"a.py","created_at":"2026-10-10T15:01:00Z","labels":["bug","ready-for-human"]},{"number":1712,"title":"Bug the owner holds","body":"b.py","created_at":"2026-10-10T15:02:00Z","labels":["bug","needs-owner"]},{"number":1713,"title":"Bug in Backlog","body":"c.py","created_at":"2026-10-10T15:03:00Z","labels":["bug"]}]\n' > "$TR/issues.json"
r=$(tr_run); rc=$?
[ "$rc" = 0 ] && ok "36 a pass with only held bugs exits 0" || ko "36 exit $rc: '$r'"
m=($(find "$TR/st/auto" -name 'bug-*' 2>/dev/null)); [ ${#m[@]} -eq 0 ] && ok "36 a holder label dispatches nothing" || ko "36 markers: ${m[*]##*/}"
[ "$($G -c '^labels ' "$TR/calls")" = 0 ] && ok "36 a holder label costs no label write" || ko "36 writes: '$($G '^labels ' "$TR/calls")'"
[ -z "$(ls "$TR/st/req/"*.body 2>/dev/null)" ] && [ ! -e "$TR/st/auto/bug-1713" ] && ok "36 a card outside Todo is never taken" \
  || ko "36 a card outside Todo went out: '$(cat "$TR/st/req/"*.body 2>/dev/null)'"

# 37) a bug that already carries ready-for-agent is dispatched with no label write: the record is already there.
tr_fresh
printf '[{"number":1721,"title":"Plain bug","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]\n' > "$TR/issues.json"
printf '1721\tTodo\n' > "$TR/cards.tsv"
r=$(tr_run)
[ -e "$TR/st/auto/bug-1721" ] && ok "37 a bug that already carries the label is dispatched" || ko "37 no dispatch: '$r'"
[ "$($G -c '^labels ' "$TR/calls")" = 0 ] && ok "37 a bug that already carries the label costs no write" || ko "37 writes: '$($G '^labels ' "$TR/calls")'"

# 38) a refused label write leaves no pending Doing event, frees the claim, and the next pass retries the bug.
tr_fresh; touch "$TR/label.fail"
r=$(tr_run); rc=$?
[ "$rc" = 0 ] && ok "38 a refused triage write exits 0" || ko "38 exit $rc: '$r'"
case "$r" in *"AUTOBUG triage failed #1701:"*) ok "38 the pass names the refused write and the card that waits";; *) ko "38 no failure line: '$r'";; esac
[ "$($G -c '^ledger board x/y#1701 ' "$TR/calls")" = 0 ] && ok "38 a refused write leaves no pending Doing event" || ko "38 the event was recorded: '$(cat "$TR/calls")'"
[ -z "$(tr_ledger pending x/y | awk -F'\t' '$1==1701')" ] && ok "38 the ledger holds no pending event for the bug" || ko "38 pending: '$(tr_ledger pending x/y)'"
[ -z "$(ls "$TR/st/req/"*.body 2>/dev/null)" ] && ok "38 a refused write writes no request" || ko "38 a request was written: '$(cat "$TR/st/req/"*.body 2>/dev/null)'"
[ ! -e "$TR/st/auto/bug-1701" ] && ok "38 a refused write frees the claim" || ko "38 the claim stays"
[ "$(cat "$TR/st/bugs-waiting" 2>/dev/null)" = 1 ] && ok "38 the refused bug counts as waiting" || ko "38 bugs-waiting: '$(cat "$TR/st/bugs-waiting" 2>/dev/null)'"
: > "$TR/calls"; rm -f "$TR/label.fail"
r=$(tr_run)
[ -e "$TR/st/auto/bug-1701" ] && [ "$($G -c '^labels .*issues/1701/labels' "$TR/calls")" = 1 ] \
  && ok "38 the next pass retries the bug and writes its label once" || ko "38 retry: '$(cat "$TR/calls")' / '$r'"

# 39) bugs-waiting counts a Todo bug with no holder label, and no bug a holder label keeps back.
tr_fresh; printf 'print("0")\n' > "$TR/s/fundroom.py"   # one run funds no child: every candidate waits
r=$(AB_DRY=1 PATH="$TR/bin:$PATH" SG_SQUAD="$SG_SQUAD" BOARD="$TR/s/board.sh" bash "$TR/s/autobug.sh" 2>&1)
[ "$(cat "$TR/st/bugs-waiting" 2>/dev/null)" = 1 ] && ok "39 bugs-waiting counts a Todo bug with no holder label" \
  || ko "39 bugs-waiting '$(cat "$TR/st/bugs-waiting" 2>/dev/null)': '$r'"
tr_fresh; printf 'print("0")\n' > "$TR/s/fundroom.py"
printf '[{"number":1731,"title":"Bug for a person","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-human"]}]\n' > "$TR/issues.json"
printf '1731\tTodo\n' > "$TR/cards.tsv"
r=$(AB_DRY=1 PATH="$TR/bin:$PATH" SG_SQUAD="$SG_SQUAD" BOARD="$TR/s/board.sh" bash "$TR/s/autobug.sh" 2>&1)
[ "$(cat "$TR/st/bugs-waiting" 2>/dev/null)" = 0 ] && ok "39 a bug a holder label keeps back is not counted as waiting" \
  || ko "39 bugs-waiting '$(cat "$TR/st/bugs-waiting" 2>/dev/null)': '$r'"
printf 'print("-")\n' > "$TR/s/fundroom.py"

# 40) (#2016, ADR-0150 across runs) the bug lane reads the files a live run holds, with the frontier's own issue-to-file map
# (slicecut.py over cluster.paths, never a second copy). A bug whose body names a file a live run holds waits for a later
# pass, and the line names the holding run. On 2026-11-16 the pass dispatched #2015 beside the live run of #1841, and both
# changed janitor.py and its board-reconcile suite. A live run on another file holds nothing.
tr_fresh
mkdir -p "$TR/co/runs/run-1741"
printf 'CHILD — GitHub issue(s) 1741 (bug; one root cause, one branch):\n' > "$TR/co/runs/run-1741/request.txt"
printf '{"event": "spawn_accepted", "child": "c1", "task": "GitHub issue #1741"}\n' > "$TR/co/runs/run-1741/record.jsonl"
printf '%s\n' run-1741 > "$TR/st/active-runs.txt"
printf '[{"number":1740,"title":"Bug on the held file","body":"fix contrib/squad-goal/scripts/janitor.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]},{"number":1741,"title":"Live work","body":"edit contrib/squad-goal/scripts/janitor.py","created_at":"2026-10-10T15:01:00Z","labels":["bug","ready-for-agent"]}]\n' > "$TR/issues.json"
printf '1740\tTodo\n1741\tTodo\n' > "$TR/cards.tsv"
r=$(AB_DRY=1 PATH="$TR/bin:$PATH" SG_SQUAD="$SG_SQUAD" BOARD="$TR/s/board.sh" bash "$TR/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG skip #1740: contrib/squad-goal/scripts/janitor.py is held by run run-1741"*)
  ok "40 a bug whose file a live run holds says so, and names the holding run";; *) ko "40 no hold line naming the run: '$r'";; esac
case "$r" in *"[1740]"*) ko "40 the bug a live run's file holds was dispatched: '$r'";; *) ok "40 the held bug is not dispatched";; esac
printf '[{"number":1740,"title":"Bug on the held file","body":"fix contrib/squad-goal/scripts/janitor.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]},{"number":1741,"title":"Live work","body":"edit docs/adr/0149-one-writer-per-loop-fact.md","created_at":"2026-10-10T15:01:00Z","labels":["bug","ready-for-agent"]}]\n' > "$TR/issues.json"
r=$(AB_DRY=1 PATH="$TR/bin:$PATH" SG_SQUAD="$SG_SQUAD" BOARD="$TR/s/board.sh" bash "$TR/s/autobug.sh" 2>&1)
case "$r" in *"AUTOBUG skip #1740"*) ko "40 a live run on another file held the bug: '$r'";; *) ok "40 a live run on another file holds nothing";; esac
case "$r" in *"[1740]"*) ok "40 the bug is dispatched when no live run holds its file";; *) ko "40 the bug stayed held with no holder: '$r'";; esac
: > "$TR/st/active-runs.txt"; rm -rf "$TR/co/runs/run-1741"

# 13) (#2052) a card write refused because its issue was deleted (HTTP 410) names the deleted card once in the cycle log
# and files no bug: GitHub has no issue to write, so a defect about it would never be fixed. Nothing launches for it.
rm -rf "$T/st/auto" "$T/st/state" "$T/st/gh-cache"; rm -f "$T"/st/gh-*.json; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$d/loopbug.sh" "$d/autohunt.sh" "$T/s/"
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
: > "$T/board.calls"
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> %s/board.calls\necho "#1228: gh: This issue was deleted (HTTP 410)" >&2; exit 1\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho %s/req.json\n' "$T" > "$T/s/mkreq.sh"
printf 'import os\nopen(os.path.join("%s", "launched"), "w").close()\n' "$T" > "$T/s/launch.py"
chmod +x "$T/s/board.sh" "$T/s/mkreq.sh" "$T/s/launch.py"
: > "$T/filed"; : > "$T/st/cycle.log"
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},parent:(if .parent then {number:.parent} else null end),comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issue(number:"*) echo '{"data":{"repository":{"i1228":{"number":1228,"title":"Core: null deref","body":"step a.py","state":"OPEN","labels":{"nodes":[]},"blockedBy":{"nodes":[]},"comments":{"nodes":[]}}}}}' ;;
  *pulls?state=open*) echo "[]" ;;
  *"issues?state=open"*labels=bug*) echo '[{"number":1228,"title":"Core: null deref","body":"step a.py","state":"open","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}]' ;;
  *"issues?state=open"*) echo "[]" ;;
  *issues/*/comments*) echo "[]" ;;
  *"--method POST"*) printf '%s\n' "\$@" >> "$T/filed"; echo '{"number":903}' ;;
  *issues/*) printf '{"number":1228,"title":"Core: null deref","body":"step a.py"}\n' ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
export BOARD="$T/s/board.sh"
r=$(bash "$T/s/autobug.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "13 the pass whose card is deleted finishes" || ko "13 the pass exited $rc: '$r'"
[ "$($G -c '^title=' "$T/filed")" = 0 ] && ok "13 a refused write for a deleted card files no bug" || ko "13 filed: $(cat "$T/filed")"
[ "$($G -c 'REFUSED' "$T/st/cycle.log")" = 0 ] && ok "13 no refusal is named for a deleted card" || ko "13 cycle.log: $(cat "$T/st/cycle.log")"
[ "$($G -c 'GONE-ISSUE .*#1228' "$T/st/cycle.log")" = 1 ] && ok "13 the deleted card is named once in the cycle log" || ko "13 no GONE-ISSUE line: $(cat "$T/st/cycle.log")"
[ ! -e "$T/launched" ] && ok "13 nothing launches for a deleted card" || ko "13 a run launched for a deleted card"

rm -rf "$T"; exit $fail
