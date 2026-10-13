#!/bin/bash
# A profile that still names the retired SG_TOOLING_REPO keeps working: env.sh maps it into SG_REPOS, prints ONE warning
# that names the profile, the setting and its replacement, and the loop runs. The same profile with the setting removed
# runs with no warning. Drives the real env.sh and autopilot.sh against a stub gh; no network.
# usage: bash test-retired-settings.sh
here=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; G=/usr/bin/grep
ok() { printf 'ok   %s\n' "$1"; }; ko() { printf 'FAIL %s\n' "$1"; fail=1; }
S=$T/scene; mkdir -p "$S/s/profiles" "$S/bin" "$S/co/runs" "$S/state"
cp "$here/env.sh" "$here/address.sh" "$here/autopilot.sh" "$S/s/"
# the scripts a pass launches record the repository they were handed
emit='printf "%s %s %s\n" "$(basename "$0")" "$SG_REPO" "$*" >> '"$S"'/launched'
for f in revci autopanel prepush; do printf '#!/bin/bash\n%s\nexit 0\n' "$emit" > "$S/s/$f.sh"; done
printf '#!/bin/bash\n%s\ncase "$*" in *--dupissue*) exit 1;; *--threads*) echo 0;; esac\nexit 0\n' "$emit" > "$S/s/autofix.sh"
chmod +x "$S/s/"*.sh
cat > "$S/bin/gh" <<'J'
#!/bin/bash
printf '%s\n' "gh $*" >> "$GHLOG"
case "$*" in
  *repos/x/emu/pulls?state=open*) printf '%s\n' "${EMU_JSON:-}";;
  *"check-runs"*) echo success;;
  *"mergeable_state"*) echo clean;;
esac
J
chmod +x "$S/bin/gh"
common='export SG_OWNER=x SG_PROJECT=4 SG_BASE=main SG_AUTO_MERGE=yes SG_MERGE_METHOD=squash'
printf ': "${SG_REPO:=x/emu}"; SG_TOOLING_REPO=x/tool; SG_CHECKOUT=%s; %s\n' "$S/co" "$common" > "$S/s/profiles/retired.sh"
printf ': "${SG_REPO:=x/emu}"; SG_REPOS="x/emu x/tool"; SG_CHECKOUT=%s; %s\n' "$S/co" "$common" > "$S/s/profiles/migrated.sh"
export PATH="$S/bin:$PATH" GHLOG="$S/ghlog" SG_SQUAD=/dev/null SG_STATE_BASE="$S/state"
pr() { printf '[{"number":%s,"head":{"sha":"%s"},"ref":"%s","title":"t","body":"Refs #%s","labels":[],"base":{"ref":"main"},"draft":false,"created_at":"2026-10-10T00:00:00Z"}]' "$1" "$2" "$3" "$4"; }
export EMU_JSON="$(pr 1512 aaaaaaaaaaaa gui/1512 1512)"
cycle() { : > "$S/launched"; : > "$GHLOG"; rm -rf "$S/state/$1"   # one pass of the loop as profile $1; stderr is kept apart
  ( cd "$S/co" && env -u SG_TOOLING_REPO -u SG_REPOS -u SG_REPO -u SG_STATE SG_PROFILE=$1 bash "$S/s/autopilot.sh" >/dev/null 2>"$S/err-$1" )
  for i in $(seq 1 20); do $G -q '^revci.sh' "$S/launched" && break; sleep 0.3; done; }
warned() { $G -c 'SG_TOOLING_REPO' "$S/err-$1" 2>/dev/null; }

# --- 1. a retired profile runs a full cycle, with one warning naming the profile, the setting and its replacement
cycle retired
$G -q '^revci.sh x/emu 1512 1512$' "$S/launched" && ok "retired profile: the pass runs and reviews the PR in its repository" \
  || ko "retired profile: the pass did not run (launched: $(cat "$S/launched")) (stderr: $(cat "$S/err-retired"))"
[ "$(warned retired)" = 1 ] && ok "retired profile: exactly one warning" || ko "retired profile: warnings = $(warned retired): $(cat "$S/err-retired")"
w=$($G SG_TOOLING_REPO "$S/err-retired" | head -1)
case "$w" in *retired*SG_REPOS*) ;; *) w="";; esac
[ -n "$w" ] && printf '%s' "$w" | $G -q 'profile retired' && ok "the warning names the profile, the setting and its replacement" || ko "the warning is incomplete: $(cat "$S/err-retired")"
# a second source of env.sh in the same state directory stays quiet: one warning per cycle, not one per script
( cd "$S/co" && env -u SG_TOOLING_REPO -u SG_REPOS -u SG_REPO -u SG_STATE SG_PROFILE=retired bash -c '. "$1/env.sh"; . "$1/env.sh"' _ "$S/s" 2>"$S/err-again" )
[ ! -s "$S/err-again" ] && ok "a later script of the same cycle is silent" || ko "the warning repeated: $(cat "$S/err-again")"

# --- nothing narrows what the profile works: its own repository and the retired one are both in the list
out=$(cd "$S/co" && env -u SG_TOOLING_REPO -u SG_REPOS -u SG_REPO -u SG_STATE SG_PROFILE=retired bash -c '. "$1/env.sh" 2>/dev/null; sg_repos | tr "\n" " "; echo "|${SG_TOOLING_REPO:-unset}|$(sg_resolve_repo tool)"' _ "$S/s")
[ "$out" = "x/emu x/tool |unset|x/tool" ] && ok "the retired repository is one more entry of SG_REPOS, still resolvable, and the retired key is no longer read" \
  || ko "retired profile repositories: '$out'"

# --- 2. the same profile with the retired setting removed runs with no warning
cycle migrated
$G -q '^revci.sh x/emu 1512 1512$' "$S/launched" && ok "migrated profile: the pass runs the same work" || ko "migrated profile: the pass did not run (launched: $(cat "$S/launched"))"
[ ! -s "$S/err-migrated" ] || ! $G -qi 'retired\|SG_TOOLING_REPO' "$S/err-migrated" && ok "migrated profile: no warning" || ko "migrated profile warned: $(cat "$S/err-migrated")"
out=$(cd "$S/co" && env -u SG_TOOLING_REPO -u SG_REPOS -u SG_REPO -u SG_STATE SG_PROFILE=migrated bash -c '. "$1/env.sh" 2>/dev/null; sg_repos | tr "\n" " "' _ "$S/s")
[ "$out" = "x/emu x/tool " ] && ok "migrated profile: the same repositories" || ko "migrated profile repositories: '$out'"

rm -rf "$T"; exit $fail
