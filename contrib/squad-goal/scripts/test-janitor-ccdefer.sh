#!/bin/bash
# A run that recorded its verdict is over, and no deferral marker of it survives the pass (#945).
# childcc writes auto/ccdefer-<run>-<child> for a child its run never reviewed, and the wave reads that run's marker as a wait
# that is still owed: after the run recorded a verdict the marker names a wait that can never end, so watch.sh prints
# "its landing is deferred" every cycle for hours (run-20261030-184206-f63e6b, #886, #931). The finished-run branch drops
# every marker of a run that has its verdict and names them in cycle.log; a live run keeps its own, and a run whose name only
# starts with another run's name is never touched.
# Stubbed gh/board, a temp state dir: the live markers and cycle.log are never read or written.
# usage: bash test-janitor-ccdefer.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
here="$(cd "$(dirname "$src")" && pwd)"
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"
cp "$src" "$T/s/janitor.py"; cp "$here/board_columns.py" "$here/board-definition.json" "$T/s/"
cp "$here/issue_state.py" "$here/issue_ledger.py" "$here/ghcall.sh" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
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
  *repos/x/y/issues/*) printf 'open\t\tIssue title\t\n';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# A run whose request names no issue: the pass reads no card and no GitHub fact for it, so the case is the marker alone.
mk() { mkdir -p "$T/co/runs/$1"; printf '%s\n' "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — no issue named here:\n' > "$T/co/runs/$1/request.txt"; }
ended() { printf '%s\n' '{"event":"verdict","verdict":"failed","reasons":["no_landed_change"],"exit_code":1}' > "$T/co/runs/$1/record.jsonl"; }
# run-ended has its verdict: both of its deferral markers go. run-live has none: its marker stays. run-ended-long is a live
# run whose name only starts with run-ended's: its marker must survive the prefix.
mk run-ended; ended run-ended
printf 'abc1234 2 0\n' > "$T/st/auto/ccdefer-run-ended-c1"
printf 'abc1234 1 0\n' > "$T/st/auto/ccdefer-run-ended-c2"
live() { printf '%s\n' '{"event":"run_start","run_id":"'$1'"}' > "$T/co/runs/$1/record.jsonl"; }
mk run-live; live run-live
printf 'def5678 4 0\n' > "$T/st/auto/ccdefer-run-live-c1"
mk run-ended-long; live run-ended-long
printf 'fed9876 1 0\n' > "$T/st/auto/ccdefer-run-ended-long-c1"
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_BOARD_VOCAB= SG_FORK_DIR= SG_FORK_BASE= SG_HUB_DIR="$T/hub" JANITOR_DASHBOARD_MAX=0 JANITOR_NOTIFY=true python3 -I "$T/s/janitor.py" 2>&1; }
o=$(run)
[ ! -e "$T/st/auto/ccdefer-run-ended-c1" ] && ok "a finished run's deferral marker is dropped" || ko "ccdefer-run-ended-c1 survived the pass: $o"
[ ! -e "$T/st/auto/ccdefer-run-ended-c2" ] && ok "every deferral marker of the finished run is dropped, not just the first" || ko "ccdefer-run-ended-c2 survived the pass: $o"
/usr/bin/grep -q 'CCDEFER-DROPPED run-ended' "$T/st/cycle.log" 2>/dev/null && ok "the pass names the dropped markers in cycle.log" || ko "cycle.log does not name run-ended's dropped markers: $(cat "$T/st/cycle.log" 2>/dev/null) $o"
[ -e "$T/st/auto/ccdefer-run-live-c1" ] && ok "a live run keeps its deferral marker" || ko "a live run lost its marker: $o"
[ -e "$T/st/auto/ccdefer-run-ended-long-c1" ] && ok "another run's marker is never dropped by a shared name prefix" || ko "run-ended's drop took run-ended-long's marker: $o"
rm -rf "$T"
exit $fail
