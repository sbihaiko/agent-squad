#!/bin/bash
# review.sh appends one ledger line per model call to $SG_STATE/token-usage.tsv (stubbed gh and claude, offline).
# 1) a claude call that reports usage: one line  ts, review:PR#7, model, in, out, cache_read ; the verdict text still parses.
# 2) a second call on the same PR is review:PR#7#2. 3) review-times.tsv keeps its 5 columns. 4) a plain-text claude: no line.
src=${1:-$(dirname "$0")/review.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$src" "$T/s/review.sh"; cp "$(dirname "$src")/revformat.py" "$(dirname "$src")/ghcall.sh" "$(dirname "$src")/ghread.py" "$T/s/"; : > "$T/s/codex-reset.py"
echo '{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
cat > "$T/s/env.sh" <<J
SG_REPO=x/y; SG_STATE=$T/st; SG_BASE=main; SG_CHECKOUT=$T; SG_REVIEW_RULES=CLAUDE.md
. "$(cd "$(dirname "$src")" && pwd)/address.sh"
J
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *'.head.sha+" "+.base.ref'*) echo "aaa111 main";;
  *Accept*compare*) printf 'diff --git a/f b/f\n+x\n';;
  *compare*) echo '{"status":"ahead"}';;
  *pulls/7/files*) echo UI/a.cs;;
  *'.head.sha'*) echo aaa111;;
  *) echo stub;;
esac
J
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > /dev/null
if [ -n "$CLAUDE_PLAIN" ]; then echo "VERDICT: APPROVE"; exit 0; fi
echo '{"type":"result","is_error":false,"result":"VERDICT: APPROVE","usage":{"input_tokens":100,"cache_creation_input_tokens":20,"output_tokens":30,"cache_read_input_tokens":400}}'
J
chmod +x "$T/bin/gh" "$T/bin/claude"; export PATH="$T/bin:$PATH"; L="$T/st/token-usage.tsv"
out=$(bash "$T/s/review.sh" 7 8); rc=$?
[ $rc = 0 ] && head -1 "$T/st/review-7.md" | grep -qx 'VERDICT: APPROVE' && ok "json output still yields the verdict" || ko "verdict: rc=$rc '$(head -1 "$T/st/review-7.md")'"
[ "$(wc -l < "$L" 2>/dev/null)" = 1 ] && ok "one call, one line" || ko "line count: $(cat "$L" 2>/dev/null)"
awk -F'\t' '$1 ~ /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+/ && $2=="review:PR#7" && $3=="claude:claude-opus-5-5" && $4==120 && $5==30 && $6==400 && NF==6 {f=1} END{exit !f}' "$L" 2>/dev/null && ok "line is ts, consumer, model, in, out, cache_read" || ko "format: $(cat "$L" 2>/dev/null)"
rm -f "$T/st/review-7.sha"; bash "$T/s/review.sh" 7 8 >/dev/null
[ "$(cut -f2 "$L" | tail -1)" = "review:PR#7#2" ] && ok "second call on a PR is #2" || ko "second: $(cat "$L")"
[ "$(tail -1 "$T/st/review-times.tsv" | awk -F'\t' '{print NF}')" = 5 ] && ok "review-times.tsv keeps 5 columns" || ko "times: $(tail -1 "$T/st/review-times.tsv")"
rm -f "$L" "$T/st/review-7.sha"; CLAUDE_PLAIN=1 bash "$T/s/review.sh" 7 8 >/dev/null
[ $? = 0 ] && [ ! -s "$L" ] && ok "a tool that prints no usage writes no line" || ko "plain: $(cat "$L" 2>/dev/null)"
rm -rf "$T"; exit $fail
