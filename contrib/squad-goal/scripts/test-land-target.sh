#!/bin/bash
# One case per route of the land rule (address.sh): sg_land_target answers, for an addressed issue, the checkout, the base,
# the branch the land merges and the route that owns it, in the repository the address NAMES and in no other.
# route=local: the repository the fork checkout ($SG_FORK_DIR) resolves to -- a local branch merged into $SG_FORK_BASE.
# route=pr: the profile's repository -- its land is the pull request's merge into $SG_BASE, so no branch is named.
# A bare number that came with a board row is a legacy row: it keeps the local route it had, and the log line names it.
# A bare number with no board row is the profile in force: its repository, the pr route, and the log line says so.
# A repository the profile does not land is refused by name, never guessed.
# Offline: a throwaway fork checkout, no gh. usage: bash test-land-target.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/fork"; git -C "$T/fork" init -q; git -C "$T/fork" remote add origin https://github.com/x/tool
export SG_REPO=x/emu SG_REPOS="x/emu x/tool" SG_CHECKOUT=$T/co SG_BASE=dev SG_FORK_DIR=$T/fork SG_FORK_BASE=main SG_FORK_BRANCH=gui/%n
. "$d/startability.sh"; . "$d/address.sh"   # address.sh last: the seam under test, whichever startability.sh is beside it
land() {  # name, address, expected row (empty = refused, with the address named on stderr), optional "row" (a board row came with it)
  local out rc err; out=$(sg_land_target "$2" ${4:+"$4"} 2>"$T/err"); rc=$?; err=$(cat "$T/err")
  if [ -z "$3" ]; then
    { [ $rc -ne 0 ] && [ -z "$out" ] && case "$err" in *"$2"*) true;; *) false;; esac; } && ok "$1: refused by name" || ko "$1: '$2' not refused by name (rc $rc, out '$out', err '$err')"
  else
    [ $rc -eq 0 ] && [ "$out" = "$3" ] && ok "$1: $2 -> $3" || ko "$1: '$2' landed as '$out' (rc $rc), expected '$3'"
  fi
}
land "route local"            "x/tool#11"  "$T/fork main gui/11 local"
land "route pr"               "x/emu#12"   "$T/co dev - pr"
land "route local, legacy row" "13"        "$T/fork main gui/13 local" row
land "route pr, bare, no row"  "13"        "$T/co dev - pr"
land "route pr, #N, no row"    "#17"       "$T/co dev - pr"
land "route local, short name" "tool#14"   "$T/fork main gui/14 local"
land "another owner, same name" "y/tool#15" ""
land "a repository not landed" "z/else#16" ""
# The log line: each legacy row the pass decides on is named, and no row changes route without a line that says so.
logs() {  # name, address, optional "row", then the text the stderr line must carry
  local n=$1 a=$2 r=$3; shift 3; sg_land_target "$a" ${r:+"$r"} >/dev/null 2>"$T/err"; local err; err=$(cat "$T/err")
  case "$err" in *"$*"*) ok "$n: line names it";; *) ko "$n: no line with '$*' (stderr '$err')";; esac
}
logs "legacy row"      13  row   "legacy row #13"
logs "legacy row, route" 13 row  "route local"
logs "no row"          13  ""    "no board row #13"
logs "no row, route"   13  ""    "route pr"
logs "no row, repo"    13  ""    "x/emu"
out=$(sg_land_target "x/tool#11" 2>&1 >/dev/null); [ -z "$out" ] && ok "an addressed issue is no legacy row: no line" || ko "addressed issue logged '$out'"
# A legacy row with no local land to keep is refused by name, never turned into a pull request in the profile's repository.
out=$(SG_FORK_DIR= sg_land_target 18 row 2>"$T/err"); rc=$?; err=$(cat "$T/err")
{ [ $rc -ne 0 ] && [ -z "$out" ] && case "$err" in *"#18"*) true;; *) false;; esac; } && ok "legacy row with no local land: refused by name" || ko "legacy row, no fork: rc $rc out '$out' err '$err'"
[ "$(sg_land_base x/emu)" = dev ] && ok "base of the pr route is the profile's base" || ko "sg_land_base x/emu: '$(sg_land_base x/emu)'"
[ "$(sg_land_base x/tool)" = main ] && ok "base of the local route is the fork's base" || ko "sg_land_base x/tool: '$(sg_land_base x/tool)'"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
