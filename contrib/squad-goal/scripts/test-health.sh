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
mkdir -p "$HOME" "$T/s" "$T/st/auto" "$T/co" "$T/bin"; cp "$src" "$T/s/health.sh"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/ghcall.sh" "$T/s/ghcall.sh"; cp "$(dirname "$src")/workbranches.sh" "$T/s/workbranches.sh"
# hermetic: health.sh counts watch.sh with a real pgrep, so the default pattern would count the host's own live loop (a duplicate there is a third cause)
export SG_WATCH_CMD_RE="$T/s/watch\.sh\$"
export SQUAD_CAPS_DIR="$T/caps"   # hermetic: the reap step (#1226) reclaims leases here, never in the machine's caps folder
printf 'export SG_STATE=%s SG_CHECKOUT=%s SG_REPO=x/y SG_OWNER=o SG_PROJECT=7 SG_GHREAD=%s SG_SQUAD=%s\n' "$T/st" "$T/co" "$T/bin/rd" "$(cd "$(dirname "$src")/../../.." && pwd)" > "$T/s/env.sh"
# the door's reader, stubbed: the open-issue list one page at a time, from $T/pages/<n>.json (a page past the last is an empty list)
mkdir -p "$T/pages"; cat > "$T/bin/rd" <<'RD'
#!/bin/bash
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
  *"issues?state=open"*) [ -n "$GH_READY" ] && printf '%s\n' "$GH_READY";;
esac
GH
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\necho READY\n' > "$T/s/stack.sh"; chmod +x "$T/s/stack.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"; export HEALTH_NOTIFY="$T/notify"
touch "$T/st/cycle.log" "$T/st/active-runs.txt"; git -C "$T/co" init -q 2>/dev/null
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q fork-parked && ko "a parked fork in the caller env reached the suite: $o" || ok "a parked fork in the caller env does not reach the suite"
echo "$o" | /usr/bin/grep -q 'HEALTH watchd-down' && ok "watchd down -> alert" || ko "no watchd-down: $o"
[ $(($(wc -l < "$T/notify.calls"))) = 2 ] && ok "one notification per cause (watchd, fastloop)" || ko "notifications: $(cat "$T/notify.calls")"
bash "$T/s/health.sh" >/dev/null; [ $(($(wc -l < "$T/notify.calls"))) = 2 ] && ok "same cause: no second alert" || ko "alerted twice"
printf '#!/bin/bash\nsleep 60\n' > "$T/s/watchd.sh"; bash "$T/s/watchd.sh" run & wp=$!; echo $wp > "$T/st/watchd.pid"; bash "$T/s/fastloop.sh" 2>/dev/null; printf '#!/bin/bash\nsleep 60\n' > "$T/s/fastloop.sh"; bash "$T/s/fastloop.sh" & fp=$!; echo $fp > "$T/st/fastloop.pid"; sleep 1
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'watchd-down cleared' && ok "watchd back -> cleared" || ko "not cleared: $o"
touch -t 202001010000 "$T/st/cycle.log"; o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'HEALTH watch-stalled' && ok "silent cycle.log -> watch-stalled" || ko "no stall: $o"
mkdir -p "$T/co/runs/run-n"; echo r > "$T/co/runs/run-n/request.txt"; touch -t 202001010000 "$T/co/runs/run-n/request.txt"; echo run-n > "$T/st/active-runs.txt"
o=$(bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'HEALTH never-started-runs' && ok "record-less run -> never-started-runs" || ko "no never-started: $o"
# A wave-only issue can be READY after its blocker closes. The health pass must expose it without a triage label.
o=$(GH_READY=41 bash "$T/s/health.sh"); printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH frontier-idle: startable 41 with no work branch' && ok "a startable wave-only issue is reported" || ko "wave-only issue was not reported: $o"
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
rm -f "$T/pages/"*.json
# ---- #1795: runs-board-drift, the loop against its own board ----
bd() { { echo '#!/bin/bash'; printf 'case "$*" in *"/items"*) cat %s/board.tsv;; *"/fields"*) echo 422164739;; esac\n' "$T"; } > "$T/bin/gh"; chmod +x "$T/bin/gh"; }
alerts() { local n; n=$(/usr/bin/grep -c 'HEALTH runs-board-drift' "$T/st/alerts.log" 2>/dev/null); echo "${n:-0}"; }
notif() { local n; n=$(/usr/bin/grep -c . "$T/notify.calls" 2>/dev/null); echo "${n:-0}"; }
liverun() { mkdir -p "$T/co/runs/$1"; printf '{"ts":1,"event": "spawn_accepted", "text": "GitHub issue #%s"}\n' "$2" > "$T/co/runs/$1/record.jsonl"
  echo x > "$T/co/runs/$1/request.txt"; touch -t 202001010000 "$T/co/runs/$1/request.txt"; echo "$1" >> "$T/st/active-runs.txt"; }
reset() { : > "$T/st/active-runs.txt"; rm -rf "$T/co/runs" "$T/st/auto/drift-prev" "$T/st/auto/health-runs-board-drift"; mkdir -p "$T/co/runs"; }
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
printf '#!/bin/bash\nsleep 60\n' > "$T/s/watch.sh"; bash "$T/s/watch.sh" & w1=$!; SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh" >/dev/null
o=$(SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'loops-duplicated' && ko "one loop alerted: $o" || ok "one watch.sh is not a duplication"
bash "$T/s/watch.sh" & w2=$!; sleep 1; o=$(SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh")
# #17: the cause names the pids it counted, so an operator can compare them with the loop watchd started
wps=$(printf '%s\n' $w1 $w2 | sort -n | tr '\n' ' '); wps=${wps% }
printf '%s' "$o" | /usr/bin/grep -qF "HEALTH loops-duplicated: 2 watch.sh process(es) alive (pids $wps;" && ok "the duplicate names both pids" || ko "pids not named ($w1 $w2): $o"
kill $w1 $w2 2>/dev/null; wait $w1 $w2 2>/dev/null
# #17: the cause also names the pid the supervisor holds for its watch.sh, so the stray one is the pid that is not named there
rm -f "$T/st/auto/health-loops-duplicated"
mkdir -p "$T/sup"; printf '#!/bin/bash\nbash "'"$T"'/s/watch.sh" &\necho $! > "'"$T"'/held.pid"\nwait\n' > "$T/sup/watchd.sh"; printf '#!/bin/bash\nsleep 60\n' > "$T/s/watch.sh"
bash "$T/sup/watchd.sh" run & sp=$!; bash "$T/s/watch.sh" & w3=$!; sleep 1; hp=$(cat "$T/held.pid"); echo "$sp" > "$T/st/watchd.pid"
o=$(SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF "watchd pid $sp holds $hp" && ok "the duplicate names the pid the supervisor holds" || ko "supervisor pid not named (watchd $sp holds $hp, stray $w3): $o"
kill $sp $hp $w3 2>/dev/null; wait $sp $hp $w3 2>/dev/null; rm -f "$T/st/watchd.pid"
# #17: pgrep -f matches the command line, and a forked subshell (pipeline, $(...), a background group) inherits its parent's argv.
# One healthy loop that forks must not read as two; the fake loop keeps a pipeline and a nested $(...) alive for the whole check.
rm -f "$T/st/auto/health-loops-duplicated"
# a detached step, `( cmd & )`, is a fork whose parent exited: parent pid 1, the loop's argv, the loop's process group (watch.sh runs forkclose so)
printf '#!/bin/bash\n{ x=$(sleep 20; true); } &\n( { sleep 20; true; } & )\ntrue | { sleep 20; true; } | cat > /dev/null\nwait\n' > "$T/s/watch.sh"; bash "$T/s/watch.sh" & wf=$!; sleep 1
/bin/ps -axwwo ppid=,command= | /usr/bin/grep -v grep | /usr/bin/grep -q "^ *1 bash $T/s/watch\.sh\$" && ok "the fake loop leaves a detached fork (parent pid 1)" || ko "no orphaned fork in the fake loop"
nf=$(/usr/bin/pgrep -f "$T/s/watch\.sh$" | wc -l | tr -d ' ')
[ "$nf" -gt 1 ] && ok "the fake loop is forking ($nf pgrep matches for one loop)" || ko "the fake loop does not fork: $nf match(es), the next check proves nothing"
o=$(SG_WATCH_CMD_RE="$T/s/watch\.sh$" bash "$T/s/health.sh"); echo "$o" | /usr/bin/grep -q 'loops-duplicated' && ko "one forking loop alerted: $o" || ok "one watch.sh that forks is not a duplication"
kill $wf 2>/dev/null; /usr/bin/pkill -P $wf 2>/dev/null; wait $wf 2>/dev/null
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
kill $wp $fp 2>/dev/null; rm -rf "$T" "$P"; exit $fail
