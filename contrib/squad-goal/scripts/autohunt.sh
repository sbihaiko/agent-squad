#!/bin/bash
# usage: autohunt.sh   (watch.sh, every cycle after autobug.sh; AH_DRY=1 prints what it would dispatch)
# When the loop has been idle, start one read-only bug hunt that files reproducible defects (label bug only).
# --file <title> <body-file>   opens the issue through the loop's ONE bug contract -- the [SQUAD] title prefix, the
#                              "Filed by" line and the labels -- puts its card on the board in the dispatch column in
#                              the same call, and logs HUNT filed #N. The hunt files with the bug kind alone; a caller
#                              that is not the hunt names itself in SG_FILED_BY and asks for the startable label beside
#                              the bug kind in SG_FILED_LABELS (a refused write, #145). A write GitHub refused logs
#                              HUNT filed with no number, because a refused filing is not a filed issue (#139).
# --none                       logs HUNT none (a hunt that found nothing)
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/ghcall.sh"; here="$(cd "$(dirname "$0")" && pwd)"; A="$SG_STATE/auto"; mkdir -p "$A" "$SG_STATE/req"
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
now=$(date +%s)
file_issue() {
  local title=$1 body=$2 resp num tagged card by=${SG_FILED_BY:-autohunt} l
  local -a labs=(-f "labels[]=${SG_BUG_LABEL:-bug}")   # the bug kind, so the loop's own dispatcher picks the issue up
  for l in ${SG_FILED_LABELS//,/ }; do [ -n "$l" ] && labs+=(-f "labels[]=$l"); done   # and the label that makes it startable beside it
  case $title in \[*) ;; *) title="[SQUAD] $title";; esac   # every issue the loop files about its own tooling carries the prefix
  tagged=$(mktemp)
  cat "$body" > "$tagged"
  grep -qF "Filed by $by" "$tagged" || echo "Filed by $by" >> "$tagged"
  # The write goes through the door (ADR-0277): `gh api` prints GitHub's error body on stdout and exits non-zero, so a
  # refused filing used to be read back as a number -- the words of a rate limit carry one ("... for user ID 1 ..."),
  # and the filer logged `HUNT filed #1`, an issue nobody opened. A refused write is gh_send's exit 2, whose line is
  # never read as an answer.
  num=""
  if resp=$(gh_send --method POST "repos/$SG_REPO/issues" -f title="$title" -F "body=@$tagged" "${labs[@]}" 2>/dev/null); then
    num=$(printf '%s' "$resp" | python3 -c 'import sys,json,re
t=sys.stdin.read().strip()
try:
  d=json.loads(t)
  if isinstance(d, dict): print(d.get("number") or "")
  elif isinstance(d, int): print(d)
except Exception:
  m=re.search(r"[0-9]+", t); print(m.group(0) if m else "")')
    [ -z "$num" ] && num=$(printf '%s' "$resp" | grep -oE '[0-9]+' | head -1)
  fi
  rm -f "$tagged"
  # The issue is on the board at once (#175): a filed issue with no card is one the next pass cannot move -- board.py
  # answers "#N not on the board", the pass files the write it cannot prove as a defect, and the bug it files has no
  # card either, so every pass files one more (#169 -> #171 -> #174 -> #175). One idempotent add, in the column the
  # dispatcher reads, through the one board door: it is the same add-then-write the board pass makes (janitor rule R),
  # so a card another pass already added is answered "already exists" and written to, never added twice. A refusal is
  # named on stderr and never un-files the issue; the board pass repairs the card on its next run.
  if [ -n "$num" ]; then
    card=$($BOARD --add "$SG_OWNER" "$SG_PROJECT" "${SG_TODO:-Todo}" "$num" 2>&1) \
      || echo "HUNT: the card of #$num is not on the board: $card" >&2
  fi
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
  AB_SKIP_DEBT=1 AB_DRY=1 bash "$here/autobug.sh" 2>/dev/null | grep -q 'would dispatch' && return 0
  AF_DRY=1 bash "$here/autofrontier.sh" 2>/dev/null | grep -q 'would dispatch' && return 0
  return 1
}
hunt_going() {
  local n age
  [ -e "$A/hunt-live" ] || return 1
  n=$(cat "$A/hunt-live" 2>/dev/null)
  [ "$n" = dry ] && return 0
  live_run && return 0
  age=$(( now - $(mtime "$A/hunt-live") ))
  [ "$age" -lt 900 ]
}

live_run && busy
lock_held gui-suite && busy
lock_held single-prland && busy   # the landing pass (#134: the fork pass it replaced is retired)
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

open_debt=$(gh api "repos/$SG_REPO/issues?state=open&labels=debt&per_page=100&sort=created&direction=asc" -q '.[]|select(.pull_request|not)|"#"+(.number|tostring)+" "+.title' 2>/dev/null)
name="hunt-$(date +%Y%m%d-%H%M%S)"; body="$SG_STATE/req/$name.body"
{ echo "IMPORTANT: your first and only action is to call the spawn tool for the 1 child below, in one turn; ending the turn without a spawn call fails the run. Spawn the child with $(bash "$(dirname "$0")/envelope-spend.sh"). Repo $SG_REPO. One child, coding model from models.json. en-US everywhere; no Co-Authored-By and no \"Generated with Claude Code\" line. Rules: $SG_CODING_RULES."
  echo "This is a read-only bug hunt. Do not edit files, do not run git writes, do not open a PR, do not push."
  echo "First source: open debt issues (work these before hunting for new defects) when there is no startable bug."
  if [ -n "$open_debt" ]; then echo "Open debt:"; echo "$open_debt"; else echo "Open debt: none."; fi
  echo "Then rotate among these sources; this hunt's rotated source is $src_name."
  echo "(a) run the squad shell suites and Python suites twice and report failures/flakes;"
  echo "(b) read code merged in the last 24h (git log) for defects;"
  echo "(c) open-question / needs-human notes of recent verdicts;"
  echo "(d) areas recent bugs came from."
  echo "May file: only a real defect with a reproduction, ideally a failing test whose output is in the issue. Search open and closed issues first; never file a duplicate; at most 3 bugs per hunt; no style opinions / architecture trade-offs (those go /adr). Each issue gets label \`bug\` and nothing else; body says Squad-only work: ... when the defect is in the agent-squad fork. File with: bash $here/autohunt.sh --file \"<title>\" <body-file>. If nothing qualifies: bash $here/autohunt.sh --none."
  echo "Never: run a GUI scenario, write-back or migration against a scenario's parent issue, delete anything (permanent deletion), spend money, touch credentials, or publish externally."
  echo
  echo "CHILD — read-only hunt (first source: open debt; then $src_name): look for real, reproducible defects and file each as a bug."
} > "$body"
req=$("$here/mkreq.sh" "$name" "$body") || exit 0
echo "$now" > "$A/hunt-activity"
if [ -n "${AH_DRY:-}" ]; then echo dry > "$A/hunt-live"; echo "HUNT started"; exit 0; fi
echo "$name" > "$A/hunt-live"
log="$SG_STATE/$name.log"
(cd "$SG_CHECKOUT" && python3 -c 'import subprocess,sys;subprocess.Popen(["python3",sys.argv[1],sys.argv[2]],stdout=open(sys.argv[3],"w"),stderr=subprocess.STDOUT,start_new_session=True)' "$here/launch.py" "$req" "$log")
echo "HUNT started"
