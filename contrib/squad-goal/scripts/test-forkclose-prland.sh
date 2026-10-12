#!/bin/bash
# One route (#32): a fork branch lands by its pull request, never by a local commit on an unpushed branch. The local gate
# (the merge into the fork's base and the fork suite) still runs first as the fast path; then the branch is pushed to the
# remote the fork checkout names, a PR that names the issue is opened, and the land is that PR's merge on a green check.
# The issue closes only after the merge, and the fork's base then matches the remote. Stub gh (a fake GitHub that merges in
# the bare remote), throwaway repos. usage: bash test-forkclose-prland.sh [path/to/forkclose.sh]
src=${1:-$(dirname "$0")/forkclose.sh}; d=$(cd "$(dirname "$src")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$src" "$T/s/forkclose.sh"
for f in forkonly.sh address.sh prland.sh fork-gate.sh serial.sh ghread.py ghthrottle.py; do [ -f "$d/$f" ] && cp "$d/$f" "$T/s/"; done
printf "exit 0\n" > "$T/s/shell-suites.sh"; printf '#!/bin/bash\necho "pass-1"; exit 0\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/emu SG_REPOS="x/emu x/tool" SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_FORK_PY_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
O=$T/gh/x/tool.git; $G init -q --bare -b local/next "$O"   # a path ending in x/tool: the address seam resolves the checkout to x/tool
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init; g remote add origin "$O"; g push -q origin local/next
g checkout -q -b gui/1 local/next; echo one > "$T/fork/one.py"; g add .; g commit -q -m "work 1"; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
# the fake GitHub for x/tool: its merge endpoint merges in the bare remote, as GitHub would
cat > "$T/bin/gh" <<'J'
#!/bin/bash
echo "$*" >> "$GH_LOG"; R=$GH_REMOTE
case "$*" in
  *"--method POST repos/x/tool/pulls"*|*"-X POST repos/x/tool/pulls"*) touch "$GH_DIR/pr"; echo '{"number":7,"html_url":"https://github.com/x/tool/pull/7"}';;
  *"repos/x/tool/pulls?state=open"*) [ -e "$GH_DIR/pr" ] && [ ! -e "$GH_DIR/merged" ] && echo '[{"number":7,"head":{"ref":"gui/1"}}]' || echo '[]';;
  *"pulls?state=open"*) ;;
  *"repos/x/tool/commits/"*"check-runs"*) printf '{"total_count":1,"check_runs":[{"status":"%s","conclusion":%s}]}\n' "$(cat "$GH_DIR/ci")" "$( [ "$(cat "$GH_DIR/ci")" = completed ] && echo '"success"' || echo null)";;
  *"PUT repos/x/tool/pulls/7/merge"*)
    t=$(git --git-dir="$R" merge-tree --write-tree local/next gui/1) || exit 1
    c=$(git --git-dir="$R" -c user.email=g@h -c user.name=gh commit-tree "$t" -p local/next -p gui/1 -m "Merge gui/1 (Refs #1)")
    git --git-dir="$R" update-ref refs/heads/local/next "$c"; touch "$GH_DIR/merged"; echo '{"merged":true}';;
  *"issues/1/comments"*) echo "TDD RED";;
  *"-X PATCH"*"issues/1"*|*"--method PATCH"*"issues/1"*) touch "$GH_DIR/closed";;
  *"issues/"*" -q .state"*) echo open;;
  *"issues/1"*) echo '{"number":1,"state":"open","body":""}';;
  *"graphql"*) echo "[]";;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH" GH_LOG=$T/gh.log GH_DIR=$T GH_REMOTE=$O
echo in_progress > "$T/ci"
r=$(bash "$T/s/forkclose.sh" 2>&1)
[ -n "$($G --git-dir="$O" rev-parse -q --verify refs/heads/gui/1)" ] && ok "the branch is pushed to the fork checkout's remote" || ko "gui/1 is not on the remote (an unpushed local land): $r"
/usr/bin/grep -qE "POST repos/x/tool/pulls" "$T/gh.log" && /usr/bin/grep -q "head=gui/1" "$T/gh.log" && /usr/bin/grep -q "Refs #1" "$T/gh.log" && ok "a PR naming the issue is opened from gui/1" || ko "no PR opened for gui/1: $(tr '\n' ';' < "$T/gh.log" | cut -c1-600)"
[ ! -e "$T/closed" ] && ok "a pending check closes nothing" || ko "the issue closed with no merged PR: $r"
/usr/bin/grep -q "pulls/7/merge" "$T/gh.log" && ko "the PR was merged on a pending check" || ok "a pending check merges nothing"
echo completed > "$T/ci"
r=$(bash "$T/s/forkclose.sh" 2>&1)
/usr/bin/grep -q "PUT repos/x/tool/pulls/7/merge" "$T/gh.log" && ok "a green check merges the PR" || ko "the PR was not merged on a green check: $r"
[ -e "$T/closed" ] && ok "the issue closes after the PR merged" || ko "no close after the merge: $r"
[ "$(g rev-parse local/next)" = "$($G --git-dir="$O" rev-parse local/next)" ] && ok "the fork's base is the remote's base after the land" || ko "the fork's base diverges from the remote: local $(g rev-parse --short local/next), remote $($G --git-dir="$O" rev-parse --short local/next)"
[ -z "$(g status --porcelain)" ] && ok "checkout left clean" || ko "dirty checkout: $(g status --porcelain)"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
