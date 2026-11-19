#!/bin/bash
# seconds: 1.94
# The promotion branch is a profile setting (#1646): a repository that promotes `dev` into `staging` names it with
# SG_STG_BRANCH, and the pass reads it everywhere -- the fetch, the ancestry read, the open-pull-request lookup, the pull
# request's base and its title. With no setting the branch is `stg`, and SG_PROMOTE=off makes the pass exit 0 before any
# fetch, so a profile that delivers by another path grows no log line.
# The fixture is a bare remote with `dev` and `staging` and no `stg`, a stub GitHub whose merge moves `staging`, and a stub
# CI seam that answers green. usage: bash test-promote-branch.sh
unset SG_SERIAL_HELD SG_CHILD_SESSION
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; R=$T/remote.git
# SQUAD_CAPS_DIR is a throwaway: the promotion takes the machine-wide gate budget, and a box whose slots other gates hold would
# refuse this suite's promotions with no defect to find, as test-promote.sh says (#1171).
export SG_STATE=$T/state SG_PROMOTE_REMOTE=$R SG_REPO=x/emu GIT_TERMINAL_PROMPT=0 SG_PROMOTE_PROOF=ci SQUAD_CAPS_DIR=$T/caps; mkdir -p "$SG_STATE"
git init -q --bare "$R"; git init -q "$T/w"; W="$G -C $T/w"
$W checkout -q -b main; $W commit -q --allow-empty -m base; $W remote add origin "$R"
for b in dev staging; do $W push -q origin "main:refs/heads/$b"; done
tip() { git -C "$R" rev-parse "refs/heads/$1" 2>/dev/null || echo missing; }
adv() { $W fetch -q origin; $W checkout -q -B "$1" "origin/$1"; $W commit -q --allow-empty -m "$2"; $W push -q origin "$1"; }
# The CI seam: green, and it logs the read so a test proves the promotion reached the proof.
printf '#!/bin/bash\necho "ci read" >> "%s/ci.log"\necho green\n' "$T" > "$T/ci"; chmod +x "$T/ci"; export SG_CI_STATE_CMD=$T/ci
# The check-run read (#1644): a green CI word is half the proof, so the pass also asks for the complete tier's jobs on the
# tip. The answer names every job the workflow starts on the `promote` label, each completed and successful, read from the
# same workflow file the pass reads: a fixture that answers only the seam leaves the suite reading the real GitHub.
cat > "$T/ciruns" <<'RUN'
#!/bin/bash
n=$(bash "$DF/promote-pass.sh" --ci-complete-job-names "$WF" 2>/dev/null); first=1
printf '{"check_runs":['
while IFS= read -r j; do [ -n "$j" ] || continue; [ "$first" = 1 ] || printf ','; first=0
  printf '{"name":"%s","status":"completed","conclusion":"success","id":1}' "$j"; done <<<"$n"
printf ']}\n'
RUN
chmod +x "$T/ciruns"; export DF="$d" WF="$d/../../../.github/workflows/ci.yml"
# The stub GitHub: one open pull request whose head and base the pass names, a merge that moves `staging`, and the tip's
# check runs.
mkdir -p "$T/bin"
cat > "$T/bin/gh" <<EOF
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *check-runs*) "$T/ciruns";;
  *graphql*) if [ -e "$T/pr" ]; then printf '{"data":{"repository":{"pullRequests":{"nodes":[{"number":%s,"headRefName":"dev","baseRefName":"staging","isDraft":false,"labels":{"nodes":[]}}],"pageInfo":{"hasNextPage":false}}}}}\n' "\$(cat "$T/pr")"; else echo '{"data":{"repository":{"pullRequests":{"nodes":[],"pageInfo":{"hasNextPage":false}}}}}'; fi;;
  *"-X POST repos/x/emu/pulls"*) echo 9 > "$T/pr"; echo '{"number":9}';;
  *"-X PUT repos/x/emu/pulls/"*"/merge"*) if git -C "$T/w" fetch -q origin && git -C "$T/w" push -q origin origin/dev:refs/heads/staging; then rm -f "$T/pr"; echo '{"merged":true,"message":"Pull Request successfully merged"}'; else echo '{"merged":false,"message":"Pull Request is not mergeable"}'; fi;;
  *) echo '{}';;
esac
EOF
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"

# One: SG_STG_BRANCH names the branch, and the pull request's base and title carry it.
adv dev one; rm -f "$T/pr" "$T/gh.log" "$T/ci.log"
out=$(SG_STG_BRANCH=staging bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && ok "a promotion branch that is not stg promotes (rc 0)" || ko "staging promotion: rc $rc, out '$out'"
[ "$(tip staging)" = "$(tip dev)" ] && ok "the dev tip reaches the named branch" || ko "staging did not move: $(tip staging), dev $(tip dev)"
[ -s "$T/ci.log" ] && ok "the pass reaches the proof before the merge" || ko "the proof was never read"
grep -q 'base=staging' "$T/gh.log" && ok "the pull request is opened against staging" || ko "the pull request base was not staging: $(grep -o 'base=[^ ]*' "$T/gh.log" | tr '\n' ' ')"
grep -q 'title=Promote dev into staging' "$T/gh.log" && ok "the pull request title names the branch" || ko "the title does not name the branch: $(grep -o 'title=[^&]*' "$T/gh.log" | tr '\n' ' ')"
grep -qE 'refs/heads/stg(:|\b)' "$T/gh.log" && ko "the promotion still read stg" || ok "no read names stg when the profile names staging"

# Two: no setting means stg, and a repository with no stg branch promotes nothing.
adv dev two; before=$(tip staging)
out=$(bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc -ne 0 ] && [ "$(tip staging)" = "$before" ] && ok "with no setting the pass reads stg and refuses a repository without it" || ko "default branch: rc $rc, staging $(tip staging), out '$out'"
case "$out" in *stg*) true;; *) false;; esac && ok "the refusal names stg" || ko "the refusal does not name stg: '$out'"

# Three: SG_PROMOTE=off exits 0 before any fetch and writes no line.
adv dev three; before=$(tip staging); rm -f "$T/gh.log" "$T/ci.log"
out=$(SG_PROMOTE=off SG_STG_BRANCH=staging bash "$d/promote-stg.sh" 2>&1); rc=$?
[ $rc = 0 ] && ok "a profile that promotes nothing exits 0" || ko "promotion off: rc $rc, out '$out'"
[ "$(tip staging)" = "$before" ] && ok "promotion off moves nothing" || ko "promotion off moved staging"
[ ! -s "$T/gh.log" ] && [ ! -s "$T/ci.log" ] && ok "promotion off reads no evidence" || ko "promotion off still called gh or the proof"

# Four: no script names the work branch, the promotion branch or the main branch as a literal.
for f in promote-stg.sh promote-pass.sh promote-main.sh promote-main-pass.sh; do
  bad=$(grep -nE 'refs/heads/(dev|stg|main)\b|origin/(dev|stg|main)\b|base=(dev|stg|main)\b|head=(dev|stg)\b' "$d/$f" | grep -v '^[0-9]*:#' | head -3)
  [ -z "$bad" ] && ok "$f names no branch as a literal" || ko "$f still names a branch: $bad"
done

# Five: the main branch is a profile setting too. SG_MAIN_BRANCH names it, and promote-main.sh moves that branch to the
# promotion branch the profile names.
$W push -q origin "main:refs/heads/trunk"; adv staging five
out=$(SG_MAIN_BRANCH=trunk SG_STG_BRANCH=staging bash "$d/promote-main.sh" 2>&1); rc=$?
[ $rc = 0 ] && [ "$(tip trunk)" = "$(tip staging)" ] && ok "a main branch that is not main is moved to the named promotion branch" || ko "trunk: rc $rc, trunk $(tip trunk), staging $(tip staging), out '$out'"
! git -C "$R" show-ref --verify -q refs/heads/main && ok "no branch named main is written when the profile names another" || ko "a main branch appeared: $(tip main)"

[ $fail = 0 ] && echo "PASS" || echo "TDD RED"
exit $fail
