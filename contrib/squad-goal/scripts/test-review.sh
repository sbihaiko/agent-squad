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
  *'.head.sha'*) if [ -n "$GH_MOVE" ] && [ -e "$GH_MOVE" ]; then echo bbb222; else echo aaa111; fi;;
  *) echo "stub";;
esac
J
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > /dev/null; if [ -n "$CLAUDE_SLOW" ]; then touch "$GH_MOVE"; sleep 40; fi; echo "VERDICT: APPROVE"; echo "1. fine"
J
chmod +x "$T/bin/gh" "$T/bin/claude"; export PATH="$T/bin:$PATH"
out=$(bash "$T/s/review.sh" 7 8); rc=$?
[ $rc = 0 ] && ok "accepts a verdict (exit 0)" || ko "accepts a verdict: review.sh exited $rc"
case "$out" in model=claude:claude-opus-5-5*) ok "reports the model";; *) ko "reports the model: '$out'";; esac
[ "$(tail -1 "$T/st/review-times.tsv" | awk -F'\t' '{print $5}')" = 0 ] && ok "logs rc 0" || ko "logs rc 0: $(tail -1 "$T/st/review-times.tsv")"
rm -f "$T/st/review-7.sha"; export GH_MOVE="$T/moved" CLAUDE_SLOW=1; t=$(date +%s); out=$(bash "$T/s/review.sh" 7 8); rc=$?; el=$(( $(date +%s) - t ))
[ $rc = 1 ] && head -1 "$T/st/review-7.md" | grep -q BLOCKED && [ $el -lt 35 ] && ok "a push during the review ends it BLOCKED in ${el}s" || ko "superseded: rc=$rc ${el}s $(head -1 "$T/st/review-7.md")"
rm -rf "$T"; exit $fail
