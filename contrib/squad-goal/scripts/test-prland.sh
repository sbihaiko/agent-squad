#!/bin/bash
# Regression test for prland.sh (#83), the closer that merges a pull request on a failure set equal to the named
# baseline: an eligible head, a refusal that names the failure the baseline does not hold, one WAIT or REFUSE line per
# head, the merge -> comment -> close order, the ledger line and the watch cycle's slot. Every route and every write is
# served by a `gh` stub that records its argv; nothing here touches the network, a live repository, a board or a model.
# usage: bash test-prland.sh [path/to/prland.sh]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done   # hermetic: the live loop's SG_* never reach a case (#1410)
src=${1:-$(dirname "$0")/prland.sh}; D=$(cd "$(dirname "$src")" && pwd); T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
S1=1111111111111111111111111111111111111111; S2=2222222222222222222222222222222222222222
S3=3333333333333333333333333333333333333333; S4=4444444444444444444444444444444444444444
S5=5555555555555555555555555555555555555555
mkdir -p "$T/s" "$T/st" "$T/bin" "$T/co/runs/run-1" "$T/f"
cp "$src" "$T/s/prland.sh"; cp "$D/ghread.py" "$D/ghthrottle.py" "$D/ghcall.sh" "$T/s/"
printf 'export SG_STATE=%s SG_REPO=x/y SG_CHECKOUT=%s SG_BASE=main SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/co" > "$T/s/env.sh"
export PATH="$T/bin:$PATH" F="$T/f"
: > "$T/f/calls"; : > "$T/f/comment.body"
# The fixture baseline is the DEFAULT one (no SG_PR_BASELINE), so the case proves the path beside the script is the one read.
base2() { printf '# the named baseline of this case\n%s\n%s\n' 'tests/a_test.py::test_one' 'tests/a_test.py::test_two' > "$T/s/prland-baseline.txt"; }
pulls() { printf '{"data":{"repository":{"pullRequests":{"nodes":[{"number":7,"title":"Close the loop pull request","body":"Refs #83","isDraft":%s,"headRefName":"%s","headRefOid":"%s"}],"pageInfo":{"hasNextPage":false}}}}}' "$2" "$3" "$1" > "$T/f/pulls.json"; }
prone() { printf '{"head":{"sha":"%s"},"mergeable_state":"%s","merged":%s,"draft":false}' "$1" "$2" "${3:-false}" > "$T/f/pr.json"; }
checks() { printf '{"check_runs":[{"name":"check (3.11)","status":"%s","conclusion":"%s","details_url":"https://github.com/x/y/actions/runs/1/job/42"}]}' "$1" "$2" > "$T/f/checks.json"; }
flog() { : > "$T/f/log"; for t in "$@"; do printf '2026-10-13T11:43:22.7969964Z FAILED %s - AssertionError\n' "$t" >> "$T/f/log"; done; }
case_prep() { : > "$T/f/calls"; : > "$T/f/comment.body"; rm -f "$T/f/merge_refused" "$T/st/auto/prland-"*; }
run() { bash "$T/s/prland.sh" 2>&1; }
wrote() { /usr/bin/grep -q -- "$1" "$T/f/calls"; }
cat > "$T/bin/gh" <<'J'
#!/bin/bash
printf '%s\n' "${*//$'\n'/ }" >> "$F/calls"   # one line per call: the aggregate route's query document spans lines
case "$*" in *body=@-*) cat > "$F/comment.body";; esac
case "$*" in
  *graphql*|*"/pulls?state=open"*) cat "$F/pulls.json";;
  *"/check-runs"*) cat "$F/checks.json";;
  *"/actions/jobs/"*"/logs") cat "$F/log";;
  *"/git/refs/heads/"*) printf '{"ref":"refs/heads/x"}';;
  *"-X PUT "*"/merge"*) if [ -e "$F/merge_refused" ]; then printf '{"message":"Required status check is expected.","status":"405"}'; exit 1; fi
    printf '{"sha":"5555555555555555555555555555555555555555"}';;
  *"-X POST "*"/comments"*) printf '{"id":11}';;
  *"-X PATCH "*"/issues/"*) printf '{"number":83,"state":"closed"}';;
  *"/pulls/"*) cat "$F/pr.json";;
  *) printf '{}';;
esac
J
chmod +x "$T/bin/gh"

# 1) an eligible head whose failing tests are exactly the baseline is merged: squash via the pull's merge endpoint, the
# head branch deleted, then one comment on the issue, then the close -- in that order.
base2; pulls "$S1" false fix/83-closer; prone "$S1" clean; checks completed failure; flog tests/a_test.py::test_one tests/a_test.py::test_two; case_prep
r=$(run)
case "$r" in *"PRLAND MERGED #7 issue #83"*) ok "1 an eligible pull request is merged and its issue is named";; *) ko "1 no MERGED line: '$r'";; esac
case "$(cat "$T/f/calls")" in
  *"-X PUT repos/x/y/pulls/7/merge"*"-X DELETE repos/x/y/git/refs/heads/fix/83-closer"*"-X POST repos/x/y/issues/83/comments"*"-X PATCH repos/x/y/issues/83"*)
    ok "1 the merge, the branch delete, the comment and the close, in that order";;
  *) ko "1 wrong write order: $(tr '\n' '|' < "$T/f/calls")";; esac
wrote 'merge_method=squash' && ok "1 the merge is a squash" || ko "1 the merge is not a squash: $(tr '\n' '|' < "$T/f/calls")"
/usr/bin/grep -q '555555555' "$T/f/comment.body" && ok "1 one comment on the issue names the merge" || ko "1 the comment does not name the merge: '$(cat "$T/f/comment.body")'"

# 2) a failure the baseline does not hold refuses the merge, and the refusal names that failure
base2; flog tests/a_test.py::test_one tests/a_test.py::test_two tests/b_test.py::test_bad; case_prep
r=$(run)
case "$r" in *"PRLAND REFUSE #7"*"tests/b_test.py::test_bad"*) ok "2 an unexpected failure refuses the merge and is named";; *) ko "2 the unexpected failure was not refused by name: '$r'";; esac
wrote '\-X PUT' && ko "2 the head was merged anyway" || ok "2 nothing was written for a refused head"

# 3) one line per head, never repeated; a moved head speaks again
r=$(run)
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c '^PRLAND REFUSE')" = 0 ] && ok "3 the refusal is not repeated for the same head" || ko "3 the refusal repeated: '$r'"
pulls "$S2" false fix/83-closer; prone "$S2" clean; flog tests/a_test.py::test_one tests/a_test.py::test_two tests/b_test.py::test_bad
r=$(run)
case "$r" in *"PRLAND REFUSE #7"*"tests/b_test.py::test_bad"*) ok "3 a moved head is refused again, and named";; *) ko "3 the moved head said nothing: '$r'";; esac
checks running "" ; flog tests/a_test.py::test_one tests/a_test.py::test_two; pulls "$S3" false fix/83-closer; prone "$S3" clean
r=$(run); r2=$(run)
case "$r" in *"PRLAND WAIT #7"*) ok "3 a head whose check is still running waits";; *) ko "3 no WAIT for a running check: '$r'";; esac
[ "$(printf '%s\n' "$r2" | /usr/bin/grep -c '^PRLAND WAIT')" = 0 ] && ok "3 the wait is not repeated for the same head" || ko "3 the wait repeated: '$r2'"

# 4) an eligible head is not every head: a draft, a pull that names no issue, a head a live run owns, and a head GitHub
# does not report clean are each left alone (the last one named once, so the operator reads why)
pulls "$S4" true fix/83-closer; prone "$S4" clean; checks completed failure
r=$(run); [ -z "$r" ] && ok "4 a draft pull request is not merged" || ko "4 the draft was acted on: '$r'"
pulls "$S4" false fix/83-closer; sed -i.bak 's/Refs #83/no issue named/' "$T/f/pulls.json"; case_prep
r=$(run); [ -z "$r" ] && ok "4 a pull request naming no issue is not the loop's" || ko "4 a pull with no Refs was acted on: '$r'"
pulls "$S4" false fix/83-closer; case_prep
printf 'x\n' > "$T/co/runs/run-1/record.jsonl"; printf 'work on fix/83-closer\n' > "$T/co/runs/run-1/request.txt"
r=$(run); [ -z "$r" ] && ok "4 a head branch a live run names is not touched" || ko "4 a watched head was acted on: '$r'"
rm -rf "$T/co/runs/run-1"; prone "$S4" dirty; case_prep
r=$(run)
case "$r" in *"PRLAND WAIT #7"*"not clean"*) ok "4 a head GitHub reports dirty waits, and says so";; *) ko "4 no WAIT naming the state: '$r'";; esac

# 5) a merge GitHub refuses is a wait that names GitHub's own words, and it writes nothing else
pulls "$S5" false fix/83-closer; prone "$S5" clean; checks completed failure; flog tests/a_test.py::test_one tests/a_test.py::test_two
case_prep; : > "$T/f/merge_refused"
r=$(run)
case "$r" in *"PRLAND WAIT #7"*"Required status check"*) ok "5 a refused merge waits, naming GitHub's message";; *) ko "5 the refused merge was silent or unnamed: '$r'";; esac
wrote '\-X POST' && ko "5 the issue was commented although the merge was refused" || ok "5 a refused merge writes no comment and no close"

# 6) every call goes through the loop's one door and its throttle, and the script is counted in the ledger
[ -s "$T/st/gh-budget.json" ] && ok "6 the reads spent the shared token bucket (ghthrottle.py)" || ko "6 no token bucket: the reads did not go through the throttle"
/usr/bin/grep -qE '^[0-9T:Z-]+ (REST|GraphQL) .* prland$' "$T/st/api.log" 2>/dev/null && ok "6 the calls are logged under the pass's own name" || ko "6 no call-log line for the pass: '$(cat "$T/st/api.log" 2>/dev/null | head -3)'"
[ "$(/usr/bin/awk '{print $1}' "$T/f/calls" | sort -u)" = api ] && ok "6 no call site of its own: every reach is a gh api argv" || ko "6 a call site reached gh directly: $(/usr/bin/awk '{print $1}' "$T/f/calls" | sort -u | tr '\n' ' ')"
/usr/bin/grep -qx 'prland.sh 0' "$D/gh-direct-calls.txt" && ok "6 gh-direct-calls.txt counts prland.sh at 0" || ko "6 prland.sh is not counted in gh-direct-calls.txt"

# 7) the shipped baseline is a list of pytest node ids, and the watch cycle runs this step under ONE single.sh slot
bad=$(/usr/bin/grep -vE '^#|^$' "$D/prland-baseline.txt" | /usr/bin/grep -vcE '^tests/[A-Za-z0-9_/.]+\.py::[A-Za-z0-9_]+$')
[ "$bad" = 0 ] && [ "$(/usr/bin/grep -vcE '^#|^$' "$D/prland-baseline.txt")" -gt 0 ] && ok "7 the named baseline is a list of test node ids" || ko "7 the baseline holds $bad line(s) that are no test node id"
/usr/bin/grep -q 'single.sh" prland' "$D/watch.sh" && ok "7 watch.sh runs prland.sh under one single.sh slot" || ko "7 watch.sh does not run prland.sh under single.sh"

rm -rf "$T"; exit $fail
