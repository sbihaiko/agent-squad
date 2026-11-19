#!/bin/bash
# seconds: 0.72
# Regression test for model-recover.sh (ADR-0160): an omitted model is probed on its own review lane, and the streak is
# cleared only on a real answer. A stub gateway answers or fails on command. usage: bash test-model-recover.sh [script]
src=${1:-$(dirname "$0")/model-recover.sh}; S=$(dirname "$src"); T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/st" "$T/bin"
cp "$src" "$T/model-recover.sh"; cp "$S/review-timeouts.py" "$S/modelreg.py" "$S/models.json" "$T/" 2>/dev/null
[ -d "$S/../../.." ] && cp -R "$S/../../../squad" "$T/squad" 2>/dev/null
python3 - "$T/modelreg.py" <<'PY' 2>/dev/null || true
PY
# modelreg.py reads the repository registry; the test pins it to the shipped file so the route is the real one
REG=$(cd "$S/../../.." 2>/dev/null && pwd)/config/models.registry.json
printf '#!/bin/bash\nexport SG_MODELS_REGISTRY=%s\nexec python3 %s "$@"\n' "$REG" "$T/modelreg.py" > "$T/mr"; chmod +x "$T/mr"
sed -i '' 's|python3 "$here/modelreg.py"|"$here/mr"|' "$T/model-recover.sh" 2>/dev/null
# a gateway stub: $STUB_ANSWER is what the direct call prints, and the body it received is kept for the argv check
cat > "$T/bin/curl" <<'CU'
#!/bin/bash
body=""; while [ $# -gt 0 ]; do [ "$1" = "-d" ] && { body=$2; shift; }; shift; done
printf '%s' "${body:-}" > "$STUB_BODY"
printf '%s' "${STUB_ANSWER:-}"
CU
chmod +x "$T/bin/curl"
echo '{"claude:claude-deepseek-flash": {"streak": 3, "until": 9999999999}}' > "$T/st/review-timeouts.json"
run() { PATH="$T/bin:$PATH" STUB_BODY="$T/body" STUB_ANSWER="${1:-}" SG_STATE="$T/st" bash "$T/model-recover.sh" "${2:-claude:claude-deepseek-flash}" 2>&1; }
# 1) a real answer clears the streak, on the no-tools lane, with thinking off
out=$(run '{"content":[{"type":"text","text":"ok"}]}')
case "$out" in MODEL\ RECOVERED*) ok "a real answer recovers the model";; *) ko "no recovery: $out";; esac
grep -q 'deepseek' "$T/st/review-timeouts.json" && ko "the streak stands after a real answer" || ok "the streak is cleared"
grep -q '"thinking"' "$T/body" 2>/dev/null && grep -q '"disabled"' "$T/body" 2>/dev/null && ok "the probe turns thinking off, as the review pass does" || ko "the probe sent no thinking:disabled: $(cat "$T/body" 2>/dev/null | tr -d '\n')"
# 2) a failed answer keeps the streak and names the cause
echo '{"claude:claude-deepseek-flash": {"streak": 3, "until": 9999999999}}' > "$T/st/review-timeouts.json"
out=$(run '')
case "$out" in MODEL\ NOT\ RECOVERED*3\ consecutive*) ok "a failed probe names the cause and keeps the streak";; *) ko "wrong report: $out";; esac
grep -q 'deepseek' "$T/st/review-timeouts.json" && ok "the streak stands after a failed probe" || ko "the streak was cleared with no answer"
# 3) a model that is not omitted is left alone
out=$(run '{"content":[{"type":"text","text":"ok"}]}' claude:claude-opus-5-5)
case "$out" in MODEL\ NOT\ OMITTED*) ok "a model with no streak is not probed";; *) ko "wrong report: $out";; esac
rm -rf "$T"; exit $fail
