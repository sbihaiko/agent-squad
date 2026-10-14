#!/bin/bash
# usage: branchland.sh      one pass over the pushed work branches: land the ones whose check is green in `dev` by a fast-forward push.
#
# A work branch is `fix/<n>-…` or `gui/<n>-…` on the remote: it names its issue. The pass reads the check-run of the branch tip by
# commit sha (never a pull request), and lands the branch in $SG_LAND_BRANCH (default dev) through ffland.sh, which rebases first when
# that branch moved. Then the issue is closed through the API, the one delivery comment is queued (cqflush.sh posts it), and the
# card moves to Done. It runs beside prland.sh until the pull request path is removed: a branch with an open pull request is
# prland's and is left alone here.
#
# A branch is landed when, and only when, it names an open issue, has no open pull request, the review of its tip approved (#357),
# and every check run on its tip finished
# in success, skipped or neutral, or, with zero check runs while CI is off, the local gate passed at the tip rebased onto $SG_LAND_BRANCH (#385). A red, running or missing check is one `BRANCHLAND WAIT <branch>: <why>`; a push a ruleset
# refuses, a rebase that conflicts or a branch with nothing ahead is one `BRANCHLAND REFUSE <branch>: <why>`. Each is kept as a
# marker under $SG_STATE/auto, so it is said once per head and reason, and a refused push lands nothing.
#
# Each landing adds one row to $SG_STATE/landed.tsv (epoch, branch, landed sha, issue): the hub's last-landing fact reads it.
#
# The branch list is `git ls-remote` of $SG_LAND_REMOTE (no API read); every GitHub read goes through the throttle (ghcall.sh).
# watch.sh runs this under single.sh, so two passes never overlap.
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
export SG_GH_PASS=branchland
SG_GHREAD="python3 $here/ghthrottle.py"; . "$here/ghcall.sh"; . "$here/address.sh"; . "$here/workbranches.sh"
A="$SG_STATE/auto"; mkdir -p "$A"; land=${SG_LAND_BRANCH:-dev}; remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}
export GIT_TERMINAL_PROMPT=0
cd "$SG_STATE" || exit 0
# say <wait|refuse> <branch> <head> <kind> <reason>: one line per head and kind, kept as a marker so it is never repeated.
say() { local m="$A/branchland-$1-$(printf '%s' "$2" | tr '/' '_')-${3:0:9}-$4"; [ -e "$m" ] && return; touch "$m"
  case "$1" in wait) echo "BRANCHLAND WAIT $2: $5";; *) echo "BRANCHLAND REFUSE $2: $5";; esac; }
# While GitHub Actions is off, a tip has zero check runs (#385). The local gate is then the green signal: ruff, mypy and the fast
# battery in a clean worktree of the tip. SG_LOCAL_GATE=no turns it off (default yes while Actions is off); SG_LOCAL_GATE_CMD
# replaces the gate command (tests stub it). A red or running check never starts it.
py=${SG_GATE_PY:-$SG_CHECKOUT/.venv/bin/python}; [ -x "$py" ] || py=python3
gatecmd=${SG_LOCAL_GATE_CMD:-"'$py' -m ruff check squad tests && '$py' -m mypy squad && '$py' -m pytest -p no:deepeval -m 'not heavy'"}
# A failed gate reruns only what failed, once: a test that machine load slowed past a line, or that raced a busy port, passes
# alone; a real red fails again. ruff and mypy do not depend on load, so a red of theirs fails the rerun as well.
rerun=${SG_LOCAL_GATE_RERUN_CMD:-"'$py' -m ruff check squad tests && '$py' -m mypy squad && '$py' -m pytest -p no:deepeval -m 'not heavy' --lf --lfnf=none"}
# localgate <branch> <sha> <issue>: exit 0 when the gate passed at <sha> rebased onto $land, which is what lands: a red the branch
# inherited from an old base is fixed on $land, never on the branch. A rebase that conflicts gates the bare tip (ffland refuses it).
# The result goes to the status comment of the issue. A failed gate is run once per head and $land head: its wait marker stops a
# second run until $land moves.
localgate() { local br=$1 sha=$2 i=$3 wt out rc base
  base=$(/usr/bin/git ls-remote "$remote" "refs/heads/$land" 2>/dev/null | awk '{print substr($1,1,7)}')
  [ -e "$A/branchland-wait-$(printf '%s' "$br" | tr '/' '_')-${sha:0:9}-gate$base" ] && return 1
  wt=$(mktemp -d "${TMPDIR:-/tmp}/sg-gate.XXXXXX")
  if ! /usr/bin/git -C "$SG_CHECKOUT" fetch -q "$remote" "refs/heads/$br" "+refs/heads/$land:refs/sg-gate/$land" >/dev/null 2>&1 || ! /usr/bin/git -C "$SG_CHECKOUT" worktree add -q --detach "$wt/tip" "$sha" >/dev/null 2>&1; then
    rm -rf "$wt"; say wait "$br" "$sha" gateco "the local gate has no clean checkout of ${sha:0:9}"; return 1; fi
  /usr/bin/git -C "$wt/tip" -c user.email=gate@squad -c user.name=squad-gate rebase -q "refs/sg-gate/$land" >/dev/null 2>&1 || /usr/bin/git -C "$wt/tip" rebase --abort >/dev/null 2>&1
  out=$(cd "$wt/tip" && PYTHONPATH=. bash -c "$gatecmd" 2>&1); rc=$?
  [ "$rc" = 0 ] || { out="$out
--- the rerun of what failed ---
$(cd "$wt/tip" && PYTHONPATH=. bash -c "$rerun" 2>&1)"; rc=$?; }
  /usr/bin/git -C "$SG_CHECKOUT" worktree remove --force "$wt/tip" >/dev/null 2>&1; rm -rf "$wt"; mkdir -p "$SG_STATE/cq"
  if [ "$rc" = 0 ]; then
    printf 'Status update — branch %s @ %s: local gate, CI off: passed at %s (ruff, mypy, pytest -m "not heavy").\n' "$br" "${sha:0:9}" "$sha" > "$(sg_cq_status "$i" "branchland$$")"
    return 0; fi
  { printf 'Status update — branch %s @ %s: local gate, CI off: failed at %s (exit %s). The branch does not land.\n\n```\n' "$br" "${sha:0:9}" "$sha" "$rc"
    printf '%s\n' "$out" | grep -E '^(FAILED|E  |ERROR)' | sort -u | head -20; printf '%s\n' "$out" | tail -30; printf '```\n'; } > "$(sg_cq_status "$i" "branchland$$")"
  say wait "$br" "$sha" "gate$base" "the local gate (CI off) failed on ${sha:0:9} rebased onto $land ${base:-?}: $(printf '%s' "$out" | tail -1)"; return 1; }

# The work branches (workbranches.sh). Only a branch whose issue is open lands (#367): the remote keeps many old work branches whose
# issue closed under a pull request or was dropped, and one of those would land its old commits in dev again.
sg_work_branches "$remote" || { echo "BRANCHLAND: $wb_err"; exit 0; }
while read -r sha br i; do
  [ -n "$br" ] || continue
  cr=$(gh_api "repos/$SG_REPO/commits/$sha/check-runs") || { echo "BRANCHLAND: the checks of $br are not readable: $cr"; continue; }
  total=$(printf '%s' "$cr" | jq -er '.check_runs|length' 2>/dev/null) || { echo "BRANCHLAND: the checks of $br are not a check-run list"; continue; }
  gate=
  if [ "$total" = 0 ]; then
    [ "${SG_LOCAL_GATE:-yes}" = yes ] || { say wait "$br" "$sha" nocheck "no check run on ${sha:0:9} yet"; continue; }
    gate=1   # zero check runs: CI is off, and the local gate runs once the review approved
  else
    [ "$(printf '%s' "$cr" | jq -r '[.check_runs[]|select(.status!="completed")]|length')" = 0 ] || { say wait "$br" "$sha" running "the check on ${sha:0:9} is still running"; continue; }
    bad=$(printf '%s' "$cr" | jq -r '[.check_runs[]|select(.conclusion!="success" and .conclusion!="skipped" and .conclusion!="neutral")|"\(.name) ended \(.conclusion)"]|first // ""')
    [ -z "$bad" ] || { say wait "$br" "$sha" red "the check on ${sha:0:9} is not green: $bad"; continue; }
  fi
  # The review must have approved this tip as well (#357): branchreview.sh keeps its verdict and the sha it read under the branch key.
  rk="$SG_STATE/review-$(printf '%s' "$br" | tr '/' '_')"
  if [ "$(cat "$rk.sha" 2>/dev/null)" != "$sha" ] || [ ! -s "$rk.md" ]; then say wait "$br" "$sha" noreview "no review of ${sha:0:9} yet"; continue; fi
  head -1 "$rk.md" | grep -q 'VERDICT: APPROVE' || { say wait "$br" "$sha" changes "the review of ${sha:0:9} asked for changes or did not approve: $(head -1 "$rk.md")"; continue; }
  if [ -n "$gate" ]; then localgate "$br" "$sha" "$i" || continue; green="The local gate (CI off) passed on ${sha:0:9}."
  else green="The check on ${sha:0:9} was green."; fi
  fl=$(SG_BASE=$land bash "$here/ffland.sh" 0 "$br" "$i" 2>/dev/null | tail -1)
  case "$fl" in
    "FFLAND "*) ms=${fl#FFLAND };;
    *) say refuse "$br" "$sha" push "not landed in $land: ${fl#FFLAND-FALLBACK }"; continue;;
  esac
  rm -f "$A"/branchland-*-"$(printf '%s' "$br" | tr '/' '_')"-*
  gh_send -X PATCH "repos/$SG_REPO/issues/$i" -f state=closed -f state_reason=completed >/dev/null || echo "BRANCHLAND: #$i did not close after the landing of $br"
  mkdir -p "$SG_STATE/cq"   # the delivery comment goes through the queue: cqflush.sh keeps one per issue, edited in place (#328)
  cat > "$(sg_cq_delivery "$i" "branchland$$")" <<EOT
Closed #$i after landing $br in $land as $ms (fast-forward push, $br deleted). $green Refs #$i
EOT
  $BOARD "$SG_OWNER" "$SG_PROJECT" "Done" "$i" >/dev/null
  printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$br" "$ms" "$i" >> "$SG_STATE/landed.tsv"   # the ledger the hub's last-landing fact reads: the landed branch is deleted by the push, so the remote no longer names it. The board draws no lane for it (#384): a landed branch is read from its issue card
  echo "BRANCHLAND LANDED $br as $ms into $land, closed #$i"
done <<EOT
$wb_list
EOT
exit 0
