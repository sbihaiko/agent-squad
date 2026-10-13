#!/bin/bash
# Regression test for live-guard.sh: the step the watch cycle runs so the live skill -- the copy the loop and the owner's
# info bar actually run -- follows the repository copy, and the difference is read instead of assumed (issue #152).
# usage: bash test-live-guard.sh [path/to/live-guard.sh]
# Hermetic: every inherited SG_* is dropped, and the fixture is a throwaway checkout and a throwaway live skill.
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(dirname "$0"); src=${1:-$d/live-guard.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }

S=$T/scripts; co=$T/co; live=$T/home/.claude/skills/squad-goal; repo=$co/contrib/squad-goal
mkdir -p "$S" "$repo/scripts/profiles" "$live/scripts/profiles" "$T/st"
cp "$src" "$S/live-guard.sh"; cp "$d/publish-live.sh" "$S/publish-live.sh"
# The pass reads its checkout and its live skill from the profile, as every pass does: the fixture profile names both.
cat > "$S/env.sh" <<EOF
export SG_CHECKOUT=\${SG_CHECKOUT:-$co} SG_LIVE_DIR=\${SG_LIVE_DIR:-$live} SG_STATE=$T/st
EOF
# The repository copy and the live copy, a version apart.
printf 'skill v2\n' > "$repo/SKILL.md"; printf 'hub v2\n' > "$repo/scripts/hub.py"
printf 'skill v1\n' > "$live/SKILL.md"; printf 'hub v1\n' > "$live/scripts/hub.py"
printf '{"models": "mine"}\n' > "$live/scripts/models.json"     # machine config: no publish names it, none may touch it
printf 'mine\n' > "$live/scripts/profiles/mine.sh"              # a per-repo profile: machine config too
printf 'note\n' > "$live/scripts/local-note.txt"                # a live-only file no one named: left alone
pass() { bash "$S/live-guard.sh" 2>&1; }
check() { bash "$S/publish-live.sh" --check "$repo" "$live" 2>&1; }

# --- the pass reads the difference, publishes it and names what it read ---------------------------
pout=$(pass); rc=$?
[ "$rc" = 0 ] && ok "the pass exits 0: a drift never fails the cycle" || ko "the pass exited $rc: $pout"
case "$pout" in *LIVE-GUARD*"copy SKILL.md"*) ok "the pass reports the difference it read";; *) ko "no drift line: $pout";; esac
[ "$(cat "$live/SKILL.md")" = "skill v2" ] && ok "the pass publishes the shipped file" || ko "SKILL.md was not published"
[ "$(cat "$live/scripts/hub.py")" = "hub v2" ] && ok "the pass publishes the shipped script" || ko "hub.py was not published"

# --- after the publish the check reports no difference (the done condition, #152) -----------------
out=$(check); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] && ok "after the pass the publish reports no difference" || ko "still differs (exit $rc): $(printf '%s' "$out" | head -1)"
case "$pout" in *"now matches the repository copy"*) ok "the pass reports the copy in step";; *) ko "the pass did not report the copy in step: $pout";; esac

# --- the explicit file list stays the whole rule: machine config is never touched -----------------
[ "$(cat "$live/scripts/models.json")" = '{"models": "mine"}' ] && ok "the pass leaves the machine config (models.json) alone" || ko "models.json was overwritten: $(cat "$live/scripts/models.json")"
[ -f "$live/scripts/profiles/mine.sh" ] && ok "the pass leaves a per-repo profile alone" || ko "the per-repo profile was removed"
[ -f "$live/scripts/local-note.txt" ] && ok "the pass removes nothing it did not name" || ko "a file no one named was removed"

# --- the copies agree: the pass has nothing to read and says nothing ------------------------------
out=$(pass); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] && ok "no difference: the pass prints nothing" || ko "a quiet pass printed (exit $rc): $out"

# --- a difference the publish cannot resolve is a fact the pass cannot leave unread ---------------
printf 'skill v3\n' > "$repo/SKILL.md"
chmod 500 "$live"                                               # the publish cannot land: the destination refuses the write
out=$(pass); rc=$?
chmod 755 "$live"
[ "$rc" = 0 ] && ok "a publish that cannot land still exits 0" || ko "the pass exited $rc: $out"
case "$out" in *LIVE-GUARD*"copy SKILL.md"*) ok "the unresolved difference is read and named";; *) ko "the unresolved difference is silent: $out";; esac
case "$out" in *LIVE-GUARD*"could not resolve"*"copy SKILL.md"*) ok "the pass names the difference the publish left";; *) ko "the pass does not name what the publish left: $out";; esac

# --- a checkout that ships no copy of the skill: nothing to read, nothing to publish --------------
out=$(SG_CHECKOUT=$T/elsewhere bash "$S/live-guard.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] && ok "a checkout with no contrib/squad-goal: the pass says nothing" || ko "a foreign checkout spoke (exit $rc): $out"

rm -rf "$T"; [ $fail = 0 ] && echo "test-live-guard: all checks green" || { echo "test-live-guard: failed"; exit 1; }
