#!/bin/bash
# One case per route of the land rule (address.sh): sg_land_target answers, for an addressed issue, the checkout, the base,
# the branch the land merges and the route that owns it, in the repository the address NAMES and in no other.
# route=local: the repository the fork checkout ($SG_FORK_DIR) resolves to -- a local branch merged into $SG_FORK_BASE.
# route=pr: the profile's repository -- its land is the pull request's merge into $SG_BASE, so no branch is named.
# A legacy bare number reads the local row; a repository the profile does not land is refused by name, never guessed.
# Offline: a throwaway fork checkout, no gh. usage: bash test-land-target.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/fork"; git -C "$T/fork" init -q; git -C "$T/fork" remote add origin https://github.com/x/tool
export SG_REPO=x/emu SG_REPOS="x/emu x/tool" SG_CHECKOUT=$T/co SG_BASE=dev SG_FORK_DIR=$T/fork SG_FORK_BASE=main SG_FORK_BRANCH=gui/%n
. "$d/forkonly.sh"; . "$d/address.sh"   # address.sh last: the seam under test, whichever forkonly.sh is beside it
land() {  # name, address, expected row (empty = refused, with the address named on stderr)
  local out rc err; out=$(sg_land_target "$2" 2>"$T/err"); rc=$?; err=$(cat "$T/err")
  if [ -z "$3" ]; then
    { [ $rc -ne 0 ] && [ -z "$out" ] && case "$err" in *"$2"*) true;; *) false;; esac; } && ok "$1: refused by name" || ko "$1: '$2' not refused by name (rc $rc, out '$out', err '$err')"
  else
    [ $rc -eq 0 ] && [ "$out" = "$3" ] && ok "$1: $2 -> $3" || ko "$1: '$2' landed as '$out' (rc $rc), expected '$3'"
  fi
}
land "route local"            "x/tool#11"  "$T/fork main gui/11 local"
land "route pr"               "x/emu#12"   "$T/co dev - pr"
land "route local, legacy bare" "13"       "$T/fork main gui/13 local"
land "route local, short name" "tool#14"   "$T/fork main gui/14 local"
land "another owner, same name" "y/tool#15" ""
land "a repository not landed" "z/else#16" ""
[ "$(sg_land_base x/emu)" = dev ] && ok "base of the pr route is the profile's base" || ko "sg_land_base x/emu: '$(sg_land_base x/emu)'"
[ "$(sg_land_base x/tool)" = main ] && ok "base of the local route is the fork's base" || ko "sg_land_base x/tool: '$(sg_land_base x/tool)'"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
