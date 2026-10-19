#!/bin/bash
# usage: check-tdd-red.sh [<base>..<head>]        (no argument: the run's own commits)
#
# ADR-0136 §1-§4, the guard the ADR names. A defect is fixed test-first and the red is measured at the parent:
# this check takes every test the range adds or changes, puts that test's own version into a scratch checkout of
# the commit BEFORE the one that carried it, and runs it there. The test must FAIL at that parent -- a test that
# passes there is not this fix's red, and a range that carries no test at all is the same refusal (§2/§4).
#
# The range's files are the diff from the merge-base of <base> and <head>, so a base that moved on adds none (#640).
# A test the merge-base already holds that the range changes and that still passes at its parent (a fixture list, say)
# is a companion: "TDD-RED OK (companion)" when the range carries at least one test red at its parent, else refused.
# An added test that passes at its parent is always refused.
#
# A commit that squashes its test and its code into one is the case where the range never held the red the
# parent would measure (the test only ever ran green in this history), so it is accepted only with the red run
# recorded in the deliverable (§3). The recorded red is read from $SG_TDD_RED, from the deliverable body
# ($PREPUSH_BODY, which prepush.sh exports from the PR body), from the range's own commit messages, and, for
# fork-only work where the deliverable is a local commit and the red lives on the issue, from a "TDD RED"
# comment on any issue the range names. Absent a record, the squash is refused.
#
# The tree under check is never written to -- no checkout, no stash, no reset: the measurement happens in a
# throwaway worktree of the parent commit, removed as the check ends. Run it from $SG_PREPUSH_CHECKS, which
# runs it in the tree being pushed.
#
# Env: SG_TDD_RANGE, SG_TDD_TEST_RE (which paths are tests), SG_TDD_TEST_CMD (how to run one, {} = its path;
# defaults: bash for .sh, python3 -m pytest for .py -- anything else must name its own command), SG_TDD_RED
# (an explicit recorded red run), SG_BASE, SG_REPO, PREPUSH_RANGE, PREPUSH_BODY.
# Prints one "TDD-RED ..." line per test; exit 0 when every test is red at its parent, 1 when any is refused.
set -u
G=/usr/bin/git

TEST_RE=${SG_TDD_TEST_RE:-'(^|/)tests?/[^/]+\.(py|sh|js|mjs|ts|tsx|cs|rs|go|rb|java|kt|php|c|cc|cpp)$|(^|/)test[_.-][^/]+\.(py|sh|js|mjs|ts|tsx|cs|rs|go|rb|java|kt|php|c|cc|cpp)$|(^|/)[^/]+[_.-]tests?\.(py|sh|js|mjs|ts|tsx|cs|rs|go|rb|java|kt|php|c|cc|cpp)$'}

ext_run() {   # ext_run <test path> -> the command that runs it, empty when the extension is not a known test
  case "$1" in
    *.sh) echo "bash {}" ;;
    *.py) echo "python3 -m pytest -q {}" ;;
    *)    echo "" ;;
  esac
}

range=${1:-${SG_TDD_RANGE:-${PREPUSH_RANGE:-}}}
if [ -z "$range" ]; then
  b=$($G merge-base HEAD "origin/${SG_BASE:-main}" 2>/dev/null)
  [ -z "$b" ] && b=$($G rev-parse -q --verify HEAD^ 2>/dev/null)
  [ -z "$b" ] && { echo "TDD-RED SKIP: no parent to measure against (one commit and no origin/${SG_BASE:-main})"; exit 0; }
  range="$b..HEAD"
fi
base=${range%%..*}; head=${range##*..}; [ -z "$head" ] && head=HEAD
$G rev-parse -q --verify "$head^{commit}" >/dev/null 2>&1 || { echo "TDD-RED SKIP: $head is not a commit in this tree"; exit 0; }
[ -n "$($G rev-list --reverse "$base..$head" 2>/dev/null)" ] || { echo "TDD-RED OK: no commit in $base..$head"; exit 0; }

# The range's files come from its fork point: a two-dot diff against a base that moved on also lists the base's own
# changes, and none of them is this range's (#640).
mb=$($G merge-base "$base" "$head" 2>/dev/null); [ -z "$mb" ] && mb=$base
tests=""
for f in $($G diff --name-only "$mb" "$head" 2>/dev/null); do
  printf '%s' "$f" | grep -qE "$TEST_RE" || continue
  $G cat-file -e "$head:$f" 2>/dev/null || continue      # a deleted test is not this fix's test
  tests="$tests $f"
done

# red_recorded: the deliverable carries the red run this range's own history cannot show (ADR-0136 §3)
red_recorded() {
  [ -n "${SG_TDD_RED:-}" ] && return 0
  case "${PREPUSH_BODY:-}" in *"TDD RED"*) return 0;; esac
  msgs=$($G log --format=%B "$base..$head" 2>/dev/null)
  case "$msgs" in *"TDD RED"*) return 0;; esac
  { [ -n "${SG_REPO:-}" ] && command -v gh >/dev/null 2>&1; } || return 1
  for n in $(printf '%s\n' "$msgs" | grep -oE '(Refs|Closes|Fixes) ?#[0-9]+|\(#[0-9]+\)' | grep -oE '[0-9]+' | sort -u); do
    gh api "repos/$SG_REPO/issues/$n/comments" --paginate -q '.[].body' 2>/dev/null | grep -q 'TDD RED' && return 0
  done
  return 1
}

fails=0; reds=0; companions=""
if [ -z "$tests" ]; then
  if red_recorded; then
    echo "TDD-RED OK: $base..$head carries no test, and the red run is recorded in the deliverable (ADR-0136 §3)"
  else
    echo "TDD-RED REFUSED: $base..$head adds or changes no test and records no red run: a fix carries the test that fails at its parent (ADR-0136 §1/§2), and where that test is missing the red run is recorded in the deliverable (§3)"
    fails=$((fails + 1))
  fi
else
  scratch=$(mktemp -d); wt="$scratch/tree"; trap 'rm -rf "$scratch"' EXIT
  for t in $tests; do
    c=$($G rev-list --reverse "$base..$head" -- "$t" | head -n1)    # the commit that carried the test
    p=$($G rev-parse -q --verify "$c^" 2>/dev/null)
    if [ -z "$p" ]; then
      echo "TDD-RED REFUSED: $t is added by $($G log -1 --format=%h "$c"), the first commit of the range, so no parent of it can be measured; record the red run in the deliverable (ADR-0136 §3)"
      fails=$((fails + 1)); continue
    fi
    squash=""
    for f in $($G show --name-only --format= "$c"); do
      printf '%s' "$f" | grep -qE "$TEST_RE" && continue
      case "$f" in *.md) continue;; esac
      squash=$f; break
    done
    if ! $G worktree add -q --detach "$wt" "$p" 2>/dev/null; then
      echo "TDD-RED REFUSED: cannot check out $($G log -1 --format=%h "$p") in a scratch worktree to measure $t"
      fails=$((fails + 1)); continue
    fi
    mkdir -p "$wt/$(dirname "$t")"
    if ! $G show "$head:$t" > "$wt/$t"; then
      echo "TDD-RED REFUSED: cannot read $t at $($G log -1 --format=%h "$head")"
      fails=$((fails + 1)); $G worktree remove --force "$wt" >/dev/null 2>&1; continue
    fi
    if [ -n "${SG_TDD_TEST_CMD:-}" ]; then cmd=$SG_TDD_TEST_CMD; else cmd=$(ext_run "$t"); fi
    if [ -z "$cmd" ]; then
      echo "TDD-RED REFUSED: no command to run $t -- set SG_TDD_TEST_CMD in the profile, with {} standing for the test path"
      fails=$((fails + 1)); $G worktree remove --force "$wt" >/dev/null 2>&1; continue
    fi
    out=$(cd "$wt" && eval "${cmd//\{\}/$t}" 2>&1); rc=$?
    $G worktree remove --force "$wt" >/dev/null 2>&1
    [ "$rc" != 0 ] && reds=$((reds + 1))
    if [ "$rc" = 0 ] && $G cat-file -e "$mb:$t" 2>/dev/null; then
      # a test the fork point holds, changed beside the fix (a fixture list, say): it has no red of its own, so it is a
      # companion when the range carries a red, and refused when it does not (#640)
      companions="$companions$t|$($G log -1 --format='%h %s' "$p")
"
    elif [ "$rc" = 0 ]; then
      echo "TDD-RED REFUSED: $t passes at $($G log -1 --format='%h %s' "$p"), the parent of the commit $($G log -1 --format=%h "$c") that carried the test: a test that already passed at the parent is not this fix's red (ADR-0136 §2/§4)"
      [ -n "$out" ] && printf '%s\n' "$out" | tail -3 | sed 's/^/    /'
      fails=$((fails + 1))
    elif [ -n "$squash" ]; then
      if red_recorded; then
        echo "TDD-RED OK: $t fails at $($G log -1 --format=%h "$p"), its parent, and the squash with $squash records the red run in the deliverable (ADR-0136 §3)"
      else
        echo "TDD-RED REFUSED: $t and $squash landed in the same commit $($G log -1 --format=%h "$c") -- a squash, so this range never held the red the parent measures; record the red run in the deliverable (ADR-0136 §3)"
        fails=$((fails + 1))
      fi
    else
      echo "TDD-RED OK: $t fails at $($G log -1 --format='%h %s' "$p"), the parent of the commit that carried it"
    fi
  done
  while IFS='|' read -r t at; do
    [ -n "$t" ] || continue
    if [ "$reds" -gt 0 ]; then
      echo "TDD-RED OK (companion): $t is changed, not added, and passes at $at; the range carries $reds test(s) red at the parent (ADR-0136 §2)"
    else
      echo "TDD-RED REFUSED: $t passes at $at, and the range carries no test red at its parent: a changed test is a companion only beside a red (ADR-0136 §2/§4)"
      fails=$((fails + 1))
    fi
  done <<< "$companions"
fi
[ "$fails" = 0 ] && exit 0
exit 1
