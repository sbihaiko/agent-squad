#!/bin/bash
# seconds: 1.51
# Regression test for a never-started run freeing its dispatch markers (Refs #1503).
# Fixture: run folder with request.txt naming GitHub issue #N, no record.jsonl, past the
# never-started threshold, auto/bug-N present. The janitor must drop the marker and queue
# one retry comment; a second never-started run for the same issue keeps the marker, queues
# an evidence comment (quoting launch.log) and raises HEALTH; no third retry.
# It also frees the auto/branchfix- marker of the fix round the run never began (#2309), and
# only a marker that names this run.
# Stubbed gh/notifier. usage: bash test-janitor-neverstarted.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"
cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *issues/*) if [ -n "${CLOSED:-}" ] && /usr/bin/grep -Eq "issues/${CLOSED}( |\$)" <<<"$*"; then echo closed; else echo open; fi;;
  *search/issues*) echo 0;;
  *pulls?state=open*) ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
req() { printf 'CHILD — GitHub issue(s) %s (bug)\n--- issue #%s\nfix it; see issue #900 for context\n' "$1" "$1"; }
mkrun() { # <run> <issue> <request name>: the request mkreq.sh wrote, copied into the run folder as launch.py does (#539)
  mkdir -p "$T/co/runs/$1" "$T/st/req"
  req "$2" > "$T/st/req/$3.txt"; cp "$T/st/req/$3.txt" "$T/co/runs/$1/request.txt"
  echo '{"root":"x"}' > "$T/co/runs/$1/graph.json"
  printf 'squad refused: vendor/skills is not clean\nNothing ran\n' > "$T/co/runs/$1/launch.log"
  touch -t 202001010000 "$T/co/runs/$1/request.txt"
  echo "$1" > "$T/st/active-runs.txt"
}
runj() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1; }

# 1. first never-started autobug run: drop auto/bug-N, queue attempt 1 of 2
mkrun run-never-a 1492 autobug-x
echo autobug-x > "$T/st/auto/bug-1492"
echo autobug-other > "$T/st/auto/bug-900"   # only mentioned in the request body prose, never dispatched here
echo run-never-a > "$T/st/auto/branchfix-gui_40-ns-123456789"   # (#2309) the round this run never started
echo run-other > "$T/st/auto/branchfix-gui_41-ns-987654321"     # another run's round, not this one's
o=$(runj)
[ ! -e "$T/st/auto/bug-1492" ] && ok "first never-started run drops auto/bug-N" || ko "bug marker kept: $o"
[ ! -e "$T/st/auto/branchfix-gui_40-ns-123456789" ] && ok "the never-started run frees the fix marker of the round it never began (#2309)" || ko "fix marker kept: $o"
[ -e "$T/st/auto/branchfix-gui_41-ns-987654321" ] && ok "a fix marker naming another run stays (#2309)" || ko "another run's fix marker was freed: $o"
[ -e "$T/st/auto/bug-900" ] && ok "an issue only named in the request body keeps its marker" || ko "freed bug-900 (mentioned in prose): $o"
[ -z "$(ls "$T/st/cq"/*#900-*.md 2>/dev/null)" ] && ok "no comment queued for an issue named only in prose" || ko "commented on 900: $(ls "$T/st/cq" 2>/dev/null)"
cq=$(ls "$T/st/cq"/*#1492-*.md 2>/dev/null | wc -l | tr -d ' ')
[ "$cq" = 1 ] && ok "first never-started run queues one comment" || ko "cq count=$cq files=$(ls "$T/st/cq" 2>/dev/null)"
/usr/bin/grep -q 'never started, dispatching again' "$T/st/cq"/*#1492-*.md && /usr/bin/grep -q 'attempt 1 of 2' "$T/st/cq"/*#1492-*.md \
  && ok "retry comment names attempt 1 of 2" || ko "retry comment: $(cat "$T/st/cq"/*#1492-*.md 2>/dev/null)"
/usr/bin/grep -q 'vendor/skills is not clean' "$T/st/cq"/*#1492-*.md && ok "retry comment quotes launch.log" || ko "launch.log not quoted"

# 2. second never-started run for the same issue: keep the marker, evidence + HEALTH, no third retry
rm -f "$T/st/cq"/*#1492-*.md
mkrun run-never-b 1492 autobug-y
echo autobug-y > "$T/st/auto/bug-1492"
o=$(runj)
[ -e "$T/st/auto/bug-1492" ] && ok "second never-started run keeps auto/bug-N" || ko "bug marker dropped on second: $o"
/usr/bin/grep -qi 'HEALTH' "$T/st/alerts.log" && ok "second never-started run raises HEALTH" || ko "no HEALTH: $(cat "$T/st/alerts.log" 2>/dev/null) $o"
/usr/bin/grep -q 'vendor/skills is not clean' "$T/st/cq"/*#1492-*.md && /usr/bin/grep -q 'attempt 2 of 2' "$T/st/cq"/*#1492-*.md \
  && ok "evidence comment quotes launch.log (attempt 2 of 2)" || ko "evidence: $(cat "$T/st/cq"/*#1492-*.md 2>/dev/null)"
# a third janitor pass on a new never-started folder must not drop the marker either
rm -f "$T/st/cq"/*#1492-*.md
mkrun run-never-c 1492 autobug-z
echo autobug-z > "$T/st/auto/bug-1492"
o=$(runj)
[ -e "$T/st/auto/bug-1492" ] && ok "no third retry: marker stays" || ko "third pass dropped the marker: $o"
[ "$(ls "$T/st/cq"/*#1492-*.md 2>/dev/null | wc -l | tr -d ' ')" = 0 ] && ok "no third comment queued" || ko "third comment: $(ls "$T/st/cq")"

# 3. same shape for auto/disp-N (autofrontier)
mkrun run-never-d 77 frontier-x
echo frontier-x > "$T/st/auto/disp-77"
o=$(runj)
[ ! -e "$T/st/auto/disp-77" ] && ok "first never-started run drops auto/disp-N" || ko "disp marker kept: $o"
/usr/bin/grep -q 'never started, dispatching again' "$T/st/cq"/*#77-*.md && ok "disp retry comment queued" || ko "no disp comment: $o"

# 4. (#868) an issue closed after its run was dispatched: its stale marker is released with no retry and no comment, so the
# closed issue is not re-armed by a "dispatching again" comment.
mkrun run-never-e 1603 autobug-c
echo autobug-c > "$T/st/auto/bug-1603"
export CLOSED=1603; o=$(runj); unset CLOSED
[ ! -e "$T/st/auto/bug-1603" ] && ok "a closed issue's stale marker is released" || ko "closed issue's marker kept: $o"
[ -z "$(ls "$T/st/cq"/*#1603-*.md 2>/dev/null)" ] && ok "no retry comment on a closed issue" || ko "retry queued for a closed issue: $(ls "$T/st/cq" 2>/dev/null)"

rm -rf "$T"; exit $fail
