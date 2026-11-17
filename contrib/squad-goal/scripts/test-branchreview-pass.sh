#!/bin/bash
# Regression test for the review pass (#397): branchreviewpass.sh reviews each pushed work branch once per tip, so branchland.sh
# finds an APPROVE at the tip. Stub gh (every argv logged), stub review.sh (writes the verdict and the sha, logs the call), a bare
# local remote, temp state dir. One line per case. usage: bash test-branchreview-pass.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SQUAD_CAPS_POLL=0.2   # a review takes a review slot (caps.reviews, #567): a throwaway budget
capsctl() { PYTHONPATH="$SG_SQUAD" python3 -m squad.caps "$@"; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin"
cp "$d/branchreviewpass.sh" "$d/caps.sh" "$d/workbranches.sh" "$d/branchreview.sh" "$d/address.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/chainhealth.py" "$d/grokmark.py" "$d/provkey.py" "$d/modelreg.py" "$d/registry.json" "$d/models.json" "$T/s/" 2>/dev/null
cat > "$T/s/env.sh" <<J
export SG_STATE=$T/st SG_REPO=x/y SG_BASE=main SG_LAND_BRANCH=dev SG_CHECKOUT=$T/co SG_LAND_REMOTE=\${SG_LAND_REMOTE:-$T/origin.git}
export SG_GH_AGGREGATE_WINDOW=0 SG_GH_CACHE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001
. "$T/s/address.sh"; . "$T/s/ghcall.sh"
J
# The review stub: one log line per call, then the verdict and the reviewed sha under the branch key, as review.sh keeps them.
cat > "$T/s/review.sh" <<J
#!/bin/bash
br=\$3; key=\$(printf '%s' "\$br" | tr / _); echo "\$br \$4" >> "$T/review.log"
echo "\$(ls "$T/caps/reviews" 2>/dev/null | wc -l | tr -d ' ')" >> "$T/slots.log"   # the review slots held while this review runs
[ -n "\${SLOWREV:-}" ] && { mkdir -p "$T/run"; touch "$T/run/\$\$"; ls "$T/run" | wc -l | tr -d ' ' >> "$T/conc.log"; sleep "\$SLOWREV"; rm -f "$T/run/\$\$"; }
[ -n "\${FAILREV:-}" ] && { echo "model failed"; exit 1; }   # a failed review keeps no verdict and no sha
v=APPROVE; [ -n "\${CHANGEREV:-}" ] && v=CHANGES; echo "VERDICT: \$v" > "$T/st/review-\$key.md"; $G -C "$T/origin.git" rev-parse "\$br" > "$T/st/review-\$key.sha"
echo "model=stub"
J
O="$G -C $T/origin.git"
$G init -q -b main "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init && $G branch -q dev)
$G clone -q --bare "$T/co" "$T/origin.git"; $G clone -q "$T/origin.git" "$T/w" 2>/dev/null
# push <branch>: one new commit on the branch, cut from dev the first time.
push() { (cd "$T/w" && { $G checkout -q "$1" 2>/dev/null || $G checkout -q -b "$1" origin/dev; } && date +%s%N >> "g-$(printf '%s' "$1" | tr / _)" && $G add -A && $G -c user.email=a@b -c user.name=t commit -q -m "work $1" && $G push -q origin "$1" 2>/dev/null); }
for b in gui/7-x fix/8-y gui/9-z feature/7-q; do push "$b"; done
# gh: no open PR unless PRS names one; OPEN names the open issues; a commit read answers the remote tip of the branch.
cat > "$T/bin/gh" <<J
#!/bin/bash
a="\${*//\$'\n'/ }"; echo "\$a" >> "$T/gh.log"
case "\$a" in
  *"pulls?per_page"*) echo "\${PRS:-[]}";;
  *"issues(states: OPEN"*) n=\$(for x in \${OPEN:-7 8}; do printf '{"number":%s,"title":"t","body":"","state":"OPEN"},' "\$x"; done); echo "{\"data\":{\"repository\":{\"issues\":{\"pageInfo\":{\"hasNextPage\":false},\"nodes\":[\${n%,}]}}}}";;
  *"/commits/"*) b=\${a#*commits/}; b=\${b%% *}; echo "{\"sha\":\"\$($O rev-parse "\$b")\"}";;
  *) echo '{}';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
rp() { bash "$T/s/branchreviewpass.sh" 2>&1; }
n() { cat "$T/review.log" 2>/dev/null | /usr/bin/grep -c "^$1 "; }

# 1) one review for a work branch with an open issue; the closed-issue branch and a non-work branch get none. A work branch is
# reviewed whether or not a pull request names it (#361: the loop reads no pull request)
PRS='[{"number":41,"head":{"ref":"fix/8-y"}}]'; export PRS
r=$(rp)
[ "$(n gui/7-x)" = 1 ] && ok "a work branch with no review at its tip is reviewed once" || ko "gui/7-x reviewed $(n gui/7-x) times: '$r' $(cat "$T/review.log" 2>/dev/null)"
/usr/bin/grep -q "^gui/7-x 7$" "$T/review.log" && ok "the review names the branch and its issue" || ko "review call: $(cat "$T/review.log" 2>/dev/null)"
[ "$(n fix/8-y)" = 1 ] && ok "a branch a pull request names is reviewed like any other" || ko "the branch a pull request names was not reviewed"
[ "$(n gui/9-z)" = 0 ] && ok "a branch that names no open issue is not reviewed" || ko "the closed-issue branch was reviewed"
[ "$(n feature/7-q)" = 0 ] && ok "a branch that is not a work branch is not reviewed" || ko "a non-work branch was reviewed"
[ "$(cat "$T/st/review-gui_7-x.sha" 2>/dev/null)" = "$($O rev-parse gui/7-x)" ] && ok "the reviewed sha is the tip" || ko "no reviewed sha at the tip"
# 2) the same tip is never reviewed twice
r=$(rp); [ "$(n gui/7-x)" = 1 ] && ok "a second pass on the same tip reviews nothing" || ko "the same tip was reviewed again: $(n gui/7-x)"
# 3) a new tip is reviewed once more
push gui/7-x; r=$(rp); r=$(rp)
[ "$(n gui/7-x)" = 2 ] && ok "a new tip is reviewed once" || ko "after a new push gui/7-x reviewed $(n gui/7-x) times"
# 4) the pass is bounded: a few branches per pass, the rest on the next passes
for i in 10 11 12 13; do push "gui/$i-b"; done; : > "$T/review.log"
r=$(OPEN="7 8 10 11 12 13" SG_REVIEW_PASS_MAX=2 rp)
[ "$(wc -l < "$T/review.log" | tr -d ' ')" = 2 ] && ok "one pass reviews at most SG_REVIEW_PASS_MAX branches" || ko "one pass reviewed $(wc -l < "$T/review.log" | tr -d ' ')"
r=$(OPEN="7 8 10 11 12 13" SG_REVIEW_PASS_MAX=2 rp)
[ "$(wc -l < "$T/review.log" | tr -d ' ')" = 4 ] && [ "$(sort -u "$T/review.log" | wc -l | tr -d ' ')" = 4 ] && ok "the next pass reviews the next branches" || ko "second bounded pass: $(cat "$T/review.log")"
# 5) a read that fails stops the pass and reviews nothing
: > "$T/review.log"; push gui/7-x; r=$(SG_LAND_REMOTE=$T/none.git rp)
[ ! -s "$T/review.log" ] && ok "an unreadable branch list reviews nothing" || ko "a review ran with no branch list"
case "$r" in *"BRANCHREVIEW: the remote branch list is not readable"*) ok "the failed read is named";; *) ko "no read failure line: '$r'";; esac
# 6) a failed review is retried, but at most 3 failed tries at one tip (ADR-0134); the stop is said once; a new tip resets the count
: > "$T/review.log"; o=; for _ in 1 2 3 4 5; do o+=$(FAILREV=1 rp)$'\n'; done
[ "$(n gui/7-x)" = 3 ] && ok "a failed review is retried, and stops after 3 failed tries at one tip" || ko "failed tip tried $(n gui/7-x) times"
[ "$(printf '%s' "$o" | /usr/bin/grep -c '^BRANCHREVIEW STOP gui/7-x: ')" = 1 ] && ok "the stop is said once" || ko "stop lines: '$o'"
push gui/7-x; r=$(FAILREV=1 rp)
[ "$(n gui/7-x)" = 4 ] && ok "a new tip resets the failed-try count" || ko "after a new tip gui/7-x tried $(n gui/7-x) times"
# 7) the cycle runs the pass under its own single.sh lock, gated like the landers
w=$(/usr/bin/grep -n 'single.sh" branchreview ' "$d/watch.sh")
case "$w" in *'SG_LAND_PAUSED'*'branchreviewpass.sh'*) ok "watch.sh runs the pass under its own single.sh lock, gated by SG_LAND_PAUSED";; *) ko "watch.sh does not run the review pass: '$w'";; esac
# 8) a stop expires (#426): a stop marker older than SG_REVIEW_STOP_TTL (default 1800 s) is cleared with its tries file, the branch is
# retried, and the retry restarts the count of 3. Each STOP names the models that failed and why, one line per model.
push gui/10-b; : > "$T/review.log"; mkdir -p "$T/st/cq"
printf 'MODEL_FAIL review codex on branch gui/10-b: exit 1, usage limit reached, try again at 14:05\n' > "$T/st/cq/x__y#10-status~mfail11-0.md"
printf 'MODEL_FORMAT review claude:opus on branch gui/10-b: line 2 is not the shape.\n' > "$T/st/cq/x__y#10-status~mformat12.md"
printf 'MODEL_HOURS review gemini-pro skipped on branch gui/10-b: outside its window in models.json.\n' > "$T/st/cq/x__y#10-status~mhours13.md"
printf 'MODEL_FAIL review codex on branch gui/10-b: exit 1, an old reason\n' > "$T/st/cq/x__y#10-status~mfail01-0.md"; touch -t 202001010000 "$T/st/cq/x__y#10-status~mfail01-0.md"
o=; for _ in 1 2 3 4; do o+=$(OPEN="7 8 10" FAILREV=1 rp)$'\n'; done
[ "$(n gui/10-b)" = 3 ] && ok "a fresh stop blocks the branch" || ko "fresh stop: tried $(n gui/10-b) times"
st=$(printf '%s' "$o" | /usr/bin/grep -c '^BRANCHREVIEW STOP gui/10-b')
[ "$st" = 1 ] && ok "the stop line is one line" || ko "stop lines ($st): '$o'"
l=$(printf '%s' "$o" | /usr/bin/grep '^BRANCHREVIEW STOP gui/10-b')
case "$l" in *"codex: exit 1, usage limit reached, try again at 14:05"*"claude:opus: line 2"*"gemini-pro: outside"*|*"claude:opus: line 2"*"codex: exit 1"*"gemini-pro: outside"*|*"gemini-pro: outside"*"claude:opus: line 2"*"codex: exit 1"*) ok "the stop names each failed model and its reason";; *) ko "stop line lacks the models: '$l'";; esac
case "$l" in *"an old reason"*) ko "the stop shows an older reason of a model with a newer one: '$l'";; *) ok "one reason per model, the newest";; esac
# 8b) the stop also reaches the issue's status file (#431): the models that failed and the chain summary, for the card
sf=$(ls "$T/st/cq/"*"#10-status~mchain"* 2>/dev/null | head -1)
[ -n "$sf" ] && /usr/bin/grep -q 'REVIEW_CHAIN STOP gui/10-b' "$sf" && /usr/bin/grep -q 'usage limit reached' "$sf" && ok "the stop is written to the issue status file" || ko "no status file with the stop: '$sf' $(cat "$sf" 2>/dev/null)"
touch -t 202001010000 "$T/st/auto/"branchreview-stop-gui_10-b-*
o=$(OPEN="7 8 10" FAILREV=1 rp)
[ "$(n gui/10-b)" = 4 ] && ok "an expired stop is cleared and the branch is retried" || ko "expired stop: tried $(n gui/10-b) times: '$o'"
[ "$(cat "$T/st/auto/"branchreview-tries-gui_10-b-* 2>/dev/null)" = 1 ] && ok "the retry restarts the count" || ko "tries after the retry: '$(cat "$T/st/auto/"branchreview-tries-gui_10-b-* 2>/dev/null)'"
ls "$T/st/auto/"branchreview-stop-gui_10-b-* >/dev/null 2>&1 && ko "the expired stop marker stayed" || ok "the expired marker is gone"
o=; for _ in 1 2 3; do o+=$(OPEN="7 8 10" FAILREV=1 rp)$'\n'; done
[ "$(n gui/10-b)" = 6 ] && ok "the restarted count stops after 3 tries again" || ko "after the restart: tried $(n gui/10-b) times"
o=$(SG_REVIEW_STOP_TTL=0 OPEN="7 8 10" FAILREV=1 rp)
[ "$(n gui/10-b)" = 7 ] && ok "SG_REVIEW_STOP_TTL sets the expiry" || ko "ttl 0: tried $(n gui/10-b) times"
# 8c) a missing resolver is a named line, and its stderr is not dropped (#505)
rm -f "$T/st/auto/"branchreview-tries-gui_10-b-* "$T/st/auto/"branchreview-stop-gui_10-b-* "$T/st/cq/"*"#10-status~mchain"*
mv "$T/s/modelreg.py" "$T/s/modelreg.py.off"
for _ in 1 2 3; do OPEN="7 8 10" FAILREV=1 rp >/dev/null; done
sf=$(ls -t "$T/st/cq/"*"#10-status~mchain"* 2>/dev/null | head -1)
[ -n "$sf" ] && /usr/bin/grep -q 'REVIEW_CHAIN: chainhealth failed' "$sf" && ok "a missing resolver is a named line" || ko "no chainhealth failure line: '$sf' $(cat "$sf" 2>/dev/null)"
mv "$T/s/modelreg.py.off" "$T/s/modelreg.py"
# 9) parallel reviews: up to SG_REVIEW_PARALLEL (default 3) branches at once, each with its own count and result
for i in 21 22 23 24; do push "gui/$i-p"; done; : > "$T/review.log"; rm -rf "$T/run" "$T/conc.log"; P="7 8 21 22 23 24"
s0=$(date +%s); r=$(OPEN="$P" SG_REVIEW_PASS_MAX=4 SLOWREV=2 rp); s1=$(date +%s)
[ "$(/usr/bin/grep -c '^gui/2[1-4]-p ' "$T/review.log")" = 4 ] && ok "four branches are reviewed in one pass" || ko "reviews: $(cat "$T/review.log"): '$r'"
[ "$(sort -n "$T/conc.log" | tail -1)" = 3 ] && ok "at most SG_REVIEW_PARALLEL (3) reviews run at once, and 3 do" || ko "peak concurrency: $(sort -n "$T/conc.log" | tr '\n' ' ')"
[ "$(/usr/bin/grep -c '^BRANCHREVIEW gui/2[1-4]-p' <<<"$r")" = 4 ] && ok "each review prints its own line" || ko "lines: '$r'"
: > "$T/review.log"; for i in 21 22 23 24; do push "gui/$i-p"; done; rm -f "$T/conc.log"
r=$(OPEN="$P" SG_REVIEW_PASS_MAX=4 SG_REVIEW_PARALLEL=1 SLOWREV=1 rp)
[ "$(sort -n "$T/conc.log" | tail -1)" = 1 ] && ok "SG_REVIEW_PARALLEL=1 reviews one at a time" || ko "serial peak: $(sort -n "$T/conc.log" | tr '\n' ' ')"
for i in 21 22 23 24; do push "gui/$i-p"; done; : > "$T/review.log"
r=$(OPEN="$P" SG_REVIEW_PASS_MAX=4 FAILREV=1 rp)
c=$(for i in 21 22 23 24; do cat "$T/st/auto/"branchreview-tries-gui_$i-p-* 2>/dev/null; done | tr -d '\n')
[ "$c" = 1111 ] && ok "failed parallel reviews keep one count per branch" || ko "tries per branch: '$c'"
[ "$(/usr/bin/grep -c '^BRANCHREVIEW FAILED gui/2[1-4]-p ' <<<"$r")" = 4 ] && ok "each failed review prints its own line" || ko "failed lines: '$r'"
ls "$T/st/auto/" | /usr/bin/grep -q 'branchreview-out' && ko "a result temp file stayed" || ok "no result temp file stays"
# 10) a review takes a review slot (caps.reviews): the stub sees one slot held while it runs, and none is left after the pass
for i in 31 32; do push "gui/$i-s"; done; : > "$T/review.log"; rm -f "$T/slots.log"
r=$(OPEN="7 8 31" SG_REVIEW_PASS_MAX=1 rp)
[ "$(cat "$T/slots.log" 2>/dev/null)" = 1 ] && ok "a branch review holds one review slot while it runs" || ko "slots held during the review: '$(cat "$T/slots.log" 2>&1)' '$r'"
[ "$(capsctl | python3 -c 'import json,sys; print(json.load(sys.stdin)["occupancy"]["reviews"])')" = 0 ] && ok "the review gives its slot back" || ko "a review slot stayed"
# 11) a budget with no free review slot: the pass waits for one (bounded), never counts a failed try, and reviews the branch later
sleep 60 >/dev/null 2>&1 & hs=$!; capsctl take reviews other --count 4 --pid "$hs" >/dev/null; : > "$T/review.log"
r=$(OPEN="7 8 32" SG_REVIEW_PASS_MAX=1 SG_REVIEW_SLOT_WAIT=1 rp)
[ "$(n gui/32-s)" = 0 ] && ok "no review runs while the review slots are full" || ko "a review ran without a slot: '$r'"
case "$r" in *"waiting"*"caps.reviews"*"other"*"BRANCHREVIEW SKIPPED gui/32-s"*) ok "the wait is logged with the full ceiling and its holder, then the branch is skipped for this pass";; *) ko "no wait/skip lines: '$r'";; esac
ls "$T/st/auto/" | /usr/bin/grep -q 'branchreview-tries-gui_32-s' && ko "a skipped review counted a failed try" || ok "a skipped review is no failed try"
capsctl release reviews other; kill $hs 2>/dev/null
r=$(OPEN="7 8 32" SG_REVIEW_PASS_MAX=1 rp); [ "$(n gui/32-s)" = 1 ] && ok "the next pass reviews the skipped branch" || ko "the skipped branch was not reviewed: '$r'"
# 12) a missing resolver is a named line on stderr, and the pass exits non-zero; the stderr is never dropped (#431)
push gui/31-r; mv "$T/s/modelreg.py" "$T/modelreg.py"; : > "$T/review.log"
o=; rc=0; for _ in 1 2 3; do o+=$(OPEN="7 8 31" FAILREV=1 bash "$T/s/branchreviewpass.sh" 2>&1) || rc=$?; done
[ "$rc" != 0 ] && ok "a missing resolver makes the pass exit non-zero" || ko "the pass exited 0 with no resolver"
case "$o" in *"chainhealth: modelreg.py is missing"*) ok "the missing resolver is a named line on stderr";; *) ko "the resolver failure is not named: '$o'";; esac
case "$o" in *"BRANCHREVIEW CHAINHEALTH gui/31-r"*) ok "the pass names the failed chain summary";; *) ko "the pass drops the chainhealth failure: '$o'";; esac
mv "$T/modelreg.py" "$T/s/modelreg.py"
# 12b) a module the resolver reads from beside itself is missing: a named line too, never a traceback (#1544)
rm -f "$T/st/auto/"branchreview-tries-gui_31-r-* "$T/st/auto/"branchreview-stop-gui_31-r-* "$T/st/cq/"*"#31-status~mchain"*
mv "$T/s/provkey.py" "$T/provkey.py"
o=; for _ in 1 2 3; do o+=$(OPEN="7 8 31" FAILREV=1 rp)$'\n'; done
mv "$T/provkey.py" "$T/s/provkey.py"
case "$o" in *"Traceback (most recent call last)"*) ko "a missing sibling module leaks a traceback: '$o'";;
  *"chainhealth: provkey.py is missing"*) ok "a missing sibling module is a named line, never a traceback";;
  *) ko "the missing sibling is not named: '$o'";; esac
# 13) an APPROVE starts a branchland pass at once, detached under single.sh (#955): a second kick while one runs is a no-op, the review
# pass never waits for the kick, and a CHANGES verdict starts none. The branchland stub logs one line per start.
cp "$d/single.sh" "$T/s/single.sh"
cat > "$T/s/branchland.sh" <<J
#!/bin/bash
echo kick >> "$T/kick.log"; sleep "\${KICKSLEEP:-0}"
J
chmod +x "$T/s/branchland.sh"; rm -f "$T/kick.log"; push gui/41-k
r=$(OPEN="41" SG_REVIEW_PASS_MAX=1 rp)
for _ in $(seq 1 50); do [ -s "$T/kick.log" ] && break; sleep 0.1; done
[ -s "$T/kick.log" ] && ok "an APPROVE starts a branchland pass" || ko "an APPROVE left no branchland kick: '$r'"
push gui/42-k; rm -f "$T/kick.log"
s0=$(date +%s); r=$(OPEN="42" SG_REVIEW_PASS_MAX=1 KICKSLEEP=4 rp); s1=$(date +%s)
[ $((s1 - s0)) -lt 3 ] && ok "the review pass does not wait for the kick" || ko "the review pass waited $((s1 - s0)) s for the kick"
for _ in $(seq 1 50); do [ -s "$T/kick.log" ] && break; sleep 0.1; done
push gui/43-k; rm -f "$T/kick.log"
r=$(OPEN="43" SG_REVIEW_PASS_MAX=1 CHANGEREV=1 rp); sleep 1
[ ! -s "$T/kick.log" ] && ok "a CHANGES verdict starts no branchland pass" || ko "a CHANGES verdict started a branchland pass: '$r'"
# A second kick while a pass runs is a no-op, because single.sh holds the lock: with the first kick asleep, the second approval
# adds no line. The stub's own log proves it, since the pass is still in its sleep when the second one approves.
for _ in $(seq 1 100); do [ -e "$T/st/locks/single-branchland" ] || break; sleep 0.1; done   # the earlier kick has ended: its lock is free
push gui/44-k; rm -f "$T/kick.log"
r=$(OPEN="44" SG_REVIEW_PASS_MAX=1 KICKSLEEP=3 rp)
for _ in $(seq 1 50); do [ -s "$T/kick.log" ] && break; sleep 0.1; done
push gui/45-k
r2=$(OPEN="45" SG_REVIEW_PASS_MAX=1 KICKSLEEP=3 rp)
[ "$(wc -l < "$T/kick.log" 2>/dev/null | tr -d ' ')" = 1 ] && ok "a second kick while a branchland pass runs is a no-op" || ko "two kicks ran: '$(cat "$T/kick.log" 2>&1)'"
sleep 3
exit $fail
