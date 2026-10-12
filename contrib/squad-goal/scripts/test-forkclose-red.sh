#!/bin/bash
# Regression test for #1413 and #1848: a fork-only request asks the child to post its TDD RED (then GREEN) output
# on the issue, and forkclose.sh refuses the landing of a branch whose issue has no RED comment (ADR-0136 §4),
# announcing it once per issue and leaving the wording of the warning it replaces.
# FC_DRY, stub gh, throwaway fork repo. usage: bash test-forkclose-red.sh
d=$(cd "$(dirname "$0")" && pwd); src=$d/forkclose.sh; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# 1: the fork request carries the RED-on-issue rule
for w in "TDD RED" "comment on the GitHub issue" "GREEN"; do $(command -v grep) -qF -- "$w" "$d/req-checklist-fork.txt" && ok "req-checklist-fork.txt carries '$w'" || ko "req-checklist-fork.txt lacks '$w'"; done
# 2: the warning
mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/co/runs/.hub" "$T/fork"; cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"   # the land resolves the branch through forkonly.sh (#1836)
printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
G=/usr/bin/git; g() { $G -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 0 > "$T/fork/f"; g add f; g commit -q -m init
for n in 1 2; do g checkout -q -b gui/$n local/next; echo $n > "$T/fork/f$n"; g add .; g commit -q -m "work $n"; done; g checkout -q local/next
echo '{"items":[{"n":1,"st":"Code Complete","s":"open"},{"n":2,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"pulls?state=open"*) ;;
  *"issues/1/comments"*) echo 'TDD RED (test-x.sh, failure line): FAIL y';;
  *"issues/2/comments"*) echo 'unrelated chatter';;
  *"issues/"*" -q .body"*) ;;
  *"issues/"*" -q .state"*) echo open;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
r=$(FC_DRY=1 bash "$T/s/forkclose.sh" 2>&1)
case "$r" in *"FORKCLOSE REFUSE #2"*"no TDD RED comment on the issue for gui/2"*) ok "no RED comment: the refusal fires, same wording";; *) ko "no refusal for #2: '$r'";; esac
case "$r" in *"REFUSE #1"*) ko "a RED comment is there, yet #1 was refused: '$r'";; *) ok "a RED comment: silent";; esac
case "$r" in *"would merge gui/2 "*) ko "the refusal did not block the landing: '$r'";; *) ok "the refusal blocks the landing";; esac
case "$r" in *"would merge gui/1 "*) ok "the branch that carries its RED still lands";; *) ko "gui/1 was blocked too: '$r'";; esac
rm -rf "$T"; exit $fail
