#!/bin/bash
# merge-audit.sh <file> — run in a repo mid-merge, after the hand resolution and before the merge commit.
# A line both parents hold (git show :2:<file>, :3:<file>) is a line neither parent dropped; the resolution
# may not drop it. Keeping one side's text over the other's is fine; deleting shared text is a fabrication.
# Text level: no network, no gate. bash -n proves nothing about a deleted statement.
# Exit: 0 clean, 1 a shared line was dropped (names file:line), 2 nothing to audit or bad use.
f=${1:-}; [ -n "$f" ] || { echo "usage: merge-audit.sh <file>" >&2; exit 2; }
ours=$(mktemp); theirs=$(mktemp); trap 'rm -f "$ours" "$theirs"' EXIT
have2=0; have3=0
git show ":2:$f" > "$ours" 2>/dev/null && have2=1
git show ":3:$f" > "$theirs" 2>/dev/null && have3=1
[ $have2 = 0 ] && [ $have3 = 0 ] && { echo "merge-audit: $f has no conflict stages (:2: and :3:); nothing to audit" >&2; exit 2; }
[ $have2 = 1 ] && [ $have3 = 1 ] || exit 0   # one parent has no such file: no line is shared
res=$f; [ -f "$res" ] || res=/dev/null
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
