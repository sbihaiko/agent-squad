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

# --- the main tree follows origin before the publish (issue #292) ---------------------------------
# The live copy is published from the main working tree, and nothing moved that tree to origin/main: it stayed at an old
# commit while later PRs merged. Before the publish the pass fast-forwards it (ff-only), and only a clean tree on its base.
# Each case is a bare origin, a clone that is the checkout, and a live skill a version behind. No network, no GitHub call.
G() { git -c user.name=t -c user.email=t@t "$@"; }
mk() { # mk NAME: bare origin $T/NAME.git, a clone at $T/NAME carrying contrib/squad-goal v1, a live skill at v1
  local n=$1; local w=$T/$n.work
  G init -q --bare -b main "$T/$n.git"; G clone -q "$T/$n.git" "$w" 2>/dev/null
  mkdir -p "$w/contrib/squad-goal/scripts"; printf 'skill v1\n' > "$w/contrib/squad-goal/SKILL.md"; printf 'hub\n' > "$w/contrib/squad-goal/scripts/hub.py"
  G -C "$w" add -A; G -C "$w" commit -qm v1; G -C "$w" push -q origin HEAD:main
  G clone -q -b main "$T/$n.git" "$T/$n" 2>/dev/null
  mkdir -p "$T/$n.live/scripts"; printf 'skill v1\n' > "$T/$n.live/SKILL.md"
  printf 'new file\n' > "$w/contrib/squad-goal/board-definition.json"; printf 'skill v2\n' > "$w/contrib/squad-goal/SKILL.md"
  G -C "$w" add -A; G -C "$w" commit -qm v2; G -C "$w" push -q origin HEAD:main     # origin is now a commit ahead of $T/$n
}
ffpass() { SG_CHECKOUT=$T/$1 SG_LIVE_DIR=$T/$1.live bash "$S/live-guard.sh" 2>&1; }

mk behind; head0=$(G -C "$T/behind" rev-parse HEAD)
out=$(ffpass behind)
[ "$(G -C "$T/behind" rev-parse HEAD)" = "$(G -C "$T/behind" rev-parse origin/main)" ] && [ "$(G -C "$T/behind" rev-parse HEAD)" != "$head0" ] && ok "a clean tree behind origin is fast-forwarded" || ko "the tree was not fast-forwarded: $out"
case "$out" in *LIVE-GUARD*"fast-forwarded"*) ok "the fast-forward is named";; *) ko "the fast-forward is silent: $out";; esac
[ "$(cat "$T/behind.live/SKILL.md")" = "skill v2" ] && [ -f "$T/behind.live/board-definition.json" ] && ok "the publish carries the files the fast-forward brought" || ko "the publish did not carry the new files: $out"

mk dirty; printf 'mine\n' >> "$T/dirty/contrib/squad-goal/SKILL.md"; h=$(G -C "$T/dirty" rev-parse HEAD)
out=$(ffpass dirty)
[ "$(G -C "$T/dirty" rev-parse HEAD)" = "$h" ] && [ "$(cat "$T/dirty/contrib/squad-goal/SKILL.md")" = "$(printf 'skill v1\nmine')" ] && ok "a dirty tree is left alone" || ko "the dirty tree was touched: $out"
case "$out" in *LIVE-GUARD*"not fast-forwarded"*dirty*) ok "the dirty tree is reported by name";; *) ko "the dirty tree is not reported: $out";; esac

mk other; G -C "$T/other" checkout -q -b topic; h=$(G -C "$T/other" rev-parse HEAD)
out=$(ffpass other)
[ "$(G -C "$T/other" rev-parse HEAD)" = "$h" ] && [ "$(G -C "$T/other" branch --show-current)" = topic ] && ok "a tree on another branch is left alone" || ko "the other-branch tree was touched: $out"
case "$out" in *LIVE-GUARD*"not fast-forwarded"*topic*) ok "the other branch is reported by name";; *) ko "the other branch is not reported: $out";; esac

mk diverged; printf 'local\n' > "$T/diverged/local.txt"; G -C "$T/diverged" add -A; G -C "$T/diverged" commit -qm local; h=$(G -C "$T/diverged" rev-parse HEAD)
out=$(ffpass diverged)
[ "$(G -C "$T/diverged" rev-parse HEAD)" = "$h" ] && ok "a diverged tree is left alone" || ko "the diverged tree was touched: $out"
case "$out" in *LIVE-GUARD*"not fast-forwarded"*"fast-forward"*) ok "the divergence is reported by name";; *) ko "the divergence is not reported: $out";; esac

# --- a checkout that ships no copy of the skill: nothing to read, nothing to publish --------------
out=$(SG_CHECKOUT=$T/elsewhere bash "$S/live-guard.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] && ok "a checkout with no contrib/squad-goal: the pass says nothing" || ko "a foreign checkout spoke (exit $rc): $out"

rm -rf "$T"; [ $fail = 0 ] && echo "test-live-guard: all checks green" || { echo "test-live-guard: failed"; exit 1; }
