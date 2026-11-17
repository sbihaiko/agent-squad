#!/bin/bash
# usage: autohunt.sh   (watch.sh, every cycle after autobug.sh; AH_DRY=1 prints what it would dispatch)
# When the loop has been idle, start one read-only bug hunt that files reproducible defects (label bug only).
# --file <title> <body-file> [--fingerprint <fp>]   opens the issue through the loop's ONE bug contract -- the [SQUAD]
#                              title prefix, the "Filed by" line and the labels -- puts its card on the board in the
#                              dispatch column in the same call, and logs HUNT filed #N. The hunt files with the bug kind
#                              alone; a caller that is not the hunt names itself in SG_FILED_BY and asks for the startable
#                              label beside the bug kind in SG_FILED_LABELS (a refused write, #145), and names the
#                              fingerprint of the cause it files with --fingerprint (sg_refusal_name, #145).
# --none                       logs HUNT none (a hunt that found nothing)
#
# One defect storm, one bounded spend (#140). A filing belongs to a PASS -- SG_FILE_PASS names it, else the hunt that is
# running names it by its own run -- and one pass files at most $SG_FILE_CEILING new issues, so a storm of defects cannot
# starve a landing. The ceiling is the one number declared below, and the hunt request asks a child for that same
# number. A filing the ceiling held back is logged `HUNT hold <fp> <name>`, with the fingerprint it would have filed,
# and never dropped in silence. The fingerprint is the CAUSE, never the occurrence: a pass that already filed that
# fingerprint opens no second issue and logs `HUNT dup #N <fp>`. The filing is a write through the one door (ghcall.sh),
# so its calls land in the loop's own accounting under the pass that spends them (ADR-0277); a filing GitHub refused
# spends no line of the pass's ledger and is named `HUNT refused`, never read back as a filed issue. A filing that names
# no pass is a pass of one: one call, one issue, nothing to hold back.
. "$(dirname "$0")/env.sh"; here="$(cd "$(dirname "$0")" && pwd)"; A="$SG_STATE/auto"; mkdir -p "$A" "$SG_STATE/req"
. "$here/caps.sh" 2>/dev/null || : "${SG_PS:=ps}"   # SG_PS (#1508)
. "$here/ghcall.sh"   # the one door to GitHub (#8, ADR-0277): a filing is a write, and it is logged like every other
export SG_GH_PASS=${SG_GH_PASS:-autohunt}   # the pass the call log names for the filer's own calls, when the caller names none
# The ceiling on the new issues ONE PASS files (#140), declared in one place: the filing below enforces this number and
# the hunt request asks a child for the same one, so the bound and the instruction about it cannot drift apart. A value
# that is not a positive count is the default, and a bad number in the loop's environment never lifts the ceiling.
file_ceil=${SG_FILE_CEILING:-3}
case $file_ceil in ''|*[!0-9]*|0) file_ceil=3;; esac
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
now=$(date +%s)
# live_run: a run of this repository whose record carries no verdict and whose pid is still alive. The pass a hunt's
# filings belong to is a hunt that is RUNNING: one that has ended leaves no live run, so a filing made after it is never
# counted against a ledger nobody will reset.
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
# fingerprint_of <name>: the cause a filing is for, never the occurrence -- the name the loop filed it under,
# lowercased and whitespace-collapsed, then hashed. A caller that knows the cause names its own with --fingerprint.
fingerprint_of() {
  printf '%s' "$1" | python3 -c 'import hashlib,sys;print(hashlib.sha1(" ".join(sys.stdin.read().lower().split()).encode()).hexdigest()[:16])' 2>/dev/null
}
# file_pass: the pass this filing belongs to, by the name its ledger is kept under. SG_FILE_PASS when the caller names
# one (the pass that hit a refused write names the run it files in), else the hunt that is running, by its own run name.
# Empty when a filing names no pass: that filing is a pass of one, and one filing is never held.
file_pass() {
  local h
  if [ -n "${SG_FILE_PASS:-}" ]; then printf '%s' "$SG_FILE_PASS"; return 0; fi
  h=$(cat "$A/hunt-live" 2>/dev/null)
  case $h in ""|dry) return 0;; esac
  live_run && printf '%s' "$h"
}
# filed_ledger <pass>: the issues that pass has filed, one `<fingerprint> <number>` a line. It is keyed by the pass, so
# the ceiling is that pass's own bound and no other pass waits behind it.
filed_ledger() { printf '%s/filed-%s' "$A" "$(printf '%s' "$1" | tr -c 'A-Za-z0-9._-' _)"; }
file_issue() {
  local title=$1 name=$1 body=$2 fp=${3:-} pass led dup count resp rc num tagged card by=${SG_FILED_BY:-autohunt} l
  local hold=frozen:not-startable prio kinds="${SG_BUG_LABEL:-bug} ${SG_FILED_LABELS//,/ }"
  # The publish hold (#1978): the issue is born held, so no pass dispatches it before its Status and Priority read back.
  local -a labs=(-f "labels[]=${SG_BUG_LABEL:-bug}" -f "labels[]=$hold")   # the bug kind, so the loop's own dispatcher picks the issue up
  for l in ${SG_FILED_LABELS//,/ }; do [ -n "$l" ] && labs+=(-f "labels[]=$l"); done   # and the label that makes it startable beside it
  [ -n "$fp" ] || fp=$(fingerprint_of "$name"); [ -n "$fp" ] || fp=000000000000
  pass=$(file_pass)
  if [ -n "$pass" ]; then
    led=$(filed_ledger "$pass")
    dup=$(awk -v f="$fp" '$1==f{print $2; exit}' "$led" 2>/dev/null)
    if [ -n "$dup" ]; then echo "HUNT dup #$dup $fp"; return 0; fi   # the pass already filed this cause: one defect, one issue
    count=0; [ -f "$led" ] && count=$(wc -l < "$led" | tr -d ' ')
    if [ "$count" -ge "$file_ceil" ]; then echo "HUNT hold $fp $name"; return 0; fi   # held by the ceiling, and named, never silent
  fi
  case $title in \[*) ;; *) title="[SQUAD] $title";; esac   # every issue the loop files about its own tooling carries the prefix
  tagged=$(mktemp)
  cat "$body" > "$tagged"
  grep -qF "Filed by $by" "$tagged" || echo "Filed by $by" >> "$tagged"
  # The write goes through the door (ADR-0277). `gh api` prints GitHub's error body on stdout and exits non-zero, so a
  # refused filing used to be read back as a number: the body carries digits ("... for user ID 1 ..."), and a rate-limit
  # message became a filed issue nobody opened. A refused write is gh_send's exit 2, whose line is never read as an answer.
  num=""
  resp=$(gh_send --method POST "repos/$SG_REPO/issues" -f title="$title" -F "body=@$tagged" "${labs[@]}" 2>/dev/null); rc=$?
  rm -f "$tagged"
  # A refusal (2) and a failure before GitHub reached a verdict (1) are each named with their fingerprint and their name,
  # and each spends no line of the pass's ledger: a storm of refusals must not consume the ceiling a real defect needs.
  # The refused write's own words are never echoed, and its line is never read as a filed issue (#139).
  if [ "$rc" -eq 2 ]; then echo "HUNT refused $fp $name"; return 0; fi
  if [ "$rc" -ne 0 ]; then echo "HUNT failed $fp $name"; return 0; fi
  num=$(printf '%s' "$resp" | python3 -c 'import sys,json,re
t=sys.stdin.read().strip()
try:
  d=json.loads(t)
  if isinstance(d, dict): print(d.get("number") or "")
  elif isinstance(d, int): print(d)
except Exception:
  m=re.search(r"[0-9]+", t); print(m.group(0) if m else "")')
  [ -z "$num" ] && num=$(printf '%s' "$resp" | grep -oE '[0-9]+' | head -1)
  [ -n "$num" ] && [ -n "$pass" ] && printf '%s %s\n' "$fp" "$num" >> "$led"   # the pass's ledger: one line per issue it filed
  # The issue is on the board at once (#175): a filed issue with no card is one the next pass cannot move -- board.py
  # answers "#N not on the board", the pass files the write it cannot prove as a defect, and the bug it files has no
  # card either, so every pass files one more (#169 -> #171 -> #174 -> #175). One idempotent add, in the column the
  # dispatcher reads, through the one board door: it is the same add-then-write the board pass makes (janitor rule R),
  # so a card another pass already added is answered "already exists" and written to, never added twice. A refusal is
  # named on stderr and never un-files the issue; the board pass repairs the card on its next run.
  # The birth Priority (#1978) is the one table's (priority_backfill.py): a bug high, a loop-bug urgent. The board door
  # writes it only on a change and reads it back. The hold is dropped only when the Status and the Priority both read
  # back; else it stays, the card stays out of dispatch, and the hold is named.
  if [ -n "$num" ]; then
    if card=$($BOARD --add "$SG_OWNER" "$SG_PROJECT" "${SG_TODO:-Todo}" "$num" 2>&1) \
      && prio=$(python3 "$here/priority_backfill.py" $kinds 2>/dev/null) && [ -n "$prio" ] \
      && card=$($BOARD --priority "$SG_OWNER" "$SG_PROJECT" "$num" "$prio" 2>&1); then
      gh_send --method DELETE "repos/$SG_REPO/issues/$num/labels/frozen%3Anot-startable" >/dev/null 2>&1 \
        || echo "HUNT: #$num stays held by $hold: the hold's removal was refused" >&2
    else
      echo "HUNT: #$num stays held by $hold: its Status or Priority does not read back: ${card:-no Priority}" >&2
    fi
  fi
  [ -n "$num" ] && echo "HUNT filed #$num" || echo "HUNT filed"
}
busy() { echo "$now" > "$A/hunt-activity"; exit 0; }
lock_held() { local h; h=$(cat "$SG_STATE/locks/$1/pid" 2>/dev/null); [ -n "$h" ] && kill -0 "$h" 2>/dev/null && [ "$($SG_PS -o stat= -p "$h" 2>/dev/null | cut -c1)" != Z ]; }
# A work branch whose tip has not been reviewed yet (#357): the review pass will spend a slot on it, so a hunt waits. It is a
# git read of the remote heads, never a GitHub call.
review_waiting() {
  local remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git} sha ref br key
  while read -r sha ref; do
    br=${ref#refs/heads/}; case "$br" in fix/*|gui/*) ;; *) continue;; esac
    key=$(printf '%s' "$br" | tr / _)
    [ "$(cat "$SG_STATE/review-$key.sha" 2>/dev/null)" = "$sha" ] && continue
    return 0
  done < <(/usr/bin/git ls-remote --heads "$remote" 2>/dev/null | awk '{print $1, $2}')
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

# The filer's own door, before the cycle's decision: a hunt's child files an issue with it, and the pass that hit a
# refused write asks the same helper. Nothing below this line runs for either.
case "${1:-}" in
  --file)
    title=$2; body=$3; shift 3; fp=
    while [ $# -gt 0 ]; do case "$1" in --fingerprint) fp=${2:-}; shift 2;; *) shift;; esac; done
    file_issue "$title" "$body" "$fp"; exit 0;;
  --none) echo "HUNT none"; exit 0;;
esac
[ "${SG_AUTO_HUNT:-yes}" = yes ] || exit 0
[ -e "$A/off" ] && exit 0

live_run && busy
lock_held gui-suite && busy
lock_held single-branchland && busy   # the landing pass (#355: the fork pass it replaced is retired)
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
{ echo "IMPORTANT: your first and only action is to return the \"spawns\" list in your final output, one entry for the 1 child below, in one turn; ending the turn without that list fails the run. No tool call makes a child: the run reads the list. The child's spend: $(bash "$(dirname "$0")/envelope-spend.sh"). Repo $SG_REPO. One child, coding model from models.json. en-US everywhere; no Co-Authored-By and no \"Generated with Claude Code\" line. Rules: $SG_CODING_RULES."
  echo "This is a read-only bug hunt. Do not edit files, do not run git writes, do not push a branch."
  echo "First source: open debt issues (work these before hunting for new defects) when there is no startable bug."
  if [ -n "$open_debt" ]; then echo "Open debt:"; echo "$open_debt"; else echo "Open debt: none."; fi
  echo "Then rotate among these sources; this hunt's rotated source is $src_name."
  echo "(a) run the squad shell suites and Python suites twice and report failures/flakes;"
  echo "(b) read code merged in the last 24h (git log) for defects;"
  echo "(c) open-question / needs-human notes of recent verdicts;"
  echo "(d) areas recent bugs came from."
  echo "May file: only a real defect with a reproduction, ideally a failing test whose output is in the issue. Search open and closed issues first; never file a duplicate; at most $file_ceil bugs per hunt; no style opinions / architecture trade-offs (those go /adr). Each issue gets label \`bug\` and nothing else; body says Squad-only work: ... when the defect is in the agent-squad fork. The fingerprint is the cause, never the occurrence: name it with --fingerprint, one defect to one cause, so the same cause twice in one hunt is one issue. File with: bash $here/autohunt.sh --file \"<title>\" <body-file> --fingerprint <cause>. If nothing qualifies: bash $here/autohunt.sh --none."
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
