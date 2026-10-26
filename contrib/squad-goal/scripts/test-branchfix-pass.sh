#!/bin/bash
# Regression test for the fix pass (#401): branchfixpass.sh dispatches one fix run for each work branch whose kept review is at the
# tip and says CHANGES, once per reviewed tip, and stops per ADR-0134 when the findings do not change. Stub gh (every argv logged),
# stub mkreq.sh and launch.py (the dispatch autofix.sh uses), a bare local remote, temp state dir. One line per case.
# usage: bash test-branchfix-pass.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin" "$T/co"
cp "$d/branchfixpass.sh" "$d/revformat.py" "$d/branchfix.sh" "$d/workbranches.sh" "$d/address.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$T/s/" 2>/dev/null
cat > "$T/s/env.sh" <<J
export SG_STATE=$T/st SG_REPO=x/y SG_BASE=main SG_LAND_BRANCH=dev SG_CHECKOUT=$T/co SG_LAND_REMOTE=\${SG_LAND_REMOTE:-$T/origin.git} SG_CODING_RULES=rules
export SG_FRONTIER_SKIP_LABELS=spec,needs-ratification,frozen:owner
export SG_GH_AGGREGATE_WINDOW=0 SG_GH_CACHE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001
. "$T/s/address.sh"; . "$T/s/ghcall.sh"
J
# The dispatch stubs: mkreq.sh keeps the request and prints its path; launch.py logs the request and prints a run id (none with NORUN).
cat > "$T/s/mkreq.sh" <<'J'
#!/bin/bash
mkdir -p "$SG_STATE/req"; cp "$2" "$SG_STATE/req/$1.txt"; echo "$SG_STATE/req/$1.txt"
J
cat > "$T/s/launch.py" <<J
import os, sys
open("$T/launch.log", "a").write(open(sys.argv[1]).read().replace("\n", " ") + "\n")
if os.environ.get("REFUSE"): print("refused: the first coding model x is not in the router envelope", file=sys.stderr); sys.exit(1)
if not os.environ.get("NORUN"): print("run-%d" % sum(1 for _ in open("$T/launch.log")))
J
printf '#!/bin/bash\necho "a spend envelope"\n' > "$T/s/envelope-spend.sh"
O="$G -C $T/origin.git"
(cd "$T/co" && $G init -q -b main && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init && $G branch -q dev)
$G clone -q --bare "$T/co" "$T/origin.git"; $G clone -q "$T/origin.git" "$T/w" 2>/dev/null
push() { (cd "$T/w" && { $G checkout -q "$1" 2>/dev/null || $G checkout -q -b "$1" origin/dev; } && date +%s%N >> "g-$(printf '%s' "$1" | tr / _)" && $G add -A && $G -c user.email=a@b -c user.name=t commit -q -m "work $1" && $G push -q origin "$1" 2>/dev/null); }
# rev <branch> <verdict> <finding> [sha]: the review kept for the branch, as branchreview.sh keeps it (the tip unless a sha is named)
rev() { local k; k=$(printf '%s' "$1" | tr / _); printf 'VERDICT: %s\n## Blocking\n- %s\n' "$2" "$3" > "$T/st/review-$k.md"; echo "${4:-$($O rev-parse "$1")}" > "$T/st/review-$k.sha"; }
cat > "$T/bin/gh" <<J
#!/bin/bash
a="\${*//\$'\n'/ }"; echo "\$a" >> "$T/gh.log"
case "\$a" in
  *"pulls?per_page"*) echo "\${PRS:-[]}";;
  *"issues(states: OPEN"*) n=\$(for x in \${OPEN:-7 8 9}; do l=; for p in \${LABELED:-}; do [ "\${p%%=*}" = "\$x" ] && l="{\\"name\\":\\"\${p#*=}\\"}"; done; printf '{"number":%s,"title":"t","body":"","state":"OPEN","labels":{"nodes":[%s]}},' "\$x" "\$l"; done); echo "{\"data\":{\"repository\":{\"issues\":{\"pageInfo\":{\"hasNextPage\":false},\"nodes\":[\${n%,}]}}}}";;
  *"/labels"*) echo '[{"name":"frozen:owner"}]';;
  *) echo '{}';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
fp() { bash "$T/s/branchfixpass.sh" 2>&1; }
n() { cat "$T/launch.log" 2>/dev/null | /usr/bin/grep -c "work branch $1 "; }
for b in gui/7-x fix/8-y gui/9-z; do push "$b"; done
rev gui/7-x CHANGES "a.sh:3 the cap is wrong"; rev fix/8-y APPROVE "none"; rev gui/9-z CHANGES "b.sh:1 old" 0000000000000000000000000000000000000000

# 1) one fix run for a CHANGES review at the tip; an APPROVE and a review of an older sha get none
r=$(fp)
[ "$(n gui/7-x)" = 1 ] && ok "a CHANGES review at the tip dispatches one fix run" || ko "gui/7-x dispatched $(n gui/7-x) times: '$r'"
/usr/bin/grep -q "FIX RUN on the work branch gui/7-x for GitHub issue #7.*a.sh:3 the cap is wrong" "$T/launch.log" && ok "the request is the branchfix.sh text with the findings" || ko "request: $(cat "$T/launch.log" 2>/dev/null)"
/usr/bin/grep -q 'one entry for the child below' "$T/launch.log" && ok "the request asks for one child in the spawns list, as an autofix request does" || ko "no spawn instruction in the request"
[ "$(n fix/8-y)" = 0 ] && ok "an APPROVE review dispatches nothing" || ko "an APPROVE branch was dispatched"
[ "$(n gui/9-z)" = 0 ] && ok "a review that is not at the tip dispatches nothing" || ko "a review of an old sha was dispatched"
# 2) a second pass at the same tip dispatches nothing
r=$(fp); [ "$(n gui/7-x)" = 1 ] && ok "a second pass at the same reviewed tip dispatches nothing" || ko "the same tip was dispatched again: $(n gui/7-x)"
# 3) a launch that prints no run id is no dispatch: the tip is retried on the next pass
push gui/7-x; rev gui/7-x CHANGES "c.sh:9 a new finding"
r=$(NORUN=1 fp); r=$(fp)
[ "$(n gui/7-x)" = 3 ] && ok "a launch with no run id frees the tip for the next pass" || ko "after a failed launch gui/7-x dispatched $(n gui/7-x) times"
# 4) the same findings at a new tip count as one failed round with no change (ADR-0134); a line number that moved is the same
#    finding. The round of case 3 and two more fail alike; at the third the rounds stop: one status line, the card frozen, once
: > "$T/launch.log"; o=
for k in 1 2 3 4 5; do push gui/7-x; rev gui/7-x CHANGES "c.sh:$((9+k)) a new finding"; o+=$(fp)$'\n'; o+=$(fp)$'\n'; done
[ "$(n gui/7-x)" = 2 ] && ok "after three fix rounds with no change the rounds stop" || ko "unchanged findings dispatched $(n gui/7-x) times: '$o'"
[ "$(printf '%s' "$o" | /usr/bin/grep -c '^BRANCHFIX STOP gui/7-x')" = 1 ] && ok "the stop is said once" || ko "stop lines: '$o'"
[ "$(/usr/bin/grep -c 'issues/7/labels.*frozen:owner' "$T/gh.log")" = 1 ] && ok "the card is frozen for the owner once (frozen:owner, through the door)" || ko "label writes: $(/usr/bin/grep labels "$T/gh.log")"
s=$(cat "$T/st/cq/"*"#7-status~"* 2>/dev/null)
case "$s" in *"gui/7-x"*"frozen:owner"*) ok "the issue gets one status line through the status queue";; *) ko "no status line: '$s' $(ls "$T/st/cq")";; esac
# 5) changed findings restart the count
push gui/7-x; rev gui/7-x CHANGES "d.sh:1 another finding"; r=$(fp)
[ "$(n gui/7-x)" = 3 ] && ok "changed findings restart the count" || ko "after changed findings gui/7-x dispatched $(n gui/7-x) times: '$r'"
# 6) the pass is bounded
: > "$T/launch.log"; for i in 10 11 12; do push "gui/$i-b"; rev "gui/$i-b" CHANGES "e.sh:$i fix"; done
r=$(OPEN="10 11 12" SG_FIX_PASS_MAX=2 fp)
[ "$(wc -l < "$T/launch.log" | tr -d ' ')" = 2 ] && ok "one pass dispatches at most SG_FIX_PASS_MAX fix runs" || ko "one pass dispatched $(wc -l < "$T/launch.log" | tr -d ' ')"
r=$(OPEN="10 11 12" SG_FIX_PASS_MAX=2 fp)
[ "$(wc -l < "$T/launch.log" | tr -d ' ')" = 3 ] && ok "the next pass dispatches the rest" || ko "second bounded pass: $(wc -l < "$T/launch.log" | tr -d ' ')"
# 7) a read that fails dispatches nothing
: > "$T/launch.log"; push gui/7-x; rev gui/7-x CHANGES "f.sh:1 x"; r=$(SG_LAND_REMOTE=$T/none.git fp)
[ ! -s "$T/launch.log" ] && ok "an unreadable branch list dispatches nothing" || ko "a fix ran with no branch list"
case "$r" in *"BRANCHFIX: the remote branch list is not readable"*) ok "the failed read is named";; *) ko "no read failure line: '$r'";; esac
# 8) every GitHub call goes through the door: no bare gh in the pass
/usr/bin/grep -nE '(^|[^_])gh api' "$d/branchfixpass.sh" | /usr/bin/grep -v '^[0-9]*:#' >/dev/null && ko "branchfixpass.sh calls gh api directly" || ok "the pass calls GitHub only through ghcall.sh"
# 9) an issue that carries a label of SG_FRONTIER_SKIP_LABELS is skipped, read from the open-issue list with no other call
: > "$T/launch.log"; for i in 13 14; do push "gui/$i-l"; rev "gui/$i-l" CHANGES "g.sh:$i fix"; done; : > "$T/gh.log"
r=$(OPEN="13 14" LABELED="13=needs-ratification 14=other" fp)
[ "$(n gui/13-l)" = 0 ] && ok "an issue with a frontier skip label gets no fix run" || ko "the skip-labelled issue was dispatched: '$r'"
[ "$(n gui/14-l)" = 1 ] && ok "an issue with another label gets its fix run" || ko "gui/14-l dispatched $(n gui/14-l) times: '$r'"
/usr/bin/grep -q "issues/1[34]" "$T/gh.log" && ko "the labels were read with an extra call: $(/usr/bin/grep 'issues/1[34]' "$T/gh.log")" || ok "the labels come from the open-issue list, with no extra call"
# 10) the cycle runs the pass under its own single.sh lock, gated like the landers
# A CHANGES with no Blocking finding (debt only) is an approval (#392): it dispatches no fix round, as autopilot merges it.
push gui/10-w; printf 'VERDICT: CHANGES\n## Blocking\n\n## Debt\n- c.sh:1 — naming — unclear — rename\n' > "$T/st/review-gui_10-w.md"; $O rev-parse gui/10-w > "$T/st/review-gui_10-w.sha"
r=$(OPEN="7 8 9 10" fp); [ "$(n gui/10-w)" = 0 ] && ok "a debt-only CHANGES dispatches no fix round" || ko "a debt-only CHANGES was dispatched: '$r'"
w=$(/usr/bin/grep -n 'single.sh" branchfix ' "$d/watch.sh")
case "$w" in *'SG_LAND_PAUSED'*'branchfixpass.sh'*) ok "watch.sh runs the pass under its own single.sh lock, gated by SG_LAND_PAUSED";; *) ko "watch.sh does not run the fix pass: '$w'";; esac
# 11) (#768) the fix round pushes nothing and runs no prepush.sh: the child rules say "push nothing", the sandbox denies prepush
: > "$T/launch.log"; push gui/15-p; rev gui/15-p CHANGES "h.sh:1 fix"; r=$(OPEN="15" fp)
q=$(/usr/bin/grep "work branch gui/15-p " "$T/launch.log")
case "$q" in *"git push"*|*prepush.sh*) ko "the fix round still asks for a push or prepush.sh: '$q'";; *"push nothing"*) ok "the fix round pushes nothing and runs no prepush.sh";; *) ko "the fix round does not say push nothing: '$q'";; esac
# 12) (#768) once the fix run has a verdict, the pass pushes the run's head to the work branch, fast-forward only, once
rid=$(cat "$T/st/auto/branchfix-gui_15-p-"*); tip=$($O rev-parse gui/15-p); tg=$T/tg; $G clone -q "$T/origin.git" "$tg" 2>/dev/null
(cd "$tg" && $G checkout -q -b "squad/run/$rid" "$tip" && echo fix > h && $G add h && $G -c user.email=a@b -c user.name=t commit -q -m "fix (Refs #15)")
mkdir -p "$T/co/runs/$rid"; printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n' "$tg" "$rid" > "$T/co/runs/$rid/record.jsonl"
r=$(OPEN="15" fp); [ "$($O rev-parse gui/15-p)" = "$tip" ] && ok "a fix run with no verdict yet is not pushed" || ko "a run with no verdict was pushed: '$r'"
echo '{"event": "verdict", "verdict": "completed"}' >> "$T/co/runs/$rid/record.jsonl"
r=$(OPEN="15" fp); [ "$($O rev-parse gui/15-p)" = "$($G -C "$tg" rev-parse "squad/run/$rid")" ] && ok "the fix run's head reaches the work branch" || ko "the work branch did not move: '$r'"
case "$r" in *"BRANCHFIX PUSH gui/15-p"*) ok "the push is named";; *) ko "no push line: '$r'";; esac
r=$(OPEN="15" fp); case "$r" in *"BRANCHFIX PUSH"*) ko "the push is made again: '$r'";; *) ok "the push is made once";; esac
push gui/16-n; rev gui/16-n CHANGES "i.sh:1 fix"; r=$(OPEN="16" fp); rid=$(cat "$T/st/auto/branchfix-gui_16-n-"*)
(cd "$tg" && $G fetch -q origin && $G checkout -q -b "squad/run/$rid" origin/main && echo y > k && $G add k && $G -c user.email=a@b -c user.name=t commit -q -m other)
mkdir -p "$T/co/runs/$rid"; printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n{"event": "verdict", "verdict": "completed"}\n' "$tg" "$rid" > "$T/co/runs/$rid/record.jsonl"
t16=$($O rev-parse gui/16-n); r=$(OPEN="16" fp); [ "$($O rev-parse gui/16-n)" = "$t16" ] && ok "a head that is not a fast-forward is not pushed" || ko "a non-fast-forward head was pushed: '$r'"
# 13) (#779) a fix run whose merge_land into its target is a no-op: the target branch stays at the base, and the child's own head
#     (the workspace head of its pass outcome, a fast-forward of the work branch) is the head pushed
push gui/17-c; rev gui/17-c CHANGES "j.sh:1 fix"; r=$(OPEN="17" fp); rid=$(cat "$T/st/auto/branchfix-gui_17-c-"*); tip=$($O rev-parse gui/17-c)
(cd "$tg" && $G fetch -q origin && $G checkout -q -b "squad/run/$rid" origin/dev && $G worktree add -q -b "child-$rid" "$T/cw" "$tip" && cd "$T/cw" && echo z > m && $G add m && $G -c user.email=a@b -c user.name=t commit -q -m "fix (Refs #17)")
ch=$($G -C "$T/cw" rev-parse HEAD); b0=$($G -C "$tg" rev-parse "squad/run/$rid"); mkdir -p "$T/co/runs/$rid"
printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n{"event": "outcome", "node": "route.router.1", "outcome": "pass", "workspace": {"name": "worktrees/child", "head": "%s"}}\n{"event": "land", "node": "route.router.1", "effect": {"name": "merge_land", "before": "%s", "head": "%s"}}\n{"event": "verdict", "verdict": "completed"}\n' "$tg" "$rid" "$ch" "$b0" "$b0" > "$T/co/runs/$rid/record.jsonl"
r=$(OPEN="17" fp); [ "$($O rev-parse gui/17-c)" = "$ch" ] && ok "a fix run with a no-op land pushes the child's own head" || ko "the child's head was not pushed: '$r'"
# 14) (#812) a launch.py that refuses the start is no dispatch: the pass says launch.py's own line, writes no fix marker,
#     and the next pass retries the tip.
push gui/18-r; rev gui/18-r CHANGES "k.sh:1 fix"
r=$(OPEN="18" REFUSE=1 fp)
case "$r" in *"refused: the first coding model"*) ok "a refused launch says launch.py's own line";; *) ko "no refusal line: '$r'";; esac
[ -z "$(ls "$T/st/auto"/branchfix-gui_18-r-* 2>/dev/null)" ] && ok "a refused launch writes no fix marker" || ko "a marker survived a refusal: $(ls "$T/st/auto"/branchfix-gui_18-r-*)"
r=$(OPEN="18" fp)
[ "$(n gui/18-r)" = 2 ] && ok "the next pass retries the refused fix run" || ko "retry: $(n gui/18-r) dispatch(es): '$r'"
exit $fail
