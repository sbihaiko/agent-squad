#!/bin/bash
# Regression tests for review.sh with stubbed gh and claude (no network, no model). usage: bash test-review.sh [path/to/review.sh]
# 1) a reviewer that answers with a VERDICT is accepted: exit 0, rc 0 in review-times.tsv (the bug: rc=$? read the status of the superseded test, so every model "failed")
# 2) the PR head moving during the review kills the reviewer and ends BLOCKED (superseded), not a hang.
# 7) #1501: the aging leash must not kill the only reachable model; 8) with several reachable models it still clamps.
src=${1:-$(dirname "$0")/review.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$src" "$T/s/review.sh"; cp "$(dirname "$src")/revformat.py" "$T/s/"; : > "$T/s/codex-reset.py"
cat > "$T/s/models.json" <<J
{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}
J
cat > "$T/s/env.sh" <<J
SG_REPO=x/y; SG_STATE=$T/st; SG_BASE=main; SG_CHECKOUT=$T; SG_REVIEW_RULES=CLAUDE.md
J
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *'.head.sha+" "+.base.ref'*) echo "aaa111 main";;
  *compare*'-q .status'*|*compare*'.status'*) echo ahead;;
  *compare*) printf 'diff --git a/f b/f\n+x\n';;
  *pulls/7/files*) printf '%s\n' ${GH_FILES:-UI/a.cs};;
  *'.head.sha'*) if [ -n "$GH_MOVE" ] && [ -e "$GH_MOVE" ]; then echo bbb222; else echo aaa111; fi;;
  *) echo "stub";;
esac
J
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > "${CLAUDE_PROMPT:-/dev/null}"; echo "$*" >> "${CLAUDE_ARGS:-/dev/null}"; [ -n "$CLAUDE_TAGGED" ] && { printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — diverges from ADR-0001 — wrong — fix\n## Debt\n- b.cs:2 — naming — nobody can tell what it names — rename it\n'; exit 0; }; [ -n "$CLAUDE_NARRATE" ] && { printf "Checking the delta first.\nVERDICT: CHANGES\n1. a finding\nIn summary, close but another round.\n"; exit 0; }; if [ -n "$CLAUDE_SLOW" ]; then touch "$GH_MOVE"; sleep 40; fi; [ -n "$CLAUDE_SLEEP" ] && sleep "$CLAUDE_SLEEP"; echo "VERDICT: APPROVE"
J
chmod +x "$T/bin/gh" "$T/bin/claude"; export PATH="$T/bin:$PATH"
out=$(bash "$T/s/review.sh" 7 8); rc=$?
[ $rc = 0 ] && ok "accepts a verdict (exit 0)" || ko "accepts a verdict: review.sh exited $rc"
case "$out" in model=claude:claude-opus-5-5*) ok "reports the model";; *) ko "reports the model: '$out'";; esac
[ "$(tail -1 "$T/st/review-times.tsv" | awk -F'\t' '{print $5}')" = 0 ] && ok "logs rc 0" || ko "logs rc 0: $(tail -1 "$T/st/review-times.tsv")"
rm -f "$T/st/review-7.sha"; export GH_MOVE="$T/moved" CLAUDE_SLOW=1; t=$(date +%s); out=$(bash "$T/s/review.sh" 7 8); rc=$?; el=$(( $(date +%s) - t ))
[ $rc = 1 ] && head -1 "$T/st/review-7.md" | grep -q BLOCKED && [ $el -lt 35 ] && ok "a push during the review ends it BLOCKED in ${el}s" || ko "superseded: rc=$rc ${el}s $(head -1 "$T/st/review-7.md")"
# 3) #1777: a reviewer that narrates and numbers its findings ignores the checklist format -- a format failure of
# that try, never a silent pass (the prose used to be saved, with the verdict moved to line 1).
unset GH_MOVE CLAUDE_SLOW; export CLAUDE_NARRATE=1; rm -f "$T/st/review-7.sha" "$T/st/cq/"*mformat*
out=$(bash "$T/s/review.sh" 7 8 2>/dev/null); rc=$?
[ $rc != 0 ] && head -1 "$T/st/review-7.md" | grep -q BLOCKED && ok "a prose review is a format failure (BLOCKED, not saved)" || ko "prose review: rc=$rc '$(head -1 "$T/st/review-7.md")'"
/usr/bin/grep -q 'line 2' "$T/st/cq/"*mformat* 2>/dev/null && ok "the format failure names the first line that is not the shape" || ko "no format reason: $(cat "$T/st/cq/"*mformat* 2>/dev/null)"
unset CLAUDE_NARRATE
# 4) fast lane: a docs/tests-only PR goes to the fastest model by measured latency, at low effort; product code keeps the order
unset CLAUDE_NARRATE; export CLAUDE_ARGS="$T/args"
printf '{"review_models":["claude:slow","claude:fast"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
printf 'x\t1\tclaude:slow\t200\t0\nx\t1\tclaude:fast\t20\t0\n' >> "$T/st/review-times.tsv"
rm -f "$T/st/review-7.sha"; out=$(GH_FILES="docs/a.md UI.Tests/b.cs scripts/test_c.py" bash "$T/s/review.sh" 7 8 2>/dev/null)
case "$out" in "model=claude:fast (fast lane"*) ok "docs/tests-only PR: fastest model, fast lane";; *) ko "fast lane: '$out'";; esac
/usr/bin/grep -q -- "--model fast --effort low" "$T/args" && ok "fast lane runs at low effort" || ko "effort: $(tail -1 "$T/args")"
rm -f "$T/st/review-7.sha"; out=$(GH_FILES="docs/a.md UI/x.cs" bash "$T/s/review.sh" 7 8 2>/dev/null)
case "$out" in model=claude:slow*) case "$out" in *"fast lane"*) ko "product PR got the fast lane";; *) ok "product code keeps the configured chain";; esac;; *) ko "product chain: '$out'";; esac
# 5) hours: a model whose window in models.json does not contain the current local hour is skipped and the chain moves on
h=$((10#$(date +%H))); a=$(( (h+1)%24 )); b=$(( (h+2)%24 )); printf -v wa '%02d-%02d' $a $b
printf '{"review_models":["claude:slow","claude:fast"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[],"hours":{"review_models":{"claude:slow":"%s"}}}' "$wa" > "$T/s/models.json"
rm -f "$T/st/review-7.sha"; out=$(bash "$T/s/review.sh" 7 8 2>/dev/null)
case "$out" in model=claude:fast*) ok "hours: a model outside its window is skipped ($wa)";; *) ko "hours skip: '$out'";; esac
# and the default is all day: the same model with no window in the map runs (the config just wrote none for claude:fast)
/usr/bin/grep -q 'claude:fast' "$T/st/cq/8-mhours"* 2>/dev/null && ko "hours: claude:fast was skipped with no window (default must be all day)" || ok "hours: no window = runs all day"
h2=$(( (h+23)%24 )); h3=$(( (h+1)%24 )); printf -v wb '%02d-%02d' $h2 $h3   # a window that does contain now (an hour either side)
printf '{"review_models":["claude:slow"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[],"hours":{"review_models":{"claude:slow":"%s"}}}' "$wb" > "$T/s/models.json"
rm -f "$T/st/review-7.sha" "$T/st/cq/8-mhours"*; out=$(bash "$T/s/review.sh" 7 8 2>/dev/null)
case "$out" in model=claude:slow*) ok "hours: inside the window the model runs ($wb)";; *) ko "hours inside: '$out'";; esac
# 6) the severity split (#1393, unchanged by #1777): the section a finding sits in is its severity, both survive
# into the saved review, on both prompt modes, and the prompt carries the checklist format with the cap from the config.
printf '{"review_models":["claude:m"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"review_max_lines":21,"anthropic_models":[]}' > "$T/s/models.json"
unset CLAUDE_NARRATE; export CLAUDE_TAGGED=1 CLAUDE_PROMPT="$T/prompt"
for mode in full fix; do
  rm -f "$T/st/review-7".*; if [ $mode = fix ]; then echo ccc333 > "$T/st/review-7.sha"; printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — x — y — z\n' > "$T/st/review-7.md"; fi
  bash "$T/s/review.sh" 7 8 >/dev/null 2>&1
  [ $mode = fix ] && { /usr/bin/grep -q 'FIX ROUND' "$T/prompt" && ok "fix mode really ran" || ko "fix mode did not run"; }
  [ "$(head -1 "$T/st/review-7.md")" = "VERDICT: CHANGES" ] && /usr/bin/grep -q '^## Blocking' "$T/st/review-7.md" && /usr/bin/grep -q '^## Debt' "$T/st/review-7.md" && /usr/bin/grep -q '^- b.cs:2 — ' "$T/st/review-7.md" && ok "$mode: both sections survive into the saved review" || ko "$mode saved review: $(cat "$T/st/review-7.md")"
  miss=""; for needle in '## Blocking' '## Debt' 'file:line' 'at most two lines' 'most severe first' 'no closing summary'; do
    /usr/bin/grep -qF "$needle" "$T/prompt" || miss="$miss '$needle'"; done
  [ -z "$miss" ] && ok "$mode prompt carries the checklist format" || ko "$mode prompt lacks$miss"
  /usr/bin/grep -qF 'at most 21 lines' "$T/prompt" && ok "$mode prompt carries the format 21-line cap from review_max_lines" || ko "$mode prompt cap: $(/usr/bin/grep -i 'lines' "$T/prompt" | head -1)"
  /usr/bin/grep -q 'divergence from an accepted ADR' "$T/prompt" && /usr/bin/grep -q 'regression of behaviour' "$T/prompt" && /usr/bin/grep -q 'wire protocol or a persisted format' "$T/prompt" && /usr/bin/grep -q 'passes while lying' "$T/prompt" && ok "$mode prompt lists the closed blocking set" || ko "$mode prompt lacks the blocking list"
  /usr/bin/grep -qi 'filed task' "$T/prompt" && ok "$mode prompt states what DEBT costs" || ko "$mode prompt lacks the DEBT cost"
done
/usr/bin/grep -qi 'previous' "$T/prompt" && ok "fix prompt answers the previous findings" || ko "fix prompt does not answer the previous findings"
# the cap is a named constant in the config, not a magic number in the prompt: another value, another prompt
printf '{"review_models":["claude:m"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"review_max_lines":33,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-7".*; bash "$T/s/review.sh" 7 8 >/dev/null 2>&1
/usr/bin/grep -qF 'at most 33 lines' "$T/prompt" && ok "the cap follows review_max_lines (33)" || ko "the cap is hardcoded: $(/usr/bin/grep -i 'lines' "$T/prompt" | head -1)"
unset CLAUDE_TAGGED CLAUDE_PROMPT
# 7) #1501: the aging leash must not kill the only reachable model. Gemini is agy-limited, Opus is
# outside its hours window and behind quota-guard; the survivor's own timeout (8 s) is longer than
# the leash (2 s). A 4 s answer must be accepted, not killed at the leash.
h=$((10#$(date +%H))); a=$(( (h+1)%24 )); b=$(( (h+2)%24 )); printf -v wa '%02d-%02d' $a $b
printf '{"review_models":["gemini-3.8-flash-high","claude:claude-opus-5-5","claude:claude-deepseek-v4-flash"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":["claude:claude-opus-5-5"],"hours":{"review_models":{"claude:claude-opus-5-5":"%s"}},"review_timeout_seconds_by_model":{"claude:claude-deepseek-v4-flash":8}}' "$wa" > "$T/s/models.json"
echo $(( $(date +%s) + 3600 )) > "$T/st/agy-limited"; touch "$T/st/quota-guard"
rm -f "$T/st/review-7".* "$T/st/cq/"*
t=$(date +%s)
out=$(SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1 SG_REVIEW_TIMEOUT=2 CLAUDE_SLEEP=4 bash "$T/s/review.sh" 7 8 2>/dev/null); rc=$?
el=$(( $(date +%s) - t ))
case "$out" in model=claude:claude-deepseek-v4-flash*) [ $rc = 0 ] && [ $el -ge 4 ] && ok "leash + one reachable model: survivor uses its own timeout ($el s)" || ko "leash + one reachable: rc=$rc ${el}s '$out' $(head -1 "$T/st/review-7.md" 2>/dev/null)";; *) ko "leash + one reachable: wanted deepseek, got '$out' rc=$rc ${el}s $(head -1 "$T/st/review-7.md" 2>/dev/null)";; esac
# 8) with several reachable models the leash still applies: two healthy claude entries, 4 s answer, 2 s leash -> first dies, chain depth 1, BLOCKED
printf '{"review_models":["claude:a","claude:b"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/agy-limited" "$T/st/quota-guard" "$T/st/review-7".* "$T/st/cq/"*
out=$(SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1 SG_REVIEW_TIMEOUT=2 CLAUDE_SLEEP=4 bash "$T/s/review.sh" 7 8 2>/dev/null); rc=$?
[ $rc != 0 ] && head -1 "$T/st/review-7.md" | grep -q BLOCKED && /usr/bin/grep -q 'after 2s' "$T/st/cq/"*mtimeout* && ok "leash + several reachable models: still clamped" || ko "several reachable leash: rc=$rc '$out' $(head -1 "$T/st/review-7.md" 2>/dev/null) cq=$(cat "$T/st/cq/"*mtimeout* 2>/dev/null)"
rm -rf "$T"; exit $fail
