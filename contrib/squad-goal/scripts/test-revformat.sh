#!/bin/bash
# Regression tests for revformat.py, the shape of a squad review (#1777): line 1 VERDICT, then a
# ## Blocking / ## Debt section (whichever is non-empty), one bullet per finding of at most two
# lines "file:line — defect — concrete failure — fix", themes under ### headings, most severe first.
# No network, no model. usage: bash test-revformat.sh [path/to/revformat.py]
src=${1:-$(dirname "$0")/revformat.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
good() { cat > "$T/r.md"; python3 "$src" "$@" "$T/r.md"; }   # args go before the file

# 1) the shape of a small review: verdict, Blocking then Debt, a theme heading, two-line bullets
out=$(good --max-lines 60 <<'M'
VERDICT: CHANGES
## Blocking
### The fix does not hold under an empty input
- src/a.py:12 — null deref — an empty list raises TypeError on the first request — return [] when the list is empty
## Debt
- src/b.py:42 — the retry loop has no backoff
  — it hammers the API after a 500 — add exponential backoff and a test for it
M
); rc=$?
[ $rc = 0 ] && ok "a conforming checklist passes" || ko "conforming checklist rejected: rc=$rc '$out'"

# 2) prose: the review this ticket is about (narration, numbered findings, no sections) is a format failure
out=$(good --max-lines 60 <<'M'
I checked the delta first and the change looks reasonable overall.
VERDICT: CHANGES
1. BLOCKING src/a.py:12 - null deref - an empty list raises - return []
2. DEBT src/b.py:42 - naming - none - rename
In summary, the change is close but needs another round.
M
); rc=$?
[ $rc = 1 ] && [ -n "$out" ] && ok "a prose review fails with a reason" || ko "prose review accepted (rc=$rc)"

# 3) the verdict must be line 1, in the exact form the loop parses
out=$(good --max-lines 60 <<'M'
VERDICT: approved
## Debt
- src/b.py:42 — naming — nobody can tell what it names — rename it
M
); [ $? = 1 ] && ok "a verdict that is not APPROVE|CHANGES fails" || ko "lowercase verdict accepted"
printf 'A preamble.\nVERDICT: CHANGES\n' | python3 "$src" --max-lines 60 - > "$T/o" 2>&1; [ $? = 1 ] && ok "a preamble before the verdict fails" || ko "preamble accepted"

# 4) every finding sits in a section, and the sections come most severe first
printf 'VERDICT: CHANGES\n- src/a.py:1 — x — y — z\n' > "$T/r.md"; python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1
[ $? = 1 ] && ok "a bullet outside any section fails" || ko "sectionless bullet accepted"
printf 'VERDICT: CHANGES\n## Debt\n- src/a.py:1 — x — y — z\n## Blocking\n- src/b.py:2 — x — y — z\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1; [ $? = 1 ] && ok "Debt before Blocking fails (most severe first)" || ko "Debt before Blocking accepted"

# 5) a bullet is at most two lines, and it names a file:line
printf 'VERDICT: CHANGES\n## Debt\n- src/a.py:1 — x — y — z\n  one more line\n  and one more\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1; [ $? = 1 ] && ok "a bullet over two lines fails" || ko "three-line bullet accepted"
printf 'VERDICT: CHANGES\n## Debt\n- the retry loop has no backoff — it hammers the API — add backoff\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1; [ $? = 1 ] && ok "a bullet without file:line fails" || ko "locationless bullet accepted"

# 6) the verdict may not contradict the review, and unknown headings are not part of the shape
printf 'VERDICT: APPROVE\n## Blocking\n- src/a.py:1 — x — y — z\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1; [ $? = 1 ] && ok "APPROVE with a Blocking finding fails" || ko "APPROVE + Blocking accepted"
printf 'VERDICT: CHANGES\n# Summary\n## Debt\n- src/a.py:1 — x — y — z\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1; [ $? = 1 ] && ok "an unknown heading fails" || ko "unknown heading accepted"
: > "$T/r.md"; python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1; [ $? = 1 ] && ok "an empty review fails" || ko "empty review accepted"
printf 'VERDICT: APPROVE\n' > "$T/r.md"; python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1
[ $? = 0 ] && ok "an APPROVE with no findings is the whole review" || ko "bare APPROVE rejected"

# 7) the length cap is the caller's (review.sh reads review_max_lines from models.json): a review over it fails
long=$(printf 'VERDICT: CHANGES\n## Debt\n'; for i in $(seq 1 30); do printf -- '- src/a.py:%s — defect %s — failure — fix\n' "$i" "$i"; done)
printf '%s\n' "$long" > "$T/r.md"; out=$(python3 "$src" --max-lines 20 "$T/r.md" 2>&1); rc=$?
[ $rc = 1 ] && case "$out" in *20*) ok "over the cap fails and names the cap";; *) ko "cap not named: '$out'";; esac || ko "over-cap review accepted"
printf '%s\n' "$long" | python3 "$src" --max-lines 60 /dev/stdin >/dev/null 2>&1; [ $? = 0 ] && ok "the same review passes a wider cap" || ko "the cap is not honoured"

# 8) --summary is what autopilot.sh asks instead of grepping the tags (ADR-0273 Decision 3/5): the bullets per section
printf 'VERDICT: CHANGES\n## Debt\n- src/a.py:1 — x — y — z\n- src/b.py:2 — x — y — z\n' > "$T/r.md"
[ "$(python3 "$src" --summary "$T/r.md")" = "blocking=0 debt=2" ] && ok "--summary counts the Debt bullets" || ko "--summary: '$(python3 "$src" --summary "$T/r.md")'"
printf 'VERDICT: CHANGES\n## Blocking\n- src/a.py:1 — x — y — z\n## Debt\n- src/b.py:2 — x — y — z\n' > "$T/r.md"
[ "$(python3 "$src" --summary "$T/r.md")" = "blocking=1 debt=1" ] && ok "--summary counts both sections" || ko "--summary both: '$(python3 "$src" --summary "$T/r.md")'"
printf 'VERDICT: CHANGES\n1. DEBT src/a.py:1 - x\n' > "$T/r.md"
[ "$(python3 "$src" --summary "$T/r.md")" = "blocking=0 debt=0" ] && ok "--summary does not read the prose shape" || ko "--summary prose: '$(python3 "$src" --summary "$T/r.md")'"
# --summary is a reading of the review, not a check of it: sections in the wrong order (a shape failure) still count
printf 'VERDICT: CHANGES\n## Debt\n- src/a.py:1 — x — y — z\n## Blocking\n- src/b.py:2 — x — y — z\n' > "$T/r.md"
[ "$(python3 "$src" --summary "$T/r.md")" = "blocking=1 debt=1" ] && ok "--summary counts a review whose sections are out of order" || ko "--summary out of order: '$(python3 "$src" --summary "$T/r.md")'"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1; [ $? = 1 ] && ok "the same review still fails the check" || ko "out-of-order sections accepted"

# 9) --normalize is what review.sh saves: the verdict canonical on line 1 (bold, wrong case or after narration);
# what stood in the way of the shape stays in the text, where the check reports it
norm() { printf '%b' "$1" > "$T/r.md"; python3 "$src" --normalize "$T/r.md"; }
[ "$(norm '**VERDICT: Changes**\n## Debt\n- a.cs:1 — x — y — z\n')" = "$(printf 'VERDICT: CHANGES\n## Debt\n- a.cs:1 — x — y — z')" ] && ok "--normalize canonicalizes a bold, lower-case verdict" || ko "--normalize: '$(norm '**VERDICT: Changes**\n## Debt\n')'"
[ "$(norm 'Narration first.\nVERDICT: APPROVE\n')" = "$(printf 'VERDICT: APPROVE\nNarration first.')" ] && ok "--normalize moves the verdict up and keeps the narration" || ko "--normalize narration: '$(norm 'Narration first.\nVERDICT: APPROVE\n')'"
[ "$(norm 'no verdict here\n')" = "no verdict here" ] && ok "--normalize leaves a verdictless review alone (the check then says so)" || ko "--normalize verdictless: '$(norm 'no verdict here\n')'"

# 10) #1832: a bullet that answers a previous finding with 'fixed' is the answer to a finding, not a finding -- so a
# fix round whose every Blocking previous finding is fixed may end APPROVE, and the soft path (ADR-0273 Decision 3/5)
# reads blocking=0 for it. A bullet saying 'not fixed' keeps the severity it was raised with (#1777), and so does one
# with no marker at all (a full review's Blocking bullet).
fx() { printf '%b' "$1" > "$T/r.md"; python3 "$src" --max-lines 60 "$T/r.md"; }
out=$(fx 'VERDICT: APPROVE\n## Blocking\n- src/a.py:12 — null deref — fixed: the guard returns [] now — tests/test_a.py::empty_list\n- src/b.py:42 — the handle leaks — fixed: it is closed on every path — tests/test_b.py::leaks\n## Debt\n- src/c.py:7 — the retry loop has no backoff — not fixed: it still hammers a 500\n')
[ $? = 0 ] && ok "APPROVE with every Blocking bullet fixed is the shape (#1832)" || ko "APPROVE + fixed Blocking rejected: '$out'"
printf 'VERDICT: APPROVE\n## Blocking\n- src/a.py:12 — null deref — not fixed: the guard still returns None\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1
[ $? = 1 ] && ok "APPROVE with a 'not fixed' Blocking bullet still fails" || ko "APPROVE + 'not fixed' Blocking accepted"
printf 'VERDICT: APPROVE\n## Blocking\n- src/a.py:12 — null deref — an empty list raises — return []\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1
[ $? = 1 ] && ok "APPROVE with an unmarked Blocking bullet still fails" || ko "APPROVE + unmarked Blocking accepted"
printf 'VERDICT: APPROVE\n## Blocking\n- src/a.py:12 — null deref — fixed: the guard returns [] — tests/test_a.py\n## Debt\n- src/c.py:7 — the retry loop has no backoff — not fixed: it still hammers a 500\n' > "$T/r.md"
[ "$(python3 "$src" --summary "$T/r.md")" = "blocking=0 debt=1" ] && ok "--summary sees blocking=0 once every Blocking bullet is fixed" || ko "--summary fix round: '$(python3 "$src" --summary "$T/r.md")'"
printf 'VERDICT: CHANGES\n## Blocking\n- src/a.py:12 — null deref — fixed: the guard returns [] — tests/test_a.py\n- src/b.py:42 — the handle leaks — not fixed: still open on the error path\n## Debt\n- src/c.py:7 — the retry loop has no backoff — fixed: it backs off now — tests/test_c.py\n' > "$T/r.md"
[ "$(python3 "$src" --summary "$T/r.md")" = "blocking=1 debt=0" ] && ok "--summary counts only the unfixed bullets" || ko "--summary mixed round: '$(python3 "$src" --summary "$T/r.md")'"

# 11) #1834: a fix round answers its previous findings in a '## Resolved' section of their own, accepted only in
# fix-round mode (--fix-round). The answered finding moves there with the same bullet, so it stays auditable; the
# section is an answer, not a severity, so neither the check nor --summary counts it. A full review (no --fix-round)
# has findings, not answers: the heading is rejected there, named as such, and the verdict stays unusable.
acc='VERDICT: APPROVE\n## Resolved\n- src/a.py:12 — null deref — fixed: the guard returns [] now — tests/test_a.py::empty_list\n- src/b.py:42 — the handle leaks — fixed: it is closed on every path — tests/test_b.py::leaks\n## Debt\n- src/c.py:7 — the retry loop has no backoff — not fixed: it still hammers a 500\n'
fxr() { printf '%b' "$1" > "$T/r.md"; python3 "$src" --fix-round --max-lines 60 "$T/r.md"; }
out=$(fxr "$acc")
[ $? = 0 ] && ok "a fix round whose every Blocking finding sits under '## Resolved' may APPROVE (#1834)" || ko "APPROVE with '## Resolved' rejected: '$out'"
printf '%b' "$acc" > "$T/r.md"
[ "$(python3 "$src" --summary "$T/r.md")" = "blocking=0 debt=1" ] && ok "--summary reads blocking=0 for it and counts the Debt bullet" || ko "--summary resolved: '$(python3 "$src" --summary "$T/r.md")'"
printf '%b' "$acc" > "$T/r.md"
out=$(python3 "$src" --max-lines 60 "$T/r.md"); rc=$?
[ $rc = 1 ] && case "$out" in *Resolved*fix*) ok "a full review may not write '## Resolved', and the reason names it as a fix round's";; *) ko "the reason does not name '## Resolved' as a fix round's: '$out'";; esac || ko "'## Resolved' accepted outside a fix round (rc=$rc)"
printf 'VERDICT: APPROVE\n## Blocking\n- src/b.py:42 — the handle leaks — not fixed: still open on the error path\n## Resolved\n- src/a.py:12 — null deref — fixed: the guard returns [] — tests/test_a.py\n## Debt\n- src/c.py:7 — the retry loop has no backoff — not fixed: it still hammers a 500\n' > "$T/r.md"
out=$(python3 "$src" --fix-round --max-lines 60 "$T/r.md"); rc=$?
[ $rc = 1 ] && ok "a 'not fixed' finding stays under '## Blocking' and still blocks the APPROVE" || ko "APPROVE + 'not fixed' Blocking accepted with Resolved (rc=$rc)"
[ "$(python3 "$src" --summary "$T/r.md")" = "blocking=1 debt=1" ] && ok "--summary counts the 'not fixed' Blocking finding and the Debt bullet, never a resolved one" || ko "--summary fix round: '$(python3 "$src" --summary "$T/r.md")'"
printf 'VERDICT: CHANGES\n## Resolved\n- src/a.py:12 — null deref — fixed: the guard returns [] — tests/test_a.py\n## Blocking\n- src/b.py:42 — the handle leaks — it is open on every error path — close it there\n' > "$T/r.md"
out=$(python3 "$src" --fix-round --max-lines 60 "$T/r.md")
[ $? = 0 ] && ok "the delta may still add a new Blocking finding beside the resolved ones" || ko "new Blocking finding beside '## Resolved' rejected: '$out'"
printf 'VERDICT: CHANGES\n## Resolved\n- src/a.py:12 — null deref — fixed: the guard returns [] — tests/test_a.py\n## Resolved\n- src/b.py:42 — the handle leaks — fixed: it is closed\n' > "$T/r.md"
out=$(python3 "$src" --fix-round --max-lines 60 "$T/r.md")
[ $? = 1 ] && case "$out" in *twice*) ok "'## Resolved' may not appear twice";; *) ko "the duplicate reason is not the shape's: '$out'";; esac || ko "two '## Resolved' sections accepted"

rm -rf "$T"; exit $fail
