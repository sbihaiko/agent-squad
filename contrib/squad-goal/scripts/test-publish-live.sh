#!/bin/bash
# Regression test for publish-live.sh: the live skill follows the repository as an explicit FILE LIST, never a mirror,
# and the fork-only route the repository retired is dropped from the live copy by name (#134).
# usage: bash test-publish-live.sh [path/to/publish-live.sh]
src=${1:-$(dirname "$0")/publish-live.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }

repo="$T/repo/contrib/squad-goal"; live="$T/home/.claude/skills/squad-goal"
mkdir -p "$repo/scripts/profiles" "$live/scripts/profiles"
printf 'skill v2\n' > "$repo/SKILL.md"; printf 'watch v2\n' > "$repo/scripts/watch.sh"
printf '{"models": 2}\n' > "$repo/scripts/models.json"; printf 'example\n' > "$repo/scripts/profiles/example.sh"
printf 'skill v1\n' > "$live/SKILL.md"; printf 'watch v1\n' > "$live/scripts/watch.sh"
printf '{"models": "mine"}\n' > "$live/scripts/models.json"      # machine config: never shipped, never dropped
printf 'mine\n' > "$live/scripts/profiles/mine.sh"              # a per-repo profile: machine config
printf 'note\n' > "$live/scripts/local-note.txt"                # a live-only file no one named: left alone
# The fork-only route #122 retired; #134 names it for the live copy.
retired=("fork""close.sh" "fork""-gate.sh" "publish-landing.sh" "sync-to-""fork.sh" "refresh-from-""fork.sh" \
         "fork""only.sh" "forkrescue.py" "forkstrand.py")
for n in "${retired[@]}"; do printf '#!/bin/bash\n' > "$live/scripts/$n"; done
# The suites of the retired route stayed behind when their scripts were dropped, and every one of them failed on a call to
# a file that is not there (#210). A retired name takes its suites with it; two of them do not carry their subject in their
# name. A suite the repository ships stays.
mysuites=(test-forkclose-wait.sh test-forkbranch-source.sh test-sync-refresh.sh)
for s in "${mysuites[@]}"; do printf '#!/bin/bash\n' > "$live/scripts/$s"; done
# The pull-request route #361 retired: the live copy held its scripts, their suites and its data file, and the publish named
# none of them, so they stayed forever. A retired name takes its suites with it; three do not carry their subject in their name.
prretired=(autofix.sh autopanel.sh autopilot.sh prland.sh retryfix.sh revci.sh speed.py)
prsuites=(test-autofix.sh test-autofix-rounds.sh test-autopilot-debt.sh test-autopilot-gate.sh test-autopilot-hold.sh \
          test-autopilot-leash.sh test-autopilot-norefs.sh test-autopilot-stale-launch.sh test-autopilot-two-repos.sh \
          test-autofrontier-merged.sh test-janitor-g.sh test-janitor-kmn.sh test-prland.sh test-revci-notice.sh)
prdata=(ci-baseline.txt)
for n in "${prretired[@]}"; do printf '#!/bin/bash\n' > "$live/scripts/$n"; done
for s in "${prsuites[@]}"; do printf '#!/bin/bash\n' > "$live/scripts/$s"; done
for n in "${prdata[@]}"; do printf 'baseline\n' > "$live/$n"; done
printf 'mine\n' > "$live/scripts/profiles/agent-squad.sh"   # machine config: never shipped, never dropped
printf '#!/bin/bash\n' > "$repo/scripts/test-watch-ok.sh"

# --- --check changes nothing and names every file it would touch ---------------------------------
before=$(cd "$live" && find . -type f | LC_ALL=C sort | while read -r f; do printf '%s %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done)
out=$(bash "$src" --check "$repo" "$live" 2>&1); rc=$?
after=$(cd "$live" && find . -type f | LC_ALL=C sort | while read -r f; do printf '%s %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done)
[ "$rc" = 1 ] && ok "--check exits 1 while the live skill differs" || ko "--check exit (got $rc)"
[ "$before" = "$after" ] && ok "--check changes nothing" || ko "--check wrote to the live skill"
printf '%s\n' "$out" | /usr/bin/grep -qx 'copy SKILL.md' && ok "--check names the file it would copy" || ko "copy line missing: $out"
for n in "${retired[@]}"; do
  printf '%s\n' "$out" | /usr/bin/grep -qx "drop scripts/$n" && ok "--check names the retired name it would drop: $n" || ko "drop line missing for $n: $out"
done
for s in "${mysuites[@]}"; do
  printf '%s\n' "$out" | /usr/bin/grep -qx "drop scripts/$s" && ok "--check names the suite of the retired route it would drop: $s" || ko "drop line missing for $s: $out"
done
for n in "${prretired[@]}"; do
  printf '%s\n' "$out" | /usr/bin/grep -qx "drop scripts/$n" && ok "--check names the retired pull-request script it would drop: $n" || ko "drop line missing for $n: $out"
done
for s in "${prsuites[@]}"; do
  printf '%s\n' "$out" | /usr/bin/grep -qx "drop scripts/$s" && ok "--check names the retired pull-request suite it would drop: $s" || ko "drop line missing for $s: $out"
done
for n in "${prdata[@]}"; do
  printf '%s\n' "$out" | /usr/bin/grep -qx "drop $n" && ok "--check names the retired data file it would drop: $n" || ko "drop line missing for $n: $out"
done

# --- a publish drops each retired name by name, never the whole directory ------------------------
out=$(bash "$src" "$repo" "$live" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "a publish exits 0" || ko "publish exit (got $rc)"
[ "$(cat "$live/SKILL.md")" = "skill v2" ] && ok "publish copies the shipped SKILL.md" || ko "SKILL.md not copied"
[ "$(cat "$live/scripts/watch.sh")" = "watch v2" ] && ok "publish copies the shipped script" || ko "watch.sh not copied"
for n in "${retired[@]}"; do
  [ ! -e "$live/scripts/$n" ] && ok "publish dropped the retired name: $n" || ko "$n survived the publish"
done
[ "$(cat "$live/scripts/models.json")" = '{"models": "mine"}' ] && ok "publish leaves the machine config (models.json) alone" || ko "models.json overwritten: $(cat "$live/scripts/models.json")"
[ -f "$live/scripts/profiles/mine.sh" ] && ok "publish leaves a per-repo profile alone" || ko "the per-repo profile was removed"
[ -f "$live/scripts/profiles/example.sh" ] && ok "publish ships the template profile" || ko "example.sh not shipped"
for s in "${mysuites[@]}"; do
  [ ! -e "$live/scripts/$s" ] && ok "publish dropped the suite of the retired route: $s" || ko "$s survived the publish"
done
for n in "${prretired[@]}"; do
  [ ! -e "$live/scripts/$n" ] && ok "publish dropped the retired pull-request script: $n" || ko "$n survived the publish"
done
for s in "${prsuites[@]}"; do
  [ ! -e "$live/scripts/$s" ] && ok "publish dropped the retired pull-request suite: $s" || ko "$s survived the publish"
done
for n in "${prdata[@]}"; do
  [ ! -e "$live/$n" ] && ok "publish dropped the retired data file: $n" || ko "$n survived the publish"
done
[ -f "$live/scripts/profiles/agent-squad.sh" ] && ok "publish leaves a per-repo profile alone (agent-squad.sh)" || ko "agent-squad.sh was removed"
[ -f "$live/scripts/test-watch-ok.sh" ] && ok "publish keeps the suite of a script it ships" || ko "the shipped suite was removed"
[ -f "$live/scripts/local-note.txt" ] && ok "publish removes nothing it did not name" || ko "a file no one named was removed"

# --- a second publish finds nothing to do --------------------------------------------------------
out=$(bash "$src" --check "$repo" "$live" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "the second --check finds nothing to do" || ko "second --check still differs: $out"
[ -z "$out" ] && ok "the second --check prints nothing" || ko "second --check printed: $out"

# --- the source must be in SG_SQUAD's history (#605) ----------------------------------------------
# The live scripts import squad from SG_SQUAD. A publish from a tree ahead of SG_SQUAD ships scripts that call a squad
# command SG_SQUAD lacks, so it is refused: nonzero, nothing written, one line naming both commits.
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t GIT_CONFIG_GLOBAL=/dev/null
sq="$T/sq"; wt="$T/wt"; mkdir -p "$sq/contrib/squad-goal/scripts"
printf 'skill A\n' > "$sq/contrib/squad-goal/SKILL.md"; printf 'watch A\n' > "$sq/contrib/squad-goal/scripts/watch.sh"
git -C "$sq" init -q -b main && git -C "$sq" add -A && git -C "$sq" commit -qm A
# The ahead tree is a clone, a main checkout of its own: a linked worktree is refused before this check (#658).
git clone -q "$sq" "$wt" && git -C "$wt" checkout -q -b ahead
printf 'skill B\n' > "$wt/contrib/squad-goal/SKILL.md"; git -C "$wt" commit -qam B
a=$(git -C "$sq" rev-parse --short HEAD); b=$(git -C "$wt" rev-parse --short HEAD)
snap() { (cd "$1" && find . -type f | LC_ALL=C sort | while read -r f; do printf '%s %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done); }
live2="$T/live2"; mkdir -p "$live2/scripts"; printf 'skill v0\n' > "$live2/SKILL.md"
before=$(snap "$live2")
out=$(SG_SQUAD="$sq" bash "$src" "$wt/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" != 0 ] && ok "a source ahead of SG_SQUAD is refused" || ko "a source ahead of SG_SQUAD published (rc $rc): $out"
[ "$before" = "$(snap "$live2")" ] && ok "a refused publish writes nothing" || ko "a refused publish wrote to the live skill"
[ "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" = 1 ] && ok "the refusal is one line" || ko "the refusal is not one line: $out"
printf '%s' "$out" | /usr/bin/grep -q "$a" && printf '%s' "$out" | /usr/bin/grep -q "$b" && printf '%s' "$out" | /usr/bin/grep -qi 'fast-forward SG_SQUAD' \
  && ok "the refusal names both commits and the fast-forward" || ko "the refusal line: $out"
out=$(SG_SQUAD="$sq" bash "$src" "$sq/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$live2/SKILL.md")" = "skill A" ] && ok "SG_SQUAD as the source publishes" || ko "SG_SQUAD as the source (rc $rc): $out"
git -C "$sq" fetch -q "$wt" ahead && git -C "$sq" merge -q --ff-only FETCH_HEAD; printf 'skill v0\n' > "$live2/SKILL.md"
git -C "$wt" checkout -q --detach HEAD~1
out=$(SG_SQUAD="$sq" bash "$src" "$wt/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$live2/SKILL.md")" = "skill A" ] && ok "a source behind SG_SQUAD publishes" || ko "a source behind SG_SQUAD (rc $rc): $out"
git -C "$wt" checkout -q ahead; printf 'skill v0\n' > "$live2/SKILL.md"
out=$(SG_SQUAD="$sq" bash "$src" "$wt/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$live2/SKILL.md")" = "skill B" ] && ok "a source equal to SG_SQUAD publishes" || ko "a source equal to SG_SQUAD (rc $rc): $out"
printf 'skill v0\n' > "$live2/SKILL.md"
out=$(env -u SG_SQUAD bash "$src" "$wt/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$live2/SKILL.md")" = "skill B" ] && ok "no SG_SQUAD publishes as before" || ko "no SG_SQUAD (rc $rc): $out"

# --- a linked worktree is never the source (#658) -------------------------------------------------
# A coding child published its unlanded branch from its own worktree. A publish from a linked worktree is refused: nonzero,
# nothing written, one line naming the tree. --check writes nothing, so it still runs.
lw="$T/linked"; git -C "$sq" worktree add -q -b child "$lw" main
printf 'skill child\n' > "$lw/contrib/squad-goal/SKILL.md"
printf 'skill v0\n' > "$live2/SKILL.md"; before=$(snap "$live2")
for env in "env -u SG_SQUAD" "env SG_SQUAD=$sq"; do
  out=$($env bash "$src" "$lw/contrib/squad-goal" "$live2" 2>&1); rc=$?
  [ "$rc" != 0 ] && ok "a publish from a linked worktree is refused ($env)" || ko "a linked worktree published (rc $rc): $out"
  [ "$before" = "$(snap "$live2")" ] && ok "a refused worktree publish writes nothing ($env)" || ko "a refused worktree publish wrote ($env)"
  printf '%s' "$out" | /usr/bin/grep -q "$(cd "$lw" && pwd -P)" && printf '%s' "$out" | /usr/bin/grep -qi 'worktree' \
    && ok "the refusal names the worktree ($env)" || ko "the worktree refusal line: $out"
done
out=$(env -u SG_SQUAD bash "$src" --check "$lw/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$out" | /usr/bin/grep -qx 'copy SKILL.md' && [ "$before" = "$(snap "$live2")" ] \
  && ok "--check from a linked worktree still reports and writes nothing" || ko "--check from a linked worktree (rc $rc): $out"

rm -rf "$T"; [ $fail = 0 ] && echo "test-publish-live: all checks green" || { echo "test-publish-live: failed"; exit 1; }
