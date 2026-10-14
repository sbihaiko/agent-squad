#!/bin/bash
# usage: prland.sh      one pass over the open pull requests: merge the ones nothing holds, whose check failed on nothing but the baseline.
#
# The loop lands finished work through pull requests. A PR whose check
# is red sat open forever, and its issue in Code Complete. The check IS red, constantly: `tests/test_gui_test_e2e.py` needs
# a MesenAI checkout the runner never has, and two app-reaping cases run only on macOS (#36). So this pass never merges on
# "checks green" and never on "checks red": it reads the failed test set out of the job log and merges when that set lies
# inside the baseline file ($SG_CI_BASELINE, default contrib/squad-goal/ci-baseline.txt in $SG_CHECKOUT, one node id per
# line with its reason). One failure the baseline does not hold is refused, and the refusal names it.
#
# A PR is merged when, and only when: it is open, not a draft, on $SG_BASE, and names its issue (fix/<n>-… or Refs #n);
# no live run names its head branch and no worktree holds that branch with uncommitted changes (the pass does: active-runs.txt, the recent run records, `git worktree list`); its check run finished; every failed test is in
# the baseline; GitHub reports it mergeable. Then ffland.sh rebases the branch on $SG_BASE and pushes it as a fast-forward: no merge API call, the commit keeps Refs (owner, #330),
# and `git push origin :<branch>` deletes the branch; the issue is then closed through the API. A push a ruleset refuses prints
# `PRLAND FALLBACK` and falls back to `gh pr merge --squash --delete-branch`, the issue closed, and one closing comment that names
# the pull request and merge sha. Anything else is one `PRLAND WAIT #<n>` (it may resolve on its own) or one
# `PRLAND REFUSE #<n>` (a person decides) per head and reason, kept as a marker under $SG_STATE/auto so it is never
# repeated for the same head (a per-head marker pattern).
#
# Every GitHub read goes through the throttle (ghthrottle.py behind ghcall.sh): a cooldown held by any reader holds here
# too, and a refused list is printed and is never read as "no PRs". The one direct `gh` reach is the merge itself
# (`gh pr merge`, counted in gh-direct-calls.txt); it runs from $SG_STATE, never from a checkout, so `--delete-branch`
# touches the remote branch only. watch.sh runs this under single.sh, so two passes never overlap.
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
here="$(cd "$(dirname "$0")" && pwd)"
export SG_GH_PASS=prland
SG_GHREAD="python3 $here/ghthrottle.py"; . "$here/ghcall.sh"
G="/usr/bin/git -C $SG_CHECKOUT"; A="$SG_STATE/auto"; mkdir -p "$A"; now=$(date +%s)
cd "$SG_STATE" || exit 0   # the pass runs outside every checkout: `gh pr merge --delete-branch` then touches the remote branch only
BL=${SG_CI_BASELINE:-$SG_CHECKOUT/contrib/squad-goal/ci-baseline.txt}
baseline=" $(sed 's/#.*//' "$BL" 2>/dev/null | awk 'NF{print $1}' | tr '\n' ' ') "   # no file: an empty baseline, so any red is refused

# The branches a live run is working on: a run listed in active-runs.txt, or one whose record moved inside the orphan window,
# names the branch it was launched for in its request (the request records the branch).
runs=$( { cat "$SG_STATE/active-runs.txt" 2>/dev/null; ls -t "$SG_CHECKOUT/runs" 2>/dev/null | head -60; } | sort -u)
livebrs=" $(for r in $runs; do
  d=$r; [ "${r#/}" = "$r" ] && d=$SG_CHECKOUT/runs/$r   # no `case` here: bash 3.2 cannot parse one inside $( )
  f="$d/record.jsonl"; [ -f "$f" ] || continue
  grep -qx "$r" "$SG_STATE/active-runs.txt" 2>/dev/null || [ $(( now - $(mtime "$f") )) -lt $(( ${SG_FORK_ORPHAN_MINUTES:-20} * 60 )) ] || continue
  grep -oE '(fix|gui)/[0-9]+[A-Za-z0-9._-]*' "$d/request.txt" 2>/dev/null | sort -u | tr '\n' ' '
done) "
# What holds a branch: a live run that names it, or a worktree that has it checked out with uncommitted tracked changes.
# A clean checkout holds nothing: the merge happens on GitHub and no local tree is touched.
held() { local w
  case "$livebrs" in *" $1 "*) echo "a live run names it"; return;; esac
  w=$($G worktree list --porcelain 2>/dev/null | awk -v x="refs/heads/$1" '/^worktree /{p=$2} $0=="branch "x{print p}')
  [ -n "$w" ] || return
  [ -n "$(/usr/bin/git -C "$w" status --porcelain --untracked-files=no 2>/dev/null)" ] && echo "it is checked out at $w with uncommitted changes"; }
# say <wait|refuse> <pr> <head> <kind> <reason>: one line per head and kind, kept as a marker so it is never repeated.
say() { local m="$A/prland-$1-$2-${3:0:9}-$4"; [ -e "$m" ] && return; touch "$m"
  case "$1" in wait) echo "PRLAND WAIT #$2: $5";; *) echo "PRLAND REFUSE #$2: $5";; esac; }

# The failed test set of one job, from its log: pytest's `FAILED <nodeid> - <reason>` lines, one node id per line.
failed_tests() { grep -oE '(FAILED|ERROR) tests/[^ ]+::[^ ]+' | sed -E 's/^(FAILED|ERROR) //' | sort -u; }

pl=$(gh_api "repos/$SG_REPO/pulls?per_page=100") || { echo "PRLAND: the open-PR list is not readable: $pl"; exit 0; }
printf '%s' "$pl" | jq -e 'type=="array"' >/dev/null 2>&1 || { echo "PRLAND: the open-PR list is not a list"; exit 0; }
for n in $(printf '%s' "$pl" | jq -r '.[].number'); do
  case "$n" in ''|*[!0-9]*) continue;; esac
  p=$(printf '%s' "$pl" | jq -c --argjson n "$n" '.[]|select(.number==$n)')
  br=$(printf '%s' "$p" | jq -r '.head.ref'); sha=$(printf '%s' "$p" | jq -r '.head.sha'); base=$(printf '%s' "$p" | jq -r '.base.ref')
  draft=$(printf '%s' "$p" | jq -r '.draft'); body=$(printf '%s' "$p" | jq -r '.body // ""')
  [ "$base" = "${SG_BASE:-main}" ] || { say refuse "$n" "$sha" base "its base is $base, not ${SG_BASE:-main}"; continue; }
  [ "$draft" = true ] && { say refuse "$n" "$sha" draft "it is a draft"; continue; }
  i=$(printf '%s' "$br" | sed -nE 's#^fix/([0-9]+)-.*#\1#p'); [ -n "$i" ] || i=$(printf '%s' "$body" | grep -oE 'Refs #[0-9]+' | head -1 | tr -dc 0-9)
  [ -n "$i" ] || { say refuse "$n" "$sha" issue "$br names no issue (fix/<n>-… or Refs #n)"; continue; }
  why=$(held "$br"); [ -z "$why" ] || { say wait "$n" "$sha" held "$br is held: $why"; continue; }
  cr=$(gh_api "repos/$SG_REPO/commits/$sha/check-runs") || { echo "PRLAND: the checks of #$n are not readable: $cr"; continue; }
  total=$(printf '%s' "$cr" | jq -r '.check_runs|length' 2>/dev/null); [ "${total:-0}" -gt 0 ] 2>/dev/null || { say wait "$n" "$sha" nocheck "no check run on ${sha:0:9} yet"; continue; }
  [ "$(printf '%s' "$cr" | jq -r '[.check_runs[]|select(.status!="completed")]|length')" = 0 ] || { say wait "$n" "$sha" running "the check on ${sha:0:9} is still running"; continue; }
  bad=""; outside=""
  for j in $(printf '%s' "$cr" | jq -r '.check_runs[]|select(.conclusion!="success" and .conclusion!="skipped" and .conclusion!="neutral")|"\(.id):\(.conclusion):\(.name|gsub(" ";"_"))"'); do
    id=${j%%:*}; rest=${j#*:}; con=${rest%%:*}; name=${rest#*:}
    [ "$con" = failure ] || { bad="check $name ended $con"; break; }
    job=$(gh_api "repos/$SG_REPO/actions/jobs/$id") || { bad="the job details of check $name are not readable: $job"; break; }
    detail=$(gh_api "repos/$SG_REPO/check-runs/$id") || { bad="the check details of check $name are not readable: $detail"; break; }
    stop=$(printf '%s\n%s\n' "$job" "$detail" | jq -sc '.[0:2]' | python3 "$here/ci_state.py")
    case "$stop" in
      cannot-run$'\t'*) say wait "$n" "$sha" cannot-run "CI cannot run: ${stop#*$'\t'}"; bad=cannot-run; break;;
    esac
    log=$(gh_get "repos/$SG_REPO/actions/jobs/$id/logs") || { bad="the log of check $name is not readable: $log"; break; }
    ft=$(printf '%s' "$log" | failed_tests)
    [ -n "$ft" ] || { bad="check $name failed and its log names no failed test (ruff, mypy or an error before the suite)"; break; }
    for t in $ft; do case "$baseline" in *" $t "*) ;; *) outside="$outside $t";; esac; done
  done
  [ "$bad" = cannot-run ] && continue
  [ -z "$bad" ] || { say refuse "$n" "$sha" check "$bad"; continue; }
  [ -z "$outside" ] || { say refuse "$n" "$sha" red "failed outside the baseline:$outside"; continue; }
  # the facts that gate the write, read live, one request: still open, still not a draft, and mergeable
  live=$(gh_api "repos/$SG_REPO/pulls/$n") || { echo "PRLAND: #$n is not readable before its merge: $live"; continue; }
  st=$(printf '%s' "$live" | jq -r '"\(.state) \(.draft) \(.mergeable_state)"')
  case "$st" in "open false clean"|"open false unstable"|"open false has_hooks"|"open false unknown") ;;
    *) say refuse "$n" "$sha" merge "not mergeable as it stands (state, draft, mergeable_state: $st)"; continue;; esac
  # the landing: a git fast-forward push (no merge API call). A refused push falls back to the API merge. Either way the commit keeps
  # Refs and the issue is closed by the API with one comment (owner, #330).
  fl=$(bash "$here/ffland.sh" "$n" "$br" "$i" 2>/dev/null | tail -1)
  case "$fl" in
    "FFLAND "*) ms=${fl#FFLAND }; how="fast-forward push";;
    *) echo "PRLAND FALLBACK #$n: ${fl#FFLAND-FALLBACK }; merging through the API instead"; how=squash
      out=$(gh pr merge "$n" --repo "$SG_REPO" --squash --delete-branch 2>&1) || { say refuse "$n" "$sha" merged "GitHub did not merge it: $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"; continue; }
      ms=$(gh_api "repos/$SG_REPO/pulls/$n" -q '.merge_commit_sha // ""') || ms="";;
  esac
  rm -f "$A"/prland-*-"$n"-*
  gh_send -X PATCH "repos/$SG_REPO/issues/$i" -f state=closed -f state_reason=completed >/dev/null || { echo "PRLAND: #$i did not close after the merge of #$n"; continue; }
  gh_send -X POST "repos/$SG_REPO/issues/$i/comments" -F body=@- >/dev/null <<EOT
Closed #$i after merging PR #$n into ${SG_BASE:-main} as ${ms:-(sha not readable)} ($how, $br deleted). The check failed on the known baseline only (contrib/squad-goal/ci-baseline.txt, #36). Refs #$i
EOT
  echo "PRLAND MERGED #$n as ${ms:-?} into ${SG_BASE:-main} by $how, closed #$i"
done
exit 0
