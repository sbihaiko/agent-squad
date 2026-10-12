#!/bin/bash
# merge-audit.sh <file> [<merge-commit>] — a line both parents hold is a line neither parent dropped, and the resolution
# may not drop it. Keeping one side's text over the other's is fine; deleting shared text is a fabrication.
# Two modes, one rule:
#   no commit: run in a repo mid-merge, after the hand resolution and before the merge commit (:2:/:3: are the parents);
#   a merge commit: the two parents are the sides and the commit's own tree is the resolution, so a merge already
#   committed -- a child's conflict fix, a candidate the pass built -- is audited after the fact.
# Text level: no network, no gate. bash -n proves nothing about a deleted statement.
# Exit: 0 clean, 1 a shared line was dropped (names file:line), 2 nothing to audit or bad use.
f=${1:-}; [ -n "$f" ] || { echo "usage: merge-audit.sh <file> [<merge-commit>]" >&2; exit 2; }
c=${2:-}
ours=$(mktemp); theirs=$(mktemp); res=$(mktemp); trap 'rm -f "$ours" "$theirs" "$res"' EXIT
have2=0; have3=0
if [ -n "$c" ]; then
  [ -n "$(git rev-parse -q --verify "$c^2" 2>/dev/null)" ] || { echo "merge-audit: $c is not a merge commit of this repository: there are no two parents to hold a line" >&2; exit 2; }
  git show "$c^1:$f" > "$ours" 2>/dev/null && have2=1
  git show "$c^2:$f" > "$theirs" 2>/dev/null && have3=1
  git show "$c:$f" > "$res" 2>/dev/null || : > "$res"   # absent from the resolution: every shared line is dropped
else
  git show ":2:$f" > "$ours" 2>/dev/null && have2=1
  git show ":3:$f" > "$theirs" 2>/dev/null && have3=1
fi
[ $have2 = 0 ] && [ $have3 = 0 ] && { echo "merge-audit: $f has no conflict stages (:2: and :3:)${c:+ and no parent of $c carries it}; nothing to audit" >&2; exit 2; }
[ $have2 = 1 ] && [ $have3 = 1 ] || exit 0   # one parent has no such file: no line is shared
[ -n "$c" ] || { : > "$res"; [ -f "$f" ] && cat "$f" > "$res"; }   # mid-merge: the working tree is the resolution
# A shared line is kept as often as the parents hold it (the fewer of the two counts); blank lines do not count.
awk -v f="$f" '
  FILENAME == ARGV[1] { n2++; l2[n2] = $0; c2[$0]++; next }
  FILENAME == ARGV[2] { c3[$0]++; next }
  { cr[$0]++ }
  END {
    for (i = 1; i <= n2; i++) {
      t = l2[i]; if (t ~ /^[ \t]*$/) continue
      need = (c2[t] < c3[t]) ? c2[t] : c3[t]
      if (need == 0 || cr[t] >= need) continue
      cr[t]++   # one occurrence is accounted for; the next missing one is reported again
      printf "merge-audit: %s:%d: the resolution dropped a line both parents hold: %s\n", f, i, t; bad = 1
    }
    exit bad
  }' "$ours" "$theirs" "$res" >&2
