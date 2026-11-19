#!/bin/bash
# seconds: 12.00
# A wave that reached its age cap retires its run before any issue of the run goes back to the queue (#1970). The landing
# pass names the run to the retirement (SG_RETIRE_CMD, a stub here: no process is read or signalled), and only an exit 0
# lets the requeue go on. A held retirement keeps the claim (the dispatch marker stays, no WAVE REQUEUE line) and leaves a
# retire marker; `wave.sh retire <run>` tries it again on a later pass and requeues each issue once. A bare local remote,
# a stub gh and a stub board: no GitHub call.
# usage: bash test-wave-retire.sh [scripts dir]
unset REV REV_BASE REV_SHA SG_SERIAL_HELD SG_GATE_STATUS SG_GATE_KIND SERIAL_WAIT_SECONDS SERIAL_HOLD_SECONDS SERIAL_POLL_SECONDS
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
cp "$d/wave.sh" "$d/columnlimit.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/wavepause.py" "$d/child_issues.py" "$d/address.sh" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/base-battery.sh" "$d/basecheck.sh" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SG_RETIRE_CMD=$T/bin/retire
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf 'export SG_LAND_REMOTE=%s/origin.git SG_WAVE_TIP_GATES=true SG_BOARD_VOCAB=shipped\n' "$T"
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
cat > "$T/bin/gh" <<STUB
#!/bin/bash
printf '%s\n' "\$*" >> "$T/gh.log"
case "\$*" in
  *"issue(number:"*) n=\$(printf '%s' "\$*" | sed -n 's/.*issue(number: \([0-9][0-9]*\)).*/\1/p')
    printf '{"data":{"repository":{"i":{"number":%s,"state":"OPEN","labels":{"nodes":[]}}}}}' "\${n:-0}" ;;
  *"issues/"*) echo '{"number":1,"state":"open"}' ;;
  *) echo '{}' ;;
esac
STUB
# The retirement stub: it logs its argv and exits with the code in retire.rc (1, a survivor, when the file is missing).
printf '#!/bin/bash\necho "$*" >> "%s/retire.log"\necho "RETIRE stub ${1##*/}"\nexit $(cat "%s/retire.rc" 2>/dev/null || echo 1)\n' "$T" "$T" > "$T/bin/retire"
chmod +x "$T/bin/gh" "$T/bin/retire"; export PATH="$T/bin:$PATH"
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"
# mkrun <run> <issues> [message]: the run's folder, its dispatch request and wave; with a message, one child commit on the wave.
mkrun() { local run=$1 n=$2 msg=${3:-} rd="$T/co/runs/$1" i
  mkdir -p "$rd"; : > "$rd/record.jsonl"
  printf 'CHILD — work GitHub issue(s) %s: do it\n' "$n" > "$rd/request.txt"
  for i in ${n//,/ }; do echo "$run" > "$T/st/auto/disp-$i"; done
  (cd "$T/co" && bash "$T/s/wave.sh" start "$run") >/dev/null
  [ -n "$msg" ] || return 0
  rm -rf "$T/c-$run"; $G clone -q "$T/origin.git" "$T/c-$run" 2>/dev/null
  (cd "$T/c-$run" && $G checkout -q -b w "origin/wave/$run" && echo "$run" > "g-$run" && $G add "g-$run" \
    && $G -c user.email=a@b -c user.name=t commit -q -m "$msg" && $G push -q origin "HEAD:refs/heads/wave/$run") 2>/dev/null; }
entered() { (. "$T/s/env.sh"; python3 "$T/s/issue_ledger.py" wave "x/y#$2" "$1" entered "child entered") >/dev/null; }
cap() { printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-$1"; }
land() { (cd "$T/co" && bash "$T/s/wave.sh" land "$1" 2>&1); }
retry() { (cd "$T/co" && bash "$T/s/wave.sh" retire "$1" 2>&1); }
queued() { printf '%s\n' "$2" | /usr/bin/grep -c "WAVE REQUEUE #$1:"; }

# A: an empty wave past its age cap, and a run that survives its retirement: no requeue, the claim stays, a marker stays.
mkrun run-a 91; cap run-a; echo 1 > "$T/retire.rc"; : > "$T/retire.log"
r=$(land run-a); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty in dev"*) true;; *) false;; esac && ok "A: the empty wave lands" || ko "A: exit $rc: '$r'"
/usr/bin/grep -q "runs/run-a" "$T/retire.log" && ok "A: the landing asks to retire the run" || ko "A: no retirement asked: '$(cat "$T/retire.log")'"
[ "$(queued 91 "$r")" = 0 ] && [ -e "$T/st/auto/disp-91" ] && ok "A: a held retirement requeues nothing and keeps the claim" || ko "A: requeued before a verified end: '$r'"
[ -e "$T/st/auto/retire-run-a" ] && ok "A: the pending retirement is kept for a later pass" || ko "A: no retire marker"
case "$r" in *"WAVE RETIRE-HOLD run-a"*) ok "A: the hold is named";; *) ko "A: no hold line: '$r'";; esac
# A2: the next pass: the run still survives, so nothing moves.
r=$(retry run-a)
[ "$(queued 91 "$r")" = 0 ] && [ -e "$T/st/auto/disp-91" ] && [ -e "$T/st/auto/retire-run-a" ] && ok "A2: a retry with a survivor still holds" || ko "A2: '$r'"
# A3: the run ended: the retry requeues the issue once and drops the marker; one more retry does nothing.
echo 0 > "$T/retire.rc"; r=$(retry run-a)
[ "$(queued 91 "$r")" = 1 ] && [ ! -e "$T/st/auto/disp-91" ] && [ ! -e "$T/st/auto/retire-run-a" ] && ok "A3: a verified end requeues the issue" || ko "A3: '$r'"
: > "$T/retire.log"; r=$(retry run-a)
[ "$(queued 91 "$r")" = 0 ] && [ ! -s "$T/retire.log" ] && ok "A3: the requeue happens once" || ko "A3: a second requeue: '$r'"

# B: a non-empty wave past its age cap: #60 entered and lands, #61 waits on the retirement of its run (a partial landing).
mkrun run-b 60,61 "work 60 (Refs #60)"; entered run-b 60; cap run-b; echo 1 > "$T/retire.rc"; : > "$T/retire.log"
r=$(land run-b); rc=$?
[ $rc = 0 ] && case "$r" in *"landed in dev (#60)"*) true;; *) false;; esac && ok "B: the wave lands #60" || ko "B: exit $rc: '$r'"
[ "$(queued 61 "$r")" = 0 ] && [ -e "$T/st/auto/disp-61" ] && [ -e "$T/st/auto/retire-run-b" ] && ok "B: #61 keeps its claim while its run survives" || ko "B: '$r'"
echo 0 > "$T/retire.rc"; r=$(retry run-b)
[ "$(queued 61 "$r")" = 1 ] && [ "$(queued 60 "$r")" = 0 ] && [ ! -e "$T/st/auto/disp-61" ] && ok "B: the retry requeues only #61" || ko "B: retry '$r'"

# C: the run ends at once: the landing requeues in the same pass, after the retirement.
mkrun run-c 93; cap run-c; echo 0 > "$T/retire.rc"; : > "$T/retire.log"
r=$(land run-c)
[ "$(queued 93 "$r")" = 1 ] && [ ! -e "$T/st/auto/retire-run-c" ] && ok "C: an ended run's issue is requeued by the landing" || ko "C: '$r'"
case "$r" in *"RETIRE stub run-c"*"WAVE REQUEUE #93"*) ok "C: the retirement comes before the requeue";; *) ko "C: order: '$r'";; esac

# D: a retry after an interrupted requeue: the issue the first try already requeued is not requeued again.
mkrun run-d 94,95; cap run-d; echo 1 > "$T/retire.rc"; land run-d >/dev/null
echo 94 >> "$T/st/auto/retire-run-d.done"; echo 0 > "$T/retire.rc"; r=$(retry run-d)
[ "$(queued 94 "$r")" = 0 ] && [ "$(queued 95 "$r")" = 1 ] && ok "D: an interrupted requeue goes on without a repeat" || ko "D: '$r'"

# E: a wave under its age cap never asks for a retirement.
mkrun run-e 96; : > "$T/retire.log"; land run-e >/dev/null
[ ! -s "$T/retire.log" ] && [ ! -e "$T/st/auto/retire-run-e" ] && ok "E: no age cap, no retirement" || ko "E: '$(cat "$T/retire.log")'"

# F: the real retirement (squad/retire.py) on a run that already has its verdict: it touches nothing, and the requeue goes on.
mkrun run-f 97; cap run-f
PYTHONPATH="$SG_SQUAD" python3 -c 'import sys; from squad import record
r = record.Record(sys.argv[1])
r.append("run_start", run_id="run-f", graph_name="g", graph_hash="h", engine_options={}, root="root", plan=[])
r.append("verdict", verdict="completed", exit_code=0, reasons=[], remains=[], folded=[])' "$T/co/runs/run-f/record.jsonl"
before=$(cat "$T/co/runs/run-f/record.jsonl")
r=$(SG_RETIRE_CMD= land run-f)
[ "$(queued 97 "$r")" = 1 ] && [ "$(cat "$T/co/runs/run-f/record.jsonl")" = "$before" ] && case "$r" in *"RETIRE OVER run-f"*) true;; *) false;; esac \
  && ok "F: a run with its verdict is untouched and its issue is requeued" || ko "F: '$r'"

[ $fail = 0 ] && echo "PASS test-wave-retire" || echo "FAIL test-wave-retire"
exit $fail
