#!/bin/bash
# Regression test for live-guard.sh: the step the watch cycle runs so the live skill -- the copy the loop and the owner's
# info bar actually run -- follows the repository copy, and the difference is read instead of assumed (issue #152).
# usage: bash test-live-guard.sh [path/to/live-guard.sh]
# Hermetic: every inherited SG_* is dropped, and the fixture is a throwaway checkout and a throwaway live skill.
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(dirname "$0"); src=${1:-$d/live-guard.sh}; T=$(mktemp -d); . "$d/driver-tree.sh"; trap 'driver_tree_kill "$T"; rm -rf "$T"' EXIT; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }

S=$T/scripts; co=$T/co; live=$T/home/.claude/skills/squad-goal; repo=$co/contrib/squad-goal
mkdir -p "$S" "$repo/scripts/profiles" "$live/scripts/profiles" "$T/st"
cp "$src" "$S/live-guard.sh"; cp "$d/publish-live.sh" "$S/publish-live.sh"; cp "$d/ghcall.sh" "$d/ghthrottle.py" "$d/ghread.py" "$d/drainpolicy.py" "$d/modelreg.py" "$d/registry.json" "$S/"
# The pass reads its checkout and its live skill from the profile, as every pass does: the fixture profile names both.
cat > "$S/env.sh" <<EOF
export SG_CHECKOUT=\${SG_CHECKOUT:-$co} SG_LIVE_DIR=\${SG_LIVE_DIR:-$live} SG_STATE=\${SG_STATE:-$T/st}
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
[ -e "$T/st/live-publish.stamp" ] && ok "a publish leaves its stamp, so the janitor reads the change as the publish's own (#659)" || ko "no publish stamp in the state dir"

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

# --- live models.json is owner config: a hand edit is named once, never compared each cycle (#700) ----------
# The per-cycle review chain compare named the same difference on most cycles. The guard now keeps the digest of the
# live models.json and names a change once; a repository models.json that differs is never a line.
cp "$repo/SKILL.md" "$live/SKILL.md"; cp "$repo/scripts/hub.py" "$live/scripts/hub.py"
printf '%s\n' '{"review_models":["claude:claude-opus-5-5","claude:claude-sonnet-5-5"]}' > "$repo/scripts/models.json"
out=$(pass)
case "$out" in *"review chain"*) ko "a repository models.json that differs is still compared: $out";; *) ok "a repository models.json that differs is not compared";; esac
out=$(pass); [ -z "$out" ] && ok "two cycles with no change print nothing" || ko "a quiet cycle printed: $out"
printf '%s\n' '{"review_models":["claude:claude-opus-5-5"]}' > "$live/scripts/models.json"
out=$(pass); dg=$(shasum -a 256 "$live/scripts/models.json" | cut -c1-12)
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c 'LIVE-GUARD live models.json changed by hand')" = 1 ] && ok "a hand edit to live models.json is one line" || ko "the hand edit is not one line: $out"
case "$out" in *"changed by hand: $dg"*) ok "the line names the new digest";; *) ko "the line does not name digest $dg: $out";; esac
[ "$(jq -c .review_models "$live/scripts/models.json")" = '["claude:claude-opus-5-5"]' ] && ok "the pass only reads live models.json, never writes it" || ko "the live models.json was rewritten"
out=$(pass); [ -z "$out" ] && ok "the same hand edit is not named again" || ko "the same hand edit printed again: $out"
printf '%s\n' '{"review_models":["grok-4.6"]}' > "$live/scripts/models.json"
out=$(pass)
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c 'changed by hand')" = 1 ] && ok "the next hand edit is named once" || ko "the next hand edit: $out"
rm -f "$repo/scripts/models.json"; printf '{"models": "mine"}\n' > "$live/scripts/models.json"; pass >/dev/null

# --- a pass that forces a real publish still compares no chain (#1261) -------------------------------------
# The first cycle above is quiet; here a shipped file changes, so the pass publishes, and the repository chains differ.
cp "$d/models_chains.py" "$S/models_chains.py"
printf '{"schema_version":2,"chains":{"router":[{"id":"new"}]}}\n' > "$repo/scripts/models.json"
printf '{"schema_version":2,"chains":{"router":[{"id":"old"}]}}\n' > "$live/scripts/models.json"; pass >/dev/null   # the hand edit is named here
printf 'skill v9\n' > "$repo/SKILL.md"
out=$(pass)
case "$out" in *"copy SKILL.md"*) ok "the changed file is published in the pass";; *) ko "no publish happened: $out";; esac
case "$out" in *router*|*"head"*|*" new"*) ko "a publish in the pass compared chains: $out";; *) ok "a publish in the pass compares no chain";; esac
rm -f "$repo/scripts/models.json"; printf '{"models": "mine"}\n' > "$live/scripts/models.json"; pass >/dev/null

# --- a stray .bak-* copy in live scripts/ is clutter, listed once (#700) -------------------------------------
printf 'old\n' > "$live/scripts/hub.py.bak-1"
out=$(pass)
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c 'LIVE-GUARD live clutter')" = 1 ] && case "$out" in *"scripts/hub.py.bak-1"*) true;; *) false;; esac \
  && ok "a stray .bak-* copy is listed once as clutter" || ko "the clutter line: $out"
[ -f "$live/scripts/hub.py.bak-1" ] && ok "the clutter is listed, never removed" || ko "the clutter was removed"
out=$(pass); [ -z "$out" ] && ok "the same clutter is not listed again" || ko "the clutter printed again: $out"
rm -f "$live/scripts/hub.py.bak-1"; pass >/dev/null
# --- the main tree follows origin/stg before the publish (issue #292, #359) ---------------------------------
# The live copy is published from the main working tree, and nothing moved that tree to origin/stg: it stayed at an old
# commit while later PRs merged. The base is stg, never dev or main: a commit that landed in dev and not in stg never publishes. Before the publish the pass fast-forwards it (ff-only), and only a clean tree on its base.
# Each case is a bare origin, a clone that is the checkout, and a live skill a version behind. No network, no GitHub call.
G() { git -c user.name=t -c user.email=t@t "$@"; }
mk() { # mk NAME: bare origin $T/NAME.git, a clone at $T/NAME carrying contrib/squad-goal v1, a live skill at v1
  local n=$1; local w=$T/$n.work
  G init -q --bare -b stg "$T/$n.git"; G clone -q "$T/$n.git" "$w" 2>/dev/null
  mkdir -p "$w/contrib/squad-goal/scripts"; printf 'skill v1\n' > "$w/contrib/squad-goal/SKILL.md"; printf 'hub\n' > "$w/contrib/squad-goal/scripts/hub.py"
  G -C "$w" add -A; G -C "$w" commit -qm v1; G -C "$w" push -q origin HEAD:stg
  G clone -q -b stg "$T/$n.git" "$T/$n" 2>/dev/null
  mkdir -p "$T/$n.live/scripts"; printf 'skill v1\n' > "$T/$n.live/SKILL.md"
  printf 'new file\n' > "$w/contrib/squad-goal/board-definition.json"; printf 'skill v2\n' > "$w/contrib/squad-goal/SKILL.md"
  G -C "$w" add -A; G -C "$w" commit -qm v2; G -C "$w" push -q origin HEAD:stg     # origin/stg is now a commit ahead of $T/$n
}
ffpass() { mkdir -p "$T/st.$1"; SG_STATE=$T/st.$1 SG_CHECKOUT=$T/$1 SG_LIVE_DIR=$T/$1.live bash "$S/live-guard.sh" 2>&1; }

mk behind; head0=$(G -C "$T/behind" rev-parse HEAD)
out=$(ffpass behind)
[ "$(G -C "$T/behind" rev-parse HEAD)" = "$(G -C "$T/behind" rev-parse origin/stg)" ] && [ "$(G -C "$T/behind" rev-parse HEAD)" != "$head0" ] && ok "a clean tree behind origin is fast-forwarded" || ko "the tree was not fast-forwarded: $out"
case "$out" in *LIVE-GUARD*"fast-forwarded"*) ok "the fast-forward is named";; *) ko "the fast-forward is silent: $out";; esac
[ "$(cat "$T/behind.live/SKILL.md")" = "skill v2" ] && [ -f "$T/behind.live/board-definition.json" ] && ok "the publish carries the files the fast-forward brought" || ko "the publish did not carry the new files: $out"
stgsha=$(G -C "$T/behind" rev-parse origin/stg)
[ "$(cut -d' ' -f1 "$T/behind.live/PUBLISHED" 2>/dev/null)" = "$stgsha" ] && ok "the publish writes PUBLISHED with the tested sha (#700)" || ko "PUBLISHED does not hold $stgsha: $(cat "$T/behind.live/PUBLISHED" 2>&1)"
case "$out" in *"LIVE-GUARD published ${stgsha:0:8}"*) ok "the pass names the sha it published";; *) ko "the published sha is not named: $out";; esac

mk dirty; printf 'mine\n' >> "$T/dirty/contrib/squad-goal/SKILL.md"; h=$(G -C "$T/dirty" rev-parse HEAD)
out=$(ffpass dirty)
[ "$(G -C "$T/dirty" rev-parse HEAD)" = "$h" ] && [ "$(cat "$T/dirty/contrib/squad-goal/SKILL.md")" = "$(printf 'skill v1\nmine')" ] && ok "a dirty tree is left alone" || ko "the dirty tree was touched: $out"
case "$out" in *LIVE-GUARD*"not fast-forwarded"*dirty*) ok "the dirty tree is reported by name";; *) ko "the dirty tree is not reported: $out";; esac
[ "$(cat "$T/dirty.live/SKILL.md")" = "skill v1" ] && [ ! -e "$T/dirty.live/board-definition.json" ] && ok "a dirty shipped file publishes nothing" || ko "a dirty tree was published: $out"
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c .)" = 1 ] && ok "the dirty tree is one log line" || ko "the dirty tree did not log one line: $out"

# Machine config the publish never copies is not dirt: the tree still fast-forwards and publishes.
mk mcfg; w=$T/mcfg.work; mkdir -p "$w/contrib/squad-goal/scripts/profiles"; printf '{}\n' > "$w/contrib/squad-goal/scripts/models.json"; printf 'p\n' > "$w/contrib/squad-goal/scripts/profiles/p.sh"
G -C "$w" add -A; G -C "$w" commit -qm cfg; G -C "$w" push -q origin HEAD:stg; G -C "$T/mcfg" pull -q --ff-only origin stg
printf 'mine\n' >> "$T/mcfg/contrib/squad-goal/scripts/models.json"; printf 'mine\n' >> "$T/mcfg/contrib/squad-goal/scripts/profiles/p.sh"
out=$(ffpass mcfg)
[ "$(cat "$T/mcfg.live/SKILL.md")" = "skill v2" ] && ok "a machine-config change is not dirt: the publish runs" || ko "machine-config only held the publish: $out"

# An unfetched checkout publishes nothing and names the fetch.
mk nofetch; G -C "$T/nofetch" remote set-url origin "$T/missing.git"; printf 'skill v0\n' > "$T/nofetch.live/SKILL.md"
out=$(ffpass nofetch)
[ "$(cat "$T/nofetch.live/SKILL.md")" = "skill v0" ] && ok "a failed fetch publishes nothing" || ko "a failed fetch was published: $out"
case "$out" in *LIVE-GUARD*"not fast-forwarded"*fetch*) ok "the failed fetch is named";; *) ko "the failed fetch is not named: $out";; esac

mk other; G -C "$T/other" checkout -q -b topic; h=$(G -C "$T/other" rev-parse HEAD)
out=$(ffpass other)
[ "$(G -C "$T/other" rev-parse HEAD)" = "$h" ] && [ "$(G -C "$T/other" branch --show-current)" = topic ] && ok "a tree on another branch is left alone" || ko "the other-branch tree was touched: $out"
case "$out" in *LIVE-GUARD*"is on topic, not stg"*) ok "the other branch is reported by name";; *) ko "the other branch is not reported: $out";; esac
# A checkout on a work branch is no source, and the shipped page still follows: the pass publishes from its own stg tree.
[ "$(cat "$T/other.live/SKILL.md")" = "skill v2" ] && [ -f "$T/other.live/board-definition.json" ] && ok "a tree on another branch still ships the stg process" || ko "the other-branch tree shipped nothing: $out"
case "$out" in *LIVE-GUARD*published*) ok "the publish from the pass's own stg tree is named";; *) ko "the publish from the stg tree is silent: $out";; esac

mk diverged; printf 'local\n' > "$T/diverged/local.txt"; G -C "$T/diverged" add -A; G -C "$T/diverged" commit -qm local; printf 'skill v0\n' > "$T/diverged.live/SKILL.md"; h=$(G -C "$T/diverged" rev-parse HEAD)
out=$(ffpass diverged)
[ "$(G -C "$T/diverged" rev-parse HEAD)" = "$h" ] && ok "a diverged tree is left alone" || ko "the diverged tree was touched: $out"
case "$out" in *LIVE-GUARD*"not fast-forwarded"*"fast-forward"*) ok "the divergence is reported by name";; *) ko "the divergence is not reported: $out";; esac
[ "$(cat "$T/diverged.live/SKILL.md")" = "skill v0" ] && ok "a diverged tree publishes nothing" || ko "a diverged tree was published: $out"

# A commit in dev that is not in stg is never published: the tree sits on stg, origin/dev is a commit ahead of it.
mk devonly; w=$T/devonly.work; G -C "$w" checkout -q -b dev origin/stg~1 2>/dev/null; G -C "$w" checkout -q -B dev origin/stg
printf 'dev only\n' > "$w/contrib/squad-goal/dev-defect.txt"; G -C "$w" add -A; G -C "$w" commit -qm devonly; G -C "$w" push -q origin dev
out=$(ffpass devonly)
[ ! -e "$T/devonly.live/dev-defect.txt" ] && [ ! -e "$T/devonly/contrib/squad-goal/dev-defect.txt" ] && ok "a commit in dev that is not in stg is not published" || ko "a dev-only commit reached the live copy: $out"
[ "$(cat "$T/devonly.live/SKILL.md")" = "skill v2" ] && ok "the publish still carries what stg holds" || ko "the stg content was not published: $out"

# A checkout on dev or main is no source: its own commits never reach the live copy, and the shipped page still follows stg.
for br in dev main; do
  mk "on$br"; G -C "$T/on$br" checkout -q -b "$br"
  printf 'branch only\n' > "$T/on$br/contrib/squad-goal/$br-defect.txt"
  G -C "$T/on$br" add -A; G -C "$T/on$br" commit -qm "$br only"; h=$(G -C "$T/on$br" rev-parse HEAD)
  out=$(ffpass "on$br")
  [ "$(G -C "$T/on$br" rev-parse HEAD)" = "$h" ] && [ "$(G -C "$T/on$br" branch --show-current)" = "$br" ] && ok "a tree on $br is left alone" || ko "the $br tree was touched: $out"
  [ ! -e "$T/on$br.live/$br-defect.txt" ] && ok "a $br-only file never reaches the live copy" || ko "a $br-only file was published: $out"
  [ "$(cat "$T/on$br.live/SKILL.md")" = "skill v2" ] && ok "a tree on $br still ships what stg holds" || ko "the $br tree shipped nothing from stg: $out"
  case "$out" in *LIVE-GUARD*"is on $br, not stg"*) ok "the $br tree is reported as not on stg";; *) ko "the $br tree is not reported: $out";; esac
done

# --- unchanged input: the pass returns at once and does no work (issue #568) ----------------------
# The cycle runs this pass every cycle, and the publish check walks both trees. The pass keys its input -- the tree's
# HEAD and status, origin/stg after the fetch, and the live copy's own state -- in $SG_STATE and returns at once when
# the key is unchanged. The live state is part of the key: a hand edit there alone still runs the pass.
mk keyed; stk=$T/st.keyed; calls=$T/keyed.calls; mkdir -p "$stk"
cp "$S/publish-live.sh" "$S/publish-live.real.sh"
cat > "$S/publish-live.sh" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >> "$calls"
exec bash "$S/publish-live.real.sh" "\$@"
EOF
kpass() { SG_STATE=$stk SG_CHECKOUT=$T/keyed SG_LIVE_DIR=$T/keyed.live bash "$S/live-guard.sh" 2>&1; }
snap() { ( cd "$T/keyed.live" && find . -type f | LC_ALL=C sort | while read -r f; do printf '%s %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done ); }

out=$(kpass)                                    # no key yet: the pass reads the difference and publishes it
case "$out" in *LIVE-GUARD*"copy SKILL.md"*) ok "the first pass reads the difference";; *) ko "the first pass did not read: $out";; esac
[ "$(cat "$T/keyed.live/SKILL.md")" = "skill v2" ] && ok "the first pass publishes" || ko "the first pass did not publish: $out"

: > "$calls"; before=$(snap)
out=$(kpass)                                    # the input did not change: the pass returns at once
[ -z "$out" ] && ok "unchanged key: the pass says nothing" || ko "an unchanged key printed: $out"
[ ! -s "$calls" ] && ok "unchanged key: the publish check (the costly step) did not run" || ko "unchanged key still ran the check: $(cat "$calls")"
[ "$before" = "$(snap)" ] && ok "unchanged key: the pass makes no write" || ko "unchanged key wrote into the live copy"

# A hand edit of the live copy alone is an input (ADR-0140): the live state is in the key, so the pass runs.
printf 'hand edit\n' > "$T/keyed.live/scripts/hub.py"
out=$(kpass)
case "$out" in *LIVE-GUARD*"copy scripts/hub.py"*) ok "a hand edit of the live copy alone runs the pass";; *) ko "the live-only edit was not read: $out";; esac
[ "$(cat "$T/keyed.live/scripts/hub.py")" = "hub" ] && ok "the pass republishes over the hand edit" || ko "the hand edit stood: $out"

: > "$calls"; out=$(kpass)
[ -z "$out" ] && [ ! -s "$calls" ] && ok "after a publish the key is stored: the next pass skips" || ko "the pass did not skip after a publish (check calls: $(cat "$calls")): $out"

# --- the drivers keep the sha they started on: a publish to another sha is a restart the owner makes (#700) ---
# watchd start records driver.sha; the guard names the pending restart once per publish and never kills or starts a process.
mk restart; str=$T/st.restart; mkdir -p "$str"; old=$(G -C "$T/restart" rev-parse HEAD); printf '%s\n' "$old" > "$str/driver.sha"
w=$T/restart.work; printf 'watchd v1\n' > "$w/contrib/squad-goal/scripts/watchd.sh"   # a file the running loop holds in memory
G -C "$w" add -A; G -C "$w" commit -qm "the driver file"; G -C "$w" push -q origin HEAD:stg
printf '#!/bin/bash\necho "$*" >> "%s"\n' "$T/restart.watchd" > "$S/watchd.sh"
rpass() { SG_STATE=$str SG_CHECKOUT=$T/restart SG_LIVE_DIR=$T/restart.live bash "$S/live-guard.sh" 2>&1; }
out=$(rpass); new=$(G -C "$T/restart" rev-parse HEAD)
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c "LIVE-GUARD drivers run ${old:0:8}, published ${new:0:8}: restart pending (owner)")" = 1 ] \
  && ok "a publish to another sha names the pending restart once" || ko "no restart-pending line: $out"
[ ! -e "$T/restart.watchd" ] && ok "the guard starts or stops no driver" || ko "the guard called watchd: $(cat "$T/restart.watchd")"
[ "$(cat "$str/driver.sha")" = "$old" ] && ok "the guard leaves driver.sha to watchd" || ko "the guard rewrote driver.sha"
out=$(rpass); [ -z "$out" ] && ok "the restart-pending line is not repeated for the same publish" || ko "the next cycle printed: $out"
printf 'hand\n' > "$T/restart.live/SKILL.md"; out=$(rpass)
case "$out" in *"restart pending"*) ko "a republish of the same sha named the restart again: $out";; *) ok "a republish of the same sha names no second restart";; esac
printf '%s\n' "$new" > "$str/driver.sha"; printf 'watchd v2\n' > "$w/contrib/squad-goal/scripts/watchd.sh"
G -C "$w" commit -qam v3; G -C "$w" push -q origin HEAD:stg; out=$(rpass)
v3=$(G -C "$T/restart" rev-parse HEAD)
case "$out" in *"drivers run ${new:0:8}, published ${v3:0:8}"*) ok "each new publish names its own restart";; *) ko "the second publish named no restart: $out";; esac
rm -f "$S/watchd.sh"

# --- only a file the long-lived loop holds in memory asks for a restart (#2019) -------------------
# A per-call script starts a new process each call, so the publish already reaches it: a publish that changes only such a
# script asks for no restart, and the guard records the new build as current. The lanes and the files they source once
# stay in memory, so a change there is the restart only the owner makes, named with the file that changed.
mk percall; sta=$T/st.percall; mkdir -p "$sta"; w=$T/percall.work
printf 'watchd\n' > "$w/contrib/squad-goal/scripts/watchd.sh"
G -C "$w" add -A; G -C "$w" commit -qm "the driver file"; G -C "$w" push -q origin HEAD:stg
ppass() { SG_STATE=$sta SG_CHECKOUT=$T/percall SG_LIVE_DIR=$T/percall.live bash "$S/live-guard.sh" 2>&1; }
out=$(ppass); v=$(G -C "$T/percall" rev-parse HEAD); printf '%s\n' "$v" > "$sta/driver.sha"   # the drivers start on this build
printf 'janitor\n' > "$w/contrib/squad-goal/scripts/janitor.py"                             # a script that starts per call
G -C "$w" add -A; G -C "$w" commit -qm "a per-call script"; G -C "$w" push -q origin HEAD:stg
out=$(ppass); vj=$(G -C "$T/percall" rev-parse HEAD)
case "$out" in *"restart pending"*) ko "a per-call-only publish asked for a restart: $out";; *) ok "a publish that changes only a per-call script asks for no restart";; esac
[ "$(cut -d' ' -f1 "$T/percall.live/PUBLISHED" 2>/dev/null)" = "$vj" ] && ok "the per-call-only publish lands" || ko "the per-call-only publish did not land: $out"
[ "$(cat "$sta/live-guard.driver" 2>/dev/null)" = "$vj" ] && ok "the guard records the new build as current" || ko "the new build is not recorded as current: $(cat "$sta/live-guard.driver" 2>&1)"
out=$(ppass); [ -z "$out" ] && ok "the per-call-only publish asks for no restart a second time" || ko "the next cycle printed: $out"
printf 'watchd v2\n' > "$w/contrib/squad-goal/scripts/watchd.sh"
G -C "$w" commit -qam "the driver file again"; G -C "$w" push -q origin HEAD:stg
out=$(ppass); vd=$(G -C "$T/percall" rev-parse HEAD)
case "$out" in *"drivers run ${v:0:8}, published ${vd:0:8}: restart pending (owner)"*"scripts/watchd.sh"*) ok "a driver-file change names the restart and the file";; *) ko "the driver-file change was not named: $out";; esac

# --- the publish closes the loop-bug issue its fix names, so the stop lifts by itself (#732) --------
# The stop reads the issue state and nothing else (ADR-0149), so the step that makes the fix live is the one that
# closes it: a published commit carrying `Closes #n` closes #n, with one comment naming the sha and the published time.
# A TDD RED commit names its issue in the subject and carries no closing trailer, so it closes nothing (ADR-0136).
mkdir -p "$T/cbin"
cat > "$T/cbin/gh" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >> "$T/gh.log"
case "\$*" in *issues*) echo '[]';; esac   # the publish rules out an open issue's hold first: it needs a readable list
exit 0
EOF
chmod +x "$T/cbin/gh"
mk closes; w=$T/closes.work
printf 'skill v3\n' > "$w/contrib/squad-goal/SKILL.md"; G -C "$w" add -A; G -C "$w" commit -qm "Fix the live copy" -m "Closes #732"
G -C "$w" push -q origin HEAD:stg
clpass() { mkdir -p "$T/st.closes"; SG_STATE=$T/st.closes SG_REPO=example/squad PATH="$T/cbin:$PATH" SG_CHECKOUT=$T/closes SG_LIVE_DIR=$T/closes.live bash "$S/live-guard.sh" 2>&1; }
out=$(clpass); rc=$?
[ "$rc" = 0 ] && ok "a pass that closes an issue still exits 0" || ko "the closing pass exited $rc: $out"
case "$out" in *"LIVE-GUARD publish-live: closed #732"*) ok "the pass names the issue the publish closed";; *) ko "the close is not named: $out";; esac
[ "$(/usr/bin/grep -c 'state=closed' "$T/gh.log" 2>/dev/null || true)" = 1 ] && ok "the pass closes the issue once" || ko "close calls: $(cat "$T/gh.log" 2>/dev/null)"
case "$(cat "$T/gh.log")" in *"api -X PATCH repos/example/squad/issues/732 "*"state=closed"*) ok "the close goes through the one interface to the issue";; *) ko "the close: $(cat "$T/gh.log")";; esac
clsha=$(G -C "$T/closes" rev-parse HEAD); cts=$(cut -d' ' -f2 "$T/closes.live/PUBLISHED")
case "$(cat "$T/gh.log")" in *"$clsha"*"$cts"*) ok "the comment names the published sha and time";; *) ko "the comment: $(cat "$T/gh.log")";; esac
printf 'hand\n' > "$T/closes.live/scripts/hub.py"; : > "$T/gh.log"; out=$(clpass)
[ ! -s "$T/gh.log" ] && ok "a republish of the same sha closes nothing again" || ko "a republish closed again: $(cat "$T/gh.log")"
printf 'skill v4\n' > "$w/contrib/squad-goal/SKILL.md"; G -C "$w" add -A; G -C "$w" commit -qm "TDD RED: the live copy leaks #740"
G -C "$w" push -q origin HEAD:stg; : > "$T/gh.log"; out=$(clpass)
[ ! -s "$T/gh.log" ] && ok "a TDD RED commit alone closes nothing" || ko "a TDD RED commit closed something: $(cat "$T/gh.log")"

# --- a checkout that ships no copy of the skill: nothing to read, nothing to publish --------------
out=$(SG_CHECKOUT=$T/elsewhere bash "$S/live-guard.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] && ok "a checkout with no contrib/squad-goal: the pass says nothing" || ko "a foreign checkout spoke (exit $rc): $out"

rm -rf "$T"; [ $fail = 0 ] && echo "test-live-guard: all checks green" || { echo "test-live-guard: failed"; exit 1; }
