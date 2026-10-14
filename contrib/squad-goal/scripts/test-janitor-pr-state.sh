#!/bin/bash
# Code Complete and Merging come from the pull request's state (#244). With SG_BOARD_VOCAB=shipped the janitor reads each
# open PR on the base from the open-PR list it already reads, and the PR's head from the loop's own review line
# (revci-<pr>.out): a conflict, a red check or no approval -> Code Complete; green, mergeable and unheld -> Merging. A PR that
# turns red keeps its card in Merging and the issue carries the pr:red badge; the badge goes when the PR is green again.
# No new read: one open-PR list, no per-PR read. Stubbed gh and board.sh; a temp state dir, the live board is never touched.
# usage: bash test-janitor-pr-state.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; here="$(cd "$(dirname "$src")" && pwd)"
cp "$src" "$T/s/janitor.py"; cp "$here/issue_state.py" "$here/board_columns.py" "$here/board-definition.json" "$T/s/"
cp "$here/ghcall.sh" "$here/ghread.py" "$here/ghthrottle.py" "$here/refusal.sh" "$here/address.sh" "$here/refusal-names.sh" "$T/s/"
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
  *is:merged*) echo 0;;
  *search/issues*) echo 1;;
  *pulls\?state=open*)
    printf '31 aaaaaaaaa1 main issue/2201 2099-01-01T00:00:00Z\t\t2201\n'
    printf '32 bbbbbbbbb2 main issue/2202 2099-01-01T00:00:00Z\tenhancement\t2202\n'
    printf '33 ccccccccc3 main issue/2203 2099-01-01T00:00:00Z\t\t2203\n'
    printf '34 ddddddddd4 main issue/2204 2099-01-01T00:00:00Z\t\t2204\n'
    printf '35 eeeeeeeee5 main issue/2205 2099-01-01T00:00:00Z\thold\t2205\n';;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*)
    printf '2201\tDoing\thttps://api.github.com/repos/x/y\n'; printf '2202\tCode Complete\thttps://api.github.com/repos/x/y\n'
    printf '2203\tMerging\thttps://api.github.com/repos/x/y\n'; printf '2204\tMerging\thttps://api.github.com/repos/x/y\n'
    printf '2205\tCode Complete\thttps://api.github.com/repos/x/y\n';;
  *graphql*)
    printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":['
    sep=""; for n in 2201 2202 2203 2204 2205; do
      printf '%s{"number":%s,"title":"Issue %s","body":"","state":"OPEN","labels":{"nodes":[]},"subIssuesSummary":{"total":0,"completed":0},"comments":{"nodes":[]}}' "$sep" "$n" "$n"; sep=,; done
    printf ']}}}}';;
  *issues/2204/labels*) echo '[]';;
  *issues/2203/labels*) echo '[]';;
  *issues/2204*) echo $'open\t\tIssue\tpr:red';;
  *issues/22*) echo $'open\t\tIssue\t';;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
echo '#31 aaaaaaaaa dirty CI=none APPROVE' > "$T/st/revci-31.out"                 # a conflict
echo '#32 bbbbbbbbb clean CI=success:3 APPROVE' > "$T/st/revci-32.out"            # green, mergeable, unheld
echo '#33 ccccccccc clean CI=failure:1,success:2 APPROVE' > "$T/st/revci-33.out"  # turned red after Merging
echo '#34 ddddddddd clean CI=success:3 APPROVE' > "$T/st/revci-34.out"            # green again, badge still on
echo '#35 eeeeeeeee clean CI=success:3 APPROVE' > "$T/st/revci-35.out"            # green, but held
o=$(SG_BOARD_VOCAB=shipped SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" python3 -I "$T/s/janitor.py" 2>&1)
bc() { cat "$T/board.calls" 2>/dev/null; }
has() { /usr/bin/grep -q "$1" "$T/board.calls" 2>/dev/null; }
has '^STATUS o 4 Code Complete 2201$' && ok "a conflicting PR puts the card in Code Complete" || ko "#2201 not Code Complete: $(bc) $o"
has '^STATUS o 4 Merging 2202$' && ok "a green, mergeable, unheld PR puts the card in Merging" || ko "#2202 not Merging: $(bc)"
! has ' 2203$' && ok "a PR that turned red keeps its card in Merging" || ko "#2203 moved back: $(bc)"
/usr/bin/grep -q 'issues/2203/labels.*pr:red' "$T/gh.calls" && ok "the red PR's card carries the pr:red badge" || ko "#2203 badge not written: $(cat "$T/gh.calls")"
! has ' 2204$' && ok "a green PR already in Merging is not written" || ko "#2204 rewritten: $(bc)"
/usr/bin/grep -q 'DELETE.*issues/2204/labels/pr:red\|issues/2204/labels/pr:red.*DELETE' "$T/gh.calls" && ok "the badge goes when the PR is green again" || ko "#2204 badge not cleared: $(cat "$T/gh.calls")"
! has ' 2205$' && ok "a held PR keeps its card in Code Complete" || ko "#2205 moved: $(bc)"
[ "$(/usr/bin/grep -c 'pulls?state=open' "$T/gh.calls")" = 1 ] && ok "one open-PR list read" || ko "open-PR list read $(/usr/bin/grep -c 'pulls?state=open' "$T/gh.calls") times"
! /usr/bin/grep -q 'pulls/3[1-5]' "$T/gh.calls" && ok "no per-PR read is added" || ko "a per-PR read: $(/usr/bin/grep 'pulls/3' "$T/gh.calls")"
[ "$fail" = 0 ] && echo "PASS" || { echo "FAILED"; exit 1; }
