#!/bin/bash
# Regression test for #1823: the fork checkout is SHALLOW, and a gui/* branch that came from the pre-migration lineage
# carries a commit whose parent this clone never received (a shallow boundary). Merging it drags that commit into
# $SG_FORK_BASE, and the push of the base is rejected by the remote (index-pack: "did not receive expected object <parent>").
# The land path must refuse that branch, NAMING it, and leave the base where it is -- instead of producing a tip that
# cannot be pushed and leaving $SG_FORK_BASE ahead of origin/$SG_FORK_BASE silently. When the base is already ahead of
# origin/$SG_FORK_BASE and carries such a commit, the pass must say so (FORKCLOSE PUSH-BLOCKED) instead of staying quiet.
# Stub gh/test.sh/mkreq.sh, throwaway shallow fork repo with a bare published remote; one fresh fixture per scenario.
# usage: bash test-forkclose-shallow.sh [path/to/forkclose.sh]
src=${1:-$(dirname "$0")/forkclose.sh}; here=$(cd "$(dirname "$src")" && pwd); own=$(cd "$(dirname "$0")" && pwd); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_E2E_DIR SG_E2E_BUILD_DIR SG_FORKONLY
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=a@b GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=a@b
# mkfix <dir>: the published lineage A -> B -> C lives in <dir>/origin.git (bare); the pre-migration lineage A -> Y -> L1
# stays in <dir>/up. <dir>/fork is the shallow fork: local/next = C (complete), and `legacy` = L1 fetched --depth=1, so L1
# is a boundary whose parent Y is absent from the machine. gui/1 is C + the legacy lineage + one commit, which is what the
# conflict pass (base merged into the branch) leaves behind for a branch born before the migration.
mkfix() { T=$1; export FCT=$T PATH="$T/bin:$PATH"; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/st/tests" "$T/st/req" "$T/bin" "$T/co/runs/.hub" "$T/up" "$T/fork"
  cp "$src" "$T/s/forkclose.sh"; cp "$(dirname "$src")/forkonly.sh" "$T/s/"; cp "${here}/gate-suites.sh" "$T/s/" 2>/dev/null || cp "$own/gate-suites.sh" "$T/s/"; cp $(dirname "$src")/ghread.py "$T/s/"; cp $(dirname "$src")/ghthrottle.py "$T/s/"
  printf '#!/bin/bash\neval "$1"\n' > "$T/s/fork-gate.sh"
  cat > "$T/s/test.sh" <<'J'
#!/bin/bash
id=${TEST_ID:-noid}
echo "green (fork suite)" > "$SG_STATE/tests/$id.log"   # the suite the landing pays before the close
echo "$id"; exit 0
J
  printf '#!/bin/bash\necho "$1" > "$FCT/lastreq"; echo "$FCT/req/$1.txt"\n' > "$T/s/mkreq.sh"
  printf 'import sys\n' > "$T/s/launch.py"; chmod +x "$T/s/test.sh" "$T/s/mkreq.sh"
  printf 'export SG_STATE=%s SG_FORK_DIR=%s SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%%n SG_REPO=x/y SG_CHECKOUT=%s SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n' "$T/st" "$T/fork" "$T/co" > "$T/s/env.sh"
  cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"pulls?state=open"*) ;;
  *"issues/1/comments"*|*"issues/2/comments"*|*"issues/3/comments"*) echo "TDD RED (test-forkclose-shallow.sh): FAIL test_x" ;;
  *"-q .state"*) echo open ;;
  *"graphql"*) echo "[]";;
  *"--include"*) echo "{\"state\":\"open\"}";;
esac
J
  chmod +x "$T/bin/gh"
  # the lineage the fork descends from: A -> B -> C published, A -> Y -> L1 pre-migration
  U=$T/up; /usr/bin/git -C "$U" init -q -b main
  ( cd "$U"; echo 0 > f; /usr/bin/git add .; /usr/bin/git commit -qm A; echo 1 > f; /usr/bin/git commit -qam B; echo 2 > f; /usr/bin/git commit -qam C
    /usr/bin/git checkout -q -b legacy main~2; echo y > y; /usr/bin/git add .; /usr/bin/git commit -qm Y; echo y1 >> y; /usr/bin/git commit -qam L1 )
  # what the migration published: the legacy commits are not on the remote, under any ref
  /usr/bin/git init -q --bare "$T/origin.git"; /usr/bin/git -C "$T/origin.git" config receive.shallowUpdate true   # GitHub takes a push from a shallow clone and only then dies on the missing parent; a local remote refuses it up front unless this is set
  /usr/bin/git -C "$U" push -q "$T/origin.git" main:refs/heads/main main:refs/heads/local/next
  # the fork: local/next complete down to A, one legacy branch fetched dark
  F=$T/fork; /usr/bin/git -C "$F" init -q -b main
  /usr/bin/git -C "$F" remote add origin "$T/origin.git"
  /usr/bin/git -C "$F" fetch -q --depth=100 origin main:refs/heads/local/next
  /usr/bin/git -C "$F" fetch -q origin
  /usr/bin/git -C "$F" fetch -q --depth=1 "file://$U" legacy:refs/heads/legacy
  /usr/bin/git -C "$F" checkout -q local/next
  /usr/bin/git -C "$F" checkout -q -b gui/1 local/next
  /usr/bin/git -C "$F" merge -q --no-ff --allow-unrelated-histories -m "Merge the pre-migration lineage" legacy 2>/dev/null \
    || { echo 2 > "$F/f"; /usr/bin/git -C "$F" add f; /usr/bin/git -C "$F" commit -qm "Merge the pre-migration lineage"; }   # no common ancestor locally: every file in both trees is add/add, and the published side wins
  echo g1 > "$F/g"; /usr/bin/git -C "$F" add g; /usr/bin/git -C "$F" commit -qm "work 1"
  # a branch born on the published lineage, the control: it is created by the scenario that needs it, so no pass here has two candidates
  /usr/bin/git -C "$F" checkout -q local/next
  echo '{"items":[{"n":1,"st":"Code Complete","s":"open"},{"n":2,"st":"Code Complete","s":"open"},{"n":3,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
}
control() { /usr/bin/git -C "$1/fork" checkout -q -b gui/2 local/next; echo g2 > "$1/fork/g2"; /usr/bin/git -C "$1/fork" add g2; /usr/bin/git -C "$1/fork" commit -qm "work 2"; /usr/bin/git -C "$1/fork" checkout -q local/next; }
base_of() { /usr/bin/git -C "$1/fork" rev-parse local/next; }
# 1: the boundary branch must be refused by name, the base untouched, and the refusal must be recorded
T1=$(mktemp -d); mkfix "$T1"; export FCT=$T1 PATH="$T1/bin:$PATH"
B1=$(/usr/bin/git -C "$T1/fork" rev-parse legacy); b0=$(base_of "$T1")
r=$(bash "$T1/s/forkclose.sh" 2>&1)
case "$r" in *"FORKCLOSE SHALLOW #1"*) ok "the boundary branch is refused with a SHALLOW line naming the issue";; *) ko "no refusal for the branch dragging the shallow boundary: '$r'";; esac
case "$r" in *"gui/1"*) ok "the refusal names the branch it refuses";; *) ko "the refusal does not name gui/1: '$r'";; esac
case "$r" in *"${B1:0:9}"*) ok "the refusal names the boundary commit ${B1:0:9}";; *) ko "the refusal does not name the boundary commit: '$r'";; esac
[ "$(base_of "$T1")" = "$b0" ] && ok "local/next did not move" || ko "the boundary branch was merged anyway: local/next is $(base_of "$T1")"
[ "$(/usr/bin/git -C "$T1/fork" rev-list --count "$b0..gui/1")" != 0 ] && ok "the refused branch is left in place, not deleted" || ko "the refused branch was rewritten"
[ -z "$(/usr/bin/git -C "$T1/fork" log --format=%s "$b0..local/next" 2>/dev/null | grep 'Merge gui/1')" ] && ok "no merge commit for the refused branch" || ko "a merge commit for gui/1 exists"
c=$(ls "$T1/st/cq/"*shallow* 2>/dev/null | head -1)
[ -n "$c" ] && grep -q "#1" "$c" && grep -q "gui/1" "$c" && ok "a cq/ card names the issue and the branch" || ko "no cq/ card for the refusal: '$(cat "$c" 2>/dev/null)'"
# the merge the loop used to do is exactly the tip #1823 is about: the push range carries the boundary commit and the remote
# has never had its parent -- which is what index-pack reports as `remote: fatal: did not receive expected object <parent>`.
/usr/bin/git -C "$T1/fork" merge -q --no-ff -m "Merge gui/1" gui/1 2>/dev/null
miss=$(/usr/bin/git -C "$T1/fork" rev-list "origin/local/next..local/next" | while read -r c; do
  for p in $(/usr/bin/git -C "$T1/fork" cat-file -p "$c" | sed -n 's/^parent //p'); do
    /usr/bin/git -C "$T1/fork" cat-file -e "$p" 2>/dev/null || { echo "$c $p"; break; }; done; done | head -1)
[ "${miss%% *}" = "$B1" ] && ok "the merged tip drags the boundary ${B1:0:9} into origin/local/next..local/next" || ko "the merged tip does not carry the boundary: '$miss'"
p=${miss#* }
[ -n "$p" ] && ! /usr/bin/git -C "$T1/origin.git" cat-file -e "$p" 2>/dev/null && ok "the parent $p index-pack would name is not on the remote either" || ko "the remote has the parent: '$p'"
# The recipe the refusal prints must not lose the branch. `rebase --onto $SG_FORK_BASE $sc $br` takes `$sc..$br`, and $sc can be
# the branch TIP -- the range is then empty and a rebase applies nothing, or worse, drops the commits the operator meant to keep
# (#1852). The line must name the branch's own commits, and a branch whose $sc IS its tip must still be described usefully.
own1=$(/usr/bin/git -C "$T1/fork" rev-list --reverse "local/next..gui/1" | tr '\n' ' ')
# The recipe must BE the cherry-pick form. The message may still mention the range form -- it names it to say why it is wrong --
# so the assertion is on the recipe it hands over, which is what an operator runs.
case "$r" in *"switch -c gui/1-rebuilt local/next"*) ok "the recipe branches the rebuilt branch off the base";; *) ko "the recipe does not rebuild from $SG_FORK_BASE: $(printf '%s' "$r" | tr '`' ' ' | cut -c1-160)";; esac
for c1 in $own1; do case "$r" in *"${c1:0:9}"*) ;; *) ko "the recipe omits own commit ${c1:0:9}"; break;; esac; done
[ -n "$own1" ] && ok "the recipe names the branch's own commits ($(wc -w <<<"$own1") of them)"
# and the recipe actually works: cherry-picking those commits onto the base yields a tree equal to the branch's own tree
/usr/bin/git -C "$T1/fork" checkout -q -b fix1 local/next
/usr/bin/git -C "$T1/fork" cherry-pick -q $own1 >/dev/null 2>&1
t1=$(/usr/bin/git -C "$T1/fork" rev-parse 'fix1^{tree}'); t2=$(/usr/bin/git -C "$T1/fork" rev-parse 'gui/1^{tree}')
[ "$t1" = "$t2" ] && ok "the recipe reproduces the branch's tree on the rebuilt branch" || ko "the recipe changes the tree: $t1 vs $t2"
/usr/bin/git -C "$T1/fork" checkout -q local/next; /usr/bin/git -C "$T1/fork" branch -q -D fix1
/usr/bin/git -C "$T1/fork" reset -q --hard "$b0"
rm -rf "$T1"
# 2: a branch born on the published lineage still lands -- the guard refuses a boundary, not everything
T2=$(mktemp -d); mkfix "$T2"; export FCT=$T2; b2=$(base_of "$T2"); control "$T2"
r=$(bash "$T2/s/forkclose.sh" 2>&1)
case "$r" in *"FORKCLOSE #2"*) ok "the branch without a boundary still lands";; *) ko "the control branch did not land: '$r'";; esac
case "$r" in *"FORKCLOSE SHALLOW #2"*) ko "the control branch was refused: '$r'";; *) ok "no false refusal on the control branch";; esac
[ "$(base_of "$T2")" != "$b2" ] && ok "local/next moved for the control branch" || ko "local/next did not move for the control branch"
rm -rf "$T2"
# 3: a base that is ALREADY ahead of origin and carries the boundary (what the old loop left behind) must be reported,
# not left silent: the push of the base cannot succeed until that commit is grafted away, and nothing else says so.
T3=$(mktemp -d); mkfix "$T3"; export FCT=$T3; b3=$(base_of "$T3"); control "$T3"
/usr/bin/git -C "$T3/fork" merge -q --no-ff -m "Merge gui/1" gui/1   # the merge the old loop did
/usr/bin/git -C "$T3/fork" branch -q -D gui/1                        # nobody is left to refuse: the branch is in the base
r=$(bash "$T3/s/forkclose.sh" 2>&1)
case "$r" in *"FORKCLOSE PUSH-BLOCKED"*) ok "a base ahead of origin carrying the boundary is reported: $(echo "$r" | grep -o 'FORKCLOSE PUSH-BLOCKED[^.]*' | head -1 | cut -c1-110)";; *) ko "the unpushable base stayed silent: '$r'";; esac
[ -n "$(ls "$T3/st/cq/"*pushblocked* 2>/dev/null)" ] && ok "the report is queued as a cq/ card too" || ko "no cq/ card for the unpushable base"
[ "$(base_of "$T3")" != "$b3" ] && ok "the pass still lands the healthy branch beside the report" || ko "the report blocked the whole pass"
rm -rf "$T3"
# 4: (#1852) the parents come from the commit HEADER, never from a `sed` over the whole object. `cat-file -p` prints the
# message verbatim after the headers, so a body line beginning with "parent " was collected as a parent hash, `cat-file -e`
# failed on the words, and the branch was parked forever as a shallow boundary. This is the 2026-10-12 case on gui/1848,
# whose message carries "parent commit and refuses a test that passes there, a range with no test, and a".
T4=$(mktemp -d); mkfix "$T4"; export FCT=$T4; b4=$(base_of "$T4")
/usr/bin/git -C "$T4/fork" branch -q -D gui/1   # the fixture's own boundary branch: its SHALLOW refusal would be the only card here
/usr/bin/git -C "$T4/fork" checkout -q -b gui/3 local/next
echo g3 > "$T4/fork/g3"; /usr/bin/git -C "$T4/fork" add g3
/usr/bin/git -C "$T4/fork" commit -q -m "Docs: the guard reads the parent from the header

The paragraph whose continuation line begins with
parent commit and refuses a test that passes there, a range with no test, and a
span that names no commit."
/usr/bin/git -C "$T4/fork" checkout -q local/next
r=$(bash "$T4/s/forkclose.sh" 2>&1)
case "$r" in *"FORKCLOSE SHALLOW #3"*) ko "a message line beginning with 'parent ' was read as a shallow boundary: '$r'";; *) ok "a body line beginning with 'parent ' is not a parent hash";; esac
[ "$(base_of "$T4")" != "$b4" ] && ok "the branch whose message says 'parent ...' still lands" || ko "it was parked as shallow: '$r'"
if [ -n "$(ls "$T4/st/cq/"*shallow* 2>/dev/null)" ]; then ko "a shallow cq/ card was written for a present-parent commit"; else ok "no shallow card for a commit whose parents are all here"; fi
rm -rf "$T4"; exit $fail
