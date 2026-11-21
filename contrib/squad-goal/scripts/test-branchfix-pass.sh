#!/bin/bash
# seconds: 11.09
# Regression test for the fix pass (#401): branchfixpass.sh dispatches one fix run for each work branch whose kept review is at the
# tip and says CHANGES, once per reviewed tip, and stops per ADR-0134 when the findings do not change. Stub gh (every argv logged),
# stub mkreq.sh and launch.py (the dispatch autofix.sh uses), a bare local remote, temp state dir. One line per case.
# usage: bash test-branchfix-pass.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin" "$T/co"
cp "$d/branchfixpass.sh" "$d/revformat.py" "$d/branchfix.sh" "$d/workbranches.sh" "$d/address.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$d/drainpolicy.py" "$T/s/" 2>/dev/null
cat > "$T/s/env.sh" <<J
export SG_STATE=$T/st SG_REPO=x/y SG_BASE=main SG_LAND_BRANCH=dev SG_CHECKOUT=$T/co SG_LAND_REMOTE=\${SG_LAND_REMOTE:-$T/origin.git} SG_CODING_RULES=rules
export SG_FRONTIER_SKIP_LABELS=spec,needs-ratification,frozen:owner SG_SQUAD=$(cd "$d/../../.." && pwd)
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
open("$T/fix.log", "a").write(("1" if os.environ.get("SG_FIX_RUN") == "1" else "0") + "\n")
if os.environ.get("REFUSE"): print("refused: the first coding model x is not in the router envelope", file=sys.stderr); sys.exit(1)
if not os.environ.get("NORUN"):
    c = int(open("$T/rc").read() or 0) if os.path.exists("$T/rc") else 0   # one id per launch, never reused across cases (#2345)
    c += 1; open("$T/rc", "w").write(str(c)); print("run-%d" % c)
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
# (#2350) The round names ONE base: the work-branch tip, in the CUT FROM line launch.py reads. The run worktree starts there,
# so the child commits on the work branch by construction and its head fast-forwards. The request never tells the child to
# check out origin/<branch>: that second base is what made the head no fast-forward of the branch.
q=$(head -1 "$T/launch.log" 2>/dev/null)
case "$q" in *"CUT FROM: gui/7-x"*) ok "2350 the request names one base, the work-branch tip (CUT FROM)";; *) ko "2350 the request names no work-branch tip: '$q'";; esac
case "$q" in *"check out origin/"*) ko "2350 the request names a second base: '$q'";; *) ok "2350 the request sends the child to the branch its worktree holds";; esac
# (#1374) the round is marked as a fix run, so the final gate proves its commands in the child's tree, never in the target
[ "$(head -1 "$T/fix.log" 2>/dev/null)" = 1 ] && ok "the round is launched marked as a fix run (#1374)" || ko "SG_FIX_RUN did not reach launch.py"
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
#    finding. The round of case 3 and the first pass here fail alike; the next pass stops the branch: one status line, the card frozen, once
: > "$T/launch.log"; o=
for k in 1 2 3 4 5; do push gui/7-x; rev gui/7-x CHANGES "c.sh:$((9+k)) a new finding"; o+=$(fp)$'\n'; o+=$(fp)$'\n'; done
[ "$(n gui/7-x)" = 1 ] && ok "after two fix rounds with no change the rounds stop" || ko "unchanged findings dispatched $(n gui/7-x) times: '$o'"
[ "$(printf '%s' "$o" | /usr/bin/grep -c '^BRANCHFIX STOP gui/7-x')" = 1 ] && ok "the stop is said once" || ko "stop lines: '$o'"
[ "$(/usr/bin/grep -c 'issues/7/labels.*frozen:owner' "$T/gh.log")" = 1 ] && ok "the card is frozen for the owner once (frozen:owner, through the door)" || ko "label writes: $(/usr/bin/grep labels "$T/gh.log")"
s=$(cat "$T/st/cq/"*"#7-status~"* 2>/dev/null)
case "$s" in *"gui/7-x"*"frozen:owner"*) ok "the issue gets one status line through the status queue";; *) ko "no status line: '$s' $(ls "$T/st/cq")";; esac
# 5) changed findings restart the count
push gui/7-x; rev gui/7-x CHANGES "d.sh:1 another finding"; r=$(fp)
[ "$(n gui/7-x)" = 2 ] && ok "changed findings restart the count" || ko "after changed findings gui/7-x dispatched $(n gui/7-x) times: '$r'"
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
# 7b) (#2323) a connect that drops is transient: the same pass reads the branch list again, so one dropped connect neither ends
#     the pass nor costs its fix round. A read that keeps failing still names itself once (case 7).
cat > "$T/bin/flakygit" <<J
#!/bin/bash
echo "\$*" >> "$T/git.log"
[ "\$1" = ls-remote ] && [ ! -e "$T/connected" ] && { touch "$T/connected"; echo "fatal: unable to access 'https://github.com/x/y.git/': Failed to connect to github.com port 443 after 75000 ms: Couldn't connect to server" >&2; exit 128; }
exec /usr/bin/git "\$@"
J
chmod +x "$T/bin/flakygit"; : > "$T/launch.log"; rm -f "$T/connected" "$T/git.log"
push gui/7-x; rev gui/7-x CHANGES "w.sh:1 the transient read"
r=$(OPEN="7" SG_GIT="$T/bin/flakygit" SG_BRANCH_LIST_PAUSE=0 fp)
[ "$(/usr/bin/grep -c 'ls-remote --heads' "$T/git.log" 2>/dev/null)" = 2 ] && ok "2323 a dropped connect: the branch list is read again inside the same pass" || ko "2323 the dropped connect was not retried: '$(cat "$T/git.log" 2>&1)' '$r'"
[ "$(n gui/7-x)" = 1 ] && ok "2323 the retried read keeps the pass's fix round" || ko "2323 one dropped connect cost the pass its fix round: '$r'"
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
# 15) (#841, ADR-0146) a branch whose issue carries `loop-bug` gets no fix run; the pass says why, once per tip (and dispatches
#     nothing on later passes while the label holds)
: > "$T/launch.log"; push gui/20-lb; rev gui/20-lb CHANGES "l.sh:1 fix"
r=$(OPEN="20" LABELED="20=loop-bug" fp); r="$r"$'\n'"$(OPEN="20" LABELED="20=loop-bug" fp)"
[ "$(n gui/20-lb)" = 0 ] && ok "a loop-bug branch gets no fix run" || ko "a loop-bug branch was dispatched: '$r'"
[ "$(printf '%s' "$r" | /usr/bin/grep -c '^BRANCHFIX SKIP gui/20-lb: loop-bug, fixed outside the loop$')" = 1 ] && ok "the skip is said once per tip" || ko "skip lines: '$r'"
# 16) (#841, B1) while a defect in the dispatch path holds LOOP STOP, no fix run starts, even for a plain bug; the skip
# says LOOP STOP. 
: > "$T/launch.log"; push gui/21-ls; rev gui/21-ls CHANGES "m.sh:1 fix"
r=$(OPEN="21 90" LABELED="90=loop-bug:dispatch" fp)
[ "$(n gui/21-ls)" = 0 ] && ok "LOOP STOP holds the fix pass for a plain bug" || ko "a fix run started under LOOP STOP: '$r'"
case "$r" in *"BRANCHFIX SKIP gui/21-ls: LOOP STOP holds, fixed outside the loop"*) ok "the skip names LOOP STOP";; *) ko "no LOOP STOP skip line: '$r'";; esac
# 17) (#841) a loop-bug:* label on one branch skips that branch only; a plain bug on another branch still gets its one launch.
# The class here is `loop-bug:publish`, which leaves dispatch open; an unknown class drains every restriction (#1980).
: > "$T/launch.log"; push gui/22-lx; push gui/23-pl; rev gui/22-lx CHANGES "p.sh:1 fix"; rev gui/23-pl CHANGES "q.sh:1 fix"
r=$(OPEN="22 23" LABELED="22=loop-bug:publish" fp)
[ "$(n gui/22-lx)" = 0 ] && ok "a loop-bug:* branch gets no fix run" || ko "a loop-bug:* branch was dispatched: '$r'"
[ "$(n gui/23-pl)" = 1 ] && ok "a plain bug branch still gets one fix run" || ko "a plain bug branch dispatched $(n gui/23-pl) times: '$r'"
# 17b) (#1708) the pass hands its list to the drain's one writer, and an open loop-bug of any spelling holds every fix run:
# the loop drains, so it starts no new run. Case 17 is a copy with no `watchd.sh`, which asks for no drain.
: > "$T/launch.log"; push gui/24-pd; rev gui/24-pd CHANGES "s.sh:1 fix"
printf '#!/bin/bash\necho 22\n' > "$T/lbcmd"; chmod +x "$T/lbcmd"
r=$(OPEN="22 24" LABELED="22=loop-bug" SG_LOOPBUG_CMD="$T/lbcmd" fp)
[ "$(n gui/24-pd)" = 0 ] && ok "1708 an open loop-bug holds the fix pass for a plain bug" || ko "1708 a fix run started under a loop-bug drain: '$r'"
case "$r" in *"LOOP STOP: #22"*) ok "1708 the fix pass names the loop-bug";; *) ko "1708 no LOOP STOP line: '$r'";; esac
# 17c) (#1789) while the loop is paused (auto/off: an owner pause or a drain), no fix run starts, the skip is said once per tip,
#      and the tip is dispatched on the first pass after the pause lifts.
: > "$T/launch.log"; push gui/25-off; rev gui/25-off CHANGES "t.sh:1 fix"
: > "$T/st/auto/off"
r=$(OPEN="25" fp); r="$r"$'\n'"$(OPEN="25" fp)"
[ "$(n gui/25-off)" = 0 ] && ok "1789 a paused loop starts no fix run" || ko "1789 a fix run started under auto/off: '$r'"
[ "$(printf '%s' "$r" | /usr/bin/grep -c '^BRANCHFIX SKIP gui/25-off: the loop is paused (auto/off), no run starts$')" = 1 ] && ok "1789 the pause skip is said once per tip" || ko "1789 skip lines: '$r'"
rm -f "$T/st/auto/off"
r=$(OPEN="25" fp)
[ "$(n gui/25-off)" = 1 ] && ok "1789 the tip is dispatched once the pause lifts" || ko "1789 after the pause: $(n gui/25-off) dispatch(es): '$r'"
# 17d) (#2381) a live hand hold on the branch's issue starts no fix round: the skip names the hold once per hold, and a stale
#      hold (past its until) no longer holds, so the tip is dispatched.
: > "$T/launch.log"; push gui/26-hh; rev gui/26-hh CHANGES "u.sh:1 fix"
printf 'by hal9k\nsince 2020-01-01T00:00:00Z\nuntil 2099-01-01T00:00:00Z\n' > "$T/st/auto/hold-26"
r=$(OPEN="26" fp); r="$r"$'\n'"$(OPEN="26" fp)"
[ "$(n gui/26-hh)" = 0 ] && ok "2381 a live hand hold starts no fix round" || ko "2381 a fix round started under a hand hold: '$r'"
[ "$(printf '%s' "$r" | /usr/bin/grep -c '^BRANCHFIX SKIP gui/26-hh: issue #26 has a live hand hold (hal9k until 2099-01-01T00:00:00Z), no fix round starts$')" = 1 ] \
  && ok "2381 the hold skip is said once per hold" || ko "2381 skip lines: '$r'"
printf 'by hal9k\nsince 2020-01-01T00:00:00Z\nuntil 2020-01-01T01:00:00Z\n' > "$T/st/auto/hold-26"
r=$(OPEN="26" fp)
[ "$(n gui/26-hh)" = 1 ] && ok "2381 a stale hold no longer holds the fix round" || ko "2381 after the hold went stale: $(n gui/26-hh) dispatch(es): '$r'"
rm -f "$T/st/auto/hold-26"
# 18) (#841) a plain bug alone: one launch, no skip line
: > "$T/launch.log"; push gui/24-pb; rev gui/24-pb CHANGES "r.sh:1 fix"
r=$(OPEN="24" LABELED="24=bug" fp)
[ "$(n gui/24-pb)" = 1 ] && ok "a plain bug gets one fix run" || ko "a plain bug dispatched $(n gui/24-pb) times: '$r'"
case "$r" in *"BRANCHFIX SKIP"*) ko "a plain bug was skipped: '$r'";; *) ok "a plain bug prints no skip line";; esac
# 19) (#1344, BLOCKING 1) a kept rebase branch (its kept marker names the ref the round rebases on) gets a round whose head is no
#     fast-forward of the reviewed tip: the pass pushes it with a lease on the reviewed sha, and the round's wording names that ref.
: > "$T/launch.log"; rb=fix/30-rebase-r1
push "$rb"; rev "$rb" CHANGES "g.sh:1 the rebase conflicts"; echo origin/dev > "$T/st/auto/kept-fix_30-rebase-r1"; K=$($O rev-parse "$rb")
r=$(OPEN="30" fp)
q=$(/usr/bin/grep "work branch $rb " "$T/launch.log")
[ "$(n "$rb")" = 1 ] && ok "a kept rebase branch gets one fix run" || ko "a kept rebase branch dispatched $(n "$rb") times: '$r'"
case "$q" in *"REBASE ROUND"*"rebase the branch it has checked out on origin/dev"*) ok "the round's wording names the ref the round rebases on";; *) ko "the round's wording: '$q'";; esac
rid=$(cat "$T/st/auto/branchfix-fix_30-rebase-r1-"* 2>/dev/null)
tg2=$T/tg2; rm -rf "$tg2"; $G clone -q "$T/origin.git" "$tg2" 2>/dev/null
(cd "$tg2" && $G checkout -q -b "squad/run/$rid" origin/dev && echo rb > rb30 && $G add rb30 && $G -c user.email=a@b -c user.name=t commit -q -m "the round (Refs #30)")
RH=$($G -C "$tg2" rev-parse HEAD); mkdir -p "$T/co/runs/$rid"
printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n{"event": "outcome", "node": "route.router.1", "outcome": "pass", "workspace": {"name": "worktrees/child", "head": "%s"}}\n{"event": "verdict", "verdict": "completed"}\n' "$tg2" "$rid" "$RH" > "$T/co/runs/$rid/record.jsonl"
r=$(OPEN="30" fp)
[ "$($O rev-parse "refs/heads/$rb")" = "$RH" ] && ok "the rebased round is pushed to the kept branch (lease on the reviewed sha)" || ko "the rebased round was not pushed: remote $($O rev-parse "refs/heads/$rb") want $RH: '$r'"
case "$r" in *"BRANCHFIX PUSH $rb"*) ok "the round's push is named";; *) ko "no push line for the round: '$r'";; esac
# 20) (#1344, DEBT) the wording follows the kept marker, not the branch name: a hand-made fix/*-rebase-* branch with no marker gets the
#     ordinary round.
: > "$T/launch.log"; push fix/31-rebase-hand; rev fix/31-rebase-hand CHANGES "m.sh:1 fix"; r=$(OPEN="31" fp)
q=$(/usr/bin/grep "work branch fix/31-rebase-hand " "$T/launch.log")
case "$q" in *"REBASE ROUND"*) ko "a hand-made rebase-named branch got the rebase round: '$q'";; *"commit on top of the branch your worktree has checked out"*) ok "a hand-made rebase-named branch gets the ordinary round";; *) ko "no ordinary round for the hand-made branch: '$q'";; esac
# 21) (#1344, BLOCKING 2) the lease is on the sha the round was dispatched for: a writer that pushed to the branch after the
#     review is never clobbered, and the round is refused by name.
: > "$T/launch.log"; rb=fix/32-rebase-r1
push "$rb"; rev "$rb" CHANGES "g.sh:1 the rebase conflicts"; echo origin/dev > "$T/st/auto/kept-fix_32-rebase-r1"; K=$($O rev-parse "$rb")
r=$(OPEN="32" fp); rid=$(cat "$T/st/auto/branchfix-fix_32-rebase-r1-"* 2>/dev/null)
tg4=$T/tg4; rm -rf "$tg4"; $G clone -q "$T/origin.git" "$tg4" 2>/dev/null
(cd "$tg4" && $G checkout -q -b "squad/run/$rid" origin/dev && echo rb > rb32 && $G add rb32 && $G -c user.email=a@b -c user.name=t commit -q -m "the round (Refs #32)")
RH=$($G -C "$tg4" rev-parse HEAD); mkdir -p "$T/co/runs/$rid"
printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n{"event": "outcome", "node": "route.router.1", "outcome": "pass", "workspace": {"name": "worktrees/child", "head": "%s"}}\n{"event": "verdict", "verdict": "completed"}\n' "$tg4" "$rid" "$RH" > "$T/co/runs/$rid/record.jsonl"
(cd "$T/w" && $G checkout -q "$rb" && echo w > w32 && $G add w32 && $G -c user.email=a@b -c user.name=t commit -q -m "a writer (Refs #32)" && $G push -q origin "$rb")
W=$($O rev-parse "$rb")
r=$(OPEN="32" fp)
[ "$($O rev-parse "refs/heads/$rb")" = "$W" ] && ok "a writer's push after the review is not clobbered by the round" || ko "the round clobbered a later push: remote $($O rev-parse "refs/heads/$rb") want $W: '$r'"
case "$r" in *"moved past the reviewed sha"*) ok "the refused round names the branch that moved";; *) ko "no moved-branch line: '$r'";; esac
# 22) (#1344, BLOCKING 1) a fix run that recorded no pass outcome names no head of its own: the target branch is never taken
#     as the round's head, so the kept branch is never replaced by the base.
: > "$T/launch.log"; rb=fix/33-rebase-r1
push "$rb"; rev "$rb" CHANGES "h.sh:1 the rebase conflicts"; echo origin/dev > "$T/st/auto/kept-fix_33-rebase-r1"; K=$($O rev-parse "$rb")
r=$(OPEN="33" fp); rid=$(cat "$T/st/auto/branchfix-fix_33-rebase-r1-"* 2>/dev/null)
tg5=$T/tg5; rm -rf "$tg5"; $G clone -q "$T/origin.git" "$tg5" 2>/dev/null; (cd "$tg5" && $G checkout -q dev && $G branch -q -f "squad/run/$rid" dev)   # the run's target branch, never advanced past the base
mkdir -p "$T/co/runs/$rid"
printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n{"event": "verdict", "verdict": "failed"}\n' "$tg5" "$rid" > "$T/co/runs/$rid/record.jsonl"
r=$(OPEN="33" fp)
[ "$($O rev-parse "refs/heads/$rb")" = "$K" ] && ok "a run that added no head leaves the kept branch as it is" || ko "the kept branch moved to $($O rev-parse "refs/heads/$rb"), want $K"
case "$r" in *"(rebase round"*) ko "a run with no head was pushed as a round: '$r'";; *) ok "a run with no head pushes no round";; esac
# 23) (#1960) a kept round whose run ended unproven still made its head: the last head whose outcome is not fail is pushed to the
#     work branch (lease on the reviewed sha), and branchland's gate and review still decide. A fail head is never pushed.
kround() {   # kround <issue> <outcome>: a kept round dispatched and ended with one head under that outcome; prints the head
  local rb=fix/$1-rebase-r1 rid tg; : > "$T/launch.log"
  push "$rb"; rev "$rb" CHANGES "u.sh:1 the rebase conflicts"; echo origin/dev > "$T/st/auto/kept-fix_$1-rebase-r1"
  OPEN="$1" fp >/dev/null; rid=$(cat "$T/st/auto/branchfix-fix_$1-rebase-r1-"* 2>/dev/null)
  tg=$T/tgk$1; rm -rf "$tg"; $G clone -q "$T/origin.git" "$tg" 2>/dev/null
  (cd "$tg" && $G checkout -q -b "squad/run/$rid" origin/dev && echo k > "k$1" && $G add "k$1" && $G -c user.email=a@b -c user.name=t commit -q -m "the round (Refs #$1)")
  mkdir -p "$T/co/runs/$rid"
  printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n{"event": "outcome", "node": "route.router.1", "outcome": "%s", "workspace": {"name": "worktrees/child", "head": "%s"}}\n{"event": "verdict", "verdict": "completed"}\n' "$tg" "$rid" "$2" "$($G -C "$tg" rev-parse HEAD)" > "$T/co/runs/$rid/record.jsonl"
  $G -C "$tg" rev-parse HEAD; }
UH=$(kround 34 unproven); r=$(OPEN="34" fp)
[ "$($O rev-parse refs/heads/fix/34-rebase-r1)" = "$UH" ] && ok "23 an unproven kept round's head is pushed to the work branch" || ko "23 the unproven round was not pushed: remote $($O rev-parse refs/heads/fix/34-rebase-r1) want $UH: '$r'"
K35=; FH=$(kround 35 fail); K35=$($O rev-parse refs/heads/fix/35-rebase-r1); r=$(OPEN="35" fp)
[ "$($O rev-parse refs/heads/fix/35-rebase-r1)" = "$K35" ] && [ "$K35" != "$FH" ] && ok "23 a fail head is never pushed" || ko "23 a fail head was pushed: '$r'"
# 22) (#1425) two fix rounds with the same blocking finding are the most the pass dispatches under the default cap: the third
#     pass stops the branch as stalled and dispatches no third run.
: > "$T/launch.log"
push gui/11-s; rev gui/11-s CHANGES "d.sh:1 the same finding"; r=$(OPEN="7 8 9 11" fp)
push gui/11-s; rev gui/11-s CHANGES "d.sh:2 the same finding"; r=$(OPEN="7 8 9 11" fp)
push gui/11-s; rev gui/11-s CHANGES "d.sh:3 the same finding"; r=$(OPEN="7 8 9 11" fp)
[ "$(n gui/11-s)" = 2 ] && ok "22 the third pass with the same finding dispatches no third fix run" || ko "22 dispatched $(n gui/11-s) fix runs: '$r'"
case "$r" in *"BRANCHFIX STOP gui/11-s"*"stalled"*) ok "22 the stop names the branch as stalled";; *) ko "22 no stalled stop line: '$r'";; esac
# 24) (#2309) a tip whose fix marker was released (test-janitor-neverstarted.sh proves the janitor releases the marker of
#     a never-started run) is dispatched again by the next pass. Case 2 already proves a standing marker holds its tip.
: > "$T/launch.log"; push gui/40-ns; rev gui/40-ns CHANGES "v.sh:1 fix"
r=$(OPEN="40" fp)
[ "$(n gui/40-ns)" = 1 ] && ok "2309 the pass after the marker's release dispatches that tip again" || ko "2309 after the release: $(n gui/40-ns) dispatch(es): '$r'"
# 25) (#2345) a round that pushes nothing is no fix round for its tip: it drops the once-per-tip marker, the branch stays where
#     it is, the next pass dispatches one round at the same tip, and the stall rule stops the retries. A kept landing round keeps
#     its marker (case 22 proves the kept path).
: > "$T/launch.log"; b25=fix/41-unpushed
push "$b25"; rev "$b25" CHANGES "w.sh:1 the cap is wrong"; t25=$($O rev-parse "$b25")
r=$(OPEN="41" fp); rid25=$(cat "$T/st/auto/branchfix-fix_41-unpushed-"* 2>/dev/null)
tg25=$T/tg25; $G clone -q "$T/origin.git" "$tg25" 2>/dev/null
(cd "$tg25" && $G checkout -q -b "squad/run/$rid25" origin/dev && echo q > q41 && $G add q41 && $G -c user.email=a@b -c user.name=t commit -q -m "a round off the base (Refs #41)")
mkdir -p "$T/co/runs/$rid25"; printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n{"event": "verdict", "verdict": "completed"}\n' "$tg25" "$rid25" > "$T/co/runs/$rid25/record.jsonl"
r=$(OPEN="41" fp)
case "$r" in *"is no fast-forward of the branch tip"*) ok "2345 the unpushed round names both heads";; *) ko "2345 no diagnostic line: '$r'";; esac
[ "$($O rev-parse "$b25")" = "$t25" ] && ok "2345 the work branch stays at its tip" || ko "2345 the branch moved"
# the marker of the unpushed round is gone; the same pass dispatches the next round, so the tip names a new run
[ "$(cat "$T/st/auto/branchfix-fix_41-unpushed-${t25:0:9}" 2>/dev/null)" != "$rid25" ] \
  && ok "2345 the unpushed round drops its once-per-tip marker and the tip is dispatched again" \
  || ko "2345 the marker of $rid25 survived"
# the second round ends unpushed too: the stall rule then stops the retries
rid25b=$(cat "$T/st/auto/branchfix-fix_41-unpushed-${t25:0:9}" 2>/dev/null)
(cd "$tg25" && $G checkout -q -b "squad/run/$rid25b" origin/dev 2>/dev/null && echo q2 > q41b && $G add q41b && $G -c user.email=a@b -c user.name=t commit -q -m "a second round off the base (Refs #41)")
mkdir -p "$T/co/runs/$rid25b"; printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n{"event": "verdict", "verdict": "completed"}\n' "$tg25" "$rid25b" > "$T/co/runs/$rid25b/record.jsonl"
r=$(OPEN="41" fp)
[ "$(n "$b25")" = 2 ] && ok "2345 two rounds are dispatched at the same tip" || ko "2345 dispatched $(n "$b25") times: '$r'"
case "$r" in *"BRANCHFIX STOP fix/41-unpushed"*) ok "2345 the stall rule stops the retries";; *) ko "2345 no stall stop: '$r'";; esac
# 26) (#2346) a round that is gone with no verdict (no line in active-runs.txt, a record that has not moved for the dead window)
#     steps aside: both markers go, say it once, and the same pass dispatches a fresh round. A round whose record just started
#     keeps them, because it may still end with a head.
: > "$T/launch.log"; b26=fix/42-dead
push "$b26"; rev "$b26" CHANGES "x.sh:1 the cap is wrong"; t26=$($O rev-parse "$b26")
r=$(OPEN="42" fp); rid26=$(cat "$T/st/auto/branchfix-fix_42-dead-${t26:0:9}" 2>/dev/null)
mkdir -p "$T/co/runs/$rid26"; printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n' "$T/co" "$rid26" > "$T/co/runs/$rid26/record.jsonl"
r=$(OPEN="42" fp)
[ -e "$T/st/auto/branchfix-push-$rid26" ] && ok "2346 a round whose record just started keeps its push marker" || ko "2346 a fresh round's push marker went: '$r'"
touch -t 202001010000 "$T/co/runs/$rid26/record.jsonl"
r=$(OPEN="42" fp)
case "$r" in *"ended with no verdict"*) ok "2346 a dead round is named";; *) ko "2346 no dead-round line: '$r'";; esac
[ ! -e "$T/st/auto/branchfix-push-$rid26" ] && [ "$(cat "$T/st/auto/branchfix-fix_42-dead-${t26:0:9}" 2>/dev/null)" != "$rid26" ] \
  && ok "2346 a dead round drops both markers and the tip is dispatched again" \
  || ko "2346 a marker of $rid26 survived: $(ls "$T/st/auto" | /usr/bin/grep 42-dead)"
# 27) (#2571) a salvage branch's rebase round: the lander's kept marker carries `salvage <tip> <base>` lines. The round at the
#     marker's last salvage tip gets one fix run with the rebase wording on origin/dev, and its head is pushed under a lease on
#     the reviewed sha. A review of the pushed head gets the ordinary wording. A round that pushes nothing, or dies, drops its
#     dispatch marker as an ordinary branch does, so the stall rule stops the retries at two.
: > "$T/launch.log"; sb=fix/50-salvage-run-a; sk=fix_50-salvage-run-a
push "$sb"; rev "$sb" CHANGES "g.sh:1 the rebase on dev conflicts"; S0=$($O rev-parse "$sb"); touch "$T/st/auto/salvaged-$sk"
printf 'origin/dev\nsalvage %s %s\n' "$S0" "$($O rev-parse dev)" > "$T/st/auto/kept-$sk"
r=$(OPEN="50" fp); q=$(/usr/bin/grep "work branch $sb " "$T/launch.log")
[ "$(n "$sb")" = 1 ] && ok "2571 a salvage round gets one fix run" || ko "2571 the salvage round dispatched $(n "$sb") times: '$r'"
case "$q" in *"REBASE ROUND"*"rebase the branch it has checked out on origin/dev"*) ok "2571 the salvage round's wording names origin/dev";; *) ko "2571 the salvage round's wording: '$q'";; esac
rid=$(cat "$T/st/auto/branchfix-$sk-${S0:0:9}" 2>/dev/null); tg6=$T/tg6; rm -rf "$tg6"; $G clone -q "$T/origin.git" "$tg6" 2>/dev/null
(cd "$tg6" && $G checkout -q -b "squad/run/$rid" origin/dev && echo s > s50 && $G add s50 && $G -c user.email=a@b -c user.name=t commit -q -m "the round (Refs #50)")
SH=$($G -C "$tg6" rev-parse HEAD); mkdir -p "$T/co/runs/$rid"
printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n{"event": "outcome", "node": "route.router.1", "outcome": "unproven", "workspace": {"name": "worktrees/child", "head": "%s"}}\n{"event": "verdict", "verdict": "completed"}\n' "$tg6" "$rid" "$SH" > "$T/co/runs/$rid/record.jsonl"
r=$(OPEN="50" fp)
[ "$($O rev-parse "refs/heads/$sb")" = "$SH" ] && case "$r" in *"(rebase round, run $rid)"*) true;; *) false;; esac \
  && ok "2571 the salvage round's head is pushed under the lease" || ko "2571 the salvage round was not pushed: remote $($O rev-parse "refs/heads/$sb") want $SH: '$r'"
: > "$T/launch.log"; rev "$sb" CHANGES "h.sh:2 a finding on the rebased head"; r=$(OPEN="50" fp); q=$(/usr/bin/grep "work branch $sb " "$T/launch.log")
case "$q" in *"REBASE ROUND"*) ko "2571 the head after the round got the rebase wording: '$q'";; *"commit on top of the branch your worktree has checked out"*) ok "2571 the head after the round gets the ordinary wording";; *) ko "2571 no round after the salvage round: '$q' '$r'";; esac
# the ordinary round after the salvage round reads pass heads only: an unproven head is not the branch's, so it is not pushed
rid=$(cat "$T/st/auto/branchfix-$sk-${SH:0:9}" 2>/dev/null); tg7=$T/tg7; rm -rf "$tg7"; $G clone -q "$T/origin.git" "$tg7" 2>/dev/null
(cd "$tg7" && $G checkout -q dev && $G branch -q -f "squad/run/$rid" dev && $G checkout -q -b uh "$SH" && echo u > u50 && $G add u50 && $G -c user.email=a@b -c user.name=t commit -q -m "unproven (Refs #50)")
UH50=$($G -C "$tg7" rev-parse HEAD); mkdir -p "$T/co/runs/$rid"
printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n{"event": "outcome", "node": "route.router.1", "outcome": "unproven", "workspace": {"name": "worktrees/child", "head": "%s"}}\n{"event": "verdict", "verdict": "completed"}\n' "$tg7" "$rid" "$UH50" > "$T/co/runs/$rid/record.jsonl"
r=$(OPEN="50" fp)
[ "$($O rev-parse "refs/heads/$sb")" = "$SH" ] && ok "2571 an ordinary round after the salvage round pushes no unproven head" || ko "2571 the ordinary round pushed an unproven head: '$r'"
# an empty salvage round drops its dispatch marker, and the stall rule stops the retries at two
: > "$T/launch.log"; sb=fix/51-salvage-run-b; sk=fix_51-salvage-run-b
push "$sb"; rev "$sb" CHANGES "g.sh:1 the rebase on dev conflicts"; S1=$($O rev-parse "$sb"); touch "$T/st/auto/salvaged-$sk"
printf 'origin/dev\nsalvage %s %s\n' "$S1" "$($O rev-parse dev)" > "$T/st/auto/kept-$sk"
eround() { local rid tg; rid=$(cat "$T/st/auto/branchfix-$sk-${S1:0:9}" 2>/dev/null); tg=$T/tge-$rid; rm -rf "$tg"; $G clone -q "$T/origin.git" "$tg" 2>/dev/null
  (cd "$tg" && $G checkout -q dev && $G branch -q -f "squad/run/$rid" dev); mkdir -p "$T/co/runs/$rid"
  printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n{"event": "verdict", "verdict": "completed"}\n' "$tg" "$rid" > "$T/co/runs/$rid/record.jsonl"; printf '%s\n' "$rid"; }
r=$(OPEN="51" fp); e1=$(eround); r=$(OPEN="51" fp)
[ "$(cat "$T/st/auto/branchfix-$sk-${S1:0:9}" 2>/dev/null)" != "$e1" ] && [ "$(n "$sb")" = 2 ] && [ "$($O rev-parse "$sb")" = "$S1" ] \
  && ok "2571 an empty salvage round drops its marker and the tip is dispatched again" || ko "2571 the empty round kept its marker: $(n "$sb") runs: '$r'"
e2=$(eround); r=$(OPEN="51" fp)
[ "$(n "$sb")" = 2 ] && case "$r" in *"BRANCHFIX STOP $sb"*) true;; *) false;; esac && ok "2571 the stall rule stops the empty salvage rounds at two" || ko "2571 no stall stop: $(n "$sb") runs: '$r'"
# a dead salvage round (gone, no verdict, a still record) drops both markers
: > "$T/launch.log"; sb=fix/52-salvage-run-c; sk=fix_52-salvage-run-c
push "$sb"; rev "$sb" CHANGES "g.sh:1 the rebase on dev conflicts"; S2=$($O rev-parse "$sb"); touch "$T/st/auto/salvaged-$sk"
printf 'origin/dev\nsalvage %s %s\n' "$S2" "$($O rev-parse dev)" > "$T/st/auto/kept-$sk"
r=$(OPEN="52" fp); rid=$(cat "$T/st/auto/branchfix-$sk-${S2:0:9}" 2>/dev/null)
mkdir -p "$T/co/runs/$rid"; printf '{"event": "run_start", "target": "%s", "target_branch": "squad/run/%s"}\n' "$T/co" "$rid" > "$T/co/runs/$rid/record.jsonl"; touch -t 202001010000 "$T/co/runs/$rid/record.jsonl"
r=$(OPEN="52" fp)
[ ! -e "$T/st/auto/branchfix-push-$rid" ] && [ "$(cat "$T/st/auto/branchfix-$sk-${S2:0:9}" 2>/dev/null)" != "$rid" ] && ok "2571 a dead salvage round drops both markers" || ko "2571 a dead salvage round kept a marker: '$r'"
exit $fail
