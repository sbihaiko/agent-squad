#!/bin/bash
# seconds: 10.21
# Regression tests for revformat.py, the shape of a squad review (#1777): line 1 VERDICT, then a
# ## Blocking / ## Debt section (whichever is non-empty), one bullet per finding of at most two
# lines "file:line — defect — concrete failure — fix", themes under ### headings, most severe first.
# No network, no model. usage: bash test-revformat.sh [path/to/revformat.py]
src=${1:-$(dirname "$0")/revformat.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
good() { cat > "$T/r.md"; python3 "$src" "$@" "$T/r.md"; }   # args go before the file
# #1525: a suite that already runs under a child sandbox (claude-sandboxed.sh) cannot nest one. The probe is the real refusal. When nested, the demotion cases run through the seam double, which keeps sandbox-exec's argv and applies no wall; the wall itself is proven where a wall can apply.
nested=; /usr/bin/sandbox-exec -p '(version 1)(allow default)' /usr/bin/true 2>/dev/null || nested=1
mkdir -p "$T/bin"
cat > "$T/bin/sandbox-pass" <<'J'
#!/bin/sh
[ "$1" = -p ] && [ "$3" = sh ] && [ "$4" = -c ] || { echo "sandbox-pass: unexpected argv: $*" >&2; exit 70; }
shift 2; exec "$@"
J
chmod +x "$T/bin/sandbox-pass"

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
# owner ruling: a preamble before the first section ("I'll review the branch against ...", grok's habit) is dropped, so a
# sound review is not lost to it; prose with no section after it stays, and the check still refuses it.
[ "$(norm "VERDICT: CHANGES\nI'll review the branch against issue #477.\n\n## Blocking\n- a.cs:1 — x — y — z\n")" = "$(printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — x — y — z')" ] && ok "--normalize drops a preamble before the first section" || ko "--normalize preamble: '$(norm "VERDICT: CHANGES\nI'll review it.\n## Blocking\n- a.cs:1 — x — y — z\n")'"
# the cut is the first severity section, never any '#' line; a numbered or '+' finding before it keeps the text whole
[ "$(norm "VERDICT: CHANGES\nI'll review it.\n# Review\n## Blocking\n- a.cs:1 — x — y — z\n")" = "$(printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — x — y — z')" ] && ok "--normalize cuts at the first severity section, not at a title" || ko "--normalize title: '$(norm "VERDICT: CHANGES\nI'll review it.\n# Review\n## Blocking\n- a.cs:1 — x — y — z\n")'"
[ "$(norm "VERDICT: CHANGES\n1. a.cs:1 — x — y — z\n## Blocking\n- b.cs:2 — x — y — z\n")" = "$(printf 'VERDICT: CHANGES\n1. a.cs:1 — x — y — z\n## Blocking\n- b.cs:2 — x — y — z')" ] && ok "--normalize keeps a numbered finding before the first section" || ko "--normalize numbered: '$(norm "VERDICT: CHANGES\n1. a.cs:1 — x — y — z\n## Blocking\n- b.cs:2 — x — y — z\n")'"
[ "$(norm 'VERDICT: APPROVE\nLooks good to me.\n')" = "$(printf 'VERDICT: APPROVE\nLooks good to me.')" ] && ok "--normalize keeps prose that no section follows" || ko "--normalize prose-only: '$(norm 'VERDICT: APPROVE\nLooks good to me.\n')'"
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

# 12) #1835: revformat.py is the single source of the answer shape -- it can print it (--shape, and --fix-round for
# the mode that has an answer section), and review.sh embeds exactly that printed text in the prompt of both review
# modes, so a wording the check cannot read is a red test here instead of a review round lost in the loop. The
# contract is checked next to the validator's other cases, with stubbed gh/claude: no network, no model.
shape() { python3 "$src" --shape "$@"; }
sh_full=$(shape); sh_fix=$(shape --fix-round)
# the shape is a template: its placeholders are the verdict and the file:line of the bullet. Filling them gives the
# smallest answer the prompt asks for, and that answer is what the check has to accept.
fill() { sed -e 's/<APPROVE or CHANGES>/CHANGES/' -e 's/file:line/path\/to\/file.ext:12/g'; }
printf '%s\n' "$sh_full" | fill > "$T/shape.md"; out=$(python3 "$src" "$T/shape.md" 2>&1); rc=$?
[ $rc = 0 ] && ok "the printed full-review shape is an answer the check accepts (#1835)" || ko "the printed full shape is not a review: rc=$rc '$out'"
printf '%s\n' "$sh_fix" | fill > "$T/shape.md"; out=$(python3 "$src" --fix-round "$T/shape.md" 2>&1); rc=$?
[ $rc = 0 ] && ok "the printed fix-round shape is an answer the check accepts (#1835)" || ko "the printed fix shape is not a review: rc=$rc '$out'"
case "$sh_fix" in *'## Resolved'*) ok "the fix-round shape prints the '## Resolved' section (#1834)";; *) ko "the fix shape does not print '## Resolved'";; esac
case "$sh_full" in *'## Resolved'*) ko "the full-review shape prints '## Resolved' (that mode has no answers)";; *) ok "the full-review shape leaves out '## Resolved'";; esac
# the prompt each mode sends, captured from a stubbed run of review.sh (its two halves meet here)
prompt_of() { local W; W=$(mktemp -d); mkdir -p "$W/bin" "$W/st/cq"
  cp "$(dirname "$src")/review.sh" "$W/review.sh"; cp "$(dirname "$src")/loopbug.sh" "$W/"; cp "$(dirname "$src")/ghcall.sh" "$(dirname "$src")/drainpolicy.py" "$(dirname "$src")/ghread.py" "$(dirname "$src")/modelreg.py" "$(dirname "$src")/registry.json" "$W/"; cp "$src" "$W/revformat.py"; : > "$W/codex-reset.py"
  printf '{"review_models":["claude:m"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"review_max_lines":60,"anthropic_models":[]}' > "$W/models.json"
  printf 'SG_REPO=x/y; SG_STATE=%s/st; SG_BASE=main; SG_CHECKOUT=%s; SG_REVIEW_RULES=CLAUDE.md\n' "$W" "$W" > "$W/env.sh"
  cat > "$W/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *Accept*compare*) printf 'diff --git a/f b/f\n+x\n';;
  *compare*) echo '{"status":"ahead","files":[{"filename":"UI/a.cs"}]}';;
  *commits/*) echo '{"sha":"aaa111"}';;
  *issues/*) echo '{"number":8,"title":"t","body":"b"}';;
  *) echo stub;;
esac
J
  cat > "$W/bin/claude" <<'J'
#!/bin/bash
cat > "$CLAUDE_PROMPT"; echo "VERDICT: APPROVE"
J
  chmod +x "$W/bin/gh" "$W/bin/claude"
  [ "$1" = fix ] && { echo ccc333 > "$W/st/review-fix_1-x.sha"; printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — x — y — z\n' > "$W/st/review-fix_1-x.md"; }
  PATH="$W/bin:$PATH" CLAUDE_PROMPT="$W/prompt" bash "$W/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
  cat "$W/prompt" 2>/dev/null; rm -rf "$W"; }
for mode in full fix; do
  want=$sh_full; [ $mode = fix ] && want=$sh_fix
  if [ -z "$want" ]; then ko "$mode: revformat.py --shape printed nothing"; continue; fi
  p=$(prompt_of $mode)
  case "$p" in *"$want"*) ok "$mode: the prompt embeds the shape revformat.py printed, verbatim (#1835)";; *) ko "$mode: the prompt does not embed the printed shape (a hand-written copy is back)";; esac
  case "$p" in *'Unrecognized arguments'*|*'unrecognized arguments'*) ko "$mode: the prompt carries an argparse error instead of the shape";; *) : ;; esac
done

# file-level findings: a defect about code that is missing has no line to cite, so a path alone (bare, in backticks, or with a
# parenthetical) names its place; a bullet with no path at all is still a format failure
out=$(good --max-lines 60 <<'M'
VERDICT: CHANGES
## Blocking
- .github/workflows/ci.yml (concurrency) — a landing can be cancelled — two pushes to dev race — set cancel-in-progress false
- `contrib/squad-goal/scripts/wave.sh` — no caller runs `land` — a finished run never lands its wave — call it at the end of the run
M
); rc=$?
[ $rc = 0 ] && ok "a file-level bullet (a path with no line) passes" || ko "a file-level bullet was rejected: rc=$rc '$out'"
out=$(good --max-lines 60 <<'M'
VERDICT: CHANGES
## Blocking
- the wave land step — no caller runs it — a finished run never lands — call it
M
); rc=$?
[ $rc != 0 ] && ok "a bullet that names no file at all still fails" || ko "a bullet with no file passed"
# a line range or an approximate line is still a place: path:N-M, path:~N, `path:N`, path:N, with a comma after it
out=$(good --max-lines 60 <<'M'
VERDICT: CHANGES
## Blocking
- .github/workflows/ci.yml:~14-19 — a landing can be cancelled — two pushes race — queue the runs
- `tests/test_tooling.py:114-120` — the guard passes wrongly — a cancelled run goes unseen — assert the queue
- squad/x.py:12, squad/y.py:9 — two writers — the record forks — keep one writer
M
); rc=$?
[ $rc = 0 ] && ok "a line range or an approximate line passes" || ko "a line range was rejected: rc=$rc '$out'"

# 13) #443: a finding about the change as a whole -- the branch delivers nothing from its issue, the commit message
# misstates it -- names no file, so the formatter rejected it with MODEL_FORMAT and the branch could never land (the
# same signature #412 fixed for a whole-file concern). A whole-diff bullet now names its place ("whole diff", "whole
# branch", "<x> scope") and passes; a bullet that names no place at all still fails (#412).
out=$(good --max-lines 60 <<'M'
VERDICT: CHANGES
## Blocking
- Whole diff — the branch delivers nothing from #352 — the acceptance criteria stay unmet — retarget the branch to its issues
M
); rc=$?
[ $rc = 0 ] && ok "a whole-diff bullet passes (#443)" || ko "a whole-diff bullet was rejected: rc=$rc '$out'"
out=$(good --max-lines 60 <<'M'
VERDICT: CHANGES
## Debt
- Branch scope — the diff carries two unrelated fixes under one issue — the review cannot pass against #249 — split the branch
- Scope: this branch carries autobug and autorefuse fixes — the issue is not delivered — file the work under its own issue
- Issue #249 scope — the diff delivers none of the issue's acceptance criteria — the issue cannot close on this branch — retarget it
M
); rc=$?
[ $rc = 0 ] && ok "a whole-diff finding phrased as a scope passes (#443)" || ko "a scope-phrased whole-diff bullet was rejected: rc=$rc '$out'"
# the whole-diff bullet is a finding like any other: --summary counts it, and it still blocks an APPROVE
printf 'VERDICT: APPROVE\n## Blocking\n- Whole diff — the branch delivers nothing — the criteria stay unmet — retarget it\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1
[ $? = 1 ] && ok "APPROVE with a whole-diff Blocking finding still fails" || ko "APPROVE + whole-diff Blocking accepted"
printf 'VERDICT: CHANGES\n## Blocking\n- Whole diff — the branch delivers nothing — the criteria stay unmet — retarget it\n' > "$T/r.md"
[ "$(python3 "$src" --summary "$T/r.md")" = "blocking=1 debt=0" ] && ok "--summary counts a whole-diff Blocking bullet (#443)" || ko "--summary whole-diff: '$(python3 "$src" --summary "$T/r.md")'"

# A section with nothing in it says so (#498): a model that has no finding writes "None.", and that is an empty
# section, not a malformed bullet. Only the section's first content, and never after a bullet.
printf 'VERDICT: APPROVE\n## Blocking\nNone.\n## Debt\n- src/a.py:1 — x — y — z\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1
[ $? = 0 ] && ok "an empty section says None." || ko "an empty section's None. is rejected: $(python3 "$src" --max-lines 60 "$T/r.md" 2>&1)"
printf 'VERDICT: APPROVE\n## Blocking\nNone\n## Debt\nNone\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1
[ $? = 0 ] && ok "two empty sections say None" || ko "two empty sections are rejected: $(python3 "$src" --max-lines 60 "$T/r.md" 2>&1)"
printf 'VERDICT: CHANGES\n## Debt\n- src/a.py:1 — x — y — z\nNone.\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1
[ $? = 0 ] && ok "None. after a bullet stays a bullet continuation, never an empty section" || ko "None. after a bullet broke its bullet: $(python3 "$src" --max-lines 60 "$T/r.md" 2>&1)"
printf 'VERDICT: APPROVE\n## Debt\n—\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1
[ $? = 0 ] && ok "an em dash is an empty section too" || ko "an em dash is rejected: $(python3 "$src" --max-lines 60 "$T/r.md" 2>&1)"
# #618: the empty marker may be wrapped: Opus writes "(none)" under a section, and "*None*" or "[none]" are the same word.
for e in '(none)' '*None*' '_none_' '[none]' '(None.)'; do
  printf 'VERDICT: APPROVE\n## Blocking\n%s\n## Debt\n- src/a.py:1 — x — y — z\n' "$e" > "$T/r.md"
  python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1 && ok "an empty section says $e" || ko "an empty section's $e is rejected: $(python3 "$src" --max-lines 60 "$T/r.md" 2>&1)"
done
printf 'VERDICT: APPROVE\n## Blocking\n(see the diff)\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1 && ko "a wrapped word that is not an empty marker passed" || ok "a wrapped word that is not an empty marker stays prose"

# #504: the empty section says None and why, on one line ("None. The four acceptance criteria are met."): Opus and DeepSeek
# both write it, and it was MODEL_FORMAT. A second prose line is still prose.
printf 'VERDICT: APPROVE\n## Blocking\nNone. The four acceptance criteria are met and no ADR is crossed.\n## Debt\n- src/a.py:1 — x — y — z\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1
[ $? = 0 ] && ok "an empty section may say None and why on one line" || ko "None with its reason is rejected: $(python3 "$src" --max-lines 60 "$T/r.md" 2>&1)"
printf 'VERDICT: APPROVE\n## Blocking\nNone. All met.\nThe code is fine.\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1
[ $? = 1 ] && ok "a second prose line after None is still prose" || ko "a second prose line was let in"

# #624: a repo-wide finding names its place as "Repo-wide" (or "repo-wide"), a whole-change label like "whole"; a bullet
# that still names no place fails as before.
for w in 'Repo-wide' 'repo-wide'; do
  printf 'VERDICT: CHANGES\n## Blocking\n- %s — the scripts read no lock — two reviews race — serialize them\n' "$w" > "$T/r.md"
  python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1 && ok "24 a $w bullet is a whole-change location (#624)" || ko "24 a $w bullet was rejected: $(python3 "$src" --max-lines 60 "$T/r.md" 2>&1)"
done
printf 'VERDICT: CHANGES\n## Blocking\n- The code is wide — two reviews race — serialize them\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1 && ko "24 an unlocated bullet passed" || ok "24 an unlocated bullet still fails"

# #625: a location may name a symbol instead of a line (`path:symbol`, backticked whole or on the symbol), and an
# acceptance-criteria finding names its criterion (AC1, AC 2, Acceptance criterion 3, Issue AC ...) as a whole-change
# location. Each bullet below reshapes a line a live review was refused for. A bullet that names no place still fails.
while IFS= read -r b; do
  printf 'VERDICT: CHANGES\n## Blocking\n- %s\n' "$b" > "$T/r.md"
  python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1 && ok "25 passes: ${b:0:44}" || ko "25 rejected: $b: $(python3 "$src" --max-lines 60 "$T/r.md" 2>&1)"
done <<'B'
`squad/launch.py:_router_open` — a second copy of the window rule — two rules drift — call modelreg
squad/modelreg.py:`_route_for` — the sdk use picks the cli route — the gateway gets the wrong id — resolve through the gateway
`tests/test_dispatch.py:test_repo_graphs_name_registry_ids` — the test asserts wire == raw — it passes on the wrong string — assert the served list
squad/modelreg.py:_wire() — the context suffix is dropped — the 1M context is lost — re-append it
AC1 (TDD RED history on dev): the diff adds no RED commit — the history cannot show the test failed first — add the RED commit
AC 2 — the loop behavior changes — v1 promises none — gate it behind an opt-in
Acceptance criterion 3 — the resolver is not the one module — two windows drift — share one rule
Issue AC check: this diff cannot prove "no behavior change" — no test covers the old path — add a regression test
B
printf 'VERDICT: CHANGES\n## Blocking\n- AC1 (TDD RED history on dev): the diff adds no RED commit\n' > "$T/r.md"
python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1 && ko "25 an AC bullet with no fields passed" || ok "25 an AC bullet still needs its fields"
for b in 'Note:something — x — y — z' 'the router: _route_for — x — y — z' 'Accepted — the code is fine — nothing fails — none'; do
  printf 'VERDICT: CHANGES\n## Blocking\n- %s\n' "$b" > "$T/r.md"
  python3 "$src" --max-lines 60 "$T/r.md" >/dev/null 2>&1 && ko "25 an unlocated bullet passed: $b" || ok "25 still fails: ${b:0:30}"
done

# #632: a full review answers in JSON with the #627 schema, and one renderer writes today's markdown from it. --schema
# prints the schema; --answer renders a JSON answer (fenced or bare) and normalizes a markdown one; --keep saves the raw
# JSON. The rendered review passes the shape check, and its --fingerprint is the one of the equivalent markdown answer.
python3 "$src" --schema | jq -e '.properties.verdict.enum == ["APPROVE","CHANGES"] and (.required|index("blocking")) != null' >/dev/null 2>&1 \
  && ok "26 --schema prints the JSON schema of an answer" || ko "26 --schema: $(python3 "$src" --schema 2>&1 | head -2)"
cat > "$T/a.json" <<'J'
{"verdict":"CHANGES","blocking":[{"location":"src/a.py:12","defect":"null deref","failure":"an empty list raises TypeError","fix":"return [] when the list is empty","repro":"false"}],
 "debt":[{"location":"src/b.py:42","defect":"no backoff","failure":"it hammers the API after a 500","fix":"add exponential backoff","repro":""}],"resolved":[]}
J
printf 'VERDICT: CHANGES\n## Blocking\n- src/a.py:12 — null deref — an empty list raises TypeError — return [] when the list is empty — repro: false\n## Debt\n- src/b.py:42 — no backoff — it hammers the API after a 500 — add exponential backoff\n' > "$T/eq.md"
python3 "$src" --answer --keep "$T/kept.json" "$T/a.json" > "$T/r.md" 2>"$T/err"; rc=$?
[ $rc = 0 ] && [ "$(head -1 "$T/r.md")" = "VERDICT: CHANGES" ] && python3 "$src" --max-lines 60 "$T/r.md" >/dev/null \
  && ok "26 a JSON answer renders to a review that passes the shape check" || ko "26 render: rc=$rc $(cat "$T/err") $(cat "$T/r.md")"
[ "$(python3 "$src" --summary "$T/r.md")" = "blocking=1 debt=1" ] && ok "26 the rendered review reads blocking=1 debt=1" || ko "26 summary: $(python3 "$src" --summary "$T/r.md")"
[ -n "$(python3 "$src" --fingerprint "$T/r.md")" ] && [ "$(python3 "$src" --fingerprint "$T/r.md")" = "$(python3 "$src" --fingerprint "$T/eq.md")" ] \
  && ok "26 the rendered --fingerprint equals the equivalent markdown's" || ko "26 fingerprint: $(diff "$T/r.md" "$T/eq.md")"
jq -e '.verdict=="CHANGES"' "$T/kept.json" >/dev/null 2>&1 && ok "26 --keep saves the raw JSON answer" || ko "26 --keep: $(cat "$T/kept.json" 2>&1)"
{ echo 'Here is the review.'; echo '```json'; cat "$T/a.json"; echo '```'; } > "$T/f.json"
python3 "$src" --answer "$T/f.json" > "$T/r2.md" 2>/dev/null && cmp -s "$T/r.md" "$T/r2.md" && ok "26 a fenced JSON answer renders the same" || ko "26 fenced: $(cat "$T/r2.md")"
printf '{"verdict":"APPROVE","blocking":[],"debt":[],"resolved":[]}' > "$T/ap.json"   # the #618 shape: an empty section
python3 "$src" --answer "$T/ap.json" > "$T/r.md" 2>/dev/null && [ "$(cat "$T/r.md")" = "VERDICT: APPROVE" ] && python3 "$src" "$T/r.md" >/dev/null \
  && ok "26 #618: an empty section is an empty list, and passes" || ko "26 #618 empty: $(cat "$T/r.md")"
cat > "$T/625.json" <<'J'
{"verdict":"CHANGES","blocking":[{"location":"squad/modelreg.py:_route_for","defect":"the sdk use picks the cli route","failure":"the gateway gets the wrong id","fix":"resolve through the gateway","repro":"false"},
 {"location":"AC1","defect":"the diff adds no RED commit","failure":"the history cannot show the test failed first","fix":"add the RED commit","repro":"false"}],"debt":[],"resolved":[]}
J
python3 "$src" --answer "$T/625.json" > "$T/r.md" 2>/dev/null && python3 "$src" --max-lines 60 "$T/r.md" >/dev/null && [ "$(python3 "$src" --summary "$T/r.md")" = "blocking=2 debt=0" ] \
  && ok "26 #625: path:symbol and AC locations in JSON pass" || ko "26 #625: $(python3 "$src" "$T/r.md") $(cat "$T/r.md")"
printf '{"verdict":"MAYBE","blocking":[],"debt":[]}' > "$T/bad.json"
out=$(python3 "$src" --answer "$T/bad.json" 2>&1 >/dev/null); rc=$?
[ $rc = 1 ] && case "$out" in *verdict*) true;; *) false;; esac && ok "26 a JSON answer off the schema is refused with the parser's reason" || ko "26 bad JSON: rc=$rc '$out'"
printf '{"verdict":"CHANGES","blocking":[{"location":"a.py:1","defect":"x"}],"debt":[]}' > "$T/bad.json"
python3 "$src" --answer "$T/bad.json" >/dev/null 2>&1 && ko "26 a finding with missing fields passed" || ok "26 a finding with missing fields is refused"
printf '**VERDICT: approve**\n' > "$T/md.md"
[ "$(python3 "$src" --answer "$T/md.md")" = "$(python3 "$src" --normalize "$T/md.md")" ] && ok "26 a markdown answer is normalized as before" || ko "26 markdown: $(python3 "$src" --answer "$T/md.md" 2>&1)"


# #633: a fix round answers in JSON with the same schema. A fixed finding sits in resolved with the "fixed:" marker in its
# failure field, and renders the '## Resolved' section the fix-round check reads; a not-fixed one stays in its section.
# The fingerprint of the rendered fix round is the one of the equivalent markdown fix round (ADR-0134).
cat > "$T/fx.json" <<'J'
{"verdict":"CHANGES","blocking":[{"location":"src/a.py:12","defect":"null deref","failure":"not fixed: the guard is still missing","fix":"an empty list still raises TypeError","repro":"false"}],
 "debt":[],"resolved":[{"location":"src/b.py:42","defect":"no backoff","failure":"fixed: backoff added","fix":"test_backoff passes","repro":""}]}
J
printf 'VERDICT: CHANGES\n## Blocking\n- src/a.py:12 — null deref — not fixed: the guard is still missing — an empty list still raises TypeError — repro: false\n## Resolved\n- src/b.py:42 — no backoff — fixed: backoff added — test_backoff passes\n' > "$T/fxeq.md"
python3 "$src" --answer "$T/fx.json" > "$T/fx.md" 2>"$T/err"; rc=$?
[ $rc = 0 ] && /usr/bin/grep -q '^## Resolved$' "$T/fx.md" && python3 "$src" --max-lines 60 --fix-round "$T/fx.md" >/dev/null \
  && ok "27 a fix-round JSON answer renders the Resolved section the fix-round check reads" || ko "27 fix render: rc=$rc $(cat "$T/err") $(cat "$T/fx.md")"
python3 "$src" --max-lines 60 "$T/fx.md" >/dev/null 2>&1 && ko "27 a full-review check took the Resolved section" || ok "27 the full-review check still refuses Resolved"
[ "$(python3 "$src" --summary "$T/fx.md")" = "blocking=1 debt=0" ] && ok "27 the rendered fix round reads blocking=1 debt=0" || ko "27 summary: $(python3 "$src" --summary "$T/fx.md")"
[ "$(python3 "$src" --fingerprint "$T/fx.md")" = "$(python3 "$src" --fingerprint "$T/fxeq.md")" ] \
  && ok "27 the fix-round fingerprint equals the equivalent markdown's (ADR-0134)" || ko "27 fix fingerprint: $(diff "$T/fx.md" "$T/fxeq.md")"
# 28) one parser classifies a kept review (#1376): branchland.sh reads this answer, never line 1 and never --summary, so a
# review outside the accepted shape holds its branch. cl() compares the answer for a file written by the caller.
printf '%s' "abc1234" > "$T/c.sha"
cl() { local want=$1 f=$2 tip=${3:-abc1234} extra=$4 got
  got=$(python3 "$src" --classify "$f" "$T/c.sha" "$tip" $extra 2>&1)
  [ "$got" = "$want" ] && ok "28 classify $want: $5" || ko "28 classify want=$want got=$got: $5"; }
printf 'VERDICT: APPROVE\n## Blocking\n(none)\n' > "$T/c1.md"; cl approves "$T/c1.md" "" "" "a bare APPROVE with no findings"
printf 'VERDICT: APPROVE (small change; no blocking findings)\n## Blocking\n(none)\n' > "$T/c2.md"
cl approves "$T/c2.md" "" "" "review.sh's legacy VERDICT line still approves, never holds forever"
printf 'VERDICT: CHANGES\n## Blocking\n- src/a.py:12 — null deref — an empty list raises TypeError — return [] when empty\n' > "$T/c3.md"
cl changes "$T/c3.md" "" "" "a CHANGES with a Blocking finding asks for changes"
printf 'VERDICT: CHANGES\n## Blocking\n(none)\n## Debt\n(none)\n' > "$T/c4.md"
cl malformed "$T/c4.md" "" "" "a CHANGES with no Blocking and no Debt is malformed, never an approval"
printf 'Looks good to me overall, ship it.\n' > "$T/c5.md"
cl malformed "$T/c5.md" "" "" "prose outside the shape is malformed"
cl stale "$T/c3.md" deadbee "" "a kept review of another tip is stale"
printf 'VERDICT: CHANGES\n## Blocking\n- src/a.py:12 — null deref — fixed: the guard is added — test_guard passes\n## Resolved\n- src/b.py:42 — no backoff — fixed: backoff added — test_backoff passes\n' > "$T/c6.md"
cl malformed "$T/c6.md" "" "" "a full review that answers findings is malformed"
cl approves "$T/c6.md" "" --fix-round "the same file answers its findings as a fix round"
# The hold a malformed review puts on its branch is never permanent: a corrected file at the same tip approves.
printf 'VERDICT: APPROVE\n## Blocking\n(none)\n' > "$T/c7.md"; cl approves "$T/c7.md" "" "" "a corrected file at the same tip clears the hold"
# 29) #1521: a Blocking finding carries a repro command, one shell line that exits non-zero while the defect is present.
# A blank repro demotes the finding to Debt in the parser, the one reader every caller shares; a set repro stays Blocking
# and renders as a fifth field. A finding with no repro key is off the schema.
printf '{"verdict":"CHANGES","blocking":[{"location":"a.py:1","defect":"x","failure":"y","fix":"z","repro":""},{"location":"b.py:2","defect":"x","failure":"y","fix":"z","repro":"false"}],"debt":[],"resolved":[]}' > "$T/rp.json"
python3 "$src" --answer "$T/rp.json" > "$T/rp.md" 2>/dev/null
[ "$(python3 "$src" --summary "$T/rp.md")" = "blocking=1 debt=1" ] && ok "29 a blank repro demotes its Blocking finding to Debt" || ko "29 blank repro: '$(python3 "$src" --summary "$T/rp.md")'"
/usr/bin/grep -q ' — repro: false$' "$T/rp.md" && ok "29 a set repro renders as a fifth field" || ko "29 repro field: $(cat "$T/rp.md")"
printf '{"verdict":"CHANGES","blocking":[{"location":"a.py:1","defect":"x","failure":"y","fix":"z"}],"debt":[],"resolved":[]}' > "$T/rp2.json"
python3 "$src" --answer "$T/rp2.json" >/dev/null 2>&1 && ko "29 a finding with no repro key passed" || ok "29 a finding with no repro key is refused"
# 30) #1521: --repro runs each Blocking repro at the sha, in the checkout, under the signal wall, and rewrites the answer. A
# repro that exits 0 moves its finding to Debt with "repro passed at <sha>"; one that exits 1 stays Blocking; one that
# cannot run (127 here) moves to Debt with "repro did not run at <sha>".
mkdir -p "$T/co"
printf '{"verdict":"CHANGES","blocking":[{"location":"a.py:1","defect":"x","failure":"y","fix":"z","repro":"true"},{"location":"b.py:2","defect":"x","failure":"y","fix":"z","repro":"false"},{"location":"c.py:3","defect":"x","failure":"y","fix":"z","repro":"no-such-command-1521"}],"debt":[],"resolved":[]}' > "$T/rr.json"
env ${nested:+SQUAD_REPRO_EXEC=$T/bin/sandbox-pass} SQUAD_SIGNAL_PROFILE="(version 1)(allow default)(deny signal)(allow signal (target self))(allow signal (target same-sandbox))" python3 "$src" --repro "$T/rr.json" "$T/co" abc1234 2>"$T/rr.err"; rc=$?
python3 "$src" --answer "$T/rr.json" > "$T/rr.md" 2>/dev/null
[ $rc = 0 ] && [ "$(python3 "$src" --summary "$T/rr.md")" = "blocking=1 debt=2" ] && /usr/bin/grep -q 'repro passed at abc1234' "$T/rr.md" \
  && /usr/bin/grep -q 'repro did not run at abc1234' "$T/rr.md" && ok "30 --repro moves a passing and an unrunnable repro to Debt, and keeps a failing one Blocking" \
  || ko "30 --repro: rc=$rc summary='$(python3 "$src" --summary "$T/rr.md" 2>&1)' $(cat "$T/rr.md" "$T/rr.err")"
# 31) #1521: a plan review (ADR-0171) reads no code, so it names no repro. --answer --plan keeps a Blocking finding with a
# blank repro Blocking; without --plan the same answer is demoted (29).
printf '{"verdict":"CHANGES","blocking":[{"location":"AC1","defect":"x","failure":"y","fix":"z","repro":""}],"debt":[],"resolved":[]}' > "$T/pl.json"
python3 "$src" --answer --plan "$T/pl.json" > "$T/pl.md" 2>"$T/pl.err"; rc=$?
[ $rc = 0 ] && [ "$(python3 "$src" --summary "$T/pl.md")" = "blocking=1 debt=0" ] && ok "31 a plan answer with a blank repro keeps its Blocking finding" \
  || ko "31 plan answer: rc=$rc summary='$(python3 "$src" --summary "$T/pl.md" 2>&1)' $(cat "$T/pl.err")"
# 36) #1521: a repro runs under a write wall: it writes only in the tree it reads and its own scratch directory. A repro
# that writes outside the tree is refused (its touch fails and it exits non-zero), so its finding stays Blocking and the
# file never appears. A write inside the tree still works.
mkdir -p "$T/w36"; rm -f "$T/out-1521"
if [ -z "$nested" ]; then
printf '{"verdict":"CHANGES","blocking":[{"location":"a.py:1","defect":"x","failure":"y","fix":"z","repro":"touch %s/out-1521"}],"debt":[],"resolved":[]}' "$T" > "$T/w36.json"
python3 "$src" --repro "$T/w36.json" "$T/w36" abc1234 2>"$T/w36.err"
python3 "$src" --answer "$T/w36.json" > "$T/w36.md" 2>/dev/null
[ ! -e "$T/out-1521" ] && [ "$(python3 "$src" --summary "$T/w36.md")" = "blocking=1 debt=0" ] \
  && ok "36 a repro cannot write outside its tree, and its finding stays Blocking" \
  || ko "36 write wall: out=$(test -e "$T/out-1521" && echo present || echo absent) summary='$(python3 "$src" --summary "$T/w36.md" 2>&1)' $(cat "$T/w36.err")"
else
# #1525: inside a child sandbox the wall cannot apply, so the repro is refused before it runs: its finding stays Blocking.
rm -f "$T/out-1525"
printf '{"verdict":"CHANGES","blocking":[{"location":"a.py:1","defect":"x","failure":"y","fix":"z","repro":"touch %s/out-1525"}],"debt":[],"resolved":[]}' "$T" > "$T/w36.json"
python3 "$src" --repro "$T/w36.json" "$T/w36" abc1234 2>"$T/w36.err"
python3 "$src" --answer "$T/w36.json" > "$T/w36.md" 2>/dev/null
[ ! -e "$T/out-1525" ] && [ "$(python3 "$src" --summary "$T/w36.md")" = "blocking=1 debt=0" ] && /usr/bin/grep -q 'repro sandbox refused at abc1234' "$T/w36.err" \
  && ok "36 inside a child sandbox the wall cannot apply: the repro never runs and its finding stays Blocking" \
  || ko "36 nested refusal: out=$(test -e "$T/out-1525" && echo present || echo absent) summary='$(python3 "$src" --summary "$T/w36.md" 2>&1)' $(cat "$T/w36.err")"
fi
# 37) #1521: the same wall lets a repro write inside its tree: a file it creates there is read back, so the repro exits 0.
printf '{"verdict":"CHANGES","blocking":[{"location":"a.py:1","defect":"x","failure":"y","fix":"z","repro":"touch inside-1521 && test -f inside-1521"}],"debt":[],"resolved":[]}' > "$T/w37.json"
env ${nested:+SQUAD_REPRO_EXEC=$T/bin/sandbox-pass} python3 "$src" --repro "$T/w37.json" "$T/w36" abc1234 2>"$T/w37.err"
python3 "$src" --answer "$T/w37.json" > "$T/w37.md" 2>/dev/null
[ -e "$T/w36/inside-1521" ] && [ "$(python3 "$src" --summary "$T/w37.md")" = "blocking=0 debt=1" ] \
  && ok "37 a repro writes inside its tree" \
  || ko "37 write inside the tree: summary='$(python3 "$src" --summary "$T/w37.md" 2>&1)' $(cat "$T/w37.err")"
# 38) #1525: a sandbox that cannot apply (rc 71, sandbox_apply) is told apart from a repro that reproduces: the repro "true" would demote its finding, but it never ran, so the finding stays Blocking and the reason names the refusal. Outside a child the suite nests the sandbox itself; inside one the suite's own sandbox is the outer one.
printf '{"verdict":"CHANGES","blocking":[{"location":"a.py:1","defect":"x","failure":"y","fix":"z","repro":"true"}],"debt":[],"resolved":[]}' > "$T/r38.json"
if [ -n "$nested" ]; then python3 "$src" --repro "$T/r38.json" "$T/co" abc1234 2>"$T/r38.err"; else /usr/bin/sandbox-exec -p '(version 1)(allow default)' python3 "$src" --repro "$T/r38.json" "$T/co" abc1234 2>"$T/r38.err"; fi; rc=$?
python3 "$src" --answer "$T/r38.json" > "$T/r38.md" 2>/dev/null; arc=$?
[ $arc = 0 ] && [ "$(python3 "$src" --summary "$T/r38.md")" = "blocking=1 debt=0" ] && /usr/bin/grep -q 'repro sandbox refused at abc1234' "$T/r38.err" && ! /usr/bin/grep -q 'repro passed' "$T/r38.md" \
  && ok "38 a sandbox that cannot apply keeps the finding Blocking and names the refusal, never 'repro passed'" \
  || ko "38 nested refusal: repro rc=$rc answer rc=$arc summary='$(python3 "$src" --summary "$T/r38.md" 2>&1)' err=$(cat "$T/r38.err")"
# 41) #1960: the loop's own rebase-round review has one writer, and the landing pass reads it as changes, never malformed. Its
# Blocking finding names a repro (ADR-0172) that exits non-zero while the branch conflicts with the ref and 0 once it is rebased.
sd=$(cd "$(dirname "$src")" && pwd)
for files in "g a.py" ""; do
  python3 "$src" --round-review dev origin/dev "$files" > "$T/rr.md" 2>"$T/rr.err"; echo tip41 > "$T/rr.sha"
  [ "$(python3 "$src" --classify "$T/rr.md" "$T/rr.sha" tip41 2>&1)" = changes ] && /usr/bin/grep -q 'Rebase this branch on origin/dev,' "$T/rr.md" \
    && ok "41 the round review ('${files:-no path}') classifies as changes" || ko "41 round review ('$files'): '$(python3 "$src" --classify "$T/rr.md" "$T/rr.sha" tip41 2>&1)' $(cat "$T/rr.md" "$T/rr.err" 2>/dev/null)"
done
rp=$(sed -n 's/.* — repro: //p' "$T/rr.md" | head -1)
G=/usr/bin/git; R=$T/rp; $G init -q -b dev "$R" && (cd "$R" && echo base > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m b \
  && $G checkout -q -b w && echo mine > g && $G -c user.email=a@b -c user.name=t commit -q -am w && $G checkout -q dev && echo other > g \
  && $G -c user.email=a@b -c user.name=t commit -q -am d && $G update-ref refs/remotes/origin/dev dev && $G checkout -q w)
(cd "$R" && sh -c "$rp") >/dev/null 2>&1; c1=$?
(cd "$R" && $G checkout -q -b clean origin/dev && sh -c "$rp") >/dev/null 2>&1; c2=$?
[ -n "$rp" ] && [ "$c1" != 0 ] && [ "$c2" = 0 ] && ok "41 the repro fails while the branch conflicts and passes once rebased" || ko "41 repro '$rp': conflict rc=$c1, clean rc=$c2"
n=$(/usr/bin/grep -l "Rebase this branch on" "$sd/wave.sh" "$sd/branchland.sh" 2>/dev/null | wc -l | tr -d ' ')
[ "$n" = 0 ] && ok "41 wave.sh and branchland.sh write no round review of their own" || ko "41 $n script(s) still write the round review text"
# 42) #2565: a repro red at the head also runs at the base (--repro-base TREE SHA), and only then. Red at the head and green
# at the base: Blocking, no note. Red at both: Blocking with a base note on its first round; Debt naming the base and both
# heads when the previous answer (--repro-prev ANSWER SHA) holds the same repro. A base run that proves nothing keeps it Blocking.
mkdir -p "$T/h42" "$T/b42"; : > "$T/h42/headmark"
r42() { local a=$1; shift; env ${nested:+SQUAD_REPRO_EXEC=$T/bin/sandbox-pass} python3 "$src" --repro "$a" "$T/h42" hhh2222 "$@" 2>"$a.err"; }
bl42() { jq -r '.blocking[].failure' "$1" | tr '\n' '|'; }
db42() { jq -r '.debt[].failure' "$1" | tr '\n' '|'; }
f42() { printf '{"location":"%s","defect":"d %s","failure":"y","fix":"z","repro":"%s"}' "$1" "$1" "$2"; }
printf '{"verdict":"CHANGES","blocking":[%s,%s,%s],"debt":[],"resolved":[]}' "$(f42 a.py 'touch a.ran; test ! -f headmark')" \
  "$(f42 b.py 'touch b.ran; test -f headmark')" "$(f42 c.py 'touch c.ran; false')" > "$T/r42.json"
r42 "$T/r42.json" --repro-base "$T/b42" bbb1111; rc=$?
[ $rc = 0 ] && [ "$(jq -r '[.blocking[].location]|join(",")' "$T/r42.json")" = "a.py,c.py" ] && [ "$(bl42 "$T/r42.json")" = "y|repro also fails at base bbb1111; y|" ] \
  && ok "42 a repro red at the base and the head stays Blocking with the base sha; one red only at the head has no note" \
  || ko "42 first round: rc=$rc blocking=$(bl42 "$T/r42.json") $(cat "$T/r42.json.err")"
[ -e "$T/b42/a.ran" ] && [ -e "$T/b42/c.ran" ] && [ ! -e "$T/b42/b.ran" ] && [ "$(ls "$T/b42" | /usr/bin/wc -l | tr -d ' ')" = 2 ] \
  && ok "42 a finding green at the head starts no base run" || ko "42 base runs: $(ls "$T/b42" | tr '\n' ' ')"
# the previous answer holds the same repro, its whitespace changed: the finding moves to Debt and stderr names the demotion
printf '{"verdict":"CHANGES","blocking":[%s],"debt":[],"resolved":[]}' "$(f42 c.py 'touch  c.ran;   false')" > "$T/p42.json"
printf '{"verdict":"CHANGES","blocking":[%s],"debt":[],"resolved":[]}' "$(f42 c.py 'touch c.ran; false')" > "$T/r42b.json"
r42 "$T/r42b.json" --repro-base "$T/b42" bbb1111 --repro-prev "$T/p42.json" ppp0000; rc=$?
[ $rc = 0 ] && [ "$(bl42 "$T/r42b.json")" = "" ] && [ "$(db42 "$T/r42b.json")" = "repro fails at base bbb1111 and at heads ppp0000, hhh2222; y|" ] \
  && /usr/bin/grep -q 'repro fails at base bbb1111 and at heads ppp0000, hhh2222' "$T/r42b.json.err" \
  && ok "42 a repro red at the base and at two heads with the same text moves to Debt, naming the base and both heads" \
  || ko "42 repeat: rc=$rc blocking=$(bl42 "$T/r42b.json") debt=$(db42 "$T/r42b.json") $(cat "$T/r42b.json.err")"
# a reworded repro is a first appearance
printf '{"verdict":"CHANGES","blocking":[%s],"debt":[],"resolved":[]}' "$(f42 c.py 'touch c.ran; false || false')" > "$T/p42w.json"
printf '{"verdict":"CHANGES","blocking":[%s],"debt":[],"resolved":[]}' "$(f42 c.py 'touch c.ran; false')" > "$T/r42c.json"
r42 "$T/r42c.json" --repro-base "$T/b42" bbb1111 --repro-prev "$T/p42w.json" ppp0000; rc=$?
[ $rc = 0 ] && [ "$(bl42 "$T/r42c.json")" = "repro also fails at base bbb1111; y|" ] && [ "$(db42 "$T/r42c.json")" = "" ] \
  && ok "42 a reworded repro keeps its finding Blocking" || ko "42 reworded: rc=$rc blocking=$(bl42 "$T/r42c.json") debt=$(db42 "$T/r42c.json")"
# a base run that proves nothing keeps the finding Blocking and stderr names the reason, even with the same repro in the
# previous answer. Each repro is red at the head (headmark) and proves nothing at the base.
nothing42() {   # <name> <stderr pattern> <repro> [python preamble] -- [revformat args]
  local n=$1 pat=$2 rp=$3 pre=$4; shift 5
  printf '{"verdict":"CHANGES","blocking":[%s],"debt":[],"resolved":[]}' "$(f42 c.py "$rp")" > "$T/p42$n.json"
  cp "$T/p42$n.json" "$T/n42$n.json"
  env ${nested:+SQUAD_REPRO_EXEC=$T/bin/sandbox-pass} ${EXEC42:+SQUAD_REPRO_EXEC=$EXEC42} python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import revformat as r; $pre; sys.exit(r.main(sys.argv[2:]))" \
    "$(cd "$(dirname "$src")" && pwd)" --repro "$T/n42$n.json" "$T/h42" hhh2222 "$@" --repro-prev "$T/p42$n.json" ppp0000 2>"$T/n42$n.err"; rc=$?
  [ $rc = 0 ] && [ "$(bl42 "$T/n42$n.json")" = "y|" ] && [ "$(db42 "$T/n42$n.json")" = "" ] && /usr/bin/grep -q "$pat" "$T/n42$n.err" && /usr/bin/grep -q 'stays Blocking' "$T/n42$n.err" \
    && ok "42 a base run that proves nothing ($n) keeps the finding Blocking and names the reason" \
    || ko "42 $n: rc=$rc blocking=$(bl42 "$T/n42$n.json") debt=$(db42 "$T/n42$n.json") err=$(cat "$T/n42$n.err")"
}
nothing42 notree 'no base tree' 'test -f headmark && exit 1; exit 1' 'pass' -- --repro-base "$T/no-such-base" bbb1111
nothing42 t124 'exit 124' 'test -f headmark && exit 1; sleep 5' 'r.REPRO_TIMEOUT = 1' -- --repro-base "$T/b42" bbb1111
nothing42 t126 'exit 126' 'test -f headmark && exit 1; exit 126' 'pass' -- --repro-base "$T/b42" bbb1111
nothing42 t127 'exit 127' 'test -f headmark && exit 1; exit 127' 'pass' -- --repro-base "$T/b42" bbb1111
nothing42 budget 'budget' 'test -f headmark && exit 1; exit 1' 'r.REPRO_BASE_BUDGET = 0' -- --repro-base "$T/b42" bbb1111
cat > "$T/bin/sandbox-refuse-base" <<'J'
#!/bin/sh
case $(pwd) in */b42) echo "sandbox-exec: sandbox_apply: Operation not permitted" >&2; exit 71;; esac
shift 2; exec "$@"
J
chmod +x "$T/bin/sandbox-refuse-base"
EXEC42=$T/bin/sandbox-refuse-base nothing42 refused 'sandbox refused' 'test -f headmark && exit 1; exit 1' 'pass' -- --repro-base "$T/b42" bbb1111
# the previous head equal to the head is no repeat: the finding stays Blocking with the base note
printf '{"verdict":"CHANGES","blocking":[%s],"debt":[],"resolved":[]}' "$(f42 c.py 'touch c.ran; false')" > "$T/r42d.json"
r42 "$T/r42d.json" --repro-base "$T/b42" bbb1111 --repro-prev "$T/p42.json" hhh2222; rc=$?
[ $rc = 0 ] && [ "$(bl42 "$T/r42d.json")" = "repro also fails at base bbb1111; y|" ] && [ "$(db42 "$T/r42d.json")" = "" ] \
  && ok "42 a previous head equal to the head is no repeat" || ko "42 same head: rc=$rc blocking=$(bl42 "$T/r42d.json") debt=$(db42 "$T/r42d.json")"
nothing42 signal 'signal' 'test -f headmark && exit 1; kill -9 $$' 'pass' -- --repro-base "$T/b42" bbb1111
# a base run is capped at the budget left: a 5 s base run under a 1 s budget is killed, and proves nothing
t42=$(date +%s); nothing42 cap 'exit 124' 'test -f headmark && exit 1; sleep 5' 'r.REPRO_BASE_BUDGET = 1' -- --repro-base "$T/b42" bbb1111
[ $(( $(date +%s) - t42 )) -lt 4 ] && ok "42 a base run past the budget left is killed" || ko "42 budget cap: the base run took $(( $(date +%s) - t42 ))s"
# 43) #2647: a repro runs in the gate's environment. The checkout's venv comes first on PATH, and pytest carries
# -p no:deepeval, so a repro that names a test the gate passes is not red because the plugin dies at startup.
mkdir -p "$T/p43" "$T/v43/.venv/bin" "$T/co43"
printf '#!/bin/sh\nexit 1\n' > "$T/p43/tool2647"; printf '#!/bin/sh\nexit 0\n' > "$T/v43/.venv/bin/tool2647"
printf '#!/bin/sh\ncase " $PYTEST_ADDOPTS " in *" -p no:deepeval "*) exit 0;; esac\nexit 1\n' > "$T/p43/pytest"
chmod +x "$T/p43/tool2647" "$T/v43/.venv/bin/tool2647" "$T/p43/pytest"
printf '{"verdict":"CHANGES","blocking":[{"location":"a.py:1","defect":"x","failure":"y","fix":"z","repro":"tool2647"},{"location":"b.py:2","defect":"x","failure":"y","fix":"z","repro":"pytest -q tests/test_b.py"}],"debt":[],"resolved":[]}' > "$T/r43.json"
env PATH="$T/p43:$PATH" SG_CHECKOUT="$T/v43" ${nested:+SQUAD_REPRO_EXEC=$T/bin/sandbox-pass} SQUAD_SIGNAL_PROFILE="(version 1)(allow default)(deny signal)(allow signal (target self))(allow signal (target same-sandbox))" python3 "$src" --repro "$T/r43.json" "$T/co43" abc2647 2>"$T/r43.err"; rc=$?
python3 "$src" --answer "$T/r43.json" > "$T/r43.md" 2>/dev/null
[ $rc = 0 ] && [ "$(python3 "$src" --summary "$T/r43.md")" = "blocking=0 debt=2" ] \
  && ok "43 a repro runs with the checkout venv first and pytest -p no:deepeval" \
  || ko "43 repro env: rc=$rc summary='$(python3 "$src" --summary "$T/r43.md" 2>&1)' $(cat "$T/r43.err")"
# 44) #2647: a markdown answer to a code review names no repro, so --demote-markdown moves its Blocking findings to Debt
# with the reason, and the review keeps the shape.
printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — x — y — z\n## Debt\n- b.cs:2 — naming — nobody can tell — rename it\n' > "$T/m44.md"
python3 "$src" --demote-markdown "$T/m44.md" 2>"$T/m44.err"; rc=$?
[ $rc = 0 ] && [ "$(python3 "$src" --summary "$T/m44.md")" = "blocking=0 debt=2" ] && /usr/bin/grep -q '^- a.cs:1 — .*no repro: markdown answer' "$T/m44.md" \
  && python3 "$src" "$T/m44.md" >/dev/null \
  && ok "44 a markdown answer's Blocking findings move to Debt with the reason" \
  || ko "44 demote: rc=$rc summary='$(python3 "$src" --summary "$T/m44.md" 2>&1)' check='$(python3 "$src" "$T/m44.md")' $(cat "$T/m44.md" "$T/m44.err")"
printf 'VERDICT: CHANGES\n## Blocking\n### Theme\n- a.cs:1 — x — y — z\n' > "$T/m44b.md"
python3 "$src" --demote-markdown "$T/m44b.md" 2>/dev/null; rc=$?
[ $rc = 0 ] && [ "$(python3 "$src" --summary "$T/m44b.md")" = "blocking=0 debt=1" ] && python3 "$src" "$T/m44b.md" >/dev/null \
  && ok "44 a Blocking section with no Debt section becomes the Debt section" \
  || ko "44 no debt: rc=$rc $(cat "$T/m44b.md")"
rm -rf "$T"; exit $fail
