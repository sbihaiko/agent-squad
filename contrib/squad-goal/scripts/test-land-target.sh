#!/bin/bash
# seconds: 0.12
# One case per rule of the land (address.sh): sg_land_target answers, for an addressed issue, the checkout, the base, the
# branch the land merges and the route that owns it -- in the profile's own repository, and in no other.
# route=pr: every land is the pull request's merge into $SG_BASE, so no branch is named (#115 retired the local route).
# A bare number resolves to the profile in force ($SG_REPO), and it writes no line: the route is a field of the row.
# A repository the profile does not land -- another owner's, or one the profile works but does not own -- is refused by
# name, never guessed.
# Offline: a throwaway fork checkout, no gh. usage: bash test-land-target.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/fork"; git -C "$T/fork" init -q; git -C "$T/fork" remote add origin https://github.com/x/tool
export SG_REPO=x/emu SG_REPOS="x/emu x/tool" SG_CHECKOUT=$T/co SG_BASE=dev SG_FORK_DIR=$T/fork SG_FORK_BASE=main SG_FORK_BRANCH=gui/%n
. "$d/startability.sh"; . "$d/address.sh"   # address.sh last: the seam under test
land() {  # name, address, expected row (empty = refused, with the address named on stderr)
  local out rc err; out=$(sg_land_target "$2" 2>"$T/err"); rc=$?; err=$(cat "$T/err")
  if [ -z "$3" ]; then
    { [ $rc -ne 0 ] && [ -z "$out" ] && case "$err" in *"$2"*) true;; *) false;; esac; } && ok "$1: refused by name" || ko "$1: '$2' not refused by name (rc $rc, out '$out', err '$err')"
  else
    [ $rc -eq 0 ] && [ "$out" = "$3" ] && ok "$1: $2 -> $3" || ko "$1: '$2' landed as '$out' (rc $rc), expected '$3'"
  fi
}
land "an addressed issue in the profile repository" "x/emu#12"  "$T/co dev - pr"
land "a bare number is the profile repository"      "13"        "$T/co dev - pr"
land "a #N address is the profile repository"       "#17"       "$T/co dev - pr"
land "a short name at the profile repository"       "emu#14"    "$T/co dev - pr"
land "a repository the profile works, not its own"  "x/tool#11" ""
land "another owner, same name"                     "y/tool#15" ""
land "a repository not landed"                      "z/else#16" ""
# The land writes no line: the route is the row's fourth field, and a pass that narrated a legacy row left with the local
# route (#115). Both an addressed issue and a bare number are silent.
for a in "x/emu#12" 13; do
  out=$(sg_land_target "$a" 2>&1 >/dev/null); [ -z "$out" ] && ok "'$a' writes no line" || ko "'$a' wrote a line: '$out'"
done
# A bare number resolves through the profile even with no fork checkout: every land is a pull request now (#115).
out=$(SG_FORK_DIR= sg_land_target 18 2>"$T/err")
[ "$out" = "$T/co dev - pr" ] && ok "no fork checkout: a bare number still lands in the profile repository" || ko "no fork checkout: out '$out', err '$(cat "$T/err")'"
[ "$(sg_land_base x/emu)" = dev ] && ok "the base of the land is the profile's base" || ko "sg_land_base x/emu: '$(sg_land_base x/emu)'"
out=$(sg_land_base x/tool 2>/dev/null) && ko "a repository not landed answered its base: '$out'" || ok "a repository not landed answers no base"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
