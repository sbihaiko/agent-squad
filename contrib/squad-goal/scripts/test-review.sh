#!/bin/bash
# Regression tests for review.sh with stubbed gh and claude (no network, no model). usage: bash test-review.sh [path/to/review.sh]
# 1) a reviewer that answers with a VERDICT is accepted: exit 0, rc 0 in review-times.tsv (the bug: rc=$? read the status of the superseded test, so every model "failed")
# 2) the branch tip moving during the review kills the reviewer and ends BLOCKED (superseded), not a hang.
# 7) #1501: the aging leash must not kill the only reachable model; 8) with several reachable models it still clamps.
src=${1:-$(dirname "$0")/review.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_SHARED="$T/st"   # the shared marks (quota-guard, *-limited) are the suite's own, never the machine's under ~/.cache
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$src" "$T/s/review.sh"; cp "$(dirname "$src")/revformat.py" "$(dirname "$src")/ghcall.sh" "$(dirname "$src")/ghread.py" "$(dirname "$src")/modelreg.py" "$T/s/"; : > "$T/s/codex-reset.py"
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
  *Accept*compare*) printf 'diff --git a/f b/f\n+x\n';;
  # one answer for both compare reads: `.status` (is the tip ahead of the reviewed one) and `.files[].filename` (the fast lane)
  *compare*) printf '{"status":"ahead","files":['; sep=""; for f in ${GH_FILES:-UI/a.cs}; do printf '%s{"filename":"%s"}' "$sep" "$f"; sep=,; done; printf ']}\n';;
  *commits/*) if [ -n "$GH_MOVE" ] && [ -e "$GH_MOVE" ]; then echo '{"sha":"bbb222"}'; else echo '{"sha":"aaa111"}'; fi;;
  *) echo "stub";;
esac
J
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > "${CLAUDE_PROMPT:-/dev/null}"; echo "$*" >> "${CLAUDE_ARGS:-/dev/null}"; [ -n "$CLAUDE_FAIL" ] && { echo "API Error: 529 overloaded_error"; exit 1; }; [ -n "$CLAUDE_RESOLVED" ] && { printf 'VERDICT: APPROVE\n## Resolved\n- a.cs:1 — diverges from ADR-0001 — fixed: the ADR is followed now — UI.Tests/a.cs\n## Debt\n- b.cs:2 — naming — nobody can tell what it names — rename it\n'; exit 0; }; [ -n "$CLAUDE_ERRJSON" ] && case "$*" in *"--model bad"*) printf '{"is_error":true,"subtype":"error_max_turns","result":"I'"'"'ll review the branch against the issue, reading the code.","usage":{"input_tokens":10,"output_tokens":5,"cache_read_input_tokens":0}}\n'; exit 0;; esac; [ -n "$CLAUDE_TAGGED" ] && { printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — diverges from ADR-0001 — wrong — fix\n## Debt\n- b.cs:2 — naming — nobody can tell what it names — rename it\n'; exit 0; }; [ -n "$CLAUDE_NARRATE" ] && { printf "Checking the delta first.\nVERDICT: CHANGES\n1. a finding\nIn summary, close but another round.\n"; exit 0; }; if [ -n "$CLAUDE_SLOW" ]; then touch "$GH_MOVE"; sleep 40; fi; [ -n "$CLAUDE_SLEEP" ] && sleep "$CLAUDE_SLEEP"; echo "VERDICT: APPROVE"
J
chmod +x "$T/bin/gh" "$T/bin/claude"; export PATH="$T/bin:$PATH"
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
  [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: CHANGES" ] && /usr/bin/grep -q '^## Blocking' "$T/st/review-fix_1-x.md" && /usr/bin/grep -q '^## Debt' "$T/st/review-fix_1-x.md" && /usr/bin/grep -q '^- b.cs:2 — ' "$T/st/review-fix_1-x.md" && ok "$mode: both sections survive into the saved review" || ko "$mode saved review: $(cat "$T/st/review-fix_1-x.md")"
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
# outside its hours window and behind quota-guard; the survivor's own timeout (8 s) is longer than
# the leash (2 s). A 4 s answer must be accepted, not killed at the leash.
h=$((10#$(date +%H))); a=$(( (h+1)%24 )); b=$(( (h+2)%24 )); printf -v wa '%02d-%02d' $a $b
printf '{"review_models":["gemini-3.8-flash-high","claude:claude-opus-5-5","claude:claude-haiku-4-5"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":["claude:claude-opus-5-5"],"hours":{"review_models":{"claude:claude-opus-5-5":"%s"}},"review_timeout_seconds_by_model":{"claude:claude-haiku-4-5":8}}' "$wa" > "$T/s/models.json"
echo $(( $(date +%s) + 3600 )) > "$T/st/agy-limited"; touch "$T/st/quota-guard"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
t=$(date +%s)
out=$(SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1 SG_REVIEW_TIMEOUT=2 CLAUDE_SLEEP=4 bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
el=$(( $(date +%s) - t ))
case "$out" in model=claude:claude-haiku-4-5*) [ $rc = 0 ] && [ $el -ge 4 ] && ok "leash + one reachable model: survivor uses its own timeout ($el s)" || ko "leash + one reachable: rc=$rc ${el}s '$out' $(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)";; *) ko "leash + one reachable: wanted the survivor, got '$out' rc=$rc ${el}s $(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)";; esac
# 8) with several reachable models the leash still applies: two healthy claude entries, 4 s answer, 2 s leash -> first dies, chain depth 1, BLOCKED
printf '{"review_models":["claude:a","claude:b"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/agy-limited" "$T/st/quota-guard" "$T/st/review-fix_1-x".* "$T/st/cq/"*
out=$(SG_REVIEW_CHAIN_DEPTH=1 SG_REVIEW_RETRY_BUDGET=1 SG_REVIEW_TIMEOUT=2 CLAUDE_SLEEP=4 bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
[ $rc != 0 ] && head -1 "$T/st/review-fix_1-x.md" | grep -q BLOCKED && /usr/bin/grep -q 'after 2s' "$T/st/cq/x__y#"*mtimeout* && ok "leash + several reachable models: still clamped" || ko "several reachable leash: rc=$rc '$out' $(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null) cq=$(cat "$T/st/cq/"*mtimeout* 2>/dev/null)"
[ -n "$(ls "$T/st/cq/")" ] && [ -z "$(ls "$T/st/cq/" | /usr/bin/grep -v '^x__y#8-status~')" ] && ok "every review note is queued as the one status comment of the branch, edited in place (#328)" || ko "a review note is not a status comment: $(ls "$T/st/cq/")"
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
# #643: a chain id with a context suffix (claude:nt[1m]) matches its per-model settings written without it (claude:nt).
printf '{"review_models":["claude:nt[1m]"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$CURL_BODY"; : > "$CLAUDE_ARGS"
GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
[ ! -s "$CLAUDE_ARGS" ] && [ -s "$CURL_BODY" ] && ok "no tools: a [1m] chain id matches its entry without the suffix (#643)" || ko "no tools: claude:nt[1m] kept its tools: args=$(cat "$CLAUDE_ARGS")"
printf '{"review_models":["claude:s[1m]"],"review_timeout_seconds_by_model":{"claude:s":2},"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*
CLAUDE_SLEEP=4 GH_FILES="UI/x.cs" bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
/usr/bin/grep -q 'after 2s' "$T/st/cq/"*mtimeout* 2>/dev/null && ok "review_timeout_seconds_by_model: a [1m] chain id takes its timeout without the suffix (#643)" || ko "per-model timeout missed claude:s[1m]: $(cat "$T/st/cq/"* 2>/dev/null | head -3)"
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
W="$T/child"; /usr/bin/git init -q -b main "$W"; /usr/bin/git -C "$W" -c user.email=a@b -c user.name=t commit -q --allow-empty -m base
/usr/bin/git -C "$W" checkout -q -b gui/9; /usr/bin/git -C "$W" -c user.email=a@b -c user.name=t commit -q --allow-empty -m child
/usr/bin/git init -q -b main "$T/co"; /usr/bin/git -C "$T/co" -c user.email=a@b -c user.name=t commit -q --allow-empty -m checkout
sed -i '' "s|SG_CHECKOUT=$T;|SG_CHECKOUT=$T/co;|" "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "$GROK_ARGS"; sleep 12; echo "VERDICT: APPROVE"\n' > "$T/bin/grok"; chmod +x "$T/bin/grok"
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
unset GROK_ARGS
printf '#!/bin/bash\necho "$*" >> "$AGY_ARGS"; echo "VERDICT: APPROVE"\n' > "$T/bin/agy"; chmod +x "$T/bin/agy"
printf '{"review_models":["gemini-3.8-flash-medium"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
export AGY_ARGS="$T/agy-args"; : > "$AGY_ARGS"; rm -f "$T/st/review-gui_9".*
out=$(cd "$W" && bash "$T/s/review.sh" --worktree main 9 2>/dev/null); rc=$?
/usr/bin/grep -q -- '--mode plan' "$AGY_ARGS" && ! /usr/bin/grep -q -- '--dangerously-skip-permissions' "$AGY_ARGS" \
  && ko "13 the agy review runs in plan mode with nothing to approve its tools: $(cat "$AGY_ARGS")" \
  || ok "13 the agy review can use its tools headless"
unset AGY_ARGS
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
printf '#!/bin/bash\n( sleep 61; echo "VERDICT: APPROVE" ) &\nwait\n' > "$T/bin/claude"; chmod +x "$T/bin/claude"
printf '{"review_models":["claude:hung"],"model_retry_budget":1,"review_timeout_seconds":35,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-gui_9".* "$T/st/cq/"*mtimeout*; t=$(date +%s)
out=$(cd "$W" && bash "$T/s/review.sh" --worktree main 9 > "$T/hb"); rc=$?; el=$(( $(date +%s) - t ))
sleep 1; left=$(ps -ax -o pid,pgid,ppid,command | /usr/bin/grep -c '[s]leep 61'); who=$(ps -ax -o pid,pgid,ppid,command | /usr/bin/grep '[s]leep 61')
[ $rc != 0 ] && [ $el -ge 35 ] && [ $el -lt 60 ] && [ "$left" = 0 ] && /usr/bin/grep -q 'after 35s' "$T/st/cq/"*mtimeout* 2>/dev/null \
  && ok "14 a child the CLI spawns is killed at the timeout, and nothing it spawned survives (${el}s)" \
  || ko "14 the spawned child outlives the timeout: rc=$rc ${el}s survivors=$left [$who] $(cat "$T/st/cq/"*mtimeout* 2>/dev/null)"
/usr/bin/grep -q 'still running' "$T/hb" && ok "14 the wait says the review is still running" || ko "14 the wait is silent: $(cat "$T/hb")"

# 15) #622: grok answers 402 "usage balance exhausted" on every call. A balance has no reset, so the first such answer
# writes $SG_SHARED/grok-limited and every later review skips grok until the owner removes it -- no dead try per round.
export SG_SHARED="$T/st"
printf '#!/bin/bash\necho x >> "$T/grokcalls"\necho "Error: 402 Payment Required: Grok Build usage balance exhausted" >&2; exit 1\n' | sed "s|\$T|$T|" > "$T/bin/grok"
printf '#!/bin/bash\ncat >/dev/null; echo "VERDICT: APPROVE"\n' > "$T/bin/claude"; chmod +x "$T/bin/grok" "$T/bin/claude"
printf '{"review_models":["grok-4.6","claude:ok"],"review_order":"config","model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/grok-limited" "$T/grokcalls" "$T/st/cq/"*
bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
[ -e "$T/st/grok-limited" ] && ok "15 a 402 balance answer writes grok-limited" || ko "15 no grok-limited marker after a 402"
rm -f "$T/st/review-fix_1-x".* "$T/grokcalls" "$T/st/cq/"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null)
[ ! -e "$T/grokcalls" ] && case "$out" in model=claude:ok*) true;; *) false;; esac && ok "15 grok-limited skips grok on the next review" || ko "15 grok tried again: calls=$(wc -l < "$T/grokcalls" 2>/dev/null) '$out'"
/usr/bin/grep -q 'owner' "$T/st/cq/"*mquota* 2>/dev/null && ok "15 the skip line says the owner clears the marker" || ko "15 skip line: $(cat "$T/st/cq/"*mquota* 2>/dev/null)"
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
J='{"verdict":"CHANGES","blocking":[{"location":"a.cs:1","defect":"diverges from ADR-0001","failure":"wrong","fix":"fix"}],"debt":[],"resolved":[]}'
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
printf '%s\n' '$J' > "\$o"
C
chmod +x "$T/bin/claude" "$T/bin/codex"
printf '{"review_models":["claude:m"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
/usr/bin/grep -q -- '--json-schema' "$T/args21" && ok "21 claude -p asks for JSON with --json-schema" || ko "21 claude args: $(cat "$T/args21")"
/usr/bin/grep -q '"verdict"' "$T/prompt21" && ok "21 the full prompt carries the schema" || ko "21 the full prompt has no schema"
[ $rc = 0 ] && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: CHANGES" ] && /usr/bin/grep -q '^- a.cs:1 — diverges from ADR-0001 — wrong — fix$' "$T/st/review-fix_1-x.md" \
  && ok "21 claude: the JSON answer is rendered into review-<key>.md" || ko "21 claude render: rc=$rc '$out' $(cat "$T/st/review-fix_1-x.md")"
jq -e '.verdict=="CHANGES"' "$T/st/review-fix_1-x.answer.json" >/dev/null 2>&1 && [ ! -e "$T/st/review-fix_1-x.json" ] \
  && ok "21 the raw JSON is kept as review-<key>.answer.json" || ko "21 raw JSON: $(/bin/ls "$T/st" | /usr/bin/grep fix_1-x)"
printf '{"review_models":["gpt-x"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
jq -e '.properties.verdict.enum==["APPROVE","CHANGES"]' "$T/schema21c" >/dev/null 2>&1 && ok "21 codex asks for JSON with --output-schema" || ko "21 codex args: $(cat "$T/args21c")"
[ $rc = 0 ] && /usr/bin/grep -q '^- a.cs:1 — diverges from ADR-0001 — wrong — fix$' "$T/st/review-fix_1-x.md" && ok "21 codex: the JSON answer is rendered" || ko "21 codex render: rc=$rc '$out' $(cat "$T/st/review-fix_1-x.md")"
# 22) #633: a fix round answers in JSON with the same schema. claude -p carries --json-schema, the prompt carries the
# schema, and the previous findings it reads are the rendered markdown. A fixed finding in resolved renders the
# '## Resolved' section the fix-round check reads, and the next fix round reads that rendered review unchanged.
# With no Blocking finding left and one Debt finding, it is saved as an approval (#653); the model wrote CHANGES.
printf 'VERDICT: CHANGES\n## Blocking\n- a.cs:1 — x — y — z\n' > "$T/st/review-fix_1-x.md"; echo ccc333 > "$T/st/review-fix_1-x.sha"
printf '{"review_models":["claude:m"],"model_retry_budget":1,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
cat > "$T/j21" <<'R'
{"verdict":"CHANGES","blocking":[],"debt":[{"location":"b.cs:2","defect":"naming","failure":"nobody can tell what it names","fix":"rename it"}],
 "resolved":[{"location":"a.cs:1","defect":"x","failure":"fixed: the ADR is followed now","fix":"UI.Tests/a.cs"}]}
R
rm -f "$T/st/cq/"*; out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>/dev/null); rc=$?
/usr/bin/grep -q 'FIX ROUND' "$T/prompt21" && /usr/bin/grep -q -- '--json-schema' "$T/args21" && ok "22 a fix round asks for JSON with --json-schema" || ko "22 fix round args: $(cat "$T/args21")"
sed -n '1,/=== BRANCH/p' "$T/prompt21" | /usr/bin/grep -q '"resolved"' && ok "22 the fix prompt carries the schema" || ko "22 the fix prompt has no schema"
sed -n '/=== PREVIOUS FINDINGS/,/=== DELTA/p' "$T/prompt21" | /usr/bin/grep -q '^- a.cs:1 — x — y — z$' && ok "22 the fix prompt reads the previous findings as markdown" || ko "22 previous findings: $(sed -n '/=== PREVIOUS/,/=== DELTA/p' "$T/prompt21")"
[ $rc = 0 ] && [ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: APPROVE (the model wrote CHANGES with only Debt findings)" ] && /usr/bin/grep -q '^## Resolved$' "$T/st/review-fix_1-x.md" \
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
[ $rc = 0 ] && [ "$l1" = "VERDICT: APPROVE (the model wrote CHANGES with only Debt findings)" ] && /usr/bin/grep -q '^- b.cs:2 — ' "$T/st/review-fix_1-x.md" \
  && ok "24 a debt-only CHANGES is saved as APPROVE, its Debt kept" || ko "24 debt-only: rc=$rc '$l1'"
printf '%s\n' "$l1" | /usr/bin/grep -q 'VERDICT: APPROVE' && ok "24 wave.sh reads it as an approval" || ko "24 wave.sh reads '$l1'"
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > "${CLAUDE_PROMPT:-/dev/null}"; printf 'VERDICT: APPROVE\n'
J
echo ccc333 > "$T/st/review-fix_1-x.sha"; printf 'VERDICT: APPROVE (the model wrote CHANGES with only Debt findings)\n## Debt\n- b.cs:2 — x — y — z\n' > "$T/st/review-fix_1-x.md"
: > "$T/prompt"; CLAUDE_PROMPT="$T/prompt" bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
/usr/bin/grep -q 'FIX ROUND' "$T/prompt" && ko "24 the saved approval started a fix round" || ok "24 the saved approval starts no fix round"
cat > "$T/bin/claude" <<'J'
#!/bin/bash
cat > /dev/null; printf 'VERDICT: CHANGES\n'; [ -n "$CLAUDE_BLOCKING" ] && printf '## Blocking\n- a.cs:1 — diverges from ADR-0001 — wrong — fix\n'
printf '## Debt\n- b.cs:2 — naming — nobody can tell what it names — rename it\n'
J
rm -f "$T/st/review-fix_1-x".*; CLAUDE_BLOCKING=1 bash "$T/s/review.sh" --branch fix/1-x 8 >/dev/null 2>&1
[ "$(head -1 "$T/st/review-fix_1-x.md")" = "VERDICT: CHANGES" ] && ok "24 a CHANGES with a Blocking finding is saved unchanged" || ko "24 blocking: '$(head -1 "$T/st/review-fix_1-x.md")'"

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
printf '#!/bin/bash\ncat > /dev/null; echo "$*" >> "$CLAUDE_ARGS"\ncase "$*" in *"--model slow"*) [ "$(cat "$T/slow.mode")" = answer ] && { echo "VERDICT: APPROVE"; exit 0; }; sleep 30;; *) echo "VERDICT: APPROVE";; esac\n' > "$T/bin/claude"; chmod +x "$T/bin/claude"
printf '{"review_models":["claude:slow","claude:fast"],"review_order":"config","model_retry_budget":1,"review_timeout_seconds":2,"review_timeout_bound":3,"review_timeout_omit_seconds":4,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
export CLAUDE_ARGS="$T/claude-args"; : > "$CLAUDE_ARGS"; echo timeout > "$T/slow.mode"
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
sleep 5; rv >/dev/null
[ "$(slow)" = 4 ] && ok "26 the window ends by time: the model is tried again" || ko "26 expiry: slow calls $(slow) $(cat "$T/rv.err")"
echo answer > "$T/slow.mode"; rv >/dev/null
echo timeout > "$T/slow.mode"; rv >/dev/null; rv >/dev/null
rv >/dev/null
[ "$(slow)" = 8 ] && ok "26 an answered review clears the streak: two timeouts after it do not omit the model" || ko "26 answer reset: slow calls $(slow) $(cat "$T/rv.err")"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/st/cycle.log" "$T/st/review-timeouts.json" "$CLAUDE_ARGS"; unset CLAUDE_ARGS

# The committed labeled sample measures historical CHANGES verdicts. The report is a read-only
# calibration instrument: it names the total, the Debt/nit-only count, and the rate.
calibration=$(python3 "$(dirname "$src")/review-calibration.py" "$(dirname "$src")/fixtures/review-calibration.json" 2>&1); rc=$?
[ $rc = 0 ] && [ "$calibration" = "labeled=10 debt_nit_only=1 rate=10.0%" ] \
  && ok "the calibration report measures the committed Debt/nit-only sample" \
  || ko "calibration report: rc=$rc '$calibration'"
# 26) a models.json with no model_retry_budget, or with it explicitly null, takes the shipped default of 2 tries -- never
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
    && ok "26 model_retry_budget $cfgv: the shipped default (2 tries) is used, with no integer error" \
    || ko "26 model_retry_budget $cfgv: calls='$n' rc=$rc err=$(head -2 "$T/e21" 2>/dev/null | tr '\n' ' ')"
done
# the configured value still wins over the default
printf '{"review_models":["claude:only"],"model_retry_budget":3,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/rcalls"
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>"$T/e21"); rc=$?
n=$(wc -l < "$T/rcalls" 2>/dev/null | tr -d ' ')
[ "$n" = 3 ] && ok "26 a configured model_retry_budget still wins (3 tries)" || ko "26 configured budget: calls='$n' rc=$rc $(head -1 "$T/st/review-fix_1-x.md" 2>/dev/null)"
# 26c) a configured budget of 0 is a real value, never the fallback: zero tries must stay zero
printf '{"review_models":["claude:only"],"model_retry_budget":0,"review_timeout_seconds":60,"review_delta_max_lines":3000,"anthropic_models":[]}' > "$T/s/models.json"
rm -f "$T/st/review-fix_1-x".* "$T/st/cq/"* "$T/rcalls"
out=$(bash "$T/s/review.sh" --branch fix/1-x 8 2>"$T/e21"); rc=$?
n=$( [ -f "$T/rcalls" ] && wc -l < "$T/rcalls" | tr -d ' ' || echo 0 )   # a zero budget calls no model, so the file is never written
[ "$n" = 0 ] && ! /usr/bin/grep -q 'integer expression expected' "$T/e21" && ok "26c a configured budget of 0 stays 0, the fallback does not fire" || ko "26c zero budget: calls='$n' rc=$rc"

rm -rf "$T"; exit $fail
