#!/bin/bash
# Regression tests for review.sh with stubbed gh and claude (no network, no model). usage: bash test-review.sh [path/to/review.sh]
# 1) a reviewer that answers with a VERDICT is accepted: exit 0, rc 0 in review-times.tsv (the bug: rc=$? read the status of the superseded test, so every model "failed")
# 2) the PR head moving during the review kills the reviewer and ends BLOCKED (superseded), not a hang.
src=${1:-$(dirname "$0")/review.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$src" "$T/s/review.sh"; : > "$T/s/codex-reset.py"
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
  *pulls/7/files*) printf '%s\n' ${GH_FILES:-UI/a.cs};;
  *'.head.sha'*) if [ -n "$GH_MOVE" ] && [ -e "$GH_MOVE" ]; then echo bbb222; else echo aaa111; fi;;
  *) echo "stub";;
esac
J
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > /dev/null; echo "$*" >> "${CLAUDE_ARGS:-/dev/null}"; [ -n "$CLAUDE_NARRATE" ] && { printf "Checking the delta first.\nVERDICT: CHANGES\n1. a finding\n"; exit 0; }; if [ -n "$CLAUDE_SLOW" ]; then touch "$GH_MOVE"; sleep 40; fi; echo "VERDICT: APPROVE"; echo "1. fine"
J
chmod +x "$T/bin/gh" "$T/bin/claude"; export PATH="$T/bin:$PATH"
out=$(bash "$T/s/review.sh" 7 8); rc=$?
[ $rc = 0 ] && ok "accepts a verdict (exit 0)" || ko "accepts a verdict: review.sh exited $rc"
case "$out" in model=claude:claude-opus-5-5*) ok "reports the model";; *) ko "reports the model: '$out'";; esac
[ "$(tail -1 "$T/st/review-times.tsv" | awk -F'\t' '{print $5}')" = 0 ] && ok "logs rc 0" || ko "logs rc 0: $(tail -1 "$T/st/review-times.tsv")"
rm -f "$T/st/review-7.sha"; export GH_MOVE="$T/moved" CLAUDE_SLOW=1; t=$(date +%s); out=$(bash "$T/s/review.sh" 7 8); rc=$?; el=$(( $(date +%s) - t ))
[ $rc = 1 ] && head -1 "$T/st/review-7.md" | grep -q BLOCKED && [ $el -lt 35 ] && ok "a push during the review ends it BLOCKED in ${el}s" || ko "superseded: rc=$rc ${el}s $(head -1 "$T/st/review-7.md")"
# 3) a reviewer that narrates before the verdict: the saved review still starts with the VERDICT line (revci/autopilot read line 1)
unset GH_MOVE CLAUDE_SLOW; export CLAUDE_NARRATE=1; rm -f "$T/st/review-7.sha"; bash "$T/s/review.sh" 7 8 >/dev/null; [ "$(head -1 "$T/st/review-7.md")" = "VERDICT: CHANGES" ] && ok "narration before the verdict: line 1 is VERDICT" || ko "line 1 is '$(head -1 "$T/st/review-7.md")'"
# 4) fast lane: a docs/tests-only PR goes to the fastest model by measured latency, at low effort; product code keeps the order
unset CLAUDE_NARRATE; export CLAUDE_ARGS="$T/args"
printf '{"review_models":["claude:slow","claude:fast"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
printf 'x\t1\tclaude:slow\t200\t0\nx\t1\tclaude:fast\t20\t0\n' >> "$T/st/review-times.tsv"
rm -f "$T/st/review-7.sha"; out=$(GH_FILES="docs/a.md UI.Tests/b.cs scripts/test_c.py" bash "$T/s/review.sh" 7 8 2>/dev/null)
case "$out" in "model=claude:fast (fast lane"*) ok "docs/tests-only PR: fastest model, fast lane";; *) ko "fast lane: '$out'";; esac
/usr/bin/grep -q -- "--model fast --effort low" "$T/args" && ok "fast lane runs at low effort" || ko "effort: $(tail -1 "$T/args")"
rm -f "$T/st/review-7.sha"; out=$(GH_FILES="docs/a.md UI/x.cs" bash "$T/s/review.sh" 7 8 2>/dev/null)
case "$out" in model=claude:slow*) case "$out" in *"fast lane"*) ko "product PR got the fast lane";; *) ok "product code keeps the configured chain";; esac;; *) ko "product chain: '$out'";; esac
rm -rf "$T"; exit $fail
