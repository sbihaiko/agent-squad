#!/bin/bash
# review.sh appends one ledger line per model call to $SG_STATE/token-usage.tsv (stubbed gh and claude, offline).
# 1) a claude call that reports usage: one line  ts, review:fix_1-x, model, in, out, cache_read (6 fields); the verdict text still parses.
# 2) a second call on the same branch is review:fix_1-x#2. 3) review-times.tsv keeps its 5 columns.
# 4) a claude answer that carries no usage -- a refusal, a zeroed .usage, an old CLI's plain text -- is named and flagged with a
#    7th field `no-usage`, so the burn total reads as a floor, never a measure (issue #442).
# 5) a model that never reports (codex here) logs 0 0 0 with NO flag: its silence is expected, not a floor.
src=${1:-$(dirname "$0")/review.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$src" "$T/s/review.sh"; cp "$(dirname "$src")/loopbug.sh" "$T/s/"; cp "$(dirname "$src")/revformat.py" "$(dirname "$src")/ghcall.sh" "$(dirname "$src")/drainpolicy.py" "$(dirname "$src")/ghread.py" "$(dirname "$src")/modelreg.py" "$(dirname "$src")/registry.json" "$T/s/"; : > "$T/s/codex-reset.py"
echo '{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
cat > "$T/s/env.sh" <<J
SG_REPO=x/y; SG_STATE=$T/st; SG_BASE=main; SG_CHECKOUT=$T; SG_REVIEW_RULES=CLAUDE.md
. "$(cd "$(dirname "$src")" && pwd)/address.sh"
J
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *Accept*compare*) printf 'diff --git a/f b/f\n+x\n';;
  *compare*) echo '{"status":"ahead","files":[{"filename":"UI/a.cs"}]}';;
  *commits/*) echo '{"sha":"aaa111"}';;
  *issues/*) echo '{"number":8,"title":"t","body":"b"}';;
  *) echo stub;;
esac
J
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > /dev/null
if [ -n "$CLAUDE_REFUSE" ]; then echo '{"type":"result","is_error":true,"result":"Refused: individual spend limit","usage":{}}'; exit 1; fi
if [ -n "$CLAUDE_PLAIN" ]; then echo "VERDICT: APPROVE"; exit 0; fi
echo '{"type":"result","is_error":false,"result":"VERDICT: APPROVE","usage":{"input_tokens":100,"cache_creation_input_tokens":20,"output_tokens":30,"cache_read_input_tokens":400}}'
J
cat > "$T/bin/codex" <<'J'
#!/bin/bash
o=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && { o=$2; shift; }; shift; done
[ -n "$o" ] && printf 'VERDICT: APPROVE\n' > "$o"
exit 0
J
chmod +x "$T/bin/gh" "$T/bin/claude" "$T/bin/codex"; export PATH="$T/bin:$PATH"; L="$T/st/token-usage.tsv"
out=$(bash "$T/s/review.sh" --branch fix/1-x 8); rc=$?
[ $rc = 0 ] && head -1 "$T/st/review-fix_1-x.md" | grep -qx 'VERDICT: APPROVE' && ok "json output still yields the verdict" || ko "verdict: rc=$rc '$(head -1 "$T/st/review-fix_1-x.md")'"
[ "$(wc -l < "$L" 2>/dev/null | tr -d " ")" = 1 ] && ok "one call, one line" || ko "line count: $(cat "$L" 2>/dev/null)"
awk -F'\t' '$1 ~ /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+/ && $2=="review:fix_1-x" && $3=="claude:claude-opus-5-5" && $4==120 && $5==30 && $6==400 && NF==6 {f=1} END{exit !f}' "$L" 2>/dev/null && ok "line is ts, consumer, model, in, out, cache_read" || ko "format: $(cat "$L" 2>/dev/null)"
rm -f "$T/st/review-fix_1-x.sha"; bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null
[ "$(cut -f2 "$L" | tail -1)" = "review:fix_1-x#2" ] && ok "second call on a branch is #2" || ko "second: $(cat "$L")"
[ "$(tail -1 "$T/st/review-times.tsv" | awk -F'\t' '{print NF}')" = 5 ] && ok "review-times.tsv keeps 5 columns" || ko "times: $(tail -1 "$T/st/review-times.tsv")"
rm -f "$L" "$T/st/review-fix_1-x.sha"; CLAUDE_PLAIN=1 bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null
awk -F'\t' '$4==0 && $5==0 && $6==0 && $7=="no-usage" && NF==7 {f=1} END{exit !f}' "$L" 2>/dev/null && ok "a claude answer with no usage is named and flagged no-usage" || ko "plain: $(cat "$L" 2>/dev/null)"
rm -f "$L" "$T/st/review-fix_1-x.sha"; CLAUDE_REFUSE=1 bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null
awk -F'\t' '$2=="review:fix_1-x" && $4==0 && $5==0 && $6==0 && $7=="no-usage" && NF==7 {f=1} END{exit !f}' "$L" 2>/dev/null && ok "a refused claude call (zeroed usage) is flagged, not logged healthy" || ko "refuse: $(cat "$L" 2>/dev/null)"
echo '{"review_models":["gpt-5.6-terra"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$L" "$T/st/review-fix_1-x.sha"; bash "$T/s/review.sh" --branch fix/2-y 9 >/dev/null
awk -F'\t' '$2=="review:fix_2-y" && $3=="gpt-5.6-terra" && $4==0 && $5==0 && $6==0 && NF==6 {f=1} END{exit !f}' "$L" 2>/dev/null && ok "a model that never reports logs 0 with no flag" || ko "codex: $(cat "$L" 2>/dev/null)"
rm -rf "$T"; exit $fail
