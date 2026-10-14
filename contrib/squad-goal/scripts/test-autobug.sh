#!/bin/bash
# Regression test for autobug ranking (bug first, then debt) and autofrontier skipping debt (Refs #1494).
# Offline: stub gh. usage: bash test-autobug.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# The free slots come from the machine-wide caps (squad/caps.py) of this checkout, with leases in the sandbox, never the real ones.
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR="$T/caps"
G=/usr/bin/grep
mkdir -p "$T/bin" "$T/st/auto" "$T/co/docs/squad/workflows" "$T/s"
cp "$d/autobug.sh" "$d/autofrontier.sh" "$d/cluster.py" "$d/single.sh" "$d/startability.sh" "$d/ghcall.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/refusal-names.sh" "$T/s/"
export SG_GH_AGGREGATE_WINDOW=0   # the open-issue list changes between cases: no snapshot answers a later one
: > "$T/st/active-runs.txt"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"   # the project lowers the machine-wide caps.runs
# exported, as the real env.sh does: the stub mkreq.sh below is a child shell and must still see SG_STATE
printf 'export SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_STATE=%s SG_BASE=main SG_CHECKOUT=%s SG_FRONTIER_LABEL=ready-for-agent SG_BUG_LABEL=bug\n' "$T/st" "$T/co" > "$T/s/env.sh"
# 1228 is a bug; 1400 is debt (and still carries ready-for-agent, the old filer). 1401 is a frontier-only slice.
cat > "$T/issues.json" <<'JSON'
[
  {"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug"]},
  {"number":1400,"title":"Debt: retry has no backoff","body":"<!-- debt-key:abcd -->","created_at":"2026-10-10T16:00:00Z","labels":["debt","ready-for-agent"]},
  {"number":1401,"title":"Slice: a frontier ticket","body":"UI/Logic/Foo.cs","created_at":"2026-10-10T17:00:00Z","labels":["ready-for-agent"]}
]
JSON
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues(states: OPEN"*) jq '{data:{repository:{issues:{pageInfo:{hasNextPage:false},nodes:[.[]|{number,title,body,state:"OPEN",labels:{nodes:[(.labels//[])[]|{name:.}]},blockedBy:{nodes:[]},comments:{nodes:[]}}]}}}}' "$T/issues.json" ;;   # the frontier's one list read (#383)
  *"issues?state=open"*labels=bug*) python3 -c 'import json
for i in json.load(open("$T/issues.json")):
  labs=",".join(i.get("labels") or [])
  if "bug" in (i.get("labels") or []): print(str(i["number"])+"\x1f"+labs)' ;;
  *"issues?state=open"*labels=debt*) python3 -c 'import json
for i in json.load(open("$T/issues.json")):
  labs=",".join(i.get("labels") or [])
  if "debt" in (i.get("labels") or []): print(str(i["number"])+"\x1f"+labs)' ;;
  *"issues?state=open"*wave*|*labels=ready-for-agent*) python3 -c 'import json
for i in json.load(open("$T/issues.json")):
  labs=",".join(i.get("labels") or [])
  if "ready-for-agent" in (i.get("labels") or []):
    print("wave:01\x1f"+str(i["number"])+"\x1f"+labs)' ;;
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); python3 -c 'import json,sys;print(json.dumps([i for i in json.load(open("$T/issues.json")) if i["number"]==int(sys.argv[1])][0]))' "\$n" ;;
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
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug"]}]
JSON
cp "$d/autobug.sh" "$T/s/"
printf '#!/bin/bash\n[ -n "\${AB_DRY:-}" ] && exit 0\ntouch %s/launched\n' "$T" > "$T/s/launch.py"; chmod +x "$T/s/launch.py"
held() {   # $1: the body of the issue's comments, one line each
  cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug\n' ;;
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
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug"]}]
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
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug\n' ;;
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
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug"]}]
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
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug\n' ;;
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
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug"]}]
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
[{"number":1228,"title":"Core: null deref","body":"step a.py","created_at":"2026-10-10T15:00:00Z","labels":["bug"]}]
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
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug\n' ;;
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
if [ "\${SG_GH_PASS:-}" = board ]; then   # the calls of the real board.py
  echo "\$a" >> "$T/board.calls"
  case "\$a" in *"--input -"*) cat > "$T/board.in";; esac
  case "\$a" in
    "api users/o") echo '{"type":"User"}' ;;
    *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_PROG","name":"In Progress"}]}]' ;;
    *items?per_page=100\&fields*) [ -e "$T/bseen" ] && printf '1228\t%s\thttps://api.github.com/repos/x/y\n' "\$(cat "$T/bstatus" 2>/dev/null)"; exit 0 ;;   # the seam's tsv row
    *items?per_page=100*) [ -e "$T/bseen" ] && printf '[1228,"ITEM1","https://api.github.com/repos/x/y"]\n'; exit 0 ;;
    *repos/x/y/issues/1228*)
      if [ ! -e "$T/b500" ]; then touch "$T/b500"; echo 'gh: HTTP 500' >&2; exit 1; fi   # GitHub's server failed once
      echo '{"id":3228}' ;;
    *"-X POST"*items*) touch "$T/bseen"; echo '{"id":"ITEM1"}' ;;
    *"-X PATCH"*items/ITEM1*) sed -n 's/.*"value": "\([^"]*\)".*/\1/p' "$T/board.in" > "$T/bstatus"; echo '{"id":"ITEM1"}' ;;
    *) echo "" ;;
  esac
  exit 0
fi
case "\$a" in
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*labels=bug*) printf '1228\x1fbug\n' ;;
  *"issues?state=open"*) echo "" ;;
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

# 13) (#243) the bug pass takes items by the kind the seam answers, never by the platform's issue type. 1500 carries kind
# bug and no label; 1501 carries the bug label but kind task (the kind wins); 1502 carries the label and no kind (the
# label stays the fallback for an item no kind was written on); 1503 carries kind bug and a skip label. Its own sandbox.
K=$T/k; mkdir -p "$K/bin" "$K/st/auto" "$K/co/docs/squad" "$K/s"
cp "$d/autobug.sh" "$d/cluster.py" "$d/single.sh" "$d/startability.sh" "$d/ghcall.sh" "$d/refusal-names.sh" "$K/s/"
: > "$K/st/active-runs.txt"; printf 'caps:\n  runs: 4\n' > "$K/co/docs/squad/defaults.yaml"
printf 'export SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_STATE=%s SG_BASE=main SG_CHECKOUT=%s SG_FRONTIER_LABEL=ready-for-agent SG_BUG_LABEL=bug\n' "$K/st" "$K/co" > "$K/s/env.sh"
printf '#!/bin/bash\necho READY\n' > "$K/s/stack.sh"; chmod +x "$K/s/stack.sh"
printf '1500\tbug\n1501\ttask\n1503\tbug\n' > "$K/kinds.tsv"
# the seam's door, stubbed at the script's own name: the pass asks `issue_state.py --kinds` and never names a carrier
cat > "$K/s/issue_state.py" <<PY
import os, sys
if sys.argv[1:] != ["--kinds"]: sys.exit(2)
if os.path.exists("$K/seam-down"): print("no board", file=sys.stderr); sys.exit(1)
sys.stdout.write(open("$K/kinds.tsv").read())
PY
cat > "$K/bin/gh" <<J
#!/bin/bash
list() { python3 -c 'import json,sys
for i in json.load(open("$K/issues.json")):
  if sys.argv[1] == "" or sys.argv[1] in i["labels"]: print(str(i["number"])+"\x1f"+",".join(i["labels"]))' "\$1"; }
case "\$*" in
  *pulls?state=open*) echo "" ;;
  *"issues?state=open"*labels=bug*) list bug ;;
  *"issues?state=open"*labels=debt*) list debt ;;
  *"issues?state=open"*) list "" ;;
  *issues/*) n=\$(echo "\$*" | /usr/bin/grep -oE 'issues/[0-9]+' | cut -d/ -f2); python3 -c 'import json,sys;print(json.dumps([i for i in json.load(open("$K/issues.json")) if i["number"]==int(sys.argv[1])][0]))' "\$n" ;;
  *) echo "" ;;
esac
J
chmod +x "$K/bin/gh"
cat > "$K/issues.json" <<'JSON'
[
  {"number":1500,"title":"Core: a","body":"a.py","created_at":"2026-10-10T10:00:00Z","labels":[]},
  {"number":1501,"title":"Core: b","body":"b.py","created_at":"2026-10-10T11:00:00Z","labels":["bug"]},
  {"number":1502,"title":"Core: c","body":"c.py","created_at":"2026-10-10T12:00:00Z","labels":["bug"]},
  {"number":1503,"title":"Core: d","body":"d.py","created_at":"2026-10-10T13:00:00Z","labels":["no-auto"]}
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

# 14) A run parent ("Squad run <run id>") is a roll-up, never a bug to fix: the bug pass skips it by title, whatever its labels.
R=$T/runparent; mkdir -p "$R/bin"
cat > "$R/issues.json" <<'JSON'
[
  {"number":60,"title":"Squad run run-20261014-030735-11d7af","labels":[{"name":"bug"}]},
  {"number":61,"title":"Squad run report is wrong","body":"x.py","created_at":"2026-10-10T15:00:00Z","labels":[{"name":"bug"}]}
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
case "$r" in *"[61]"*) ok "14 an issue whose title only starts alike is still a candidate";; *) ko "14 a look-alike title was skipped: '$r'";; esac

rm -rf "$T"; exit $fail
