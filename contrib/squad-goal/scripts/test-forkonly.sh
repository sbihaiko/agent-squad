#!/bin/bash
# Regression test for the squad-only request rule (#1411): the `squad-only` label (or a "Squad-only work" body line) makes every
# request builder carry ONLY the fork preamble, and forkrescue.py re-points a stray fix/<N>-x commit to gui/<N>.
# Offline: stub gh/board/launch over throwaway repos, HOME and state. usage: bash test-forkonly.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR="$T/caps"   # the dispatch scripts read the machine-wide ceilings from the checkout under test, and the leases stay in the sandbox (#21)
G=/usr/bin/grep; mkdir -p "$T/bin"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
# issue 1: squad-only by label; 2: squad-only by the plain body marker; 3: plain MesenAI issue; 10: the OLD label spelling
# 4-6: the marker bulleted, bolded, quoted; 7/9: the phrase only inside a sentence; 8: the marker on a later line
a="$*"; n=$(printf '%s' "$a" | grep -oE 'issues/[0-9]+' | head -1 | tr -dc 0-9)
lab() { case $1 in 1) echo '{"name":"bug"},{"name":"squad-only"}';; 10) echo '{"name":"bug"},{"name":"fork-only"}';; *) echo '{"name":"bug"}';; esac; }
body() { case $1 in
  2) printf 'Fork-only work: no PR.\\nfix it';;
  4) printf 'Fix it.\\n- Fork-only work: no PR.';;
  5) printf 'Fix it.\\n**Fork-only work**: no PR.';;
  6) printf 'Fix it.\\n> Fork-only work: no PR.';;
  7) printf 'Decided this is Fork-only work for the fork, so no PR for now.';;
  8) printf 'Fix it.\\nFork-only work: no PR.';;
  9) printf 'Fix it.\\n- the issue is Fork-only work per the decision below';;
  *) printf 'plain body';; esac; }
case "$a" in
  *"pulls?state=open"*) ;;
  *"issues?state=open"*) echo "$GH_LIST";;
  *"issues/$n -q '{number"*) printf '{"number":%s,"title":"t%s","body":"x","created_at":"2026-01-01T00:00:00Z"}' "$n" "$n";;
  *"issues/$n -q"*) printf '"t%s"\n' "$n"; body "$n" | sed 's/\\n/\n/g';;
  *"issues/$n"*) printf '{"number":%s,"state":"open","title":"t%s","labels":[%s],"body":"%s"}' "$n" "$n" "$(lab "$n")" "$(body "$n")";;
esac
GH
chmod +x "$T/bin/gh"; printf '#!/bin/bash\necho "$@" >> "$SG_STATE/moves"\n' > "$T/board.sh"; chmod +x "$T/board.sh"
# scene <name>: scripts dir + state + home for one case
scene() { S=$T/$1/scripts; mkdir -p "$S" "$T/$1/st/req" "$T/$1/st/cq" "$T/$1/st/auto" "$T/$1/home" "$T/$1/co"
  cp "$d"/{autobug.sh,autofrontier.sh,mkreq.sh,single.sh,forkonly.sh,forkrescue.py,cluster.py,models.json,req-checklist.txt,req-checklist-fork.txt} "$S/" 2>/dev/null; cp "$d"/ghread.py "$d"/ghthrottle.py "$S/" 2>/dev/null
  : > "$S/launch.py"; printf '#!/bin/bash\necho "READY"\n' > "$S/stack.sh"; chmod +x "$S/stack.sh"; : > "$T/$1/st/active-runs.txt"
  echo 'RULE: open the PR with origin/main, run prepush.sh' > "$T/$1/pre.txt"
  cat > "$S/env.sh" <<E
SG_REPO=o/r; SG_OWNER=o; SG_PROJECT=1; SG_STATE=$T/$1/st; SG_CHECKOUT=$T/$1/co; SG_BASE=main; SG_CODING_RULES=rules
SG_FORK_BASE=local/next; SG_FORK_BRANCH='gui/%n'; SG_FRONTIER_LABEL=gui; SG_FRONTIER_PREAMBLE=$T/$1/pre.txt; BOARD=$T/board.sh
E
  export HOME=$T/$1/home SG_STATE=$T/$1/st; }
req() { cat "$SG_STATE"/req/*.txt 2>/dev/null; }
forkfree() { # <label> : the request carries the fork preamble and none of the MesenAI one
  local r; r=$(req); [ -n "$r" ] || { ko "$1: no request written"; return; }
  for w in "local/next" "gui/" "never push" "never open a PR"; do printf '%s' "$r" | $G -qF -- "$w" || ko "$1: fork preamble lacks '$w'"; done
  for w in "origin/main" "open the PR" "prepush.sh" "fix/<N>" "RULE: open the PR"; do printf '%s' "$r" | $G -qF -- "$w" && ko "$1: fork request still carries '$w'"; done
  printf '%s' "$r" | $G -q "pass" >/dev/null; ok "$1: checked"; }
export PATH="$T/bin:$PATH"
# 1-2: autobug, fork-only by label, then by body line
for c in 1 2; do scene bug$c; GH_LIST=$(printf '%s\x1fbug' $c) bash "$S/autobug.sh" >/dev/null 2>&1; forkfree "autobug fork-only #$c"; done
# 3: a plain issue keeps the MesenAI preamble
scene bug3; GH_LIST=$(printf '3\x1fbug') bash "$S/autobug.sh" >/dev/null 2>&1; r=$(req)
for w in "origin/main" "open the PR" "prepush.sh"; do printf '%s' "$r" | $G -qF -- "$w" && ok "autobug plain keeps '$w'" || ko "autobug plain lost '$w'"; done
# 4-5: autofrontier
for c in 1 2; do scene fr$c; GH_LIST=$(printf 'wave:01\x1f%s\x1fgui' $c) bash "$S/autofrontier.sh" >/dev/null 2>&1; forkfree "autofrontier fork-only #$c"; done
scene fr3; GH_LIST=$(printf 'wave:01\x1f3\x1fgui') bash "$S/autofrontier.sh" >/dev/null 2>&1; r=$(req)
for w in "origin/main" "prepush.sh" "RULE: open the PR"; do printf '%s' "$r" | $G -qF -- "$w" && ok "autofrontier plain keeps '$w'" || ko "autofrontier plain lost '$w'"; done
# 6: mkreq: the marker, and a hand-written body with the Fork-only work line, get the fork DONE CHECK and checklist
scene mk; printf 'IMPORTANT: your first and only action is X\nFORK-ONLY: all\nbody\n' > "$T/mk/b1"; bash "$S/mkreq.sh" m1 "$T/mk/b1" >/dev/null 2>&1
printf 'IMPORTANT: your first and only action is X\nFork-only work: no PR.\nbody\n' > "$T/mk/b2"; bash "$S/mkreq.sh" m2 "$T/mk/b2" >/dev/null 2>&1
printf 'IMPORTANT: your first and only action is X\nbody\n' > "$T/mk/b3"; bash "$S/mkreq.sh" m3 "$T/mk/b3" >/dev/null 2>&1
for m in m1 m2; do f=$SG_STATE/req/$m.txt
  $G -qE 'origin/main|open the PR|prepush.sh|gh api --method POST repos/<owner>/<repo>/pulls' "$f" && ko "mkreq $m: PR text in a fork-only request" || ok "mkreq $m: no PR text"
  $G -q 'cd <fork worktree>' "$f" && ok "mkreq $m: fork DONE CHECK" || ko "mkreq $m: no fork DONE CHECK"; done
$G -q 'gh api --method POST' "$SG_STATE/req/m3.txt" && ok "mkreq plain keeps the PR checklist" || ko "mkreq plain lost the PR checklist"
# 7: forkonly.sh rule: the label, or a marker LINE (plain / bulleted / bolded / quoted), never the phrase inside a sentence
# 10 is the old label spelling: it still reads, so an issue filed before the 2026-10-12 rename keeps its route (#1411).
for p in "1:yes" "2:yes" "3:no" "4:yes" "5:yes" "6:yes" "7:no" "8:yes" "9:no" "10:yes"; do (. "$S/forkonly.sh"; sg_fork_only "${p%%:*}") && v=yes || v=no; [ "$v" = "${p#*:}" ] && ok "rule #${p%%:*} = $v" || ko "rule #${p%%:*} = $v"; done
# 8: forkrescue.py re-points a stray fix/<N>-x commit to gui/<N>, moves the card, leaves a note.
# The branch decides, not only the brand (#1831): #1 is fork-only by label and carries a fork path, #3 carries a MesenAI path.
# Both cards are parked at the landing stage in the hub's board cache, which is what the rescue reads (no gh call).
scene rs; F=$T/rs/fork; /usr/bin/git init -q -b local/next "$F"; gi() { (cd "$F" && /usr/bin/git -c user.email=a@b -c user.name=n "$@"); }
gi commit -q --allow-empty -m base; mkdir -p "$F/contrib/squad-goal/scripts" "$F/UI"
gi checkout -q -b fix/1-x; echo tooling > "$F/contrib/squad-goal/scripts/t.sh"; gi add -A; gi commit -q -m work; gi checkout -q local/next
gi checkout -q -b fix/3-y; echo game > "$F/UI/Foo.cs"; gi add -A; gi commit -q -m other; gi checkout -q local/next
mkdir -p "$T/rs/co/runs/r1" "$T/rs/co/runs/r3" "$T/rs/co/runs/.hub"
printf '{"items":[{"n":1,"st":"Code Complete","s":"open"},{"n":3,"st":"Code Complete","s":"open"}]}' > "$T/rs/co/runs/.hub/hub-board.json"
echo r1 >> "$T/rs/st/active-runs.txt"; echo r3 >> "$T/rs/st/active-runs.txt"
for p in "r1:1" "r3:3"; do r=${p%%:*}; i=${p#*:}; { echo '{"event": "spawn_accepted", "child": "c", "definition": "GitHub issue #'$i'"}'; echo '{"event": "outcome", "node": "c", "outcome": "needs-human", "reasons": ["where does the PR go?"]}'; echo '{"event": "verdict"}'; } > "$T/rs/co/runs/$r/record.jsonl"; done
export SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_CHECKOUT=$T/rs/co SG_FORK_DIR=$F SG_FORK_BASE=local/next SG_FORK_BRANCH='gui/%n' SG_FORKONLY="$T/rs/scripts/forkonly.sh" SG_BOARD="$T/board.sh"
(cd "$T/rs/co" && python3 "$T/rs/scripts/forkrescue.py" >"$T/rs/out" 2>&1)
[ "$(/usr/bin/git -C "$F" rev-parse gui/1 2>/dev/null)" = "$(/usr/bin/git -C "$F" rev-parse fix/1-x)" ] && ok "rescue: gui/1 points at the fix/1-x commit" || ko "rescue: gui/1 missing or elsewhere ($(cat "$T/rs/out"))"
$G -q 'Code Complete 1$' "$T/rs/st/moves" 2>/dev/null && ok "rescue: card -> Code Complete" || ko "rescue: card not moved"
ls "$T/rs/st/cq"/*#1-forkrescue* >/dev/null 2>&1 && ok "rescue: note queued on the issue" || ko "rescue: no note"
/usr/bin/git -C "$F" rev-parse -q --verify gui/3 >/dev/null && ko "rescue: touched a non-fork-only issue (#3)" || ok "rescue: non-fork-only issue left alone"
# 9: forkclose.sh's conflict and red-suite builders mark their bodies fork-only, so mkreq.sh gives them the fork checklist
[ "$($G -c "^      echo \"FORK-ONLY: all\"\|echo \"FORK-ONLY: all\"" "$d/forkclose.sh")" -ge 2 ] && ok "forkclose builders carry the FORK-ONLY marker" || ko "forkclose builders lack the FORK-ONLY marker"
rm -rf "$T"; exit $fail
