#!/bin/bash
# A finished run is cleaned before FINISHED-RUN drops its line (#2419). A run whose verdict is final leaves active-runs.txt
# on its first pass, and no later pass reads it, so every cleanup must run in that same pass: rule A kills the leftover
# processes, and the ccdefer markers go. Rule A already kills a leftover; its kill holds no longer, because the drop read
# the pass-start process table, so a process the pass had just killed still named the run. The deferral cleanup sat after
# the drop and never ran. Stubbed gh/board, a temp state dir: the live markers and cycle.log are never read or written.
# usage: bash test-janitor-finished-cleanup.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
here="$(cd "$(dirname "$src")" && pwd)"
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs" "$T/caps"
cp "$src" "$T/s/janitor.py"; cp "$here/board_columns.py" "$here/board-definition.json" "$T/s/"
cp "$here/issue_state.py" "$here/issue_ledger.py" "$here/spec_fold.py" "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; printf '%s\n' "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *search/issues*) case "$a" in *"-X GET"*|*"--method GET"*) echo 0;; *) printf '{\n  "message": "Not Found"\n}\n'; exit 1;; esac;;
  *projectsV2/4/fields*) printf 'FID4\n';;
  *projectsV2/4/items*) :;;
  *graphql*) printf '%s\n' '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}';;
  *repos/x/y/branches*) :;;
  *"-q .state") printf 'open\n';;
  *repos/x/y/issues/*) printf 'open\t\tIssue title\t\n';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# A run whose request names no issue: the pass reads no card and no GitHub fact for it, so each case is the run alone.
mk() { mkdir -p "$T/co/runs/$1"; printf '%s\n' "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — no issue named here:\n' > "$T/co/runs/$1/request.txt"; }
ended() { printf '%s\n' '{"event":"verdict","verdict":"failed","reasons":["no_landed_change"],"exit_code":1}' > "$T/co/runs/$1/record.jsonl"; touch -t 202001010000 "$T/co/runs/$1/record.jsonl"; }
# run-proc has its verdict, an old record and a live leftover that matches the run's own liveness (a gate process): the pass
# kills it and drops the line in one pass. run-def has its verdict, an old record and a deferral marker: the marker goes
# before the line. run-live has no verdict: its marker and its line both stay.
mk run-proc; ended run-proc
printf '#!/bin/bash\nsleep 300\n' > "$T/gate.sh"; chmod +x "$T/gate.sh"
( cd "$T/co/runs/run-proc" && exec "$T/gate.sh" ) & lp=$!
mk run-def; ended run-def
printf 'abc1234 2 0\n' > "$T/st/auto/ccdefer-run-def-c1"
live() { printf '%s\n' '{"event":"run_start","run_id":"'$1'"}' > "$T/co/runs/$1/record.jsonl"; }
mk run-live; live run-live
printf 'def5678 4 0\n' > "$T/st/auto/ccdefer-run-live-c1"
sleep 1   # the leftover's cwd is readable
run() { SQUAD_CAPS_DIR="$T/caps" SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_BOARD_VOCAB= SG_FORK_DIR= SG_FORK_BASE= SG_HUB_DIR="$T/hub" JANITOR_DASHBOARD_MAX=0 JANITOR_NOTIFY=true python3 -I "$T/s/janitor.py" 2>&1; }
o=$(run); o2=$(cat "$T/st/cycle.log" 2>/dev/null)
kill -0 "$lp" 2>/dev/null && ko "the leftover process of the finished run survived the pass" || ok "the pass kills the leftover process of a finished run"
echo "$o2" | /usr/bin/grep -q "pid=$lp .*leftover of finished run run-proc" && ok "the pass names the killed leftover in cycle.log" || ko "cycle.log does not name the killed leftover: $o2 $o"
! /usr/bin/grep -qx run-proc "$T/st/active-runs.txt" && ok "a run whose leftover the pass killed leaves active-runs in that same pass" || ko "the killed leftover still held the line: $(cat "$T/st/active-runs.txt") $o"
[ ! -e "$T/st/auto/ccdefer-run-def-c1" ] && ok "a finished run's deferral marker is dropped before its line" || ko "ccdefer-run-def-c1 survived the pass: $o"
echo "$o2" | /usr/bin/grep -q 'CCDEFER-DROPPED run-def' && ok "the pass names the dropped marker in cycle.log" || ko "cycle.log does not name run-def's dropped marker: $o2 $o"
! /usr/bin/grep -qx run-def "$T/st/active-runs.txt" && ok "the finished run leaves active-runs" || ko "run-def kept its line: $(cat "$T/st/active-runs.txt")"
[ -e "$T/st/auto/ccdefer-run-live-c1" ] && ok "a live run keeps its deferral marker" || ko "a live run lost its marker: $o"
/usr/bin/grep -qx run-live "$T/st/active-runs.txt" && ok "a live run keeps its line" || ko "a live run lost its line: $(cat "$T/st/active-runs.txt")"
kill "$lp" 2>/dev/null; wait 2>/dev/null
rm -rf "$T"; exit $fail
