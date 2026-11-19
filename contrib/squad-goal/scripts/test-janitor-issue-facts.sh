#!/bin/bash
# (#1744) A janitor pass reads the issues that changed since the last pass in one list read, and keeps each issue's facts in
# $SG_STATE/issue-facts.json. It read every issue of every run folder one by one on every pass: 155 reads and 96 s of a
# 181 s pass, for a repository with 11 open issues. The stub counts the reads.
# usage: bash test-janitor-issue-facts.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }
ko() { echo "FAIL $1"; fail=1; }
here="$(cd "$(dirname "$src")" && pwd)"
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"
cp "$src" "$T/s/janitor.py"; cp "$here/board_columns.py" "$here/board-definition.json" "$here/spec_fold.py" "$T/s/"
cp "$here/issue_state.py" "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
printf '#!/bin/bash\nexit 0\n' > "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; printf '%s\n' "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *'issues?state=all&since='*) [ -e "TMPDIR/sync.json" ] && cat "TMPDIR/sync.json";;   # no file: the list does not answer
  *'repos/x/y/issues/5002 -q'*) printf '{"message":"This issue was deleted","status":"410"}'; exit 1;;
  *projectsV2/4/fields*) printf 'FID4\n';;
  *projectsV2/4/items*) printf '5001\tDoing\thttps://api.github.com/repos/x/y\n';;
  *graphql*) printf '%s\n' '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[]}}}}';;
  *repos/x/y/branches*) :;;
  *repos/x/y/issues/5001*) printf 'open\t\tIssue 5001\t\t\n';;
  *repos/x/y/issues/5003*) printf 'open\t\tIssue 5003\t\t\n';;
esac
J
sed -i '' "s|TMPDIR|$T|g" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() {
  mkdir -p "$T/co/runs/$1"
  printf '%s\n' "$1" >> "$T/st/active-runs.txt"
  printf 'CHILD — GitHub issue(s) %s:\n' "$2" > "$T/co/runs/$1/request.txt"
  printf '%s\n' '{"event":"verdict","verdict":"failed","reasons":["red gate"],"exit_code":1}' > "$T/co/runs/$1/record.jsonl"
}
mk run-a 5001
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_BOARD_VOCAB= SG_FORK_DIR= SG_FORK_BASE= SG_HUB_DIR="$T/hub" JANITOR_DASHBOARD_MAX=0 "$@" python3 -I "$T/s/janitor.py" 2>&1; }
reads() { /usr/bin/grep -c "repos/x/y/issues/$1 -q" "$T/gh.calls" 2>/dev/null; }
lists() { /usr/bin/grep -c 'issues?state=all&since=' "$T/gh.calls" 2>/dev/null; }
fact() { python3 -c 'import json,sys; print((json.load(open(sys.argv[1]))["issues"].get(sys.argv[2]) or {}).get("f", ["none"])[0])' "$T/st/issue-facts.json" "$1" 2>/dev/null; }
F=$T/st/issue-facts.json

# A dry pass only prints: it writes no file.
run env JANITOR_DRY=1 > /dev/null
[ ! -e "$F" ] && ok "a dry pass writes no facts file" || ko "the dry pass wrote: $(cat "$F")"
: > "$T/gh.calls"

# The list does not answer: one read per issue, as before, and nothing is kept as a synced file.
run > /dev/null; run > /dev/null
[ "$(reads 5001)" = 2 ] && ok "a list that does not answer leaves one read per issue on every pass" || ko "reads with no list answer: $(reads 5001)"

# The list answers, with nothing changed: the first pass reads the issue itself and keeps it; the next pass reads nothing.
echo '[]' > "$T/sync.json"; : > "$T/gh.calls"
run > /dev/null
[ "$(reads 5001)" = 1 ] && [ "$(fact 5001)" = open ] && ok "an issue the file does not hold is read once and joins the file" || ko "first synced pass: reads $(reads 5001), fact '$(fact 5001)'"
run > /dev/null; run > /dev/null
[ "$(reads 5001)" = 1 ] && ok "an issue the file holds and the list did not name is not read again" || ko "reads after three synced passes: $(reads 5001)"
[ "$(lists)" = 3 ] && ok "each pass asks for the changed issues in one list read" || ko "list reads: $(lists)"
/usr/bin/grep 'issues?state=all&since=' "$T/gh.calls" | tail -1 | /usr/bin/grep -q 'since=20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]T' \
  && ok "the list read names the last sync as its since" || ko "since: $(/usr/bin/grep 'since=' "$T/gh.calls" | tail -1)"

# The list names a changed issue: its new facts are the file's, with no read of the issue itself.
echo '[{"number":5001,"state":"closed","state_reason":"completed","title":"Issue 5001","labels":[{"name":"bug"}],"closed_at":"2026-11-14T10:00:00Z"}]' > "$T/sync.json"
run > /dev/null
[ "$(reads 5001)" = 1 ] && [ "$(fact 5001)" = closed ] && ok "a changed issue takes its facts from the list, with no read of its own" || ko "changed issue: reads $(reads 5001), fact '$(fact 5001)'"

# An entry older than a day is read again, so a deleted issue is found. Issue 5001 closed above, and the janitor retires its run
# (CLOSED-RUN, #2138), so the aged entry belongs to a run that still holds an open issue.
mk run-c 5003
echo '[]' > "$T/sync.json"
run > /dev/null
python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["issues"]["5003"]["at"]=1.0; json.dump(d, open(p,"w"))' "$F"
run > /dev/null
[ "$(reads 5003)" = 2 ] && ok "an entry older than a day is read again" || ko "reads of an old entry: $(reads 5003)"

# GitHub's deleted message is an answer about the issue, never a fact to keep.
mk run-b 5002
run > /dev/null
[ "$(reads 5002)" -ge 1 ] && [ "$(fact 5002)" = none ] && ok "a deleted issue's message is never kept in the file" || ko "deleted issue: reads $(reads 5002), fact '$(fact 5002)'"

rm -rf "$T"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
exit $fail
