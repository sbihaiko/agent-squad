#!/bin/bash
# A pass reads the profile's repository, or the one repository named as its argument (#20 retired the second, tooling,
# repository a profile could name). Formerly: regression for the loop reading ONE repository (#1820): the squad works in two -- the emulator ($SG_REPO) and its
# own tooling (SG_TOOLING_REPO) -- and the autopilot listed only the first, so a pull request in the tooling
# repository was something a human merged by hand. One pass must now read the open PRs of BOTH, act on a tooling PR
# through the same machinery (here: launch revci.sh for a head with no review, with the repository it belongs to in
# its environment), and keep the profile's repository alone when it is named as the argument.
# Stub gh, no network. usage: bash test-autopilot-two-repos.sh [path/to/autopilot.sh]
src=${1:-$(dirname "$0")/autopilot.sh}; here=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); log=$T/report; : > "$log"; fail=0
ok() { printf 'ok   %s\n' "$1" >> "$log"; }; ko() { printf 'FAIL %s\n' "$1" >> "$log"; fail=1; }
G=/usr/bin/grep
S=$T/scene; mkdir -p "$S/s" "$S/st/auto" "$S/st/cq" "$S/bin" "$S/co/runs"
cp "$src" "$S/s/autopilot.sh"
printf 'export SG_STATE=%s SG_REPO=x/emu SG_TOOLING_REPO=x/tool SG_BASE=main SG_AUTO_MERGE=yes SG_MERGE_METHOD=squash SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT=%s BOARD=true\n' "$S/st" "$S/co" > "$S/s/env.sh"
# the scripts a pass launches record which repository they were handed: that is the whole point here. autofix's two
# questions answer "not a duplicate" and "no threads", so the pass reaches the review it launches.
emit='printf "%s %s %s\n" "$(basename "$0")" "$SG_REPO" "$*" >> '"$S"'/launched'
for f in revci autopanel prepush; do printf '#!/bin/bash\n%s\nexit 0\n' "$emit" > "$S/s/$f.sh"; done
printf '#!/bin/bash\n%s\ncase "$*" in *--dupissue*) exit 1;; *--threads*) echo 0;; esac\nexit 0\n' "$emit" > "$S/s/autofix.sh"
chmod +x "$S/s/"*.sh
cat > "$S/bin/gh" <<'J'
#!/bin/bash
printf '%s\n' "gh $*" >> "$GHLOG"
case "$*" in
  # the two repositories answer with their own PR list, and the fake replies name the repository they came from
  *repos/x/emu/pulls?state=open*) printf '%s\n' "${EMU_JSON:-}";;
  *repos/x/tool/pulls?state=open*) printf '%s\n' "${TOOL_JSON:-}";;
  *"check-runs"*) echo success;;
  *"mergeable_state"*) echo clean;;
  *pulls/x*) echo "";;
esac
J
chmod +x "$S/bin/gh"
export PATH="$S/bin:$PATH" SG_STATE="$S/st" GHLOG="$S/ghlog" LAUNCHED="$S/launched"
: > "$GHLOG"; : > "$LAUNCHED"
pr() { printf '[{"number":%s,"head":{"sha":"%s"},"ref":"%s","title":"t","body":"%s","labels":[],"base":{"ref":"main"},"draft":false,"created_at":"2026-10-10T00:00:00Z"}]' "$1" "$2" "$3" "Refs #$4"; }
run() { bash "$S/s/autopilot.sh" "$@" 2>/dev/null; }
wait_launched() { for i in $(seq 1 20); do [ "$($G -c '^revci.sh' "$LAUNCHED" 2>/dev/null)" -ge "$1" ] && break; sleep 0.3; done; }

# --- one pass, no argument: the profile's repository alone is read, even with the retired SG_TOOLING_REPO left in the
# environment (#20: a repository is worked through its own profile, never as a second repository of another's loop)
export EMU_JSON="$(pr 1512 aaaaaaaaaaaa gui/1512 1512)" TOOL_JSON="$(pr 1821 bbbbbbbbbbbb gui/1821 1809)"
r=$(run); wait_launched 1
$G -q 'repos/x/emu/pulls?state=open' "$GHLOG" && ok "one pass reads the open PRs of the profile's repository" || ko "the pass did not read x/emu: $($G 'pulls?state=open' "$GHLOG" | tr '\n' ' ')"
$G -q 'repos/x/tool/pulls?state=open' "$GHLOG" && ko "the pass read the retired tooling repository" || ok "the retired SG_TOOLING_REPO is never read"
$G -q '^revci.sh x/emu 1512 1512$' "$LAUNCHED" && ok "the emulator PR is reviewed by the loop's own chain, in its repository" || ko "no review of the emulator PR: $(cat "$LAUNCHED")"
$G -q '^autofix.sh x/emu --dupissue 1512$' "$LAUNCHED" && ok "the emulator PR's fix machinery works in its repository" || ko "autofix did not answer for the emulator PR: $(cat "$LAUNCHED")"

# --- a named repository is the only one that pass reads
: > "$GHLOG"; r=$(run x/tool)
$G -q 'repos/x/tool/pulls?state=open' "$GHLOG" && ok "a named repository is read" || ko "the named repository was not read"
wait_launched 2; $G -q '^revci.sh x/tool 1821 1809$' "$LAUNCHED" && ok "a named repository's PR is reviewed in that repository, with its own Refs" || ko "no review of the named repository's PR: $(cat "$LAUNCHED")"
$G -q 'repos/x/emu/pulls?state=open' "$GHLOG" && ko "the pass read the other repository too" || ok "the pass read no other repository"

[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
cat "$log"; rm -rf "$T"; exit $fail
