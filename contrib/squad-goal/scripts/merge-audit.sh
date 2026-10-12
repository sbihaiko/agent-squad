#!/bin/bash
# usage: merge-audit.sh <file>   (from inside the repository, during a merge conflict and BEFORE `git add <file>`)
#
# Why (#12): a hand resolution may drop a line neither parent dropped. Observed 2026-10-12 while resolving gui/1893 in watch.sh:
# the merged line `echo $$ > "$wlock/pid"; trap 'rm -rf "$wlock"' EXIT` disappeared from the resolution. The script still parsed
# -- `bash -n` proves nothing about a deleted statement -- and the symptom arrived an hour later, in a tree nobody had written:
# `watch.sh already running pid=` with an empty pid, a lock never released, two suites red.
#
# The rule: a line BOTH parents keep and the resolution does not is a fabrication, and the merge is refused before it is
# committed. Keeping one side's text over the other's is NOT a failure: inside a conflict hunk the losing side's lines are meant
# to disappear, and the two-parent test is what separates the two -- only a line the two sides agreed on is a statement the
# resolution invented a deletion for. A resolution that dropped the whole file is the same offence with every line at once.
#
# Text level by construction: no gate, no emulator, no network. The parents are read from the index (`git show :2:<file>` is
# ours, `:3:<file>` is theirs), which is why this runs while the merge is still in progress and the file has NOT been staged
# yet -- after `git add` the three stages collapse into stage 0, there is nothing left to audit, and this says so instead of
# reading as clean (exit 2). Same for a file that is not conflicted at all.
#
# exit 0 = nothing both parents keep is missing; 1 = a fabrication, with the file and the line named; 2 = not auditable.
export LC_ALL=C
f=${1:-}
[ -n "$f" ] || { echo "usage: merge-audit.sh <file>   (during a merge conflict, before \`git add\`)" >&2; exit 2; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git show ":2:$f" > "$tmp/ours" 2>/dev/null; ro=$?
git show ":3:$f" > "$tmp/theirs" 2>/dev/null; rt=$?
if [ $ro != 0 ] && [ $rt != 0 ]; then
  echo "merge-audit: cannot audit $f: the index carries no stage-2/stage-3 entry for it. A merge has to be in progress and the file must not be \`git add\`ed yet -- the two parents exist only while the conflict is unresolved, and an unauditable file is never reported as clean (#12)." >&2; exit 2; fi
if [ $ro != 0 ] || [ $rt != 0 ]; then
  echo "merge-audit: $f is in the index on one side only (an add/delete conflict): no line is kept by both parents, so there is no line this rule can hold the resolution to" >&2; exit 0; fi
sort -u "$tmp/ours" > "$tmp/o.s"; sort -u "$tmp/theirs" > "$tmp/t.s"
comm -12 "$tmp/o.s" "$tmp/t.s" | grep -v '^[[:space:]]*$' > "$tmp/both"   # blank lines are not statements: a resolution may drop one without losing anything
[ -s "$tmp/both" ] || exit 0
if [ -f "$f" ]; then sort -u "$f" > "$tmp/res"; else : > "$tmp/res"; fi
comm -23 "$tmp/both" "$tmp/res" > "$tmp/dropped"
[ -s "$tmp/dropped" ] || exit 0
n=0
while IFS= read -r l; do
  n=$(( n + 1 ))
  on=$(grep -nF -x -m1 -- "$l" "$tmp/ours" | cut -d: -f1); tn=$(grep -nF -x -m1 -- "$l" "$tmp/theirs" | cut -d: -f1)
  printf 'merge-audit: FABRICATION %s: the resolution dropped a line neither parent dropped (ours:%s, theirs:%s):\n+ %s\n' "$f" "${on:-?}" "${tn:-?}" "$l"
done < "$tmp/dropped"
echo "merge-audit: $f: $n line(s) the resolution dropped that neither parent dropped. Both parents kept them, so no side of this merge asked for their removal: put them back, or delete them on the branch that wants them gone and merge again. Nothing was committed; fix the resolution and re-run (#12)." >&2
exit 1
