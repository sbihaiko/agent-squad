#!/bin/bash
# seconds: 1.48
# Rule P (#1830): a landing that was later unwound leaves a `fork-<n>-<sha9>` marker claiming a delivery the fork base
# no longer has. No other rule owns that state: rule O releases a FAILED run that landed nothing, and a close pass can
# only merge a branch that is ahead of the base. The card goes back to the dispatchable column and the markers are
# dropped, so the frontier picks the issue up again.
#  (A) the marked commit left the base and its change is gone from the base -> card Todo, fork/disp/bug markers dropped;
#  (B) the healthy post-land state: the marked commit is IN the base -> rule P leaves it, and the board pass writes no card
#      for it (#239: a landed-branch marker is not a merged pull request naming the issue on the base, so it is not Done);
#  (C) a rebased head: the marked commit is not in the base but the base holds its content -> rule P leaves it (no false positive), no card written;
#  (D) a fix is coming: the branch is still ahead of the base -> untouched by both, the close retries on the new head.
# Stubbed gh/board; a throwaway fork repo and state dir, so the live fork, markers and board are never read or written.
# usage: bash test-janitor-rule-p.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
a="$*"
case "$a" in
  *search/issues*) case "$a" in *"-X GET"*|*"--method GET"*) ;; *) printf '{\n  "message": "Not Found"\n}\n'; exit 1;; esac; echo 0;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/1/fields*) echo FID1;;
  *projectsV2/4/items*) for n in 901 902 903 904; do printf '%s\tIn Progress\n' "$n"; done;;   # one read, every card
  *projectsV2/1/items*) for n in 901; do printf '%s\tDoing\n' "$n"; done;;
  *issues/9*) echo open;;
esac
J
sed -i '' "s|TMPDIR|$T|" "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# --- a throwaway fork: $SG_FORK_BASE is local/next, the branch template gui/%n, one commit per case
F="$T/fork"; G="/usr/bin/git -C $F"; mkdir -p "$F"; $G init -q -b local/next
$G -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
ci() { echo "$2" > "$F/$1"; $G add "$1"; $G -c user.email=t@t -c user.name=t commit -q -m "$3"; $G rev-parse HEAD; }
# A: gui/901's commit is not in the base, its file never reached the base, and the branch was put back on the base
$G checkout -q -b gui/901; sha901=$(ci f901.txt one "work 901")
$G checkout -q local/next
echo x > "$F/note.txt"; $G add note.txt; $G -c user.email=t@t -c user.name=t commit -q -m 'Revert "Merge gui/901 (Refs #901)"'
$G branch -f gui/901 local/next
# B: landed for real -- the marked commit is an ancestor of the base
$G checkout -q -b gui/902 local/next; sha902=$(ci f902.txt two "work 902"); $G checkout -q local/next; $G merge -q --no-ff -m "Merge gui/902" gui/902
# C: rebased head -- the marked commit is orphaned, but the base carries the same change
$G checkout -q -b gui/903 local/next; sha903=$(ci f903.txt three "work 903")
$G -c user.email=t@t -c user.name=t commit -q --amend -m "work 903 (amended)"; $G checkout -q local/next; $G merge -q --no-ff -m "Merge gui/903" gui/903
# D: a fix is coming -- the marked commit is the branch head and the branch is ahead of the base
$G checkout -q -b gui/904 local/next; sha904=$(ci f904.txt four "work 904"); $G checkout -q local/next
for p in 901:"$sha901" 902:"$sha902" 903:"$sha903" 904:"$sha904"; do echo marker > "$T/st/auto/fork-${p%%:*}-$(printf '%s' "${p#*:}" | cut -c1-9)"; done
for n in 901 902 903 904; do echo run-$n > "$T/st/auto/disp-$n"; done
echo run-901 > "$T/st/auto/bug-901"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BUG_PROJECT=1 SG_TODO=Todo SG_BUG_DOING=Doing \
  SG_BUG_TRIAGE="To triage" SG_BASE=main SG_HUB_DIR="$T/st" SG_FORK_DIR="$F" SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%n \
  JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1)
/usr/bin/grep -q '^o 4 Todo 901$' "$T/board.calls" 2>/dev/null && ok "P: unwound landing -> card back to Todo" || ko "P #901 not released: $(cat "$T/board.calls" 2>/dev/null) $o"
/usr/bin/grep -q '^o 1 To triage 901$' "$T/board.calls" 2>/dev/null && ok "P: a bug issue goes back to triage on the bug board" || ko "P #901 bug card kept: $(cat "$T/board.calls" 2>/dev/null)"
{ [ ! -e "$T/st/auto/disp-901" ] && [ ! -e "$T/st/auto/bug-901" ] && [ ! -e "$T/st/auto/fork-901-${sha901:0:9}" ]; } \
  && ok "P: unwound landing -> fork/disp/bug markers dropped" || ko "P markers kept: $(ls "$T/st/auto" | tr '\n' ' ')"
/usr/bin/grep -q 'UNWOUND #901' "$T/st/cycle.log" 2>/dev/null && ok "P: reported the unwound landing" || ko "P cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -l 'UNWOUND\|unwound' "$T/st/cq/"*901* >/dev/null 2>&1 && /usr/bin/grep -q "${sha901:0:9}" "$T/st/cq/"*901* \
  && ok "P: comment names the commit that left the base" || ko "P comment: $(ls "$T/st/cq" 2>/dev/null)"
[ -e "$T/st/auto/disp-902" ] && [ -e "$T/st/auto/fork-902-${sha902:0:9}" ] && ! /usr/bin/grep -q '^o 4 [A-Za-z ]* 902$' "$T/board.calls" 2>/dev/null \
  && ok "P: a landed marker whose commit is in the base writes no card, so it is never Done" || ko "P #902 wrong: $(cat "$T/board.calls" 2>/dev/null) $o"
[ -e "$T/st/auto/disp-903" ] && ! /usr/bin/grep -q '^o 4 [A-Za-z ]* 903$' "$T/board.calls" 2>/dev/null \
  && ok "P: a rebased head whose change is in the base writes no card, so it is never Done" || ko "P #903 released by mistake: $(cat "$T/board.calls" 2>/dev/null)"
[ -e "$T/st/auto/disp-904" ] && ! /usr/bin/grep -q ' 904$' "$T/board.calls" 2>/dev/null \
  && ok "P: a branch still ahead of the base is left to the close pass" || ko "P #904 released by mistake: $(cat "$T/board.calls" 2>/dev/null)"
rm -rf "$T"; exit $fail
