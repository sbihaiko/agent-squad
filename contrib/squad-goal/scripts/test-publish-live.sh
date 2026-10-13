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
# The suites of a script the repository no longer ships leave with it (#210): a publish that drops the script and keeps
# its suite leaves the live copy running a suite that calls a file that is not there. The suite of the retired sync pair
# carries neither script's name, so the rule cannot be a name prefix alone.
suites=("test-fork""close-wait.sh" "test-sync-refresh.sh" "test-publish-landing.sh")
for s in "${suites[@]}"; do printf '#!/bin/bash\n' > "$live/scripts/$s"; done
shipped_suite=test-watch.sh                                    # the suite of a script the repository still ships
printf 'watch suite v1\n' > "$live/scripts/$shipped_suite"; printf 'watch suite v2\n' > "$repo/scripts/$shipped_suite"

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
for s in "${suites[@]}"; do
  printf '%s\n' "$out" | /usr/bin/grep -qx "drop scripts/$s" && ok "--check names the retired script's suite it would drop: $s" || ko "drop line missing for $s: $out"
done
printf '%s\n' "$out" | /usr/bin/grep -qx "drop scripts/$shipped_suite" && ko "--check would drop the suite of a script the repository still ships: $shipped_suite" || ok "--check keeps the suite of a shipped script: $shipped_suite"

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
[ -f "$live/scripts/local-note.txt" ] && ok "publish removes nothing it did not name" || ko "a file no one named was removed"
for s in "${suites[@]}"; do
  [ ! -e "$live/scripts/$s" ] && ok "publish dropped the retired script's suite: $s" || ko "$s survived the publish"
  printf '%s\n' "$out" | /usr/bin/grep -qx "publish-live: dropped scripts/$s" && ok "publish reports the suite it dropped: $s" || ko "no report for $s: $out"
done
[ "$(cat "$live/scripts/$shipped_suite")" = "watch suite v2" ] && ok "publish keeps the suite of a script the repository still ships: $shipped_suite" || ko "the shipped suite was dropped or left behind: $(cat "$live/scripts/$shipped_suite" 2>&1)"

# --- a second publish finds nothing to do --------------------------------------------------------
out=$(bash "$src" --check "$repo" "$live" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "the second --check finds nothing to do" || ko "second --check still differs: $out"
[ -z "$out" ] && ok "the second --check prints nothing" || ko "second --check printed: $out"

rm -rf "$T"; [ $fail = 0 ] && echo "test-publish-live: all checks green" || { echo "test-publish-live: failed"; exit 1; }
