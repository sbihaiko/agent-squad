#!/bin/bash
# Regression test for autohunt.sh: when the loop is idle, start one read-only bug hunt (Refs #1493, #1504).
# Offline: stub gh, autobug, autofrontier, mkreq, launch. usage: bash test-autohunt.sh [path/to/autohunt.sh]
export SG_MIN_FREE_GB=0   # the dispatchers pin their disk floor: the real volume must not stop a fixture pass (#792); a case that tests the stop sets its own
export SG_NIGHTLY_OFF=1   # the dispatch hold reads the wall clock: the nightly hour must not hold a fixture pass (#2397); a case that tests the hold sets its own hour
src=${1:-$(dirname "$0")/autohunt.sh}; here=$(cd "$(dirname "$src")" && pwd); T=$(mktemp -d); fail=0
repo=$(cd "$(dirname "$src")/../../.." && pwd)   # SG_SQUAD: squad/caps.py lives in the tooling repo the fixture is testing (#21)
export SG_SQUAD=$repo SQUAD_CAPS_DIR=$T/caps   # (#21) the pass reads the machine-wide ceiling through squad.caps, exported so the reader process sees it; the leases are sandboxed
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
[ -f "$src" ] || { echo "FAIL starts after idle window: autohunt.sh does not exist"; rm -rf "$T"; exit 1; }
mkdir -p "$T/bin"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
echo "$*" >> "${GHLOG:-/dev/null}"
case "$*" in
  *pulls*) printf '%s\n' "${GH_PRS:-}";;
  *labels=debt*) echo "${GH_DEBT:-}";;
  *POST*|*issues*)
    echo "$*" >> "${GHPOST:-/dev/null}"
    for a in "$@"; do case "$a" in body=@*) cat "${a#body=@}" >> "${GHPOST:-/dev/null}";; esac; done
    echo "{\"number\":${GH_NEW:-42}}";;
  *) ;;
esac
GH
chmod +x "$T/bin/gh"
export PATH="$T/bin:$PATH"
restore_gh() {
  cat > "$T/bin/gh" <<'GH'
#!/bin/bash
echo "$*" >> "${GHLOG:-/dev/null}"
case "$*" in
  *pulls*) printf '%s\n' "${GH_PRS:-}";;
  *POST*|*issues*)
    echo "$*" >> "${GHPOST:-/dev/null}"
    for a in "$@"; do case "$a" in body=@*) cat "${a#body=@}" >> "${GHPOST:-/dev/null}";; esac; done
    echo "{\"number\":${GH_NEW:-42}}";;
  *) ;;
esac
GH
  chmod +x "$T/bin/gh"
}
scene() {
  restore_gh
  S=$T/$1/scripts; mkdir -p "$S" "$T/$1/st/req" "$T/$1/st/cq" "$T/$1/st/auto" "$T/$1/st/locks" "$T/$1/co/runs" "$T/$1/home"
  /usr/bin/git init -q --bare "$T/$1/origin.git"   # the branch read of the case: a local bare repository, never github.com
  cp "$src" "$S/autohunt.sh"
  cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/workbranches.sh" "$here/refusal-names.sh" "$here/envelope-spend.sh" "$here/issue_ledger.py" "$here/caps.sh" "$here/priority_backfill.py" "$here/priority_rank.py" "$S/"   # the birth Priority table (#1978); the filer's one write door (#140, #139): --file writes through gh_send, which reads ghread.py; the work-branch read is workbranches.sh (#361); the process read is caps.sh, which resolves to squad.procs through SG_SQUAD inside the child's sandbox (#1594)
  printf '#!/bin/bash\n[ -n "${AB_DRY:-}" ] && [ -e "$SG_STATE/auto/startable-bug" ] && echo "AUTOBUG would dispatch 1 cluster(s)"; exit 0\n' > "$S/autobug.sh"
  printf '#!/bin/bash\n[ -n "${AF_DRY:-}" ] && [ -e "$SG_STATE/auto/startable-frontier" ] && echo "FRONTIER would dispatch"; exit 0\n' > "$S/autofrontier.sh"
  printf '#!/bin/bash\nmkdir -p "$SG_STATE/req"; echo "$SG_STATE/req/$1.txt"; cat "$2" > "$SG_STATE/req/$1.txt"\n' > "$S/mkreq.sh"
  printf 'import sys\nopen("%s/launch.log","a").write(sys.argv[1]+"\\n")\n' "$T/$1" > "$S/launch.py"
  chmod +x "$S/autobug.sh" "$S/autofrontier.sh" "$S/mkreq.sh"
  cat > "$S/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$T/$1/st; SG_CHECKOUT=$T/$1/co; SG_BASE=main
SG_CODING_RULES=rules; BOARD=true
E
  : > "$T/$1/st/active-runs.txt"
  export HOME=$T/$1/home SG_STATE=$T/$1/st GHLOG=$T/$1/gh.log GHPOST=$T/$1/ghpost
  export SG_LAND_REMOTE=$T/$1/origin.git
  export SG_AUTO_HUNT=yes SG_HUNT_IDLE_MINUTES=0 SG_HUNT_COOLDOWN_MINUTES=0 SG_HUNT_PER_DAY=6
  unset SG_FILE_PASS SG_GH_PASS SG_FILE_CEILING   # a filing names no pass here: the ceiling is not this suite's subject (#140)
  echo $(( $(date +%s) - 3600 )) > "$SG_STATE/auto/hunt-activity"
}
# Every case reads the pushed branch heads through SG_LAND_REMOTE. An unset one falls back to https://github.com/$SG_REPO.git,
# a repository that is not there: one read costs 0.8 s of wall time and no CPU, the suite makes one per case, and its wall time
# then follows the network instead of the loop. The fixture sets a local bare repository, so no case can leave the machine.
leaves_the_machine() { case "${SG_LAND_REMOTE:-}" in "" | http* | git@*) return 0;; *) return 1;; esac; }
run() {
  if leaves_the_machine; then
    ko "the case at test-autohunt.sh:${BASH_LINENO[0]} runs with SG_LAND_REMOTE='${SG_LAND_REMOTE:-}', so its branch read leaves the machine"
    return 1
  fi
  AH_DRY=1 bash "$S/autohunt.sh" 2>&1
}
started() { case "$1" in *"HUNT started"*) return 0;; *) return 1;; esac; }
skipped() { case "$1" in *"HUNT started"*) return 1;; *) return 0;; esac; }

# 1) starts after the idle window
scene idle; r=$(run)
started "$r" && ok "starts after idle window" || ko "starts after idle window: '$r'"
[ -s "$T/idle/launch.log" ] && ko "AH_DRY launched a run" || ok "AH_DRY does not launch"

# 2) idle window not yet elapsed
scene window; export SG_HUNT_IDLE_MINUTES=10
echo $(date +%s) > "$SG_STATE/auto/hunt-activity"
r=$(run); skipped "$r" && ok "does not start before the idle window" || ko "started before idle window: '$r'"

# 3) live squad run (record without verdict, process alive)
scene runlive
mkdir -p "$T/runlive/co/runs/r1"
printf '{"event": "call_start", "pid": %s}\n{"event": "spawn_accepted", "definition": "GitHub issue #1"}\n' "$$" > "$T/runlive/co/runs/r1/record.jsonl"
echo r1 > "$SG_STATE/active-runs.txt"
r=$(run); skipped "$r" && ok "does not start while a live run exists" || ko "started with a live run: '$r'"

# 4) gui-suite lock held
scene gate
mkdir -p "$SG_STATE/locks/gui-suite"; echo $$ > "$SG_STATE/locks/gui-suite/pid"
r=$(run); skipped "$r" && ok "does not start while a gate test holds gui-suite" || ko "started with gui-suite lock: '$r'"

# 5) the landing pass's lock held: the branch landing pass is what replaced the retired fork pass, so a hunt waits for it (#134)
scene fc
mkdir -p "$SG_STATE/locks/single-branchland"; echo $$ > "$SG_STATE/locks/single-branchland/pid"
r=$(run); skipped "$r" && ok "does not start while the landing pass is running" || ko "started with branchland lock: '$r'"

# 5b. (#1057) a landing lock whose holder died as a zombie is dead: the hunt does not wait for it
scene fc
sh -c 'sleep 0 & echo $! > "$1"; exec sleep 300' sh "$T/zombie" >/dev/null 2>&1 & zpar=$!
for i in $(seq 1 50); do [ -s "$T/zombie" ] && [ "$(/bin/ps -o stat= -p "$(cat "$T/zombie")" 2>/dev/null | cut -c1)" = Z ] && break; sleep 0.1; done
rm -rf "$SG_STATE/locks/single-branchland"; mkdir -p "$SG_STATE/locks/single-branchland"; echo "$(cat "$T/zombie")" > "$SG_STATE/locks/single-branchland/pid"
r=$(run); skipped "$r" && ko "a zombie landing lock held the hunt: '$r'" || ok "a zombie landing lock does not hold the hunt"
kill -9 "$zpar" 2>/dev/null; wait "$zpar" 2>/dev/null

# 6) startable autobug / autofrontier
scene bug; touch "$SG_STATE/auto/startable-bug"
r=$(run); skipped "$r" && ok "does not start while a startable bug exists" || ko "started with startable bug: '$r'"
scene fr; touch "$SG_STATE/auto/startable-frontier"
r=$(run); skipped "$r" && ok "does not start while a startable frontier exists" || ko "started with startable frontier: '$r'"

# 7) a pushed work branch whose tip has no kept review: the review pass will spend a slot on it, so a hunt waits (#357)
scene rev
remote=$T/rev/origin.git; wt=$T/rev/wt    # the bare repository scene rev made; the case gives it one branch
/usr/bin/git clone -q "$remote" "$wt" 2>/dev/null
( cd "$wt" && /usr/bin/git -c user.email=a@b -c user.name=t commit -q --allow-empty -m x && /usr/bin/git branch -q fix/1100-x && /usr/bin/git push -q origin fix/1100-x )
r=$(run); skipped "$r" && ok "does not start while a work branch waits on a review" || ko "started with a waiting branch: '$r'"

# 8) cooldown (a recent hunt-last, as if a hunt-filed fix already started)
scene cdn
echo $(date +%s) > "$SG_STATE/auto/hunt-last"
export SG_HUNT_COOLDOWN_MINUTES=60
r=$(run); skipped "$r" && ok "respects cooldown" || ko "started inside cooldown: '$r'"

# 9) daily cap
scene cap
printf '%s 6\n' "$(date +%F)" > "$SG_STATE/auto/hunt-day"
r=$(run); skipped "$r" && ok "respects daily cap" || ko "started past daily cap: '$r'"

# 10) SG_AUTO_HUNT=no
scene offenv; export SG_AUTO_HUNT=no
r=$(run); skipped "$r" && ok "SG_AUTO_HUNT=no turns it off" || ko "started with SG_AUTO_HUNT=no: '$r'"

# 11) auto/off
scene offile; touch "$SG_STATE/auto/off"
r=$(run); skipped "$r" && ok "auto/off turns it off" || ko "started with auto/off: '$r'"

# 12) never two hunts at once
scene two; r=$(run); started "$r" || ko "first hunt did not start: '$r'"
r=$(run); skipped "$r" && ok "never starts two hunts at once" || ko "started a second hunt: '$r'"

# 13) hunt request names sources, filing rules, carve-outs
scene req; r=$(run); body=$(cat "$SG_STATE/req/"*.body "$SG_STATE/req/"*.txt 2>/dev/null)
for w in "shell suites" "Python suites" "git log" "needs-human" "recent bugs" \
         "at most 3" "duplicate" "label \`bug\`" "Squad-only work" \
         "GUI scenario" "credentials" "permanent deletion" "architecture"; do
  printf '%s' "$body" | $G -qi -- "$w" || { ko "request lacks '$w'"; w=; }
done
[ -n "$w" ] && ok "hunt request names sources, filing rules and carve-outs"
printf '%s' "$body" | $G -q "read-only" && ok "hunt request is read-only" || ko "request is not read-only"

# 14) filed-issue helper adds only the bug label
scene file
: > "$T/file/ghpost"
printf 'repro\n' > "$T/file/body.md"
n=$(bash "$S/autohunt.sh" --file "a defect" "$T/file/body.md" 2>&1)
case "$n" in *"HUNT filed #42"*) ok "file helper logs HUNT filed #N";; *) ko "file helper: '$n'";; esac
$G -qF "labels[]=bug" "$T/file/ghpost" && ok "file helper adds the bug label" || ko "no bug label: $(cat "$T/file/ghpost")"
extra=$($G -oE 'labels\[\]=[^ ]+' "$T/file/ghpost" 2>/dev/null | $G -cvxF -e 'labels[]=bug' -e 'labels[]=frozen:not-startable' || true)
[ "${extra:-0}" -eq 0 ] && ok "file helper adds only the bug label and the publish hold (#1978)" || ko "file helper added extra labels: $(cat "$T/file/ghpost")"
$G -qF "title=[SQUAD] a defect" "$T/file/ghpost" && ok "file helper prefixes the title with [SQUAD]" || ko "no [SQUAD] prefix: $(cat "$T/file/ghpost")"
: > "$T/file/ghpost"
bash "$S/autohunt.sh" --file "[SQUAD] already tagged" "$T/file/body.md" >/dev/null 2>&1
$G -qF "title=[SQUAD] already tagged" "$T/file/ghpost" && ! $G -qF "[SQUAD] [SQUAD]" "$T/file/ghpost" \
  && ok "file helper never doubles an existing prefix" || ko "doubled prefix: $(cat "$T/file/ghpost")"
n=$(bash "$S/autohunt.sh" --none 2>&1)
case "$n" in *"HUNT none"*) ok "empty hunt logs HUNT none";; *) ko "none: '$n'";; esac

# 15) watch.sh calls autohunt.sh every cycle after autobug.sh
w=$here/watch.sh
if [ -f "$w" ]; then
  python3 - "$w" <<'PY' && ok "watch.sh calls autohunt.sh after autobug.sh" || ko "watch.sh does not call autohunt.sh after autobug.sh"
import pathlib, sys
t = pathlib.Path(sys.argv[1]).read_text().splitlines()
ai = [i for i,l in enumerate(t) if "autobug.sh" in l]
hi = [i for i,l in enumerate(t) if "autohunt.sh" in l]
sys.exit(0 if ai and hi and hi[0] > ai[0] else 1)
PY
else ko "watch.sh missing"; fi

# 16) a hunt that files nothing leaves cooldown and the day count clear (Refs #1504)
scene emptyhunt; r=$(run)
started "$r" || ko "empty-hunt setup did not start: '$r'"
src1=$(cat "$SG_STATE/auto/hunt-source" 2>/dev/null || echo 0)
[ -f "$SG_STATE/auto/hunt-last" ] && ko "empty hunt: hunt-last written at hunt start" || ok "empty hunt: hunt-last not written at start"
if [ -f "$SG_STATE/auto/hunt-day" ]; then
  read -r _ n < "$SG_STATE/auto/hunt-day"
  [ "${n:-0}" -eq 0 ] && ok "empty hunt: hunt-day not incremented at start" || ko "empty hunt: hunt-day=$n at start"
else ok "empty hunt: hunt-day not incremented at start"; fi
bash "$S/autohunt.sh" --none >/dev/null
rm -f "$SG_STATE/auto/hunt-live"
export SG_HUNT_COOLDOWN_MINUTES=60
r=$(run)
started "$r" && ok "empty hunt: next idle check hunts again" || ko "empty hunt: cooldown blocked a no-find hunt: '$r'"
src2=$(cat "$SG_STATE/auto/hunt-source" 2>/dev/null || echo 0)
[ "$src2" = "$(( (src1 + 1) % 4 ))" ] && ok "empty hunt: the second hunt took the next source in the rotation" || ko "empty hunt: source stalled at $src2 after $src1"
if [ -f "$SG_STATE/auto/hunt-day" ]; then
  read -r _ n < "$SG_STATE/auto/hunt-day"
  [ "${n:-0}" -eq 0 ] && ok "empty hunt: hunt-day still not incremented" || ko "empty hunt: hunt-day=$n after a no-find"
else ok "empty hunt: hunt-day still not incremented"; fi

# 17) a hunt-filed bug starts cooldown and the day count when autobug dispatches it (Refs #1504)
scene huntdisp
d=$(cd "$(dirname "$src")" && pwd)
cp "$d/autobug.sh" "$d/loopbug.sh" "$d/columnlimit.sh" "$d/single.sh" "$d/startability.sh" "$d/skiplabels.sh" "$d/ghthrottle.py" "$S/"
[ -f "$d/cluster.py" ] && cp "$d/cluster.py" "$S/"
printf '#!/bin/bash\necho READY\n' > "$S/stack.sh"; chmod +x "$S/stack.sh"   # the blocker gate (#479): #42 has no blocker
printf 'import sys\nif sys.argv[1:]: sys.exit(1)\nprint("42\\tTodo")\n' > "$S/issue_state.py"   # the seam's door, stubbed: #42's card sits in Todo (#550)
mkdir -p "$T/huntdisp/co/docs/squad/workflows"
echo '{"max_parallel": 10}' > "$T/huntdisp/co/docs/squad/workflows/dynamic.graph.json"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
echo "$*" >> "${GHLOG:-/dev/null}"
case "$*" in
  *pulls*) ;;
  *"issues(states: OPEN"*) echo '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":42,"title":"a hunt bug","body":"","state":"OPEN","labels":{"nodes":[{"name":"bug"},{"name":"ready-for-agent"}]},"blockedBy":{"nodes":[]},"comments":{"nodes":[]}}]}}}}';;   # the blocker gate's one list read (#479)
  *"issues?state=open"*) echo '[{"number":42,"title":"a hunt bug","labels":[{"name":"bug"},{"name":"ready-for-agent"}]}]';;   # the candidate list as GitHub answers it (JSON)
  *POST*)
    echo "$*" >> "${GHPOST:-/dev/null}"
    echo "{\"number\":${GH_NEW:-42}}";;
  *"issue(number: 42)"*) echo '{"data":{"repository":{"i42":{"number":42,"title":"a hunt bug","body":"repro\\nFiled by autohunt\\n","state":"OPEN","createdAt":"2026-10-11T00:00:00Z","labels":{"nodes":[{"name":"bug"}]},"blockedBy":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}}}}';;   # the single-issue read, as the GraphQL fact answers it
  *issues/42/comments*) echo '[]';;
  *issues/42*)
    printf '{"number":42,"title":"a hunt bug","body":"repro\\nFiled by autohunt\\n","created_at":"2026-10-11T00:00:00Z","labels":[{"name":"bug"}],"state":"open"}\n';;
  *) ;;
esac
GH
chmod +x "$T/bin/gh"
[ -f "$SG_STATE/auto/hunt-last" ] && ko "dispatch setup: hunt-last already set" || ok "dispatch setup: no hunt-last yet"
ab=$(bash "$S/autobug.sh" 2>&1)
case "$ab" in *"AUTOBUG dispatched"*) ok "autobug dispatched the hunt-filed bug";; *) ko "autobug did not dispatch: '$ab'";; esac
[ -f "$SG_STATE/auto/bug-42" ] || ko "auto/bug-42 not written"
last=$(cat "$SG_STATE/auto/hunt-last" 2>/dev/null || echo 0)
[ "${last:-0}" -gt 0 ] && ok "hunt-last set at autobug dispatch" || ko "hunt-last not set at dispatch (last=$last)"
if [ -f "$SG_STATE/auto/hunt-day" ]; then
  read -r _ n < "$SG_STATE/auto/hunt-day"
  [ "${n:-0}" -eq 1 ] && ok "hunt-day incremented once at dispatch" || ko "hunt-day=$n at dispatch"
else ko "hunt-day missing at dispatch"; fi
rm -f "$SG_STATE/auto/hunt-live"
export SG_HUNT_COOLDOWN_MINUTES=60 SG_HUNT_IDLE_MINUTES=0
echo $(( $(date +%s) - 3600 )) > "$SG_STATE/auto/hunt-activity"
r=$(AH_DRY=1 bash "$S/autohunt.sh" 2>&1)
skipped "$r" && ok "next hunt waits 60 minutes after a hunt-fix dispatch" || ko "hunted inside cooldown after dispatch: '$r'"

# 18) an issue the loop files is on the board at once, so the pass that dispatch picks it up with has a card to move
# (#175). A filed issue with no card made the very next autobug pass's card move exit 1 with "#N not on the board"; that
# pass files a write it cannot prove as a defect, and the bug it filed had no card either, so every pass filed one more
# (#169 -> #171 -> #174 -> #175). The filer and the real board door sit beside a stub gh, so the move the dispatch makes
# is the one the card is proven by (#105).
scene filed
f=$T/filed
# board.py imports issue_state.py, the one seam for an issue's state (#236): the copy needs it beside board.py (#393)
cp "$(dirname "$src")/board.sh" "$(dirname "$src")/board.py" "$(dirname "$src")/issue_state.py" "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$S/"
printf 'export SG_REPO SG_OWNER SG_PROJECT SG_STATE SG_CHECKOUT\nexport SG_BOARD_READ_BACKOFF=0\nexport BOARD="%s/board.sh"\n' "$S" >> "$S/env.sh"
export SGH_TMP=$f
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; D=${SGH_TMP:?}
echo "$a" >> "$D/gh.calls"
case "$a" in *"--input -"*) cat > "$D/last.in";; esac
case "$a" in
  *users/o) echo '{"type":"User"}';;
  *fields?per_page=100*) echo '[{"id":"F_STATUS","name":"Status","options":[{"id":"O_TODO","name":{"raw":"Todo"}},{"id":"O_DOING","name":{"raw":"In Progress"}}]}]';;
  *items?per_page=100\&fields*)   # the Status read: what the card carries, "" when it carries none; the reader's --jq ends in @tsv (#393)
    [ -e "$D/seen" ] && printf '1228\t%s\t\n' "$(cat "$D/carried" 2>/dev/null)"; exit 0;;
  *items?per_page=100*)   # the items route is a bare array with no total, read through gh's --jq
    [ -e "$D/seen" ] && printf '[1228,"ITEM1228","https://api.github.com/repos/o/r"]\n'; exit 0;;
  *"-X POST"*items*)   # the add: an item already on the board is refused, exactly as GitHub refuses it
    case "$(cat "$D/last.in")" in
      *4711*) [ -e "$D/seen" ] && { echo '{"message":"Validation Failed: item already exists"}' >&2; exit 1; }
              touch "$D/seen"; echo '{"id":"ITEM1228"}';;
      *) echo '{"message":"Resource not accessible by integration"}' >&2; exit 1;;
    esac;;
  *"-X PATCH"*items/ITEM1228*)   # the write lands: the card carries it, which is the proof of the write (#105)
    sed -n 's/.*"value": "\([^"]*\)".*/\1/p' "$D/last.in" > "$D/carried"; echo '{"id":"ITEM1228"}';;
  *repos/o/r/issues/1228*) echo '{"id":4711}';;
  *repos/o/r/issues*) echo "$a" >> "$D/ghpost"; echo '{"number":1228}';;
  *) echo '{"message":"not found"}' >&2; exit 1;;
esac
J
chmod +x "$T/bin/gh"
printf 'repro\n' > "$f/body.md"; : > "$f/ghpost"
n=$(bash "$S/autohunt.sh" --file "a defect" "$f/body.md" 2>&1)
case "$n" in *"HUNT filed #1228"*) ok "18 the filer logs the issue it opened";; *) ko "18 the filer: '$n'";; esac
$G -qF -- '-X POST /users/o/projectsV2/1/items' "$f/gh.calls" && ok "18 the issue the loop filed is on the board at once" || ko "18 the filer left the issue with no card: $(cat "$f/gh.calls")"
export SG_STATE=$f/st SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_BOARD_READ_BACKOFF=0   # the move the dispatch makes reads these, never the caller's shell
mv=$(bash "$S/board.sh" o 1 "In Progress" 1228 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "18 the card move of the pass that picks it up succeeds" || ko "18 the dispatch's card move exited $rc: '$mv'"
case "$mv" in *"#1228 -> In Progress"*) ok "18 the card the board carries is In Progress";; *) ko "18 the dispatch's card move: '$mv'";; esac
case "$mv" in *"not on the board"*) ko "18 the pass that picks the filed issue up reports it absent: '$mv'";; *) ok "18 the filed issue is never reported absent";; esac

# 19) (#140) the ceiling the filer enforces is the number the hunt request asks a child for: the bound and the
# instruction about it share one declaration, so they cannot drift apart.
scene ceiling; export SG_FILE_CEILING=2
r=$(run)
body=$(cat "$SG_STATE/req/"*.body 2>/dev/null)
printf '%s' "$body" | $G -qF 'at most 2 bugs per hunt' && ok "19 the hunt request asks for the ceiling the filer declares" || ko "19 the request does not carry the declared ceiling: $(printf '%s' "$body" | $G -o 'at most [0-9]* bugs per hunt')"

# 20) (#139) a write GitHub refused is never read back as a filed issue: `gh api` writes GitHub's error body to stdout
# and exits non-zero, so the words of a rate limit ("... for user ID 1 ...") read as a number and the filer logged
# `HUNT filed #1` -- an issue nobody opened. A refused filing is the door's exit 2, named with its fingerprint, and it
# is never a number.
scene refused
printf 'repro\n' > "$T/refused/body.md"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
echo "$*" >> "${GHLOG:-/dev/null}"
case "$*" in
  *"--method POST"*)
    printf '%s' '{"message":"API rate limit exceeded for user ID 1. Include the request ID 06F8:34CAED:22CD6B:3FC4397.","documentation_url":"https://docs.github.com/rest","status":"403"}'
    exit 1;;
  *) ;;
esac
GH
chmod +x "$T/bin/gh"
n=$(bash "$S/autohunt.sh" --file "a refused filing" "$T/refused/body.md" 2>&1)
case "$n" in *"HUNT filed #"*)   ko "20 a refused filing is never read back as a filed issue: '$n'";;
             *"HUNT refused"*) ok "20 a refused filing is named with its fingerprint, never read back as a filed issue";;
             *)                ko "20 the filer said something else: '$n'";; esac
case "$n" in *"rate limit"*|*"403"*) ko "20 the refusal's own words became the filer's line: '$n'";;
             *)                      ok "20 the refusal's own words are never read as an issue number";; esac

# 21) (#895) the hunt files a bug alone, and its card joins the Todo column: the bug kind is the only label the hunt
# writes, and the card is added in Todo, the column the dispatcher reads. ready-for-agent is named by the caller that
# dispatches the filing (autobug, through SG_FILED_LABELS), never by the hunt: a hunt that named it would start work
# the owner has not moved to Todo. The board door is a spy that records its arguments; test 18 runs the real door.
scene huntlabel
f=$T/huntlabel
cat > "$S/board-spy.sh" <<'SPY'
#!/bin/bash
echo "$*" >> "$SGH_BOARD/board.calls"
echo "posted=$(/usr/bin/grep -c POST "$GHPOST")" >> "$SGH_BOARD/board.calls"   # the filing POST is already written when the card is added
echo "card ok"
SPY
chmod +x "$S/board-spy.sh"
printf 'export SG_REPO SG_OWNER SG_PROJECT SG_STATE SG_CHECKOUT\nexport BOARD="%s/board-spy.sh"\n' "$S" >> "$S/env.sh"
export SGH_BOARD=$f
: > "$f/ghpost"; : > "$f/board.calls"; printf 'repro\n' > "$f/body.md"
unset SG_FILED_LABELS SG_FILED_BY SG_TODO SG_FILE_PASS
n=$(bash "$S/autohunt.sh" --file "a defect" "$f/body.md" 2>&1)
case "$n" in *"HUNT filed #42"*) ok "21 the hunt logs the bug it filed";; *) ko "21 the hunt did not file: '$n'";; esac
labels=$($G -oE 'labels\[\]=[^ ]+' "$f/ghpost" | sort -u | tr '\n' ' ')
[ "$labels" = "labels[]=bug labels[]=frozen:not-startable " ] && ok "21 the hunt labels the bug kind and the publish hold, nothing else (#1978)" || ko "21 the hunt's labels: '$labels'"
$G -qxF -- "--add o 1 Todo 42" "$f/board.calls" && ok "21 the card joins the board in Todo" || ko "21 the card's column: $(cat "$f/board.calls")"
$G -qxF "posted=1" "$f/board.calls" && ok "21 the card is added after the filing POST, so its number is real" || ko "21 the card came before the filing: $(cat "$f/board.calls")"
: > "$f/ghpost"; : > "$f/board.calls"
n=$(SG_FILED_BY=autobug SG_FILED_LABELS=ready-for-agent bash "$S/autohunt.sh" --file "a second defect" "$f/body.md" 2>&1)
labels=$($G -oE 'labels\[\]=[^ ]+' "$f/ghpost" | sort -u | tr '\n' ' ')
[ "$labels" = "labels[]=bug labels[]=frozen:not-startable labels[]=ready-for-agent " ] && ok "21 only a named caller adds the startable label" || ko "21 a caller's labels: '$labels'"
# 21b) (#1677) a bug filed for a person carries ready-for-human on the issue before its card reaches Todo, so no pass
# takes it in the window between the two writes; a bug filed for the loop carries the bug kind and nothing extra.
: > "$f/ghpost"; : > "$f/board.calls"
n=$(SG_FILED_BY=manual SG_FILED_LABELS=ready-for-human bash "$S/autohunt.sh" --file "a defect for a person" "$f/body.md" 2>&1)
case "$n" in *"HUNT filed #42"*) ok "21b the filing for a person lands";; *) ko "21b the filing for a person failed: '$n'";; esac
labels=$($G -oE 'labels\[\]=[^ ]+' "$f/ghpost" | sort -u | tr '\n' ' ')
[ "$labels" = "labels[]=bug labels[]=frozen:not-startable labels[]=ready-for-human " ] && ok "21b a bug filed for a person carries ready-for-human" || ko "21b a person's labels: '$labels'"
$G -qxF "posted=1" "$f/board.calls" && ok "21b the holder label is on the issue before its card reaches Todo" || ko "21b the card came first: $(cat "$f/board.calls")"

# 22) (#1978) the birth policy: every filing carries the publish hold, its card joins Todo, and the board door sets the
# birth Priority (a bug high, a loop-bug urgent) and reads it back. The hold is dropped only after both read back; a
# Priority that does not read back keeps the hold, so the card stays out of dispatch. The spy fails `--priority` on demand.
cat > "$S/board-spy.sh" <<'SPY'
#!/bin/bash
echo "$*" >> "$SGH_BOARD/board.calls"
[ "$1" = --priority ] && [ -n "${SPY_PRIO_FAIL:-}" ] && { echo "#$4: issue $4 does not carry 'high' after the write" >&2; exit 1; }
echo "card ok"
SPY
chmod +x "$S/board-spy.sh"
: > "$f/ghpost"; : > "$f/board.calls"; : > "$GHLOG"
n=$(bash "$S/autohunt.sh" --file "a born bug" "$f/body.md" 2>&1)
$G -qxF -- "--priority o 1 42 high" "$f/board.calls" && ok "22 a bug is born with Priority high" || ko "22 the bug's Priority: $(cat "$f/board.calls")"
[ "$(sed -n 1p "$f/board.calls")" = "--add o 1 Todo 42" ] && ok "22 the card joins Todo before its Priority is set" || ko "22 the board calls: $(cat "$f/board.calls")"
$G -q 'DELETE repos/o/r/issues/42/labels/frozen%3Anot-startable' "$GHLOG" && ok "22 the hold is dropped once the Status and the Priority read back" || ko "22 the hold stays: $(cat "$GHLOG")"
: > "$f/ghpost"; : > "$f/board.calls"; : > "$GHLOG"
n=$(SG_FILED_BY=wave SG_FILED_LABELS=loop-bug bash "$S/autohunt.sh" --file "a born loop bug" "$f/body.md" 2>&1)
$G -qxF -- "--priority o 1 42 urgent" "$f/board.calls" && ok "22 a loop-bug is born with Priority urgent" || ko "22 the loop-bug's Priority: $(cat "$f/board.calls")"
: > "$f/ghpost"; : > "$f/board.calls"; : > "$GHLOG"
n=$(SPY_PRIO_FAIL=1 bash "$S/autohunt.sh" --file "a bug whose priority is lost" "$f/body.md" 2>&1)
case "$n" in *"HUNT filed #42"*) ok "22 a lost Priority never un-files the issue";; *) ko "22 the filing: '$n'";; esac
$G -q 'labels/frozen' "$GHLOG" && ko "22 a card whose Priority does not read back lost its hold: $(cat "$GHLOG")" || ok "22 a card whose Priority does not read back stays held"
case "$n" in *"held"*) ok "22 the held card is named";; *) ko "22 the hold is silent: '$n'";; esac

rm -rf "$T"; exit $fail
