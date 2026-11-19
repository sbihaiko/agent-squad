#!/bin/bash
# seconds: 18.04
# Regression test for wave.sh land (#1003): a wave whose commits name no issue (no `Refs #N` line at all) falls back to the
# issues its run was dispatched for, read from the run's own request (run_issues). It closes them as a Refs line would, and it
# clears their dispatch marker (auto/disp-<n>). A wave with a Refs line keeps the per-commit rule. A run whose issues cannot be
# read closes nothing and says so, naming the run. A bare local remote, a stub gh that logs each argv, a stub board: no GitHub call.
# usage: bash test-wave-norefs.sh [scripts dir]
unset REV REV_BASE REV_SHA SG_SERIAL_HELD SG_GATE_STATUS SG_GATE_KIND SERIAL_WAIT_SECONDS SERIAL_HOLD_SECONDS SERIAL_POLL_SECONDS
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
cp "$d/wave.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/wavepause.py" "$d/child_issues.py" "$d/address.sh" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/base-battery.sh" "$d/basecheck.sh" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SG_RETIRE_CMD=/usr/bin/true   # a capped run here is an ended run (#1970); test-wave-retire.sh holds the retirement
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf 'export SG_LAND_REMOTE=%s/origin.git SG_WAVE_TIP_GATES=true SG_BOARD_VOCAB=shipped\n' "$T"
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *) echo "{}";; esac\n' "$T" > "$T/bin/gh"
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"
# closes: the issue numbers the stub gh was asked to close, one per line, in order.
closes() { sed -n 's#^api -X PATCH repos/x/y/issues/\([0-9]*\) -f state=closed -f state_reason=completed$#\1#p' "$T/gh.log" 2>/dev/null | tr '\n' ' '; }
# mkrun <run> <issues|-> <message>: the run's folder with its dispatch request, its wave branch, and one child commit on the wave.
# <issues> is one issue or a comma list (a multi-issue request). An issue of `-` writes a request with no CHILD line and no
# record, so the run's issues cannot be read.
mkrun() { local run=$1 n=$2 msg=$3 rd="$T/co/runs/$1" i
  mkdir -p "$rd"; : > "$rd/record.jsonl"
  if [ "$n" = - ]; then rm -f "$rd/record.jsonl"; printf 'work with no child line\n' > "$rd/request.txt"
  else printf 'CHILD — work GitHub issue(s) %s: do it\n' "$n" > "$rd/request.txt"
    for i in ${n//,/ }; do echo "$run" > "$T/st/auto/disp-$i"; done; fi
  (cd "$T/co" && bash "$T/s/wave.sh" start "$run") >/dev/null
  rm -rf "$T/c-$run"; $G clone -q "$T/origin.git" "$T/c-$run" 2>/dev/null
  (cd "$T/c-$run" && $G checkout -q -b w "origin/wave/$run" && echo "$run" > "g-$run" && $G add "g-$run" \
    && $G -c user.email=a@b -c user.name=t commit -q -m "$msg" && $G push -q origin "HEAD:refs/heads/wave/$run") 2>/dev/null; }
# entered <run> <issue>: the issue ledger records that the issue's child entered the run's wave, as wave.sh enter writes it.
entered() { (. "$T/s/env.sh"; python3 "$T/s/issue_ledger.py" wave "x/y#$2" "$1" entered "child entered") >/dev/null; }
# cap <run>: the run's wave is past its age cap.
cap() { printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-$1"; }
land() { (cd "$T/co" && bash "$T/s/wave.sh" land "$1" 2>&1); }

# A: a one-issue run whose only commit names no issue: the landing closes the run's issue and clears its dispatch marker.
mkrun run-a 41 "work with no reference line"; : > "$T/gh.log"
r=$(land run-a); rc=$?
[ $rc = 0 ] && ok "A: the wave lands (exit 0)" || ko "A: exit $rc: '$r'"
[ "$(closes)" = "41 " ] && ok "A: the run's issue #41 closes, with the exact close argv" || ko "A: closes '$(closes)'; gh.log: $(cat "$T/gh.log")"
[ -e "$T/st/auto/disp-41" ] && ko "A: the dispatch marker of #41 stays" || ok "A: the dispatch marker of #41 is cleared"
case "$r" in *"landed in dev (#41)"*) ok "A: the landing names #41";; *) ko "A: the landing line: '$r'";; esac
case "$r" in *"no commit names an issue"*"run run-a was dispatched for"*) ok "A: the fallback is named with the run";; *) ko "A: no fallback note: '$r'";; esac

# B: a commit with a Refs line keeps the per-commit rule: that issue closes, and the dispatch marker is not the landing's.
mkrun run-b 42 "work 42 (Refs #42)"; : > "$T/gh.log"
r=$(land run-b); rc=$?
[ $rc = 0 ] && [ "$(closes)" = "42 " ] && ok "B: a Refs line closes its issue as before" || ko "B: exit $rc, closes '$(closes)': '$r'"
case "$r" in *"no commit names an issue"*) ko "B: the fallback ran on a wave with a Refs line: '$r'";; *) ok "B: no fallback on a wave with a Refs line";; esac
[ -e "$T/st/auto/disp-42" ] && ok "B: the Refs path leaves the dispatch marker as before" || ko "B: the Refs path removed disp-42"

# C: no Refs, a request with no CHILD line and no record: nothing closes, and one line names the run.
mkrun run-c - "work with no reference line"; : > "$T/gh.log"
r=$(land run-c); rc=$?
[ -z "$(closes)" ] && ok "C: nothing closes when the run's issues cannot be read" || ko "C: closes '$(closes)'"
case "$r" in *"run run-c"*"cannot be read"*) ok "C: the landing says the run's issues cannot be read, with the run id";; *) ko "C: no note: '$r'";; esac

# D: a two-issue run, no Refs, and only #60's child entered: only #60 closes; #61 stays open with its marker.
mkrun run-d 60,61 "work with no reference line"; entered run-d 60; : > "$T/gh.log"
r=$(land run-d); rc=$?
[ $rc = 0 ] && [ "$(closes)" = "60 " ] && ok "D: only the entered issue #60 closes" || ko "D: exit $rc, closes '$(closes)': '$r'"
[ -e "$T/st/auto/disp-61" ] && ok "D: the marker of #61, which never entered, stays" || ko "D: disp-61 was cleared"
[ -e "$T/st/auto/disp-60" ] && ko "D: disp-60 stays" || ok "D: the marker of #60 is cleared"

# E: the same run past its age cap: #61 goes back to the queue and never closes; #60 closes.
mkrun run-e 70,71 "work with no reference line"; entered run-e 70; cap run-e; : > "$T/gh.log"
r=$(land run-e); rc=$?
[ $rc = 0 ] && [ "$(closes)" = "70 " ] && ok "E: a capped wave closes only the entered #70" || ko "E: exit $rc, closes '$(closes)': '$r'"

# F: a one-issue run past its age cap whose child never entered: its issue goes back to the queue and closes not.
mkrun run-f 80 "work with no reference line"; cap run-f; : > "$T/gh.log"
r=$(land run-f); rc=$?
[ -z "$(closes)" ] && ok "F: a capped one-issue wave does not close the issue it requeued" || ko "F: closes '$(closes)': '$r'"

[ $fail = 0 ] && echo "PASS test-wave-norefs" || echo "FAIL test-wave-norefs"
exit $fail
