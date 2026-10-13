#!/bin/bash
# Table test for the address seam (address.sh): ONE function, sg_resolve_repo, turns each spelling of "which repository"
# into one `owner/name` -- an address `owner/repo#N`, a bare `N`, a profile name and a checkout -- and sg_same_repo calls
# two names one repository only when owner AND name match. A short name with no owner is resolved against the repositories
# the profile works, or refused by name. Offline: throwaway profiles and checkouts, no gh. usage: bash test-address.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/profiles" "$T/co"
printf ': "${SG_REPO:=acme/emu}"\n' > "$T/profiles/emu.sh"
git -C "$T/co" init -q; git -C "$T/co" remote add origin https://github.com/acme/tool.git
mkdir -p "$T/co2"; git -C "$T/co2" init -q; git -C "$T/co2" remote add origin git@github.com:acme/emu.git
export SG_REPO=acme/emu SG_REPOS="acme/emu acme/tool other/emu2" SG_PROFILES_DIR=$T/profiles
. "$d/forkonly.sh"; . "$d/address.sh"   # address.sh last: the seam under test, whichever forkonly.sh is beside it
row() {  # name, input, expected repository (empty = refused, with the input named on stderr)
  local out err rc; out=$(sg_resolve_repo "$2" 2>"$T/err"); rc=$?; err=$(cat "$T/err")
  if [ -z "$3" ]; then
    { [ $rc -ne 0 ] && [ -z "$out" ] && case "$err" in *"$2"*) true;; *) false;; esac; } && ok "$1: refused by name" || ko "$1: '$2' not refused by name (rc $rc, out '$out', err '$err')"
  else
    [ $rc -eq 0 ] && [ "$out" = "$3" ] && ok "$1: $2 -> $3" || ko "$1: '$2' resolved to '$out' (rc $rc), expected $3"
  fi
}
row "address"         "acme/tool#12"  acme/tool
row "address, other"  "zed/else#3"    zed/else
row "bare number"     "12"            acme/emu
row "bare #number"    "#12"           acme/emu
row "profile name"    "emu"           acme/emu
row "checkout https"  "$T/co"         acme/tool
row "checkout ssh"    "$T/co2"        acme/emu
row "short, one match" "tool#7"       acme/tool
row "short, no match" "nowhere#7"     ""
SG_REPOS="acme/tool x/tool" row "short, two matches" "tool#7" ""
row "empty"           ""              ""
sg_same_repo acme/tool acme/tool && ok "same: owner and name match" || ko "same: acme/tool != acme/tool"
sg_same_repo acme/tool other/tool && ko "same: other/tool equals acme/tool on the name alone" || ok "same: another owner is another repository"
sg_same_repo acme/tool acme/emu && ko "same: acme/emu equals acme/tool" || ok "same: another name is another repository"
sg_same_repo tool acme/tool && ok "same: the short name resolves to its one match" || ko "same: short 'tool' did not resolve to acme/tool"
SG_REPOS="acme/tool x/tool" sg_same_repo tool acme/tool 2>/dev/null && ko "same: an ambiguous short name was taken as acme/tool" || ok "same: an ambiguous short name is refused"
sg_same_repo "" "" && ko "same: two empty names are one repository" || ok "same: an empty name is no repository"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
