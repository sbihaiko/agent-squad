#!/bin/bash
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

rm -rf "$T"; [ $fail = 0 ] && echo "test-publish-live: all checks green" || { echo "test-publish-live: failed"; exit 1; }
