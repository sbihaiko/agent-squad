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

rm -rf "$T"; [ $fail = 0 ] && echo "test-publish-live: all checks green" || { echo "test-publish-live: failed"; exit 1; }
