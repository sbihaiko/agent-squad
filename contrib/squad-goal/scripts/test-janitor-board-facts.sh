#!/bin/bash
# seconds: 1.24
# The board facts have one runtime producer (ADR-0145): the janitor's reconcile fills the facts board_columns.column_for reads
# (wave_branch, landing, dev_ci, review_wait) from what the loop already knows -- the wave branch of a run on the remote, the
# review of its tip kept in the state dir, the check runs of the dev tip, and the review of a work branch -- and writes the
# column the table names. Stubbed gh and board.sh; a temp state dir, so the live loop state and board are never read or written.
# usage: bash test-janitor-board-facts.sh [janitor.py]
# (#3065) SG_SQUAD names the tree this suite sits in, and the pass runs from the fixture folder: the janitor reads squad/ from
# SG_SQUAD first and from the caller's cwd only as a fallback. The daily pass clears every SG_* setting and runs from a cwd
# that holds no squad/, so the fallback reached /squad/procs.py and the pass died at import with a traceback.
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; here="$(cd "$(dirname "$src")" && pwd)"
repo=$(cd "$here/../../.." && pwd)   # the tree this suite sits in: the janitor reads squad/ from SG_SQUAD, never from the caller's cwd
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board_facts.py" "$here/board-definition.json" "$here/spec_fold.py" "$T/s/"
# the gating fact's journal (#1257, ADR-0170) is a sibling module the janitor imports under -I
cp "$here/gate_attempts.py" "$here/board_flow_history.py" "$T/s/"
cp "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
# (#3056) The suite names its interpreter, never the `python3` the caller's PATH happens to hold first: under a minimal PATH
# that was Xcode's 3.9, and board_flow_history.py's `from datetime import UTC` raised an ImportError. caps.sh's
# sg_gate_python answers the gate's interpreter; a tree without caps.sh keeps the plain name.
py=python3; [ -f "$here/caps.sh" ] && { . "$here/caps.sh"; py=$(sg_gate_python 2>/dev/null) || py=python3; }; [ -n "$py" ] || py=python3
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
  *search/issues*) echo 0;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*) for n in 2301 2302 2303 2305 2306; do printf '%s\tTodo\thttps://api.github.com/repos/x/y\n' "$n"; done;;
  *branches/wave%2Frun-wt*) echo shaWT;;
  *commits/shaWT/check-runs*) echo '{"check_runs":[{"name":"test","conclusion":"success"}]}';;
  *repos/x/y/branches*) printf 'wave/run-wl shaWL\nwave/run-wc shaWC\nwave/run-wt shaWT\nfix/2305-x shaF5\nfix/2306-x shaF6\n';;
  *compare/dev...shaWT*|*compare/dev...shaF*) :;;
  *--include*commits/dev/check-runs*) echo '{"check_runs":[{"name":"test","status":"in_progress"}]}';;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 2301 2302 2303 2305 2306; do
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}' "$sep" "$n" "$n"; sep=,; done
    printf ']}}}}';;
  *issues/23*) echo $'open\t\tIssue';;
esac
J
"$py" - "$T/bin/gh" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
# compare answers: the wave of run-wt is in dev, every other branch is ahead of it
t = t.replace("  *compare/dev...shaWT*|*compare/dev...shaF*) :;;\n", "  *compare/dev...shaWT*) echo behind;;\n  *compare/dev...*) echo ahead;;\n")
open(p, "w").write(t)
PY
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s:\n' "$2" > "$T/co/runs/$1/request.txt"
  printf '{"event":"run_start","run_id":"%s","ts":"2026-10-13T08:00:00Z"}\n{"event":"land","outcome":"pass"}\n{"event":"verdict","verdict":"pass","reasons":[],"exit_code":0}\n' "$1" > "$T/co/runs/$1/record.jsonl"; }
mk run-wl 2301; mk run-wc 2302; mk run-wt 2303
printf 'VERDICT: APPROVE\n' > "$T/st/review-wave_run-wl.md"; printf 'shaWL\n' > "$T/st/review-wave_run-wl.sha"
printf 'VERDICT: APPROVE\n' > "$T/st/review-fix_2305-x.md"; printf 'old\n' > "$T/st/review-fix_2305-x.sha"
# The pass runs from the fixture folder with SG_SQUAD naming this tree, never the caller's cwd: a pass that starts under a
# checkout would read that checkout's package, and the daily pass starts under no tree at all (#3065).
o=$(cd "$T" && SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=dev SG_HUB_DIR="$T/st" "$py" -I "$T/s/janitor.py" 2>&1)
bc() { cat "$T/board.calls" 2>/dev/null; }
has() { /usr/bin/grep -q "$1" "$T/board.calls" 2>/dev/null; }
has '^STATUS o 4 Batching 2301$' && ok "an approved wave ahead of dev: its child's card is Batching" || ko "#2301 not Batching: $(bc) $o"
has '^STATUS o 4 Batching 2302$' && ok "a wave ahead of dev with no approval: Batching" || ko "#2302 not Batching: $(bc)"
has '^STATUS o 4 Closed 2303$' && ok "a wave in dev while the dev tip's CI runs: Closed, the CI holds nothing open" || ko "#2303 not Closed: $(bc) $o"
has '^STATUS o 4 Reviewing 2305$' && ok "a work branch whose review waits: Reviewing (#603)" || ko "#2305 not Reviewing: $(bc)"
has '^STATUS o 4 Batching 2306$' && ok "a work branch with no review yet keeps Batching" || ko "#2306 lost Batching: $(bc)"
/usr/bin/grep -q 'commits/dev/check-runs' "$T/gh.calls" && ok "the dev tip's CI is read once through the door" || ko "no check-runs read"
[ "$(/usr/bin/grep -c 'commits/dev/check-runs' "$T/gh.calls")" -le 1 ] && ok "the dev tip's CI is read at most once a pass" || ko "check-runs read more than once"
[ "$fail" = 0 ] && echo "PASS" || { echo "FAILED"; exit 1; }
