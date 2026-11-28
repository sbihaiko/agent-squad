#!/bin/bash
# seconds: 2.94
# A card in Doing whose run ends without a verdict must come back to Todo, or the frontier never offers its issue again (ADR-0143).
# Rule C reaps a zombie run and rule O drops a missing run's dispatch marker; neither left a card a path back, so it sat in Doing.
# A spec's card is a fold over its children and never enters Doing (ADR-0142).
#  (A) a zombie run (no verdict, idle, no process) that held #911 with no marker: its card goes back to Todo in the same pass;
#  (B) a dispatch marker of a run whose folder is gone (rule O) leaves #915 in Todo on the next pass, under its reoffer marker;
#  (C) a spec #920 whose child #921 is in Doing under a live run is not lifted into Doing;
#  (D) a live run's card #913 is left alone;
#  (E) an owner-frozen zombie card #914 keeps its column;
#  (F) a second pass writes no card again, and no reoffer marker is left behind;
#  (G) a spec #922 that sits in Doing with no live child goes back to Todo, the column its lift started from (ADR-0143 correction);
#  (H) a spec #923 in Doing whose child #924 waits in Todo goes back to Todo, the owner's column, not Backlog;
#  (I) a spec #925 in Code Complete whose child #926 is in Doing under a live run leaves Code Complete for Todo;
#  (J) a card #927 in Doing with a recorded dispatch, no run, no marker and nothing landed goes back to Todo (the one-time
#      recovery); it carries no wave label, so the label is no proof of the loop's ownership;
#  (K) a card #928 in Doing with no recorded dispatch is a hand placement and stays in Doing.
# Stubbed gh and board; a temp state dir, so the live loop state and board are never read or written.
# The janitor loads squad/procs.py from the tree it runs in: it walks up from its own directory, then SG_SQUAD, SG_CHECKOUT
# and cwd. The fixture links the repository's squad package beside the janitor copy, and each pass runs from $T, so the
# product walk-up answers with no SG_SQUAD and never with the caller's cwd (#3105).
# usage: bash test-janitor-reoffer.sh [janitor.py]
for v in $(env | /usr/bin/grep -o '^SG_[A-Za-z0-9_]*'); do unset "$v"; done
src=${1:-$(dirname "$0")/janitor.py}; here="$(cd "$(dirname "$src")" && pwd)"; T=$(mktemp -d); fail=0
repo=$(cd "$here/../../.." && pwd -P)
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; ln -s "$repo/squad" "$T/s/squad"; cp "$src" "$T/s/janitor.py"
cp "$here/issue_state.py" "$here/board_columns.py" "$here/board-definition.json" "$here/issue_ledger.py" "$here/spec_fold.py" "$here/board_facts.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$T/s/"
printf '911\tDoing\n912\tTodo\n913\tDoing\n914\tDoing\n915\tDoing\n920\tBacklog\n921\tDoing\n922\tDoing\n923\tDoing\n924\tTodo\n925\tCode Complete\n926\tDoing\n927\tDoing\n928\tDoing\n' > "$T/cards.tsv"
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
a="$*"
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
    sep=""
    for n in 911 912 913 914 915 920 921 922 923 924 925 926 927 928; do
      labs='{"nodes":[{"name":"ready-for-agent"}]}'
      [ "$n" = 914 ] && labs='{"nodes":[{"name":"frozen:owner"}]}'
      [ "$n" = 920 ] || [ "$n" = 922 ] && labs='{"nodes":[{"name":"spec"}]}'
      [ "$n" = 923 ] || [ "$n" = 925 ] && labs='{"nodes":[{"name":"spec"}]}'
      subs='{"nodes":[]}'
      [ "$n" = 920 ] && subs='{"nodes":[{"number":921}]}'
      [ "$n" = 923 ] && subs='{"nodes":[{"number":924}]}'
      [ "$n" = 925 ] && subs='{"nodes":[{"number":926}]}'
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":%s,"subIssuesSummary":{"total":0,"completed":0},"subIssues":%s,"comments":{"nodes":[]}}' "$sep" "$n" "$n" "$labs" "$subs"; sep=,
    done
    printf ']}}}}';;
  *issues/9*) echo $'open\t\tIssue';;
esac
J
sed -i.bak "s|TMPDIR|$T|" "$T/bin/gh"; rm -f "$T/bin/gh.bak"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s (feature):\n' "$2" > "$T/co/runs/$1/request.txt"
  printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #%s on fix/%s-x"}\n' "$2" "$2" > "$T/co/runs/$1/record.jsonl"; }
# A: no verdict, idle long past the zombie bound, no process, no dispatch marker
mk run-zombie 911; touch -t 202001010000 "$T/co/runs/run-zombie" "$T/co/runs/run-zombie/record.jsonl"
# D: a live run (fresh record) holds #913 through its dispatch marker
mk run-live 913; echo run-live > "$T/st/auto/disp-913"
# E: a zombie whose issue carries frozen:owner keeps the column the owner gave it
mk run-frozen 914; touch -t 202001010000 "$T/co/runs/run-frozen" "$T/co/runs/run-frozen/record.jsonl"
# B: a run in active-runs whose folder is gone, holding a dispatch marker for #915 (rule O)
echo run-missing >> "$T/st/active-runs.txt"; echo run-missing > "$T/st/auto/disp-915"
# C: the child #921 of spec #920 is in Doing under a live run
mk run-child 921; echo run-child > "$T/st/auto/disp-921"
# I: the child #926 of spec #925 (in Code Complete) is in Doing under a live run
mk run-child2 926; echo run-child2 > "$T/st/auto/disp-926"
# J: a run recorded that it dispatched #927 (its entered fact in the issue's ledger), and it is gone; the card carries no wave label
SG_STATE="$T/st" SG_REPO=x/y python3 "$T/s/issue_ledger.py" wave 'x/y#927' run-old entered 'the child entered the wave' >/dev/null
run() { ( cd "$T" || exit 1; SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$T/st" SG_FORK_DIR= SG_FORK_BASE= SG_BOARD_VOCAB=shipped JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1 ); }
bc() { cat "$T/board.calls" 2>/dev/null; }
o=$(run)
/usr/bin/grep -q '^o 4 Todo 911$' "$T/board.calls" 2>/dev/null && ok "A: a zombie run with no marker returns its card to Todo" || ko "A #911 card not back in Todo: $o $(bc)"
[ "$(/usr/bin/grep -c '^o 4 [A-Za-z ]* 911$' "$T/board.calls" 2>/dev/null)" = 1 ] && ok "A: the zombie's card is written once" || ko "A #911 written more than once: $(bc)"
/usr/bin/grep -q '^o 4 Doing 920$' "$T/board.calls" 2>/dev/null && ko "C spec #920 lifted into Doing: $(bc)" || ok "C: a spec card is never lifted into Doing"
/usr/bin/grep -q '^o 4 Todo 922$' "$T/board.calls" 2>/dev/null && ok "G: a spec card in Doing with no live child returns to Todo" || ko "G spec #922 not returned to Todo: $(bc) $o"
/usr/bin/grep -q '^o 4 Backlog 922$' "$T/board.calls" 2>/dev/null && ko "G spec #922 returned to Backlog, which the board refuses: $(bc)" || ok "G: the correction is not Backlog, which the board refuses"
/usr/bin/grep -q '^o 4 [A-Za-z ]* 913$' "$T/board.calls" 2>/dev/null && ko "D live run #913 card written: $(bc)" || ok "D: a live run's card is left alone"
/usr/bin/grep -q '^o 4 [A-Za-z ]* 914$' "$T/board.calls" 2>/dev/null && ko "E owner-frozen #914 card written: $(bc)" || ok "E: an owner-frozen card keeps its column"
o2=$(run)
/usr/bin/grep -q '^o 4 Todo 915$' "$T/board.calls" 2>/dev/null && ok "B: a missing run's card returns to Todo on the pass after its marker is dropped" || ko "B #915 not back in Todo: $(bc) $o2"
before=$(/usr/bin/grep -c '^o 4 ' "$T/board.calls" 2>/dev/null)
o3=$(run)
after=$(/usr/bin/grep -c '^o 4 ' "$T/board.calls" 2>/dev/null)
[ "$before" = "$after" ] && ok "F: a later pass writes no card again" || ko "F the later pass wrote cards: $(bc) $o3"
[ ! -e "$T/st/auto/reoffer-911" ] && [ ! -e "$T/st/auto/reoffer-915" ] && ok "F: the reoffer markers of restored cards are consumed" || ko "F reoffer markers left: $(ls "$T/st/auto"/reoffer-*)"
[ -e "$T/st/auto/reoffer-914" ] && ok "F: an owner-frozen card keeps its marker until the owner moves it" || ko "F frozen #914 lost its marker"
/usr/bin/grep -q '^o 4 Todo 923$' "$T/board.calls" 2>/dev/null && ok "H: a spec in Doing whose child waits in Todo returns to Todo" || ko "H spec #923 not in Todo: $(bc) $o"
/usr/bin/grep -q '^o 4 Backlog 923$' "$T/board.calls" 2>/dev/null && ko "H spec #923 returned to Backlog, not the owner's column: $(bc)" || ok "H: the owner's column is not Backlog for it"
/usr/bin/grep -q '^o 4 Todo 925$' "$T/board.calls" 2>/dev/null && ok "I: a spec in Code Complete with a child in Doing returns to Todo" || ko "I spec #925 not in Todo: $(bc) $o"
/usr/bin/grep -q '^o 4 Todo 927$' "$T/board.calls" 2>/dev/null && ok "J: a card in Doing with a recorded dispatch, no wave label, no run, no marker and nothing landed returns to Todo" || ko "J #927 not in Todo: $(bc) $o"
/usr/bin/grep -q '^o 4 [A-Za-z ]* 928$' "$T/board.calls" 2>/dev/null && ko "K hand-placed #928 written: $(bc)" || ok "K: a card in Doing with no recorded dispatch is left alone"
/usr/bin/grep -q 'STALE-CARD #911: card Doing behind run run-zombie' "$T/st/cycle.log" 2>/dev/null && ok "A: the reoffer names the card's column as the shipped one" || ko "A message names the wrong column: $(cat "$T/st/cycle.log" 2>/dev/null)"
rm -rf "$T"; exit $fail
