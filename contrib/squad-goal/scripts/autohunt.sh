#!/bin/bash
# usage: autohunt.sh   (watch.sh, every cycle after autobug.sh; AH_DRY=1 prints what it would dispatch)
# When the loop has been idle, start one read-only bug hunt that files reproducible defects (label bug only).
# --file <title> <body-file>   opens the issue with only the bug label and logs HUNT filed #N
# --none                       logs HUNT none (a hunt that found nothing)
. "$(dirname "$0")/env.sh"; here="$(cd "$(dirname "$0")" && pwd)"; A="$SG_STATE/auto"; mkdir -p "$A" "$SG_STATE/req"
now=$(date +%s)
file_issue() {
  local title=$1 body=$2 resp num
  resp=$(gh api --method POST "repos/$SG_REPO/issues" -f title="$title" -F "body=@$body" -f 'labels[]=bug' 2>/dev/null)
  num=$(printf '%s' "$resp" | python3 -c 'import sys,json,re
t=sys.stdin.read().strip()
try:
  d=json.loads(t)
  if isinstance(d, dict): print(d.get("number") or "")
  elif isinstance(d, int): print(d)
except Exception:
  m=re.search(r"[0-9]+", t); print(m.group(0) if m else "")')
  [ -z "$num" ] && num=$(printf '%s' "$resp" | grep -oE '[0-9]+' | head -1)
  [ -n "$num" ] && echo "HUNT filed #$num" || echo "HUNT filed"
}
case "${1:-}" in
  --file) file_issue "$2" "$3"; exit 0;;
  --none) echo "HUNT none"; exit 0;;
esac
[ "${SG_AUTO_HUNT:-yes}" = yes ] || exit 0
[ -e "$A/off" ] && exit 0

busy() { echo "$now" > "$A/hunt-activity"; exit 0; }
lock_held() { local h; h=$(cat "$SG_STATE/locks/$1/pid" 2>/dev/null); [ -n "$h" ] && kill -0 "$h" 2>/dev/null; }
live_run() {
  local r f pid
  for r in $(cat "$SG_STATE/active-runs.txt" 2>/dev/null); do
    case "$r" in /*) f=$r/record.jsonl;; *) f=$SG_CHECKOUT/runs/$r/record.jsonl;; esac
    [ -f "$f" ] || continue
    grep -q '"event": *"verdict"' "$f" && continue
    pid=$(grep -o '"pid": *[0-9][0-9]*' "$f" | tail -1 | tr -dc '0-9')
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && return 0
  done
  return 1
}
review_waiting() {
  local line pr sha
  while read -r line; do
    pr=$(printf '%s' "$line" | awk '{print $1}' | tr -dc '0-9'); sha=$(printf '%s' "$line" | awk '{print $2}')
    [ -n "$pr" ] || continue
    grep -qE "^#$pr ${sha:0:9} .*VERDICT" "$SG_STATE/revci-$pr.out" 2>/dev/null && continue
    return 0
  done < <(gh api "repos/$SG_REPO/pulls?state=open&per_page=100" 2>/dev/null)
  return 1
}
startable() {
  AB_DRY=1 bash "$here/autobug.sh" 2>/dev/null | grep -q 'would dispatch' && return 0
  AF_DRY=1 bash "$here/autofrontier.sh" 2>/dev/null | grep -q 'would dispatch' && return 0
  return 1
}
hunt_going() {
  local n age
  [ -e "$A/hunt-live" ] || return 1
  n=$(cat "$A/hunt-live" 2>/dev/null)
  [ "$n" = dry ] && return 0
  live_run && return 0
  age=$(( now - $(stat -f %m "$A/hunt-live" 2>/dev/null || echo 0) ))
  [ "$age" -lt 900 ]
}

live_run && busy
lock_held gui-suite && busy
lock_held single-forkclose && busy
startable && busy
review_waiting && busy

idle_m=${SG_HUNT_IDLE_MINUTES:-10}
act=$(cat "$A/hunt-activity" 2>/dev/null || echo 0)
[ $(( now - act )) -ge $(( idle_m * 60 )) ] || exit 0

hunt_going && exit 0
rm -f "$A/hunt-live"

cool=${SG_HUNT_COOLDOWN_MINUTES:-60}
last=$(cat "$A/hunt-last" 2>/dev/null || echo 0)
[ $(( now - last )) -ge $(( cool * 60 )) ] || exit 0

day=$(date +%F); cap=${SG_HUNT_PER_DAY:-6}; d=; n=0
[ -f "$A/hunt-day" ] && read -r d n < "$A/hunt-day"
[ "$d" = "$day" ] || n=0
[ "${n:-0}" -lt "$cap" ] || exit 0

src_i=$(cat "$A/hunt-source" 2>/dev/null || echo 0)
src_i=$(( src_i % 4 ))
case $src_i in
  0) src_name="(a) run the squad shell suites and Python suites twice and report failures/flakes";;
  1) src_name="(b) read code merged in the last 24h (git log) for defects";;
  2) src_name="(c) open-question / needs-human notes of recent verdicts";;
  3) src_name="(d) areas recent bugs came from";;
esac
echo $(( (src_i + 1) % 4 )) > "$A/hunt-source"

name="hunt-$(date +%Y%m%d-%H%M%S)"; body="$SG_STATE/req/$name.body"
{ echo "IMPORTANT: your first and only action is to call the spawn tool for the 1 child below, in one turn; ending the turn without a spawn call fails the run. Spawn the child with spend 8 (the router may not request more: spend_outside_budget). Repo $SG_REPO. One child, coding model from models.json. en-US everywhere; no Co-Authored-By and no \"Generated with Claude Code\" line. Rules: $SG_CODING_RULES."
  echo "This is a read-only bug hunt. Do not edit files, do not run git writes, do not open a PR, do not push."
  echo "Rotate among these sources; this hunt's source is $src_name."
  echo "(a) run the squad shell suites and Python suites twice and report failures/flakes;"
  echo "(b) read code merged in the last 24h (git log) for defects;"
  echo "(c) open-question / needs-human notes of recent verdicts;"
  echo "(d) areas recent bugs came from."
  echo "May file: only a real defect with a reproduction, ideally a failing test whose output is in the issue. Search open and closed issues first; never file a duplicate; at most 3 bugs per hunt; no style opinions / architecture trade-offs (those go /adr). Each issue gets label \`bug\` and nothing else; body says Fork-only work: ... when the defect is in the agent-squad fork. File with: bash $here/autohunt.sh --file \"<title>\" <body-file>. If nothing qualifies: bash $here/autohunt.sh --none."
  echo "Never: run a GUI scenario, write-back or migration against a scenario's parent issue, delete anything (permanent deletion), spend money, touch credentials, or publish externally."
  echo
  echo "CHILD — read-only hunt (source $src_name): look for real, reproducible defects and file each as a bug."
} > "$body"
req=$("$here/mkreq.sh" "$name" "$body") || exit 0
echo "$now" > "$A/hunt-last"; echo "$now" > "$A/hunt-activity"
printf '%s %s\n' "$day" "$((n+1))" > "$A/hunt-day"
if [ -n "${AH_DRY:-}" ]; then echo dry > "$A/hunt-live"; echo "HUNT started"; exit 0; fi
echo "$name" > "$A/hunt-live"
log="$SG_STATE/$name.log"
(cd "$SG_CHECKOUT" && python3 -c 'import subprocess,sys;subprocess.Popen(["python3",sys.argv[1],sys.argv[2]],stdout=open(sys.argv[3],"w"),stderr=subprocess.STDOUT,start_new_session=True)' "$here/launch.py" "$req" "$log")
echo "HUNT started"
