#!/bin/bash
# Guard for ADR-0171 (#1358): a loop defect is reviewed by Fable or Astra before it is built, and autobug.sh dispatches
# no loop-bug issue without an approving plan review of its current body. Offline: stub gh and stub claude, no model.
# usage: bash test-loopbug-review.sh [scripts dir]
# 1) the loop-defect chain: its first entry is claude-fable-5-1 or gpt-6-astra, its second the other, both on the cli route,
#    and the rest is the review chain in its own order (deepseek-flash reachable, never first).
# 2) review.sh takes the loop-defect chain for an issue carrying loop-bug (or any loop-bug:* label), the ordinary chain
#    otherwise, and a failed label read keeps the ordinary chain and says so in one line on stderr.
# 3) review.sh --plan writes the verdict under the issue's key, its .sha is the body hash, and loopbug.sh counts only an
#    approval of the current body: a missing, changed or non-approving review does not pass.
# 4) autobug.sh sources loopbug.sh and holds a loop-bug candidate that has no approving plan review.
# 5) the record: ADR-0171 exists, its index row names it, and the CLAUDE.md rule names this guard.
d=${1:-$(cd "$(dirname "$0")" && pwd)}; repo_root=$(cd "$d/../../.." && pwd)
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_SHARED="$T/st"
mkdir -p "$T/bin" "$T/st" "$T/s"
cp "$d/review.sh" "$T/s/review.sh"
cp "$d/revformat.py" "$d/ghcall.sh" "$d/ghread.py" "$d/modelreg.py" "$d/review-timeouts.py" "$d/registry.json" "$d/models.json" "$d/address.sh" "$T/s/"
[ -f "$d/loopbug.sh" ] && cp "$d/loopbug.sh" "$T/s/"
: > "$T/s/codex-reset.py"
cat > "$T/s/env.sh" <<J
SG_REPO=x/y; SG_STATE=$T/st; SG_BASE=main; SG_CHECKOUT=$T; SG_REVIEW_RULES=CLAUDE.md
. "$T/s/address.sh"
J

# --- 1) the chain -----------------------------------------------------------------------------------------------------
loop_ids=($(python3 "$d/modelreg.py" chain loop_defect --models "$d/models.json" 2>/dev/null))
review_ids=($(python3 "$d/modelreg.py" chain review --models "$d/models.json" 2>/dev/null))
routes=$(python3 "$d/modelreg.py" chain loop_defect --models "$d/models.json" --tsv 2>/dev/null | head -2 | cut -f3 | paste -sd, -)
if { [ "${loop_ids[0]:-}" = claude-fable-5-1 ] && [ "${loop_ids[1]:-}" = gpt-6-astra ]; } \
   || { [ "${loop_ids[0]:-}" = gpt-6-astra ] && [ "${loop_ids[1]:-}" = claude-fable-5-1 ]; }; then
  ok "1a loop_defect head is Fable and Astra, in some order"
else ko "1a loop_defect head is not Fable then Astra: ${loop_ids[*]:-none}"; fi
[ "$routes" = cli,cli ] && ok "1b the first two entries are on the cli route" || ko "1b the first two routes are not cli,cli: ${routes:-none}"
tail_ids=("${loop_ids[@]:2}")
if [ "${#review_ids[@]}" -gt 0 ] && [ "$(printf '%s\n' "${tail_ids[@]}")" = "$(printf '%s\n' "${review_ids[@]}")" ]; then
  ok "1c the rest of loop_defect is the review chain, in its order"
else ko "1c loop_defect tail is not the review chain: ${tail_ids[*]:-none} vs ${review_ids[*]:-none}"; fi
[ "${#loop_ids[@]}" -gt 0 ] && [ "${review_ids[0]:-}" = deepseek-flash ] && [ "${loop_ids[0]:-}" != deepseek-flash ] && [ "${loop_ids[1]:-}" != deepseek-flash ] \
  && ok "1d deepseek-flash is reachable in loop_defect and never first" || ko "1d deepseek-flash is first or missing in loop_defect"

# --- 2) review.sh takes the chain by label ------------------------------------------------------------------------------
cat > "$T/bin/gh" <<'J'
#!/bin/bash
printf '%s\n' "gh $*" >> "${GHLOG:-/dev/null}"
case "$*" in
  *Accept*compare*) printf 'diff --git a/f b/f\n+x\n';;
  *compare*) printf '{"status":"ahead","files":[{"filename":"contrib/squad-goal/scripts/a.sh"}]}\n';;
  *commits/*) echo '{"sha":"aaa111"}';;
  *issues/*)
    if [ -n "${GH_LABELS_BAD:-}" ]; then echo '{"number":8,"title":"the issue","body":"the acceptance criteria","labels":"not-a-list"}'
    else printf '{"number":8,"title":"the issue","body":"the acceptance criteria","labels":[%s]}\n' "${LB_LABELS:-}"; fi;;
  *) echo "stub";;
esac
J
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > /dev/null; echo "$*" >> "${CLAUDE_ARGS:-/dev/null}"; echo "VERDICT: APPROVE"
J
chmod +x "$T/bin/gh" "$T/bin/claude"
export PATH="$T/bin:$PATH" GHLOG="$T/gh.log" CLAUDE_ARGS="$T/claude.args"
pick() { rm -f "$T/st/review-"*.model "$T/claude.args" "$T/st/review-"*.md "$T/st/review-"*.sha; LB_LABELS="$1" GH_LABELS_BAD="$2" bash "$T/s/review.sh" --branch fix/8-x 8 >/dev/null 2>"$T/err"; }
pick '{"name":"loop-bug"}' ''
[ "$(cat "$T/st/review-fix_8-x.model" 2>/dev/null)" = claude-fable-5-1 ] \
  && ok "2a a loop-bug issue is first reviewed by claude-fable-5-1" || ko "2a a loop-bug issue was not reviewed by Fable first: $(cat "$T/st/review-fix_8-x.model" 2>/dev/null || echo none)"
pick '{"name":"loop-bug:dispatch"}' ''
[ "$(cat "$T/st/review-fix_8-x.model" 2>/dev/null)" = claude-fable-5-1 ] \
  && ok "2b a loop-bug:dispatch issue is reviewed by Fable first" || ko "2b a loop-bug:dispatch issue was not reviewed by Fable first"
pick '{"name":"bug"}' ''
m=$(cat "$T/st/review-fix_8-x.model" 2>/dev/null)
[ -n "$m" ] && [ "$m" != claude-fable-5-1 ] && ! grep -q 'claude-fable-5-1' "$T/claude.args" 2>/dev/null \
  && ok "2c an ordinary issue keeps the ordinary chain ($m)" || ko "2c an ordinary issue reached Fable or ran no model: ${m:-none}"
pick '' 'bad'
[ "$(cat "$T/st/review-fix_8-x.model" 2>/dev/null)" != claude-fable-5-1 ] && grep -q 'label' "$T/err" \
  && ok "2d a failed label read keeps the ordinary chain and names it on stderr" \
  || ko "2d a failed label read did not keep the ordinary chain with one stderr line: $(head -1 "$T/err" 2>/dev/null)"

# --- 3) the plan review and its approval ---------------------------------------------------------------------------------
pick_plan() { rm -f "$T/st/review-"*.md "$T/st/review-"*.sha; LB_LABELS='{"name":"loop-bug"}' bash "$T/s/review.sh" --plan 8 >/dev/null 2>"$T/err"; }
pick_plan
key='x_y#8'
[ "$(head -1 "$T/st/review-$key.md" 2>/dev/null)" = "VERDICT: APPROVE" ] \
  && ok "3a review.sh --plan writes its verdict under the issue key" || ko "3a the plan review did not write VERDICT: APPROVE under review-$key.md"
want=$(printf '%s' "the acceptance criteria" | python3 -I -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')
[ "$(cat "$T/st/review-$key.sha" 2>/dev/null)" = "$want" ] \
  && ok "3b the plan review records the body hash" || ko "3b the plan review's .sha is not the body hash"
if [ -f "$d/loopbug.sh" ]; then
  . "$d/loopbug.sh"
  body_h=$(lb_body_hash "the acceptance criteria")
  [ "$(lb_plan_verdict "$T/st" x/y 8 "$body_h")" = approve ] && ok "3c an approval of the current body passes" || ko "3c an approval of the current body does not pass"
  lb_plan_verdict "$T/st" x/y 8 "$(lb_body_hash "an edited body")" >/dev/null && ko "3d a stale review of an edited body passed" || ok "3d a changed body makes the review stale"
  lb_plan_verdict "$T/st" x/y 9 "$body_h" >/dev/null && ko "3e a missing review passed" || ok "3e a missing review does not pass"
  printf 'VERDICT: CHANGES\n' > "$T/st/review-x_y#10.md"; printf '%s\n' "$(lb_body_hash "b")" > "$T/st/review-x_y#10.sha"
  lb_plan_verdict "$T/st" x/y 10 "$(lb_body_hash "b")" >/dev/null && ko "3f a CHANGES plan review passed" || ok "3f a CHANGES plan review does not pass"
else ko "3c loopbug.sh is missing: the plan verdict has no helper"; fi

# --- 4) autobug.sh holds a loop-bug candidate with no approving plan review -----------------------------------------------
/usr/bin/grep -q 'loopbug.sh' "$d/autobug.sh" && /usr/bin/grep -q 'lb_plan_verdict' "$d/autobug.sh" \
  && ok "4a autobug.sh sources loopbug.sh and asks it for the plan verdict" || ko "4a autobug.sh does not gate loop-bug on the plan verdict"
/usr/bin/grep -q 'lb_is_loop_bug' "$d/autobug.sh" && ok "4b autobug.sh selects loop-bug by label" || ko "4b autobug.sh does not select loop-bug by label"

# --- 5) the record -------------------------------------------------------------------------------------------------------
adr=$(ls "$repo_root"/docs/adr/0171-*.md 2>/dev/null | head -1)
[ -n "$adr" ] && ok "5a ADR-0171 exists" || ko "5a no ADR-0171 under docs/adr/"
/usr/bin/grep -q '^| \[0171\]' "$repo_root/docs/adr/README.md" && ok "5b the ADR index names ADR-0171" || ko "5b docs/adr/README.md has no 0171 row"
/usr/bin/grep -q 'test-loopbug-review.sh' "$repo_root/CLAUDE.md" && ok "5c the CLAUDE.md rule names this guard" || ko "5c CLAUDE.md does not name test-loopbug-review.sh"

rm -rf "$T"
exit $fail
