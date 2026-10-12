#!/bin/bash
# One route (#32): every delivered issue lands by a PR, so the view (hub.py) reads the PR columns of every repository a
# delivery opens a PR in, not only of the profile's subject repository: the repositories the profile lists (SG_REPOS) and
# the repository of the fork checkout (SG_FORK_DIR), whose branches now land by PR too. The retired SG_TOOLING_REPO stays
# unread. Offline: hub.py is imported with a throwaway profile and checkout, no gh. usage: bash test-hub-repos.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/fork" "$T/co" "$T/hub"; git -C "$T/fork" init -q; git -C "$T/fork" remote add origin https://github.com/x/tool.git
repos() {  # env assignments... -> the REPOS list hub.py reads at import, one per line
  env -i PATH="$PATH" HOME="$T" SG_REPO=x/emu SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T/co" SG_HUB_DIR="$T/hub" "$@" \
    python3 -c 'import importlib.util,sys; s=importlib.util.spec_from_file_location("hub", sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print("\n".join(m.REPOS))' "$d/hub.py" 2>"$T/err"
}
r=$(repos SG_FORK_DIR="$T/fork")
printf '%s\n' "$r" | grep -qx x/tool && ok "the fork checkout's repository is read: its PRs show in the view" || ko "the view reads no PR of the fork checkout's repository x/tool: '$(echo $r)' $(tail -2 "$T/err")"
printf '%s\n' "$r" | head -1 | grep -qx x/emu && ok "the profile's repository stays first" || ko "first repository is not x/emu: '$(echo $r)'"
r=$(repos SG_REPOS="x/emu y/lib")
printf '%s\n' "$r" | grep -qx y/lib && ok "every repository the profile lists is read" || ko "SG_REPOS entry y/lib not read: '$(echo $r)'"
r=$(repos SG_TOOLING_REPO=x/old)
printf '%s\n' "$r" | grep -qx x/old && ko "the retired SG_TOOLING_REPO is read" || ok "the retired SG_TOOLING_REPO stays unread"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
