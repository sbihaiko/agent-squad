#!/bin/bash
# Regression test for janitor rule V (STACK-BASE-LEFT-LANDING, #2260): a run whose child started on a blocker's approved tip
# is stopped and has its issue freed when that blocker leaves Landing without a landing; a blocker still in Landing, or one
# whose work already stands in the landing branch, keeps the run. Stubbed gh and a temp state dir; the live board and the
# live loop state are never touched. usage: bash test-janitor-stack-left.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
here="$(cd "$(dirname "$src")" && pwd)"
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/spec_fold.py" "$T/s/"
cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
cp "$here"/*.py "$T/s/" 2>/dev/null   # the modules the pass loads by path (wip_snapshot.py, squad_procs and the rest)
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$1" in
  --run-fields) echo "RUN $2 $3 $4 $5 $6" >> "TMPDIR/board.calls";;
  --clear-run) echo "CLEAR $2 $3 $4" >> "TMPDIR/board.calls";;
  --add) echo "ADD $2 $3 $4 $5" >> "TMPDIR/board.calls";;
  *) echo "STATUS $1 $2 $3 $4" >> "TMPDIR/board.calls";;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/s/board.sh" 2>/dev/null || sed -i "s|TMPDIR|$T|" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
# #2300: the blocker left Landing and its branch is still ahead of dev. #2301: a blocker still in Landing. #2304: a blocker
# whose branch tip already stands in dev, so its work landed and only the card lags.
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *repos/x/y/branches*)
    printf 'gui/2300-base sha2300\ngui/2301-base sha2301\ngui/2304-base sha2304\nmain shamain\n';;
  *compare/dev...sha2300*) echo ahead;;
  *compare/dev...sha2301*) echo ahead;;
  *compare/dev...sha2304*) echo behind;;
  *compare/main...*) echo ahead;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)
    printf '2300\tDoing\thttps://api.github.com/repos/x/y\n'
    printf '2301\tLanding\thttps://api.github.com/repos/x/y\n'
    printf '2302\tDoing\thttps://api.github.com/repos/x/y\n'
    printf '2303\tDoing\thttps://api.github.com/repos/x/y\n'
    printf '2304\tClosed\thttps://api.github.com/repos/x/y\n';;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 2300 2301 2302 2303 2304; do
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"comments":{"nodes":[]}}' "$sep" "$n" "$n"; sep=,; done
    printf ']}}}}';;
  *issues/23*/labels*) echo '[]';;
  *issues/23*) echo $'open\t\tIssue\t';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh" 2>/dev/null || sed -i "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
sq=$(cd "$here/../../.." && pwd -P)
ago() { date -u -v-5M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "-5 minutes" +%Y-%m-%dT%H:%M:%SZ; }
rs() { printf '{"event":"run_start","run_id":"%s","graph_name":"g","graph_hash":"h","engine_options":{},"root":"route","plan":["route"],"ts":"%s","pos":1}' "$1" "$2"; }
snn() { printf '{"event":"spawn_accepted","request_key":"k","child":"route.router.1","parent":"route","depth":1,"envelope":{},"reservation":{},"parent_entry":1,"ts":"%s","pos":2,"definition":{"prompt":"Fix GitHub issue #%s in the repository"}}' "$1" "$2"; }
# mkr <run> <blocker> [<tip line>] <issue>: a live run whose request names the blocker tip it was cut from.
mkr() { local name=$1 blk=$2 tip=$3 issue=$4; mkdir -p "$T/co/runs/$name"
  { echo "CHILD — GitHub issue(s) $issue"; echo "CUT FROM: gui/$blk-base"; echo "STACK TIP: $tip (#$blk, a blocker in Landing)"; } > "$T/co/runs/$name/request.txt"
  printf '%s\n' "$(rs "$name" "$(ago)")" "$(snn "$(ago)" "$issue")" > "$T/co/runs/$name/record.jsonl"
  printf '%s\n' "$name" > "$T/st/auto/disp-$issue"; echo "$name" >> "$T/st/active-runs.txt"
  ( cd "$T/co/runs/$name" && exec sleep 60 ) & echo $! > "$T/pid-$name"; disown; }
alive() { kill -0 "$(cat "$T/pid-$1" 2>/dev/null)" 2>/dev/null; }
mkr run-a 2300 aaaa111122223333 2302
mkr run-b 2301 bbbb111122223333 2303
# run-c had its branch cut from the base, with no Landing stack: its blocker's column is none of rule V's business.
mkdir -p "$T/co/runs/run-c"; { echo "CHILD — GitHub issue(s) 2305"; } > "$T/co/runs/run-c/request.txt"
printf '%s\n' "$(rs run-c "$(ago)")" "$(snn "$(ago)" 2305)" > "$T/co/runs/run-c/record.jsonl"
printf 'run-c\n' > "$T/st/auto/disp-2305"; echo run-c >> "$T/st/active-runs.txt"
( cd "$T/co/runs/run-c" && exec sleep 60 ) & echo $! > "$T/pid-run-c"; disown
sleep 0.3
o=$(SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_LAND_BRANCH=dev SG_HUB_DIR="$T/st" SG_SQUAD="$sq" python3 -I "$T/s/janitor.py" 2>&1)
for i in 1 2 3 4 5 6 7 8 9 10; do alive run-a || break; sleep 0.2; done
/usr/bin/grep -q 'STACK-BASE-LEFT-LANDING run-a' "$T/st/cycle.log" 2>/dev/null && ok "a blocker that left Landing without a landing stops the run" || ko "run-a not stopped: $o"
/usr/bin/grep -q 'run-a.*#2300.*Doing' "$T/st/cycle.log" 2>/dev/null && ok "the stop names the blocker and the column it left to" || ko "the stop names no blocker column: $o"
alive run-a && { ko "the process inside the stopped run survived"; kill "$(cat "$T/pid-run-a")"; } || ok "the stop reaches the processes inside the run folder"
[ -e "$T/st/auto/disp-2302" ] && ko "the stopped run kept its dispatch marker" || ok "the stopped run frees its issue's dispatch marker"
[ -e "$T/st/auto/reoffer-2302" ] && ok "the freed issue carries the card's way back to Todo" || ko "no reoffer marker for #2302"
tail -1 "$T/co/runs/run-a/record.jsonl" 2>/dev/null | /usr/bin/grep -q 'stack_base_left_landing:#2300' && ok "the ended run carries a verdict that says why" || ko "no verdict for run-a: $(tail -2 "$T/co/runs/run-a/record.jsonl")"
/usr/bin/grep -q 'STACK-BASE-LEFT-LANDING run-b' "$T/st/cycle.log" 2>/dev/null && ko "a blocker still in Landing stopped the run" || ok "a blocker still in Landing keeps the run"
alive run-b && ok "the run on a Landing blocker keeps its process" || ko "the process of run-b died"
[ -e "$T/st/auto/disp-2303" ] && ok "the run on a Landing blocker keeps its dispatch marker" || ko "run-b lost its marker"
/usr/bin/grep -q 'STACK-BASE-LEFT-LANDING run-c' "$T/st/cycle.log" 2>/dev/null && ko "a run cut from the base was stopped" || ok "a run cut from the base is untouched"
kill "$(cat "$T/pid-run-c" 2>/dev/null)" 2>/dev/null; kill "$(cat "$T/pid-run-b" 2>/dev/null)" 2>/dev/null
# #2304: the blocker's card reads Closed but its branch tip stands in dev: the work landed, only the card lags.
rm -f "$T/st/auto"/*; : > "$T/st/cycle.log"
mkr run-d 2304 dddd111122223333 2302
sleep 0.3
o=$(SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_LAND_BRANCH=dev SG_HUB_DIR="$T/st" SG_SQUAD="$sq" python3 -I "$T/s/janitor.py" 2>&1)
/usr/bin/grep -q 'STACK-BASE-LEFT-LANDING run-d' "$T/st/cycle.log" 2>/dev/null && ko "a blocker whose work reached dev stopped the run" || ok "a blocker whose work reached dev keeps the run"
kill "$(cat "$T/pid-run-d" 2>/dev/null)" 2>/dev/null
[ "$fail" = 0 ] && echo "PASS" || { echo "FAILED"; exit 1; }
rm -rf "$T"
