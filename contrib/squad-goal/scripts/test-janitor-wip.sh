#!/bin/bash
# seconds: 1.40
# Guard for #1662: a run the janitor takes over (rule T) is snapshotted before its teardown. Every git tree of the run
# (its own tree, ws/*, worktrees/*) writes its tracked changes and untracked files into a commit reached only by
# refs/wip/<run-id>/<label>, one cycle.log line per tree (`WIP saved` or `WIP clean`). The real index, the branch tip and
# the working tree do not move. Offline: fixture git repos, a stub gh and board, a temp state dir.
# usage: bash test-janitor-wip.sh [janitor.py]
export SG_TAKEOVER_SECONDS=1800
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/janitor.py}; T=$(mktemp -d)
trap 'kill "$sp" 2>/dev/null; rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs/run-w"
cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; [ -f "$(dirname "$src")/wip_snapshot.py" ] && cp "$(dirname "$src")/wip_snapshot.py" "$T/s/wip_snapshot.py"
printf '#!/bin/bash\n' > "$T/s/board.sh"; printf '#!/bin/bash\n' > "$T/bin/gh"; printf '#!/bin/bash\n' > "$T/notify"; chmod +x "$T/bin/gh" "$T/s/board.sh"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
# A child tree with one commit: tracked.txt and a .gitignore that hides *.log. Then a modified tracked file, a new untracked
# file and an ignored log are left in it, as a run leaves them.
mktree() { mkdir -p "$1" && git -C "$1" init -q && printf 'ignored.log\n' > "$1/.gitignore" && printf 'v1\n' > "$1/tracked.txt" \
  && git -C "$1" add -A && git -C "$1" commit -q -m base; }
RUN="$T/co/runs/run-w"; C1="$RUN/ws/child-1"; C2="$RUN/ws/child-2"; C3="$RUN/ws/child-3"
mktree "$C1"; printf 'v2\n' > "$C1/tracked.txt"; printf 'new work\n' > "$C1/new.txt"; printf 'noise\n' > "$C1/ignored.log"
mktree "$C2"; printf 'only untracked\n' > "$C2/fresh.txt"
mktree "$C3"
printf '{"event": "run_start", "run_id": "run-w", "ts": 1}\n' > "$RUN/record.jsonl"
echo "run-w" > "$T/st/active-runs.txt"
# A process inside the child tree: rule T ends it with the run.
( cd "$C1" && exec sleep 300 ) & sp=$!; sleep 0.3
# Invariants: the real index bytes, the branch tip and the working-tree file, before and after every snapshot.
idx() { cat "$(git -C "$1" rev-parse --absolute-git-dir)/index" | shasum -a 256 | cut -d' ' -f1; }
before_idx=$(idx "$C1"); before_head=$(git -C "$C1" rev-parse HEAD)
pass() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_SQUAD="$repo" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 SG_BASE=main SG_HUB_DIR="$T/st" \
  JANITOR_NOTIFY="$T/notify" PATH="$T/bin:$PATH" "$@" python3 -I "$T/s/janitor.py" >"$T/out" 2>&1; }

# Rule T in dry mode: it names the takeover, writes no ref and no WIP line, and kills nothing.
pass env JANITOR_DRY=1
git -C "$C1" show-ref -q --verify "refs/wip/run-w/ws/child-1" && ko "a dry pass wrote a WIP ref" || ok "a dry pass writes no WIP ref"
[ -s "$T/st/cycle.log" ] && grep -q 'WIP ' "$T/st/cycle.log" && ko "a dry pass wrote a WIP line" || ok "a dry pass writes no WIP line"
kill -0 "$sp" 2>/dev/null && ok "a dry pass kills nothing" || ko "a dry pass killed the child's process"

pass env
ref=refs/wip/run-w/ws/child-1
git -C "$C1" rev-parse --verify -q "$ref" >/dev/null && ok "rule T saves the child tree under refs/wip/<run-id>/<tree>" || ko "no WIP ref after the takeover: $(cat "$T/out")"
[ "$(git -C "$C1" show "$ref:tracked.txt" 2>/dev/null)" = "v2" ] && ok "the modified tracked file is recoverable from the ref" || ko "the tracked change is not in the ref"
[ "$(git -C "$C1" show "$ref:new.txt" 2>/dev/null)" = "new work" ] && ok "the untracked file is recoverable from the ref" || ko "the untracked file is not in the ref"
git -C "$C1" ls-tree -r --name-only "$ref" | grep -qx 'ignored.log' && ko ".gitignore was not honoured: the ignored log is in the ref" || ok "the ignored log is not in the ref (.gitignore honoured)"
grep -q "WIP saved ws/child-1 $ref" "$T/st/cycle.log" && ok "cycle.log names the tree and its ref" || ko "no WIP saved line for child-1: $(cat "$T/st/cycle.log" 2>/dev/null)"
st=$(ps -o state= -p "$sp" 2>/dev/null | tr -d ' '); case "$st" in Z*|"") ok "the takeover still ends the child's process";; *) ko "the child's process survived the takeover (state $st)";; esac
[ "$(git -C "$C1" rev-parse HEAD)" = "$before_head" ] && ok "the branch tip did not move" || ko "the branch tip moved"
[ "$(idx "$C1")" = "$before_idx" ] && ok "the real index is byte-identical" || ko "the real index changed"
[ "$(cat "$C1/tracked.txt")" = "v2" ] && [ -e "$C1/new.txt" ] && ok "the working tree still holds the run's files" || ko "the working tree moved"
grep -q "WIP clean ws/child-3" "$T/st/cycle.log" && ok "a clean tree writes WIP clean" || ko "no WIP clean line for child-3"
git -C "$C3" show-ref -q --verify "refs/wip/run-w/ws/child-3" && ko "a clean tree got a ref" || ok "a clean tree gets no ref"
git -C "$C2" show "refs/wip/run-w/ws/child-2:fresh.txt" 2>/dev/null | grep -qx 'only untracked' && ok "a tree with only untracked files still gets a ref" || ko "an untracked-only tree got no ref"

# The helper alone, on a run whose only tree is the own tree: the same contract, without the janitor.
OWN="$T/own"; mktree "$OWN"; printf 'staged then edited\n' > "$OWN/tracked.txt"; git -C "$OWN" add tracked.txt; printf 'edited again\n' > "$OWN/tracked.txt"
own_idx=$(idx "$OWN"); own_head=$(git -C "$OWN" rev-parse HEAD)
python3 -I "$d/wip_snapshot.py" "$OWN" own-run >"$T/own.out" 2>&1 && ok "the helper exits 0 when every tree is saved" || ko "the helper failed: $(cat "$T/own.out")"
grep -q "^WIP saved own refs/wip/own-run/own$" "$T/own.out" && ok "the helper names the own tree and its ref" || ko "own tree line: $(cat "$T/own.out")"
[ "$(git -C "$OWN" show "refs/wip/own-run/own:tracked.txt")" = "edited again" ] && ok "the helper keeps the newest tracked content" || ko "the helper lost the newest content"
[ "$(idx "$OWN")" = "$own_idx" ] && [ "$(git -C "$OWN" rev-parse HEAD)" = "$own_head" ] && ok "the helper moves neither the index nor the branch" || ko "the helper moved the index or the branch"
[ -n "$(git -C "$OWN" diff --cached --name-only)" ] && ok "the staged change stays staged in the real index" || ko "the real index lost its staged change"

exit $fail
