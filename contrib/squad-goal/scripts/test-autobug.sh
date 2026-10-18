#!/bin/bash
# Regression test for autobug ranking (bug first, then debt) and autofrontier skipping debt (Refs #1494).
# Offline: stub gh. usage: bash test-autobug.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# The free slots come from the machine-wide caps (squad/caps.py) of this checkout, with leases in the sandbox, never the real ones.
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR="$T/caps"
G=/usr/bin/grep
mkdir -p "$T/bin" "$T/st/auto" "$T/co/docs/squad/workflows" "$T/s"
cp "$d/autobug.sh" "$d/autofrontier.sh" "$d/cluster.py" "$d/single.sh" "$d/startability.sh" "$d/ghcall.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/refusal-names.sh" "$d/workbranches.sh" "$d/address.sh" "$T/s/"
# The fixture reaches no network (#556): `sg_work_issues` runs `git ls-remote` on the repository's https remote,
# and an unset remote took every pass to github.com -- 0.7 s per pass at best, a stall at worst. git refuses the
# transport itself, so the read answers "no branch" at once, the same fact a 404 gives, and no pass leaves the machine.
export GIT_ALLOW_PROTOCOL=file
unset SG_BOARD_VOCAB   # the loop that runs a child exports it; the cases that need the shipped vocabulary set it themselves
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
if sys.argv[1:]: sys.exit(1)
try: sys.stdout.write(open("$T/cards.tsv").read())
except OSError: print("no board", file=sys.stderr); sys.exit(1)
PY
cp "$T/s/issue_state.py" "$T/issue_state-stub.py"
todo() { local n; : > "$T/cards.tsv"; for n in "$@"; do printf '%s\tTodo\n' "$n" >> "$T/cards.tsv"; done; }   # <issue>...: their cards sit in Todo
todo 1228 1229 1400 1401 60 61 62 70 71 72 73   # the graph names no funding bound unless a case says so
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;   # the frontier's one list read (#383)
  *"issues?state=open"*labels=bug*) jq -r '.[]|select((.labels // [])|index("bug"))|"\(.number)\u001f\(.labels|join(","))"' "$T/issues.json" ;;   # pure jq: a python startup per call is a measured cost (#556)
  *"issues?state=open"*labels=debt*) jq -r '.[]|select((.labels // [])|index("debt"))|"\(.number)\u001f\(.labels|join(","))"' "$T/issues.json" ;;
  *"issues?state=open"*wave*|*labels=ready-for-agent*) jq -r '.[]|select((.labels // [])|index("ready-for-agent"))|"wave:01\u001f\(.number)\u001f\(.labels|join(","))"' "$T/issues.json" ;;
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); jq -c --argjson n "\$n" '.[]|select(.number==\$n)' "$T/issues.json" ;;
  *) echo "" ;;
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
printf '#!/bin/bash\ntouch %s/launched\n' "$T" > "$T/s/launch.py"
chmod +x "$T/s/mkreq.sh" "$T/s/launch.py"
r=$(bash "$T/s/autobug.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "3b a refused dispatch exits 0" || ko "3b refused dispatch exited $rc: '$r'"
[ -e "$T/st/auto/bug-1400" ] && ko "3b a refused dispatch left auto/bug-1400 behind: the issue is never retried" || ok "3b a refused dispatch frees the marker it wrote"
[ -e "$T/st/auto/bug-1228" ] && ko "3b a refused dispatch left auto/bug-1228 behind" || ok "3b no stale marker for another issue either"
[ -e "$T/launched" ] && ko "3b a refused dispatch launched a run" || ok "3b a refused dispatch launches no run"

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
cp "$d/autobug.sh" "$T/s/"   # test 5 stubbed it out; put the real pass back
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto"
cat > "$T/refusal.json" <<'JSON'
{"message":"API rate limit exceeded for user ID 122891372. If you reach out to GitHub Support for help, please include the request ID 06F8:34CAED:22CD6B6:3FC4397:6ACC6C2A and timestamp 2026-10-12 05:12:10 UTC. For more on scraping GitHub and how it may affect your rights, please review our Terms of Service (https://docs.github.com/en/site-policy/github-terms/github-terms-of-service)","documentation_url":"https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api#rate-limiting","status":"403"}
JSON
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*) cat "$T/refusal.json"; exit 1 ;;                 # the rate limit: the error body on stdout, exit 1
  *issues/*) echo '{}' ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\n[ -n "\${AB_DRY:-}" ] && exit 0\ntouch %s/launched\n' "$T" > "$T/s/launch.py"
printf '#!/bin/bash\ntouch %s/req-built\n' "$T" > "$T/s/mkreq.sh"; chmod +x "$T/s/mkreq.sh" "$T/s/launch.py"
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
$G -q "not a decimal issue number" "$T/err2" && ok "6c the dropped line is logged" || ko "6c the dropped line was not logged: '$(cat "$T/err2")'"
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
cp "$d/autobug.sh" "$T/s/"
printf '#!/bin/bash\n[ -n "\${AB_DRY:-}" ] && exit 0\ntouch %s/launched\n' "$T" > "$T/s/launch.py"; chmod +x "$T/s/launch.py"
held() {   # $1: the body of the issue's comments, one line each
  cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug,ready-for-agent\n' ;;
  *"issues?state=open"*) echo "" ;;
  *issues/*/comments*) printf '%s' "$1" ;;
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

# 8) (#145) a write the loop cannot prove is a defect the loop files. The card move at the end of this pass threw its
# output away, so a refused write looked like a moved card. The pass that hit the refusal asks the filer itself, in that
# same pass: ONE naming helper builds the refusal's name and fingerprint (the title and the cycle-log line carry both, so
# a refusal named by hand would carry a fingerprint the log does not), the body carries the evidence inside one fence,
# and the issue arrives through the filer's own contract -- the [SQUAD] prefix, the "Filed by" line, the bug kind and the
# startable label. No process, no queue reader, no timer: the refused write itself asks.
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$d/autohunt.sh" "$T/s/"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> %s/board.calls\necho "#1228: GitHub refused the card write (HTTP 403)" >&2\nexit 1\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho %s/req.json\n' "$T" > "$T/s/mkreq.sh"
printf '#!/bin/bash\ntouch %s/launched\n' "$T" > "$T/s/launch.py"
chmod +x "$T/s/board.sh" "$T/s/mkreq.sh" "$T/s/launch.py"
: > "$T/filed"; : > "$T/st/cycle.log"
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug,ready-for-agent\n' ;;
  *"issues?state=open"*) echo "" ;;
  *issues/*/comments*) echo "" ;;
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
case "$r" in *"AUTOBUG dispatched"*) ok "8 the dispatch still happens" ;; *) ko "8 no dispatch: '$r'";; esac
filed=$($G -c '^title=' "$T/filed")
[ "$filed" = 1 ] && ok "8 a refused write opens exactly one bug" || ko "8 a refused write opened $filed issue(s): $(cat "$T/filed")"
TITLE=$($G '^title=' "$T/filed" | head -1)
case "$TITLE" in "title=[SQUAD] refused "*) ok "8 the bug carries the [SQUAD] prefix and the helper's name" ;; *) ko "8 title: '$TITLE'";; esac
FP=$(printf '%s' "$TITLE" | $G -oE '\[[0-9a-f]{12}\]$')
[ -n "$FP" ] && ok "8 the title carries the naming helper's fingerprint" || ko "8 no fingerprint in '$TITLE'"
$G -qF "Filed by autobug" "$T/filed" && ok "8 the bug carries the Filed by line of the pass that hit the refusal" || ko "8 no Filed by line: $(cat "$T/filed")"
$G -qF 'labels[]=bug' "$T/filed" && $G -qF 'labels[]=ready-for-agent' "$T/filed" \
  && ok "8 the bug arrives with the bug kind and the startable label" || ko "8 labels: $(cat "$T/filed")"
extra=$($G -oE '^labels\[\]=[^ ]+' "$T/filed" | $G -cv -e 'labels\[\]=bug' -e 'labels\[\]=ready-for-agent' || true)
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
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug,ready-for-agent\n' ;;
  *"issues?state=open"*) echo "" ;;
  *issues/*/comments*) echo "" ;;
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
done

# 9) (#188) the dispatch moves the cards of the issues it just requested, and an issue the loop filed itself carries no
# card until the board pass adds one. A plain move for a card the board does not carry is refused ("#N not on the
# board"), and through sg_write that refusal is filed as one more bug, whose own dispatch is refused the same way and
# files one more: the pass fed itself the same defect. A dispatch cannot assume the card: it moves its cards through the
# idempotent add path every other board writer uses (board.py --add, janitor rule R, reconcile.py), so a card the board
# does not carry is added with its status, and a card it already carries answers "already exists" and is moved.
# The stub below answers like board.py does: the add path succeeds, the plain move is refused.
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$d/autohunt.sh" "$T/s/"
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
printf '#!/bin/bash\ntouch %s/launched\n' "$T" > "$T/s/launch.py"
chmod +x "$T/s/board.sh" "$T/s/mkreq.sh" "$T/s/launch.py"
: > "$T/filed"; : > "$T/st/cycle.log"
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug,ready-for-agent\n' ;;
  *"issues?state=open"*) echo "" ;;
  *issues/*/comments*) echo "" ;;
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
case "$r" in *"AUTOBUG dispatched"*) ok "9 the dispatch still happens" ;; *) ko "9 no dispatch: '$r'";; esac
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
cp "$d/autobug.sh" "$d/autohunt.sh" "$T/s/"
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
: > "$T/board.calls"
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> %s/board.calls\necho "#1228: GitHub refused the card write (HTTP 403)" >&2\nexit 1\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho %s/req.json\n' "$T" > "$T/s/mkreq.sh"
printf '#!/bin/bash\ntouch %s/launched\n' "$T" > "$T/s/launch.py"
chmod +x "$T/s/board.sh" "$T/s/mkreq.sh" "$T/s/launch.py"
: > "$T/filed"; : > "$T/st/cycle.log"
# The loop board and the bug board are two boards (#140): a distinct bug board makes the two card moves two causes, so
# each is filed once. Were they one board, the same cause twice would be one issue, and this case wants two writers.
export BOARD="$T/s/board.sh" SG_BUG_PROJECT=5 SG_BUG_DOING="In Progress"
bash "$T/s/autobug.sh" >/dev/null 2>&1
set=$(bash "$d/refusal-names.sh" 2>/dev/null)
[ -n "$set" ] && ok "10 the helper prints the closed set in one place" || ko "10 nothing prints the set"
filed=$($G -c '^title=' "$T/filed")
[ "$filed" = 2 ] && ok "10 both writers of the pass name a refusal through the door" || ko "10 $filed bug(s), not the two cards: $(cat "$T/filed")"
named=0; stray=""
while read -r t; do
  n=$($G -oE '[A-Z][A-Z0-9-]*-FAILED' <<< "$t" | head -1)
  if [ -n "$n" ] && $G -qxF "$n" <<< "$set"; then named=$((named+1)); else stray="$stray $n"; fi
done < <($G '^title=' "$T/filed")
[ "$named" = 2 ] && ok "10 the name every writer gives is a member of the set" || ko "10 names outside the set:$stray / $(cat "$T/filed")"
$G -qE '^[0-9]{2}:[0-9]{2}:[0-9]{2} autobug REFUSED refused BOARD-WRITE-FAILED ' "$T/st/cycle.log" \
  && [ "$($G -c 'REFUSED' "$T/st/cycle.log")" = 2 ] && ok "10 the cycle log repeats the same two names, in one shape" \
  || ko "10 cycle.log: $(cat "$T/st/cycle.log")"
[ "$($G -c '^--add o 4 In Progress 1228$' "$T/board.calls")" = 1 ] && [ "$($G -c '^--add o 5 In Progress 1228$' "$T/board.calls")" = 1 ] \
  && ok "10 both refusals came from the pass's own card moves, one per board" || ko "10 board calls: $(cat "$T/board.calls")"
unset SG_BUG_PROJECT SG_BUG_DOING
# 11) (#218) the request the pass builds carries each issue's own text. The block runs its jq program through the real
# jq -- the stub below serves `-q` the way `gh api` does -- so a program that does not parse prints nothing and the child
# reads a bare `--- issue #N` line, while the request itself tells it the text below is DATA. Every stub above answers
# without a `-q`, which is what let a malformed program go unnoticed: a program that never runs is never checked.
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$T/s/"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
cat > "$T/issues.json" <<'JSON'
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]}]
JSON
printf '#!/bin/bash\ncp "$2" %s/req.body\necho %s/req.json\n' "$T" "$T" > "$T/s/mkreq.sh"
printf '#!/bin/bash\ntouch %s/launched\n' "$T" > "$T/s/launch.py"
printf '#!/bin/bash\necho "#1228 -> In Progress"\n' > "$T/s/board.sh"
chmod +x "$T/s/mkreq.sh" "$T/s/launch.py" "$T/s/board.sh"
cat > "$T/bin/gh" <<J
#!/bin/bash
prog=; prev=; for a in "\$@"; do [ "\$prev" = "-q" ] && prog="\$a"; prev="\$a"; done
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug,ready-for-agent\n' ;;
  *"issues?state=open"*) echo "" ;;
  *issues/*/comments*) echo "" ;;
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
cp "$d/autobug.sh" "$d/autohunt.sh" "$d/board.sh" "$d/board.py" "$d/issue_state.py" "$T/s/"   # board.py imports the seam
printf '#!/bin/bash\necho %s/req.json\n' "$T" > "$T/s/mkreq.sh"
printf '#!/bin/bash\ntouch %s/launched\n' "$T" > "$T/s/launch.py"
chmod +x "$T/s/mkreq.sh" "$T/s/launch.py"
: > "$T/filed"; : > "$T/st/cycle.log"; : > "$T/board.calls"; rm -f "$T/b500" "$T/bseen" "$T/bstatus"
cat > "$T/bin/gh" <<J
#!/bin/bash
a="\$*"
if [ "\${SG_GH_PASS:-}" = board ] || case "\$a" in *projectsV2*|"api users/o") true;; *) false;; esac; then   # the calls of the real board.py, and the pass's own card read (#550)
  echo "\$a" >> "$T/board.calls"
  case "\$a" in *"--input -"*) cat > "$T/board.in";; esac
  case "\$a" in
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
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug,ready-for-agent\n' ;;
  *"issues?state=open"*) printf '1228\x1fbug,ready-for-agent\n' ;;   # the unlabelled list the kinds route reads (#550: the seam now answers the kinds)
  *issues/*/comments*) echo "" ;;
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
rm -rf "$T/st/auto"; mkdir -p "$T/st/auto"; rm -f "$T"/st/rest-* "$T/bseen" "$T/bstatus" "$T/launched"; touch "$T/b500" "$T/bnowrite"
: > "$T/filed"; : > "$T/st/cycle.log"
export SG_BOARD_READ_BACKOFF=0 SG_BOARD_SETTLE_SECONDS=0
r=$(bash "$T/s/autobug.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "12b the pass whose card read never caught up finishes" || ko "12b the pass exited $rc: '$r'"
filed=$($G -c '^title=' "$T/filed")
[ "$filed" = 0 ] && ok "12b a read failure files no bug" || ko "12b a read failure filed $filed bug(s): $(cat "$T/filed")"
$G -q 'BOARD-READ-FAILED' "$T/st/cycle.log" && ok "12b the read failure is named in the cycle log" || ko "12b cycle.log: $(cat "$T/st/cycle.log")"
rm -f "$T/bnowrite"; unset SG_BOARD_READ_BACKOFF SG_BOARD_SETTLE_SECONDS
cp "$T/issue_state-stub.py" "$T/s/issue_state.py"   # the dry cases below read the cards from cards.tsv again

# 13) (#243) the bug pass takes items by the kind the seam answers, never by the platform's issue type. 1500 carries kind
# bug and no label; 1501 carries the bug label but kind task (the kind wins); 1502 carries the label and no kind (the
# label stays the fallback for an item no kind was written on); 1503 carries kind bug and a skip label. Its own sandbox.
K=$T/k; mkdir -p "$K/bin" "$K/st/auto" "$K/co/docs/squad" "$K/s"
cp "$d/autobug.sh" "$d/cluster.py" "$d/single.sh" "$d/startability.sh" "$d/ghcall.sh" "$d/refusal-names.sh" "$d/ghread.py" "$d/ghthrottle.py" "$K/s/"
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
list() { jq -r --arg l "\$1" '.[]|select(\$l == "" or ((.labels // [])|index(\$l)))|"\(.number)\u001f\(.labels|join(","))"' "$K/issues.json"; }
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}}]}}}}' "$K/issues.json" ;;   # the blocker gate's one list read (#479)
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
case "\$*" in *"issues?state=open"*) jq -r "\$q" "$R/issues.json";; *pulls?state=open*) echo "";; *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); jq -c --argjson n "\$n" '.[]|select(.number==\$n)' "$R/issues.json";; *) echo "";; esac
G
chmod +x "$R/bin/gh"
r=$(PATH="$R/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[60]"*) ko "14 the bug pass picked a run parent: '$r'";; *) ok "14 a run parent is not a bug candidate";; esac
case "$r" in *"[62]"*) ko "14 the bug pass picked an old-title run parent: '$r'";; *) ok "14 an old-title run parent is not a bug candidate";; esac
case "$r" in *"[61]"*) ok "14 an issue whose title only starts alike is still a candidate";; *) ko "14 a look-alike title was skipped: '$r'";; esac

# 15) A frozen issue waits on its owner or on a dependency outside the repo: the bug pass skips it, as autofrontier does (#530).
F=$T/frozen; mkdir -p "$F/bin"
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
# 15b) (#537) a profile sets its own SG_BUG_SKIP_LABELS, and env.sh exports it: the frozen labels are a floor, never a default
# a profile list replaces. The live profile named the four old labels, so the pass dispatched a frozen:dependency issue.
r=$(PATH="$F/bin:$PATH" SG_BUG_SKIP_LABELS=needs-owner,no-auto,wontfix,question AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[70]"*) ko "15b a profile skip list let a frozen:dependency issue through: '$r'";; *) ok "15b a profile skip list keeps the frozen:dependency floor";; esac
case "$r" in *"[71]"*) ko "15b a profile skip list let a frozen:owner issue through: '$r'";; *) ok "15b a profile skip list keeps the frozen:owner floor";; esac
case "$r" in *"[72]"*) ok "15b a plain bug is still a candidate under a profile skip list";; *) ko "15b the plain bug was skipped: '$r'";; esac

# 16) (#550) the column Todo is the owner's go signal: the bug pass takes a bug only when its card sits in Todo. A bug in
# Backlog, or with no card, waits for the owner. The cards come from one board read a pass; a board that does not answer
# dispatches nothing.
todo 92
r=$(PATH="$F/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[72]"*) ko "16 a bug with no card in Todo was dispatched: '$r'";; *) ok "16 a bug whose card is not in Todo is not dispatched";; esac
printf '72\tBacklog\n' > "$T/cards.tsv"
r=$(PATH="$F/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[72]"*) ko "16 a bug in Backlog was dispatched: '$r'";; *) ok "16 a bug in Backlog is not dispatched";; esac
todo 72 73; printf 'caps:\n  runs: 4\n' > "$T/co/docs/squad/defaults.yaml"   # room for both: only the label holds #73
r=$(PATH="$F/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[72]"*) ok "16 a bug in Todo is dispatched";; *) ko "16 a bug in Todo was not dispatched: '$r'";; esac
case "$r" in *73*) ko "16 a bug in Todo without the go label was dispatched: '$r'";; *) ok "16 a bug in Todo without the go label is not dispatched";; esac
rm -f "$T/cards.tsv"
r=$(PATH="$F/bin:$PATH" AB_DRY=1 bash "$T/s/autobug.sh" 2>&1)
case "$r" in *"[72]"*) ko "16 a bug went out with no board read: '$r'";; *"board"*) ok "16 a board that does not answer dispatches nothing, and the pass says so";; *) ko "16 the pass is silent about the board: '$r'";; esac

# 17) (#596, ADR-0146) an open issue carrying `loop-bug` is a defect in the loop's own machinery: the loop cannot dispatch
# its own fix, so the pass dispatches nothing, names the issue and exits 0. The stop is read from the open issues the pass
# already reads for its blocker gate, published to the loop log and to the issue's line on the board, and it has no marker:
# closing the issue is the only action that resumes dispatch.
rm -rf "$T/st/auto" "$T/st/req" "$T/st/cq"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$T/s/"
printf 'caps:\n  runs: 4\n' > "$T/co/docs/squad/defaults.yaml"
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"
printf 'print("-")\n' > "$T/s/fundroom.py"
todo 1228 596
cat > "$T/issues.json" <<'JSON'
[
  {"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug","ready-for-agent"]},
  {"number":596,"title":"The loop cannot land its own fix","body":"step loop.py","created_at":"2026-10-18T08:00:00Z","labels":["bug","ready-for-agent","loop-bug"]}
]
JSON
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug,ready-for-agent\n596\x1fbug,ready-for-agent,loop-bug\n' ;;
  *"issues?state=open"*) echo "" ;;
  *issues/*) printf '{"number":1228,"title":"Core: null deref","body":"step a.py"}\n' ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
: > "$T/st/cycle.log"; rm -f "$T/launched"
r=$(AB_DRY=1 bash "$T/s/autobug.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "17 a loop defect stops the bug pass with exit 0" || ko "17 the stopped pass exited $rc: '$r'"
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

# 18) (#556) a selected issue is read once a pass. The validation read and the cluster read used to be two fetches of
# the same issue, and each fetch paid a Python startup plus a gh call. The stub records the plain issue routes it
# answers: the pass asks for the issue one time, and the number it validated is the JSON the cluster receives.
rm -rf "$T/st/auto" "$T/st/req" "$T/st/cq"; mkdir -p "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/st/locks"
cp "$d/autobug.sh" "$T/s/"
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
  *pulls?state=open*) echo "" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug,ready-for-agent\n' ;;
  *"issues?state=open"*) echo "" ;;
  *issues/*/comments*) echo "" ;;
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

rm -rf "$T"; exit $fail
