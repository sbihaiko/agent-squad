#!/bin/bash
# seconds: 0.22
# Regression test for #8 (ADR-0277): check-gh-interface.sh refuses a live loop script that reaches GitHub with a call
# site of its own, naming the script and the route -- a write exactly as a read -- and lets a caller through the door
# go. It runs from shell-suites.sh beside this script, and that is what gates a branch: startability.sh runs the suites
# for a branch that edits contrib/squad-goal/scripts, which is exactly the branch that could add a call site.
#
# The last case runs the tree this suite ships in, so the rule is checked on the real scripts and not only on fixtures:
# no script may be above its line in gh-direct-calls.txt, and a script the file does not name may not reach GitHub at
# all. Nothing here touches the network, a model or a board: the fixtures are text, and the check only reads.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
check="$here/check-gh-interface.sh"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok() { echo "  ok: $1"; }
bad() { echo "  FAILED: $1"; fails=$((fails + 1)); }

mkdir -p "$T/tree/contrib/squad-goal/scripts"

# A live script that reads GitHub itself: named, with the route it reads, and told which reach it was.
printf '#!/bin/bash\no=$(gh api "repos/o/r/pulls?state=open&per_page=100" -q %s.[].number%s) || exit 1\n' "'" "'" \
  > "$T/tree/contrib/squad-goal/scripts/reader.sh"
out=$(bash "$check" "$T/tree" 2>&1); [ $? -eq 1 ] || bad "a direct read must be refused"
case $out in *reader.sh*) ok "the refusal names the script";; *) bad "the refusal names the script: $out";; esac
case $out in *"repos/o/r/pulls?state=open&per_page=100"*) ok "the refusal names the route";; *) bad "the route: $out";; esac
case $out in *"read repos"*) ok "the refusal says which reach it was";; *) bad "the reach: $out";; esac

# A write behind the interface's back: the same refusal, because a refused write looks like success.
rm "$T/tree/contrib/squad-goal/scripts/reader.sh"
printf '#!/bin/bash\ngh api -X PATCH repos/o/r/issues/7 -f state=closed >/dev/null 2>&1 && touch done\n' \
  > "$T/tree/contrib/squad-goal/scripts/writer.sh"
out=$(bash "$check" "$T/tree" 2>&1); [ $? -eq 1 ] || bad "a direct write must be refused"
case $out in *"writer.sh"*"repos/o/r/issues/7"*) ok "the write is refused by name and route";; *) bad "the write: $out";; esac
case $out in *"write"*) ok "and says it was a write";; *) bad "the write's kind: $out";; esac

# A caller through the door is clean, and the budget file is a ceiling: one reach past the line is refused.
rm "$T/tree/contrib/squad-goal/scripts/writer.sh"
printf '#!/bin/bash\n. "$(dirname "$0")/ghcall.sh"\ngh_api "repos/o/r/pulls?state=open" -q %s.[].number%s\ngh_send -X POST repos/o/r/issues/7/comments -F body=x\n' "'" "'" \
  > "$T/tree/contrib/squad-goal/scripts/caller.sh"
out=$(bash "$check" "$T/tree" 2>&1); [ $? -eq 0 ] || bad "a caller through the door is clean: $out"
case $out in *ok:*) ok "a caller through the door is clean";; *) bad "the clean line: $out";; esac
printf 'caller.sh 0\n' > "$T/tree/contrib/squad-goal/scripts/gh-direct-calls.txt"
printf '#!/bin/bash\n. "$(dirname "$0")/ghcall.sh"\ngh_api repos/o/r/pulls?state=open\ngh api repos/o/r/issues?state=open\n' \
  > "$T/tree/contrib/squad-goal/scripts/caller.sh"
out=$(bash "$check" "$T/tree" 2>&1); [ $? -eq 1 ] || bad "one reach past the line must be refused: $out"

# The tree this suite ships in: the live scripts, against the budget committed beside them.
out=$(bash "$check" "$here/../../.." 2>&1); [ $? -eq 0 ] || bad "the live tree reaches GitHub over its budget: $out"
case $out in *ok:*) ok "the live tree is within its migration budget";; *) bad "the live tree: $out";; esac

[ $fails -eq 0 ] && echo "test-check-gh-interface: all checks green" || { echo "test-check-gh-interface: $fails failed"; exit 1; }
