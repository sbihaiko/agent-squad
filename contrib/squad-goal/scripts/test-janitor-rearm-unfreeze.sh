#!/bin/bash
# The owner's rearm lifts the needs-human freeze (#1562, ADR-0029, ADR-0149). `issue_ledger.py rearm` is the owner's act on
# an issue whose run stopped needs-human: it clears the loop's facts and records a floor (rearmed.ts). The reconciler folds a
# needs-human run that landed nothing into its legacy Blocked, which in the shipped vocabulary is no column but a
# frozen:owner label, and it writes that label back every pass. The fold reads the run record, which is immutable history, so
# the owner's act changed nothing and the label returned: the frontier skips frozen:owner, so no newer run can outrank the old
# one and the issue stays frozen for good.
# D1  a rearm newer than the run's verdict keeps the issue out of the freeze: no BOARD-FREEZE line and no label write.
# D2  a rearm older than the verdict is no floor: the freeze stands.
# D3  a resume with a new needs-human verdict after the rearm is newer than the floor: the freeze stands again.
# D4  a ledger that cannot be read holds the issue: the freeze stands (fail closed, ADR-0149 rule 4).
# D5  an issue that carries needs-owner is the owner's by its own label: the freeze stands.
# D6  a rearm never lifts the "nothing landed" stop a stale work branch would read as Code Complete.
# D7  the child-needs-human branch and the stopped_run branch freeze the same way, and the rearm lifts both.
# D8  the floor's run set never decides the freeze: a floor that names the run, with a verdict newer than its time, freezes.
# D9  the stopped_run path never reads a stale work branch as Code Complete after the rearm lifts its Blocked.
# D10 the BOARD-UNFROZEN line is written once per (issue, run verdict), never once per pass.
# Stubbed gh and board.sh; a temp state dir. usage: bash test-janitor-rearm-unfreeze.sh [janitor.py]
unset SG_BOARD_VOCAB
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_TAKEOVER_SECONDS=1000000000000   # the runs here carry fixed stamps and test the fold, not the takeover bound (ADR-0166)
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/state" "$T/co/runs" "$T/live"
here="$(cd "$(dirname "$src")" && pwd)"
cp "$src" "$T/s/janitor.py"
cp "$here/issue_state.py" "$here/issue_ledger.py" "$here/board_columns.py" "$here/board-definition.json" \
   "$here/board_facts.py" "$here/spec_fold.py" "$here/gate_attempts.py" "$T/s/"
cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$T/s/"
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
case "$1" in
  --run-fields) echo "RUN $2 $3 $4 $5 $6" >> "TMPDIR/board.calls";;
  --clear-run) echo "CLEAR $2 $3 $4" >> "TMPDIR/board.calls";;
  --add) echo "ADD $2 $3 $4 $5" >> "TMPDIR/board.calls";;
  *) echo "STATUS $1 $2 $3 $4" >> "TMPDIR/board.calls";;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *--include*search/issues*) echo '{"total_count":0,"items":[]}';;
  *--include*issues/*/comments*) echo '[]';;
  *--include*issues/*/events*) echo '[]';;
  *search/issues*) echo 0;;
  *repos/x/y/branches*) printf 'dev deadbeef\nfix/3006-a sha3006\nfix/3007-a sha3007\nfix/3009-a sha3009\n';;
  *repos/x/y/compare/*) echo ahead;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)
    for n in 3001 3002 3003 3004 3005 3006 3007 3008 3009 3010; do printf '%s\tTodo\thttps://api.github.com/repos/x/y\n' "$n"; done;;
  *issues/3005/labels*|*issues/3005*) echo $'open\t\tIssue\tneeds-owner';;
  *issues/30[0-9][0-9]*) echo $'open\t\tIssue';;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 3001 3002 3003 3004 3005 3006 3007 3008 3009 3010; do
      case $n in
        3005) labs='{"nodes":[{"name":"needs-owner"}]}';;
        *) labs='{"nodes":[]}';;
      esac
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":%s,"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}' "$sep" "$n" "$n" "$labs"; sep=,
    done
    printf ']}}}}';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
T0=1000   # the verdict stamp every run carries; the ledger's rearm time is read against it
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s:\n' "$2" > "$T/co/runs/$1/request.txt"; }
rec() { printf '%s\n' "$2" > "$T/co/runs/$1/record.jsonl"; touch -t 202001010000 "$T/co/runs/$1/record.jsonl"; }
nh() { printf '{"event":"verdict","verdict":"needs-human","reasons":["landing cap"],"exit_code":3,"ts":%s}' "$1"; }
# led <issue> <rearm ts|corrupt> [run ...]: the issue's ledger, with the owner's rearm at that time and the floor naming those
# runs, written by the ledger's own writer
led() {
  if [ "$2" = corrupt ]; then printf '{"key": "x/y#%s", this is not a ledger\n' "$1" > "$T/st/state/x__y__$1.json"; return; fi
  SG_STATE="$T/st" python3 -I - "$T/s" "x/y#$1" "$2" "${@:3}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import issue_ledger as L
rec = L.empty(sys.argv[2])
rec["rearmed"] = {"ts": float(sys.argv[3]), "runs": sorted(sys.argv[4:])}
L.save(sys.argv[2], rec)
PY
}
# D1: run 3001 stopped needs-human, nothing landed, rearm at T0+60, no owner label on the issue
mk run-d1 3001; rec run-d1 "{\"event\":\"run_start\",\"run_id\":\"run-d1\",\"ts\":\"1970-01-01T00:16:40Z\"}
$(nh $T0)"; led 3001 1060
# D2: the same run, rearm before the verdict
mk run-d2 3002; rec run-d2 "{\"event\":\"run_start\",\"run_id\":\"run-d2\",\"ts\":\"1970-01-01T00:16:40Z\"}
$(nh $T0)"; led 3002 940
# D3: rearm at T0+60, then a resume with a new needs-human verdict at T0+120
mk run-d3 3003; rec run-d3 "{\"event\":\"run_start\",\"run_id\":\"run-d3\",\"ts\":\"1970-01-01T00:16:40Z\"}
$(nh $T0)
{\"event\":\"resume\",\"run_id\":\"run-d3\",\"ts\":1120}
$(nh 1120)"; led 3003 1060
# D4: rearm at T0+60, but the ledger file is corrupt
mk run-d4 3004; rec run-d4 "{\"event\":\"run_start\",\"run_id\":\"run-d4\",\"ts\":\"1970-01-01T00:16:40Z\"}
$(nh $T0)"; led 3004 corrupt
# D5: rearm at T0+60, and the issue carries needs-owner
mk run-d5 3005; rec run-d5 "{\"event\":\"run_start\",\"run_id\":\"run-d5\",\"ts\":\"1970-01-01T00:16:40Z\"}
$(nh $T0)"; led 3005 1060
# D6: rearm at T0+60, and a work branch (fix/3006-a) is ahead of dev
mk run-d6 3006; rec run-d6 "{\"event\":\"run_start\",\"run_id\":\"run-d6\",\"ts\":\"1970-01-01T00:16:40Z\"}
$(nh $T0)"; led 3006 1060
# D7a: the child-needs-human branch: a child of the run names 3007, a sibling lands, the run stops needs-human
mk run-d7a 3007; rec run-d7a "{\"event\":\"run_start\",\"run_id\":\"run-d7a\",\"ts\":\"1970-01-01T00:16:40Z\"}
{\"event\":\"spawn_accepted\",\"child\":\"c1\",\"definition\":\"GitHub issue #3007\"}
{\"event\":\"spawn_accepted\",\"child\":\"c2\"}
{\"event\":\"land\",\"node\":\"c2\",\"outcome\":\"pass\",\"effect\":{\"name\":\"merge_land\",\"head\":\"a1b2c3\"}}
$(nh $T0)"; led 3007 1060
# D7b: the stopped_run branch: the run's own child landed 3008, and the run still stopped needs-human
mk run-d7b 3008; rec run-d7b "{\"event\":\"run_start\",\"run_id\":\"run-d7b\",\"ts\":\"1970-01-01T00:16:40Z\"}
{\"event\":\"spawn_accepted\",\"child\":\"c1\",\"definition\":\"GitHub issue #3008\"}
{\"event\":\"land\",\"node\":\"c1\",\"outcome\":\"pass\",\"effect\":{\"name\":\"merge_land\",\"head\":\"d4e5f6\"}}
$(nh $T0)"; led 3008 1060
# D8: the floor's run set names the run, but the verdict is newer than the rearm's time: the time decides, so the freeze stands.
# A run the ledger listed while it was still live lands here (runreview.sh writes its verdict before the run stops).
mk run-d8 3010; rec run-d8 "{\"event\":\"run_start\",\"run_id\":\"run-d8\",\"ts\":\"1970-01-01T00:16:40Z\"}
$(nh $T0)"; led 3010 940 run-d8
# D9: the stopped_run path: the run's own child landed locally, the run stopped needs-human, the rearm lifts its Blocked, and a
# stale work branch (fix/3009-a) is still ahead of dev
mk run-d9 3009; rec run-d9 "{\"event\":\"run_start\",\"run_id\":\"run-d9\",\"ts\":\"1970-01-01T00:16:40Z\"}
{\"event\":\"spawn_accepted\",\"child\":\"c1\",\"definition\":\"GitHub issue #3009\"}
{\"event\":\"land\",\"node\":\"c1\",\"outcome\":\"pass\",\"effect\":{\"name\":\"merge_land\",\"head\":\"0a1b2c\"}}
$(nh $T0)"; led 3009 1060
pass() { SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main \
    SG_HUB_DIR="$T/st" SG_LIVE_DIR="$T/live" SG_LAND_BRANCH=dev python3 -I "$T/s/janitor.py" 2>&1; }
o=$(pass)
pass > /dev/null 2>&1   # a second pass over the same state: the once-marker holds (D10)
lbl() { /usr/bin/grep -q "repos/x/y/issues/$1/labels.*frozen:owner" "$T/gh.calls" 2>/dev/null; }
bf() { /usr/bin/grep -q "BOARD-FREEZE #$1" "$T/st/cycle.log" 2>/dev/null; }
bc() { cat "$T/board.calls" 2>/dev/null; }
cl() { /usr/bin/grep BOARD-FREEZE "$T/st/cycle.log" 2>/dev/null; }
# D1: the rearm is newer than the run's verdict, so the owner's act lifts the freeze it was meant to lift.
! bf 3001 && ok "D1: a rearm newer than the needs-human verdict writes no BOARD-FREEZE" || ko "D1: the rearm did not lift the freeze: $(/usr/bin/grep 'BOARD-FREEZE #3001' "$T/st/cycle.log")"
! lbl 3001 && ok "D1: and no frozen:owner label is written back" || ko "D1: frozen:owner written back after the rearm: $(/usr/bin/grep 'issues/3001/labels' "$T/gh.calls")"
# D2: the rearm predates the verdict, so it is no floor for it. This case proves the fixture reaches the fold.
bf 3002 && ok "D2: a rearm older than the verdict leaves the freeze standing" || ko "D2: no freeze for a run the rearm predates: $(cl) $o"
lbl 3002 && ok "D2: and the frozen:owner label is written" || ko "D2: no frozen:owner label: $(cat "$T/gh.calls" 2>/dev/null)"
# D3: the resume's verdict is newer than the rearm, so the freeze returns.
bf 3003 && ok "D3: a new needs-human verdict after the rearm freezes again" || ko "D3: a newer verdict was covered by an older rearm: $(cl)"
lbl 3003 && ok "D3: and its frozen:owner label is written" || ko "D3: no frozen:owner label for the newer verdict"
# D4: a ledger that cannot be read is no floor: the freeze stands.
bf 3004 && ok "D4: a corrupt ledger keeps the freeze (fail closed)" || ko "D4: a corrupt ledger lifted the freeze: $(cl)"
lbl 3004 && ok "D4: and its frozen:owner label is written" || ko "D4: no frozen:owner label on a corrupt ledger"
# D5: the issue is the owner's by its own label.
bf 3005 && ok "D5: needs-owner keeps the freeze whatever the rearm says" || ko "D5: needs-owner did not hold: $(cl)"
lbl 3005 && ok "D5: and its frozen:owner label is written" || ko "D5: no frozen:owner label on a needs-owner issue"
# D6: lifting the freeze never turns a stale work branch into delivered work.
! /usr/bin/grep -q '^STATUS o 4 Code Complete 3006$' "$T/board.calls" 2>/dev/null \
  && ok "D6: a rearmed run's stale work branch writes no Code Complete" || ko "D6: #3006 read Code Complete from a stale branch: $(bc)"
# D7: both needs-human folds consult the same floor.
! bf 3007 && ok "D7: the child-needs-human branch writes no BOARD-FREEZE after the rearm" || ko "D7: the child-needs-human branch froze #3007: $(/usr/bin/grep 'BOARD-FREEZE #3007' "$T/st/cycle.log")"
! lbl 3007 && ok "D7: and writes no frozen:owner label for it" || ko "D7: frozen:owner label on #3007"
! bf 3008 && ok "D7: the stopped_run branch writes no BOARD-FREEZE after the rearm" || ko "D7: the stopped_run branch froze #3008: $(/usr/bin/grep 'BOARD-FREEZE #3008' "$T/st/cycle.log")"
! lbl 3008 && ok "D7: and writes no frozen:owner label for it" || ko "D7: frozen:owner label on #3008"
# The child-needs-human fold keeps its own stop, exactly as stopped_run does: the rearm lifts the freeze, never the "nothing
# landed" stop, so a stale work branch cannot read as delivered work (ADR-0143). Without this line, deleting stopped.add(n)
# from that fold would leave every other case green.
! /usr/bin/grep -q '^STATUS o 4 Code Complete 3007$' "$T/board.calls" 2>/dev/null \
  && ok "D7: a rearmed child-needs-human run's stale work branch writes no Code Complete" || ko "D7: #3007 read Code Complete from a stale branch: $(bc)"
# D8: the run set is no floor: a verdict newer than the rearm's time freezes, though the floor names the run.
bf 3010 && ok "D8: a floor that names the run does not lift a freeze its time never covered" || ko "D8: the run set bypassed the rearm time: $(cl)"
lbl 3010 && ok "D8: and the frozen:owner label is written" || ko "D8: no frozen:owner label when the floor names the run"
# D9: the stopped_run path keeps its stop, so a stale work branch never reads as delivered work.
! bf 3009 && ok "D9: the stopped_run path writes no BOARD-FREEZE after the rearm" || ko "D9: the stopped_run path froze #3009: $(/usr/bin/grep 'BOARD-FREEZE #3009' "$T/st/cycle.log")"
! /usr/bin/grep -q '^STATUS o 4 Code Complete 3009$' "$T/board.calls" 2>/dev/null \
  && ok "D9: a stopped_run issue's stale work branch writes no Code Complete after the rearm" || ko "D9: #3009 read Code Complete from a stale branch: $(bc)"
# D10: the report is a fact of the run verdict, not of the pass: a second pass writes no second line.
[ "$(/usr/bin/grep -c 'BOARD-UNFROZEN #3001' "$T/st/cycle.log" 2>/dev/null)" = 1 ] \
  && ok "D10: two passes write one BOARD-UNFROZEN line for one run verdict" || ko "D10: the BOARD-UNFROZEN line repeats every pass: $(/usr/bin/grep -c 'BOARD-UNFROZEN #3001' "$T/st/cycle.log" 2>/dev/null) lines"
rm -rf "$T"
[ "$fail" = 0 ] && echo "test-janitor-rearm-unfreeze: PASS" || echo "test-janitor-rearm-unfreeze: FAIL"
exit "$fail"
