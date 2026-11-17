#!/bin/bash
# Rule R leaves a card the owner froze, and never moves a card forward for a run that stopped needs-human or failed.
#
# A run that reached the landing cap stopped needs-human; wave.sh stuck() froze its issue (frozen:owner) and put the card
# in Todo. Rule R then read the stale work branch, still ahead of dev, and wrote Code Complete over it: nothing had landed
# and the owner was the next actor. The freeze is the owner's hold on the column the work reached, so R leaves it alone
# and says why in cycle.log. A stopped run's work branch is stale in the same way: it never lifts the Todo or Blocked its
# own verdict set. An issue with no such freeze or stop still follows its branch to Code Complete.
# Stubbed gh and board.sh; a temp state dir. usage: bash test-janitor-frozen-owner.sh [janitor.py]
unset SG_BOARD_VOCAB   # this suite reads the legacy option names
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs" "$T/home"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/issue_ledger.py" "$(dirname "$src")/spec_fold.py" "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
here="$(cd "$(dirname "$0")" && pwd)"; cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$T/s/"
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
  *search/issues*) case "$a" in *"-X GET"*|*"--method GET"*) ;; *) echo '{"message":"Not Found"}'; exit 1;; esac; echo 0;;
  *repos/x/y/branches*) printf 'fix/3001-a sha3001\nfix/3002-a sha3002\nfix/3003-a sha3003\nfix/3004-a sha3004\nfix/3005-a sha3005\nfix/3006-a sha3006\n';;
  *repos/x/y/compare/*) echo ahead;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*) for n in 3001 3002 3003 3004 3005 3006; do printf '%s\tTodo\thttps://api.github.com/repos/x/y\n' "$n"; done;;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 3001 3002 3003 3004 3005 3006; do
      case $n in
        3001|3005) labs='{"nodes":[{"name":"frozen:owner"}]}';;
        *) labs='{"nodes":[]}';;
      esac
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":%s,"comments":{"nodes":[]}}' "$sep" "$n" "$n" "$labs"; sep=,
    done
    printf ']}}}}';;
  *issues/300[15]*) echo $'open\t\tIssue\tfrozen:owner';;
  *issues/30*) echo $'open\t\tIssue';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s:\n' "$2" > "$T/co/runs/$1/request.txt"; }
rec() { printf '%s\n' "$2" > "$T/co/runs/$1/record.jsonl"; }
# 3003 stopped needs-human with nothing landed; 3004 failed with nothing landed; 3006 stopped needs-human after a child
# landed work locally (the observed incident: the landing never reached dev).
mk run-needs-human 3003
rec run-needs-human '{"event":"run_start","run_id":"run-needs-human","ts":"2026-10-19T18:00:00Z"}
{"event":"verdict","verdict":"needs-human","reasons":["landing cap"],"exit_code":3}'
mk run-failed 3004
rec run-failed '{"event":"run_start","run_id":"run-failed","ts":"2026-10-19T18:01:00Z"}
{"event":"verdict","verdict":"failed","reasons":["red gate"],"exit_code":1}'
mk run-needs-human-land 3006
rec run-needs-human-land '{"event":"run_start","run_id":"run-needs-human-land","ts":"2026-10-19T18:02:00Z"}
{"event":"spawn_accepted","child":"route.router.1","definition":"GitHub issue #3006 on fix/3006-a"}
{"event":"land","node":"route.router.1","parent":"route","entry":1,"outcome":"pass","reasons":[],"effect":{"name":"merge_land","head":"a7df8f1ecff77bdb5833721121a7a50be4066053"}}
{"event":"verdict","verdict":"needs-human","reasons":["landing cap"],"exit_code":3}'
o=$(HOME="$T/home" SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" SG_LIVE_DIR="$T/none" \
    python3 -I "$T/s/janitor.py" 2>&1)
bc() { cat "$T/board.calls" 2>/dev/null; }
# 3001 carries frozen:owner and a branch ahead of dev: R writes nothing and says why.
! /usr/bin/grep -q ' 3001$' "$T/board.calls" 2>/dev/null \
  && ok "a frozen issue with a work branch ahead keeps its card where stuck() put it" \
  || ko "#3001 moved: $(bc) $o"
/usr/bin/grep -q 'BOARD-FROZEN #3001: frozen:owner holds the card in Todo' "$T/st/cycle.log" 2>/dev/null \
  && ok "cycle.log names why R left the frozen card" || ko "no BOARD-FROZEN reason: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q 'would have written Code Complete' "$T/st/cycle.log" 2>/dev/null \
  && ok "cycle.log names the write it held back" || ko "the held write is not named: $(cat "$T/st/cycle.log" 2>/dev/null)"
# 3002 is frozen by nothing: the branch still sets Code Complete.
/usr/bin/grep -q '^STATUS o 4 Code Complete 3002$' "$T/board.calls" 2>/dev/null \
  && ok "an unfrozen issue with a work branch ahead still goes to Code Complete" || ko "#3002 not Code Complete: $(bc) $o"
# 3003 stopped needs-human: its verdict sets Blocked, and the stale branch never lifts it.
/usr/bin/grep -q '^STATUS o 4 Blocked 3003$' "$T/board.calls" 2>/dev/null \
  && ok "a needs-human run keeps its card Blocked over a stale work branch" || ko "#3003 not Blocked: $(bc) $o"
/usr/bin/grep -q 'Code Complete 3003' "$T/board.calls" 2>/dev/null \
  && ko "#3003 moved forward over a stale work branch: $(bc)" || ok "and the stale branch never writes Code Complete"
# 3004 failed: the card stays in the queue; the branch is stale.
! /usr/bin/grep -q ' 3004$' "$T/board.calls" 2>/dev/null \
  && ok "a failed run's card stays in Todo over a stale work branch" || ko "#3004 moved: $(bc) $o"
# 3005 carries the owner freeze and stopped needs-human: R writes nothing.
! /usr/bin/grep -q ' 3005$' "$T/board.calls" 2>/dev/null \
  && ok "a frozen issue whose run stopped needs-human keeps its card" || ko "#3005 moved: $(bc) $o"
# 3006 is the observed shape: a needs-human run whose child landed locally, with the work branch still ahead.
/usr/bin/grep -q '^STATUS o 4 Blocked 3006$' "$T/board.calls" 2>/dev/null \
  && ok "a needs-human run that landed only locally keeps its card Blocked" || ko "#3006 not Blocked: $(bc) $o"
/usr/bin/grep -q 'Code Complete 3006' "$T/board.calls" 2>/dev/null \
  && ko "#3006 read Code Complete from a landing that stopped needs-human: $(bc)" || ok "and the local landing never writes Code Complete"
rm -rf "$T"
[ "$fail" = 0 ] && echo "test-janitor-frozen-owner: PASS" || echo "test-janitor-frozen-owner: FAIL"
exit "$fail"
