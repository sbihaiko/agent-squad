#!/bin/bash
# A deleted issue is a terminal board-write refusal: report it once and stop writing it.
# Transient refusals remain defects on every pass. A finished run that names only deleted
# issues leaves active-runs.txt, because no future pass can reconcile it.
# usage: bash test-janitor-gone-issue.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }
ko() { echo "FAIL $1"; fail=1; }
here="$(cd "$(dirname "$src")" && pwd)"
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"
cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$(dirname "$src")/spec_fold.py" "$T/s/"
cp "$here/issue_state.py" "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
cat > "$T/s/board.sh" <<'J'
#!/bin/bash
n="${!#}"
printf '%s\n' "$*" >> "TMPDIR/board.calls"
gh api -X PATCH "repos/x/y/project-write/$n"
J
sed -i '' "s|TMPDIR|$T|" "$T/s/board.sh"; chmod +x "$T/s/board.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"; printf '%s\n' "$a" >> "TMPDIR/gh.calls"
case "$a" in
  *project-write/4660*) printf 'HTTP 410 Gone\n' >&2; exit 1;;
  *project-write/4661*) printf '#4661: HTTP 502 Bad Gateway (retry after 410 ms)\n' >&2; exit 1;;   # a 410 that is no status (#466 review)
  *'repos/x/y/issues/4662 -q'*) printf '{"message":"This issue was deleted","documentation_url":"https://docs.github.com/rest","status":"410"}'; exit 1;;   # gh writes the error body to stdout and ignores -q
  *projectsV2/4/fields*) printf 'FID4\n';;
  *projectsV2/4/items*) printf '4660\tCode Complete\thttps://api.github.com/repos/x/y\n4661\tCode Complete\thttps://api.github.com/repos/x/y\n';;
  *graphql*) printf '%s\n' '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":4660,"title":"Issue 4660","body":"","state":"OPEN","labels":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}},{"number":4661,"title":"Issue 4661","body":"","state":"OPEN","labels":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"subIssues":{"nodes":[]},"comments":{"nodes":[]}}]}}}}';;
  *repos/x/y/branches*) :;;
  *repos/x/y/issues/4660*) printf 'open\t\tIssue 4660\t\n';;
  *repos/x/y/issues/4661*) printf 'open\t\tIssue 4661\t\n';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() {
  mkdir -p "$T/co/runs/$1"
  printf '%s\n' "$1" >> "$T/st/active-runs.txt"
  printf 'CHILD — GitHub issue(s) %s:\n' "$2" > "$T/co/runs/$1/request.txt"
  printf '%s\n' '{"event":"verdict","verdict":"failed","reasons":["red gate"],"exit_code":1}' > "$T/co/runs/$1/record.jsonl"
}
mk run-write-gone 4660
mk run-write-retries 4661
mk run-finished-gone 4662
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_BOARD_VOCAB= SG_FORK_DIR= SG_FORK_BASE= SG_HUB_DIR="$T/hub" JANITOR_DASHBOARD_MAX=0 python3 -I "$T/s/janitor.py" 2>&1; }
o1=$(run)
o2=$(run)
[ "$(/usr/bin/grep -c 'project-write/4660' "$T/gh.calls")" = 1 ] && ok "410: the board write is sent once" || ko "410 writes: $(/usr/bin/grep 'project-write/4660' "$T/gh.calls")"
[ "$(/usr/bin/grep -c 'GONE-ISSUE #4660' "$T/st/cycle.log")" = 1 ] && ok "410: the deleted issue is reported once by name" || ko "410 reports: $(cat "$T/st/cycle.log")"
[ "$(/usr/bin/grep -c 'project-write/4661' "$T/gh.calls")" = 2 ] && ok "502: the board write is retried on each pass" || ko "502 writes: $(/usr/bin/grep 'project-write/4661' "$T/gh.calls")"
[ "$(/usr/bin/grep -c 'BOARD-WRITE-FAILED #4661' "$T/st/cycle.log")" = 2 ] && ok "502: each refusal is reported" || ko "502 reports: $(cat "$T/st/cycle.log")"
! /usr/bin/grep -qx 'run-finished-gone' "$T/st/active-runs.txt" && ok "a finished run whose issues are gone leaves active-runs" || ko "active runs: $(cat "$T/st/active-runs.txt")"
/usr/bin/grep -q -- 'repos/x/y/issues/4662 -q' "$T/gh.calls" && ok "the gone-run check reads the issue response" || ko "gone-run read: $(cat "$T/gh.calls")"
rm -rf "$T"
exit $fail
