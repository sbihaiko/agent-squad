#!/bin/bash
# seconds: 2.50
# Regression test for publish-live.sh: the live skill follows the repository as an explicit FILE LIST, never a mirror,
# and the fork-only route the repository retired is dropped from the live copy by name (#134).
# usage: bash test-publish-live.sh [path/to/publish-live.sh]
src=${1:-$(dirname "$0")/publish-live.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset SG_REPO                                                          # the close reads the fixture's own origin, never the loop's repository

repo="$T/repo/contrib/squad-goal"; live="$T/home/.claude/skills/squad-goal"
mkdir -p "$repo/scripts/profiles" "$live/scripts/profiles"
printf 'skill v2\n' > "$repo/SKILL.md"; printf 'watch v2\n' > "$repo/scripts/watch.sh"
printf '{"models": 2}\n' > "$repo/scripts/models.json"; printf 'example\n' > "$repo/scripts/profiles/example.sh"
printf 'skill v1\n' > "$live/SKILL.md"; printf 'watch v1\n' > "$live/scripts/watch.sh"
printf '{"models": "mine"}\n' > "$live/scripts/models.json"      # machine config: never shipped, never dropped
printf 'mine\n' > "$live/scripts/profiles/mine.sh"              # a per-repo profile: machine config
printf 'note\n' > "$live/scripts/local-note.txt"                # a live-only file no one named: left alone
# The fork-only route #122 retired; #134 names it for the live copy.
retired=("fork""close.sh" "fork""-gate.sh" "publish-landing.sh" "sync-to-""fork.sh" "refresh-from-""fork.sh" \
         "fork""only.sh" "forkrescue.py" "forkstrand.py")
for n in "${retired[@]}"; do printf '#!/bin/bash\n' > "$live/scripts/$n"; done
# The suites of the retired route stayed behind when their scripts were dropped, and every one of them failed on a call to
# a file that is not there (#210). A retired name takes its suites with it; two of them do not carry their subject in their
# name. A suite the repository ships stays.
mysuites=(test-forkclose-wait.sh test-forkbranch-source.sh test-sync-refresh.sh)
for s in "${mysuites[@]}"; do printf '#!/bin/bash\n' > "$live/scripts/$s"; done
# The pull-request route #361 retired: the live copy held its scripts, their suites and its data file, and the publish named
# none of them, so they stayed forever. A retired name takes its suites with it; three do not carry their subject in their name.
prretired=(autofix.sh autopanel.sh autopilot.sh prland.sh retryfix.sh revci.sh speed.py)
prsuites=(test-autofix.sh test-autofix-rounds.sh test-autopilot-debt.sh test-autopilot-gate.sh test-autopilot-hold.sh \
          test-autopilot-leash.sh test-autopilot-norefs.sh test-autopilot-stale-launch.sh test-autopilot-two-repos.sh \
          test-autofrontier-merged.sh test-janitor-g.sh test-janitor-kmn.sh test-prland.sh test-revci-notice.sh)
prdata=(ci-baseline.txt)
for n in "${prretired[@]}"; do printf '#!/bin/bash\n' > "$live/scripts/$n"; done
for s in "${prsuites[@]}"; do printf '#!/bin/bash\n' > "$live/scripts/$s"; done
for n in "${prdata[@]}"; do printf 'baseline\n' > "$live/$n"; done
printf 'mine\n' > "$live/scripts/profiles/agent-squad.sh"   # machine config: never shipped, never dropped
printf '#!/bin/bash\n' > "$repo/scripts/test-watch-ok.sh"

# --- --check changes nothing and names every file it would touch ---------------------------------
before=$(cd "$live" && find . -type f | LC_ALL=C sort | while read -r f; do printf '%s %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done)
out=$(bash "$src" --check "$repo" "$live" 2>&1); rc=$?
after=$(cd "$live" && find . -type f | LC_ALL=C sort | while read -r f; do printf '%s %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done)
[ "$rc" = 1 ] && ok "--check exits 1 while the live skill differs" || ko "--check exit (got $rc)"
[ "$before" = "$after" ] && ok "--check changes nothing" || ko "--check wrote to the live skill"
printf '%s\n' "$out" | /usr/bin/grep -qx 'copy SKILL.md' && ok "--check names the file it would copy" || ko "copy line missing: $out"
for n in "${retired[@]}"; do
  printf '%s\n' "$out" | /usr/bin/grep -qx "drop scripts/$n" && ok "--check names the retired name it would drop: $n" || ko "drop line missing for $n: $out"
done
for s in "${mysuites[@]}"; do
  printf '%s\n' "$out" | /usr/bin/grep -qx "drop scripts/$s" && ok "--check names the suite of the retired route it would drop: $s" || ko "drop line missing for $s: $out"
done
for n in "${prretired[@]}"; do
  printf '%s\n' "$out" | /usr/bin/grep -qx "drop scripts/$n" && ok "--check names the retired pull-request script it would drop: $n" || ko "drop line missing for $n: $out"
done
for s in "${prsuites[@]}"; do
  printf '%s\n' "$out" | /usr/bin/grep -qx "drop scripts/$s" && ok "--check names the retired pull-request suite it would drop: $s" || ko "drop line missing for $s: $out"
done
for n in "${prdata[@]}"; do
  printf '%s\n' "$out" | /usr/bin/grep -qx "drop $n" && ok "--check names the retired data file it would drop: $n" || ko "drop line missing for $n: $out"
done

# --- a publish drops each retired name by name, never the whole directory ------------------------
out=$(bash "$src" "$repo" "$live" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "a publish exits 0" || ko "publish exit (got $rc)"
[ "$(cat "$live/SKILL.md")" = "skill v2" ] && ok "publish copies the shipped SKILL.md" || ko "SKILL.md not copied"
[ "$(cat "$live/scripts/watch.sh")" = "watch v2" ] && ok "publish copies the shipped script" || ko "watch.sh not copied"
for n in "${retired[@]}"; do
  [ ! -e "$live/scripts/$n" ] && ok "publish dropped the retired name: $n" || ko "$n survived the publish"
done
[ "$(cat "$live/scripts/models.json")" = '{"models": "mine"}' ] && ok "publish leaves the machine config (models.json) alone" || ko "models.json overwritten: $(cat "$live/scripts/models.json")"
[ -f "$live/scripts/profiles/mine.sh" ] && ok "publish leaves a per-repo profile alone" || ko "the per-repo profile was removed"
[ -f "$live/scripts/profiles/example.sh" ] && ok "publish ships the template profile" || ko "example.sh not shipped"
for s in "${mysuites[@]}"; do
  [ ! -e "$live/scripts/$s" ] && ok "publish dropped the suite of the retired route: $s" || ko "$s survived the publish"
done
for n in "${prretired[@]}"; do
  [ ! -e "$live/scripts/$n" ] && ok "publish dropped the retired pull-request script: $n" || ko "$n survived the publish"
done
for s in "${prsuites[@]}"; do
  [ ! -e "$live/scripts/$s" ] && ok "publish dropped the retired pull-request suite: $s" || ko "$s survived the publish"
done
for n in "${prdata[@]}"; do
  [ ! -e "$live/$n" ] && ok "publish dropped the retired data file: $n" || ko "$n survived the publish"
done
[ -f "$live/scripts/profiles/agent-squad.sh" ] && ok "publish leaves a per-repo profile alone (agent-squad.sh)" || ko "agent-squad.sh was removed"
[ -f "$live/scripts/test-watch-ok.sh" ] && ok "publish keeps the suite of a script it ships" || ko "the shipped suite was removed"
[ -f "$live/scripts/local-note.txt" ] && ok "publish removes nothing it did not name" || ko "a file no one named was removed"

# --- a second publish finds nothing to do --------------------------------------------------------
out=$(bash "$src" --check "$repo" "$live" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "the second --check finds nothing to do" || ko "second --check still differs: $out"
[ -z "$out" ] && ok "the second --check prints nothing" || ko "second --check printed: $out"

# --- the source must be in SG_SQUAD's history (#605) ----------------------------------------------
# The live scripts import squad from SG_SQUAD. A publish from a tree ahead of SG_SQUAD ships scripts that call a squad
# command SG_SQUAD lacks, so it is refused: nonzero, nothing written, one line naming both commits.
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t GIT_CONFIG_GLOBAL=/dev/null
sq="$T/sq"; wt="$T/wt"; mkdir -p "$sq/contrib/squad-goal/scripts"
printf 'skill A\n' > "$sq/contrib/squad-goal/SKILL.md"; printf 'watch A\n' > "$sq/contrib/squad-goal/scripts/watch.sh"
git -C "$sq" init -q -b main && git -C "$sq" add -A && git -C "$sq" commit -qm A
# The ahead tree is a clone, a main checkout of its own: a linked worktree is refused before this check (#658).
git clone -q "$sq" "$wt" && git -C "$wt" checkout -q -b ahead
printf 'skill B\n' > "$wt/contrib/squad-goal/SKILL.md"; git -C "$wt" commit -qam B
a=$(git -C "$sq" rev-parse --short HEAD); b=$(git -C "$wt" rev-parse --short HEAD)
snap() { (cd "$1" && find . -type f | LC_ALL=C sort | while read -r f; do printf '%s %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done); }
live2="$T/live2"; mkdir -p "$live2/scripts"; printf 'skill v0\n' > "$live2/SKILL.md"
before=$(snap "$live2")
out=$(SG_SQUAD="$sq" bash "$src" "$wt/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" != 0 ] && ok "a source ahead of SG_SQUAD is refused" || ko "a source ahead of SG_SQUAD published (rc $rc): $out"
[ "$before" = "$(snap "$live2")" ] && ok "a refused publish writes nothing" || ko "a refused publish wrote to the live skill"
[ "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" = 1 ] && ok "the refusal is one line" || ko "the refusal is not one line: $out"
printf '%s' "$out" | /usr/bin/grep -q "$a" && printf '%s' "$out" | /usr/bin/grep -q "$b" && printf '%s' "$out" | /usr/bin/grep -qi 'fast-forward SG_SQUAD' \
  && ok "the refusal names both commits and the fast-forward" || ko "the refusal line: $out"
out=$(SG_SQUAD="$sq" bash "$src" "$sq/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$live2/SKILL.md")" = "skill A" ] && ok "SG_SQUAD as the source publishes" || ko "SG_SQUAD as the source (rc $rc): $out"
git -C "$sq" fetch -q "$wt" ahead && git -C "$sq" merge -q --ff-only FETCH_HEAD; printf 'skill v0\n' > "$live2/SKILL.md"
git -C "$wt" checkout -q --detach HEAD~1
out=$(SG_SQUAD="$sq" bash "$src" "$wt/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$live2/SKILL.md")" = "skill A" ] && ok "a source behind SG_SQUAD publishes" || ko "a source behind SG_SQUAD (rc $rc): $out"
git -C "$wt" checkout -q ahead; printf 'skill v0\n' > "$live2/SKILL.md"
out=$(SG_SQUAD="$sq" bash "$src" "$wt/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$live2/SKILL.md")" = "skill B" ] && ok "a source equal to SG_SQUAD publishes" || ko "a source equal to SG_SQUAD (rc $rc): $out"
printf 'skill v0\n' > "$live2/SKILL.md"
out=$(env -u SG_SQUAD bash "$src" "$wt/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$live2/SKILL.md")" = "skill B" ] && ok "no SG_SQUAD publishes as before" || ko "no SG_SQUAD (rc $rc): $out"

# --- a publish records the tested sha, and --check reads it (#700) ---------------------------------
# The tested sha is the source HEAD. A publish writes PUBLISHED (`<sha> <utc>`) at the top of the live skill; --check
# names `publish <sha>` while PUBLISHED is missing or names another sha.
live3="$T/live3"; mkdir -p "$live3/scripts"; sqh=$(git -C "$sq" rev-parse HEAD)
out=$(env -u SG_SQUAD bash "$src" --check "$sq/contrib/squad-goal" "$live3" 2>&1); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$out" | /usr/bin/grep -qx "publish $sqh" && ok "--check names the sha to publish when PUBLISHED is missing" || ko "--check with no PUBLISHED (rc $rc): $out"
[ ! -e "$live3/PUBLISHED" ] && ok "--check writes no PUBLISHED" || ko "--check wrote PUBLISHED"
out=$(env -u SG_SQUAD bash "$src" "$sq/contrib/squad-goal" "$live3" 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cut -d' ' -f1 "$live3/PUBLISHED" 2>/dev/null)" = "$sqh" ] && ok "a publish writes PUBLISHED with the source HEAD sha" || ko "PUBLISHED after a publish (rc $rc): $(cat "$live3/PUBLISHED" 2>&1)"
/usr/bin/grep -qE "^$sqh [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\$" "$live3/PUBLISHED" && ok "PUBLISHED is '<sha> <utc>'" || ko "PUBLISHED shape: $(cat "$live3/PUBLISHED" 2>&1)"
out=$(env -u SG_SQUAD bash "$src" --check "$sq/contrib/squad-goal" "$live3" 2>&1); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] && ok "--check is quiet once PUBLISHED names the source HEAD" || ko "--check after the publish (rc $rc): $out"
printf '0000000000000000000000000000000000000000 2020-01-01T00:00:00Z\n' > "$live3/PUBLISHED"
out=$(env -u SG_SQUAD bash "$src" --check "$sq/contrib/squad-goal" "$live3" 2>&1); rc=$?
[ "$rc" = 1 ] && [ "$out" = "publish $sqh" ] && ok "--check names the sha to publish when PUBLISHED is stale" || ko "--check with a stale PUBLISHED (rc $rc): $out"

# --- a publish closes the issues its commits name, and a TDD RED commit closes nothing (#732) -----
# The loop-bug stop reads the issue state (ADR-0149): a published commit carrying a `Closes #n` trailer closes #n with
# one comment naming the sha and the published time, so the stop lifts on the next pass. The commits are the range the
# publish makes live: the previously published sha (exclusive) to the source HEAD, or the HEAD alone when PUBLISHED is
# missing. A TDD RED commit names its issue in the subject and carries no closing trailer, so it closes nothing
# (ADR-0136). --check closes nothing. The repository is SG_REPO when set, else the source's github.com origin.
cb="$T/closes"; mkdir -p "$cb/contrib/squad-goal/scripts" "$T/cbin"
cat > "$T/cbin/gh" <<EOF
#!/bin/bash
case "\$*" in *state=open*) echo '[]'; exit 0;; esac
printf '%s\n' "\$*" >> "$T/gh.log"
exit 0
EOF
chmod +x "$T/cbin/gh"
git init -q -b main "$cb"; git -C "$cb" remote add origin https://github.com/example/squad.git
printf 'skill v1\n' > "$cb/contrib/squad-goal/SKILL.md"; git -C "$cb" add -A; git -C "$cb" commit -qm base
livec="$T/live-closes"; mkdir -p "$livec/scripts" "$T/st"; printf 'skill v0\n' > "$livec/SKILL.md"
crun() { env -u SG_SQUAD -u SG_REPO SG_STATE="$T/st" PATH="$T/cbin:$PATH" bash "$src" "$cb/contrib/squad-goal" "$livec" 2>&1; }
ccheck() { env -u SG_SQUAD -u SG_REPO SG_STATE="$T/st" PATH="$T/cbin:$PATH" bash "$src" --check "$cb/contrib/squad-goal" "$livec" 2>&1; }
closes_count() { /usr/bin/grep -c 'state=closed' "$T/gh.log" 2>/dev/null || true; }
# A TDD RED commit names the issue before its fix exists; the fix commit is the one that carries the closing trailer.
printf 'skill v1b\n' > "$cb/contrib/squad-goal/SKILL.md"; git -C "$cb" add -A; git -C "$cb" commit -qm "TDD RED: the live copy loses a line #11"
printf 'skill v2\n' > "$cb/contrib/squad-goal/SKILL.md"; git -C "$cb" add -A; git -C "$cb" commit -qm "Fix the live copy" -m "Closes #12"
fixsha=$(git -C "$cb" rev-parse HEAD)
out=$(crun); rc=$?
[ "$rc" = 0 ] && ok "a publish with a closing commit exits 0" || ko "publish with a closing commit (rc $rc): $out"
[ "$(closes_count)" = 1 ] && ok "the publish closes exactly the issue the fix names" || ko "close calls: $(cat "$T/gh.log" 2>/dev/null)"
case "$(cat "$T/gh.log")" in *"api -X POST repos/example/squad/issues/12/comments"*) ok "the comment goes to the origin repository and #12";; *) ko "the comment: $(cat "$T/gh.log")";; esac
case "$(cat "$T/gh.log")" in *"api -X PATCH repos/example/squad/issues/12 "*"state=closed"*) ok "the close names the origin repository and #12";; *) ko "the close: $(cat "$T/gh.log")";; esac
case "$(cat "$T/gh.log")" in *"$fixsha"*) ok "the comment names the published sha";; *) ko "the comment does not name $fixsha: $(cat "$T/gh.log")";; esac
ts=$(cut -d' ' -f2 "$livec/PUBLISHED"); case "$(cat "$T/gh.log")" in *"$ts"*) ok "the comment names the published time";; *) ko "the comment does not name $ts: $(cat "$T/gh.log")";; esac
case "$(cat "$T/gh.log")" in *"issues/11"*) ko "the TDD RED commit closed #11: $(cat "$T/gh.log")";; *) ok "a TDD RED commit alone closes nothing";; esac
case "$out" in *"publish-live: closed #12"*) ok "the publish names the issue it closed";; *) ko "the close is not named: $out";; esac
# The next publish closes only the commits the last one did not carry; a Refs line is not a closing trailer, and a list
# on one trailer closes each number once.
printf 'skill v3\n' > "$cb/contrib/squad-goal/SKILL.md"; git -C "$cb" add -A; git -C "$cb" commit -qm "Touch the copy" -m "Refs #13"
printf 'skill v4\n' > "$cb/contrib/squad-goal/SKILL.md"; git -C "$cb" add -A; git -C "$cb" commit -qm "Another fix" -m "Closes #14, #15"
printf 'skill v5\n' > "$cb/contrib/squad-goal/SKILL.md"; git -C "$cb" add -A; git -C "$cb" commit -qm "The same issue again" -m "Closes #14"
: > "$T/gh.log"; out=$(crun); rc=$?
case "$(cat "$T/gh.log")" in *"api -X PATCH repos/example/squad/issues/14 "*"state=closed"*) ok "a list in one trailer closes its issue";; *) ko "no close for #14: $(cat "$T/gh.log")";; esac
case "$(cat "$T/gh.log")" in *"issues/15 "*"state=closed"*) ok "the second number of a list closes";; *) ko "no close for #15: $(cat "$T/gh.log")";; esac
case "$(cat "$T/gh.log")" in *"issues/13"*) ko "a Refs line closed #13: $(cat "$T/gh.log")";; *) ok "a Refs line closes nothing";; esac
[ "$(closes_count)" = 2 ] && ok "an issue named twice in the range closes once" || ko "close calls: $(cat "$T/gh.log")"
# A republish of the same sha closes nothing again, and --check never closes.
printf 'hand\n' > "$livec/SKILL.md"; : > "$T/gh.log"; out=$(crun)
[ ! -s "$T/gh.log" ] && ok "a republish of the same sha closes nothing" || ko "a republish closed again: $(cat "$T/gh.log")"
printf 'skill v6\n' > "$cb/contrib/squad-goal/SKILL.md"; git -C "$cb" add -A; git -C "$cb" commit -qm "Body" -m "Closes #16"
: > "$T/gh.log"; out=$(ccheck); rc=$?
[ "$rc" = 1 ] && [ ! -s "$T/gh.log" ] && ok "--check names a difference and closes nothing" || ko "--check closed (rc $rc): $(cat "$T/gh.log")"
crun >/dev/null                      # the publish --check named carries the #16 fix: it closes now, so the next range holds the TDD RED commit alone
# A commit that names its issue in a TDD RED subject closes nothing, even inside the published range.
printf 'skill v7\n' > "$cb/contrib/squad-goal/SKILL.md"; git -C "$cb" add -A; git -C "$cb" commit -qm "TDD RED: the publish still leaks #17"
: > "$T/gh.log"; out=$(crun); rc=$?
[ "$rc" = 0 ] && [ ! -s "$T/gh.log" ] && ok "a TDD RED commit alone closes nothing inside the range" || ko "a TDD RED commit closed something: $(cat "$T/gh.log")"
# A close GitHub refuses is named and never fails the publish: the live copy already moved.
mkdir -p "$T/fbin"
cat > "$T/fbin/gh" <<EOF
#!/bin/bash
case "\$*" in *state=open*) echo '[]'; exit 0;; esac
printf '%s\n' "\$*" >> "$T/gh.log"
case "\$*" in *state=closed*) exit 1;; esac
exit 0
EOF
chmod +x "$T/fbin/gh"
printf 'skill v8\n' > "$cb/contrib/squad-goal/SKILL.md"; git -C "$cb" add -A; git -C "$cb" commit -qm "A fix GitHub refuses" -m "Closes #18"
: > "$T/gh.log"; out=$(env -u SG_SQUAD -u SG_REPO SG_STATE="$T/st" PATH="$T/fbin:$PATH" bash "$src" "$cb/contrib/squad-goal" "$livec" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "a refused close does not fail the publish" || ko "a refused close failed the publish (rc $rc): $out"
case "$out" in *"publish-live: #18 did not close:"*) ok "the refused close is named";; *) ko "the refused close is silent: $out";; esac
case "$out" in *"file(s) published"*) ok "the publish still reports the files it copied";; *) ko "the publish line is missing: $out";; esac

# --- a linked worktree is never the source (#658) -------------------------------------------------
# A coding child published its unlanded branch from its own worktree. A publish from a linked worktree is refused: nonzero,
# nothing written, one line naming the tree. --check writes nothing, so it still runs.
lw="$T/linked"; git -C "$sq" worktree add -q -b child "$lw" main
printf 'skill child\n' > "$lw/contrib/squad-goal/SKILL.md"
rm -f "$live2/PUBLISHED"; printf 'skill v0\n' > "$live2/SKILL.md"; before=$(snap "$live2")
for env in "env -u SG_SQUAD" "env SG_SQUAD=$sq"; do
  out=$($env bash "$src" "$lw/contrib/squad-goal" "$live2" 2>&1); rc=$?
  [ "$rc" != 0 ] && ok "a publish from a linked worktree is refused ($env)" || ko "a linked worktree published (rc $rc): $out"
  [ "$before" = "$(snap "$live2")" ] && ok "a refused worktree publish writes nothing ($env)" || ko "a refused worktree publish wrote ($env)"
  printf '%s' "$out" | /usr/bin/grep -q "$(cd "$lw" && pwd -P)" && printf '%s' "$out" | /usr/bin/grep -qi 'worktree' \
    && ok "the refusal names the worktree ($env)" || ko "the worktree refusal line: $out"
done
[ ! -e "$live2/PUBLISHED" ] && ok "a refused worktree publish writes no PUBLISHED" || ko "a refused worktree publish wrote PUBLISHED"
out=$(env -u SG_SQUAD bash "$src" --check "$lw/contrib/squad-goal" "$live2" 2>&1); rc=$?
[ "$rc" = 1 ] && printf '%s\n' "$out" | /usr/bin/grep -qx 'copy SKILL.md' && [ "$before" = "$(snap "$live2")" ] \
  && ok "--check from a linked worktree still reports and writes nothing" || ko "--check from a linked worktree (rc $rc): $out"

# --- a hand-run publish reports a chain whose head or order differs, and never writes live models.json (#1261) ----
cr="$T/chain/repo/contrib/squad-goal"; cl="$T/chain/live"; mkdir -p "$cr/scripts" "$cl/scripts"
chains() { printf '{"schema_version":2,"chains":{"router":[{"id":"%s"},{"id":"z"}],"coding":[{"id":"a"}]}}\n' "$1"; }
chains head-new > "$cr/scripts/models.json"; chains head-old > "$cl/scripts/models.json"; printf 'k\n' > "$cr/SKILL.md"; cp "$cr/SKILL.md" "$cl/SKILL.md"
before=$(cat "$cl/scripts/models.json")
out=$(env -u SG_SQUAD bash "$src" "$cr" "$cl" 2>&1)
printf '%s\n' "$out" | /usr/bin/grep -E 'router' | /usr/bin/grep -q 'head-new' && printf '%s\n' "$out" | /usr/bin/grep -E 'router' | /usr/bin/grep -q 'head-old' \
  && ok "a differing router head is one line naming both chains" || ko "no chain line: $out"
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c 'coding')" = 0 ] && ok "a chain that matches is not named" || ko "coding named: $out"
[ "$before" = "$(cat "$cl/scripts/models.json")" ] && ok "the publish never writes live models.json" || ko "live models.json was written"
out=$(env -u SG_SQUAD bash "$src" --no-compare "$cr" "$cl" 2>&1)
case "$out" in *head-*) ko "--no-compare still compared: $out";; *) ok "--no-compare compares nothing";; esac
chains head-new > "$cl/scripts/models.json"
out=$(env -u SG_SQUAD bash "$src" "$cr" "$cl" 2>&1)
case "$out" in *head-*) ko "identical chains printed: $out";; *) ok "identical chains print nothing";; esac

# --- the live skill copy is never the source (#1377) ----------------------------------------------
# A publish run from the live skill copy copies the tree onto itself and reports a successful publish.
# It is refused: nonzero, nothing written, one line naming both paths and the recovery command, before
# any copy, retirement or tracker call. --check refuses too, and a symlink alias of the live copy is
# the same tree. A publish from the repository tree still publishes.
live4="$T/live4"; mkdir -p "$live4/scripts" "$live4/scripts/profiles"
printf 'skill v1\n' > "$live4/SKILL.md"; printf 'watch v1\n' > "$live4/scripts/watch.sh"
printf '{"models": "mine"}\n' > "$live4/scripts/models.json"
printf '#!/bin/bash\n' > "$live4/scripts/autofix.sh"          # a retired name: a refusal never drops it
before=$(snap "$live4")
alias_="$T/alias-live"; ln -s "$live4" "$alias_"
for s in "$live4" "$alias_"; do
  for mode in "" "--check"; do
    out=$(env -u SG_SQUAD bash "$src" $mode "$s" "$live4" 2>&1); rc=$?
    [ "$rc" != 0 ] && ok "a publish whose source is the destination is refused ($s $mode)" || ko "the live copy published ($s $mode, rc $rc): $out"
    [ "$before" = "$(snap "$live4")" ] && ok "a refused self-publish writes nothing ($s $mode)" || ko "a refused self-publish wrote ($s $mode)"
    [ "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" = 1 ] && ok "the self-publish refusal is one line ($s $mode)" || ko "not one line ($s $mode): $out"
    printf '%s' "$out" | /usr/bin/grep -q "$live4" && printf '%s' "$out" | /usr/bin/grep -qi 'repository tree' \
      && ok "the refusal names both paths and the recovery command ($s $mode)" || ko "the refusal line ($s $mode): $out"
  done
done
[ -e "$live4/scripts/autofix.sh" ] && ok "a refusal retires nothing" || ko "a refusal dropped a retired name"
out=$(env -u SG_SQUAD bash "$src" "$sq/contrib/squad-goal" "$live4" 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$live4/SKILL.md")" = "skill B" ] && ok "a publish from the repository tree still publishes" || ko "repository-tree publish (rc $rc): $out"

# --- a publish verifies the destination before it reports success (#1379) ------------------------
# The copy loop reports what it tried, never what arrived. After the shipped selection and the retirement list are
# applied, the publish reads the destination back: every shipped file is present with the same bytes and, when the
# source is executable, executable; no retired name is left. A mismatch is a failed publish: nonzero, one line naming
# it, no success count and no PUBLISHED.
vt="$T/verify"; vr="$vt/repo/contrib/squad-goal"; vl="$vt/live"; vshim="$vt/shim"; mkdir -p "$vshim"
mkdir -p "$vr/scripts/profiles" && git -C "$vt/repo" init -q -b main
printf 'skill v2\n' > "$vr/SKILL.md"; printf 'watch v2\n' > "$vr/scripts/watch.sh"
printf '#!/bin/bash\necho tool\n' > "$vr/scripts/tool.sh"; chmod +x "$vr/scripts/tool.sh"
git -C "$vt/repo" add -A && git -C "$vt/repo" commit -qm verify
vfix() {
  rm -rf "$vl"; mkdir -p "$vl/scripts/profiles"
  printf 'skill v1\n' > "$vl/SKILL.md"; printf 'watch v1\n' > "$vl/scripts/watch.sh"
  printf '{"models": "mine"}\n' > "$vl/scripts/models.json"; printf 'mine\n' > "$vl/scripts/profiles/mine.sh"
  printf '#!/bin/bash\n' > "$vl/scripts/publish-landing.sh"      # a retired name: a normal publish drops it
}
# The shims stand in for a copy that goes wrong the ways a filesystem can: it fails midway, it leaves a partial temp
# and fails, it reports success with other bytes, or it drops the executable bit. The rm shim reports success and
# removes nothing, so a retired name lingers.
printf '%s\n' '#!/bin/bash' 'last=; for a; do last=$a; done' \
  'case "$VMODE" in' \
  '  fail) case "$*" in *watch.sh.new.*) exit 1;; esac;;' \
  '  partial) case "$*" in *watch.sh.new.*) printf part > "$last"; exit 1;; esac;;' \
  '  wrongbytes) /bin/cp "$@" || exit $?; printf corrupt > "$last"; exit 0;;' \
  '  noexec) /bin/cp "$@" || exit $?; chmod a-x "$last"; exit 0;;' \
  'esac' 'exec /bin/cp "$@"' > "$vshim/cp"
printf '%s\n' '#!/bin/bash' '[ "$VMODE" = keeprm ] && exit 0' 'exec /bin/rm "$@"' > "$vshim/rm"
chmod +x "$vshim/cp" "$vshim/rm"
vrun() { env -u SG_SQUAD -u SG_REPO PATH="$vshim:$PATH" VMODE="$1" bash "$src" "$vr" "$vl" 2>&1; }
vbad() { # vbad MODE NAMED LABEL: the publish must fail, name NAMED, print no count and write no PUBLISHED
  local out rc; vfix; out=$(vrun "$1"); rc=$?
  [ "$rc" != 0 ] && ok "$3 exits non-zero" || ko "$3 published (rc $rc): $out"
  printf '%s\n' "$out" | /usr/bin/grep -q "mismatch $2" && ok "$3 names $2" || ko "$3 names no mismatch for $2: $out"
  case "$out" in *"file(s) published"*) ko "$3 printed the success count: $out";; *) ok "$3 prints no success count";; esac
  [ ! -e "$vl/PUBLISHED" ] && ok "$3 writes no PUBLISHED" || ko "$3 wrote PUBLISHED"
}
vbad fail scripts/watch.sh "a copy that fails midway"
vbad partial scripts/watch.sh "a copy that leaves a partial temp and fails"
vfix; vrun partial >/dev/null 2>&1
[ -z "$(/bin/ls "$vl"/scripts/*.new.* 2>/dev/null)" ] && ok "a failed copy leaves no temp file at the destination" || ko "a failed copy left a temp file: $(/bin/ls "$vl"/scripts/*.new.* 2>/dev/null)"
vbad wrongbytes scripts/watch.sh "a copy that reports success with wrong bytes"
vbad noexec scripts/tool.sh "a copy that loses its executable bit"
vbad keeprm scripts/publish-landing.sh "a retired file that stays at the destination"
vfix; out=$(vrun normal); rc=$?
[ "$rc" = 0 ] && ok "a normal publish exits 0" || ko "a normal publish (rc $rc): $out"
case "$out" in *"file(s) published"*) ok "a normal publish prints the success count";; *) ko "a normal publish prints no count: $out";; esac
[ "$(cat "$vl/scripts/watch.sh")" = "watch v2" ] && [ -x "$vl/scripts/tool.sh" ] && ok "a normal publish lands the bytes and the executable bit" || ko "a normal publish landed wrong bytes or mode"
[ ! -e "$vl/scripts/publish-landing.sh" ] && ok "a normal publish drops the retired file" || ko "the retired file survived a normal publish"
[ "$(cat "$vl/scripts/models.json")" = '{"models": "mine"}' ] && [ -f "$vl/scripts/profiles/mine.sh" ] && ok "a normal publish keeps machine configuration" || ko "a normal publish touched machine configuration"
[ -s "$vl/PUBLISHED" ] && ok "a normal publish writes PUBLISHED" || ko "a normal publish wrote no PUBLISHED"
out=$(env -u SG_SQUAD -u SG_REPO bash "$src" --check "$vr" "$vl" 2>&1); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] && ok "the destination verifies clean after a normal publish" || ko "--check after a normal publish (rc $rc): $out"

# --- a non-git SG_SQUAD (the plugin cache) is checked against the commit the plugin was installed at (#1422) -----------
# env.sh defaults SG_SQUAD to the cache, which is no git repo, so the #605 check skipped it. The installed commit is read
# from installed_plugins.json; a squad/ or pyproject.toml change since that commit refuses the publish.
pr="$T/pcrepo"; mkdir -p "$pr/squad" "$pr/contrib/squad-goal/scripts"
printf 'caps v1\n' > "$pr/squad/caps.py"; printf 'p1\n' > "$pr/pyproject.toml"; printf 'skill P1\n' > "$pr/contrib/squad-goal/SKILL.md"
git -C "$pr" init -q -b main && git -C "$pr" add -A && git -C "$pr" commit -qm P1
p1=$(git -C "$pr" rev-parse HEAD)
cache="$T/cache/0.2.34"; mkdir -p "$cache/squad"; printf 'caps v1\n' > "$cache/squad/caps.py"
phome="$T/phome"; mkdir -p "$phome/.claude/plugins"
reg() { printf '{"version":2,"plugins":{"agent-squad@agent-squad":[{"scope":"user","installPath":"%s","version":"0.2.34","gitCommitSha":"%s"}]}}\n' "$cache" "$1" > "$phome/.claude/plugins/installed_plugins.json"; }
pl="$T/plive"; mkdir -p "$pl/scripts"; printf 'skill v0\n' > "$pl/SKILL.md"
prun() { env -u SG_REPO HOME="$phome" SG_SQUAD="$cache" bash "$src" "$pr/contrib/squad-goal" "$pl" 2>&1; }
printf 'caps v2\n' > "$pr/squad/caps.py"; git -C "$pr" commit -qam "squad change"
printf 'skill P2\n' > "$pr/contrib/squad-goal/SKILL.md"; git -C "$pr" commit -qam "skill change"
p2=$(git -C "$pr" rev-parse HEAD)
reg "$p1"; before=$(snap "$pl")
out=$(prun); rc=$?
[ "$rc" = 3 ] && ok "a cache installed before a squad/ change refuses (exit 3)" || ko "a stale cache did not refuse with 3 (rc $rc): $out"
[ "$before" = "$(snap "$pl")" ] && ok "the stale-cache refusal writes nothing" || ko "the stale-cache refusal wrote to the live skill"
printf '%s' "$out" | /usr/bin/grep -q "${p1:0:8}" && printf '%s' "$out" | /usr/bin/grep -q "${p2:0:8}" \
  && printf '%s' "$out" | /usr/bin/grep -q 'claude plugin uninstall agent-squad@agent-squad && claude plugin install agent-squad@agent-squad' \
  && ok "the refusal names both commits and the reinstall command" || ko "the stale-cache refusal line: $out"
printf '{"plugins":{}}\n' > "$phome/.claude/plugins/installed_plugins.json"
out=$(prun); rc=$?
[ "$rc" = 3 ] && [ "$before" = "$(snap "$pl")" ] && ok "an unreadable installed sha refuses" || ko "an unreadable sha (rc $rc): $out"
reg "$p2"
out=$(prun); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$pl/SKILL.md")" = "skill P2" ] && ok "a cache installed at HEAD publishes" || ko "a cache at HEAD (rc $rc): $out"
printf 'skill P3\n' > "$pr/contrib/squad-goal/SKILL.md"; git -C "$pr" commit -qam "outside squad"
out=$(prun); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$pl/SKILL.md")" = "skill P3" ] && ok "a change outside squad/ publishes" || ko "a change outside squad/ (rc $rc): $out"

# --- an open loop-bug:publish holds a publish to its reviewed sha (#1980) ----------------------------
# The hold reads the open issues of the origin repository. Only the reviewed sha its issue names passes; a missing or
# other sha is refused, and an unreadable read is refused too.
hb="$T/hold"; hr="$hb/repo"; hl="$hb/live"; hbin="$hb/bin"; mkdir -p "$hr/contrib/squad-goal/scripts" "$hl/scripts" "$hbin" "$T/hst"
git init -q -b main "$hr"; git -C "$hr" remote add origin https://github.com/example/squad.git
printf 'skill h1\n' > "$hr/contrib/squad-goal/SKILL.md"; git -C "$hr" add -A; git -C "$hr" commit -qm base
hsha=$(git -C "$hr" rev-parse HEAD)
printf 'skill h2\n' > "$hr/contrib/squad-goal/SKILL.md"; git -C "$hr" add -A; git -C "$hr" commit -qm "Fix the hold path"
hfix=$(git -C "$hr" rev-parse HEAD)
printf 'skill v0\n' > "$hl/SKILL.md"
cat > "$hbin/gh" <<EOF
#!/bin/bash
case "\$*" in
  *state=open*) [ -e "$hb/hold.fail" ] && exit 1; cat "$hb/hold.json"; exit 0;;
esac
printf '%s\n' "\$*" >> "$hb/gh.log"; exit 0
EOF
chmod +x "$hbin/gh"
hrun() { env -u SG_SQUAD -u SG_REPO SG_STATE="$T/hst" PATH="$hbin:$PATH" bash "$src" "$hr/contrib/squad-goal" "$hl" 2>&1; }
hset() { printf '%s\n' "$1" > "$hb/hold.json"; }
hset '[{"number":596,"title":"a publish defect","body":"no sha line here","labels":[{"name":"loop-bug:publish"}]}]'
out=$(hrun); rc=$?
[ "$rc" = 3 ] && ok "H1: a publish hold with no reviewed sha refuses (exit 3)" || ko "H1: rc $rc: $out"
case "$out" in *"#596"*) ok "H1: the refusal names the holding issue";; *) ko "H1: the refusal does not name #596: $out";; esac
[ "$(cat "$hl/SKILL.md")" = "skill v0" ] && ok "H1: a held publish writes nothing" || ko "H1: a held publish wrote the live skill"
hset "[{\"number\":596,\"title\":\"a publish defect\",\"body\":\"reviewed-sha: 0000000000000000000000000000000000000000\",\"labels\":[{\"name\":\"loop-bug:publish\"}]}]"
out=$(hrun); rc=$?
[ "$rc" = 3 ] && [ "$(cat "$hl/SKILL.md")" = "skill v0" ] && ok "H2: a sha the hold does not name refuses (exit 3)" || ko "H2: rc $rc: $out"
hset "[{\"number\":596,\"title\":\"a publish defect\",\"body\":\"Fixed here.\\nreviewed-sha: $hsha\",\"labels\":[{\"name\":\"loop-bug:publish\"}]}]"
out=$(hrun); rc=$?
[ "$rc" = 3 ] && ok "H3: the sha before the fix does not pass the hold" || ko "H3: rc $rc: $out"
hset "[{\"number\":596,\"title\":\"a publish defect\",\"body\":\"Fixed here.\\nreviewed-sha: $hfix\",\"labels\":[{\"name\":\"loop-bug:publish\"}]}]"
out=$(hrun); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$hl/SKILL.md")" = "skill h2" ] && ok "H4: the reviewed sha of the fix passes the hold" || ko "H4: the reviewed sha did not publish (rc $rc): $out"
touch "$hb/hold.fail"; printf 'skill v0\n' > "$hl/SKILL.md"
out=$(hrun); rc=$?
[ "$rc" = 3 ] && [ "$(cat "$hl/SKILL.md")" = "skill v0" ] && ok "H5: an unreadable issue list refuses (exit 3)" || ko "H5: rc $rc: $out"
case "$out" in *"could not be read"*) ok "H5: the refusal says the list could not be read";; *) ko "H5: refusal line: $out";; esac
rm -f "$hb/hold.fail"; hset '[]'
out=$(hrun); rc=$?
[ "$rc" = 0 ] && [ "$(cat "$hl/SKILL.md")" = "skill h2" ] && ok "H6: no open publish hold lets the publish run" || ko "H6: rc $rc: $out"

rm -rf "$T"; [ $fail = 0 ] && echo "test-publish-live: all checks green" || { echo "test-publish-live: failed"; exit 1; }
