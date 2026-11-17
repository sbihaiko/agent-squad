#!/bin/bash
# A profile that still names the retired SG_TOOLING_REPO keeps working: env.sh maps it into SG_REPOS, prints ONE warning
# that names the profile, the setting and its replacement, and a real pass runs and reads its repository. The same profile
# with the setting removed runs with no warning. Drives the real env.sh, address.sh and branchreviewpass.sh against a stub
# gh and a local remote; no network. (#361: the pass driven here used to be autopilot.sh, which the pull-request path took
# with it; the branch passes are the loop's work now.)
# usage: bash test-retired-settings.sh
here=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; G=/usr/bin/grep; GIT=/usr/bin/git
ok() { printf 'ok   %s\n' "$1"; }; ko() { printf 'FAIL %s\n' "$1"; fail=1; }
S=$T/scene; mkdir -p "$S/s/profiles" "$S/bin" "$S/co"
cp "$here/env.sh" "$here/skiplabels.sh" "$here/address.sh" "$here/ghcall.sh" "$here/drainpolicy.py" "$here/ghread.py" "$here/ghthrottle.py" "$here/workbranches.sh" \
   "$here/branchreviewpass.sh" "$here/caps.sh" "$here/branchreview.sh" "$here/chainhealth.py" "$here/modelreg.py" "$here/registry.json" "$here/models.json" "$S/s/" 2>/dev/null
# the pass hands the repository to the review: the stub records that call, and keeps the verdict where branchland.sh reads it
cat > "$S/s/review.sh" <<J
#!/bin/bash
printf 'review.sh %s %s %s\n' "\$1" "\$3" "\$4" >> "$S/launched"
key=\$(printf '%s' "\$3" | tr / _)
echo "VERDICT: APPROVE" > "\$SG_STATE/review-\$key.md"
echo "model=stub"
J
chmod +x "$S/s/review.sh"
# a local remote with one work branch naming open issue 1512, and the open-issue list the pass reads through the door
( cd "$S/co" && echo x > f && $GIT init -q -b main . >/dev/null && $GIT add f && $GIT -c user.email=a@b -c user.name=t commit -q -m init \
  && $GIT branch -q dev && $GIT checkout -q -b gui/1512 && echo y >> f && $GIT -c user.email=a@b -c user.name=t commit -qam work )
$GIT clone -q --bare "$S/co" "$T/origin.git"
cat > "$S/bin/gh" <<J
#!/bin/bash
printf '%s\n' "gh \$*" >> "$S/ghlog"
case "\$*" in
  *"issues(states: OPEN"*) echo '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":1512,"title":"t","body":"Refs #0","state":"OPEN","labels":{"nodes":[]},"blockedBy":{"nodes":[]},"comments":{"nodes":[]}}]}}}}';;
  *"issues?state=open"*) echo '[{"number":1512,"title":"t","body":"Refs #0","labels":[]}]';;
  *commits/gui/1512*) echo '{"sha":"aaaa1111bbbb"}';;
  *) echo '{}';;
esac
J
chmod +x "$S/bin/gh"
common="SG_BASE=main SG_OWNER=x SG_PROJECT=4 SG_LAND_REMOTE=$T/origin.git SG_LAND_BRANCH=dev SG_GH_AGGREGATE_WINDOW=0 SG_GH_CACHE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001"
printf ': "${SG_REPO:=x/emu}"; SG_TOOLING_REPO=x/tool; SG_CHECKOUT=%s; %s\n' "$S/co" "$common" > "$S/s/profiles/retired.sh"
printf ': "${SG_REPO:=x/emu}"; SG_REPOS="x/emu x/tool"; SG_CHECKOUT=%s; %s\n' "$S/co" "$common" > "$S/s/profiles/migrated.sh"
# a review takes a review slot of the machine-wide budget (caps.sh, squad/caps.py, #567): this repository's module, a throwaway budget
export PATH="$S/bin:$PATH" SG_SQUAD=$(cd "$here/../../.." && pwd) SQUAD_CAPS_DIR="$T/caps" SQUAD_CAPS_POLL=0.2 SG_STATE_BASE="$S/state"
# one pass of the loop as profile $1: its output is dropped, its stderr is kept (env.sh's warning goes there)
cycle() { : > "$S/launched"; : > "$S/ghlog"; rm -rf "$S/state"
  ( cd "$S/co" && env -u SG_TOOLING_REPO -u SG_REPOS -u SG_REPO -u SG_STATE SG_PROFILES_DIR=$S/s/profiles SG_PROFILE=$1 bash -c '. "$1/env.sh"; exec bash "$1/branchreviewpass.sh"' _ "$S/s" >/dev/null 2>"$S/err-$1" )
  for i in $(seq 1 20); do $G -q '^review.sh' "$S/launched" 2>/dev/null && break; sleep 0.3; done; }
warned() { $G -c 'SG_TOOLING_REPO' "$S/err-$1" 2>/dev/null; }

# --- 1. a retired profile runs a full cycle, with one warning naming the profile, the setting and its replacement
cycle retired
$G -q '^review.sh x/emu gui/1512 1512$' "$S/launched" && ok "retired profile: the pass runs and reviews the branch in its repository" \
  || ko "retired profile: the pass did not run (launched: $(cat "$S/launched")) (stderr: $(cat "$S/err-retired"))"
[ "$(warned retired)" = 1 ] && ok "retired profile: exactly one warning" || ko "retired profile: warnings = $(warned retired): $(cat "$S/err-retired")"
w=$($G SG_TOOLING_REPO "$S/err-retired" | head -1)
case "$w" in *retired*SG_REPOS*) ;; *) w="";; esac
[ -n "$w" ] && printf '%s' "$w" | $G -q 'profile retired' && ok "the warning names the profile, the setting and its replacement" || ko "the warning is incomplete: $(cat "$S/err-retired")"
# a second source of env.sh in the same state directory stays quiet: one warning per cycle, not one per script
( cd "$S/co" && env -u SG_TOOLING_REPO -u SG_REPOS -u SG_REPO -u SG_STATE SG_PROFILES_DIR=$S/s/profiles SG_PROFILE=retired bash -c '. "$1/env.sh"; . "$1/env.sh"' _ "$S/s" 2>"$S/err-again" )
[ ! -s "$S/err-again" ] && ok "a later script of the same cycle is silent" || ko "the warning repeated: $(cat "$S/err-again")"

# --- nothing narrows what the profile works: its own repository and the retired one are both in the list
out=$(cd "$S/co" && env -u SG_TOOLING_REPO -u SG_REPOS -u SG_REPO -u SG_STATE SG_PROFILES_DIR=$S/s/profiles SG_PROFILE=retired bash -c '. "$1/env.sh" 2>/dev/null; sg_repos | tr "\n" " "; echo "|${SG_TOOLING_REPO:-unset}|$(sg_resolve_repo tool)"' _ "$S/s")
[ "$out" = "x/emu x/tool |unset|x/tool" ] && ok "the retired repository is one more entry of SG_REPOS, still resolvable, and the retired key is no longer read" \
  || ko "retired profile repositories: '$out'"

# --- 2. the same profile with the retired setting removed runs with no warning
cycle migrated
$G -q '^review.sh x/emu gui/1512 1512$' "$S/launched" && ok "migrated profile: the pass runs the same work" || ko "migrated profile: the pass did not run (launched: $(cat "$S/launched"))"
[ ! -s "$S/err-migrated" ] || ! $G -qi 'retired\|SG_TOOLING_REPO' "$S/err-migrated" && ok "migrated profile: no warning" || ko "migrated profile warned: $(cat "$S/err-migrated")"
out=$(cd "$S/co" && env -u SG_TOOLING_REPO -u SG_REPOS -u SG_REPO -u SG_STATE SG_PROFILES_DIR=$S/s/profiles SG_PROFILE=migrated bash -c '. "$1/env.sh" 2>/dev/null; sg_repos | tr "\n" " "' _ "$S/s")
[ "$out" = "x/emu x/tool " ] && ok "migrated profile: the same repositories" || ko "migrated profile repositories: '$out'"

# --- 3. a suite the loop runs inherits SG_PROFILES_DIR from env.sh (#664), and the nightly is such a suite (#746): the scene
# names its own profiles directory in every child, so the live copy never decides what it resolves.
SG_PROFILES_DIR=$T/nowhere cycle migrated
$G -q '^review.sh x/emu gui/1512 1512$' "$S/launched" && ok "an inherited SG_PROFILES_DIR does not decide the scene" \
  || ko "an inherited SG_PROFILES_DIR decided the scene: $(cat "$S/err-migrated")"

rm -rf "$T"; exit $fail
