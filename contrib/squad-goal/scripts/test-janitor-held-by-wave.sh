#!/bin/bash
# A closed issue whose old run left its wave open says nothing: the wave owns nothing once the issue is closed (#1566).
# Rule O holds an issue for an open wave and prints one HELD-BY-WAVE line per pass (observability only, #1423, ADR-0166).
# The line had no closed check, so an issue closed by another route (a hand landing, a duplicate, a fix on another branch)
# repeated the line on every pass for the life of the loop, and the cycle.log grew a line that described nothing.
#  (A) an open issue whose old run's wave is open prints the line once, and its dispatch marker stays;
#  (B) the same issue once it is closed prints no line, and its dispatch marker still stays (the rule is observability only);
#  (C) an answer that is not the open-issue list (no door beside the module) prints the line: a read that did not answer
#      decides nothing, so a transient failure never silences a line that is still true.
# Stubbed gh and board; a temp state dir, so the live loop state and board are never read or written.
# usage: bash test-janitor-held-by-wave.sh [janitor.py]
for v in $(env | /usr/bin/grep -o '^SG_[A-Za-z0-9_]*'); do unset "$v"; done
src=${1:-$(dirname "$0")/janitor.py}; here="$(cd "$(dirname "$src")" && pwd)"; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"
cp "$here/issue_state.py" "$here/board_columns.py" "$here/board-definition.json" "$here/issue_ledger.py" "$here/spec_fold.py" "$here/board_facts.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$T/s/"
printf '1566\topen\n' > "$T/iss.tsv"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"
case "$a" in
  *search/issues*) echo 0;;
  *repos/x/y/branches*) ;;
  *repos/x/y/compare/*) echo behind;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*) ;;
  *projectsV2/1/fields*) echo FID1;;
  *projectsV2/1/items*) ;;
  *check-runs*|*check_runs*) echo '{"total_count":0,"check_runs":[]}';;
  *graphql*)   # the one open-issue list: every issue this repository has open
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""
    while IFS=$'\t' read -r n s; do
      [ "$s" = open ] || continue
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}' "$sep" "$n" "$n"; sep=,
    done < ISSFILE
    printf ']}}}}';;
  *repos/x/y/issues/*)
    n=${a##*issues/}; n=${n%% *}; n=${n%%\?*}
    s=$(awk -F'\t' -v n="$n" '$1==n{print $2}' ISSFILE); s=${s:-open}
    case "$a" in *"@tsv"*) printf '%s\t\tIssue %s\t\n' "$s" "$n";; *) echo "$s";; esac;;
esac
J
sed -i.bak "s|ISSFILE|$T/iss.tsv|" "$T/bin/gh"; rm -f "$T/bin/gh.bak"; chmod +x "$T/bin/gh"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"; chmod +x "$T/s/board.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
export PATH="$T/bin:$PATH"
# One run ended an hour ago with a verdict, cut its wave, the wave is still open, and its dispatch marker holds #1566.
mkdir -p "$T/co/runs/run-old"; echo run-old >> "$T/st/active-runs.txt"
printf 'CHILD — GitHub issue(s) 1566:\n' > "$T/co/runs/run-old/request.txt"
printf '{"event": "run_start", "run_id": "rid-old"}\n{"event": "verdict", "verdict": "failed", "reasons": ["a child failed"], "exit_code": 1}\n' > "$T/co/runs/run-old/record.jsonl"
echo run-old > "$T/st/auto/disp-1566"; echo wave/run-old > "$T/st/auto/wave-run-old"; echo wave/run-old > "$T/st/auto/waved-run-old"
touch -t 202001010000 "$T/co/runs/run-old" "$T/co/runs/run-old/record.jsonl" "$T/co/runs/run-old/request.txt" "$T/st/auto/disp-1566"
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$T/st" SG_FORK_DIR= SG_FORK_BASE= SG_GH_AGGREGATE_WINDOW=0 SG_GH_CACHE_WINDOW=0 JANITOR_NOTIFY="$T/notify" python3 -I "$1/janitor.py" 2>&1; }
line="HELD-BY-WAVE #1566: run run-old ended failed, its wave is open; the landing pass owns the issue"
# A: the issue is open: the line is printed, once.
o1=$(run "$T/s")
[ "$(/usr/bin/grep -c "$line" "$T/st/cycle.log" 2>/dev/null)" = 1 ] && ok "A: an open issue whose old wave is open is named once" || ko "A: the line is not there once: $(cat "$T/st/cycle.log" 2>/dev/null) $o1"
[ -e "$T/st/auto/disp-1566" ] && ok "A: the held marker stays" || ko "A: the held marker went"
# B: the issue closed by another route: no line, and the marker still stays.
printf '1566\tclosed\n' > "$T/iss.tsv"
n1=$(/usr/bin/grep -c "$line" "$T/st/cycle.log" 2>/dev/null)
run "$T/s" >/dev/null
n2=$(/usr/bin/grep -c "$line" "$T/st/cycle.log" 2>/dev/null)
[ "$n1" = "$n2" ] && ok "B: a closed issue writes no HELD-BY-WAVE line" || ko "B: the closed issue still printed the line ($n1 then $n2): $(tail -3 "$T/st/cycle.log" 2>/dev/null)"
[ -e "$T/st/auto/disp-1566" ] && ok "B: the closed issue's marker still stays" || ko "B: the closed issue's marker went"
# C: no door beside the module: the list does not answer, so the line is still printed.
T2="$T/nodoor"; mkdir -p "$T2"; cp "$T/s"/*.py "$T/s"/*.json "$T/s"/refusal.sh "$T/s"/address.sh "$T/s"/refusal-names.sh "$T2"/ 2>/dev/null   # the door alone is missing
printf '1566\topen\n' > "$T/iss.tsv"
cp "$T/st/cycle.log" "$T/cycle.before"
o3=$(run "$T2")
[ "$(/usr/bin/grep -c "$line" "$T/st/cycle.log" 2>/dev/null)" -gt "$(/usr/bin/grep -c "$line" "$T/cycle.before" 2>/dev/null)" ] && ok "C: an unanswered open-issue list still prints the line" || ko "C: an unanswered list silenced the line: $o3"
# D (#2138): the run is over and every issue it names is closed, so nothing reads its line again. No wave stands for it
# either. The line leaves active-runs: a finished run the loop still counts as live work holds the child room the frontier
# needs, and six such lines left it with none.
printf '1566\tclosed\n' > "$T/iss.tsv"
rm -f "$T/st/auto/wave-run-old" "$T/st/auto/waved-run-old" "$T/st/issue-facts.json"   # the earlier passes cached the issue open; this pass reads it closed
run "$T/s" >/dev/null
/usr/bin/grep -q "run-old" "$T/st/active-runs.txt" 2>/dev/null && ko "D: a finished run whose issues are all closed keeps its line: $(cat "$T/st/active-runs.txt" 2>/dev/null)" || ok "D: a finished run whose issues are all closed leaves active-runs"
# E (#2138): the verdict is final and no wave stands for the run, so nothing reads its line again, whatever its issue says.
# A line the loop counts as live work holds the child room the frontier needs, and the landing pass is no part of this rule.
printf 'run-old\n' > "$T/st/active-runs.txt"
printf '1566\topen\n' > "$T/iss.tsv"
run "$T/s" >/dev/null
/usr/bin/grep -q "run-old" "$T/st/active-runs.txt" 2>/dev/null && ko "E: a finished run with no wave keeps its line: $(cat "$T/st/active-runs.txt" 2>/dev/null)" || ok "E: a finished run with no wave and no process leaves active-runs"
rm -rf "$T"; exit $fail
