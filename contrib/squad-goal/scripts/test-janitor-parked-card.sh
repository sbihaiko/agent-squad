#!/bin/bash
# A run's end never moves a card the owner parked in Backlog (#2007). The loop's one backward move undoes its own
# dispatch claim, and a dispatch reads Todo only, so a card in Backlog was never claimed: it is the owner's (ADR-0142,
# ADR-0143). Rule R derives Todo for a run that ended without landing anything; that Todo must not reach a Backlog
# card, and rule O must not report the card as Todo nor promise a dispatch for it.
#  (A) a retired run ending with nothing landed keeps its card #1995 in Backlog: no card write at all;
#  (B) the same run end still moves its Doing card #1996 back to Todo, the one backward move (control);
#  (C) the end-of-run comment names the parked card, never "Its card is Todo";
#  (D) the pass names the park in cycle.log;
#  (E) no writer adds a label (ready-for-agent or any other) to the parked card.
# Stubbed gh and board; a temp state dir, so the live loop state and board are never read or written.
# usage: bash test-janitor-parked-card.sh [janitor.py]
for v in $(env | /usr/bin/grep -o '^SG_[A-Za-z0-9_]*'); do unset "$v"; done
src=${1:-$(dirname "$0")/janitor.py}; here="$(cd "$(dirname "$src")" && pwd)"; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"
cp "$here/issue_state.py" "$here/board_columns.py" "$here/board-definition.json" "$here/issue_ledger.py" "$here/spec_fold.py" "$here/board_facts.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$T/s/"
# #1995 was parked by the owner door; #1996 is a control card the loop itself claimed.
printf '1995\tBacklog\n1996\tDoing\n' > "$T/cards.tsv"
cat > "$T/s/board.sh" <<'B'
#!/bin/bash
echo "$*" >> "CARDDIR/board.calls"
a=("$@")
case "$1" in
  --*) ;;
  *) n="${a[$(($#-1))]}"; s="${a[*]:2:$(($#-3))}"
     [ -f "CARDDIR/cards.tsv" ] && awk -F'\t' -v n="$n" '$1 != n' "CARDDIR/cards.tsv" > "CARDDIR/cards.tmp"
     printf '%s\t%s\n' "$n" "$s" >> "CARDDIR/cards.tmp"; mv "CARDDIR/cards.tmp" "CARDDIR/cards.tsv";;
esac
B
sed -i.bak "s|CARDDIR|$T|g" "$T/s/board.sh"; rm -f "$T/s/board.sh.bak"; chmod +x "$T/s/board.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; echo "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *search/issues*) echo 0;;
  *repos/x/y/branches*) ;;
  *repos/x/y/compare/*) echo behind;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*) awk '{print $0"\thttps://api.github.com/repos/x/y"}' TMPDIR/cards.tsv;;
  *projectsV2/1/fields*) echo FID1;;
  *projectsV2/1/items*) ;;
  *check-runs*|*check_runs*) echo '{"total_count":0,"check_runs":[]}';;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    printf '{"number":1995,"title":"Parked bug","body":"","state":"OPEN","labels":{"nodes":[{"name":"bug"}]},"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}},'
    printf '{"number":1996,"title":"Claimed bug","body":"","state":"OPEN","labels":{"nodes":[{"name":"bug"},{"name":"ready-for-agent"}]},"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}'
    printf ']}}}}';;
  *issues/199*) case "$a" in *"-q .state"*) echo open;; *) echo $'open\t\tIssue';; esac;;   # rule O reads the state alone; the one facts read takes the TSV
esac
J
sed -i.bak "s|TMPDIR|$T|" "$T/bin/gh"; rm -f "$T/bin/gh.bak"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue #%s (bug; one PR):\n' "$2" > "$T/co/runs/$1/request.txt"
  printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #%s on fix/%s-x"}\n{"event": "verdict", "verdict": "killed", "reasons": ["retired"], "exit_code": 0}\n' "$2" "$2" > "$T/co/runs/$1/record.jsonl"
  echo "$1" > "$T/st/auto/bug-$2"
  touch -t 202001010000 "$T/co/runs/$1" "$T/co/runs/$1/record.jsonl" "$T/st/auto/bug-$2"; }
mk run-ended-1995 1995
mk run-ended-1996 1996
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$T/st" SG_FORK_DIR= SG_FORK_BASE= SG_BOARD_VOCAB=shipped SG_TAKEOVER_SECONDS=1000000000000 JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1; }
o=$(run)
bc() { cat "$T/board.calls" 2>/dev/null; }
col() { /usr/bin/awk -v n="$1" '$1==n{print $2}' "$T/cards.tsv" 2>/dev/null; }
[ -z "$(/usr/bin/grep -E '^(o|\-\-add) 4 [A-Za-z ]+ 1995$' "$T/board.calls" 2>/dev/null)" ] \
  && ok "A: a parked card in Backlog takes no write from its run's end" \
  || ko "A #1995 was written by the run's end: $(bc)"
[ "$(col 1995)" = "Backlog" ] && ok "A: #1995 stays in Backlog after its run ends" || ko "A #1995 moved to $(col 1995)"
o2=$(run)
[ "$(col 1995)" = "Backlog" ] && ok "A: a second pass keeps #1995 in Backlog" || ko "A #1995 moved on the second pass to $(col 1995)"
/usr/bin/grep -q '^o 4 Todo 1996$' "$T/board.calls" 2>/dev/null && ok "B: a Doing card still returns to Todo, the loop's own claim undone" || ko "B #1996 not back in Todo: $(bc) $o"
cq1995=$(cat "$T"/st/cq/*1995* 2>/dev/null)
case "$cq1995" in *"Its card is Todo"*) ko "C the comment claims the parked card is Todo: $cq1995";; *) ok "C: the comment never claims the parked card is Todo";; esac
case "$cq1995" in *Backlog*) ok "C: the comment names the park in Backlog";; *) ko "C the comment does not name Backlog: $cq1995";; esac
/usr/bin/grep -qi 'parked' "$T/st/cycle.log" 2>/dev/null && ok "D: the pass names the park in cycle.log" || ko "D cycle.log names no park: $(cat "$T/st/cycle.log" 2>/dev/null)"
[ -z "$(/usr/bin/grep -E 'issues/1995/labels' "$T/gh.calls" 2>/dev/null)" ] && ok "E: no label write reaches the parked card" || ko "E a label write reached #1995: $(/usr/bin/grep 'issues/1995/labels' "$T/gh.calls")"
rm -rf "$T"; exit $fail
