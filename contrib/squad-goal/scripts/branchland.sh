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
# in success, skipped or neutral. A red, running or missing check is one `BRANCHLAND WAIT <branch>: <why>`; a push a ruleset
# refuses, a rebase that conflicts or a branch with nothing ahead is one `BRANCHLAND REFUSE <branch>: <why>`. Each is kept as a
# marker under $SG_STATE/auto, so it is said once per head and reason, and a refused push lands nothing.
#
# Each landing adds one row to $SG_STATE/landed.tsv (epoch, branch, landed sha, issue): the hub's Landed branches lane reads it.
#
# With GitHub Actions off a tip never gets a check run, so the pass would wait on `nocheck` forever (#385). While the local
# gate is on (SG_LOCAL_GATE, default on while Actions is off) a tip with zero check runs instead runs the gate -- ruff, mypy
# and the fast pytest battery (SG_LOCAL_GATE_CMD) -- in a clean checkout of that exact sha. A pass lands as a green check
# would, and the status comment names "local gate, CI off" and the sha it ran at; a fail lands nothing and records the
# failure on the issue. Only a tip with zero check runs reaches the gate: a red or running check never triggers it, and a
# red check never lands. SG_LOCAL_GATE=0 turns the local path off, as a later ticket does when Actions is back.
#
# The branch list is `git ls-remote` of $SG_LAND_REMOTE (no API read); every GitHub read goes through the throttle (ghcall.sh).
# watch.sh runs this under single.sh, so two passes never overlap.
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
export SG_GH_PASS=branchland
SG_GHREAD="python3 $here/ghthrottle.py"; . "$here/ghcall.sh"; . "$here/address.sh"
A="$SG_STATE/auto"; mkdir -p "$A"; land=${SG_LAND_BRANCH:-dev}; remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}
export GIT_TERMINAL_PROMPT=0
cd "$SG_STATE" || exit 0
# say <wait|refuse> <branch> <head> <kind> <reason>: one line per head and kind, kept as a marker so it is never repeated.
say() { local m="$A/branchland-$1-$(printf '%s' "$2" | tr '/' '_')-${3:0:9}-$4"; [ -e "$m" ] && return; touch "$m"
  case "$1" in wait) echo "BRANCHLAND WAIT $2: $5";; *) echo "BRANCHLAND REFUSE $2: $5";; esac; }
# The local gate (#385): CI off, so a tip with no check run is judged by ruff, mypy and the fast pytest battery in a clean
# checkout of that exact sha. SG_LOCAL_GATE=0 turns the path off; the command it runs is SG_LOCAL_GATE_CMD. It prints the
# last lines of the output and returns the command's status.
: "${SG_LOCAL_GATE:=1}"
: "${SG_LOCAL_GATE_CMD:=uv run --extra dev ruff check squad tests && uv run --extra dev mypy squad && uv run --extra dev pytest -n auto -m 'not heavy'}"
local_gate() { local br=$1 sha=$2 w rc out
  w=$(mktemp -d) || { echo "no scratch directory"; return 1; }
  /usr/bin/git -C "$w" init -q 2>/dev/null
  /usr/bin/git -C "$w" remote add origin "$remote" 2>/dev/null
  out=$(/usr/bin/git -C "$w" fetch -q origin "+refs/heads/$br:refs/remotes/origin/$br" 2>&1) || { echo "the fetch of $br failed: $(printf '%s' "$out" | tail -1)"; rm -rf "$w"; return 1; }
  out=$(/usr/bin/git -C "$w" checkout -q --detach "$sha" 2>&1) || { echo "the checkout of ${sha:0:9} failed: $(printf '%s' "$out" | tail -1)"; rm -rf "$w"; return 1; }
  out=$(cd "$w" && bash -c "$SG_LOCAL_GATE_CMD" 2>&1); rc=$?
  rm -rf "$w"
  printf '%s' "$out" | tail -5
  return $rc
}

heads=$(/usr/bin/git ls-remote --heads "$remote" 2>&1) || { echo "BRANCHLAND: the remote branch list is not readable: $(printf '%s' "$heads" | tail -1)"; exit 0; }
pl=$(gh_api "repos/$SG_REPO/pulls?per_page=100") || { echo "BRANCHLAND: the open-PR list is not readable: $pl"; exit 0; }
printf '%s' "$pl" | jq -e 'type=="array"' >/dev/null 2>&1 || { echo "BRANCHLAND: the open-PR list is not a list"; exit 0; }
prbranches=" $(printf '%s' "$pl" | jq -r '.[].head.ref' | tr '\n' ' ') "
# Only a branch whose issue is open lands (#367): the remote keeps many old work branches whose issue closed under a pull
# request or was dropped, and one of those would land its old commits in dev again.
oi=$(gh_api "repos/$SG_REPO/issues?state=open") || { echo "BRANCHLAND: the open-issue list is not readable: $oi"; exit 0; }
openiss=" $(printf '%s' "$oi" | jq -r '.[].number' 2>/dev/null | tr '\n' ' ') "
while read -r sha ref; do
  br=${ref#refs/heads/}
  i=$(printf '%s' "$br" | sed -nE 's#^(fix|gui)/([0-9]+)([^0-9].*)?$#\2#p'); [ -n "$i" ] || continue
  case "$prbranches" in *" $br "*) continue;; esac
  case "$openiss" in *" $i "*) ;; *) continue;; esac
  cr=$(gh_api "repos/$SG_REPO/commits/$sha/check-runs") || { echo "BRANCHLAND: the checks of $br are not readable: $cr"; continue; }
  total=$(printf '%s' "$cr" | jq -r '.check_runs|length' 2>/dev/null); via=check
  if [ "${total:-0}" -gt 0 ] 2>/dev/null; then
    [ "$(printf '%s' "$cr" | jq -r '[.check_runs[]|select(.status!="completed")]|length')" = 0 ] || { say wait "$br" "$sha" running "the check on ${sha:0:9} is still running"; continue; }
    bad=$(printf '%s' "$cr" | jq -r '[.check_runs[]|select(.conclusion!="success" and .conclusion!="skipped" and .conclusion!="neutral")|"\(.name) ended \(.conclusion)"]|first // ""')
    [ -z "$bad" ] || { say wait "$br" "$sha" red "the check on ${sha:0:9} is not green: $bad"; continue; }
  elif [ "$SG_LOCAL_GATE" = 1 ]; then
    # Zero check runs: with Actions off that is the normal case, so the local gate decides (#385). Only this path reaches
    # the gate: a red or a running check returned above.
    gk="$A/branchland-localgate-$(printf '%s' "$br" | tr '/' '_')-${sha:0:9}"
    [ -e "$gk.fail" ] && { say wait "$br" "$sha" gate "the local gate failed on ${sha:0:9}; the branch is not landed"; continue; }
    gd=$(local_gate "$br" "$sha"); grc=$?
    if [ "$grc" != 0 ]; then
      : > "$gk.fail"
      cat > "$(sg_cq_status "$i" "localgate$$")" <<EOG
Status update — branch $br @ ${sha:0:9}: local gate, CI off — ruff, mypy and pytest -m "not heavy" failed at $sha; the branch is not landed.
$(printf '%s' "$gd" | tail -3)
EOG
      say wait "$br" "$sha" gate "the local gate failed on ${sha:0:9}: $(printf '%s' "$gd" | tail -1)"
      continue
    fi
    rm -f "$gk.fail"
    cat > "$(sg_cq_status "$i" "localgate$$")" <<EOG
Status update — branch $br @ ${sha:0:9}: local gate, CI off — ruff, mypy and pytest -m "not heavy" passed at $sha.
EOG
    via=gate
  else
    say wait "$br" "$sha" nocheck "no check run on ${sha:0:9} yet"; continue
  fi
  # The review must have approved this tip as well (#357): branchreview.sh keeps its verdict and the sha it read under the branch key.
  rk="$SG_STATE/review-$(printf '%s' "$br" | tr '/' '_')"
  if [ "$(cat "$rk.sha" 2>/dev/null)" != "$sha" ] || [ ! -s "$rk.md" ]; then say wait "$br" "$sha" noreview "no review of ${sha:0:9} yet"; continue; fi
  head -1 "$rk.md" | grep -q 'VERDICT: APPROVE' || { say wait "$br" "$sha" changes "the review of ${sha:0:9} asked for changes or did not approve: $(head -1 "$rk.md")"; continue; }
  fl=$(SG_BASE=$land bash "$here/ffland.sh" 0 "$br" "$i" 2>/dev/null | tail -1)
  case "$fl" in
    "FFLAND "*) ms=${fl#FFLAND };;
    *) say refuse "$br" "$sha" push "not landed in $land: ${fl#FFLAND-FALLBACK }"; continue;;
  esac
  rm -f "$A"/branchland-*-"$(printf '%s' "$br" | tr '/' '_')"-*
  gh_send -X PATCH "repos/$SG_REPO/issues/$i" -f state=closed -f state_reason=completed >/dev/null || echo "BRANCHLAND: #$i did not close after the landing of $br"
  mkdir -p "$SG_STATE/cq"   # the delivery comment goes through the queue: cqflush.sh keeps one per issue, edited in place (#328)
  if [ "$via" = gate ]; then   # the local gate was the signal, not a check run (#385)
    cat > "$(sg_cq_delivery "$i" "branchland$$")" <<EOT
Closed #$i after landing $br in $land as $ms (fast-forward push, $br deleted). The local gate, CI off, passed at $sha. Refs #$i
EOT
  else
    cat > "$(sg_cq_delivery "$i" "branchland$$")" <<EOT
Closed #$i after landing $br in $land as $ms (fast-forward push, $br deleted). The check on ${sha:0:9} was green. Refs #$i
EOT
  fi
  $BOARD "$SG_OWNER" "$SG_PROJECT" "Done" "$i" >/dev/null
  printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$br" "$ms" "$i" >> "$SG_STATE/landed.tsv"   # the ledger the hub's Landed branches lane reads: the landed branch is deleted by the push, so the remote no longer names it
  echo "BRANCHLAND LANDED $br as $ms into $land, closed #$i"
done <<EOT
$(printf '%s\n' "$heads" | awk '{print $1, $2}')
EOT
exit 0
