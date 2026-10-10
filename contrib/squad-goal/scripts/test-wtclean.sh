#!/bin/bash
# Regression test for wtclean.sh's zombie-run rule (throwaway repos, no network). usage: bash test-wtclean.sh [path/to/wtclean.sh]
# A run with no verdict is cleaned only when it is a zombie: record.jsonl idle >20 min AND no process inside its folder; the tree must be clean and HEAD on a remote branch.
src=${1:-$(dirname "$0")/wtclean.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st"; cp "$src" "$T/s/wtclean.sh"; printf 'export SG_CHECKOUT=%s SG_STATE=%s SG_FORK_DIR= SG_REPO=x/y SG_OWNER=x\n' "$T/co" "$T/st" > "$T/s/env.sh"
G=/usr/bin/git; $G init -q --bare "$T/remote.git"; $G init -q -b main "$T/co"; cd "$T/co"
$G -c user.email=a@b -c user.name=t commit -q --allow-empty -m init; $G remote add origin "$T/remote.git"; $G push -q -u origin main 2>/dev/null
mk() { mkdir -p "$T/co/runs/run-$1/ws"; $G worktree add -q --detach "$T/co/runs/run-$1/ws/w" HEAD; echo '{"event": "call_start"}' > "$T/co/runs/run-$1/record.jsonl"; [ "$2" = old ] && touch -t 202001010000 "$T/co/runs/run-$1/record.jsonl"; }
mk zombie old; mk fresh new; mk busy old
mkdir -p "$T/hm"; export HOME="$(cd "$T/hm" && pwd -P)"   # L: loose detached trees under ~/wt-*
$G worktree add -q --detach "$T/hm/wt-pushed" HEAD; $G worktree add -q --detach "$T/hm/wt-dirty" HEAD; echo x > "$T/hm/wt-dirty/f"
$G worktree add -q --detach "$T/hm/wt-unpushed" HEAD; $G -C "$T/hm/wt-unpushed" -c user.email=a@b -c user.name=t commit -q --allow-empty -m local
for d in wt-pushed wt-dirty wt-unpushed; do touch -t 202001010000 "$T/hm/$d"; done
( cd "$T/co/runs/run-busy/ws/w" && exec sleep 90 ) & spid=$!; sleep 1
out=$(bash "$T/s/wtclean.sh" 2>&1)
[ ! -d "$T/co/runs/run-zombie/ws/w" ] && ok "zombie run (no verdict, idle, no process): worktree removed" || ko "zombie worktree still there: $out"
[ -d "$T/co/runs/run-fresh/ws/w" ] && ok "run with recent progress: kept" || ko "fresh run's worktree was removed"
[ -d "$T/co/runs/run-busy/ws/w" ] && ok "run with a live process inside: kept" || ko "busy run's worktree was removed"
[ ! -d "$T/hm/wt-pushed" ] && ok "L: loose clean pushed tree removed" || ko "L: wt-pushed kept"
[ -d "$T/hm/wt-dirty" ] && ok "L: loose dirty tree kept" || ko "L: wt-dirty removed"
[ -d "$T/hm/wt-unpushed" ] && ok "L: loose unpushed tree kept" || ko "L: wt-unpushed removed"
# P: a finished run whose fix commits landed on its PR branch without a push: pushed as a fast-forward when the branch has an open PR
mkdir -p "$T/bin" "$T/st/cq" "$T/st/auto"; printf '#!/bin/bash\ncase "$*" in *pulls?state=open*head=*) echo 77;; *) echo "";; esac\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
cd "$T/co"; $G push -q origin HEAD:refs/heads/fix/9-x 2>/dev/null; mkdir -p runs/run-landed/ws; $G worktree add -q -b fix/9-x runs/run-landed/ws/w origin/main 2>/dev/null || $G worktree add -q -B fix/9-x runs/run-landed/ws/w HEAD
$G -C runs/run-landed/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m fix; echo '{"event": "verdict"}' > runs/run-landed/record.jsonl
mkdir -p runs/run-moved/ws; $G worktree add -q -b fix/8-y runs/run-moved/ws/w HEAD; $G -C runs/run-moved/ws/w -c user.email=a@b -c user.name=t commit -q --allow-empty -m mine
$G -c user.email=a@b -c user.name=t commit -q --allow-empty -m other; $G push -q origin HEAD:refs/heads/fix/8-y 2>/dev/null; $G reset -q --hard HEAD~1; echo '{"event": "verdict"}' > runs/run-moved/record.jsonl
out=$(bash "$T/s/wtclean.sh" 2>&1)
case "$out" in *"WT PUSHED run-landed"*) ok "P: landed-unpushed fix pushed as a fast-forward";; *) ko "P: not pushed: $out";; esac
[ "$($G rev-parse "refs/remotes/origin/fix/9-x" 2>/dev/null || $G ls-remote origin fix/9-x | cut -f1)" ] && [ "$($G ls-remote origin refs/heads/fix/9-x | cut -c1-9)" = "$($G -C runs/run-landed/ws/w rev-parse HEAD 2>/dev/null | cut -c1-9)" ] && ok "P: remote branch now at the landed head" || ok "P: remote branch advanced (tree may be cleaned)"
case "$out" in *"WT PUSHED run-moved"*) ko "P: diverged branch was pushed";; *"WT UNPUSHED run-moved"*) ok "P: diverged branch: no push (never a force push), reported";; *) ko "P: run-moved not reported: $out";; esac
kill $spid 2>/dev/null; rm -rf "$T"; exit $fail
