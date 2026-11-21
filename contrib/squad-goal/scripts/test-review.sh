#!/bin/bash
# seconds: 266.80
# Regression tests for review.sh with stubbed gh and claude (no network, no model). usage: bash test-review.sh [path/to/review.sh]
# 1) a reviewer that answers with a VERDICT is accepted: exit 0, rc 0 in review-times.tsv (the bug: rc=$? read the status of the superseded test, so every model "failed")
# 2) the branch tip moving during the review kills the reviewer and ends BLOCKED (superseded), not a hang.
# 7) #1501: the aging leash must not kill the only reachable model; 8) with several reachable models it still clamps.
suite_t0=$(date +%s)   # #1413: the acceptance command of a run is capped (SG_AC_TIMEOUT, 300 s), so the suite times itself
src=${1:-$(dirname "$0")/review.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_SHARED="$T/st"   # the shared marks (quota-guard, *-limited) are the suite's own, never the machine's under ~/.cache
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$src" "$T/s/review.sh"; cp "$(dirname "$src")/loopbug.sh" "$T/s/"; cp "$(dirname "$src")/revformat.py" "$(dirname "$src")/ghcall.sh" "$(dirname "$src")/drainpolicy.py" "$(dirname "$src")/ghread.py" "$(dirname "$src")/modelreg.py" "$(dirname "$src")/review-timeouts.py" "$(dirname "$src")/grokmark.py" "$(dirname "$src")/review-shadow.py" "$(dirname "$src")/review_stream.py" "$T/s/"; : > "$T/s/codex-reset.py"
# the registry the review reads: the shipped one plus `nt`, a model whose capabilities say no tools (the no-tools path reads that, not a list)
jq '.models += [{id:"nt",label:"nt",provider:"claude",subscription:"claude",aliases:["claude:nt"],capabilities:{tools:false},
  routes:{cli:{auth:"subscription",argv:["claude","-p","--model","{model}","{prompt_file}"],served:["nt"]},direct:{auth:"subscription",model:"nt",served:["nt"]}}}]' "$(dirname "$src")/registry.json" > "$T/s/registry.json"
cat > "$T/s/models.json" <<J
{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}
J
cat > "$T/s/env.sh" <<J
SG_REPO=x/y; SG_STATE=$T/st; SG_BASE=main; SG_CHECKOUT=$T; SG_REVIEW_RULES=CLAUDE.md
. "$(cd "$(dirname "$src")" && pwd)/address.sh"   # the writers name a queued comment through the seam, as the real env.sh loads it
J
cat > "$T/bin/gh" <<'J'
#!/bin/bash
printf '%s\n' "gh $*" >> "${GHLOG:-/dev/null}"
case "$*" in
  *Accept*compare*) [ -n "${GH_DIFF_EMPTY:-}" ] || printf 'diff --git a/f b/f\n+x\n';;
  # one answer for both compare reads: `.status` (is the tip ahead of the reviewed one) and `.files[].filename` (the fast lane)
  *compare*) printf '{"status":"ahead","files":['; sep=""; for f in ${GH_FILES:-UI/a.cs}; do printf '%s{"filename":"%s"}' "$sep" "$f"; sep=,; done; printf ']}\n';;
  *commits/*) if [ -n "$GH_MOVE" ] && [ -e "$GH_MOVE" ]; then echo '{"sha":"bbb222"}'; else echo "{\"sha\":\"${GH_SHA:-aaa111}\"}"; fi;;
  *issues/*) if [ -n "${GH_ISSUE_REFUSE:-}" ]; then printf '%s\n' '{"message":"API rate limit exceeded for 203.0.113.7.","documentation_url":"https://docs.github.com/rest/overview/rate-limits-for-the-rest-api","status":"403"}'; exit 1; fi
    echo '{"number":8,"title":"the issue","body":"the acceptance criteria"}';;
  *) echo "stub";;
esac
J
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > "${CLAUDE_PROMPT:-/dev/null}"; echo "$*" >> "${CLAUDE_ARGS:-/dev/null}"; [ -n "$CLAUDE_FAIL" ] && { echo "API Error: 529 overloaded_error"; exit 1; }; [ -n "$CLAUDE_RESOLVED" ] && { printf 'VERDICT: APPROVE\n## Resolved\n- a.cs:1 — diverges from ADR-0001 — fixed: the ADR is followed now — UI.Tests/a.cs\n## Debt\n- b.cs:2 — naming — nobody can tell what it names — rename it\n'; exit 0; }; [ -n "$CLAUDE_ERRJSON" ] && case "$*" in *"--model bad"*) printf '{"is_error":true,"subtype":"error_max_turns","result":"I'"'"'ll review the branch against the issue, reading the code.","usage":{"input_tokens":10,"output_tokens":5,"cache_read_input_tokens":0}}\n'; exit 0;; esac; [ -n "$CLAUDE_TAGGED" ] && { printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — diverges from ADR-0001 — wrong — fix\n## Debt\n- b.cs:2 — naming — nobody can tell what it names — rename it\n'; exit 0; }; [ -n "$CLAUDE_NARRATE" ] && { printf "Checking the delta first.\nVERDICT: CHANGES\n1. a finding\nIn summary, close but another round.\n"; exit 0; }; if [ -n "$CLAUDE_SLOW" ]; then touch "$GH_MOVE"; sleep 40; fi; [ -n "$CLAUDE_SLEEP" ] && sleep "$CLAUDE_SLEEP"; [ -n "$CLAUDE_WAIT" ] && { : > "$CLAUDE_WAIT.started"; while [ ! -e "$CLAUDE_WAIT" ]; do sleep 0.2; done; }; echo "VERDICT: APPROVE"
J
chmod +x "$T/bin/gh" "$T/bin/claude"; export PATH="$T/bin:$PATH"
export SG_CAP_POLL=0.2   # review.sh cap() polls every 2 s by default; a stub reviewer needs no such wait
# #1525: review.sh's repro step runs under sandbox-exec; a suite inside a child sandbox cannot nest one, so it runs the repro through the seam double. The wall is test-revformat.sh's to prove; this suite proves which tree the repro runs in and what its exit does.
nested=; /usr/bin/sandbox-exec -p '(version 1)(allow default)' /usr/bin/true 2>/dev/null || nested=1
mkdir -p "$T/bin"
cat > "$T/bin/sandbox-pass" <<'J'
#!/bin/sh
[ "$1" = -p ] && [ "$3" = sh ] && [ "$4" = -c ] || { echo "sandbox-pass: unexpected argv: $*" >&2; exit 70; }
shift 2; exec "$@"
J
chmod +x "$T/bin/sandbox-pass"
[ -n "$nested" ] && export SQUAD_REPRO_EXEC=$T/bin/sandbox-pass
out=$(bash "$T/s/review.sh" --branch fix/1-x 8); rc=$?
[ $rc = 0 ] && ok "accepts a verdict (exit 0)" || ko "accepts a verdict: review.sh exited $rc"
case "$out" in model=claude:claude-opus-5-5*) ok "reports the model";; *) ko "reports the model: '$out'";; esac
[ "$(tail -1 "$T/st/review-times.tsv" | awk -F'\t' '{print $5}')" = 0 ] && ok "logs rc 0" || ko "logs rc 0: $(tail -1 "$T/st/review-times.tsv")"
rm -f "$T/st/review-fix_1-x.sha"; export GH_MOVE="$T/moved" CLAUDE_SLOW=1; t=$(date +%s); out=$(bash "$T/s/review.sh" --branch fix/1-x 8); rc=$?; el=$(( $(date +%s) - t ))
[ $rc = 1 ] && head -1 "$T/st/review-fix_1-x.md" | grep -q BLOCKED && [ $el -lt 35 ] && ok "a push during the review ends it BLOCKED in ${el}s" || ko "superseded: rc=$rc ${el}s $(head -1 "$T/st/review-fix_1-x.md")"
# 3) #1777: a reviewer that narrates and numbers its findings ignores the checklist format -- a format failure of
# that try, never a silent pass (the prose used to be saved, with the verdict moved to line 1).
unset GH_MOVE CLAUDE_SLOW; export CLAUDE_NARRATE=1; rm -f "$T/st/review-fix_1-x.sha" "$T/st/cq/"*mformat*
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc != 0 ] && head -1 "$T/st/review-fix_1-x.md" | grep -q BLOCKED && ok "a prose review is a format failure (BLOCKED, not saved)" || ko "prose review: rc=$rc '$(head -1 "$T/st/review-fix_1-x.md")'"
/usr/bin/grep -q 'line 2' "$T/st/cq/x__y#"*mformat* 2>/dev/null && ok "the format failure names the first line that is not the shape" || ko "no format reason: $(cat "$T/st/cq/"*mformat* 2>/dev/null)"
unset CLAUDE_NARRATE
# A model that fails writes its reason where its CLI puts it: claude -p prints an API error on stdout, not stderr. The MODEL_FAIL
# status names that reason, never an empty one (the saved review file is then overwritten with BLOCKED, so it is the only trace).
export CLAUDE_FAIL=1; rm -f "$T/st/review-fix_1-x.sha" "$T/st/cq/"*mfail*
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
/usr/bin/grep -q '529 overloaded_error' "$T/st/cq/x__y#"*mfail* 2>/dev/null && ok "a failed model: MODEL_FAIL names the error it printed on stdout" || ko "a failed model: the reason is lost: $(cat "$T/st/cq/"*mfail* 2>/dev/null)"
unset CLAUDE_FAIL
# 4) fast lane: a docs/tests-only branch goes to the fastest model by measured latency, at low effort. Product code is not a
# fast lane -- it keeps its own effort -- but the order is the same measured one there too (#1846, block 10 below).
unset CLAUDE_NARRATE; export CLAUDE_ARGS="$T/args"
printf '{"review_models":["claude:slow","claude:fast"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
printf 'x\t1\tclaude:slow\t200\t0\nx\t1\tclaude:fast\t20\t0\n' >> "$T/st/review-times.tsv"
rm -f "$T/st/review-fix_1-x.sha"; out=$(GH_FILES="docs/a.md UI.Tests/b.cs scripts/test_c.py" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in "model=claude:fast (fast lane"*) ok "docs/tests-only branch: fastest model, fast lane";; *) ko "fast lane: '$out'";; esac
/usr/bin/grep -q -- "--model fast --effort low" "$T/args" && ok "fast lane runs at low effort" || ko "effort: $(tail -1 "$T/args")"
rm -f "$T/st/review-fix_1-x.sha"; out=$(GH_FILES="docs/a.md UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:fast*) case "$out" in *"fast lane"*) ko "product code got the fast lane";; *) ok "product code is ordered by measured latency too, without the fast-lane label (#1846)";; esac;; *) ko "product chain: '$out'";; esac
case "$(tail -1 "$T/args")" in *"--model fast --effort low"*) ko "product code ran at fast-lane effort";; *) ok "product code keeps its own effort (not low)";; esac
# 5) hours: a model whose window in models.json does not contain the current local hour is skipped and the chain moves on
h=$((10#$(date +%H))); a=$(( (h+1)%24 )); b=$(( (h+2)%24 )); printf -v wa '%02d-%02d' $a $b
printf '{"review_models":["claude:slow","claude:fast"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[],"hours":{"review_models":{"claude:slow":"%s"}}}' "$wa" > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x.sha"; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:fast*) ok "hours: a model outside its window is skipped ($wa)";; *) ko "hours skip: '$out'";; esac
# and the default is all day: the same model with no window in the map runs (the config just wrote none for claude:fast)
/usr/bin/grep -q 'claude:fast' "$T/st/cq/"*"#8-status~mhours"* 2>/dev/null && ko "hours: claude:fast was skipped with no window (default must be all day)" || ok "hours: no window = runs all day"
h2=$(( (h+23)%24 )); h3=$(( (h+1)%24 )); printf -v wb '%02d-%02d' $h2 $h3   # a window that does contain now (an hour either side)
printf '{"review_models":["claude:slow"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[],"hours":{"review_models":{"claude:slow":"%s"}}}' "$wb" > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x.sha" "$T/st/cq/"*"#8-status~mhours"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:slow*) ok "hours: inside the window the model runs ($wb)";; *) ko "hours inside: '$out'";; esac
# 6) the severity split (#1393, unchanged by #1777): the section a finding sits in is its severity, both survive
# into the saved review, on both prompt modes, and the prompt carries the checklist format with the cap from the config.
printf '{"review_models":["claude:m"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"review_max_lines":21,"anthropic_models":[]}' > "$T/s/models.json"
unset CLAUDE_NARRATE; export CLAUDE_TAGGED=1 CLAUDE_PROMPT="$T/prompt"
for mode in full fix; do
  rm -f "$T/st/review-fix_1-x".*; if [ $mode = fix ]; then echo ccc333 > "$T/st/review-fix_1-x.sha"; printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — x — y — z\n' > "$T/st/review-fix_1-x.md"; fi
  bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
  [ $mode = fix ] && { /usr/bin/grep -q 'FIX ROUND' "$T/prompt" && ok "fix mode really ran" || ko "fix mode did not run"; }
  # #2647: the stub answers in markdown, which names no repro, so its Blocking finding is saved under Debt with the
  # reason (ADR-0172) and the debt-only CHANGES is an approval (#653); both findings survive into the saved review.
  [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: APPROVE" ] && ! /usr/bin/grep -q '^## Blocking' "$T/st/review-fix_1-x.md" && /usr/bin/grep -q '^## Debt' "$T/st/review-fix_1-x.md" && /usr/bin/grep -q '^- a.cs:1 — .*no repro: markdown answer' "$T/st/review-fix_1-x.md" && /usr/bin/grep -q '^- b.cs:2 — ' "$T/st/review-fix_1-x.md" && ok "$mode: both findings survive into the saved review" || ko "$mode saved review: $(cat "$T/st/review-fix_1-x.md")"
  # #1834: the fix round answers its previous findings in a '## Resolved' section, so only that mode's prompt asks
  # for it -- a full review has findings, not answers, and its prompt must not name the heading.
  case "$mode" in
    fix) /usr/bin/grep -qF '## Resolved' "$T/prompt" && ok "fix prompt asks for the '## Resolved' answers (#1834)" || ko "fix prompt does not name '## Resolved'";;
    full) /usr/bin/grep -qF '## Resolved' "$T/prompt" && ko "full prompt names '## Resolved' (a full review has no answers)" || ok "full prompt does not name '## Resolved'";;
  esac
  miss=""; for needle in '## Blocking' '## Debt' 'file:line' 'most severe first' 'APPROVE or CHANGES'; do
    /usr/bin/grep -qF "$needle" "$T/prompt" || miss="$miss '$needle'"; done
  [ -z "$miss" ] && ok "$mode prompt carries the checklist format" || ko "$mode prompt lacks$miss"
  # #1835: the shape in the prompt is the text revformat.py printed, verbatim -- review.sh keeps no copy of its own to
  # drift from, so a wording the check cannot parse is a red test here rather than a review round lost in the loop.
  extra=""; [ $mode = fix ] && extra=--fix-round
  want=$(python3 "$T/s/revformat.py" --shape $extra 2>/dev/null)
  if [ -z "$want" ]; then ko "$mode prompt: revformat.py --shape printed nothing"; else
    case "$(cat "$T/prompt")" in *"$want"*) ok "$mode prompt carries the shape revformat.py printed (#1835)";; *) ko "$mode prompt does not carry the printed shape";; esac
  fi
  /usr/bin/grep -qF 'at most 21 lines' "$T/prompt" && ok "$mode prompt carries the format 21-line cap from review_max_lines" || ko "$mode prompt cap: $(/usr/bin/grep -i 'lines' "$T/prompt" | head -1)"
  /usr/bin/grep -q 'divergence from an accepted ADR' "$T/prompt" && /usr/bin/grep -q 'regression of behaviour' "$T/prompt" && /usr/bin/grep -q 'wire protocol or a persisted format' "$T/prompt" && /usr/bin/grep -q 'passes while lying' "$T/prompt" && ok "$mode prompt lists the closed blocking set" || ko "$mode prompt lacks the blocking list"
  /usr/bin/grep -qi 'filed task' "$T/prompt" && ok "$mode prompt states what DEBT costs" || ko "$mode prompt lacks the DEBT cost"
  /usr/bin/grep -qF 'Debt/nit-only findings require APPROVE, never CHANGES.' "$T/prompt" && ok "$mode prompt rules Debt/nit-only findings as APPROVE" || ko "$mode prompt does not rule Debt/nit-only findings as APPROVE"
done
/usr/bin/grep -qi 'previous' "$T/prompt" && ok "fix prompt answers the previous findings" || ko "fix prompt does not answer the previous findings"
# the cap is a named constant in the config, not a magic number in the prompt: another value, another prompt
printf '{"review_models":["claude:m"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"review_max_lines":33,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".*; bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
/usr/bin/grep -qF 'at most 33 lines' "$T/prompt" && ok "the cap follows review_max_lines (33)" || ko "the cap is hardcoded: $(/usr/bin/grep -i 'lines' "$T/prompt" | head -1)"
unset CLAUDE_TAGGED CLAUDE_PROMPT
# 6b) #1834 end to end: a fix round whose previous Blocking finding is answered under '## Resolved' is accepted and
# saved. review.sh asks revformat.py for the mode it is in, so the section a fix round needs is not the format failure
# it is in the full-review mode, and --summary reads blocking=0 for the saved answer (the debt-only path of ADR-0273).
printf '{"review_models":["claude:m"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"review_max_lines":33,"anthropic_models":[]}' > "$T/s/models.json"
printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — diverges from ADR-0001 — wrong — fix\n' > "$T/st/review-fix_1-x.md"; echo ccc333 > "$T/st/review-fix_1-x.sha"; rm -f "$T/st/cq/"*mformat*
export CLAUDE_RESOLVED=1
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: APPROVE" ] && ok "a fix round that resolves its previous Blocking finding under '## Resolved' is accepted (#1834)" || ko "fix round with '## Resolved': rc=$rc '$(head -1 "$T/st/review-fix_1-x.md")' $(cat "$T/st/cq/"*mformat* 2>/dev/null)"
[ "$(python3 "$T/s/revformat.py" --summary "$T/st/review-fix_1-x.md")" = "blocking=0 debt=1" ] && ok "the saved fix round reads blocking=0 and its Debt bullet" || ko "the saved fix round reads: '$(python3 "$T/s/revformat.py" --summary "$T/st/review-fix_1-x.md")'"
unset CLAUDE_RESOLVED
# 7) #1501: the aging leash must not kill the only reachable model. Gemini is agy-limited, Opus is
# outside its hours window and behind quota-guard; the survivor's own timeout (30 s) is longer than
# the leash (2 s). #1385: the answer is held by the test and released once the stub is inside its call,
# 3 s in -- past the 2 s leash and well inside the survivor's own 30 s timeout -- so the review must
# accept it (rc 0, verdict saved). A change that applied the leash, or the per-call deadline, to the
# sole reachable model rejects that answer and fails here.
h=$((10#$(date +%H))); a=$(( (h+1)%24 )); b=$(( (h+2)%24 )); printf -v wa '%02d-%02d' $a $b
printf '{"review_models":["gemini-3.8-flash-high","claude:claude-opus-5-5","claude:claude-haiku-4-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":["claude:claude-opus-5-5"],"hours":{"review_models":{"claude:claude-opus-5-5":"%s"}},"review_timeout_seconds_by_model":{"claude:claude-haiku-4-5":30}}' "$wa" > "$T/s/models.json"
echo $(( $(date +%s) + 3600 )) > "$T/st/agy-limited"; touch "$T/st/quota-guard"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/leash-held" "$T/leash-held.started"
CLAUDE_WAIT="$T/leash-held" SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1 SG_REVIEW_TIMEOUT=2 bash "$T/s/review.sh" --branch fix/1-x 8 >"$T/leash-out" 2>&1 & rp=$!
for _ in $(seq 1 100); do [ -e "$T/leash-held.started" ] && break; sleep 0.1; done   # the stub is inside its call
sleep 2.5   # past the 2 s leash, far inside the survivor's own 30 s timeout
touch "$T/leash-held"
wait $rp; rc=$?; out=$(cat "$T/leash-out")
[ $rc = 0 ] && head -1 "$T/st/review-fix_1-x.md" | grep -q 'VERDICT: APPROVE' && ok "leash + one reachable model: an answer past the leash and inside its own 30 s timeout is accepted" || ko "leash + one reachable (accepted): rc=$rc '$out' $(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null) cq=$(cat "$T/st/cq/"*mtimeout* 2>/dev/null)"
# 7b) the survivor's own timeout is its bound: with the answer never released, the review ends on that
# timeout (4 s), never on the 2 s leash, and the note names the configured value.
printf '{"review_models":["gemini-3.8-flash-high","claude:claude-opus-5-5","claude:claude-haiku-4-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":["claude:claude-opus-5-5"],"hours":{"review_models":{"claude:claude-opus-5-5":"%s"}},"review_timeout_seconds_by_model":{"claude:claude-haiku-4-5":4}}' "$wa" > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/leash-held" "$T/leash-held.started"
CLAUDE_WAIT="$T/leash-held" SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1 SG_REVIEW_TIMEOUT=2 bash "$T/s/review.sh" --branch fix/1-x 8 >"$T/leash-out" 2>&1; rc=$?; out=$(cat "$T/leash-out")
[ $rc != 0 ] && /usr/bin/grep -q 'claude:claude-haiku-4-5.*after 4s' "$T/st/cq/x__y#"*mtimeout* && head -1 "$T/st/review-fix_1-x.md" | grep -q BLOCKED && ok "leash + one reachable model: a held answer ends on its own 4 s timeout, not the 2 s leash" || ko "leash + one reachable (held): rc=$rc '$out' $(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null) cq=$(cat "$T/st/cq/"*mtimeout* 2>/dev/null)"
# 8) with several reachable models the leash still applies: two healthy claude entries, a 4 s answer held by the test,
# a 2 s leash -> the first dies at the leash, chain depth 1, BLOCKED. The hold keeps a slow wake from saving the answer.
printf '{"review_models":["claude:a","claude:b"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/agy-limited" "$T/st/quota-guard" "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(CLAUDE_SLEEP=4 SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1 SG_REVIEW_TIMEOUT=2 bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc != 0 ] && head -1 "$T/st/review-fix_1-x.md" | grep -q BLOCKED && /usr/bin/grep -q 'after 2s' "$T/st/cq/x__y#"*mtimeout* && ok "leash + several reachable models: still clamped" || ko "several reachable leash: rc=$rc '$out' $(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null) cq=$(cat "$T/st/cq/"*mtimeout* 2>/dev/null)"
[ -n "$(ls "$T/st/cq/")" ] && [ -z "$(ls "$T/st/cq/" | /usr/bin/grep -v '^x__y#8-status~')" ] && ok "every review note is queued as the one status comment of the branch, edited in place (#328)" || ko "a review note is not a status comment: $(ls "$T/st/cq/")"
# 8b) #1385: the box is saturated, so the watchdog wakes late -- here its 2 s sleep takes 5 s, and the free 4 s answer
# lands before the kill. The leash is a deadline, not a race: the review takes that answer as the timeout the watchdog
# owed, and the note names the configured lease, whatever the wake time. Before the fix this case read rc=0 and a saved
# 'VERDICT: APPROVE' -- the false red of issue #1385.
unset CLAUDE_WAIT   # this case must let the answer land, or the deadline is never tested
mkdir -p "$T/late"; printf '#!/bin/bash\ncase "${1:-}" in 2|2.0) exec /bin/sleep 5;; esac\nexec /bin/sleep "$@"\n' > "$T/late/sleep"; chmod +x "$T/late/sleep"
printf '{"review_models":["claude:a","claude:b"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(PATH="$T/late:$PATH" CLAUDE_SLEEP=4 SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1 SG_REVIEW_TIMEOUT=2 bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc != 0 ] && head -1 "$T/st/review-fix_1-x.md" | grep -q BLOCKED && /usr/bin/grep -q 'after 2s' "$T/st/cq/x__y#"*mtimeout* && ok "leash + a watchdog that wakes late: the deadline still holds" || ko "late watchdog: rc=$rc '$out' $(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null) cq=$(cat "$T/st/cq/"*mtimeout* 2>/dev/null)"
# 8c) #1385: a green answer inside its timeout is never rewritten to a timeout by the wall clock a caller reads after
# the call returns. The stub answers in 0.2 s under a 3 s timeout, and the poll interval (4 s) outlasts that timeout,
# so the post-call span is past the lease although the answer was not. The deadline belongs to the call: rc 0, the
# verdict saved, and no MODEL_TIMEOUT.
printf '{"review_models":["claude:a","claude:b"],"model_retry_budget":1,"review_timeout_seconds":3,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; unset CLAUDE_WAIT
out=$(SG_CAP_POLL=4 CLAUDE_SLEEP=0.2 SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1 bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && head -1 "$T/st/review-fix_1-x.md" | grep -q 'VERDICT: APPROVE' && ok "8c an answer inside its timeout survives the poll loop that outlasts it" || ko "8c a green answer was read as a timeout: rc=$rc '$out' $(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null) cq=$(cat "$T/st/cq/"*mtimeout* 2>/dev/null)"
# 9) #1820: review.sh takes the branch's own repository as its first argument -- the squad works in two repositories now,
# and a tooling branch is reviewed through the same chain. Every read of the branch (head, files, diff, the head that
# supersedes it) goes to the repository it was given; with no argument it is the profile's repository, as before.
printf '{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
unset CLAUDE_NARRATE CLAUDE_TAGGED; export GHLOG="$T/ghlog"; : > "$GHLOG"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; export CLAUDE_PROMPT="$T/prompt9"
out=$(bash "$T/s/review.sh" o/tool --branch fix/1-x 8 2>/dev/null); rc=$?
case "$out" in model=*) ok "the repository is the first argument (the review ran)";; *) ko "repo argument: rc=$rc '$out'";; esac
/usr/bin/grep -q 'repos/o/tool/compare' "$GHLOG" && ok "every read of the branch goes to the repository it was given" || ko "repo argument: no read of o/tool ($(head -3 "$GHLOG"))"
/usr/bin/grep -q 'repos/x/y/' "$GHLOG" && ko "repo argument: a read still went to the profile's repository ($(/usr/bin/grep -m1 'repos/x/y/' "$GHLOG"))" || ok "repo argument: nothing read the profile's repository"
/usr/bin/grep -q 'repo o/tool' "$CLAUDE_PROMPT" && ok "the prompt names the branch's own repository" || ko "prompt names: $(/usr/bin/grep -o 'of repo [^ ]*' "$CLAUDE_PROMPT" | head -1)"
unset CLAUDE_PROMPT; : > "$GHLOG"; rm -f "$T/st/review-fix_1-x".*
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
/usr/bin/grep -q 'repos/x/y/compare' "$GHLOG" && ok "with no argument the profile's repository is the one read" || ko "default repo: $(head -3 "$GHLOG")"
# 9b) (#1425) a branch that adds only tests is judged against the code at the base: the branch prompt says a behaviour the base
# already has is not missing, so a reviewer cannot report a producer that sits in origin/dev as absent.
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; export CLAUDE_PROMPT="$T/prompt9b"
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
/usr/bin/grep -q 'is not missing' "$CLAUDE_PROMPT" && /usr/bin/grep -q 'judged against the code at origin/' "$CLAUDE_PROMPT" && ok "9b the branch prompt judges a missing behaviour against the base" || ko "9b branch prompt lacks the base rule: '$out'"
unset CLAUDE_PROMPT
# 10) #1846: the normal path walks the eligible chain by the same measured latency the fast lane uses, not the config's
# order -- a review that lands on the slow end costs the PR minutes for the same verdict. Only a SUCCESSFUL try is a
# latency; ties and models with no record fall back to the configured order (never dropped, never given an invented
# number), and every skip still runs, so a latency never promotes an ineligible model.
printf '{"review_models":["claude:slow","claude:fast"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
printf 'x\t1\tclaude:slow\t200\t0\nx\t1\tclaude:fast\t20\t0\n' > "$T/st/review-times.tsv"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:fast*) case "$out" in *"fast lane"*) ko "normal-path ordering: product code was labelled a fast lane";; *) ok "normal path: the fastest measured model first (20 s over 200 s)";; esac;; *) ko "normal-path ordering: '$out'";; esac
# the fast lane is unchanged: the same order, and it still says so
rm -f "$T/st/review-fix_1-x".*
out=$(GH_FILES="docs/a.md" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in "model=claude:fast (fast lane"*) ok "fast lane: still the fastest model, still labelled";; *) ko "fast lane changed: '$out'";; esac
# a tie in the measured median falls back to the configured order
printf 'x\t1\tclaude:slow\t50\t0\nx\t1\tclaude:fast\t50\t0\n' > "$T/st/review-times.tsv"; rm -f "$T/st/review-fix_1-x".*
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:slow*) ok "a tie in the median keeps the configured order";; *) ko "tie fallback: '$out'";; esac
# a failed try is not a latency: rc != 0 rows never order the chain
printf 'x\t1\tclaude:slow\t200\t0\nx\t1\tclaude:fast\t20\t1\n' > "$T/st/review-times.tsv"; rm -f "$T/st/review-fix_1-x".*
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:slow*) ok "only successful tries order the chain";; *) ko "successful-tries-only: '$out'";; esac
# no record at all: the configured order stands and no model is dropped from the chain
: > "$T/st/review-times.tsv"; rm -f "$T/st/review-fix_1-x".*
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:slow*) ok "no record at all: the configured order stands";; *) ko "no-record fallback: '$out'";; esac
# no tools (#504): a model whose registry capabilities say tools:false reviews in one direct call to the gateway, thinking off, from the prompt alone --
# no tool, and a prompt that does not send it to read more code. Through `claude -p` DeepSeek V4.1 Flash explored the repo
# for 14 minutes, and with no tools it thought 32k tokens a turn and never answered: the CLI sends nothing that turns its
# thinking off. A model not listed keeps its tools and the CLI.
printf '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && { cp "${2#@}" "$CURL_BODY"; }; shift; done\ncat "$CURL_REPLY"\n' > "$T/bin/curl"; chmod +x "$T/bin/curl"
export CURL_BODY="$T/curl-body" CURL_REPLY="$T/curl-reply"
printf '{"type":"message","stop_reason":"end_turn","content":[{"type":"text","text":"VERDICT: APPROVE"}],"usage":{"input_tokens":900,"output_tokens":40}}' > "$CURL_REPLY"
printf '{"review_models":["claude:nt"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
: > "$T/st/review-times.tsv"; rm -f "$T/st/review-fix_1-x".* "$CURL_BODY"; export CLAUDE_ARGS="$T/args-nt" CLAUDE_PROMPT="$T/prompt-nt"; : > "$CLAUDE_ARGS"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ ! -s "$CLAUDE_ARGS" ] && jq -e '.thinking.type=="disabled" and (has("tools")|not) and .model=="nt"' "$CURL_BODY" >/dev/null 2>&1 && ok "no tools: one direct call, thinking off, no tool" || ko "no tools: not a direct no-thinking call: args=$(cat "$CLAUDE_ARGS") body=$(head -c 200 "$CURL_BODY" 2>/dev/null)"
jq -r '.messages[0].content' "$CURL_BODY" 2>/dev/null | /usr/bin/grep -q 'no tools' && ! jq -r '.messages[0].content' "$CURL_BODY" | /usr/bin/grep -q 'git show' && ok "no tools: the prompt judges from its own text" || ko "no tools: the prompt still sends it to read code"
[ $rc = 0 ] && head -1 "$T/st/review-fix_1-x.md" | /usr/bin/grep -q 'VERDICT: APPROVE' && /usr/bin/grep -q 'claude:nt	900	40' "$T/st/token-usage.tsv" && ok "no tools: the answer is the review, and its tokens are counted" || ko "no tools: answer not kept: rc=$rc $(cat "$T/st/review-fix_1-x.md" 2>/dev/null) $(tail -1 "$T/st/token-usage.tsv")"
# #783: a gateway that names its cache read another way (OpenAI prompt_tokens_details.cached_tokens, input_tokens_details,
# cache_read_tokens, cached_tokens) still reaches the ledger's cache_read field, as sdk_adapter.usage_of reads it.
for u in '"prompt_tokens_details":{"cached_tokens":77}' '"input_tokens_details":{"cached_tokens":77}' '"cache_read_tokens":77' '"cached_tokens":77'; do
  printf '{"type":"message","stop_reason":"end_turn","content":[{"type":"text","text":"VERDICT: APPROVE"}],"usage":{"input_tokens":900,"output_tokens":40,%s}}' "$u" > "$CURL_REPLY"
  rm -f "$T/st/review-fix_1-x".*; : > "$T/st/token-usage.tsv"
  GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
  /usr/bin/grep -q 'claude:nt	900	40	77' "$T/st/token-usage.tsv" && ok "cache read from ${u%%:*} reaches the ledger (#783)" || ko "cache read ${u%%:*} lost: $(tail -1 "$T/st/token-usage.tsv")"
done
printf '{"type":"message","stop_reason":"end_turn","content":[{"type":"text","text":"VERDICT: APPROVE"}],"usage":{"input_tokens":900,"output_tokens":40}}' > "$CURL_REPLY"
# #643: a chain id with a context suffix (claude:nt[1m]) matches its per-model settings written without it (claude:nt).
printf '{"review_models":["claude:nt[1m]"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$CURL_BODY"; : > "$CLAUDE_ARGS"
GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
[ ! -s "$CLAUDE_ARGS" ] && [ -s "$CURL_BODY" ] && ok "no tools: a [1m] chain id matches its entry without the suffix (#643)" || ko "no tools: claude:nt[1m] kept its tools: args=$(cat "$CLAUDE_ARGS")"
printf '{"review_models":["claude:s[1m]"],"review_timeout_seconds_by_model":{"claude:s":2},"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
CLAUDE_WAIT="$T/leash-held" GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1   # #1385: a held answer cannot outrun a late kill
/usr/bin/grep -q 'after 2s' "$T/st/cq/"*mtimeout* 2>/dev/null && ok "review_timeout_seconds_by_model: a [1m] chain id takes its timeout without the suffix (#643)" || ko "per-model timeout missed claude:s[1m]: $(cat "$T/st/cq/"* 2>/dev/null | head -3)"
# #925: a timed-out try is a MODEL_TIMEOUT only; a MODEL_FAIL beside it quoted the CLI's stderr notice as the cause.
/bin/ls "$T/st/cq" | /usr/bin/grep -q mfail && ko "#925 a timeout also queued a MODEL_FAIL: $(cat "$T/st/cq/"*mfail* 2>/dev/null)" || ok "#925 a timeout queues no MODEL_FAIL"
printf '{"review_models":["claude:nt"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
printf '{"type":"message","stop_reason":"max_tokens","content":[{"type":"text","text":"VERDICT: APPROVE"}],"usage":{"input_tokens":900,"output_tokens":8000}}' > "$CURL_REPLY"; rm -f "$T/st/review-fix_1-x".*
GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1; rc=$?
[ $rc != 0 ] && ! head -1 "$T/st/review-fix_1-x.md" 2>/dev/null | /usr/bin/grep -q 'VERDICT: APPROVE' && ok "no tools: an answer cut at max_tokens is no review" || ko "no tools: a cut answer was kept: rc=$rc"
# one repair turn: DeepSeek writes a sound review with one line outside the shape. The answer goes back once, with the
# check's own complaint, and the second answer is the review; both calls' tokens are counted.
printf '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && { n=$(ls "$CURL_BODY".* 2>/dev/null | wc -l); cp "${2#@}" "$CURL_BODY.$((n+1))"; }; shift; done\nn=$(ls "$CURL_BODY".* | wc -l); cat "$CURL_REPLY.$((n))"\n' > "$T/bin/curl"; chmod +x "$T/bin/curl"
printf '{"stop_reason":"end_turn","content":[{"type":"text","text":"VERDICT: CHANGES\\n## Blocking\\n- a.py:1 — the tally was retuned; verify it"}],"usage":{"input_tokens":900,"output_tokens":40}}' > "$CURL_REPLY.1"
printf '{"stop_reason":"end_turn","content":[{"type":"text","text":"VERDICT: CHANGES\\n## Blocking\\n- a.py:1 — the tally was retuned — a wrong count passes — re-anchor it"}],"usage":{"input_tokens":1000,"output_tokens":50}}' > "$CURL_REPLY.2"
rm -f "$T/st/review-fix_1-x".* "$CURL_BODY".*; : > "$T/st/token-usage.tsv"
GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && /usr/bin/grep -q 're-anchor it' "$T/st/review-fix_1-x.md" && jq -e '(.messages|length)==3 and .messages[1].role=="assistant" and (.messages[2].content|test("line 3"))' "$CURL_BODY.2" >/dev/null 2>&1 && /usr/bin/grep -q 'claude:nt	1900	90' "$T/st/token-usage.tsv" && ok "no tools: an answer outside the shape is repaired once, with the check's complaint" || ko "no tools: no repair turn: rc=$rc $(head -3 "$T/st/review-fix_1-x.md" 2>/dev/null) $(tail -1 "$T/st/token-usage.tsv")"
rm -f "$T/bin/curl" "$CURL_BODY".* "$CURL_REPLY".*; unset CURL_BODY CURL_REPLY
printf '{"review_models":["claude:x"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".*; : > "$CLAUDE_ARGS"
GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
/usr/bin/grep -q -- '--tools Read Grep Glob Bash' "$CLAUDE_ARGS" && /usr/bin/grep -q 'git show' "$CLAUDE_PROMPT" && ok "a model not listed keeps its tools and its prompt" || ko "an unlisted model lost its tools: $(cat "$CLAUDE_ARGS")"
unset CLAUDE_ARGS CLAUDE_PROMPT
# review_order "config" (#503): the owner picks the head of the chain (a cheaper model first, so the token cap holds), and the
# measured latency does not put a faster, dearer model in front of it.
printf '{"review_models":["claude:slow","claude:fast"],"review_order":"config","model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
printf 'x\t1\tclaude:slow\t200\t0\nx\t1\tclaude:fast\t20\t0\n' > "$T/st/review-times.tsv"; rm -f "$T/st/review-fix_1-x".*
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:slow*) ok "review_order config: the configured head is tried first, whatever the latency";; *) ko "review_order config: '$out'";; esac
# a model with no record is ranked after the measured ones, never dropped: the measured head is skipped by quota,
# and the unmeasured tail must still be reachable (a chain that dropped it would end BLOCKED untried)
printf '{"review_models":["claude:fast","claude:unmeasured"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":["claude:fast"]}' > "$T/s/models.json"
printf 'x\t1\tclaude:fast\t20\t0\n' > "$T/st/review-times.tsv"; touch "$T/st/quota-guard"; rm -f "$T/st/review-fix_1-x".*
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:unmeasured*) ok "a model with no record is not dropped when the measured head is skipped";; *) ko "no-record model dropped: '$out'";; esac
rm -f "$T/st/quota-guard"
# the skip wins over the latency: the fast model outside its window is not promoted, the slower one runs
h=$((10#$(date +%H))); a=$(( (h+1)%24 )); b=$(( (h+2)%24 )); printf -v wc '%02d-%02d' $a $b
printf '{"review_models":["claude:slow","claude:fast"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[],"hours":{"review_models":{"claude:fast":"%s"}}}' "$wc" > "$T/s/models.json"
printf 'x\t1\tclaude:slow\t200\t0\nx\t1\tclaude:fast\t20\t0\n' > "$T/st/review-times.tsv"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:slow*) ok "an ineligible model is not promoted by its latency ($wc)";; *) ko "skip before ordering: '$out'";; esac
# 11) the review call carries --strict-mcp-config: a non-interactive `claude` call otherwise loads the owner's own MCP
# servers (Notion, Slack, ...) and pays tens of thousands of input tokens of tool schemas and server instructions
# before a line of the review. A review needs no MCP server, so the flag is on the call.
printf '{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; export CLAUDE_ARGS="$T/claude-args"; : > "$CLAUDE_ARGS"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
/usr/bin/grep -q -- '--strict-mcp-config' "$CLAUDE_ARGS" && ok "the review call sets --strict-mcp-config (no owner MCP servers in a review)" || ko "the review call misses --strict-mcp-config: $(cat "$CLAUDE_ARGS" 2>/dev/null)"
unset CLAUDE_ARGS
# 12) #483: a --worktree review watches the child's own HEAD, never the checkout's. The grok and gemini legs run their CLI
# from SG_CHECKOUT, and the head watch ran there too: it read the checkout's HEAD, called the child's head moved within
# 10 s, and every such review of a child ended "BLOCKED (head moved during the review)" with no verdict.
# grok-4.7 is a no-tools reviewer (#1373): it reaches the gateway before its CLI, and a live gateway here would let a
# test answer for a stub. A closed port, scoped to these grok-4.7 cases, keeps the suite's own environment untouched.
export ANTHROPIC_BASE_URL=http://127.0.0.1:9
W="$T/child"; /usr/bin/git init -q -b main "$W"; /usr/bin/git -C "$W" -c user.email=a@b -c user.name=t commit -q --allow-empty -m base
/usr/bin/git -C "$W" checkout -q -b gui/9; echo child > "$W/c.txt"; /usr/bin/git -C "$W" add c.txt   # a real change: an empty diff is a refused read (#846)
/usr/bin/git -C "$W" -c user.email=a@b -c user.name=t commit -q -m child
/usr/bin/git init -q -b main "$T/co"; /usr/bin/git -C "$T/co" -c user.email=a@b -c user.name=t commit -q --allow-empty -m checkout
sed -i '' "s|SG_CHECKOUT=$T;|SG_CHECKOUT=$T/co;|" "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "$GROK_ARGS"; sleep 4; echo "VERDICT: APPROVE"\n' > "$T/bin/grok"; chmod +x "$T/bin/grok"
printf '{"review_models":["grok-4.7"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
out=$(cd "$W" && bash "$T/s/review.sh" --worktree main 9 2>/dev/null); rc=$?
[ $rc = 0 ] && head -1 "$T/st/review-gui_9.md" | grep -q APPROVE && [ "$(cat "$T/st/review-gui_9.sha" 2>/dev/null)" = "$(/usr/bin/git -C "$W" rev-parse HEAD)" ] \
  && ok "12 a worktree review run from the checkout keeps its verdict for the child's head" \
  || ko "12 a worktree review read the checkout's HEAD: rc=$rc '$(head -1 "$T/st/review-gui_9.md" 2>/dev/null)' sha=$(cat "$T/st/review-gui_9.sha" 2>/dev/null)"
# 13) a headless review must not run a CLI in a mode that auto-denies its own tools. grok in --permission-mode plan and
# agy in --mode plan both print nothing ("a tool required the command permission that headless mode cannot prompt for,
# so it was auto-denied") and the review ends "BLOCKED (every review model failed)" with no verdict.
export GROK_ARGS="$T/grok-args"; : > "$GROK_ARGS"; rm -f "$T/st/review-gui_9".*
out=$(cd "$W" && bash "$T/s/review.sh" --worktree main 9 2>/dev/null); rc=$?
/usr/bin/grep -q -- '--permission-mode plan' "$GROK_ARGS" \
  && ko "13 the grok review runs in plan mode, which headless grok auto-denies: $(cat "$GROK_ARGS")" \
  || ok "13 the grok review does not run in plan mode"
# 13b) grok always reasons and defaults to high effort: a review that names no effort runs minutes long and times
# out (#1848). The chain entry's effort is passed, and an entry that names none gets medium.
/usr/bin/grep -q -- '--reasoning-effort medium' "$GROK_ARGS" \
  && ok "13b the grok review passes the chain's reasoning effort (medium by default)" \
  || ko "13b the grok review passes no reasoning effort, so grok runs at its slow high default: $(cat "$GROK_ARGS")"
unset GROK_ARGS
printf '#!/bin/bash\necho "$*" >> "$AGY_ARGS"; echo "VERDICT: APPROVE"\n' > "$T/bin/agy"; chmod +x "$T/bin/agy"
printf '{"review_models":["gemini-3.8-flash-medium"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
export AGY_ARGS="$T/agy-args"; : > "$AGY_ARGS"; rm -f "$T/st/review-gui_9".*
out=$(cd "$W" && bash "$T/s/review.sh" --worktree main 9 2>/dev/null); rc=$?
/usr/bin/grep -q -- '--mode plan' "$AGY_ARGS" && ! /usr/bin/grep -q -- '--dangerously-skip-permissions' "$AGY_ARGS" \
  && ko "13 the agy review runs in plan mode with nothing to approve its tools: $(cat "$AGY_ARGS")" \
  || ok "13 the agy review can use its tools headless"
unset AGY_ARGS ANTHROPIC_BASE_URL
# A CLI run that ends in an error is not a review (#501): the JSON carries is_error and the subtype, and .result holds only
# the preamble the model produced before the run ended. The call is a MODEL_FAIL naming the subtype, the chain moves on,
# and the raw JSON stays beside the answer so the next occurrence is diagnosed without a re-run.
export CLAUDE_ERRJSON=1
printf '{"review_models":["claude:bad"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x.sha" "$T/st/review-fix_1-x.md" "$T/st/review-fix_1-x.json" "$T/st/cq/"*mfail* "$T/st/cq/"*mformat*
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
head -1 "$T/st/review-fix_1-x.md" 2>/dev/null | /usr/bin/grep -q "I'll review" && ko "an error answer was saved as the review" || ok "an error answer is never saved as the review"
/usr/bin/grep -q 'error_max_turns' "$T/st/cq/x__y#"*mfail* 2>/dev/null && ok "the MODEL_FAIL names the subtype" || ko "the subtype is lost: $(cat "$T/st/cq/"*mfail* 2>/dev/null)"
[ -s "$T/st/review-fix_1-x.json" ] && ok "the raw JSON is kept beside the answer" || ko "no raw JSON was kept"
printf '{"review_models":["claude:bad","claude:good"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x.sha" "$T/st/cq/"*mfail* "$T/st/cq/"*mswitch*
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && case "$out" in model=claude:good*) ok "the chain moves on to the next model after an error answer";; *) ko "chain after error: '$out'";; esac || ko "chain after error: rc=$rc '$out'"
# #631: the move to the next model is queued once, as one MODEL_SWITCH status that names both models.
sw=$(cat "$T/st/cq/"*mswitch* 2>/dev/null)
[ "$(/bin/ls "$T/st/cq/" | /usr/bin/grep -c mswitch)" = 1 ] && case "$sw" in "MODEL_SWITCH review claude:bad -> claude:good "*) true;; *) false;; esac \
  && ok "a failed model then an approving one queues one MODEL_SWITCH naming both" || ko "MODEL_SWITCH: '$sw'"
unset CLAUDE_ERRJSON
# 13b) #511: the agy-limited marker takes its reset from `agy -p /quota` (the reading the hub's q_agy uses), never from the
# error text: the error said "Resets in 1h" while /quota said the Gemini weekly limit was spent for six more days, so the
# chain tried Gemini at each false reset, failed and marked it again.
cp "$(dirname "$src")/agyquota.py" "$T/s/" 2>/dev/null
cat > "$T/bin/agy" <<'J'
#!/bin/bash
if [ "$*" = "-p /quota" ]; then
  printf 'Gemini Models\tWeekly Limit Remaining\t0%%\t%s\n' "$(date -u -r "$AGY_RESET" +%FT%TZ)"
  printf 'Claude and GPT models\tWeekly Limit Remaining\t100%%\t2030-01-01T00:00:00Z\n'; exit 0; fi
echo "RESOURCE_EXHAUSTED: Individual quota reached. Resets in 1h0m0s" >&2; exit 1
J
export SG_SHARED="$T/st"   # the marker is the suite's own, never the live one under ~/.cache
chmod +x "$T/bin/agy"; export AGY_RESET=$(( $(date +%s) + 6*86400 + 5*3600 ))
printf '{"review_models":["gemini-3.8-flash-medium"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-gui_9".* "$T/st/agy-limited" "$T/st/cq/"*
out=$(cd "$W" && bash "$T/s/review.sh" --worktree main 9 2>/dev/null); rc=$?
[ "$(cat "$T/st/agy-limited" 2>/dev/null)" = "$AGY_RESET" ] \
  && ok "13b the agy-limited marker holds the reset /quota reports, not the error's" \
  || ko "13b the agy-limited marker is the error's false reset: marker=$(cat "$T/st/agy-limited" 2>/dev/null) quota=$AGY_RESET"
# a /quota that cannot be read leaves the error's own reset, then the 6 h fallback, as before
printf '#!/bin/bash\n[ "$*" = "-p /quota" ] && exit 1\necho "RESOURCE_EXHAUSTED: Resets in 1h0m0s" >&2; exit 1\n' > "$T/bin/agy"
rm -f "$T/st/review-gui_9".* "$T/st/agy-limited" "$T/st/cq/"*; t=$(date +%s)
out=$(cd "$W" && bash "$T/s/review.sh" --worktree main 9 2>/dev/null)
m=$(cat "$T/st/agy-limited" 2>/dev/null); [ -n "$m" ] && [ "$m" -ge $((t+3500)) ] && [ "$m" -le $((t+3700)) ] \
  && ok "13b without a /quota reading the error's reset still holds" || ko "13b fallback to the error's reset: marker=$m"
unset AGY_RESET SG_SHARED; rm -f "$T/bin/agy" "$T/st/agy-limited"
# 14) the timeout must reach the process the CLI spawns, and the wait must say it is still working. The alarm is
# set on the CLI, but the CLI execs the model in a child that ignores the alarm, so the review runs past its
# timeout with no output and no line anywhere to show it is alive (#491 follow-up: a 360 s review ran 9 min, silent).
printf '#!/bin/bash\n( sleep 23; echo "VERDICT: APPROVE" ) &\nwait\n' > "$T/bin/claude"; chmod +x "$T/bin/claude"
printf '{"review_models":["claude:hung"],"model_retry_budget":1,"review_timeout_seconds":6,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-gui_9".* "$T/st/cq/"*mtimeout*; t=$(date +%s)
out=$(cd "$W" && SG_CAP_HEARTBEAT=3 bash "$T/s/review.sh" --worktree main 9 > "$T/hb"); rc=$?; el=$(( $(date +%s) - t ))
sleep 1; left=$(ps -ax -o pid,pgid,ppid,command | /usr/bin/grep -c '[s]leep 23'); who=$(ps -ax -o pid,pgid,ppid,command | /usr/bin/grep '[s]leep 23')
# #1385: the elapsed time proves the kill came no earlier than the timeout; the upper bound only catches a hang, so it
# keeps a margin a saturated machine cannot cross (a loaded box stretched this case past 20 s).
[ $rc != 0 ] && [ $el -ge 6 ] && [ $el -lt 60 ] && [ "$left" = 0 ] && /usr/bin/grep -q 'after 6s' "$T/st/cq/"*mtimeout* 2>/dev/null \
  && ok "14 a child the CLI spawns is killed at the timeout, and nothing it spawned survives (${el}s)" \
  || ko "14 the spawned child outlives the timeout: rc=$rc ${el}s survivors=$left [$who] $(cat "$T/st/cq/"*mtimeout* 2>/dev/null)"
/usr/bin/grep -q 'still running' "$T/hb" && ok "14 the wait says the review is still running" || ko "14 the wait is silent: $(cat "$T/hb")"

# 15) #622: grok answers 402 "usage balance exhausted" on every call. A balance has no reset, so the first such answer
# writes $SG_SHARED/grok-limited and every later review skips grok while the marker is fresh (#1417: SG_GROK_LIMIT_TTL, then one probe).
export SG_SHARED="$T/st"
printf '#!/bin/bash\necho x >> "$T/grokcalls"\necho "Error: 402 Payment Required: Grok Build usage balance exhausted" >&2; exit 1\n' | sed "s|\$T|$T|" > "$T/bin/grok"
printf '#!/bin/bash\ncat >/dev/null; echo "VERDICT: APPROVE"\n' > "$T/bin/claude"; chmod +x "$T/bin/grok" "$T/bin/claude"
printf '{"review_models":["grok-4.6","claude:ok"],"review_order":"config","model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/grok-limited" "$T/grokcalls" "$T/st/cq/"*
bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
[ -e "$T/st/grok-limited" ] && ok "15 a 402 balance answer writes grok-limited" || ko "15 no grok-limited marker after a 402"
rm -f "$T/st/review-fix_1-x".* "$T/grokcalls" "$T/st/cq/"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
[ ! -e "$T/grokcalls" ] && case "$out" in model=claude:ok*) true;; *) false;; esac && ok "15 grok-limited skips grok on the next review" || ko "15 grok tried again: calls=$(wc -l < "$T/grokcalls" 2>/dev/null) '$out'"
 /usr/bin/grep -q 'next probe' "$T/st/cq/"*mquota* 2>/dev/null && ok "15 the skip line names the next probe time" || ko "15 skip line: $(cat "$T/st/cq/"*mquota* 2>/dev/null)"
rm -f "$T/st/grok-limited" "$T/bin/grok"
# 16) #622, #634: with one reachable model, an answer outside the checklist shape gets its repair turn, not BLOCKED at once.
printf '#!/bin/bash\ncat >/dev/null; echo x >> "$T/ccalls"; [ "$(wc -l < "$T/ccalls")" -ge 2 ] && { echo "VERDICT: APPROVE"; exit 0; }; printf "Checking.\\nVERDICT: CHANGES\\n1. a finding\\n"\n' | sed "s|\$T|$T|g" > "$T/bin/claude"; chmod +x "$T/bin/claude"
printf '{"review_models":["claude:only"],"model_retry_budget":2,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/ccalls" "$T/st/cq/"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && case "$out" in model=claude:only*) true;; *) false;; esac && ok "16 one reachable model: a format failure is retried" || ko "16 one reachable model ended at the format failure: rc=$rc '$out' $(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)"
unset SG_SHARED

# 17) #624: two callers review one branch at once (runreview.sh and childcc.py, both through childreview.sh). The first
# chain fails, the second approves. Neither reads the other's files: the failed chain's BLOCKED line is never the
# approving model's answer, and the approving review exits 0 with APPROVE on line 1.
cat > "$T/bin/claude" <<J
#!/bin/bash
cat >/dev/null; echo "\$CLAUDE_MODE" >> "$T/ccalls"
case "\$CLAUDE_MODE" in fail) sleep 3; echo "API Error: 529 overloaded_error"; exit 1;; *) sleep 6; echo "VERDICT: APPROVE";; esac
J
chmod +x "$T/bin/claude"
printf '{"review_models":["claude:only"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/ccalls" "$T/st/cq/"*
(CLAUDE_MODE=fail bash "$T/s/review.sh" --branch fix/1-x 8 > "$T/c1.out" 2>&1; echo $? > "$T/c1.rc") & c1=$!
sleep 1
(CLAUDE_MODE=ok bash "$T/s/review.sh" --branch fix/1-x 8 > "$T/c2.out" 2>&1; echo $? > "$T/c2.rc") & c2=$!
wait $c1 $c2
[ "$(cat "$T/c2.rc")" = 0 ] && /usr/bin/grep -q '^model=claude:only.*VERDICT: APPROVE' "$T/c2.out" && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: APPROVE" ] \
  && ok "17 two reviews of one branch at once: the approving one exits 0 with APPROVE" \
  || ko "17 concurrent reviews: rc=$(cat "$T/c2.rc") '$(head -2 "$T/c2.out" | tr '\n' ' ')' md='$(head -1 "$T/st/review-fix_1-x.md")'"
/usr/bin/grep -qs 'BLOCKED' "$T/st/cq/"*mformat* && ko "17 one review read the other's BLOCKED line as its answer: $(cat "$T/st/cq/"*mformat*)" || ok "17 no review reads the other's BLOCKED line"
[ "$(cat "$T/c1.rc")" = 1 ] && ok "17 the failed chain exits 1" || ko "17 the failed chain: rc=$(cat "$T/c1.rc")"
# 18) #624: a review that waited for another one of the same head reuses its verdict and calls no model.
: > "$T/ccalls"; out=$(CLAUDE_MODE=ok bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && [ ! -s "$T/ccalls" ] && case "$out" in model=claude:only*"VERDICT: APPROVE"*) true;; *) false;; esac \
  && ok "18 a head already reviewed is reused, with no model call" || ko "18 reuse: rc=$rc calls=$(wc -l < "$T/ccalls") '$out'"
# 19) #624: two models that both fail the shape in one pass keep two MODEL_FORMAT lines; the second never overwrites the first.
printf '#!/bin/bash\ncat >/dev/null; printf "Checking.\\nVERDICT: CHANGES\\n1. a finding\\n"\n' > "$T/bin/claude"; chmod +x "$T/bin/claude"
printf '{"review_models":["claude:one","claude:two"],"review_order":"config","model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
/usr/bin/grep -qs 'claude:one' "$T/st/cq/"*mformat* && /usr/bin/grep -qs 'claude:two' "$T/st/cq/"*mformat* \
  && ok "19 each model's MODEL_FORMAT keeps its own status file" || ko "19 status files: $(/bin/ls "$T/st/cq")"
/bin/ls "$T/st/" | /usr/bin/grep -q 'review-fix_1-x\.md\.[0-9]' && ko "19 a private attempt file was left: $(/bin/ls "$T/st")" || ok "19 no private attempt file is left"

# 20) #625: with several reachable models, an answer outside the shape goes back once to the same model with the check's
# complaint, before the chain moves on. The second answer is the shape: APPROVE by that model, no BLOCKED.
cat > "$T/bin/claude" <<J
#!/bin/bash
cat > "$T/prompt.\$(wc -l < "$T/ccalls" 2>/dev/null | tr -d ' ')"; echo "\$*" >> "$T/ccalls"
[ "\$(wc -l < "$T/ccalls")" -ge 2 ] && { echo "VERDICT: APPROVE"; exit 0; }
printf 'VERDICT: CHANGES\\n## Blocking\\n- a finding with no place\\n'
J
chmod +x "$T/bin/claude"
printf '{"review_models":["claude:one","claude:two"],"review_order":"config","model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/ccalls" "$T/prompt".*; : > "$T/ccalls"; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && case "$out" in model=claude:one*"VERDICT: APPROVE"*) true;; *) false;; esac && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: APPROVE" ] \
  && ok "20 a format failure is repaired once by the same model" || ko "20 repair: rc=$rc '$out' md='$(head -1 "$T/st/review-fix_1-x.md")'"
[ "$(/usr/bin/grep -c -- '--model one' "$T/ccalls")" = 2 ] && ! /usr/bin/grep -q -- '--model two' "$T/ccalls" && ok "20 the repair is the same model, once" || ko "20 calls: $(cat "$T/ccalls")"
/usr/bin/grep -q 'outside the shape' "$T/prompt.1" 2>/dev/null && /usr/bin/grep -q 'a finding with no place' "$T/prompt.1" && ok "20 the repair prompt carries the complaint and the answer" || ko "20 repair prompt: $(tail -5 "$T/prompt.1" 2>/dev/null)"
/bin/ls "$T/st/" | /usr/bin/grep -q 'review-fix_1-x\.md\.[0-9]' && ko "20 a private attempt file was left" || ok "20 no private attempt file is left"

# 21) #632: a full review asks for JSON on every model path and renders it. claude -p carries --json-schema and its answer
# is the envelope's structured_output; codex carries --output-schema <file>; the prompt (agy, grok, the direct path)
# carries the schema. The saved review-<key>.md is the rendered checklist, and the raw JSON is kept as
# review-<key>.answer.json, never as review-<key>.json (a failed claude envelope).
J='{"verdict":"CHANGES","blocking":[{"location":"a.cs:1","defect":"diverges from ADR-0001","failure":"wrong","fix":"fix","repro":"false"}],"debt":[],"resolved":[]}'
cat > "$T/bin/claude" <<C
#!/bin/bash
cat > "$T/prompt21"; echo "\$*" > "$T/args21"
s='$J'; [ -s "$T/j21" ] && s=\$(cat "$T/j21")
jq -cn --argjson s "\$s" '{type:"result",subtype:"success",is_error:false,result:"",structured_output:\$s,usage:{input_tokens:10,output_tokens:5}}'
C
cat > "$T/bin/codex" <<C
#!/bin/bash
cat > "$T/prompt21c"; echo "\$*" > "$T/args21c"; o=""
while [ \$# -gt 0 ]; do case "\$1" in -o) o=\$2; shift;; --output-schema) cp "\$2" "$T/schema21c"; shift;; esac; shift; done
printf '%s\n' '$J' > "\$o"; printf 'tokens used\n54.227\n'
C
chmod +x "$T/bin/claude" "$T/bin/codex"
printf '{"review_models":["claude:m"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
/usr/bin/grep -q -- '--json-schema' "$T/args21" && ok "21 claude -p asks for JSON with --json-schema" || ko "21 claude args: $(cat "$T/args21")"
/usr/bin/grep -q '"verdict"' "$T/prompt21" && ok "21 the full prompt carries the schema" || ko "21 the full prompt has no schema"
[ $rc = 0 ] && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: CHANGES" ] && /usr/bin/grep -q '^- a.cs:1 — diverges from ADR-0001 — wrong — fix — repro: false$' "$T/st/review-fix_1-x.md" \
  && ok "21 claude: the JSON answer is rendered into review-<key>.md" || ko "21 claude render: rc=$rc '$out' $(cat "$T/st/review-fix_1-x.md")"
jq -e '.verdict=="CHANGES"' "$T/st/review-fix_1-x.answer.json" >/dev/null 2>&1 && [ ! -e "$T/st/review-fix_1-x.json" ] \
  && ok "21 the raw JSON is kept as review-<key>.answer.json" || ko "21 raw JSON: $(/bin/ls "$T/st" | /usr/bin/grep fix_1-x)"
printf '{"review_models":["gpt-x"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
jq -e '.properties.verdict.enum==["APPROVE","CHANGES"]' "$T/schema21c" >/dev/null 2>&1 && ok "21 codex asks for JSON with --output-schema" || ko "21 codex args: $(cat "$T/args21c")"
[ $rc = 0 ] && /usr/bin/grep -q '^- a.cs:1 — diverges from ADR-0001 — wrong — fix — repro: false$' "$T/st/review-fix_1-x.md" && ok "21 codex: the JSON answer is rendered" || ko "21 codex render: rc=$rc '$out' $(cat "$T/st/review-fix_1-x.md")"
# #764: codex read an unrelated global skill (graphify SKILL.md) during a review, so the prompt forbids every skill file.
/usr/bin/grep -q 'Open no skill file (no SKILL.md under ~/.agents, ~/.codex or ~/.claude) and use no skill' "$T/prompt21c" && ok "21 the codex prompt forbids skill files (#764)" || ko "21 the codex prompt has no skill line (#764)"
# #765: codex prints 'tokens used' and the count, with a locale thousands separator; the ledger keeps it as the input count
/usr/bin/grep -q '	gpt-x	54227	0	0$' "$T/st/token-usage.tsv" && ok "21 codex: its token count reaches the ledger" || ko "21 codex tokens: $(/usr/bin/grep 'gpt-x' "$T/st/token-usage.tsv" | tail -1)"
# 22) #633: a fix round answers in JSON with the same schema. claude -p carries --json-schema, the prompt carries the
# schema, and the previous findings it reads are the rendered markdown. A fixed finding in resolved renders the
# '## Resolved' section the fix-round check reads, and the next fix round reads that rendered review unchanged.
# With no Blocking finding left and one Debt finding, it is saved as an approval (#653); the model wrote CHANGES.
printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — x — y — z\n' > "$T/st/review-fix_1-x.md"; echo ccc333 > "$T/st/review-fix_1-x.sha"
printf '{"review_models":["claude:m"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
cat > "$T/j21" <<'R'
{"verdict":"CHANGES","blocking":[],"debt":[{"location":"b.cs:2","defect":"naming","failure":"nobody can tell what it names","fix":"rename it","repro":""}],
 "resolved":[{"location":"a.cs:1","defect":"x","failure":"fixed: the ADR is followed now","fix":"UI.Tests/a.cs","repro":""}]}
R
rm -f "$T/st/cq/"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
/usr/bin/grep -q 'FIX ROUND' "$T/prompt21" && /usr/bin/grep -q -- '--json-schema' "$T/args21" && ok "22 a fix round asks for JSON with --json-schema" || ko "22 fix round args: $(cat "$T/args21")"
sed -n '1,/=== BRANCH/p' "$T/prompt21" | /usr/bin/grep -q '"resolved"' && ok "22 the fix prompt carries the schema" || ko "22 the fix prompt has no schema"
/usr/bin/grep -q 'Open no skill file (no SKILL.md under ~/.agents, ~/.codex or ~/.claude) and use no skill' "$T/prompt21" && ok "22 the fix prompt forbids skill files (#764)" || ko "22 the fix prompt has no skill line (#764)"
sed -n '/=== PREVIOUS FINDINGS/,/=== DELTA/p' "$T/prompt21" | /usr/bin/grep -q '^- a.cs:1 — x — y — z$' && ok "22 the fix prompt reads the previous findings as markdown" || ko "22 previous findings: $(sed -n '/=== PREVIOUS/,/=== DELTA/p' "$T/prompt21")"
[ $rc = 0 ] && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: APPROVE" ] && /usr/bin/grep -q '^## Resolved$' "$T/st/review-fix_1-x.md" \
  && /usr/bin/grep -q '^- a.cs:1 — x — fixed: the ADR is followed now — UI.Tests/a.cs$' "$T/st/review-fix_1-x.md" \
  && ok "22 the fix-round JSON answer renders the '## Resolved' section" || ko "22 fix render: rc=$rc '$out' $(cat "$T/st/review-fix_1-x.md")"
[ "$(python3 "$T/s/revformat.py" --summary "$T/st/review-fix_1-x.md")" = "blocking=0 debt=1" ] && sed '1s/.*/VERDICT: CHANGES/' "$T/st/review-fix_1-x.md" | python3 "$T/s/revformat.py" --fix-round - >/dev/null \
  && ok "22 the rendered fix round passes the fix-round check, blocking=0 debt=1" || ko "22 fix check: $(sed '1s/.*/VERDICT: CHANGES/' "$T/st/review-fix_1-x.md" | python3 "$T/s/revformat.py" --fix-round -)"
jq -e '.resolved|length==1' "$T/st/review-fix_1-x.answer.json" >/dev/null 2>&1 && ok "22 the fix round keeps its raw JSON answer" || ko "22 no raw fix-round JSON"
# an approval starts no fix round (#653), so line 1 is set back to CHANGES to start the next one
sed '1s/.*/VERDICT: CHANGES/' "$T/st/review-fix_1-x.md" > "$T/r653" && mv -f "$T/r653" "$T/st/review-fix_1-x.md"; echo ccc333 > "$T/st/review-fix_1-x.sha"; bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
sed -n '/=== PREVIOUS FINDINGS/,/=== DELTA/p' "$T/prompt21" | /usr/bin/grep -q '^- a.cs:1 — x — fixed: the ADR is followed now — UI.Tests/a.cs$' \
  && ok "22 the next fix round reads the rendered review as its previous findings" || ko "22 next previous findings: $(sed -n '/=== PREVIOUS/,/=== DELTA/p' "$T/prompt21")"
rm -f "$T/j21"
rm -f "$T/bin/codex"

# 23) #634: a JSON answer off the schema gets one repair turn by the same model, with the parser's error, also when one
# model is all that is reachable. A second answer off the schema ends that model's turn: no plain retry follows.
cat > "$T/bin/claude" <<J
#!/bin/bash
cat > "$T/prompt.\$(wc -l < "$T/ccalls" 2>/dev/null | tr -d ' ')"; echo "\$*" >> "$T/ccalls"
a='{"verdict":"CHANGES","blocking":[{"location":"a.cs:1","defect":"d"}],"debt":[],"resolved":[]}'
[ -e "$T/fix23" ] && [ "\$(wc -l < "$T/ccalls")" -ge 2 ] && a='{"verdict":"APPROVE","blocking":[],"debt":[],"resolved":[]}'
jq -cn --arg a "\$a" '{type:"result",subtype:"success",is_error:false,result:\$a,usage:{input_tokens:10,output_tokens:5}}'
J
chmod +x "$T/bin/claude"; touch "$T/fix23"
printf '{"review_models":["claude:only"],"model_retry_budget":3,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/prompt".*; : > "$T/ccalls"; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: APPROVE" ] && [ "$(wc -l < "$T/ccalls" | tr -d ' ')" = 2 ] \
  && ok "23 one reachable model: a JSON answer off the schema is repaired once" || ko "23 one-model repair: rc=$rc '$out' calls=$(wc -l < "$T/ccalls")"
/usr/bin/grep -q 'outside the shape' "$T/prompt.1" 2>/dev/null && /usr/bin/grep -q 'blocking\[0\].failure is missing or empty' "$T/prompt.1" \
  && ok "23 the repair prompt carries the parser's error" || ko "23 repair prompt: $(tail -3 "$T/prompt.1" 2>/dev/null)"
/usr/bin/grep -qs 'the same model answers once more' "$T/st/cq/"*mformat* && ok "23 the MODEL_FORMAT line names the repair turn" || ko "23 mformat: $(cat "$T/st/cq/"*mformat* 2>/dev/null)"
rm -f "$T/fix23" "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/prompt".*; : > "$T/ccalls"; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc != 0 ] && head -1 "$T/st/review-fix_1-x.md" | /usr/bin/grep -q BLOCKED && [ "$(wc -l < "$T/ccalls" | tr -d ' ')" = 2 ] \
  && ok "23 one reachable model: a failed repair ends the turn after two calls" || ko "23 failed repair: rc=$rc calls=$(wc -l < "$T/ccalls") md='$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)'"
printf '{"review_models":["claude:one","claude:two"],"review_order":"config","model_retry_budget":3,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/prompt".*; : > "$T/ccalls"; bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
[ "$(/usr/bin/grep -c -- '--model one' "$T/ccalls")" = 2 ] && [ "$(/usr/bin/grep -c -- '--model two' "$T/ccalls")" = 2 ] \
  && ok "23 several reachable models: one repair turn each, then the next model" || ko "23 several models: $(cat "$T/ccalls")"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/prompt".* "$T/ccalls"
# 24) #653: a CHANGES with only Debt findings is saved as an APPROVE (the model's verdict named after it), so wave.sh, childcc
# and the fix round read it as an approval; a CHANGES with a Blocking finding is saved as the model wrote it.
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > /dev/null; printf 'VERDICT: CHANGES\n'; [ -n "$CLAUDE_BLOCKING" ] && printf '## Blocking\n- a.cs:1 — diverges from ADR-0001 — wrong — fix\n'
printf '## Debt\n- b.cs:2 — naming — nobody can tell what it names — rename it\n'
J
chmod +x "$T/bin/claude"
printf '{"review_models":["claude:m"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
l1=$(head -1 "$T/st/review-fix_1-x.md")
[ $rc = 0 ] && [ "$l1" = "VERDICT: APPROVE" ] && /usr/bin/grep -q '^- b.cs:2 — ' "$T/st/review-fix_1-x.md" \
  && ok "24 a debt-only CHANGES is saved as APPROVE, its Debt kept" || ko "24 debt-only: rc=$rc '$l1'"
printf '%s\n' "$l1" | /usr/bin/grep -q 'VERDICT: APPROVE' && ok "24 wave.sh reads it as an approval" || ko "24 wave.sh reads '$l1'"
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > "${CLAUDE_PROMPT:-/dev/null}"; printf 'VERDICT: APPROVE\n'
J
echo ccc333 > "$T/st/review-fix_1-x.sha"; printf 'VERDICT: APPROVE\n## Debt\n- b.cs:2 — x — y — z\n' > "$T/st/review-fix_1-x.md"
: > "$T/prompt"; CLAUDE_PROMPT="$T/prompt" bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
/usr/bin/grep -q 'FIX ROUND' "$T/prompt" && ko "24 the saved approval started a fix round" || ok "24 the saved approval starts no fix round"
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > /dev/null; printf 'VERDICT: CHANGES\n'; [ -n "$CLAUDE_BLOCKING" ] && printf '## Blocking\n- a.cs:1 — diverges from ADR-0001 — wrong — fix\n'
printf '## Debt\n- b.cs:2 — naming — nobody can tell what it names — rename it\n'
J
# #2647: a markdown answer names no repro, so its Blocking finding moves to Debt with the reason (ADR-0172), and the
# debt-only CHANGES is then an approval by the #653 path. A JSON CHANGES whose repro fails stays CHANGES (31).
rm -f "$T/st/review-fix_1-x".*; CLAUDE_BLOCKING=1 bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
[ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: APPROVE" ] && [ "$(python3 "$T/s/revformat.py" --summary "$T/st/review-fix_1-x.md")" = "blocking=0 debt=2" ] \
  && /usr/bin/grep -q '^- a.cs:1 — .*no repro: markdown answer' "$T/st/review-fix_1-x.md" \
  && ok "24 a markdown CHANGES moves its Blocking finding to Debt and is saved as APPROVE" \
  || ko "24 markdown blocking: $(cat "$T/st/review-fix_1-x.md")"
# #2647: the prompt states the repro's polarity, so a grep for text the fix removes is written as ! grep -q.
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > "${CLAUDE_PROMPT:-/dev/null}"; printf 'VERDICT: APPROVE\n'
J
rm -f "$T/st/review-fix_1-x".*; : > "$T/prompt"; CLAUDE_PROMPT="$T/prompt" bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
/usr/bin/grep -qF 'text that must be absent is `! grep -q' "$T/prompt" && /usr/bin/grep -qF 'text that must be present is `grep -q' "$T/prompt" \
  && ok "24 the prompt states the grep polarity of a repro" || ko "24 polarity: $(/usr/bin/grep -o 'repro is one shell command[^.]*' "$T/prompt" | head -1)"

# 25) #675: on the claude -p lane, the CLI's own --json-schema check can refuse every answer: the run ends
# error_max_structured_output_retries. That is a format failure, not an infrastructure one: the same model answers once
# more with the complaint, as on the direct lane, and the MODEL_FORMAT line names the model that failed.
cat > "$T/bin/claude" <<J
#!/bin/bash
cat > "$T/prompt.\$(wc -l < "$T/ccalls" 2>/dev/null | tr -d ' ')"; echo "\$*" >> "$T/ccalls"
if [ -e "$T/fix25" ] && [ "\$(wc -l < "$T/ccalls")" -ge 2 ]; then
  jq -cn '{type:"result",subtype:"success",is_error:false,result:"",structured_output:{verdict:"APPROVE",blocking:[],debt:[],resolved:[]},usage:{input_tokens:10,output_tokens:5}}'
else jq -cn '{type:"result",subtype:"error_max_structured_output_retries",is_error:true,result:"VERDICT: APPROVE, no findings",usage:{input_tokens:10,output_tokens:5}}'; fi
J
chmod +x "$T/bin/claude"; touch "$T/fix25"
printf '{"review_models":["claude:one","claude:two"],"review_order":"config","model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/prompt".*; : > "$T/ccalls"; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && case "$out" in model=claude:one*"VERDICT: APPROVE"*) true;; *) false;; esac && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: APPROVE" ] \
  && ok "25 a CLI schema refusal is repaired once by the same model" || ko "25 CLI repair: rc=$rc '$out' md='$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)'"
[ "$(/usr/bin/grep -c -- '--model one' "$T/ccalls")" = 2 ] && ! /usr/bin/grep -q -- '--model two' "$T/ccalls" && ok "25 the re-ask is the same model, once" || ko "25 calls: $(cat "$T/ccalls")"
/usr/bin/grep -q 'outside the shape' "$T/prompt.1" 2>/dev/null && /usr/bin/grep -q 'error_max_structured_output_retries' "$T/prompt.1" && /usr/bin/grep -q 'VERDICT: APPROVE, no findings' "$T/prompt.1" \
  && ok "25 the re-ask carries the complaint and the previous answer" || ko "25 re-ask prompt: $(tail -4 "$T/prompt.1" 2>/dev/null)"
/usr/bin/grep -qs 'claude:one.*the same model answers once more' "$T/st/cq/"*mformat*claude_one* && ok "25 the MODEL_FORMAT line names the model that failed" || ko "25 mformat: $(cat "$T/st/cq/"*mformat* 2>/dev/null)"
/bin/ls "$T/st/cq" | /usr/bin/grep -q mfail && ko "25 a format refusal was queued as MODEL_FAIL: $(cat "$T/st/cq/"*mfail*)" || ok "25 no MODEL_FAIL for a format refusal"
rm -f "$T/fix25" "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/prompt".*; : > "$T/ccalls"; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc != 0 ] && [ "$(/usr/bin/grep -c -- '--model one' "$T/ccalls")" = 2 ] && [ "$(/usr/bin/grep -c -- '--model two' "$T/ccalls")" = 2 ] \
  && /usr/bin/grep -qs 'claude:one' "$T/st/cq/"*mformat*claude_one* && /usr/bin/grep -qs 'claude:two' "$T/st/cq/"*mformat*claude_two* \
  && ok "25 a failed re-ask ends the model's turn, and each model's failure is recorded" || ko "25 failed re-ask: rc=$rc calls=$(cat "$T/ccalls" | tr '\n' ' ') cq=$(/bin/ls "$T/st/cq" | tr '\n' ' ')"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/prompt".* "$T/ccalls"

# v2 (ADR-0147): the review chain is a list of registry ids; an entry switched off is skipped, and the next one runs with its own effort.
printf '#!/bin/bash\ncat > /dev/null; echo "$*" >> "$CLAUDE_ARGS"; echo "VERDICT: APPROVE"\n' > "$T/bin/claude"; chmod +x "$T/bin/claude"   # earlier cases replace the stub
printf '{"schema_version":2,"chains":{"review":[{"id":"claude-opus-5-5","raw":"claude:claude-opus-5-5","route":"cli","on":false},{"id":"claude-sonnet-5-5","raw":"claude:claude-sonnet-5-5","route":"cli","effort":"high"}]},"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; export CLAUDE_ARGS="$T/claude-args"; : > "$CLAUDE_ARGS"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
/usr/bin/grep -q -- '--model claude-sonnet-5-5' "$CLAUDE_ARGS" && /usr/bin/grep -q -- '--effort high' "$CLAUDE_ARGS" && ! /usr/bin/grep -q 'claude-opus-5-5' "$CLAUDE_ARGS" \
  && ok "a v2 review chain skips its off entry and runs the next with its effort" || ko "v2 review chain: args=$(cat "$CLAUDE_ARGS" 2>/dev/null) cq=$(/bin/ls "$T/st/cq" | tr '\n' ' ')"
unset CLAUDE_ARGS
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*

# 26) #749: a review model that times out three times in a row is omitted from the next chains for its window, and the
# omission is named once in the pass (stderr), in cycle.log and in the queue. One timeout omits nothing. An answered review
# clears the streak. The window ends by time: the model is tried again with no state edit.
printf '#!/bin/bash\ncat > /dev/null; echo "$*" >> "$CLAUDE_ARGS"\ncase "$*" in *"--model slow"*) [ "$(cat "$SLOW_MODE")" = answer ] && { echo "VERDICT: APPROVE"; exit 0; }; sleep 30;; *) echo "VERDICT: APPROVE";; esac\n' > "$T/bin/claude"; chmod +x "$T/bin/claude"
printf '{"review_models":["claude:slow","claude:fast"],"review_order":"config","model_retry_budget":1,"review_timeout_seconds":2,"review_timeout_bound":3,"review_timeout_omit_seconds":3600,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
export CLAUDE_ARGS="$T/claude-args" SLOW_MODE="$T/slow.mode"; : > "$CLAUDE_ARGS"; echo timeout > "$SLOW_MODE"
rm -f "$T/st/review-timeouts.json" "$T/st/cycle.log" "$T/st/cq/"*
rv() { rm -f "$T/st/review-fix_1-x".*; GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>"$T/rv.err"; }
slow() { /usr/bin/grep -c -- '--model slow' "$CLAUDE_ARGS"; }
rv >/dev/null; rv >/dev/null
[ "$(slow)" = 2 ] && ok "26 a single timeout omits nothing: the next review tries the model again" || ko "26 one timeout omitted the model: slow calls $(slow) $(cat "$T/rv.err")"
rv >/dev/null
[ "$(slow)" = 3 ] && ok "26 the third timeout in a row is the last try of the model" || ko "26 third timeout: slow calls $(slow)"
rv >/dev/null
[ "$(slow)" = 3 ] && [ "$(/usr/bin/grep -c MODEL_OMIT "$T/rv.err")" = 1 ] && /usr/bin/grep -q 'MODEL_OMIT review claude:slow' "$T/rv.err" \
  && [ "$(/usr/bin/grep -c 'MODEL_OMIT review claude:slow' "$T/st/cycle.log")" = 2 ] && /usr/bin/grep -q 'MODEL_OMIT' "$T/st/cq/"* \
  && ok "26 an omitted model is not tried, and the pass, cycle.log and the queue each name the omission" \
  || ko "26 omission: slow calls $(slow) pass=[$(cat "$T/rv.err")] cycle=[$(cat "$T/st/cycle.log" 2>/dev/null)] cq=$(ls "$T/st/cq" | tr '\n' ' ')"
# The window ends by time. The case moves that clock, not the state's shape: it backdates the recorded end, so no sleep
# races a loaded machine (a 4 s window against a 5 s sleep failed whenever the box was busy, #749).
python3 - "$T/st/review-timeouts.json" <<'PY'
import json, sys, time
p = sys.argv[1]; d = json.load(open(p))
for e in d.values():
    if e.get("until") is not None: e["until"] = int(time.time()) - 10
open(p, "w").write(json.dumps(d, sort_keys=True) + "\n")
PY
rv >/dev/null
[ "$(slow)" = 4 ] && ok "26 the window ends by time: the model is tried again" || ko "26 expiry: slow calls $(slow) $(cat "$T/rv.err")"
echo answer > "$T/slow.mode"; rv >/dev/null
echo timeout > "$T/slow.mode"; rv >/dev/null; rv >/dev/null
rv >/dev/null
[ "$(slow)" = 8 ] && ok "26 an answered review clears the streak: two timeouts after it do not omit the model" || ko "26 answer reset: slow calls $(slow) $(cat "$T/rv.err")"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/st/cycle.log" "$T/st/review-timeouts.json" "$CLAUDE_ARGS"; unset CLAUDE_ARGS SLOW_MODE

# The committed labeled sample measures historical CHANGES verdicts. The report is a read-only
# calibration instrument: it names the total, the Debt/nit-only count, and the rate.
calibration=$(python3 "$(dirname "$src")/review-calibration.py" "$(dirname "$src")/fixtures/review-calibration.json" 2>&1); rc=$?
[ $rc = 0 ] && [ "$calibration" = "labeled=10 debt_nit_only=1 rate=10.0%" ] \
  && ok "the calibration report measures the committed Debt/nit-only sample" \
  || ko "calibration report: rc=$rc '$calibration'"
# 28) #762: the model that wrote the tip never reviews it while another reviewer is reachable; it says so on the issue
unset SLOW_MODE CLAUDE_ARGS; cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat >/dev/null; jq -cn '{type:"result",subtype:"success",is_error:false,result:"",structured_output:{verdict:"APPROVE",blocking:[],debt:[],resolved:[]},usage:{input_tokens:10,output_tokens:5}}'
J
chmod +x "$T/bin/claude"
printf '{"review_models":["claude:slow","claude:fast"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[],"review_order":"config"}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; out=$(SG_REVIEW_AUTHOR=claude:slow bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:fast*) ok "28 the author's model is skipped in the review chain";; *) ko "28 self-review: '$out'";; esac
/usr/bin/grep -q 'claude:slow' "$T/st/cq/"*"#8-status~mself"* 2>/dev/null && ok "28 the skip is said on the issue" || ko "28 no MODEL_SELF status"
printf 'claude:slow\n' > "$T/st/author-fix_1-x"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
case "$out" in model=claude:fast*) ok "28 the author file names the model too";; *) ko "28 author file: '$out'";; esac
printf '{"review_models":["claude:slow"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rm -f "$T/st/author-fix_1-x"
case "$out" in model=claude:slow*) ok "28 with no other reviewer the author's model still reviews";; *) ko "28 lone reviewer: '$out'";; esac
# 13) #846: a read the prompt carries as DATA must be an answer. A refused issue read (GitHub's rate-limit 403) or an empty
# diff ends the try BLOCKED before any model is called: the reviewer never judges the text of an error as the code.
printf '{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > "${CLAUDE_PROMPT:-/dev/null}"; echo "$*" >> "${CLAUDE_ARGS:-/dev/null}"; echo "VERDICT: APPROVE"
J
chmod +x "$T/bin/claude"; export CLAUDE_ARGS="$T/args-846" CLAUDE_PROMPT="$T/prompt-846"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/args-846"
out=$(GH_ISSUE_REFUSE=1 GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc != 0 ] && head -1 "$T/st/review-fix_1-x.md" 2>/dev/null | /usr/bin/grep -q 'VERDICT: BLOCKED (the issue read refused' && [ ! -s "$T/args-846" ] \
  && ok "a refused issue read (403 rate limit) is BLOCKED and no model is called (#846)" \
  || ko "refused issue read: rc=$rc '$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)' called=$(head -1 "$T/args-846" 2>/dev/null)"
rm -f "$T/st/review-fix_1-x".* "$T/args-846"
out=$(GH_DIFF_EMPTY=1 GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc != 0 ] && head -1 "$T/st/review-fix_1-x.md" 2>/dev/null | /usr/bin/grep -q 'VERDICT: BLOCKED (the diff read is empty' && [ ! -s "$T/args-846" ] \
  && ok "an empty diff is BLOCKED and no model is called (#846)" \
  || ko "empty diff: rc=$rc '$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)' called=$(head -1 "$T/args-846" 2>/dev/null)"
rm -f "$T/st/review-fix_1-x".* "$T/args-846" "$T/prompt-846"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && [ -s "$T/args-846" ] && /usr/bin/grep -q 'the acceptance criteria' "$T/prompt-846" 2>/dev/null \
  && ok "a normal issue and diff read still reach the reviewer (#846)" \
  || ko "normal read: rc=$rc '$out' called=$(head -1 "$T/args-846" 2>/dev/null)"
unset CLAUDE_ARGS CLAUDE_PROMPT

# 27) a models.json with no model_retry_budget, or with it explicitly null, takes the shipped default of 2 tries -- never
# jq's literal "null". That string made `[ "$i" -lt "$lim" ]` fail with "integer expression expected", the loop body never
# ran, and every review ended BLOCKED while the run retried once a minute.
cat > "$T/bin/claude" <<J
#!/bin/bash
cat >/dev/null; echo x >> "$T/rcalls"; echo "API Error: 529 overloaded_error"; exit 1
J
chmod +x "$T/bin/claude"
for cfgv in absent null; do
  case $cfgv in
    absent) printf '{"review_models":["claude:only"],"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json";;
    null) printf '{"review_models":["claude:only"],"model_retry_budget":null,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json";;
  esac
  rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/rcalls"
  out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>"$T/e21"); rc=$?
  n=$(wc -l < "$T/rcalls" 2>/dev/null | tr -d ' ')
  [ "$n" = 2 ] && ! /usr/bin/grep -q 'integer expression expected' "$T/e21" \
    && ok "27 model_retry_budget $cfgv: the shipped default (2 tries) is used, with no integer error" \
    || ko "27 model_retry_budget $cfgv: calls='$n' rc=$rc err=$(head -2 "$T/e21" 2>/dev/null | tr '\n' ' ')"
done
# the configured value still wins over the default
printf '{"review_models":["claude:only"],"model_retry_budget":3,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/rcalls"
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>"$T/e21"); rc=$?
n=$(wc -l < "$T/rcalls" 2>/dev/null | tr -d ' ')
[ "$n" = 3 ] && ok "27 a configured model_retry_budget still wins (3 tries)" || ko "26 configured budget: calls='$n' rc=$rc $(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)"
# 27c) a configured budget of 0 is a real value, never the fallback: zero tries must stay zero
printf '{"review_models":["claude:only"],"model_retry_budget":0,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/rcalls"
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>"$T/e21"); rc=$?
n=$( [ -f "$T/rcalls" ] && wc -l < "$T/rcalls" | tr -d ' ' || echo 0 )   # a zero budget calls no model, so the file is never written
[ "$n" = 0 ] && ! /usr/bin/grep -q 'integer expression expected' "$T/e21" && ok "27c a configured budget of 0 stays 0, the fallback does not fire" || ko "26c zero budget: calls='$n' rc=$rc"

# The grok-4.7 cases below reach the gateway before their CLI (#1373): the same closed port, scoped to this block.
export ANTHROPIC_BASE_URL=http://127.0.0.1:9
# 24b) grok files a real defect as Debt in most runs (measured on a planted diff). Its debt-only CHANGES must keep its
# verdict: the downgrade of #653 would turn that habit into an approval and the fix round would never run.
printf '#!/bin/bash\nprintf %s\n' "'VERDICT: CHANGES\n## Debt\n- b.cs:2 — naming — nobody can tell what it names — rename it\n'" > "$T/bin/grok"
chmod +x "$T/bin/grok"
printf '{"review_models":["grok-4.7"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/prompt".*
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
l1=$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)
[ $rc = 0 ] && [ "$l1" = "VERDICT: CHANGES" ] && /usr/bin/grep -q '^- b.cs:2 — ' "$T/st/review-fix_1-x.md" \
  && ok "24b a grok debt-only CHANGES keeps its verdict, so the fix round runs" \
  || ko "24b grok debt-only was saved as '$l1' (rc=$rc)"
# 24c) the published verdict names its model and its counts in the state, one ledger row per verdict.
vm=$(cat "$T/st/review-fix_1-x.verdict-model" 2>/dev/null); lg=$(tail -1 "$T/st/review-ledger.jsonl" 2>/dev/null)
[ "$vm" = "grok-4.7" ] && jq -e '.model=="grok-4.7" and .verdict=="VERDICT: CHANGES" and .blocking==0 and .debt==1 and (.effort // "")!=""' <<<"$lg" >/dev/null 2>&1 \
  && ok "24c the published verdict names its model in verdict-model and the ledger" || ko "24c verdict-model '$vm', ledger '$lg'"
at=$(tail -1 "$T/st/review-attempts.tsv" 2>/dev/null); [ "$(printf '%s' "$at" | awk -F'\t' '{print NF}')" = 6 ] \
  && [ "$(printf '%s' "$at" | awk -F'\t' '{print $3" "$4" "$6}')" = "grok-4.7 medium 0" ] \
  && ok "24c every try is logged with its effort, so a low-effort review can be counted" \
  || ko "24c attempts: '$at'"
# 24d) a reused verdict names the model that ruled, not the model a killed try left behind.
printf 'claude:first' > "$T/st/review-fix_1-x.model"
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
case "$out" in "model=grok-4.7 (reused"*CHANGES) ok "24d a reused verdict names the model that ruled, not the killed try" ;; *) ko "24d reuse named '$out' (rc=$rc)";; esac
rm -f "$T/st/review-fix_1-x.model"
# 13c) a v2 chain entry that names its effort is what the call gets, not effort_of's default.
printf '{"schema_version":2,"chains":{"review":[{"id":"grok-4.7","route":"cli","effort":"low"}]},"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
printf '#!/bin/bash\necho "$*" >> "$GROK_ARGS"; printf %s\n' "'VERDICT: APPROVE\n'" > "$T/bin/grok"; chmod +x "$T/bin/grok"
export GROK_ARGS="$T/grok-args"; : > "$GROK_ARGS"; rm -f "$T/st/review-fix_1-x".*
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
/usr/bin/grep -q -- '--reasoning-effort low' "$GROK_ARGS" \
  && ok "13c the grok review takes the chain entry's own effort" \
  || ko "13c the grok review ignored the entry's effort: $(cat "$GROK_ARGS")"
# 29) #1371: a grok review asks for --output-format json: the usage it reports is logged (not unreported), and the verdict
# parses from the envelope's text field. The stub answers plain text to any other argv, as the old plain leg did.
cat > "$T/bin/grok" <<'J'
#!/bin/bash
echo "$*" >> "$GROK_ARGS"
case "$*" in
  *"--output-format json"*) printf '%s\n' '{"type":"result","text":"VERDICT: APPROVE\n","usage":{"input_tokens":1200,"output_tokens":90,"cache_read_input_tokens":400}}';;
  *) printf 'VERDICT: APPROVE\n';;
esac
J
chmod +x "$T/bin/grok"
printf '{"review_models":["grok-4.7"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
export GROK_ARGS="$T/grok-args"; : > "$GROK_ARGS"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/st/token-usage.tsv"
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
l1=$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)
awk -F'\t' '$3=="grok-4.7" && $4==1200 && $5==90 && $6==400 && NF==6 {f=1} END{exit !f}' "$T/st/token-usage.tsv" 2>/dev/null && [ "$l1" = "VERDICT: APPROVE" ] && [ $rc = 0 ] \
  && ok "29 a grok review asks for json, logs its reported tokens, and the verdict parses from the text field" \
  || ko "29 grok json: rc=$rc line1='$l1' ledger='$(tail -1 "$T/st/token-usage.tsv" 2>/dev/null)' args='$(cat "$GROK_ARGS")'"
unset GROK_ARGS
# 13d) #1373 (review of #1373): the CLI fallback calls `grok -m $gwire`, so its effort is the wire spelling's own.
# The chain entry here is spelled `claude:grok-4.7`: the CLI model is `grok-4.7`, whose own default is medium, while
# the chain id's row says low. The fallback must read the model it calls, never the id the chain row named.
cat > "$T/bin/grok" <<'G'
#!/bin/bash
echo "$*" >> "$GROK_ARGS"
printf '%s\n' '{"type":"result","text":"VERDICT: APPROVE\n","usage":{"input_tokens":1200,"output_tokens":90,"cache_read_input_tokens":400}}'
G
chmod +x "$T/bin/grok"
printf '{"review_models":["claude:grok-4.7"],"efforts":{"review_models":{"claude:grok-4.7":"low"}},"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
export GROK_ARGS="$T/grok-args"; : > "$GROK_ARGS"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
/usr/bin/grep -q -- '-m grok-4.7 --reasoning-effort medium' "$GROK_ARGS" \
  && ok "1373 an id that differs from the wire spelling gets the called model's own effort" \
  || ko "1373 the CLI fallback took the chain id's effort: $(cat "$GROK_ARGS")"
unset GROK_ARGS
# 24e) #1403: a sampled shadow review of a published grok verdict. The next chain entry reads the same head; its answer
# is never published and never moves the verdict -- one ledger line (shadow: true) carries it, so the measure can count
# the grok Blocking findings the next entry would have missed. The claude lane answers in its real --output-format json
# envelope, so the shadow must unwrap .result before the shape check: a bare verdict here hid that bug (#1403).
printf '#!/bin/bash\nprintf %s\n' "'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — diverges from ADR-0001 — wrong — fix\n'" > "$T/bin/grok"; chmod +x "$T/bin/grok"
cat > "$T/bin/claude" <<J
#!/bin/bash
cat >/dev/null; echo "\$*" >> "\$SHADOW_CALLS"; printf '%s' '{"result":"","structured_output":{"verdict":"APPROVE","blocking":[],"debt":[],"resolved":[]},"usage":{"input_tokens":10,"output_tokens":5},"is_error":false,"subtype":"success"}'
J
chmod +x "$T/bin/claude"; export SHADOW_CALLS="$T/shadow-calls"
printf '{"review_models":["grok-4.7","claude:claude-opus-5-5"],"review_order":"config","model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/review-ledger.jsonl" "$SHADOW_CALLS"
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
sl=$(tail -1 "$T/st/review-ledger.jsonl" 2>/dev/null)
[ $rc = 0 ] && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: CHANGES" ] && [ -s "$SHADOW_CALLS" ] \
  && jq -e '.shadow==true and .model=="claude:claude-opus-5-5" and .verdict=="VERDICT: APPROVE" and .blocking==0 and .debt==0 and .head=="aaa111"' <<<"$sl" >/dev/null 2>&1 \
  && ok "24e a grok Blocking verdict is shadow-reviewed by the next chain entry, the verdict unchanged" \
  || ko "24e shadow: rc=$rc verdict='$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)' ledger='$sl'"
# a grok verdict with no Blocking finding is not sampled: only the published row is in the ledger.
printf '#!/bin/bash\nprintf %s\n' "'VERDICT: CHANGES\n## Debt\n- b.cs:2 — naming — nobody can tell what it names — rename it\n'" > "$T/bin/grok"; chmod +x "$T/bin/grok"
rm -f "$T/st/review-fix_1-x".* "$T/st/review-ledger.jsonl" "$SHADOW_CALLS"
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && [ ! -s "$SHADOW_CALLS" ] && [ "$(wc -l < "$T/st/review-ledger.jsonl" 2>/dev/null | tr -d ' ')" = 1 ] \
  && ok "24e a grok verdict with no Blocking finding is not shadow-reviewed" \
  || ko "24e debt-only shadow: rc=$rc calls=$(wc -l < "$SHADOW_CALLS" 2>/dev/null) ledger=$(cat "$T/st/review-ledger.jsonl" 2>/dev/null)"
unset SHADOW_CALLS
# 31) #1521: a Blocking finding whose repro exits 0 moves to Debt with "repro passed"; one whose repro fails stays Blocking.
# With every repro passing, the debt-only CHANGES is an approval by the #653 path. The repro runs in the checkout (SG_CHECKOUT).
# Under a child sandbox the repro runs through the seam (#1525).
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > /dev/null; echo "$*" >> "${CLAUDE_ARGS:-/dev/null}"
case "${REPRO_CASE:-mixed}" in
  mixed) rp1=true; rp2=false;;
  passing) rp1=true; rp2=true;;
esac
printf '{"result":"","structured_output":{"verdict":"CHANGES","blocking":[{"location":"a.cs:1","defect":"d one","failure":"f one","fix":"fix one","repro":"%s"},{"location":"b.cs:2","defect":"d two","failure":"f two","fix":"fix two","repro":"%s"}],"debt":[],"resolved":[]},"usage":{"input_tokens":10,"output_tokens":5},"is_error":false,"subtype":"success"}' "$rp1" "$rp2"
J
chmod +x "$T/bin/claude"
printf '{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/review-ledger.jsonl"
# #1521: the repro runs at the head, so the head is a real commit of the checkout (SG_CHECKOUT, $T/co).
H31=$(git -C "$T/co" rev-parse HEAD)
out=$(GH_SHA=$H31 REPRO_CASE=mixed bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
rv=$(python3 "$T/s/revformat.py" --summary "$T/st/review-fix_1-x.md" 2>/dev/null)
[ $rc = 0 ] && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: CHANGES" ] && [ "$rv" = "blocking=1 debt=1" ] \
  && /usr/bin/grep -q "repro passed at $H31" "$T/st/review-fix_1-x.md" && /usr/bin/grep -q "repro: false" "$T/st/review-fix_1-x.md" \
  && ok "31 a Blocking finding whose repro passes is Debt, and the one whose repro fails stays Blocking" \
  || ko "31 mixed repro: rc=$rc verdict='$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)' summary='$rv' review=$(cat "$T/st/review-fix_1-x.md" 2>/dev/null)"
rm -f "$T/st/review-fix_1-x".*
out=$(GH_SHA=$H31 REPRO_CASE=passing bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: APPROVE" ] && /usr/bin/grep -q "repro passed at $H31" "$T/st/review-fix_1-x.md" \
  && ok "31 with every repro passing, the debt-only review is an approval (#653)" \
  || ko "31 passing repro: rc=$rc verdict='$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)' review=$(cat "$T/st/review-fix_1-x.md" 2>/dev/null)"
# The repro tree (#1521). REPRO_CMD is the repro the stub's one Blocking finding names; the defect is "the file holds BUG".
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > /dev/null; echo "$*" >> "${CLAUDE_ARGS:-/dev/null}"
printf '{"result":"","structured_output":{"verdict":"CHANGES","blocking":[{"location":"f.txt:1","defect":"the file holds BUG","failure":"the defect ships","fix":"remove BUG","repro":"%s"}],"debt":[],"resolved":[]},"usage":{"input_tokens":10,"output_tokens":5},"is_error":false,"subtype":"success"}\n' "$REPRO_CMD"
J
chmod +x "$T/bin/claude"
# 32) #1521, --worktree: the review reads the child worktree's head, so the repro runs in that worktree. SG_CHECKOUT ($T/co)
# is another tree, where f.txt holds no BUG: a repro run there reads the defect as absent and demotes it.
W=$T/wt; mkdir -p "$W"; git -C "$W" init -q -b fix/9-x && git -C "$W" config user.email t@t && git -C "$W" config user.name t
printf 'base\n' > "$W/f.txt"; git -C "$W" add f.txt && git -C "$W" commit -q -m base
printf 'BUG\n' > "$W/f.txt"; git -C "$W" commit -q -am defect
printf 'clean\n' > "$T/co/f.txt"; rm -f "$T/st/review-fix_9-x".* "$T/st/cq/"*
out=$(cd "$W" && REPRO_CMD='! grep -q BUG f.txt' bash "$T/s/review.sh" --worktree HEAD~1 9 2>/dev/null); rc=$?
[ "$(head -1 "$T/st/review-fix_9-x.md" 2>/dev/null)" = "VERDICT: CHANGES" ] && [ "$(python3 "$T/s/revformat.py" --summary "$T/st/review-fix_9-x.md" 2>/dev/null)" = "blocking=1 debt=0" ] \
  && ok "32 --worktree runs the repro in the worktree at the head: a defect there stays Blocking" \
  || ko "32 --worktree repro tree: rc=$rc review=$(cat "$T/st/review-fix_9-x.md" 2>/dev/null)"
# 33) #1521, --branch: a head that is no commit of SG_CHECKOUT has no tree to run the repro in, so the finding stays Blocking
# (fail closed). $T/co holds the file that would let the repro pass.
touch "$T/co/fixed-1521"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(GH_SHA=0000000000000000000000000000000000001521 REPRO_CMD='test -f fixed-1521' bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ "$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)" = "VERDICT: CHANGES" ] && [ "$(python3 "$T/s/revformat.py" --summary "$T/st/review-fix_1-x.md" 2>/dev/null)" = "blocking=1 debt=0" ] \
  && ok "33 a branch head with no tree to run in keeps its Blocking finding (fail closed)" \
  || ko "33 fail closed: rc=$rc review=$(cat "$T/st/review-fix_1-x.md" 2>/dev/null)"
# 34) #1521, --branch: the head is a commit of SG_CHECKOUT that is not checked out. The repro runs in a throwaway worktree at
# that head, where the defect is present, so the finding stays Blocking. The checkout ($T/co, at a fix commit) reads it as passed.
C="git -C $T/co -c user.email=a@b -c user.name=t"; printf 'BUG\n' > "$T/co/f.txt"; $C add f.txt; $C commit -q -m defect1521; Y=$(git -C "$T/co" rev-parse HEAD)
printf 'clean\n' > "$T/co/f.txt"; $C commit -q -am fix1521
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(GH_SHA=$Y REPRO_CMD='! grep -q BUG f.txt' bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ "$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)" = "VERDICT: CHANGES" ] && [ "$(python3 "$T/s/revformat.py" --summary "$T/st/review-fix_1-x.md" 2>/dev/null)" = "blocking=1 debt=0" ] \
  && [ "$(git -C "$T/co" worktree list | wc -l | tr -d ' ')" = 1 ] \
  && ok "34 a repro at a head that is not checked out runs in a throwaway worktree, and the finding stays Blocking" \
  || ko "34 throwaway repro: rc=$rc review=$(cat "$T/st/review-fix_1-x.md" 2>/dev/null) worktrees=$(git -C "$T/co" worktree list | wc -l)"
# 35) #1521, --branch: the head is the checkout's own HEAD, and the checkout's working tree is edited so the repro would
# pass there. The repro runs in a throwaway worktree of that head, where the committed defect is present, so the finding
# stays Blocking. SG_CHECKOUT is never the repro's cwd.
C="git -C $T/co -c user.email=a@b -c user.name=t"; printf 'BUG\n' > "$T/co/f.txt"; $C commit -q -am defect1521b; H2=$(git -C "$T/co" rev-parse HEAD)
printf 'clean\n' > "$T/co/f.txt"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(GH_SHA=$H2 REPRO_CMD='! grep -q BUG f.txt' bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ "$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)" = "VERDICT: CHANGES" ] && [ "$(python3 "$T/s/revformat.py" --summary "$T/st/review-fix_1-x.md" 2>/dev/null)" = "blocking=1 debt=0" ] \
  && [ "$(git -C "$T/co" worktree list | wc -l | tr -d ' ')" = 1 ] \
  && ok "35 a --branch repro at the checkout's own head runs in a throwaway worktree, never the checkout" \
  || ko "35 checkout repro: rc=$rc review=$(cat "$T/st/review-fix_1-x.md" 2>/dev/null) summary=$(python3 "$T/s/revformat.py" --summary "$T/st/review-fix_1-x.md" 2>/dev/null)"
# 36) #2565: a Blocking repro still red at the head also runs at the merge-base of the head and the base ref, in a throwaway
# worktree that is removed after; never in the checkout, the reviewed tree or the base tip. The repro is red at every head and
# in a clean tree at the merge-base, green at the base tip and in a dirty tree at the merge-base (dirty-2565): a base note
# with the merge-base sha proves where it ran.
rp36() { printf 'h=$(git rev-parse HEAD); test $h = %s && test -e dirty-2565 && exit 0; test $h = %s && exit 0; exit 1' "$1" "$2"; }
fails36() { jq -r "[.$2[].failure]|join(\"|\")" "$T/st/review-$1.answer.json" 2>/dev/null; }
R=$T/r65; git init -q -b mbb "$R"; G="git -C $R -c user.email=a@b -c user.name=t"
echo base > "$R/g"; $G add g; $G commit -q -m mb; MB=$(git -C "$R" rev-parse HEAD)
$G checkout -q -b dev; echo tip > "$R/g"; $G commit -q -am tip; TIP=$(git -C "$R" rev-parse HEAD); $G checkout -q mbb; : > "$R/dirty-2565"
$G worktree add -q -b fix/65-x "$T/w65" "$MB"; echo h > "$T/w65/h"; git -C "$T/w65" add h; git -C "$T/w65" -c user.email=a@b -c user.name=t commit -q -m head
rm -f "$T/st/review-fix_65-x".* "$T/st/cq/"*
out=$(cd "$T/w65" && REPRO_CMD=$(rp36 "$MB" "$TIP") bash "$T/s/review.sh" --worktree dev 65 2>"$T/r65.err"); rc=$?
[ "$(head -1 "$T/st/review-fix_65-x.md" 2>/dev/null)" = "VERDICT: CHANGES" ] && [ "$(fails36 fix_65-x blocking)" = "repro also fails at base $MB; the defect ships" ] \
  && [ "$(git -C "$R" worktree list | wc -l | tr -d ' ')" = 2 ] \
  && ok "36 --worktree runs the base repro in a throwaway worktree of the merge-base, and removes it" \
  || ko "36 --worktree base repro: rc=$rc blocking=$(fails36 fix_65-x blocking) worktrees=$(git -C "$R" worktree list | wc -l) err=$(cat "$T/r65.err")"
# One head run per review: a repro that turns green on its second run (a count file in the reviewed tree) runs once at the
# head, so its finding stays Blocking with the base note.
$G worktree add -q -b fix/66-x "$T/w66" "$MB"; echo h > "$T/w66/h"; git -C "$T/w66" add h; git -C "$T/w66" -c user.email=a@b -c user.name=t commit -q -m head
rm -f "$T/st/review-fix_66-x".* "$T/st/cq/"*
out=$(cd "$T/w66" && REPRO_CMD='echo x >> runs-2565; test $(wc -l < runs-2565) -ge 2' bash "$T/s/review.sh" --worktree dev 66 2>"$T/r66.err"); rc=$?
[ "$(/usr/bin/wc -l < "$T/w66/runs-2565" 2>/dev/null | tr -d ' ')" = 1 ] && [ "$(fails36 fix_66-x blocking)" = "repro also fails at base $MB; the defect ships" ] \
  && ok "36 a review runs each Blocking repro once at the head" \
  || ko "36 head runs: rc=$rc runs=$(/usr/bin/wc -l < "$T/w66/runs-2565" 2>/dev/null) blocking=$(fails36 fix_66-x blocking) debt=$(fails36 fix_66-x debt) err=$(cat "$T/r66.err")"
# no tree holds the base (its worktree add fails): no base repro runs, the finding stays Blocking, and stderr names the reason
mkdir -p "$T/gitfail"; printf '#!/bin/bash\ncase " $* " in *" worktree add "*"$GIT_FAIL_SHA"*) exit 128;; esac\nexec /usr/bin/git "$@"\n' > "$T/gitfail/git"; chmod +x "$T/gitfail/git"
rm -f "$T/st/review-fix_66-x".* "$T/st/cq/"*
out=$(cd "$T/w66" && PATH=$T/gitfail:$PATH GIT_FAIL_SHA=$MB REPRO_CMD=false bash "$T/s/review.sh" --worktree dev 66 2>"$T/r66b.err"); rc=$?
[ "$(fails36 fix_66-x blocking)" = "the defect ships" ] && /usr/bin/grep -q "no tree holds base $MB" "$T/r66b.err" \
  && ok "36 a base with no tree keeps its Blocking finding and names the reason" \
  || ko "36 no base tree: rc=$rc blocking=$(fails36 fix_66-x blocking) err=$(cat "$T/r66b.err")"
# --branch: the checkout ($T/co) sits at the merge-base with dirty-2565; origin/dev is the base tip.
C="git -C $T/co -c user.email=a@b -c user.name=t"; $C commit -q -am pre2565 >/dev/null 2>&1; MB=$(git -C "$T/co" rev-parse HEAD); : > "$T/co/dirty-2565"
H1=$($C commit-tree "$MB^{tree}" -p "$MB" -m head2565); TIP=$($C commit-tree "$MB^{tree}" -p "$MB" -m tip2565); git -C "$T/co" update-ref refs/remotes/origin/dev "$TIP"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(GH_SHA=$H1 SG_REVIEW_BASE=dev REPRO_CMD=$(rp36 "$MB" "$TIP") bash "$T/s/review.sh" --branch fix/1-x 8 2>"$T/r36.err"); rc=$?
[ "$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)" = "VERDICT: CHANGES" ] && [ "$(fails36 fix_1-x blocking)" = "repro also fails at base $MB; the defect ships" ] \
  && [ "$(git -C "$T/co" worktree list | wc -l | tr -d ' ')" = 1 ] \
  && ok "36 --branch runs the base repro in a throwaway worktree of the merge-base, never the checkout, and removes it" \
  || ko "36 --branch base repro: rc=$rc blocking=$(fails36 fix_1-x blocking) worktrees=$(git -C "$T/co" worktree list | wc -l) err=$(cat "$T/r36.err")"
# a previous head that is no ancestor of the head (a rebase) is no repeat: the finding stays Blocking with the base note
H3=$($C commit-tree "$MB^{tree}" -p "$MB" -m head2565c)
out=$(GH_SHA=$H3 SG_REVIEW_BASE=dev REPRO_CMD=$(rp36 "$MB" "$TIP") bash "$T/s/review.sh" --branch fix/1-x 8 2>"$T/r36d.err"); rc=$?
[ "$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)" = "VERDICT: CHANGES" ] && [ "$(fails36 fix_1-x blocking)" = "repro also fails at base $MB; the defect ships" ] \
  && ok "36 a previous head that is no ancestor of the head is no repeat" \
  || ko "36 non-ancestor: rc=$rc blocking=$(fails36 fix_1-x blocking) debt=$(fails36 fix_1-x debt) err=$(cat "$T/r36d.err")"
# the fix round: the same repro is red at the base and at a second head, so its finding moves to Debt and the debt-only
# CHANGES saves as APPROVE (#653).
H2=$($C commit-tree "$MB^{tree}" -p "$H3" -m head2565b)
out=$(GH_SHA=$H2 SG_REVIEW_BASE=dev REPRO_CMD=$(rp36 "$MB" "$TIP") bash "$T/s/review.sh" --branch fix/1-x 8 2>"$T/r36b.err"); rc=$?
[ "$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)" = "VERDICT: APPROVE" ] && [ "$(fails36 fix_1-x blocking)" = "" ] \
  && [ "$(fails36 fix_1-x debt)" = "repro fails at base $MB and at heads $H3, $H2; the defect ships" ] \
  && [ "$(git -C "$T/co" worktree list | wc -l | tr -d ' ')" = 1 ] \
  && ok "36 a fix round whose only Blocking finding is red at the base and at two heads saves as APPROVE" \
  || ko "36 two heads: rc=$rc line1=$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null) blocking=$(fails36 fix_1-x blocking) debt=$(fails36 fix_1-x debt) err=$(cat "$T/r36b.err")"
# no merge-base: no base repro runs, the finding stays Blocking, and stderr names the reason
git -C "$T/co" update-ref -d refs/remotes/origin/dev; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(GH_SHA=$H1 SG_REVIEW_BASE=dev REPRO_CMD=$(rp36 "$MB" "$TIP") bash "$T/s/review.sh" --branch fix/1-x 8 2>"$T/r36c.err"); rc=$?
[ "$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)" = "VERDICT: CHANGES" ] && [ "$(fails36 fix_1-x blocking)" = "the defect ships" ] \
  && /usr/bin/grep -q 'no merge-base' "$T/r36c.err" \
  && ok "36 a head with no merge-base keeps its Blocking finding and names the reason" \
  || ko "36 no merge-base: rc=$rc blocking=$(fails36 fix_1-x blocking) err=$(cat "$T/r36c.err")"
rm -f "$T/co/dirty-2565"
unset SHADOW_CALLS ANTHROPIC_BASE_URL
# 40) #1373: a grok reviewer answers through one streamed no-tools gateway call, with its effort in output_config, its
# usage in the ledger, and no CLI. The request carries no thinking and no tools, and the model is the registry's gateway wire name.
rm -rf "$T/st/cq"; mkdir -p "$T/st/cq"; rm -f "$T/st/grok-limited" "$T/bin/grok" "$T/bin/curl"
G_START='{"type":"message_start","message":{"usage":{"input_tokens":900,"output_tokens":1,"cache_read_input_tokens":300}}}'
G_TEXT='{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"VERDICT: APPROVE"}}'
G_END='{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":42}}'
G_STOP='{"type":"message_stop"}'
printf '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && { cp "${2#@}" "$CURL_BODY"; }; shift; done\ncat "$CURL_REPLY"\n' > "$T/bin/curl"; chmod +x "$T/bin/curl"
export CURL_BODY="$T/curl-body-30" CURL_REPLY="$T/curl-reply-30"
cat > "$T/bin/grok" <<'G'
#!/bin/bash
echo "$*" >> "$GROK_ARGS"; pf=""; while [ $# -gt 0 ]; do [ "$1" = --prompt-file ] && pf="$2"; shift; done
cp "$pf" "$GROK_PROMPT"; echo "VERDICT: APPROVE"
G
chmod +x "$T/bin/grok"
export GROK_ARGS="$T/grok-args" GROK_PROMPT="$T/grok-prompt"
printf '{"review_models":["grok-4.7"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
printf 'event: message_start\ndata: %s\n\nevent: content_block_delta\ndata: %s\n\nevent: message_delta\ndata: %s\n\nevent: message_stop\ndata: %s\n\n' "$G_START" "$G_TEXT" "$G_END" "$G_STOP" > "$CURL_REPLY"
rm -f "$T/st/review-fix_1-x".* "$T/st/token-usage.tsv" "$CURL_BODY" "$GROK_PROMPT"; : > "$GROK_ARGS"; : > "$T/st/token-usage.tsv"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
want=$(python3 "$T/s/modelreg.py" review-route grok-4.7 | cut -f3)
jq -e --arg w "$want" '.stream==true and .output_config.effort=="medium" and (has("thinking")|not) and (has("tools")|not) and .model==$w' "$CURL_BODY" >/dev/null 2>&1 \
  && [ ! -s "$GROK_ARGS" ] \
  && awk -F'\t' 'NF==6 && $2=="review:fix_1-x" && $3=="grok-4.7" && $4==900 && $5==42 && $6==300 {f=1} END{exit !f}' "$T/st/token-usage.tsv" \
  && [ "$(wc -l < "$T/st/token-usage.tsv" | tr -d ' ')" = 1 ] \
  && head -1 "$T/st/review-fix_1-x.md" 2>/dev/null | /usr/bin/grep -q 'VERDICT: APPROVE' \
  && ok "1373 a grok review streams through the gateway with no tools, counts its usage, and runs no CLI" \
  || ko "1373 streamed grok review: rc=$rc body=$(head -c 300 "$CURL_BODY" 2>/dev/null) cli=$(cat "$GROK_ARGS") ledger=$(cat "$T/st/token-usage.tsv")"
# 41) a stream cut before its stop falls back to the grok CLI on the no-tools prompt; the failed call is a ledger row of
# its own with the usage the stream reported, and the CLI's answer is the next row with the CLI's own usage.
printf 'event: message_start\ndata: %s\n\nevent: content_block_delta\ndata: %s\n\n' "$G_START" "$G_TEXT" > "$CURL_REPLY"
cat > "$T/bin/grok" <<'G'
#!/bin/bash
echo "$*" >> "$GROK_ARGS"; pf=""; while [ $# -gt 0 ]; do [ "$1" = --prompt-file ] && pf="$2"; shift; done
cp "$pf" "$GROK_PROMPT"
printf '%s\n' '{"type":"result","text":"VERDICT: APPROVE\n","usage":{"input_tokens":1200,"output_tokens":90,"cache_read_input_tokens":400}}'
G
chmod +x "$T/bin/grok"
rm -f "$T/st/review-fix_1-x".* "$T/st/token-usage.tsv" "$CURL_BODY" "$GROK_PROMPT"; : > "$GROK_ARGS"; : > "$T/st/token-usage.tsv"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ -s "$GROK_ARGS" ] \
  && [ "$(wc -l < "$T/st/token-usage.tsv" | tr -d ' ')" = 2 ] \
  && awk -F'\t' 'NF==7 && $2=="review:fix_1-x" && $3=="grok-4.7" && $4==900 && $5==1 && $6==300 && $7=="gateway-error_stream" {f=1} END{exit !f}' "$T/st/token-usage.tsv" \
  && awk -F'\t' 'NF==6 && $2=="review:fix_1-x#2" && $3=="grok-4.7" && $4==1200 && $5==90 && $6==400 {f=1} END{exit !f}' "$T/st/token-usage.tsv" \
  && /usr/bin/grep -q 'You have no tools' "$GROK_PROMPT" && ! /usr/bin/grep -q 'git show' "$GROK_PROMPT" \
  && head -1 "$T/st/review-fix_1-x.md" 2>/dev/null | /usr/bin/grep -q 'VERDICT: APPROVE' \
  && ok "1373 a cut grok stream falls back to the CLI on the no-tools prompt, and each call keeps its own usage row" \
  || ko "1373 cut grok stream: rc=$rc rows=$(wc -l < "$T/st/token-usage.tsv" | tr -d ' ') cli=$(cat "$GROK_ARGS") prompt=$(grep -c 'You have no tools' "$GROK_PROMPT" 2>/dev/null)/$(grep -c 'git show' "$GROK_PROMPT" 2>/dev/null) line1=$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null) ledger=$(tr '\n' '|' < "$T/st/token-usage.tsv")"
rm -f "$T/bin/curl" "$CURL_BODY".* "$CURL_REPLY".*; unset CURL_BODY CURL_REPLY
# 42) #1373: a gateway call that reaches its deadline is no fallback. The MODEL_TIMEOUT path owns it, so one review never
# spends two timeouts, and the grok CLI does not run at all. The stub reads the deadline curl is given instead of pinning
# a 30 s wall clock, so the suite does not race a loaded machine. The timed-out try keeps exactly one ledger row, the
# gateway's, with zero usage, never a second row from a CLI call.
cat > "$T/bin/curl" <<'C'
#!/bin/bash
m=600; while [ $# -gt 0 ]; do [ "$1" = -m ] && m=$2; shift; done
sleep $((m + 1))
C
chmod +x "$T/bin/curl"
printf '#!/bin/bash\necho "$*" >> "$GROK_ARGS"; echo "VERDICT: APPROVE"\n' > "$T/bin/grok"; chmod +x "$T/bin/grok"
printf '{"review_models":["grok-4.7"],"model_retry_budget":1,"review_timeout_seconds":2,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
export GROK_ARGS="$T/grok-args"; : > "$GROK_ARGS"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/st/token-usage.tsv"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ ! -s "$GROK_ARGS" ] && [ "$(wc -l < "$T/st/token-usage.tsv" | tr -d ' ')" = 1 ] \
  && awk -F'\t' 'NF==7 && $2=="review:fix_1-x" && $3=="grok-4.7" && $4==0 && $5==0 && $6==0 && $7=="no-usage" {f=1} END{exit !f}' "$T/st/token-usage.tsv" \
  && /usr/bin/grep -q 'grok-4.7' "$T/st/cq/"*mtimeout* && [ $rc != 0 ] \
  && ok "1373 a grok gateway timeout reaches MODEL_TIMEOUT, never the CLI, and keeps only the gateway's zero-usage row" \
  || ko "1373 grok timeout fell back: rc=$rc cli=$(cat "$GROK_ARGS") rows=$(tr '\n' '|' < "$T/st/token-usage.tsv") cq=$(cat "$T/st/cq/"* 2>/dev/null | head -3)"
# 43) #1373: a gateway that refuses the connection answers curl's own error at once, well inside the deadline. That is a
# cut, not a timeout, so the grok CLI answers the review.
printf '#!/bin/bash\nexit 7\n' > "$T/bin/curl"; chmod +x "$T/bin/curl"
printf '{"review_models":["grok-4.7"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
# the real CLI prints its JSON envelope, not the bare verdict: the fallback must reshape it before the check chain reads it
cat > "$T/bin/grok" <<'G'
#!/bin/bash
echo "$*" >> "$GROK_ARGS"
printf '%s\n' '{"type":"result","text":"VERDICT: APPROVE\n","usage":{"input_tokens":1200,"output_tokens":90,"cache_read_input_tokens":400}}'
G
chmod +x "$T/bin/grok"
: > "$GROK_ARGS"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/st/token-usage.tsv"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ -s "$GROK_ARGS" ] && head -1 "$T/st/review-fix_1-x.md" 2>/dev/null | /usr/bin/grep -q 'VERDICT: APPROVE' \
  && awk -F'\t' 'NF==6 && $2=="review:fix_1-x#2" && $3=="grok-4.7" && $4==1200 && $5==90 && $6==400 {f=1} END{exit !f}' "$T/st/token-usage.tsv" \
  && ok "1373 a refused gateway connection falls back to the grok CLI, whose JSON envelope is reshaped with its usage" \
  || ko "1373 refused connection did not fall back: rc=$rc cli=$(cat "$GROK_ARGS") cq=$(/bin/ls "$T/st/cq") line1=$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null) ledger=$(tr '\n' '|' < "$T/st/token-usage.tsv")"
# 44) #1373: a stream cut before its stop falls back to the grok CLI, and that CLI's 402 balance answer marks grok spent:
# the marker is written and the CLI's failed call is counted in the ledger, so the quota follows the vendor.
export CURL_BODY="$T/curl-body-34" CURL_REPLY="$T/curl-reply-34" GROK_ARGS="$T/grok-args-34"
printf '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && { cp "${2#@}" "$CURL_BODY"; }; shift; done\ncat "$CURL_REPLY"\n' > "$T/bin/curl"; chmod +x "$T/bin/curl"
printf 'event: message_start\ndata: %s\n\nevent: content_block_delta\ndata: %s\n\n' "$G_START" "$G_TEXT" > "$CURL_REPLY"
printf '#!/bin/bash\necho "$*" >> "$GROK_ARGS"; echo "402 Payment Required: balance exhausted" >&2; exit 1\n' > "$T/bin/grok"; chmod +x "$T/bin/grok"
printf '{"review_models":["grok-4.7"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
: > "$GROK_ARGS"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/st/grok-limited"; : > "$T/st/token-usage.tsv"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ -s "$GROK_ARGS" ] && [ -e "$T/st/grok-limited" ] && [ "$(wc -l < "$T/st/token-usage.tsv" | tr -d ' ')" = 2 ] \
  && ok "1373 a 402 on the CLI fallback marks grok spent, and the failed gateway call keeps its own row" \
  || ko "1373 402 on the CLI fallback: rc=$rc cli=$(cat "$GROK_ARGS") marker=$(ls "$T/st/grok-limited" 2>/dev/null) rows=$(tr '\n' '|' < "$T/st/token-usage.tsv")"
rm -f "$T/bin/curl" "$T/st/grok-limited"; unset GROK_ARGS CURL_BODY CURL_REPLY
# 45) #1373: a converter that fails after it printed an answer is no answer. Its exit status decides whether its output is
# the answer or the raw gateway body, which is no JSON, so the grok CLI answers the review.
cat > "$T/s/review_stream.py" <<'P'
import sys
sys.stdin.read()
print('{"result": "VERDICT: APPROVE", "usage": {}, "subtype": "success", "is_error": false}')
sys.exit(1)
P
printf '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && { cp "${2#@}" "$CURL_BODY"; }; shift; done\ncat "$CURL_REPLY"\n' > "$T/bin/curl"; chmod +x "$T/bin/curl"
printf '#!/bin/bash\necho "$*" >> "$GROK_ARGS"; echo "VERDICT: APPROVE"\n' > "$T/bin/grok"; chmod +x "$T/bin/grok"
export CURL_BODY="$T/curl-body-35" CURL_REPLY="$T/curl-reply-35" GROK_ARGS="$T/grok-args-35"
printf 'event: message_start\ndata: %s\n\nevent: content_block_delta\ndata: %s\n\nevent: message_delta\ndata: %s\n\nevent: message_stop\ndata: %s\n\n' "$G_START" "$G_TEXT" "$G_END" "$G_STOP" > "$CURL_REPLY"
printf '{"review_models":["grok-4.7"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
: > "$GROK_ARGS"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/st/grok-limited"; : > "$T/st/token-usage.tsv"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ -s "$GROK_ARGS" ] && head -1 "$T/st/review-fix_1-x.md" 2>/dev/null | /usr/bin/grep -q 'VERDICT: APPROVE' \
  && awk -F'\t' 'NF==7 && $2=="review:fix_1-x" && $3=="grok-4.7" && $4==0 && $5==0 && $6==0 && $7=="gateway-unreadable" {f=1} END{exit !f}' "$T/st/token-usage.tsv" \
  && ok "1373 a converter that fails after its answer is no answer: the grok CLI answers, and the ledger names the unreadable gateway answer" \
  || ko "1373 a failed converter kept its partial answer: rc=$rc cli=$(cat "$GROK_ARGS") line1=$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null) ledger=$(tr '\n' '|' < "$T/st/token-usage.tsv")"
cp "$(dirname "$src")/review_stream.py" "$T/s/"; rm -f "$T/bin/curl" "$T/bin/grok"; unset CURL_BODY CURL_REPLY GROK_ARGS
# 46) #1373 (review of #1373): a registry lookup that fails once is asked again. Its empty row is no route, so the model is
# not held out of the whole review.
cp "$(dirname "$src")/modelreg.py" "$T/s/modelreg_real.py"
cat > "$T/s/modelreg.py" <<'P'
import os, sys
if "review-route" in sys.argv and os.path.exists(os.environ["ROUTE_FLAG"]):
    os.remove(os.environ["ROUTE_FLAG"]); sys.exit(1)
here = os.path.dirname(os.path.abspath(__file__))
os.execv(sys.executable, [sys.executable, os.path.join(here, "modelreg_real.py")] + sys.argv[1:])
P
export ROUTE_FLAG="$T/route-fail-once"; : > "$ROUTE_FLAG"
printf '#!/bin/bash\ncat >/dev/null; echo "VERDICT: APPROVE"\n' > "$T/bin/claude"; chmod +x "$T/bin/claude"   # case 27 leaves a stub that always fails
printf '{"review_models":["claude:claude-opus-5-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
case "$out" in model=claude:claude-opus-5-5*) ok "1373 a registry lookup that fails once is asked again: the review still runs";; *) ko "1373 a failed registry lookup held the model out: rc=$rc '$out' cq=$(cat "$T/st/cq/"* 2>/dev/null | head -2)";; esac
cp "$(dirname "$src")/modelreg.py" "$T/s/"; rm -f "$T/s/modelreg_real.py" "$ROUTE_FLAG"; unset ROUTE_FLAG
# 47) #1373 (review of #1373): an explicit gateway error event is no cut. The gateway's own overload answer follows the
# chain's retry path, so no grok CLI call is spent on a deliberate error, and MODEL_FAIL names the subtype.
export CURL_BODY="$T/curl-body-37" CURL_REPLY="$T/curl-reply-37" GROK_ARGS="$T/grok-args-37"
printf '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && { cp "${2#@}" "$CURL_BODY"; }; shift; done\ncat "$CURL_REPLY"\n' > "$T/bin/curl"; chmod +x "$T/bin/curl"
cat > "$T/bin/grok" <<'G'
#!/bin/bash
echo "$*" >> "$GROK_ARGS"; printf '%s\n' '{"type":"result","text":"VERDICT: APPROVE","usage":{}}'
G
chmod +x "$T/bin/grok"
printf 'event: message_start\ndata: %s\n\nevent: error\ndata: %s\n\n' "$G_START" '{"type":"error","error":{"type":"overloaded_error","message":"busy"}}' > "$CURL_REPLY"
printf '{"review_models":["grok-4.7"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
: > "$GROK_ARGS"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/st/token-usage.tsv"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ ! -s "$GROK_ARGS" ] && cat "$T/st/cq/"*mfail* 2>/dev/null | /usr/bin/grep -q 'error_overloaded_error' \
  && awk -F'\t' 'NF==6 && $2=="review:fix_1-x" && $3=="grok-4.7" && $4==900 {f=1} END{exit !f}' "$T/st/token-usage.tsv" && [ $rc != 0 ] \
  && ok "1373 an explicit gateway error event is no CLI fallback: MODEL_FAIL names the subtype, and the gateway's own row keeps its usage" \
  || ko "1373 overloaded gateway spent the CLI: rc=$rc cli=$(cat "$GROK_ARGS") ledger=$(tr '\n' '|' < "$T/st/token-usage.tsv") cq=$(cat "$T/st/cq/"*mfail* 2>/dev/null | head -2)"
# 48) #1373 (review of #1373): one resolver call per (model, route) for the whole review. route_of memoizes on $$, which
# bash keeps as the parent's pid inside a subshell, so eligible(), vendor_of() and the try loop share the top shell's rows.
cp "$(dirname "$src")/modelreg.py" "$T/s/modelreg_real.py"
cat > "$T/s/modelreg.py" <<'P'
import os, sys
if sys.argv[1:2] == ["review-route"]:
    with open(os.environ["ROUTE_CALLS"], "a") as f: f.write(" ".join(sys.argv[1:3]) + "\n")
here = os.path.dirname(os.path.abspath(__file__))
os.execv(sys.executable, [sys.executable, os.path.join(here, "modelreg_real.py")] + sys.argv[1:])
P
export ROUTE_CALLS="$T/route-calls"; : > "$ROUTE_CALLS"
printf 'event: message_start\ndata: %s\n\nevent: content_block_delta\ndata: %s\n\nevent: message_delta\ndata: %s\n\nevent: message_stop\ndata: %s\n\n' "$G_START" "$G_TEXT" "$G_END" "$G_STOP" > "$CURL_REPLY"
: > "$GROK_ARGS"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/st/token-usage.tsv"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc = 0 ] && [ "$(wc -l < "$ROUTE_CALLS" | tr -d ' ')" = 2 ] \
  && ok "1373 one resolver call per (model, route) for a whole review: the cache is shared across subshells" \
  || ko "1373 the resolver was asked too often: rc=$rc calls=$(tr '\n' '|' < "$ROUTE_CALLS")"
cp "$(dirname "$src")/modelreg.py" "$T/s/"; rm -f "$T/s/modelreg_real.py" "$T/bin/curl" "$T/bin/grok"; unset ROUTE_CALLS CURL_BODY CURL_REPLY GROK_ARGS
# 49) #1373 (review of #1373): curl's own deadline exit (28) is the model's timeout whenever it arrives, not only when
# the wall clock happens to reach the budget. A stall that ends the stream at 28 just under the budget is still one
# timeout: the grok CLI never runs, and the gateway's single row is the review's only spend.
export CURL_BODY="$T/curl-body-49" CURL_REPLY="$T/curl-reply-49" GROK_ARGS="$T/grok-args-49"
printf '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && { cp "${2#@}" "$CURL_BODY"; }; shift; done\ncat "$CURL_REPLY"\nexit 28\n' > "$T/bin/curl"; chmod +x "$T/bin/curl"
printf 'event: message_start\ndata: %s\n\nevent: content_block_delta\ndata: %s\n\n' "$G_START" "$G_TEXT" > "$CURL_REPLY"
printf '#!/bin/bash\necho "$*" >> "$GROK_ARGS"; echo "VERDICT: APPROVE"\n' > "$T/bin/grok"; chmod +x "$T/bin/grok"
printf '{"review_models":["grok-4.7"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
: > "$GROK_ARGS"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/st/token-usage.tsv"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ ! -s "$GROK_ARGS" ] && [ "$(wc -l < "$T/st/token-usage.tsv" | tr -d ' ')" = 1 ] \
  && /usr/bin/grep -q 'grok-4.7' "$T/st/cq/"*mtimeout* && [ $rc != 0 ] \
  && ok "1373 a stream cut at curl's deadline exit is no fallback: the grok CLI never runs" \
  || ko "1373 a curl 28 under the budget fell back: rc=$rc cli=$(cat "$GROK_ARGS") rows=$(tr '\n' '|' < "$T/st/token-usage.tsv") cq=$(cat "$T/st/cq/"* 2>/dev/null | head -3)"
rm -f "$T/bin/curl" "$T/bin/grok"; unset CURL_BODY CURL_REPLY GROK_ARGS
# 50) #1373 (review of #1373): the grok CLI's own envelope can report an error (a quota answer carrying text, is_error
# true). That is no verdict: the CLI lane reads it the way the claude lane reads its envelope, so the try ends in a
# MODEL_FAIL naming the subtype, and the error text is never published as a review.
export CURL_BODY="$T/curl-body-50" CURL_REPLY="$T/curl-reply-50" GROK_ARGS="$T/grok-args-50"
printf '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && { cp "${2#@}" "$CURL_BODY"; }; shift; done\ncat "$CURL_REPLY"\n' > "$T/bin/curl"; chmod +x "$T/bin/curl"
printf 'event: message_start\ndata: %s\n\nevent: content_block_delta\ndata: %s\n\n' "$G_START" "$G_TEXT" > "$CURL_REPLY"
cat > "$T/bin/grok" <<'G'
#!/bin/bash
echo "$*" >> "$GROK_ARGS"
printf '%s\n' '{"type":"result","text":"quota exceeded","usage":{"input_tokens":10,"output_tokens":5},"is_error":true,"subtype":"error_quota"}'
G
chmod +x "$T/bin/grok"
printf '{"review_models":["grok-4.7"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
: > "$GROK_ARGS"; rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/st/token-usage.tsv"
out=$(GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ -s "$GROK_ARGS" ] && /usr/bin/grep -q 'error_quota' "$T/st/cq/"*mfail* \
  && ! head -1 "$T/st/review-fix_1-x.md" 2>/dev/null | /usr/bin/grep -q 'VERDICT: APPROVE' \
  && ok "1373 a grok CLI envelope that reports an error is a MODEL_FAIL naming its subtype, never a verdict" \
  || ko "1373 an errored grok CLI envelope became a verdict: rc=$rc cli=$(cat "$GROK_ARGS") cq=$(cat "$T/st/cq/"* 2>/dev/null | head -3) line1=$(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)"
rm -f "$T/bin/curl" "$T/bin/grok"; unset CURL_BODY CURL_REPLY GROK_ARGS
suite_s=$(( $(date +%s) - suite_t0 ))
# #1413, #1488: the suite's own wall time is an info line, never a verdict. Wall time on a shared machine measures load, not
# the code, so a green suite must not turn red for it. The runtime guards are the suite's own `# seconds:` header,
# the slow cap, and the acceptance cap the runner enforces (SG_AC_TIMEOUT).
cap=${SG_AC_TIMEOUT:-300}
echo "info: the suite took ${suite_s}s of the ${cap}s acceptance cap"
rm -rf "$T"; exit $fail
