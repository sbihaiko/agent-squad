#!/bin/bash
# Regression test for #1836: the branch a fork-only child commits on has ONE source, $SG_FORK_BRANCH (%n = the issue) --
# the setting forkclose.sh substitutes into the ref it merges. The request builder names it and the land resolves it, so
# moving the setting in one place moves both; a fork-only dispatch that names another branch, or whose setting cannot name
# a landing branch at all, is refused by mkreq.sh before launch.py writes a run folder. #1831 is the cost: a child that
# read `fix/<N>-<short-slug>` (the MesenAI habit) delivered its commit on a branch the close pass never reads, and the
# card sat at Code Complete with nobody waiting for it.
# Offline: stub gh/board/launch, a throwaway fork repo, throwaway HOME and state. usage: bash test-forkbranch-source.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# Hermetic (#1410): every inherited SG_* is cleared, so the live loop's fork, repo and profile never reach an assertion.
unset $(compgen -e | /usr/bin/grep '^SG_') 2>/dev/null || true
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR="$T/caps"   # the request builders read the machine-wide ceilings from the checkout under test, and the leases stay in the sandbox (#21)
G=/usr/bin/grep; S=$T/s
mkdir -p "$S" "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/bin" "$T/home" "$T/co/runs/.hub" "$T/fork"
cp "$d"/{autobug.sh,autofrontier.sh,mkreq.sh,forkonly.sh,forkclose.sh,fork-gate.sh,serial.sh,single.sh,cluster.py,models.json,req-checklist.txt,req-checklist-fork.txt} "$S/"; cp "$d"/ghread.py "$d"/ghthrottle.py "$S/" 2>/dev/null
: > "$S/launch.py"; printf '#!/bin/bash\necho READY\n' > "$S/stack.sh"; printf 'exit 0\n' > "$S/shell-suites.sh"
printf '#!/bin/bash\necho "pass-1"; exit 0\n' > "$S/test.sh"; chmod +x "$S"/*.sh
printf 'RULE: open the PR with origin/main, run prepush.sh\n' > "$T/pre.txt"
printf '#!/bin/bash\necho "$@" >> "$T/st/moves"\n' > "$T/board.sh"; chmod +x "$T/board.sh"
: > "$T/st/active-runs.txt"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
# issue 1: open, labeled gui + fork-only. Nothing else exists in this world; the PR pages are empty.
a="$*"; n=$(printf '%s' "$a" | /usr/bin/grep -oE 'issues/[0-9]+' | head -1 | tr -dc 0-9)
case "$a" in
  *"pulls?state=open"*) ;;
  *"pulls?state=closed"*) ;;
  *"issues?state=open"*) printf 'wave:01\x1f1\x1fgui\n';;
  *"issues/$n -q '{number"*) printf '{"number":%s,"title":"t","body":"x","created_at":"2026-01-01T00:00:00Z"}\n' "$n";;
  *"issues/$n -q .state"*) echo open;;
  *"issues/$n -q .body"*) ;;
  *"issues/$n -q"*) printf '"t%s"\n' "$n";;
  *"issues/$n"*) printf '{"number":%s,"state":"open","title":"t","labels":[{"name":"gui"},{"name":"squad-only"}],"body":"plain body"}\n' "$n";;
esac
GH
chmod +x "$T/bin/gh"
export PATH="$T/bin:$PATH" HOME=$T/home
# One env.sh for the whole scene: the request builders and the land read the SAME setting from it, which is the point.
env_of() { printf "export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_STATE=%s SG_CHECKOUT=%s SG_BASE=main SG_CODING_RULES=rules SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH='%s' SG_FORK_TEST_CMD=true SG_FORK_PY_CMD=true SG_FRONTIER_LABEL=gui SG_FRONTIER_PREAMBLE=%s BOARD=%s\n" "$T/st" "$T/co" "$T/fork" "$1" "$T/pre.txt" "$T/board.sh" > "$S/env.sh"; }
g() { /usr/bin/git -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
forkrepo() { rm -rf "$T/fork"; mkdir -p "$T/fork"; g init -q -b local/next
  echo 0 > "$T/fork/f"; g add f; g commit -q -m init
  g checkout -q -b "$1" local/next; echo one > "$T/fork/one.py"; g add .; g commit -q -m "work 1"; g checkout -q local/next; }
reqtext() { cat "$T/st/req"/*.txt 2>/dev/null; }
clearreq() { rm -f "$T/st/req"/*.txt; }
ask() { bash "$S/mkreq.sh" "$1" "$T/$1.body" 2>"$T/$1.err" >"$T/$1.out"; }   # exit status: refused or not
# A. The setting is `issue/%n`, NOT the tooling's own habit: the request must name the branch the land reads, and the land
# must land exactly it. Nothing here reads `gui/`, so a name that still says gui/1 came from a second source.
env_of 'issue/%n'; forkrepo issue/1
printf '{"items":[{"n":1,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
bash "$S/autofrontier.sh" >/dev/null 2>&1
r=$(reqtext)
[ -n "$r" ] || ko "the fork-only request was never written"
printf '%s' "$r" | $G -qF 'issue/1' && ok "the request names issue/1: the branch the land resolves for #1" || ko "the request never names issue/1 (the land's branch for #1): $(printf '%s' "$r" | $G -o 'one local commit on [^) ]*')"
printf '%s' "$r" | $G -qF 'issue/<N>' && ok "the request carries the land's branch template, preamble and checklist alike" || ko "the request never names issue/<N>"
printf '%s' "$r" | $G -qF 'gui/' && ko "the request still names a gui/ branch: the name has a second source" || ok "no gui/ anywhere in the request"
bash "$S/forkclose.sh" >/dev/null 2>&1
[ -e "$T/fork/one.py" ] && ok "the land merged issue/1: the name in the request is the one it resolves" || ko "forkclose did not land issue/1 (the request's name is not the land's)"
$G -q 'Merge issue/1' <(g log --format=%s -3) && ok "the merge commit names issue/1" || ko "no merge commit naming issue/1: $(g log --format=%s -3 | tr '\n' ';')"
# B. A dispatch that names a branch the land would not read is refused, before any run folder exists. B1 is the #1831 leak
# itself: the MesenAI habit reaching a fork-only request. B2 is the same defect with the setting moved the other way (a
# request still hardcoding gui/<N> under a land that reads issue/<N>).
env_of 'gui/%n'
printf 'IMPORTANT: your first and only action is X\nFORK-ONLY: all\nCHILD — GitHub issue(s) 1 (FORK-ONLY bug; one root cause, one local commit on fix/<N>-short-slug):\n' > "$T/b1.body"
clearreq
ask b1 && ko "a fork-only request naming fix/<N>-short-slug was accepted" || ok "a fork-only request naming fix/<N>-short-slug is refused"
$G -qF 'fix/<N>' "$T/b1.err" && ok "the refusal names the branch the request carried" || ko "refusal hides it: $(cat "$T/b1.err")"
$G -qF 'gui/<N>' "$T/b1.err" && ok "the refusal names the branch the land reads" || ko "refusal hides the land's branch: $(cat "$T/b1.err")"
[ -f "$T/st/req/b1.txt" ] && ko "the refused dispatch wrote a request (a run could start)" || ok "the refused dispatch wrote no request: nothing can launch"
env_of 'issue/%n'
printf 'IMPORTANT: your first and only action is X\nFORK-ONLY: all\nCHILD — GitHub issue(s) 1 (FORK-ONLY bug; one root cause, one local commit on gui/1):\n' > "$T/b2.body"
clearreq
ask b2 && ko "a request naming gui/1 under a land that reads issue/%n was accepted" || ok "a request naming gui/1 under issue/%n is refused"
$G -qF 'issue/1' "$T/b2.err" && ok "the refusal names the branch the land reads for #1" || ko "refusal hides the land's branch: $(cat "$T/b2.err")"
# C. A dispatch that cannot name a landing branch is refused with the reason.
env_of 'gui/fork'
printf 'IMPORTANT: your first and only action is X\nFORK-ONLY: all\nCHILD — GitHub issue(s) 1 (FORK-ONLY bug; one local commit):\n' > "$T/c1.body"
clearreq
ask c1 && ko "a setting that names no branch per issue was accepted" || ok "a setting without %n is refused"
$G -qF '%n' "$T/c1.err" && ok "the refusal names the missing %n" || ko "refusal text: $(cat "$T/c1.err")"
[ -f "$T/st/req/c1.txt" ] && ko "the refused dispatch wrote a request" || ok "no request written for c1"
env_of 'gui %n'
printf 'IMPORTANT: your first and only action is X\nFORK-ONLY: all\nCHILD — GitHub issue(s) 1 (FORK-ONLY bug; one local commit):\n' > "$T/c2.body"
clearreq
ask c2 && ko "a setting git cannot turn into a branch was accepted" || ok "a setting yielding an invalid ref is refused"
$G -q 'not a valid branch' "$T/c2.err" && ok "the refusal says the ref is invalid" || ko "refusal text: $(cat "$T/c2.err")"
# D. Control: a well-formed fork-only body under the default setting still builds, with the fork checklist.
env_of 'gui/%n'
printf 'IMPORTANT: your first and only action is X\nFORK-ONLY: all\nCHILD — GitHub issue(s) 1 (FORK-ONLY bug; one local commit on gui/1):\n' > "$T/d1.body"
clearreq
ask d1 && ok "a well-formed fork-only request builds" || ko "control refused: $(cat "$T/d1.err")"
f=$T/st/req/d1.txt
[ -f "$f" ] && ok "the control request exists" || ko "no control request"
$G -q 'cd <fork worktree>' "$f" 2>/dev/null && ok "the control carries the fork DONE CHECK" || ko "control lacks the fork DONE CHECK"
$G -qE 'origin/main|open the PR|prepush.sh' "$f" 2>/dev/null && ko "control carries PR text" || ok "control carries no PR text"
rm -rf "$T"; exit $fail
