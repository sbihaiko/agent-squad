#!/bin/bash
# The resolution seam (#20): repoctx.sh turns a profile slug, a checkout, an address owner/repo#n or a bare n into one
# repository context, and refuses an unknown repository by name. A table of inputs over two fixture profiles; no network.
# usage: bash test-repoctx.sh [path/to/repoctx.sh]
src=${1:-$(cd "$(dirname "$0")" && pwd)/repoctx.sh}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
P=$T/profiles; B=$T/state; mkdir -p "$P" "$T/co-alpha" "$T/co-beta" "$T/plain"
cat > "$P/alpha.sh" <<EOF
: "\${SG_REPO:=x/alpha}"; : "\${SG_OWNER:=x}"; : "\${SG_PROJECT:=4}"; : "\${SG_CHECKOUT:=$T/co-alpha}"; : "\${SG_BASE:=main}"
: "\${SG_FORK_DIR:=$T/fork-alpha}"
EOF
cat > "$P/Beta.sh" <<EOF
: "\${SG_REPO:=Y/Beta}"; : "\${SG_OWNER:=Y}"; : "\${SG_PROJECT:=9}"; : "\${SG_CHECKOUT:=$T/co-beta}"; : "\${SG_BASE:=develop}"
EOF
printf ': "${SG_REPO:=owner/repo}"\n' > "$P/example.sh"
printf ': "${SG_REPO:=x/legacy}"; : "${SG_PROJECT:=4}"; : "${SG_CHECKOUT:=%s/co-legacy}"; SG_TOOLING_REPO=x/tool\n' "$T" > "$P/legacy.sh"   # names the retired second repository
git init -q "$T/clone" && git -C "$T/clone" remote add origin git@github.com:Y/Beta.git
export SG_PROFILES_DIR=$P SG_STATE_BASE=$B; unset SG_PROFILE SG_REPO SG_CHECKOUT SG_STATE
# resolve <want: field=value ...> -- <input> [SG_PROFILE]: every named field of the printed context must match
row() { local want=() a; while [ "$1" != -- ]; do want+=("$1"); shift; done; shift
  local out rc; out=$(cd "$T/plain" && SG_PROFILE=${2:-} bash "$src" "$1" 2>&1); rc=$?
  [ $rc = 0 ] || { ko "'$1' (profile '${2:-}') was refused: $out"; return; }
  for a in "${want[@]}"; do printf '%s\n' "$out" | /usr/bin/grep -qxF "$(printf '%s=%q' "${a%%=*}" "${a#*=}")" \
    || { ko "'$1' (profile '${2:-}'): want ${a}, got: $(echo $out)"; return; }; done
  ok "'$1' (profile '${2:-}') resolves to ${want[*]}"; }
refused() { local out rc; out=$(cd "$T/plain" && SG_PROFILE=${3:-} bash "$src" "$1" 2>&1); rc=$?
  [ $rc != 0 ] && printf '%s' "$out" | /usr/bin/grep -qF "$2" && ok "'$1' is refused, naming $2" || ko "'$1' was not refused by name (rc $rc): $out"; }

row SG_PROFILE=alpha SG_REPO=x/alpha SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT=$T/co-alpha SG_BASE=main SG_FORK_DIR=$T/fork-alpha \
    SG_STATE=$B/alpha SG_HUB_PATH=/alpha SG_ISSUE= -- alpha
row SG_PROFILE=alpha SG_REPO=x/alpha SG_ISSUE=12 SG_STATE=$B/alpha -- 'x/alpha#12'
row SG_PROFILE=alpha SG_ISSUE=12 -- 'X/Alpha#12'
row SG_PROFILE=Beta SG_REPO=Y/Beta SG_PROJECT=9 SG_BASE=develop SG_STATE=$B/Beta SG_HUB_PATH=/Beta SG_ISSUE= -- y/beta
row SG_PROFILE=Beta SG_REPO=Y/Beta SG_ISSUE=12 -- 12 Beta
row SG_PROFILE=alpha SG_REPO=x/alpha SG_ISSUE=12 -- '#12' alpha
row SG_PROFILE=alpha SG_CHECKOUT=$T/co-alpha -- "$T/co-alpha"
row SG_PROFILE=Beta SG_REPO=Y/Beta -- "$T/clone"
# a bare number in a checkout a profile matches resolves to that checkout's profile (the profile in force)
out=$(cd "$T/clone" && SG_PROFILE= bash "$src" 7 2>&1); printf '%s\n' "$out" | /usr/bin/grep -qx 'SG_REPO=Y/Beta' \
  && ok "a bare number resolves to the profile matched from the cwd's origin" || ko "bare number in a matched checkout: $out"
refused 'z/gone#3' z/gone
refused 'z/gone' z/gone
refused nope nope
refused example example
refused 12 'no profile in force'
refused 12 gone gone
refused "$T/plain" "$T/plain"
# the retired settings map into the repository list with one named warning each, and never stop the run
out=$(cd "$T/plain" && bash "$src" legacy 2>"$T/warn1"); rc=$?
[ $rc = 0 ] && printf '%s\n' "$out" | /usr/bin/grep -qx 'SG_REPOS=x/legacy\\ x/tool' \
  && ok "a profile that names SG_TOOLING_REPO resolves, the tooling repository one more entry after its own" || ko "legacy (rc $rc): $out"
[ "$(/usr/bin/grep -c 'SG_TOOLING_REPO' "$T/warn1")" = 1 ] && /usr/bin/grep -q 'retired' "$T/warn1" \
  && ok "the retired SG_TOOLING_REPO draws one named warning" || ko "legacy warning: $(cat "$T/warn1")"
(cd "$T/plain" && bash "$src" legacy >/dev/null 2>"$T/warn2"); [ ! -s "$T/warn2" ] \
  && ok "the warning is given once per state directory, not on every call" || ko "second call warned again: $(cat "$T/warn2")"
rm -rf "$B/alpha"; out=$(cd "$T/plain" && bash "$src" alpha 2>"$T/warn3"); /usr/bin/grep -q 'SG_FORK_DIR' "$T/warn3" && printf '%s\n' "$out" | /usr/bin/grep -qx 'SG_FORK_DIR='"$T"'/fork-alpha' \
  && ok "the retired SG_FORK_DIR draws a named warning and is still carried for the legacy land route" || ko "alpha fork warning: $(cat "$T/warn3")"
rm -rf "$B/legacy"
out=$(cd "$T/plain" && SG_PROFILE=legacy SG_SQUAD=/dev/null bash -c '. "$1/env.sh"; echo "reached $SG_REPOS"' _ "$(dirname "$src")" 2>&1); rc=$?
[ $rc = 0 ] && printf '%s' "$out" | /usr/bin/grep -q 'SG_TOOLING_REPO' && printf '%s' "$out" | /usr/bin/grep -qF 'reached x/legacy x/tool' \
  && ok "env.sh warns by name, runs, and exports SG_REPOS" || ko "env.sh with SG_TOOLING_REPO set (rc $rc): $out"
# the source form: sg_repo_ctx sets the context in the caller's shell
out=$(cd "$T/plain" && . "$src" && sg_repo_ctx 'y/beta#5' && echo "$SG_REPO $SG_ISSUE $SG_STATE")
[ "$out" = "Y/Beta 5 $B/Beta" ] && ok "sourced, sg_repo_ctx sets the context in the caller" || ko "sourced form: '$out'"
out=$(bash "$src" --list 2>&1); [ "$out" = "$(printf 'Beta\tY/Beta\nalpha\tx/alpha\nlegacy\tx/legacy')" ] \
  && ok "--list names every known profile and its repository, the example excluded" || ko "--list: $out"
rm -rf "$T"; exit $fail
