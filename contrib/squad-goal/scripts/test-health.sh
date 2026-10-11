#!/bin/bash
# Regression test for health.sh: a down watchd alerts once, a live one clears it, a silent cycle.log is reported. Stubbed env.sh/gh/notifier. usage: bash test-health.sh [health.sh]
src=${1:-$(dirname "$0")/health.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# hermetic: a fork gate (or any shell) exports SG_FORK_*; health.sh would then scan the real fork repo and add a fork-parked cause
unset SG_FORK_DIR SG_FORK_BRANCH SG_FORK_BASE SG_FORK_ORPHAN_MINUTES
# #1410: a real-looking parked fork in the caller's environment must not reach the assertions
P=$(mktemp -d); G=/usr/bin/git; pg() { $G -C "$P" -c user.email=a@b -c user.name=t "$@"; }; pg init -q -b local/next; echo 0 > "$P/f"; pg add f; pg commit -q -m init
pg checkout -q -b gui/7 local/next; echo 7 > "$P/g"; pg add g; pg commit -q -m w; pg checkout -q local/next; touch -t 202001010000 "$P/g"
export SG_FORK_DIR="$P" SG_FORK_BASE=local/next SG_FORK_BRANCH='gui/%n' SG_FORK_ORPHAN_MINUTES=0
unset "${!SG_@}"; export HOME="$T/home"   # hermetic: whatever the caller exports (the live loop's SG_*) stops here
mkdir -p "$HOME" "$T/s" "$T/st/auto" "$T/co" "$T/bin"; cp "$src" "$T/s/health.sh"
printf 'export SG_STATE=%s SG_CHECKOUT=%s SG_REPO=x/y SG_OWNER=o SG_PROJECT=7\n' "$T/st" "$T/co" > "$T/s/env.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
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
reset; bd; printf '10\tIn Progress\n1783\tIn Progress\n1792\tIn Progress\n' > "$T/board.tsv"; liverun run-a 10; liverun run-b 1768
bash "$T/s/health.sh" >/dev/null; nb=$(notif); al=$(alerts); cp "$T/st/auto/drift-prev" "$T/prev.bak" 2>/dev/null
o=$(HEALTH_DRY=1 bash "$T/s/health.sh")
printf '%s' "$o" | /usr/bin/grep -qF 'HEALTH runs-board-drift: 2 live run(s)' && ok "HEALTH_DRY prints the cause" || ko "dry: $o"
[ -e "$T/st/auto/health-runs-board-drift" ] && ko "HEALTH_DRY wrote the alert marker" || ok "HEALTH_DRY writes no marker"
[ "$(alerts)" = "$al" ] && ok "HEALTH_DRY writes no alerts.log line" || ko "HEALTH_DRY alerted"
[ "$(notif)" = "$nb" ] && ok "HEALTH_DRY does not notify" || ko "HEALTH_DRY notified"
cmp -s "$T/st/auto/drift-prev" "$T/prev.bak" && ok "HEALTH_DRY leaves drift-prev alone" || ok "HEALTH_DRY leaves no stale drift-prev"
kill $wp $fp 2>/dev/null; rm -rf "$T" "$P"; exit $fail
