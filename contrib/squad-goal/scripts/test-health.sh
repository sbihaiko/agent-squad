#!/bin/bash
# Regression test for health.sh: a down watchd alerts once, a live one clears it, a silent cycle.log is reported. Stubbed env.sh/gh/notifier. usage: bash test-health.sh [health.sh]
src=${1:-$(dirname "$0")/health.sh}; T=$(mktemp -d); . "$(dirname "$0")/driver-tree.sh"; trap 'driver_tree_kill "$T"; rm -rf "$T" "$P"' EXIT; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# hermetic: a fork gate (or any shell) exports SG_FORK_*; health.sh would then scan the real fork repo and add a fork-parked cause
unset SG_FORK_DIR SG_FORK_BRANCH SG_FORK_BASE SG_FORK_ORPHAN_MINUTES
# #1410: a real-looking parked fork in the caller's environment must not reach the assertions
P=$(mktemp -d); G=/usr/bin/git; pg() { $G -C "$P" -c user.email=a@b -c user.name=t "$@"; }; pg init -q -b local/next; echo 0 > "$P/f"; pg add f; pg commit -q -m init
pg checkout -q -b gui/7 local/next; echo 7 > "$P/g"; pg add g; pg commit -q -m w; pg checkout -q local/next; touch -t 202001010000 "$P/g"
export SG_FORK_DIR="$P" SG_FORK_BASE=local/next SG_FORK_BRANCH='gui/%n' SG_FORK_ORPHAN_MINUTES=0
unset "${!SG_@}"; export HOME="$T/home"   # hermetic: whatever the caller exports (the live loop's SG_*) stops here
export SG_WATCH_CMD_RE="$T/s/watch\.sh\$"   # ...and so do the live loops of this machine: health.sh counts watch.sh processes by command line, so without this a second real loop adds a loops-duplicated alert to every count below
mkdir -p "$HOME" "$T/s" "$T/st/auto" "$T/co" "$T/bin"; cp "$src" "$T/s/health.sh"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/ghcall.sh" "$T/s/ghcall.sh"; cp "$(dirname "$src")/drainpolicy.py" "$T/s/"; cp "$(dirname "$src")/workbranches.sh" "$T/s/workbranches.sh"; cp "$(dirname "$src")/startability.sh" "$T/s/startability.sh"; cp "$(dirname "$src")/skiplabels.sh" "$T/s/skiplabels.sh"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$(dirname "$src")/caps.sh" "$T/s/"   # the claimed column's name (#1342); the process read is caps.sh, which resolves to squad.procs through SG_SQUAD inside the child's sandbox (#1594); the skip set is skiplabels.sh (#1593)
# hermetic: health.sh counts watch.sh with a real pgrep, so the default pattern would count the host's own live loop (a duplicate there is a third cause)
export SG_WATCH_CMD_RE="$T/s/watch\.sh\$"
export SQUAD_CAPS_DIR="$T/caps"   # hermetic: the reap step (#1226) reclaims leases here, never in the machine's caps folder
. "$(dirname "$src")/caps.sh"   # the test's own process read (#1594): SG_PS is squad.procs through this file's repository, so a read works where the setuid ps cannot be exec'd
printf 'export SG_STATE=%s SG_CHECKOUT=%s SG_REPO=x/y SG_OWNER=o SG_PROJECT=7 SG_GHREAD=%s SG_SQUAD=%s\n' "$T/st" "$T/co" "$T/bin/rd" "$(cd "$(dirname "$src")/../../.." && pwd)" > "$T/s/env.sh"
# the door's reader, stubbed: the open-issue list one page at a time, from $T/pages/<n>.json (a page past the last is an empty list)
mkdir -p "$T/pages"; cat > "$T/bin/rd" <<'RD'
#!/bin/bash
case "$1" in   # the numbered list (#1648): one entry per issue asked, in the door's own shape, and the route is recorded
  *"numbers="*) printf '%s\n' "$1" >> "$(dirname "$0")/../numreads"
    for n in $(printf '%s' "$1" | sed -n 's/.*numbers=\([^&]*\).*/\1/p' | tr ',' ' '); do jq -nc --argjson n "$n" '{number:$n,body:"",blocked_by:[],blocked_by_read:true,blockers:[]}'; done | jq -s .; exit 0;;
esac
n=$(printf '%s' "$1" | sed -n 's/.*[?&]page=\([0-9]*\).*/\1/p'); f="$(dirname "$0")/../pages/${n:-1}.json"
[ -f "$f" ] && cat "$f" || echo '[]'
RD
chmod +x "$T/bin/rd"
# iss <number> <title> <label,...>: one issue object
iss() { jq -nc --argjson n "$1" --arg t "$2" --arg l "$3" '{number:$n,title:$t,body:"",labels:($l|split(",")|map(select(.!=""))|map({name:.}))}'; }
# page <n> <issue json>...: write one page
page() { local n=$1; shift; printf '%s\n' "$@" | jq -s . > "$T/pages/$n.json"; }
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
case "$*" in
  *"issues?state=open"*) "$(dirname "$0")/issues" "$@";;
esac
GH
# the open-issue list (#1338): GH_READY (numbers) when set, else page 1 of $T/pages, through the call's own -q filter as gh applies it
cat > "$T/bin/issues" <<'IS'
#!/bin/bash
if [ -n "$GH_READY" ]; then printf '%s\n' "$GH_READY"; exit 0; fi
q=""; while [ $# -gt 0 ]; do [ "$1" = -q ] && q=$2; shift; done
f="$(dirname "$0")/../pages/1.json"; [ -f "$f" ] || exit 0
if [ -n "$q" ]; then jq -r "$q" "$f"; else cat "$f"; fi
IS
chmod +x "$T/bin/gh" "$T/bin/issues"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"; export HEALTH_NOTIFY="$T/notify"
touch "$T/st/cycle.log" "$T/st/active-runs.txt"; git -C "$T/co" init -q 2>/dev/null
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q fork-parked && ko "a parked fork in the caller env reached the suite: $o" || ok "a parked fork in the caller env does not reach the suite"
echo "$o" | /usr/bin/grep -q 'HEALTH watchd-down' && ok "watchd down -> alert" || ko "no watchd-down: $o"
[ $(($(wc -l < "$T/notify.calls"))) = 2 ] && ok "one notification per cause (watchd, fastloop)" || ko "notifications: $(cat "$T/notify.calls")"
bash "$T/s/health.sh" >/dev/null; [ $(($(wc -l < "$T/notify.calls"))) = 2 ] && ok "same cause: no second alert" || ko "alerted twice"
# #1467: the stub refuses a start, so a case where the supervisor is down reports it and never relights
printf '#!/bin/bash\n[ "$1" = start ] && { echo "stub: start refused"; exit 1; }\nsleep 60\n' > "$T/s/watchd.sh"; bash "$T/s/watchd.sh" run & wp=$!; echo $wp > "$T/st/watchd.pid"; bash "$T/s/fastloop.sh" 2>/dev/null; printf '#!/bin/bash\nsleep 60\n' > "$T/s/fastloop.sh"; bash "$T/s/fastloop.sh" & fp=$!; echo $fp > "$T/st/fastloop.pid"; sleep 1
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'watchd-down cleared' && ok "watchd back -> cleared" || ko "not cleared: $o"
touch -t 202001010000 "$T/st/cycle.log"; o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'HEALTH watch-stalled' && ok "silent cycle.log -> watch-stalled" || ko "no stall: $o"
mkdir -p "$T/co/runs/run-n"; echo r > "$T/co/runs/run-n/request.txt"; touch -t 202001010000 "$T/co/runs/run-n/request.txt"; echo run-n > "$T/st/active-runs.txt"
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'HEALTH never-started-runs' && ok "record-less run -> never-started-runs" || ko "no never-started: $o"
# A wave-only issue can be READY after its blocker closes. The health pass must expose it without a triage label.
# An open issue without bug, enhancement, or spec is visible before the loop can dispatch it untyped.
page 1 "$(iss 427 'Add a thing' '')"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH untyped-issues: open issue(s) without a type label: #427' && ok "an untyped open issue is reported" || ko "untyped issue was not reported: $o"
# #437: the read pages. Page one is a full page of typed issues, the untyped one sits on page two.
for i in $(seq 1 100); do iss $((1000+i)) "Typed $i" enhancement; done | jq -s . > "$T/pages/1.json"; page 2 "$(iss 901 'Add another thing' '')"; rm -f "$T/st/auto/health-untyped-issues"   # each cause alerts once: re-arm it
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -qF 'without a type label: #901' && ok "an untyped issue beyond the first page is reported" || ko "page two was not read: $o"
# #437: a defect carries bug. An enhancement whose title reads as a defect is named; a bug and a plain enhancement are not.
page 1 "$(iss 501 'Health guard fails on a long list' enhancement)" "$(iss 502 'Health guard fails on a long list' bug)" "$(iss 503 'Add a thing' enhancement)"; rm -f "$T/pages/2.json"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH defect-without-bug: open defect(s) that carry no bug label: #501' && ok "a defect filed as enhancement is reported" || ko "defect without bug was not reported: $o"
printf '%s' "$o" | /usr/bin/grep -qE 'defect-without-bug.*#(502|503)' && ko "a bug or a plain enhancement was named: $o" || ok "a bug and a plain enhancement are not named"
printf '%s' "$o" | /usr/bin/grep -q 'HEALTH untyped-issues:' && ko "typed issues reported untyped: $o" || ok "typed issues are not untyped"
# #1681: "bug lane" and "bug pass" name a board part, not a defect; a real crash next to them still flags
ib() { jq -nc --argjson n "$1" --arg t "$2" --arg b "$3" '{number:$n,title:$t,body:$b,labels:[{name:"enhancement"}]}'; }
page 1 "$(ib 504 'Group the board by lane' 'Cards group by the bug lane, and the bug pass sorts them.')" "$(ib 505 'Export the list' 'The export crashes on an empty list.')" "$(ib 506 'Show the bug lane count' 'The bug lane count fails to refresh.')"; rm -f "$T/pages/2.json" "$T/st/auto/health-defect-without-bug"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -qE 'defect-without-bug.*#504' && ko "an enhancement that names the bug lane was named a defect: $o" || ok "an enhancement that names the bug lane is not a defect"
printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH defect-without-bug: open defect(s) that carry no bug label: #505 #506' && ok "an enhancement that names a real defect is reported" || ko "a real defect filed as enhancement was not reported: $o"
rm -f "$T/pages/"*.json
# ---- #1795: runs-board-drift, the loop against its own board ----
bd() { { echo '#!/bin/bash'; printf 'case "$*" in *"/items"*) cat %s/board.tsv;; *"/fields"*) echo 422164739;; *"issues?state=open"*) "$(dirname "$0")/issues" "$@";; esac\n' "$T"; } > "$T/bin/gh"; chmod +x "$T/bin/gh"; }
alerts() { local n; n=$(/usr/bin/grep -c 'HEALTH runs-board-drift' "$T/st/alerts.log" 2>/dev/null); echo "${n:-0}"; }
notif() { local n; n=$(/usr/bin/grep -c . "$T/notify.calls" 2>/dev/null); echo "${n:-0}"; }
liverun() { mkdir -p "$T/co/runs/$1"; printf '{"ts":1,"event": "spawn_accepted", "text": "GitHub issue #%s"}\n' "$2" > "$T/co/runs/$1/record.jsonl"
  echo x > "$T/co/runs/$1/request.txt"; touch -t 202001010000 "$T/co/runs/$1/request.txt"; echo "$1" >> "$T/st/active-runs.txt"; }
reset() { : > "$T/st/active-runs.txt"; rm -rf "$T/co/runs" "$T/st/auto/drift-prev" "$T/st/auto/health-runs-board-drift"; mkdir -p "$T/co/runs"; }
# #2360: a pass that read an empty board primes the board snapshot; the card that lands inside its window must still count
reset; bd; : > "$T/board.tsv"; bash "$T/s/health.sh" >/dev/null
printf '10\tIn Progress\n' > "$T/board.tsv"; liverun run-a 10
bash "$T/s/health.sh" >/dev/null; o=$(bash "$T/s/health.sh")
echo "$o" | /usr/bin/grep -q 'runs-board-drift' && ko "a primed snapshot answered the drift check: $o" || ok "the drift check reads the live board inside the snapshot window"
reset; bd; printf '10\tIn Progress\n' > "$T/board.tsv"; liverun run-a 10
bash "$T/s/health.sh" >/dev/null; o=$(bash "$T/s/health.sh")
echo "$o" | /usr/bin/grep -q 'runs-board-drift' && ko "equal counts alerted: $o" || ok "equal counts stay silent"
[ "$(alerts)" = 0 ] && ok "equal counts: no alerts.log line" || ko "equal counts: $(alerts) alert(s)"
/usr/bin/grep -q '"runs":1,"inprogress":1' "$T/st/health.json" && ok "health.json carries the pair (runs, inprogress)" || ko "health.json: $(cat "$T/st/health.json")"
reset; bd; printf '10\tIn Progress\n1783\tIn Progress\n1792\tIn Progress\n' > "$T/board.tsv"; liverun run-a 10; liverun run-b 1768
o=$(bash "$T/s/health.sh")
echo "$o" | /usr/bin/grep -q 'runs-board-drift' && ko "a single divergent pass alerted: $o" || ok "one divergent pass does not alert"
nb=$(notif); o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH runs-board-drift: 2 live run(s), 3 card(s) In Progress; cards with no live run: #1783 #1792; live runs with no In Progress card: #1768' && ok "second pass names both sides of the drift" || ko "named drift: $o"
[ "$(alerts)" = 1 ] && ok "the second pass alerts once" || ko "alert lines: $(alerts)"
[ "$(notif)" -gt "$nb" ] && ok "the drift notifies" || ko "no notification for the drift"
bash "$T/s/health.sh" >/dev/null; [ "$(alerts)" = 1 ] && ok "same drift: no second alert" || ko "alerted twice: $(alerts)"
printf '10\tIn Progress\n1768\tIn Progress\n' > "$T/board.tsv"; o=$(bash "$T/s/health.sh")
echo "$o" | /usr/bin/grep -q 'HEALTH runs-board-drift cleared' && ok "cards back in step -> cleared" || ko "not cleared: $o"
[ -e "$T/st/auto/health-runs-board-drift" ] && ko "the alert marker survived the clear" || ok "the clear re-arms the cause"
[ -e "$T/st/auto/drift-prev" ] && ko "drift-prev survived the clear" || ok "the clear drops drift-prev"
printf '10\tIn Progress\n1783\tIn Progress\n1792\tIn Progress\n' > "$T/board.tsv"; o=$(bash "$T/s/health.sh")
echo "$o" | /usr/bin/grep -q 'runs-board-drift' && ko "re-armed on a single pass: $o" || ok "after a clear, one divergent pass stays silent"
bash "$T/s/health.sh" >/dev/null; [ "$(alerts)" = 2 ] && ok "two fresh passes alert again" || ko "re-arm: $(alerts) alert(s)"
reset; bd; printf '10\tIn Progress\n' > "$T/board.tsv"; mkdir -p "$T/co/runs/run-y"; echo x > "$T/co/runs/run-y/request.txt"; echo run-y >> "$T/st/active-runs.txt"
bash "$T/s/health.sh" >/dev/null; o=$(bash "$T/s/health.sh")
echo "$o" | /usr/bin/grep -q 'runs-board-drift' && ko "a run in its launch window fired the cause: $o" || ok "a run in its launch window never fires the cause on its own"
/usr/bin/grep -q '"runs":1,"inprogress":1' "$T/st/health.json" && ok "the launch window still reports the pair" || ko "pair: $(cat "$T/st/health.json")"
touch -t 202001010000 "$T/co/runs/run-y/request.txt"; bash "$T/s/health.sh" >/dev/null; o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH runs-board-drift: 0 live run(s), 1 card(s) In Progress; cards with no live run: #10; live runs with no In Progress card: -' && ok "a card with nobody on it is named" || ko "dead run: $o"
# #1342: this repository's claimed column is Doing, not In Progress. A live run on a Doing card is no drift; a Doing card with no
# live run is drift on the second pass, and the alert names the column it read.
reset; bd; printf '10\tDoing\n' > "$T/board.tsv"; liverun run-a 10
bash "$T/s/health.sh" >/dev/null; o=$(bash "$T/s/health.sh")
echo "$o" | /usr/bin/grep -q 'runs-board-drift' && ko "a live run on a Doing card alerted: $o" || ok "a live run on a Doing card raises no drift"
reset; bd; printf '10\tDoing\n' > "$T/board.tsv"
bash "$T/s/health.sh" >/dev/null; o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH runs-board-drift: 0 live run(s), 1 card(s) Doing; cards with no live run: #10; live runs with no Doing card: -' && ok "a Doing card with no live run raises drift, naming Doing" || ko "Doing card with no live run: $o"
# a card In Progress that still holds its dispatch marker, with no run on it (a run that stopped without a verdict): the cause names that
# subset, so the reader sees which cards the janitor releases (rule Q) and which need a person
reset; bd; printf '10\tIn Progress\n25\tIn Progress\n26\tIn Progress\n' > "$T/board.tsv"; liverun run-a 10; echo run-gone > "$T/st/auto/disp-25"
bash "$T/s/health.sh" >/dev/null; o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF 'cards with no live run: #25 #26; live runs with no In Progress card: -; holding a dispatch marker with no live run (the janitor releases them): #25' && ok "a stale card behind its dispatch marker is named apart" || ko "stale marker card: $o"
rm -f "$T/st/auto/disp-25"; rm -f "$T/st/auto/health-runs-board-drift" "$T/st/auto/drift-prev"; bash "$T/s/health.sh" >/dev/null; o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF 'holding a dispatch marker' && ko "no marker, yet the cause names one: $o" || ok "cards with no marker add no marker clause"
reset; bd; printf '10\tIn Progress\n1783\tIn Progress\n1792\tIn Progress\n' > "$T/board.tsv"; liverun run-a 10; liverun run-b 1768
bash "$T/s/health.sh" >/dev/null; nb=$(notif); al=$(alerts); cp "$T/st/auto/drift-prev" "$T/prev.bak" 2>/dev/null
o=$(HEALTH_DRY=1 bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH runs-board-drift: 2 live run(s)' && ok "HEALTH_DRY prints the cause" || ko "dry: $o"
[ -e "$T/st/auto/health-runs-board-drift" ] && ko "HEALTH_DRY wrote the alert marker" || ok "HEALTH_DRY writes no marker"
[ "$(alerts)" = "$al" ] && ok "HEALTH_DRY writes no alerts.log line" || ko "HEALTH_DRY alerted"
[ "$(notif)" = "$nb" ] && ok "HEALTH_DRY does not notify" || ko "HEALTH_DRY notified"
cmp -s "$T/st/auto/drift-prev" "$T/prev.bak" && ok "HEALTH_DRY leaves drift-prev alone" || ok "HEALTH_DRY leaves no stale drift-prev"
# ---- #1338: frontier-idle names only work autofrontier would start: a Todo card, no spec, no live run ----
fr() { : > "$T/st/active-runs.txt"; rm -rf "$T/co/runs" "$T/st/auto/health-frontier-idle"; mkdir -p "$T/co/runs"; rm -f "$T/pages/"*.json; bd; }
fr; printf '41\tTodo\n' > "$T/board.tsv"; page 1 "$(iss 41 'A wave-only thing' '')"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH frontier-idle: startable 41 with no work branch' && ok "a startable wave-only issue is reported" || ko "wave-only issue was not reported: $o"
fr; printf '41\tTodo\n' > "$T/board.tsv"; page 1 "$(iss 41 'Spec of a thing' 'spec')"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'frontier-idle' && ko "a spec was named startable: $o" || ok "a spec issue is never named startable (ADR-0142)"
fr; printf '41\tTodo\n' > "$T/board.tsv"; page 1 "$(iss 41 'Has a live run' '')"; liverun run-old 41
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'frontier-idle' && ko "a live run older than 30 min did not suppress frontier-idle: $o" || ok "a live run older than 30 min suppresses frontier-idle"
# #1593: every label the frontier skips is no work at all, so a card that carries one is never reported startable.
# The set is the frontier's own (skiplabels.sh): a card autofrontier.sh will never start must never be named here.
i=0; for l in frozen:owner needs-owner wontfix frozen:dependency needs-info needs-ratification; do
  i=$((i+1)); n=$((500+i))   # one number per label: the board cache keeps a repeated number's card
  fr; printf '%s\tTodo\n' "$n" > "$T/board.tsv"; page 1 "$(iss "$n" "Waits on $l" "$l")"
  o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'frontier-idle' && ko "a $l issue was named startable: $o" || ok "a $l issue is never named startable (#1593)"
done
# A label only the bug lane skips (no-auto, question) never holds a frontier card: the frontier starts it, so a starved one
# is a real frontier-idle and health names it. The two lanes keep their own sets; only the union hid this work (#1593).
i=0; for l in no-auto question; do
  i=$((i+1)); n=$((520+i))   # a number per label
  fr; printf '%s\tTodo\n' "$n" > "$T/board.tsv"; page 1 "$(iss "$n" "Carries $l" "$l")"
  o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -qF "HEALTH frontier-idle: startable $n" && ok "a $l issue is named startable: the frontier starts it (#1593)" || ko "a $l issue was hidden from frontier-idle: $o"
done
fr; printf '41\tCode Complete\n' > "$T/board.tsv"; page 1 "$(iss 41 'Already in code' '')"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'frontier-idle' && ko "a card not in Todo was named startable: $o" || ok "a card not in Todo is not named"
fr; rm -f "$T/board.tsv"; page 1 "$(iss 41 'Board unreadable' '')"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH frontier-idle: startable 41' && ok "an unreadable board keeps today's alert" || ko "unreadable board dropped the alert: $o"
# ---- #1858: a failure that repeats unchanged is a defect in the loop, and two loops are not twice the loop ----
rep() { mkdir -p "$T/co/runs/$1"; printf '{"ts":1,"event": "spawn_accepted", "text": "GitHub issue #%s"}\n{"ts":2,"event": "verdict", "verdict": "failed", "reasons": ["%s", "why %s"]}\n' "${2:-}" "$3" "$3" > "$T/co/runs/$1/record.jsonl"; }
rc() { { echo '#!/bin/bash'; printf 'for a; do case "$a" in body=@*) cp "${a#body=@}" %s/comment.body 2>/dev/null;; esac; done\ncase "$*" in *comments*) echo "$*" >> %s/comments.calls;; */labels*) echo "$*" >> %s/labels.calls;; esac\nexit 0\n' "$T" "$T" "$T"; } > "$T/bin/gh"; chmod +x "$T/bin/gh"; }
rg() { { echo '#!/bin/bash'; printf '#!/bin/bash\nsleep 60\n' > "$T/s/watch.sh"; } > /dev/null; }
reset; : > "$T/st/active-runs.txt"; rm -rf "$T/co/runs" "$T/st/auto/health-runs-repeating-failure"; mkdir -p "$T/co/runs"; rc
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'runs-repeating-failure' && ko "one failed run alerted: $o" || ok "one failed run is not a repetition"
rep r1 10 no_such_issue; rep r2 10 no_such_issue; bash "$T/s/health.sh" >/dev/null
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'runs-repeating-failure' && ko "two failed runs alerted: $o" || ok "two is not yet a repetition"
rep r3 10 no_such_issue; o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF "HEALTH runs-repeating-failure: 3 run(s) ended failed with the same reason 'no_such_issue'" && ok "the third identical failure alerts, naming the reason and the count" || ko "repeat: $o"
/usr/bin/grep -q 'issues/10/comments' "$T/comments.calls" 2>/dev/null && ok "the report is written on the issue the runs were on" || ko "no comment on #10: $(cat "$T/comments.calls" 2>/dev/null)"
/usr/bin/grep -qF 'labels[]=frozen:not-startable' "$T/labels.calls" 2>/dev/null && /usr/bin/grep -q 'issues/10/labels' "$T/labels.calls" && ok "the marker carries the frozen:not-startable label on the same issue" || ko "no frozen:not-startable label on #10: $(cat "$T/labels.calls" 2>/dev/null)"
# (#1297) the marker tells the owner what re-arms it: the comment and the label go together, because the label alone holds an issue
# whose marker names no build, and removing the comment alone leaves the label holding it.
/usr/bin/grep -qF 'remove this comment and the frozen:not-startable label' "$T/comment.body" 2>/dev/null && ok "the marker names the comment and the frozen:not-startable label as the re-arm" || ko "the marker's re-arm line: $(tail -n 2 "$T/comment.body" 2>/dev/null)"
bash "$T/s/health.sh" >/dev/null; n2=$(/usr/bin/grep -c 'issues/10/comments' "$T/comments.calls" 2>/dev/null); [ "${n2:-0}" = 1 ] && ok "the same repetition comments once" || ko "commented $n2 time(s)"
# #1292: the reason is the marker's file name, and a reason with a path separator named a directory that does not exist,
# so the guard never held and every pass posted the marker again
reset; rm -f "$T"/st/auto/notstartable-*; rm -f "$T/st/auto/health-runs-repeating-failure"; : > "$T/comments.calls"
rep s1 12 killed/no-process; rep s2 12 killed/no-process; rep s3 12 killed/no-process
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF "same reason 'killed/no-process'" && ok "#1292 a reason with a slash is reported" || ko "#1292 no report: $o"
[ -e "$T/st/auto/notstartable-12-killed-no-process" ] && ok "#1292 the marker's name is path-safe" || ko "#1292 no marker file: $(ls "$T/st/auto" | tr '\n' ' ')"
n1=$(/usr/bin/grep -c 'issues/12/comments' "$T/comments.calls" 2>/dev/null); bash "$T/s/health.sh" >/dev/null
n2=$(/usr/bin/grep -c 'issues/12/comments' "$T/comments.calls" 2>/dev/null)
[ "${n1:-0}" = 1 ] && [ "${n2:-0}" = 1 ] && ok "#1292 a slash reason comments once and the next pass adds none" || ko "#1292 commented ${n1:-0} then ${n2:-0} time(s)"
# #1292: a marker written before the path-safe rule keeps its name, so it still dedupes
reset; rm -f "$T"/st/auto/notstartable-* "$T/st/auto/health-runs-repeating-failure"; : > "$T/comments.calls"; rc
: > "$T/st/auto/notstartable-13-no_such_issue"
rep l1 13 no_such_issue; rep l2 13 no_such_issue; rep l3 13 no_such_issue
bash "$T/s/health.sh" >/dev/null; nl=$(/usr/bin/grep -c 'issues/13/comments' "$T/comments.calls" 2>/dev/null || true)
[ "${nl:-0}" = 0 ] && ok "#1292 a legacy path-safe marker still dedupes" || ko "#1292 the legacy marker was reposted ${nl:-0} time(s)"
# #1294: every reason group at or over the count reports and marks its own issues; a later group never folds into an earlier
# group's free text. The helper prints one line per group, so the shell must read the whole answer, not its first line.
reset; rm -f "$T"/st/auto/notstartable-* "$T/st/auto/health-runs-repeating-failure"; : > "$T/comments.calls"; rm -rf "$T/bodies"; mkdir -p "$T/bodies"
cat > "$T/bin/gh" <<GH1294
#!/bin/bash
case "\$*" in
  *comments*)
    echo "\$*" >> "$T/comments.calls"
    for a in "\$@"; do case "\$a" in body=@*) cp "\${a#body=@}" "$T/bodies/\$(printf '%s' "\$*" | sed -n 's#.*issues/\([0-9]*\)/comments.*#\1#p')";; esac; done;;
  */labels*) echo "\$*" >> "$T/labels.calls";;
esac
exit 0
GH1294
chmod +x "$T/bin/gh"
rep a1 20 alpha_bad; rep a2 20 alpha_bad; rep a3 20 alpha_bad; rep a4 20 alpha_bad
rep b1 21 beta_bad; rep b2 21 beta_bad; rep b3 21 beta_bad
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF "runs-repeating-failure: 4 run(s) ended failed with the same reason 'alpha_bad'" && ok "#1294 the first group reports its own reason and count" || ko "#1294 alpha: $o"
printf '%s' "$o" | /usr/bin/grep -qF "runs-repeating-failure: 3 run(s) ended failed with the same reason 'beta_bad'" && ok "#1294 the second group reports its own reason and count" || ko "#1294 beta: $o"
[ "$(jq '[.causes[]|select(.=="runs-repeating-failure")]|length' "$T/st/health.json")" = 2 ] && ok "#1294 health.json carries one cause entry per group" || ko "#1294 health.json: $(cat "$T/st/health.json")"
[ -e "$T/st/auto/notstartable-20-alpha_bad" ] && ok "#1294 the first group marks its issue" || ko "#1294 no marker on #20: $(/bin/ls "$T/st/auto" | tr '\n' ' ')"
[ -e "$T/st/auto/notstartable-21-beta_bad" ] && ok "#1294 the second group marks its own issue" || ko "#1294 no marker on #21: $(/bin/ls "$T/st/auto" | tr '\n' ' ')"
/usr/bin/grep -q 'issues/20/comments' "$T/comments.calls" && /usr/bin/grep -q 'issues/21/comments' "$T/comments.calls" && ok "#1294 each group comments on its own issue" || ko "#1294 comments: $(cat "$T/comments.calls" 2>/dev/null)"
/usr/bin/grep -q 'alpha_bad' "$T/bodies/20" 2>/dev/null && /usr/bin/grep -q 'beta_bad' "$T/bodies/21" 2>/dev/null && ok "#1294 each marker names its own reason" || ko "#1294 marker bodies: $(cat "$T/bodies/20" "$T/bodies/21" 2>/dev/null)"
/usr/bin/grep -q 'beta_bad' "$T/bodies/20" 2>/dev/null && ko "#1294 the second group's table leaked into the first group's marker" || ok "#1294 no other group's table in the first marker"
/usr/bin/grep -q 'alpha_bad' "$T/bodies/21" 2>/dev/null && ko "#1294 the first group's table leaked into the second group's marker" || ok "#1294 no other group's table in the second marker"
# #1294 review: the alert is keyed per group (cause plus reason), so a second group that reaches the count on a LATER pass,
# while the first still persists, alerts, writes its alerts.log line and notifies; a persisting group does not alert again
reset; rm -f "$T"/st/auto/notstartable-* "$T"/st/auto/health-runs-repeating-failure*; : > "$T/comments.calls"; rc; : > "$T/st/alerts.log"
rep p1 40 alpha_late; rep p2 40 alpha_late; rep p3 40 alpha_late
bash "$T/s/health.sh" >/dev/null
nb=$(notif)
rep q1 41 beta_late; rep q2 41 beta_late; rep q3 41 beta_late
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF "HEALTH runs-repeating-failure: 3 run(s) ended failed with the same reason 'beta_late'" && ok "a second group reaching the count on a later pass is reported" || ko "later group silent: $o"
[ "$(/usr/bin/grep -c 'beta_late' "$T/st/alerts.log" 2>/dev/null)" = 1 ] && ok "the later group writes its alerts.log line" || ko "later group alerts.log: $(cat "$T/st/alerts.log" 2>/dev/null)"
[ "$(notif)" -gt "$nb" ] && ok "the later group notifies" || ko "no notification for the later group"
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'runs-repeating-failure' && ko "persisting groups alerted again: $o" || ok "persisting groups do not alert again"
# #1294 review: a machine reason with a tab or a newline is flattened in the group key, so the TSV keeps its four fields
reset; rm -f "$T"/st/auto/notstartable-* "$T"/st/auto/health-runs-repeating-failure*; : > "$T/comments.calls"; rc
rep f1 50 'x\ty\nz'; rep f2 50 'x\ty\nz'; rep f3 50 'x\ty\nz'
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF "HEALTH runs-repeating-failure: 3 run(s) ended failed with the same reason 'x y z'" && ok "a reason with a tab and a newline is one flattened group" || ko "flattening: $o"
[ -e "$T/st/auto/notstartable-50-x-y-z" ] && ok "the flattened reason marks its issue" || ko "no marker for the flattened reason: $(/bin/ls "$T/st/auto" | tr '\n' ' ')"
/usr/bin/grep -q 'issues/50/comments' "$T/comments.calls" 2>/dev/null && ok "the flattened group comments on its issue" || ko "no comment on #50: $(cat "$T/comments.calls" 2>/dev/null)"
# #1294 review: an empty machine reason keys as "?", so the TSV does not collapse its empty field
reset; rm -f "$T"/st/auto/notstartable-* "$T"/st/auto/health-runs-repeating-failure*; : > "$T/comments.calls"; rc
rep e1 51 ''; rep e2 51 ''; rep e3 51 ''
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF "HEALTH runs-repeating-failure: 3 run(s) ended failed with the same reason '?'" && ok "an empty machine reason is keyed as ?" || ko "empty reason: $o"
[ -e "$T/st/auto/notstartable-51-" ] && ko "an empty reason made an empty marker key" || ok "an empty reason makes no empty marker key"
# #1294 review: a group whose runs named no issue has no issue to comment on or mark: no "-" issue, no "notstartable---" marker
reset; rm -f "$T"/st/auto/notstartable-* "$T"/st/auto/health-runs-repeating-failure*; : > "$T/comments.calls"; rc
rep n1 "" no_issue_reason; rep n2 "" no_issue_reason; rep n3 "" no_issue_reason
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF "same reason 'no_issue_reason'" && ok "a group with no issue still reports" || ko "no-issue group not reported: $o"
/usr/bin/grep -q 'issues/-/' "$T/comments.calls" 2>/dev/null && ko "a comment went to issues/-" || ok "a group with no issue posts no comment"
[ -n "$(/usr/bin/find "$T/st/auto" -name 'notstartable---*' 2>/dev/null)" ] && ko "a marker was claimed for -" || ok "a group with no issue claims no marker"
# #1294 review: health.json lists one causes entry per group (its count of groups), and briefs stay keyed by cause name, so a
# repeating failure never carries a brief of its own
reset; rm -f "$T"/st/auto/notstartable-* "$T"/st/auto/health-runs-repeating-failure*; : > "$T/comments.calls"; rc
rep h1 60 shape_a; rep h2 60 shape_a; rep h3 60 shape_a; rep h4 61 shape_b; rep h5 61 shape_b; rep h6 61 shape_b
bash "$T/s/health.sh" >/dev/null
[ "$(jq '[.causes[]|select(.=="runs-repeating-failure")]|length' "$T/st/health.json")" = 2 ] && ok "health.json has one causes entry per group" || ko "health.json groups: $(cat "$T/st/health.json")"
jq -e '.briefs | has("runs-repeating-failure") | not' "$T/st/health.json" >/dev/null && ok "health.json carries no brief for a repeating failure" || ko "health.json brief: $(cat "$T/st/health.json")"
# #1292: a post GitHub refuses drops the claim, so the next pass tries again
reset; rm -f "$T"/st/auto/notstartable-* "$T/st/auto/health-runs-repeating-failure" "$T/post.ok"; : > "$T/comments.calls"
{ echo '#!/bin/bash'; printf 'case "$*" in *comments*) [ -e %s/post.ok ] || exit 1; echo "$*" >> %s/comments.calls;; */labels*) echo "$*" >> %s/labels.calls;; esac\nexit 0\n' "$T" "$T" "$T"; } > "$T/bin/gh"; chmod +x "$T/bin/gh"
rep t1 13 a_real_reason; rep t2 13 a_real_reason; rep t3 13 a_real_reason
bash "$T/s/health.sh" >/dev/null
[ -e "$T/st/auto/notstartable-13-a_real_reason" ] && ko "#1292 a refused post kept the claim" || ok "#1292 a refused post drops the claim, so the next pass retries"
: > "$T/post.ok"; bash "$T/s/health.sh" >/dev/null
[ -e "$T/st/auto/notstartable-13-a_real_reason" ] && ok "#1292 the retried post claims its marker once" || ko "#1292 the retry did not claim the marker"
rc
rep r4 10 a_different_reason; o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'runs-repeating-failure cleared' && ok "a different reason breaks the streak -> cleared" || ok "the streak re-arms only when the group drops below the count"
# #585: a run keeps its folder in its own worktree (#580) or in a fork route's child tree; the repetition reads those runs too
reset; rm -rf "$T/st/auto/health-runs-repeating-failure"; : > "$T/comments.calls"
repw() { local d="$T/co/worktrees/$1/runs/$2"; mkdir -p "$d"; printf '{"ts":1,"event": "spawn_accepted", "text": "GitHub issue #11"}\n{"ts":2,"event": "verdict", "verdict": "failed", "reasons": ["wt_reason", "why"]}\n' > "$d/record.jsonl"; }
repw run/k1 w1; repw run/k2 w2; repw child/gui-11 w3; o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF "HEALTH runs-repeating-failure: 3 run(s) ended failed with the same reason 'wt_reason'" && ok "#585: failed runs in run and child worktrees are a repetition" || ko "#585 worktree repeat: $o"
rm -rf "$T/co/worktrees"
reset; : > "$T/st/active-runs.txt"; rm -rf "$T/co/runs" "$T/st/auto/health-loops-duplicated"; mkdir -p "$T/co/runs"
rm -f "$T/st/locks/watch/pid" "$T/st/watchd.pid"   # every loop case starts from a clean locks/watch directory
# #1427: a loop names itself in its own state dir, through the lock pidfile it holds. The stub holds one only when STUB_LOCK=1,
# so a case names exactly the process it means; a case that leaves it unset starts a loop no state dir names.
printf '#!/bin/bash\n[ -n "${STUB_LOCK:-}" ] && { mkdir -p "$SG_STATE/locks/watch"; echo $$ > "$SG_STATE/locks/watch/pid"; }\nsleep 60\n' > "$T/s/watch.sh"
SG_STATE="$T/st" STUB_LOCK=1 bash "$T/s/watch.sh" & w1=$!; sleep 1
o=$(SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'loops-duplicated' && ko "one loop alerted: $o" || ok "one watch.sh is not a duplication"
# #1427: a second profile runs the same watch.sh under its own state dir, so this profile's pass never names it
mkdir -p "$T/st2/locks/watch" "$T/st3/locks/watch"
SG_STATE="$T/st2" STUB_LOCK=1 bash "$T/s/watch.sh" & p2=$!
SG_STATE="$T/st3" STUB_LOCK=1 bash "$T/s/watch.sh" & p3=$!; sleep 1
o=$(SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -q 'loops-duplicated' && ko "#1427 another profile's loop alerted as a duplicate ($p2, $p3): $o" || ok "#1427 another profile's watch.sh is not this profile's duplicate"
kill $p2 $p3 2>/dev/null; wait $p2 $p3 2>/dev/null
# two loops of this profile: one holds its lock pidfile, the other is the watch.sh lane its supervisor holds in watchd.pid
rm -f "$T/st/auto/health-loops-duplicated"
mkdir -p "$T/sup"; printf '#!/bin/bash\nbash "'"$T"'/s/watch.sh" &\necho $! > "'"$T"'/lane.pid"\nwait\n' > "$T/sup/lane.sh"
SG_STATE="$T/st" bash "$T/sup/lane.sh" & qd=$!; sleep 1; q2=$(cat "$T/lane.pid"); echo "$qd" > "$T/st/watchd.pid"
o=$(SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh")
# #17: the cause names the pids it counted, so an operator can compare them with the loop watchd started
qps=$(printf '%s\n' $w1 $q2 | sort -n | tr '\n' ' '); qps=${qps% }
printf '%s' "$o" | /usr/bin/grep -qF "HEALTH loops-duplicated: 2 watch.sh process(es) alive (pids $qps;" && ok "#1427 two loops under one profile alert, naming both pids" || ko "#1427 two loops under this profile ($w1, $q2): $o"
kill $qd $q2 $w1 2>/dev/null; wait $qd $q2 $w1 2>/dev/null; rm -f "$T/st/watchd.pid" "$T/st/locks/watch/pid"
# #1427: the count is fail-open. Two loops of this profile that no pidfile names -- a hand-started stray where the OS hides
# another process's environment -- still alert; only another state dir's pidfile takes a process out of the count.
rm -f "$T/st/auto/health-loops-duplicated" "$T/st/watchd.pid" "$T/st/locks/watch/pid"
SG_STATE="$T/st" bash "$T/s/watch.sh" & s1=$!
SG_STATE="$T/st" bash "$T/s/watch.sh" & s2=$!; sleep 1
o=$(SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh")
sps=$(printf '%s\n' $s1 $s2 | sort -n | tr '\n' ' '); sps=${sps% }
printf '%s' "$o" | /usr/bin/grep -qF "HEALTH loops-duplicated: 2 watch.sh process(es) alive (pids $sps;" && ok "#1427 two loops of this profile that no pidfile names still alert" || ko "#1427 unattributed loops of this profile were dropped ($s1, $s2): $o"
kill $s1 $s2 2>/dev/null; wait $s1 $s2 2>/dev/null
# #17: the cause also names the pid the supervisor holds for its watch.sh, so the stray one is the pid that is not named there
rm -f "$T/st/auto/health-loops-duplicated"
mkdir -p "$T/sup"; printf '#!/bin/bash\nbash "'"$T"'/s/watch.sh" &\necho $! > "'"$T"'/held.pid"\nwait\n' > "$T/sup/watchd.sh"
SG_STATE="$T/st" bash "$T/sup/watchd.sh" run & sp=$!
SG_STATE="$T/st" STUB_LOCK=1 bash "$T/s/watch.sh" & w3=$!; sleep 1; hp=$(cat "$T/held.pid"); echo "$sp" > "$T/st/watchd.pid"
o=$(SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF "watchd pid $sp holds $hp" && ok "the duplicate names the pid the supervisor holds" || ko "supervisor pid not named (watchd $sp holds $hp, stray $w3): $o"
kill $sp $hp $w3 2>/dev/null; wait $sp $hp $w3 2>/dev/null; rm -f "$T/st/watchd.pid" "$T/st/locks/watch/pid" "$T/held.pid" "$T/lane.pid"
# #17: pgrep -f matches the command line, and a forked subshell (pipeline, $(...), a background group) inherits its parent's argv.
# One healthy loop that forks must not read as two; the fake loop keeps a pipeline and a nested $(...) alive for the whole check.
rm -f "$T/st/auto/health-loops-duplicated"
# a detached step, `( cmd & )`, is a fork whose parent exited: parent pid 1, the loop's argv, the loop's process group (watch.sh runs forkclose so)
printf '#!/bin/bash\nmkdir -p "$SG_STATE/locks/watch"; echo $$ > "$SG_STATE/locks/watch/pid"\n{ x=$(sleep 20; true); } &\n( { sleep 20; true; } & )\ntrue | { sleep 20; true; } | cat > /dev/null\nwait\n' > "$T/s/watch.sh"; SG_STATE="$T/st" bash "$T/s/watch.sh" & wf=$!; sleep 1
$SG_PS -axwwo ppid=,command= | /usr/bin/grep -v grep | /usr/bin/grep -q "^ *1 bash $T/s/watch\.sh\$" && ok "the fake loop leaves a detached fork (parent pid 1)" || ko "no orphaned fork in the fake loop"
nf=$(/usr/bin/pgrep -f "$T/s/watch\.sh$" | wc -l | tr -d ' ')
[ "$nf" -gt 1 ] && ok "the fake loop is forking ($nf pgrep matches for one loop)" || ko "the fake loop does not fork: $nf match(es), the next check proves nothing"
o=$(SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'loops-duplicated' && ko "one forking loop alerted: $o" || ok "one watch.sh that forks is not a duplication"
kill $wf 2>/dev/null; /usr/bin/pkill -P $wf 2>/dev/null; wait $wf 2>/dev/null; rm -f "$T/st/locks/watch/pid" "$T/st/watchd.pid"
# a fork base the fork repository does not hold makes `rev-list --count` print nothing: the parked count reads zero,
# and the shell never reports an unreadable number
$G -C "$P" rev-parse -q --verify refs/heads/gui/7 >/dev/null && ok "the unreadable-base case holds its parked branch" || ko "no gui/7 branch, so the unreadable-base case proves nothing"
o=$(SG_FORK_DIR="$P" SG_FORK_BASE=no-such-base SG_FORK_BRANCH='gui/%n' SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh" 2>&1)
printf '%s' "$o" | /usr/bin/grep -q 'integer expression expected' && ko "an unreadable fork base leaks a shell error: $o" || ok "an unreadable fork base counts as zero parked"
# ---- #1226: the loop is stopped, and this pass frees what a dead run holds (squad/reap.py) ----
kill $wp $fp 2>/dev/null; wait $wp $fp 2>/dev/null; rm -f "$T/st/watchd.pid" "$T/st/fastloop.pid"   # the loop is stopped
reset; rm -rf "$T/caps"; bash -c 'exit 0' & dp=$!; wait $dp   # a pid whose process is gone
sleep 300 & lp=$!; sleep 300 & up=$!   # lp: a live run's engine; up: an unrelated process that reuses a dead run's pid
mkdir -p "$T/caps/gates"; printf '{"holder": "child-dead", "pid": %s, "since": 1}\n' "$dp" > "$T/caps/gates/child-dead.slot0.json"
printf '{"holder": "child-live", "pid": %s, "since": %s}\n' "$lp" "$(date +%s)" > "$T/caps/gates/child-live.json"
# rrun <name> <event>...: a listed run whose record holds the events, each `start`, `resume`, `verdict` or `call:<pid>:<started_at>`
rrun() { local n=$1 d="$T/co/runs/$1"; shift; mkdir -p "$d"; : > "$d/record.jsonl"; local p=0 e
  for e in "$@"; do p=$((p+1)); case "$e" in
    start) printf '{"event": "run_start", "ts": 1, "pos": %s, "run_id": "%s", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": ["main"]}\n' $p "$n";;
    resume) printf '{"event": "resume", "ts": 1, "pos": %s, "run_id": "%s", "graph_hash": "h", "frontier": [], "invoker": "cli", "engine_options": {}, "preflight": {}}\n' $p "$n";;
    verdict) printf '{"event": "verdict", "ts": 1, "pos": %s, "verdict": "pass", "exit_code": 0, "reasons": [], "remains": [], "folded": []}\n' $p;;
    call:*) e=${e#call:}; printf '{"event": "call_start", "ts": 1, "pos": %s, "node": "main", "entry": 1, "session": "s", "pid": %s, "limit": null, "started_at": %s}\n' $p "${e%%:*}" "${e#*:}";;
  esac; done >> "$d/record.jsonl"
  echo x > "$d/request.txt"; touch -t 202001010000 "$d/request.txt" "$d/record.jsonl"; echo "$n" >> "$T/st/active-runs.txt"; }
lastev() { tail -1 "$T/co/runs/$1/record.jsonl"; }
now0=$(date +%s)
rrun run-dead start "call:$dp:$((now0-7200))"
rrun run-resumed start "call:$dp:$((now0-7200))" verdict resume "call:$lp:$now0"
rrun run-live start "call:$lp:$now0"
rrun run-reused start "call:$up:1000000"
cp "$T/st/active-runs.txt" "$T/act.bak"
o=$(bash "$T/s/health.sh")
[ -e "$T/caps/gates/child-dead.slot0.json" ] && ko "loop stopped: a dead gate lease survived the pass" || ok "loop stopped: the pass frees a dead gate lease"
[ -e "$T/caps/gates/child-live.json" ] && ok "a live gate lease stays" || ko "a live gate lease was freed"
lastev run-dead | /usr/bin/grep -q '"killed/no-process"' && ok "a dead unfinished run gets the killed verdict" || ko "dead run not marked: $(lastev run-dead) / $o"
printf '%s' "$o" | /usr/bin/grep -q 'REAPED run-dead' && ok "the pass names the run it marked" || ko "no REAPED line: $o"
lastev run-resumed | /usr/bin/grep -q '"call_start"' && ok "a resumed run whose new process lives is not killed" || ko "resumed run marked: $(lastev run-resumed)"
lastev run-live | /usr/bin/grep -q '"call_start"' && ok "a live run is untouched" || ko "live run marked: $(lastev run-live)"
lastev run-reused | /usr/bin/grep -q '"killed/no-process"' && ok "a process reusing the pid is not taken as the run" || ko "reused pid read as the run: $(lastev run-reused)"
kill -0 $up 2>/dev/null && ok "the unrelated process keeps running (the pass kills nothing)" || ko "the unrelated process was killed"
cmp -s "$T/st/active-runs.txt" "$T/act.bak" && ok "the active-runs lines are kept" || ko "active-runs changed: $(cat "$T/st/active-runs.txt")"
n2=$(bash "$T/s/health.sh" | /usr/bin/grep -c REAPED); [ "$n2" = 0 ] && ok "a run with its final verdict is not marked twice" || ko "marked again: $n2"
kill $lp $up 2>/dev/null; wait $lp $up 2>/dev/null
# ---- #1270: a pass that reads no free run slot names the holder that has had no live lease and no live process for five minutes ----
# The ceiling is one run (the project file lowers it) and it is taken, so the pass reads no free slot and must say which run holds
# it. Its engine is gone and its request is fresh: the reap leaves a run in its launch window alone, so only the report names it.
reset; : > "$T/st/active-runs.txt"; rm -rf "$T/co/runs" "$T/st/auto/health-no-free-slot"; mkdir -p "$T/co/runs" "$T/co/docs/squad"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
sleep 0 & dp=$!; wait $dp; rrun run-noshow start "call:$dp:$(( $(date +%s) ))"; touch "$T/co/runs/run-noshow/request.txt"
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH no-free-slot: NO-SLOT run-noshow: caps.runs has no free slot and this run has had no live lease and no live process for' && ok "a full ceiling names the run that has had no lease and no process for five minutes" || ko "the stale holder was not named: $o"
[ -e "$T/st/auto/health-no-free-slot" ] && ok "the named holder alerts once" || ko "no alert marker for the named holder"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'HEALTH no-free-slot' && ko "the same holder alerted twice: $o" || ok "the same holder does not alert twice"
# a named engine that lives is never named, whatever its record says (#1270): the report and the reap read the same pid
reset; rm -rf "$T/co/runs" "$T/st/auto/health-no-free-slot"; mkdir -p "$T/co/runs"
sleep 300 & sp=$!; rrun run-live start "call:$sp:$(( $(date +%s) ))"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'NO-SLOT run-live' && ko "a run whose named engine lives was named: $o" || ok "a run whose named engine lives is never named"
kill $sp 2>/dev/null; wait $sp 2>/dev/null; rm -f "$T/co/docs/squad/defaults.yaml"
# a free slot names nobody
printf 'caps:\n  runs: 2\n' > "$T/co/docs/squad/defaults.yaml"; rm -f "$T/st/auto/health-no-free-slot"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'no-free-slot' && ko "a free ceiling named a holder: $o" || ok "a free ceiling names no holder"
rm -f "$T/co/docs/squad/defaults.yaml"
# ---- #1290: a landing held past its own gate wait plus the gate bound is a named failure; a wait inside them stays quiet ----
mkdir -p "$T/st/locks/single-wland"; /bin/sleep 60 & lh=$!; echo "$lh" > "$T/st/locks/single-wland/pid"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'landing-held' && ko "a fresh landing wait alerted: $o" || ok "a landing wait inside its bounds stays quiet"
touch -t 202001010000 "$T/st/locks/single-wland/pid"
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF "HEALTH landing-held: the landing lock has been held by pid $lh since 2020-01-01 00:00:00" \
  && ok "a holder past the gate wait plus the gate bound alerts, naming the pid and the instant" || ko "no landing-held cause: $o"
/usr/bin/grep -q 'HEALTH landing-held:' "$T/st/alerts.log" && ok "alerts.log carries it" || ko "alerts.log: $(cat "$T/st/alerts.log" 2>/dev/null)"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'HEALTH landing-held:' && ko "the same held landing alerted twice" || ok "the same held landing alerts once"
kill "$lh" 2>/dev/null; wait "$lh" 2>/dev/null; rm -rf "$T/st/locks/single-wland"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'HEALTH landing-held cleared' && ok "a released landing clears the cause" || ko "not cleared: $o"
# ---- #1290: a landing that runs more than one gate round (a red tip, a child that leaves, the tip again) is progressing: its last
# round's start is the clock, not the landing's start. The held instant still names when the lock was taken.
mkdir -p "$T/st/locks/single-wland"; /bin/sleep 60 & lh=$!; echo "$lh" > "$T/st/locks/single-wland/pid"; touch -t 202001010000 "$T/st/locks/single-wland/pid"
touch "$T/st/locks/single-wland/round"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'landing-held' && ko "a landing in a fresh gate round alerted: $o" || ok "a landing past its bounds whose gate round just started stays quiet"
touch -t 202001010000 "$T/st/locks/single-wland/round"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -qF "HEALTH landing-held: the landing lock has been held by pid $lh since 2020-01-01 00:00:00" \
  && ok "a landing whose last gate round is past the bounds alerts, naming the held instant" || ko "a stale gate round did not alert: $o"
kill "$lh" 2>/dev/null; wait "$lh" 2>/dev/null; rm -rf "$T/st/locks/single-wland"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'HEALTH landing-held cleared' && ok "the round-progress landing clears its cause" || ko "round-progress cause not cleared: $o"
# ---- #1775: a janitor pass that holds its single-janitor lock past its own bound is stuck, not slow. The pass deadline
# lives inside janitor.py (#1774) and fires only when the next call would start, so a hang in the shell wrapper, or in
# Python code between calls, held the lock in silence. The bound is the pass deadline plus one call deadline; a live
# holder past it alerts once, naming its pid and the age, and a lock whose holder is dead raises no alert of this kind.
aget() { local e=$(( $(date +%s) - $2 )); touch -t "$(date -r "$e" '+%Y%m%d%H%M.%S' 2>/dev/null || date -d "@$e" '+%Y%m%d%H%M.%S')" "$1"; }   # mtime <n> seconds ago, whichever date this machine has
jhld="$T/st/locks/single-janitor"; rm -rf "$jhld" "$T/st/auto/health-janitor-held"
mkdir -p "$jhld"; /bin/sleep 60 & jh=$!; echo "$jh" > "$jhld/pid"
o=$(SG_JANITOR_DEADLINE=20 JANITOR_CALL_TIMEOUT=10 bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'janitor-held' && ko "a fresh janitor pass alerted: $o" || ok "a janitor pass inside its deadline stays quiet"
aget "$jhld/pid" 20
o=$(SG_JANITOR_DEADLINE=20 JANITOR_CALL_TIMEOUT=10 bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'janitor-held' && ko "a lock inside the pass deadline plus one call deadline alerted: $o" || ok "a lock inside the pass deadline plus one call deadline stays quiet"
aget "$jhld/pid" 40
o=$(SG_JANITOR_DEADLINE=20 JANITOR_CALL_TIMEOUT=10 bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qE "HEALTH janitor-held: the janitor lock has been held by pid $jh for 4[0-9]s, past the 20s pass deadline plus the 10s call deadline" \
  && ok "a live holder past the pass deadline plus one call deadline alerts, naming the pid and the age" || ko "no janitor-held cause: $o"
/usr/bin/grep -q 'HEALTH janitor-held:' "$T/st/alerts.log" && ok "alerts.log carries the held janitor lock" || ko "alerts.log: $(cat "$T/st/alerts.log" 2>/dev/null)"
o=$(SG_JANITOR_DEADLINE=20 JANITOR_CALL_TIMEOUT=10 bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'HEALTH janitor-held:' && ko "the same held janitor lock alerted twice" || ok "the same held janitor lock alerts once"
kill "$jh" 2>/dev/null; wait "$jh" 2>/dev/null
o=$(SG_JANITOR_DEADLINE=20 JANITOR_CALL_TIMEOUT=10 bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'HEALTH janitor-held cleared' && ok "a freed janitor lock clears the cause" || ko "not cleared: $o"
rm -f "$T/st/auto/health-janitor-held"; : > "$T/st/alerts.log"   # re-arm: the dead-holder case must raise no alert of its own
mkdir -p "$jhld"; echo "$jh" > "$jhld/pid"; aget "$jhld/pid" 40   # the pid of the process just waited for: dead, the stale lock single.sh reaps
o=$(SG_JANITOR_DEADLINE=20 JANITOR_CALL_TIMEOUT=10 bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -q 'HEALTH janitor-held' && ko "a lock whose holder is dead alerted: $o" || ok "a lock whose holder is dead raises no janitor-held alert"
[ -s "$T/st/alerts.log" ] && ko "a dead janitor holder wrote an alert line" || ok "a dead janitor holder writes no alert line"
rm -rf "$jhld"
# ---- #1467: a pass that finds the supervisor dead relights it, through the same stops a start obeys. The stub start is a real
# `watchd.sh run` child, so the process check that reads the slot finds it, exactly as the live script's own start leaves it.
rm -f "$T/st/watchd.pid" "$T/st/relights" "$T/st/start-mode" "$T/st/auto/off" "$T/st/auto/drain-waiter" "$T/st/auto/health-watchd-down"; : > "$T/bin/started"   # each cause alerts once: re-arm watchd-down
cat > "$T/s/watchd.sh" <<'WD'
#!/bin/bash
d=$(dirname "$0")
case "${1:-status}" in
  run) /bin/sleep 60;;
  start) echo start >> "$d/../bin/started"
         case "$(cat "$d/../st/start-mode" 2>/dev/null)" in
           refuse) echo "watchd start refused: #1467 is a loop defect; the fix lands outside the loop" >&2; exit 1;;
           held)   echo "watchd already running pid=4242"; exit 0;;
           *)      bash "$0" run </dev/null >/dev/null 2>&1 & echo $! > "$d/../st/watchd.pid"; echo "watchd started pid=$!"; exit 0;;   # detached as the live start's Popen is: a held stdout would hang the pass's $(...)
         esac;;
esac
WD
chmod +x "$T/s/watchd.sh"
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -q 'HEALTH watchd-down: relit: watchd started pid=' && ok "a dead supervisor is relit and the relight is reported" || ko "no relight: $o"
[ "$(($(wc -l < "$T/st/relights" 2>/dev/null)))" = 1 ] && ok "the relight is recorded" || ko "relights file: $(cat "$T/st/relights" 2>/dev/null)"
o=$(bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -q 'HEALTH watchd-down:' && ko "a relit supervisor still alerts down: $o" || ok "the pass after a relight is quiet"
/usr/bin/grep -q '"relights":1' "$T/st/health.json" && ok "health.json carries the relight count" || ko "relights not in health.json: $(cat "$T/st/health.json")"
# a pid file that is stale or absent while the supervisor runs: start answers "already running", nothing is restarted or recorded
rm -f "$T/st/watchd.pid"; echo held > "$T/st/start-mode"; rm -f "$T/st/relights"; sz=$(wc -l < "$T/bin/started")
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -q 'HEALTH watchd-down:' && ko "already running still alerted down: $o" || ok "a supervisor that is already up is not reported down"
[ "$(wc -l < "$T/bin/started")" -gt "$sz" ] && ok "the relight went through the start path" || ko "start was never called"
[ -s "$T/st/relights" ] && ko "an already-running supervisor was recorded as a relight" || ok "no relight is recorded for a supervisor that never died"
rm -f "$T/st/start-mode"
# the hub's off switch and a drain are stops the start path would override: the pass reads them first and calls nothing
: > "$T/bin/started"; touch "$T/st/auto/off"; rm -f "$T/st/watchd.pid"
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -q 'the loop switch is off' && ok "the off switch refuses the relight and names it" || ko "off switch not named: $o"
[ -s "$T/bin/started" ] && ko "the off switch still called start" || ok "with the loop switch off, start is never called"
rm -f "$T/st/auto/off" "$T/st/auto/health-watchd-down"; /bin/sleep 60 & dw=$!; echo "$dw" > "$T/st/auto/drain-waiter"
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -q 'a drain is in progress' && ok "a drain refuses the relight and names it" || ko "drain not named: $o"
[ -s "$T/bin/started" ] && ko "a drain still called start" || ok "during a drain, start is never called"
kill "$dw" 2>/dev/null; rm -f "$T/st/auto/drain-waiter"
# the refusals the start path itself carries (a loop-bug open) reach the alert unchanged, and nothing is recorded as a relight
echo refuse > "$T/st/start-mode"; rm -f "$T/st/relights" "$T/st/auto/health-watchd-down"
o=$(bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -q 'watchd start refused: #1467 is a loop defect' && ok "a start refusal reaches the alert" || ko "start refusal missing: $o"
[ -s "$T/st/relights" ] && ko "a refused start was recorded as a relight" || ok "a refused start is not a relight"
rm -f "$T/st/start-mode"
# a supervisor that keeps dying is named: three relights in the hour stay quiet, a fourth raises loop-flapping (#1467 debt)
rm -f "$T/st/relights" "$T/st/watchd.pid"; now=$(date +%s)
printf '%s\n%s\n' "$((now - 60))" "$((now - 30))" > "$T/st/relights"
bash "$T/s/health.sh" >/dev/null
/usr/bin/grep -q 'loop-flapping' "$T/st/health.json" && ko "three relights in the hour raised loop-flapping" || ok "three relights in the hour stay quiet"
rm -f "$T/st/watchd.pid"; printf '%s\n%s\n%s\n' "$((now - 90))" "$((now - 60))" "$((now - 30))" > "$T/st/relights"
bash "$T/s/health.sh" >/dev/null
/usr/bin/grep -q 'loop-flapping' "$T/st/health.json" && ok "a fourth relight in the hour raises loop-flapping" || ko "no loop-flapping at four: $(cat "$T/st/health.json")"
/usr/bin/grep -q '"relights":4' "$T/st/health.json" && ok "health.json counts the relights in the window" || ko "count: $(cat "$T/st/health.json")"
# The readiness walk reads its facts in one numbered list, never one per card (#1648): this pass runs every two minutes,
# and a per-card read here spent more than a thousand GraphQL requests an hour on its own.
rm -f "$T/st/relights" "$T/st/watchd.pid" "$T/numreads"
page 1 "$(iss 427 'Add a thing' 'enhancement')" "$(iss 428 'Another thing' 'enhancement')"
bd
GH_READY="427 428" bash "$T/s/health.sh" >/dev/null
[ -s "$T/numreads" ] && ok "the readiness walk asks the numbered list once" || ko "the readiness walk asked no numbered list"
[ "$(/usr/bin/grep -c 'numbers=' "$T/numreads")" = 1 ] && ok "one numbered list covers the startable cards" || ko "the numbered list ran $(/usr/bin/grep -c 'numbers=' "$T/numreads") times: $(cat "$T/numreads")"
# ---- #1708, ADR-0146: the watcher, end to end over the real watchd.sh and stub lanes. A loop that drained itself for a
# loop-bug starts again on the first pass after no loop-bug is open, and the audit names the issue. A loop the owner
# stopped stays off, whatever closes.
pkill -f "$T/s/watchd.sh run" 2>/dev/null; kill $wp $fp 2>/dev/null; rm -f "$T/st/watchd.pid" "$T/st/fastloop.pid" "$T/st/relights" "$T/st/auto"/* "$T/st/loop-audit.jsonl" "$T/st/loop-bug-watch"
# The copy's caps.sh is the sandbox's own beside health.sh: a path taken from the caller's spelling of `src` is relative
# whenever the suite is run with a relative path, and the supervisor starts from another directory, so it died at once.
python3 - "$(dirname "$src")/watchd.sh" "$T/s/watchd.sh" "$T/s" <<"PY"
import pathlib, sys
t = pathlib.Path(sys.argv[1]).read_text()
a = "\"$(dirname \"$0\")/caps.sh\""; assert a in t; t = t.replace(a, "\"%s/caps.sh\"" % sys.argv[3])
# the open-issue list is a file, as the other watchd suites stub the one seam: an absent file is a failed read
a = ". \"$(dirname \"$0\")/ghcall.sh\"   # the one seam the start\x27s loop-bug read goes through (ADR-0146)"; assert a in t
t = t.replace(a, "gh_aggregate() { [ -s \"$SG_STATE/issues.json\" ] || return 1; cat \"$SG_STATE/issues.json\"; }")
pathlib.Path(sys.argv[2]).write_text(t)
PY
li() { printf "%s\n" "$1" > "$T/st/issues.json"; }
for l in watch.sh fastloop.sh cqflush.sh; do printf "#!/bin/bash\nsleep 300\n" > "$T/s/$l"; done; chmod +x "$T/s"/*.sh
export SG_LOOPBUG_WATCH_S=0 SG_DRAIN_POLL=1
hsup() { p=$(cat "$T/st/watchd.lock/pid" 2>/dev/null); [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
hdown() { for _ in $(seq 1 20); do hsup || return 0; /bin/sleep 1; done; return 1; }
li "[{\"number\":427,\"labels\":[]}]"
(cd "$T/co" && bash "$T/s/watchd.sh" start >/dev/null 2>&1); hsup && ok "1708 the real watchd is up for the watcher cases" || ko "1708 the real watchd did not start: $(cd "$T/co" && bash "$T/s/watchd.sh" start 2>&1 | tail -2)"
li "[{\"number\":427,\"labels\":[]},{\"number\":1708,\"labels\":[{\"name\":\"bug\"},{\"name\":\"loop-bug\"}]}]"
SG_GH_PASS=autobug bash "$T/s/watchd.sh" bugdrain 1708 >/dev/null 2>&1; hdown
[ "$(cat "$T/st/auto/loop-bug-drain" 2>/dev/null)" = 1708 ] && [ -e "$T/st/auto/off" ] && ! hsup && ok "1708 the loop drained itself for the loop-bug and is off" || ko "1708 the drain left: $(ls "$T/st/auto")"
bash "$T/s/health.sh" >/dev/null 2>&1
! hsup && [ -e "$T/st/auto/loop-bug-drain" ] && ok "1708 the watcher holds while the loop-bug is open" || ko "1708 the watcher started the loop under an open loop-bug"
li "{}"; bash "$T/s/health.sh" >/dev/null 2>&1
! hsup && [ -e "$T/st/auto/loop-bug-drain" ] && ok "1708 the watcher holds on an answer that is no issue list" || ko "1708 the watcher started the loop on a body that is no list"
li "[{\"number\":427,\"labels\":[]}]"
SG_LOOPBUG_WATCH_S=300 bash "$T/s/health.sh" >/dev/null 2>&1
! hsup && ok "1708 inside the cadence the watcher reads nothing and starts nothing" || ko "1708 the watcher started inside its cadence"
bash "$T/s/health.sh" >/dev/null 2>&1
hsup && [ ! -e "$T/st/auto/off" ] && [ ! -e "$T/st/auto/loop-bug-drain" ] && ok "1708 with no loop-bug open the watcher starts the loop, and the pause and the mark go" || ko "1708 the watcher did not start the loop: $(ls "$T/st/auto"; tail -2 "$T/st/watch.log")"
[ "$(/usr/bin/grep -c "\"action\": \"start\"" "$T/st/loop-audit.jsonl" 2>/dev/null)" = 1 ] && /usr/bin/grep "\"action\": \"start\"" "$T/st/loop-audit.jsonl" | /usr/bin/grep -q "\"issue\": 1708" && ok "1708 the start is one audit line that names the issue" || ko "1708 the audit: $(cat "$T/st/loop-audit.jsonl" 2>/dev/null)"
# the owner twins: an owner stop after the drain ended, and an owner stop on an idle loop
li "[{\"number\":1708,\"labels\":[{\"name\":\"loop-bug\"}]}]"
bash "$T/s/watchd.sh" bugdrain 1708 >/dev/null 2>&1; hdown
bash "$T/s/watchd.sh" stop >/dev/null 2>&1
li "[{\"number\":427,\"labels\":[]}]"; bash "$T/s/health.sh" >/dev/null 2>&1
! hsup && ok "1708 a loop the owner stopped after the drain stays off when the loop-bug closes" || ko "1708 the watcher started a loop the owner stopped"
rm -f "$T/st/auto"/*; (cd "$T/co" && bash "$T/s/watchd.sh" start >/dev/null 2>&1); hsup || ko "1708 the loop did not start for the idle owner stop"
bash "$T/s/watchd.sh" stop >/dev/null 2>&1; hdown
o=$(bash "$T/s/health.sh" 2>&1)
! hsup && printf "%s" "$o" | /usr/bin/grep -q "the owner stopped the loop" && ok "1708 an owner stop on an idle loop is not relit, and the pass names it" || ko "1708 the pass relit a loop the owner stopped: $o"
bash "$T/s/watchd.sh" stop --force >/dev/null 2>&1; unset SG_LOOPBUG_WATCH_S SG_DRAIN_POLL
pkill -f "$T/s/watchd.sh run" 2>/dev/null
kill $wp $fp 2>/dev/null; rm -rf "$T" "$P"; exit $fail
