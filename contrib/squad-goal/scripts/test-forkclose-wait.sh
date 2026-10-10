#!/bin/bash
# Regression test for forkclose.sh's Blocked-by wait (FC_DRY, stub gh, throwaway fork repo). usage: bash test-forkclose-wait.sh [path/to/forkclose.sh]
src=${1:-$(dirname "$0")/forkclose.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$src" "$T/s/forkclose.sh"
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
for n in 1 2; do g checkout -q -b gui/$n local/next; echo $n > "$T/fork/f$n"; g add .; g commit -q -m "work $n"; done; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"},{"n":2,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"pulls?state=open"*) ;;
  *"issues/2 -q .body"*) printf 'Work.\n\n## Blocked by\n\n- #1\n\n## Notes\n- #99\n';;
  *"issues/"*" -q .body"*) ;;
  *"issues/"*" -q .state"*) echo open;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
r=$(FC_DRY=1 bash "$T/s/forkclose.sh" 2>&1)
case "$r" in *"would merge gui/1 "*) ok "the unblocked issue is a candidate";; *) ko "gui/1 not a candidate: '$r'";; esac
case "$r" in *"WAIT #2: the fork half of #1"*) ok "the blocked issue waits and says on whom";; *) ko "no WAIT for #2: '$r'";; esac
case "$r" in *"would merge gui/2"*) ko "the blocked issue was a candidate";; *) ok "the blocked issue is not merged yet";; esac
r=$(FC_DRY=1 bash "$T/s/forkclose.sh" 2>&1); case "$r" in *WAIT*) ko "WAIT repeated: '$r'";; *) ok "WAIT said once per blocker set";; esac
g merge -q --no-ff -m "land 1" gui/1
r=$(FC_DRY=1 bash "$T/s/forkclose.sh" 2>&1); case "$r" in *"would merge gui/2"*) ok "blocker landed: the dependent is a candidate";; *) ko "gui/2 still held: '$r'";; esac
rm -rf "$T"; exit $fail
