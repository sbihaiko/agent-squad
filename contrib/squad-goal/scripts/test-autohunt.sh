#!/bin/bash
# Regression test for autohunt.sh: when the loop is idle, start one read-only bug hunt (Refs #1493, #1504).
# Offline: stub gh, autobug, autofrontier, mkreq, launch. usage: bash test-autohunt.sh [path/to/autohunt.sh]
src=${1:-$(dirname "$0")/autohunt.sh}; T=$(mktemp -d); fail=0
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
  cp "$src" "$S/autohunt.sh"
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
  export SG_AUTO_HUNT=yes SG_HUNT_IDLE_MINUTES=0 SG_HUNT_COOLDOWN_MINUTES=0 SG_HUNT_PER_DAY=6
  echo $(( $(date +%s) - 3600 )) > "$SG_STATE/auto/hunt-activity"
}
run() { AH_DRY=1 bash "$S/autohunt.sh" 2>&1; }
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

# 5) forkclose lock held
scene fc
mkdir -p "$SG_STATE/locks/single-forkclose"; echo $$ > "$SG_STATE/locks/single-forkclose/pid"
r=$(run); skipped "$r" && ok "does not start while forkclose is running" || ko "started with forkclose lock: '$r'"

# 6) startable autobug / autofrontier
scene bug; touch "$SG_STATE/auto/startable-bug"
r=$(run); skipped "$r" && ok "does not start while a startable bug exists" || ko "started with startable bug: '$r'"
scene fr; touch "$SG_STATE/auto/startable-frontier"
r=$(run); skipped "$r" && ok "does not start while a startable frontier exists" || ko "started with startable frontier: '$r'"

# 7) PR waiting on a review round
scene rev
export GH_PRS='1	aaaaaaaa	1100'
r=$(run); skipped "$r" && ok "does not start while a PR waits on a review round" || ko "started with a waiting PR: '$r'"
unset GH_PRS

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
extra=$($G -oE 'labels\[\]=[^ ]+' "$T/file/ghpost" 2>/dev/null | $G -cvF 'labels[]=bug' || true)
[ "${extra:-0}" -eq 0 ] && ok "file helper adds only the bug label" || ko "file helper added extra labels: $(cat "$T/file/ghpost")"
$G -qF "title=[SQUAD] a defect" "$T/file/ghpost" && ok "file helper prefixes the title with [SQUAD]" || ko "no [SQUAD] prefix: $(cat "$T/file/ghpost")"
: > "$T/file/ghpost"
bash "$S/autohunt.sh" --file "[SQUAD] already tagged" "$T/file/body.md" >/dev/null 2>&1
$G -qF "title=[SQUAD] already tagged" "$T/file/ghpost" && ! $G -qF "[SQUAD] [SQUAD]" "$T/file/ghpost" \
  && ok "file helper never doubles an existing prefix" || ko "doubled prefix: $(cat "$T/file/ghpost")"
n=$(bash "$S/autohunt.sh" --none 2>&1)
case "$n" in *"HUNT none"*) ok "empty hunt logs HUNT none";; *) ko "none: '$n'";; esac

# 15) watch.sh calls autohunt.sh every cycle after autobug.sh
w=$(cd "$(dirname "$src")" && pwd)/watch.sh
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
cp "$d/autobug.sh" "$d/single.sh" "$d/startability.sh" "$S/"
[ -f "$d/cluster.py" ] && cp "$d/cluster.py" "$S/"
mkdir -p "$T/huntdisp/co/docs/squad/workflows"
echo '{"max_parallel": 10}' > "$T/huntdisp/co/docs/squad/workflows/dynamic.graph.json"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
echo "$*" >> "${GHLOG:-/dev/null}"
case "$*" in
  *pulls*) ;;
  *"issues?state=open"*) printf '42\x1fbug\n';;
  *POST*)
    echo "$*" >> "${GHPOST:-/dev/null}"
    echo "{\"number\":${GH_NEW:-42}}";;
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

rm -rf "$T"; exit $fail
