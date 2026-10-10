#!/bin/bash
# Regression test for forkclose.sh's reland: a branch that is already an ancestor of the base but whose files a sibling's reverted merge took away is merged again (restoring them), and its dependents wait for it. Stub gh/test.sh, throwaway repo.
src=${1:-$(dirname "$0")/forkclose.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$src" "$T/s/forkclose.sh"
printf '#!/bin/bash\necho "pass-1"; exit 0\n' > "$T/s/test.sh"; chmod +x "$T/s/test.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
g checkout -q -b gui/1 local/next; echo one > "$T/fork/one.py"; g add .; g commit -q -m "work 1"
g checkout -q -b gui/2 gui/1; echo two > "$T/fork/two.py"; g add .; g commit -q -m "work 2"; g checkout -q local/next
g merge -q --no-ff -m "Merge gui/2 (Refs #2)" gui/2; g revert -m 1 --no-edit HEAD >/dev/null   # gui/2 went red: its dependency gui/1 left with it
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"},{"n":2,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"pulls?state=open"*) ;;
  *"issues/2 -q .body"*) printf '## Blocked by\n\n- #1\n';;
  *"issues/"*" -q .body"*) ;;
  *"issues/"*" -q .state"*) echo open;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
[ "$(g rev-list --count local/next..gui/1)" = 0 ] && [ ! -e "$T/fork/one.py" ] && ok "setup: gui/1 is an ancestor yet its file is gone" || ko "setup wrong"
r=$(FC_DRY=1 bash "$T/s/forkclose.sh" 2>&1)
case "$r" in *"would merge gui/1 "*) ok "an ancestor branch with lost files is a candidate";; *) ko "gui/1 not a candidate: '$r'";; esac
case "$r" in *"WAIT #2"*) ok "the dependent waits for it";; *) ko "no WAIT for #2: '$r'";; esac
bash "$T/s/forkclose.sh" >/dev/null 2>&1
[ -e "$T/fork/one.py" ] && ok "the lost file is back in the base" || ko "one.py not restored"
[ "$(g rev-list --parents -n1 HEAD | wc -w | tr -d ' ')" = 3 ] || [ "$(g log --merges --format=%s -1 | /usr/bin/grep -c 'Merge gui/1')" = 1 ] && ok "restored as a merge commit naming gui/1" || ko "no merge commit: $(g log --oneline -2 | tr '\n' ';')"
[ -z "$(g status --porcelain)" ] && ok "checkout left clean" || ko "dirty checkout: $(g status --porcelain)"
rm -rf "$T"; exit $fail
