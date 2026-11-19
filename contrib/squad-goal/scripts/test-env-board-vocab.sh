#!/bin/bash
# seconds: 0.13
# env.sh names the board vocabulary when the profile does not (#760): a board made by board_define.py carries the shipped
# vocabulary, so the default is `shipped`; a profile that names another value keeps it. Offline: a throwaway profile.
# usage: bash test-env-board-vocab.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/profiles" "$T/plain"
printf ': "${SG_REPO:=acme/emu}"; : "${SG_OWNER:=acme}"; : "${SG_PROJECT:=1}"\n' > "$T/profiles/emu.sh"
printf ': "${SG_REPO:=acme/old}"; : "${SG_OWNER:=acme}"; : "${SG_PROJECT:=2}"; export SG_BOARD_VOCAB=legacy\n' > "$T/profiles/old.sh"
vocab() { (cd "$T/plain" && env -u SG_BOARD_VOCAB -u SG_REPO -u SG_REPOS -u SG_CHECKOUT SG_PROFILE="$1" SG_PROFILES_DIR="$T/profiles" \
  SG_STATE="$T/state-$1" SG_SHARED="$T/shared" SG_BOARD_CACHE="$T/cache/b.json" SG_SQUAD="$T" \
  bash -c '. "$1/env.sh" >/dev/null 2>&1; bash -c "echo \${SG_BOARD_VOCAB:-}"' _ "$d"); }
v=$(vocab emu); [ "$v" = shipped ] && ok "no vocabulary in the profile: shipped, exported" || ko "no vocabulary in the profile: got '$v', want shipped"
v=$(vocab old); [ "$v" = legacy ] && ok "a profile's vocabulary wins" || ko "a profile's vocabulary: got '$v', want legacy"
rm -rf "$T"; exit $fail
