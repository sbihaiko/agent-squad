#!/bin/bash
# seconds: 1.04
# Regression test for throughput.sh: the loop's throughput over a time window, read from local records only (#564).
# usage: bash test-throughput.sh [path/to/throughput.sh]
# Hermetic: every inherited SG_* is dropped, and the fixture is a throwaway checkout, state folder and run list. A `gh`
# stub first in PATH records any GitHub call, and the report makes none.
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(dirname "$0"); src=${1:-$d/throughput.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/co" "$T/st" "$T/bin"
cp "$src" "$T/s/throughput.sh" 2>/dev/null || { echo "FAIL no throughput.sh at $src"; exit 1; }
cp "$d/throughput.py" "$d/child_issues.py" "$T/s/" 2>/dev/null
printf '#!/bin/bash\n: > %s\n' "$T/ghcalled" > "$T/bin/gh"; chmod +x "$T/bin/gh"

# A local clone on the base branch, with commits at known calendar dates. Commit date is the landing date.
G() { git -c user.name=t -c user.email=t@t "$@"; }
G init -q -b dev "$T/co"
# A commit must sit in the past: git's --since drops a commit dated after now, and today's own commit is not future-dated.
ago() { python3 -c 'import datetime,sys
n=int(sys.argv[1]); now=datetime.datetime.now()
print((now-datetime.timedelta(seconds=1) if n==0 else now-datetime.timedelta(days=n)).strftime(sys.argv[2]))' "$1" "$2"; }
stamp() { ago "$1" "%Y-%m-%dT%H:%M:%S"; }
day() { python3 -c 'import datetime,sys;print((datetime.date.today()-datetime.timedelta(days=int(sys.argv[1]))).isoformat())' "$1"; }
stampn() { ago "$1" "run-%Y%m%d-%H%M%S"; }
commit() { local dt; dt=$(stamp "$1"); printf '%s\n' "$2" > "$T/msg"
  GIT_AUTHOR_DATE="$dt" GIT_COMMITTER_DATE="$dt" G -C "$T/co" commit -q --allow-empty -F "$T/msg"; }

commit 5 'Old work

Refs #100'                       # outside the default window, and the root: a history is ordered oldest first
commit 2 'Feature D

Refs #104
Refs #105'                       # one commit may name two issues
commit 1 'Feature C

Refs #103'
commit 0 'TDD RED: feature A

Refs #101'
commit 0 'Feature A

Refs #101'                       # the same issue twice on one day is one landed issue that day
commit 0 'Feature B

Refs #102'

# The run folders whose request names an issue. One names two issues, one has no request, and one is older than the window.
R0a=$(stampn 0); R0b=$(stampn 0); R1=$(stampn 1); R5=$(stampn 5); R0c=$(stampn 0)
mkdir -p "$T/co/runs/$R0a-r1" "$T/co/runs/$R0b-r2" "$T/co/runs/$R1-r3" "$T/co/runs/$R5-r4" "$T/co/runs/$R0c-r5"
printf 'GitHub issue #101\n' > "$T/co/runs/$R0a-r1/request.txt"
printf 'Refs #101\n' > "$T/co/runs/$R0b-r2/request.txt"
printf 'GitHub issues 102, 103\n' > "$T/co/runs/$R1-r3/request.txt"
printf 'Refs #104\n' > "$T/co/runs/$R5-r4/request.txt"   # outside the window, so issue 104 has no run in it

# The child review lines the review pass writes, the requeue line a CHANGES verdict appends, and the one a fix run with unchanged findings appends.
cat > "$T/st/watch.log" <<'EOF'
CHILD REVIEW run-20261018-121500-r1 child-1: squad/run-1/ws-child-1 123456789 VERDICT: APPROVE
CHILD REVIEW run-20261018-121500-r1 child-2: squad/run-1/ws-child-2 abcdef123 VERDICT: CHANGES
CHILD REVIEW REQUEUE #102: the review of its own diff asked for changes: a.sh:1 — wrong — fix
CHILD FIX REQUEUE #103: a fix run left the same review findings: b.sh:2 — wrong — fix
CHILD REVIEW run-20261018-121500-r1 child-3: squad/run-1/ws-child-3 111111111 VERDICT: APPROVE
CHILD REVIEW run-20261018-121500-r1 child-4: squad/run-1/ws-child-4 222222222 VERDICT: BLOCKED (every review model failed)
watch: a line that is not a child review
EOF

# The gate history gate.sh appends: <finish> TAB <kind> TAB <result> TAB <sha> TAB <start> TAB <selection>. Two selected gates
# (100 s and 50 s), three whole-battery gates (400 s, an empty selection, and a line with no selection field: 100 s and 200 s).
# The last line is not a history line at all, so the report skips it and counts nothing.
tab=$(printf '\t')
printf '%s\n' \
  "1000${tab}fast${tab}pass${tab}aaa${tab}900${tab}selected 3 files" \
  "2000${tab}fast${tab}pass${tab}bbb${tab}1950${tab}selected 1 files" \
  "3000${tab}full${tab}pass${tab}ccc${tab}2600${tab}all: no test imports x" \
  "4000${tab}gate${tab}fail${tab}ddd${tab}3900${tab}" \
  "5000${tab}gate${tab}pass${tab}eee${tab}4800" \
  "not a gate history line" > "$T/st/gate-history"

# The loop exports its address to every pass: the fixture names the same three settings, and a `gh` stub first in PATH would
# record any GitHub call.
run() { SG_CHECKOUT="$T/co" SG_STATE="$T/st" SG_BASE=dev PATH="$T/bin:$PATH" bash "$T/s/throughput.sh" "$@"; }
has() { printf '%s\n' "$out" | /usr/bin/grep -qxF -- "$1"; }
D0=$(day 0); D1=$(day 1); D2=$(day 2); D5=$(day 5)
before=$(find "$T/co" "$T/st" -type f | sort | while read -r f; do printf '%s %s\n' "$f" "$(cksum < "$f")"; done)

# --- the default window is the last 3 days -------------------------------------------------------
out=$(run 2>&1); rc=$?
[ "$rc" = 0 ] && ok "the report exits 0" || ko "the report exited $rc: $out"
has "  $D0: 2" && ok "today: two landed issues from three commits, the repeated issue counted once" || ko "today is not 2: $out"
has "  $D1: 1" && ok "yesterday: one landed issue" || ko "yesterday is not 1: $out"
has "  $D2: 2" && ok "two days ago: one commit naming two issues counts both" || ko "two days ago is not 2: $out"
has "  total: 5 issues" && ok "the total is the distinct issues of the window" || ko "the total is not 5: $out"
has "  $D5: 1" && ko "a commit outside the window was counted" || ok "a commit outside the window is left out"
has "Mean runs per issue: 1.33 (4 run-issue pairs over 3 issues)" && ok "the mean counts each issue's runs and averages them" || ko "the mean is not 1.33: $out"
has "  APPROVE: 2" && ok "two APPROVE lines are counted" || ko "APPROVE is not 2: $out"
has "  CHANGES: 1" && ok "one CHANGES line is counted" || ko "CHANGES is not 1: $out"
has "  BLOCKED: 1" && ok "one BLOCKED line is counted" || ko "BLOCKED is not 1: $out"
has "  REQUEUE: 2" && ok "one CHILD REVIEW REQUEUE and one CHILD FIX REQUEUE line are counted (#638)" || ko "REQUEUE is not 2: $out"
[ ! -e "$T/ghcalled" ] && ok "the report makes no GitHub call" || ko "the report called gh"
after=$(find "$T/co" "$T/st" -type f | sort | while read -r f; do printf '%s %s\n' "$f" "$(cksum < "$f")"; done)
[ "$before" = "$after" ] && ok "the report writes nothing into the checkout or the state folder" || ko "the report wrote into its sources"

# --- --days N narrows the window, today included ------------------------------------------------
out=$(run --days 1 2>&1); rc=$?
[ "$rc" = 0 ] && ok "--days 1 exits 0" || ko "--days 1 exited $rc: $out"
has "  $D0: 2" && ok "--days 1 keeps today" || ko "--days 1 lost today: $out"
has "  $D1: 1" && ko "--days 1 kept yesterday" || ok "--days 1 drops yesterday"
has "  total: 2 issues" && ok "--days 1 totals the one day" || ko "--days 1 total is not 2: $out"
has "Mean runs per issue: 2.00 (2 run-issue pairs over 1 issue)" && ok "--days 1 averages only the runs it counts" || ko "--days 1 mean is not 2.00: $out"

# --- a hand run in a checkout names no profile: it reads that checkout on dev and says it read no watch.log ----------
out=$(cd "$T/co" && PATH="$T/bin:$PATH" bash "$T/s/throughput.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "a hand run with no address exits 0" || ko "a hand run exited $rc: $out"
has "  $D0: 2" && ok "a hand run reads the checkout it stands in" || ko "a hand run lost the landed issues: $out"
printf '%s\n' "$out" | /usr/bin/grep -q 'no SG_STATE: no verdict was read' && ok "a hand run says it read no watch.log" || ko "a hand run did not say what it could not read: $out"
has "  no SG_STATE: no gate time was read" && ok "a hand run says it read no gate history" || ko "a hand run did not say it read no gate history: $out"

# --- gate time: selected gates and whole-battery gates, two figures (#647) -----------------------
out=$(run 2>&1)
has "Gate time (gate-history, all lines)" && ok "the report has a gate time section" || ko "no gate time section: $out"
has "  selected: 2 gates, 150 s total, 75.0 s mean" && ok "a selected gate counts as selected: two gates, 150 s" || ko "selected split is not 2 gates, 150 s: $out"
has "  whole battery: 3 gates, 700 s total, 233.3 s mean" && ok "a gate with no selection counts as whole battery: three gates, 700 s" || ko "whole-battery split is not 3 gates, 700 s: $out"

mkdir -p "$T/empty"; : > "$T/empty/gate-history"
out=$(SG_CHECKOUT="$T/co" SG_STATE="$T/empty" SG_BASE=dev PATH="$T/bin:$PATH" bash "$T/s/throughput.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "an empty gate history is not an error" || ko "an empty gate history exited $rc: $out"
has "  selected: 0 gates, 0 s total, 0.0 s mean" && has "  whole battery: 0 gates, 0 s total, 0.0 s mean" && ok "an empty gate history reports zero for both groups" || ko "an empty gate history is not zero for both groups: $out"
rm -f "$T/empty/gate-history"
out=$(SG_CHECKOUT="$T/co" SG_STATE="$T/empty" SG_BASE=dev PATH="$T/bin:$PATH" bash "$T/s/throughput.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && has "  selected: 0 gates, 0 s total, 0.0 s mean" && ok "a missing gate history reports zero for both groups and is not an error" || ko "a missing gate history: rc $rc: $out"

# --- the help text names what no local record can answer ----------------------------------------
out=$(run --help 2>&1); rc=$?
[ "$rc" = 0 ] && ok "--help exits 0" || ko "--help exited $rc"
printf '%s\n' "$out" | /usr/bin/grep -qi 'todo' && ok "the help text names Todo" || ko "the help text does not name Todo: $out"
printf '%s\n' "$out" | /usr/bin/grep -qi 'no local record' && ok "the help text says no local record stamps the move to Todo" || ko "the help text does not say what it cannot show: $out"
printf '%s\n' "$out" | /usr/bin/grep -q -- '--days' && ok "the help text names the window option" || ko "the help text does not name --days: $out"

rm -rf "$T"; [ $fail = 0 ] && echo "test-throughput: all checks green" || { echo "test-throughput: failed"; exit 1; }
