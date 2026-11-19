#!/bin/bash
# seconds: 26.45
# Guard for #816: the review prompt is bounded, and a profile can leave generated paths out of the diff.
# A child diff held a 33 MB `graphify-out/graph.json` that a git hook regenerated, and every review model refused the prompt
# the same way ("Input exceeds the maximum length"), so the review ended "every review model failed" and the wave landed empty.
# A) SG_REVIEW_EXCLUDE names path prefixes that leave the diff and the file list, in both modes, and the review still runs.
# B) a prompt over review_max_prompt_chars (models.json) calls no model: the review is BLOCKED (diff too large) and names the
#    largest files. Stubbed gh and claude: no network, no model.
# usage: bash test-review-diff-bound.sh [path/to/review.sh]
src=${1:-$(dirname "$0")/review.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep; git() { /usr/bin/git -c user.email=a@b -c user.name=t "$@"; }
export SG_SHARED="$T/st"
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$src" "$T/s/review.sh"; cp "$(dirname "$src")/loopbug.sh" "$T/s/"
for f in revformat.py ghcall.sh drainpolicy.py ghread.py modelreg.py review-timeouts.py grokmark.py registry.json; do cp "$(dirname "$src")/$f" "$T/s/"; done; : > "$T/s/codex-reset.py"
models() { printf '{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]%s}' "$1" > "$T/s/models.json"; }
cat > "$T/s/env.sh" <<J
SG_REPO=x/y; SG_STATE=$T/st; SG_BASE=main; SG_CHECKOUT=$T; SG_REVIEW_RULES=CLAUDE.md
. "$(cd "$(dirname "$src")" && pwd)/address.sh"
J
big=$(head -c 6000 /dev/zero | tr '\0' 'x')
cat > "$T/bin/gh" <<J
#!/bin/bash
case "\$*" in
  *Accept*compare*) printf 'diff --git a/gen/graph.json b/gen/graph.json\n+%s\ndiff --git a/src/a.py b/src/a.py\n+small change\n' "$big";;
  *compare*) echo '{"status":"ahead","files":[{"filename":"gen/graph.json"},{"filename":"src/a.py"}]}';;
  *commits/*) echo '{"sha":"aaa111"}';;
  *issues/*) echo '{"number":8,"title":"t","body":"b"}';;
  *) echo "stub";;
esac
J
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > "$CLAUDE_PROMPT"; echo call >> "$CLAUDE_CALLS"; echo "VERDICT: APPROVE"
J
chmod +x "$T/bin/gh" "$T/bin/claude"; export PATH="$T/bin:$PATH" CLAUDE_PROMPT="$T/prompt" CLAUDE_CALLS="$T/calls"
W="$T/child"; git init -q -b main "$W"; git -C "$W" commit -q --allow-empty -m base; git -C "$W" checkout -q -b gui/9
mkdir -p "$W/gen" "$W/src"; printf '%s\n' "$big" > "$W/gen/graph.json"; echo 'small change' > "$W/src/a.py"
git -C "$W" add -A; git -C "$W" commit -q -m child
reset() { rm -f "$T/prompt" "$T/calls" "$T/st"/review-*; }

# A) the excluded prefix leaves the diff in both modes; the review still runs on what is left
models ""; reset
out=$(cd "$W" && SG_REVIEW_EXCLUDE="gen/" bash "$T/s/review.sh" --worktree main 9 2>/dev/null); rc=$?
if [ "$rc" = 0 ] && $G -q 'src/a.py' "$T/prompt" 2>/dev/null && ! $G -q 'gen/graph.json' "$T/prompt"; then ok "A1 a worktree review leaves an excluded path out of the diff"
else ko "A1 worktree exclude: rc=$rc out='$out' gen=$($G -c 'gen/graph.json' "$T/prompt" 2>/dev/null)"; fi
reset
out=$(SG_REVIEW_EXCLUDE="docs/ gen/" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
if [ "$rc" = 0 ] && $G -q 'src/a.py' "$T/prompt" 2>/dev/null && ! $G -q 'gen/graph.json' "$T/prompt"; then ok "A2 a branch review leaves an excluded path out of the diff"
else ko "A2 branch exclude: rc=$rc out='$out' gen=$($G -c 'gen/graph.json' "$T/prompt" 2>/dev/null)"; fi

# B) an oversized prompt calls no model and says why, naming the largest file
models ',"review_max_prompt_chars":4000'; reset
out=$(cd "$W" && bash "$T/s/review.sh" --worktree main 9 2>/dev/null); rc=$?
first=$(head -1 "$T/st/review-gui_9.md" 2>/dev/null)
if [ "$rc" != 0 ] && [ ! -e "$T/calls" ] && case "$first" in "VERDICT: BLOCKED (diff too large: "*gen/graph.json*) true;; *) false;; esac
then ok "B1 an oversized prompt is BLOCKED (diff too large) before any model, and names the largest file"
else ko "B1 oversized: rc=$rc calls=$(cat "$T/calls" 2>/dev/null | wc -l) first='$first'"; fi
reset
out=$(cd "$W" && SG_REVIEW_EXCLUDE="gen/" bash "$T/s/review.sh" --worktree main 9 2>/dev/null); rc=$?
[ "$rc" = 0 ] && [ -e "$T/calls" ] && ok "B2 the same diff with the generated path excluded fits and is reviewed" || ko "B2 excluded fits: rc=$rc out='$out'"

rm -rf "$T"; exit $fail
