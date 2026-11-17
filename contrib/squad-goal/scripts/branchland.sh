#!/bin/bash
# usage: branchland.sh      one pass over the pushed work branches: land the ones whose check is green in `dev` by a fast-forward push.
#
# A work branch is `fix/<n>-…` or `gui/<n>-…` on the remote: it names its issue. The pass reads the check-run of the branch tip by
# commit sha, and lands the branch in $SG_LAND_BRANCH (default dev) through ffland.sh, which rebases first when that branch moved.
# Then the issue is closed through the API, the one delivery comment is queued (cqflush.sh posts it), and the card moves to Done.
#
# A branch is landed when, and only when, it names an open issue, the review of its tip approved (#357),
# and every check run on its tip finished
# in success, skipped or neutral, or, with zero check runs while CI is off, the local gate passed at the tip rebased onto $SG_LAND_BRANCH (#385).
# A red base holds a branch unless the tip is on top of the red head (#809): then its own evidence proves the fix, and one line says so.
# A red, running or missing check is one `BRANCHLAND WAIT <branch>: <why>`; a push a ruleset
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
SG_GHREAD="python3 $here/ghthrottle.py"; . "$here/ghcall.sh"; . "$here/address.sh"; . "$here/workbranches.sh"; . "$here/basecheck.sh"
A="$SG_STATE/auto"; mkdir -p "$A"; land=${SG_LAND_BRANCH:-dev}; remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}
export GIT_TERMINAL_PROMPT=0
cd "$SG_STATE" || exit 0
# say <wait|refuse|conflict> <branch> <head> <kind> <reason>: one line per head and kind, kept as a marker so it is never repeated.
say() { local m="$A/branchland-$1-$(printf '%s' "$2" | tr '/' '_')-${3:0:9}-$4"; [ -e "$m" ] && return; touch "$m"
  case "$1" in wait) echo "BRANCHLAND WAIT $2: $5";; conflict) echo "BRANCHLAND CONFLICT $2: $5";; *) echo "BRANCHLAND REFUSE $2: $5";; esac; }
# While GitHub Actions is off, a tip has zero check runs (#385). The local gate is then the green signal: ruff, mypy and the fast
# battery in a clean worktree of the tip. SG_LOCAL_GATE=no turns it off (default yes while Actions is off); SG_LOCAL_GATE_CMD
# replaces the gate command (tests stub it). A red or running check never starts it.
here=$(cd "$(dirname "$0")" && pwd)
py=${SG_GATE_PY:-$SG_CHECKOUT/.venv/bin/python}; [ -x "$py" ] || py=python3
# The default is the fast gate (fast-gate.sh) over the one base battery (base-battery.sh) at the `landing` stage, so its
# pytest is the full battery, never a delta; the shell suites of the scripts the tip changes are read against refs/sg-gate/$land.
. "$here/base-battery.sh"
gatecmd=${SG_LOCAL_GATE_CMD:-"$(sg_base_gate "refs/sg-gate/$land" "$py" landing)"}
# The gate runs with none of the loop's SG_* variables, so a test reads its own fixture and never the live loop state, and with the
# 2 s line lifted to the 20 s cap: that line is the CI speed rule, and this machine's load is not a defect of the branch.
# SG_SERIAL_HELD stays: it is not loop state but the guard of the lock the gate holds, and a gate nested in the gate command
# (promote-stg.sh) must find it, or it waits for its own parent.
noloop=$(env | sed -n 's/^\(SG_[A-Za-z0-9_]*\)=.*/-u \1/p' | /usr/bin/grep -v -x -- '-u SG_SERIAL_HELD' | tr '\n' ' ')
# A failed gate reruns only what failed, once: a test that machine load slowed past a line, or that raced a busy port, passes
# alone; a real red fails again. The suite runs with no cache provider, so --lf has nothing to read: the rerun gets the failed
# node ids in SG_GATE_FAILED. A red of ruff or mypy names no test, so its rerun fails again.
rerun=${SG_LOCAL_GATE_RERUN_CMD:-"'$py' -m ruff check squad tests && '$py' -m mypy squad && [ -n \"\$SG_GATE_FAILED\" ] && '$py' -m pytest -p no:deepeval -o heavy_cap_seconds=20 \$SG_GATE_FAILED"}
fixcmd=${SG_RUFF_FIX_CMD:-"'$py' -m ruff check --fix squad tests"}
# localgate <branch> <sha> <issue>: exit 0 when the gate passed at <sha> rebased onto $land, which is what lands: a red the branch
# inherited from an old base is fixed on $land, never on the branch. A rebase that conflicts gates the bare tip (ffland refuses it).
# The result goes to the status comment of the issue. A failed gate is run once per head and $land head: its wait marker stops a
# second run until $land moves.
# unwait <output>: the output without serial's wait lines, which it passes on to stderr. The wait is said once, there; the failure
# record and the log that quote the output do not carry it a second time.
unwait() { printf '%s\n' "$1" | /usr/bin/grep '^serial: waiting for lock' >&2; printf '%s' "$1" | /usr/bin/grep -v '^serial: waiting for lock'; }
localgate() { local br=$1 sha=$2 i=$3 wt out rc red base gaid
  base=$(/usr/bin/git ls-remote "$remote" "refs/heads/$land" 2>/dev/null | awk '{print substr($1,1,7)}')
  # One landing cycle is one gate attempt (#1255): the first gate, the rerun of what failed and the ruff-fix
  # re-gate all answer for the same verdict the cycle ends on, so the chain carries one attempt id and the
  # journal holds one attempt per cycle, never three.
  gaid="branchland-$i-${sha:0:9}-${base:-nobase}"
  [ -e "$A/branchland-wait-$(printf '%s' "$br" | tr '/' '_')-${sha:0:9}-gate$base" ] && return 1
  # (#1396) A red this tip already opened a fix round for is not paid again: the round owns the tip while its window holds,
  # however many base heads move under it. The marker names the tip, so a fixed tip is a new question and is gated again. Past
  # the window the gate runs once more, so a fix run that died without pushing a new tip never strands the branch.
  local fpm; fpm="$A/branchland-fixpending-$(printf '%s' "$br" | tr '/' '_')-${sha:0:9}"
  [ -s "$fpm" ] && [ $(( $(date +%s) - $(cat "$fpm" 2>/dev/null || echo 0) )) -lt "${SG_BRANCHFIX_TTL:-1800}" ] && return 1
  wt=$(mktemp -d "${TMPDIR:-/tmp}/sg-gate.XXXXXX")
  if ! /usr/bin/git -C "$SG_CHECKOUT" fetch -q "$remote" "refs/heads/$br" "+refs/heads/$land:refs/sg-gate/$land" >/dev/null 2>&1 || ! /usr/bin/git -C "$SG_CHECKOUT" worktree add -q --detach "$wt/tip" "$sha" >/dev/null 2>&1; then
    rm -rf "$wt"; say wait "$br" "$sha" gateco "the local gate has no clean checkout of ${sha:0:9}"; return 1; fi
  [ -e "$A/branchland-conflict-$(printf '%s' "$br" | tr '/' '_')-${sha:0:9}-conflict$base" ] && { /usr/bin/git -C "$SG_CHECKOUT" worktree remove --force "$wt/tip" >/dev/null 2>&1; rm -rf "$wt"; return 1; }
  /usr/bin/git -C "$wt/tip" -c user.email=gate@squad -c user.name=squad-gate rebase -q "refs/sg-gate/$land" >/dev/null 2>&1 || {
    # A content conflict is the author's to resolve: abort clean, resolve nothing, say which files and which $land head, once.
    files=$(/usr/bin/git -C "$wt/tip" diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ' | sed 's/ $//'); [ -n "$files" ] || files="(unreadable)"
    /usr/bin/git -C "$wt/tip" rebase --abort >/dev/null 2>&1; /usr/bin/git -C "$wt/tip" reset -q --hard >/dev/null 2>&1
    /usr/bin/git -C "$SG_CHECKOUT" worktree remove --force "$wt/tip" >/dev/null 2>&1; rm -rf "$wt"; mkdir -p "$SG_STATE/cq"
    printf 'Status update — BRANCHLAND CONFLICT %s: %s conflicts with %s %s (rebase of %s). The branch does not land until it is rebased.\n' "$br" "$files" "$land" "${base:-?}" "${sha:0:9}" > "$(sg_cq_status "$i" "branchland$$")"
    # A rebase branch kept by a wave conflict (#1344) gets one rebase round: a CHANGES verdict on its tip, which branchfixpass.sh
    # dispatches as a fix run on the same branch. Its review is read at this tip only, so the review pass does not replace it.
    rbk=$(printf '%s' "$br" | tr / _)
    # (#1500) A kept landing branch whose round pushed a new head and still conflicts is the repeat rule's: wave.sh decides between the next
    # round, the loop bug and the fresh build. A head no round has touched is still in its round, and gets the review below.
    if [ -s "$SG_STATE/auto/kept-$rbk" ] && /usr/bin/grep -q '^round ' "$SG_STATE/auto/kept-$rbk" && [ -x "$here/wave.sh" ] \
       && [ -s "$SG_STATE/landround-$rbk.head" ] && [ "$(cat "$SG_STATE/landround-$rbk.head")" != "$sha" ]; then
      lcout=$(bash "$here/wave.sh" landconflict "$br" "$(/usr/bin/git -C "$SG_CHECKOUT" rev-parse -q --verify "refs/sg-gate/$land" 2>/dev/null)" "$sha" "$files" 2>&1)   # captured whole: a pipe to head would kill wave.sh with SIGPIPE mid-round
      printf '%s\n' "$lcout" | head -5
      say conflict "$br" "$sha" "conflict$base" "$files conflicts with $land ${base:-?} (rebase of ${sha:0:9})"; return 1; fi
    if [ -s "$SG_STATE/auto/kept-$rbk" ]; then rbref=$(head -1 "$SG_STATE/auto/kept-$rbk")
      python3 "$here/revformat.py" --round-review "$land" "$rbref" "$files" > "$SG_STATE/review-$rbk.md"; printf '%s\n' "$sha" > "$SG_STATE/review-$rbk.sha"; rm -f "$SG_STATE/review-$rbk.base"; fi
    say conflict "$br" "$sha" "conflict$base" "$files conflicts with $land ${base:-?} (rebase of ${sha:0:9})"; return 1; }
  # The gate runs under the machine-wide gate lock (#392): a second gate waits, and its one wait line is passed on. The lock
  # reads SG_STATE, so gate.sh runs in the loop env and only the gate command inside it drops the SG_* variables.
  out=$(cd "$wt/tip" && PYTHONPATH=. SG_GATE_CALLER=landing SG_GATE_KIND=fast SG_GATE_CARD="$i" SG_GATE_AID="$gaid" bash "$here/gate.sh" env $noloop bash -c "$gatecmd" 2>&1); rc=$?; out=$(unwait "$out")
  # Exit 4 is an inconclusive gate (#699): fast-gate ran its one retry and that failed too. Exit 73 is the machine's budget
  # (#1684): gate.sh took no slot, so no battery ran. Neither gets a rerun and neither gets a ruff fix, so nothing grants the
  # landing it held and no fix round is paid for a tree the gate never read.
  red=$rc   # the first gate's answer: a retry that never ran never clears it (#1684)
  [ "$rc" = 0 ] || [ "$rc" = 4 ] || [ "$rc" = 73 ] || { out="$out
--- the rerun of what failed ---
$(cd "$wt/tip" && SG_GATE_FAILED="$(printf '%s\n' "$out" | sed -n 's/^FAILED \([^ ]*\).*/\1/p' | sort -u | tr '\n' ' ' | sed 's/ $//')" PYTHONPATH=. SG_GATE_CALLER=landing SG_GATE_KIND=fast SG_GATE_CARD="$i" SG_GATE_AID="$gaid" bash "$here/gate.sh" env $noloop bash -c "$rerun" 2>&1)"; rc=$?; out=$(unwait "$out"); [ "$rc" = 73 ] && [ "$red" = 1 ] && rc=1; }
  # Only ruff red, and ruff says it can fix it: `ruff check --fix` (safe fixes only) in the trial, one commit, the gate again.
  # The fixed trial is pushed to the branch (it is rebased on $land), so the fix lands with the branch.
  if [ "$rc" != 0 ] && [ "$rc" != 4 ] && [ "$rc" != 73 ] && printf '%s' "$out" | /usr/bin/grep -q 'fixable with the'; then
    # (#837) Stage only the files the fix changed: a file the gate already dirtied (a generated one) is not the fix's.
    pyhash() { /usr/bin/git -C "$wt/tip" ls-files -m -o --exclude-standard | while IFS= read -r f; do printf '%s %s\n' "$f" "$(/usr/bin/git -C "$wt/tip" hash-object -- "$f")"; done; }
    before=$(pyhash)
    (cd "$wt/tip" && env $noloop bash -c "$fixcmd") >/dev/null 2>&1
    fixed=$(pyhash | while IFS= read -r line; do printf '%s\n' "$before" | /usr/bin/grep -qxF "$line" || printf '%s\n' "${line% *}"; done)
    if [ -n "$fixed" ]; then
      printf '%s\n' "$fixed" | while IFS= read -r f; do /usr/bin/git -C "$wt/tip" add -- "$f" >/dev/null 2>&1; done
      /usr/bin/git -C "$wt/tip" -c user.email=gate@squad -c user.name=squad-gate commit -q -m "Apply ruff --fix at the landing

Co-Authored-By: Claude Code <noreply@anthropic.com>" >/dev/null 2>&1
      local rr=$rc; out=$(cd "$wt/tip" && PYTHONPATH=. SG_GATE_CALLER=landing SG_GATE_KIND=fast SG_GATE_CARD="$i" SG_GATE_AID="$gaid" bash "$here/gate.sh" env $noloop bash -c "$gatecmd" 2>&1); rc=$?; out=$(unwait "$out"); [ "$rc" = 73 ] && rc=$rr   # a re-gate that never ran clears nothing (#1684)
      if [ "$rc" = 0 ] && ! /usr/bin/git -C "$wt/tip" push -q --force-with-lease="refs/heads/$br:$sha" "$remote" "HEAD:refs/heads/$br" >/dev/null 2>&1; then rc=1; out="the ruff fix passed the gate but its push to $br was refused"; fi
    fi
  fi
  # A failed gate leaves the trial clean: no staged or stray file, then the worktree goes.
  /usr/bin/git -C "$wt/tip" reset -q --hard >/dev/null 2>&1; /usr/bin/git -C "$wt/tip" clean -fdq >/dev/null 2>&1
  /usr/bin/git -C "$SG_CHECKOUT" worktree remove --force "$wt/tip" >/dev/null 2>&1; rm -rf "$wt"; mkdir -p "$SG_STATE/cq"
  if [ "$rc" = 0 ]; then
    printf 'Status update — branch %s @ %s: local gate, CI off: passed at %s (ruff, mypy, pytest -m "not heavy", the shell suites of the changed scripts).\n' "$br" "${sha:0:9}" "$sha" > "$(sg_cq_status "$i" "branchland$$")"
    return 0; fi
  local res=failed; case "$rc" in 4) res=inconclusive;; 73) res="unproven, the machine's gate budget was full";; esac
  { printf 'Status update — branch %s @ %s: local gate, CI off: %s at %s (exit %s). The branch does not land.\n\n```\n' "$br" "${sha:0:9}" "$res" "$sha" "$rc"
    printf '%s\n' "$out" | grep -E '^(FAILED|E  |ERROR)' | sort -u | head -20; printf '%s\n' "$out" | tail -30; printf '```\n'; } > "$(sg_cq_status "$i" "branchland$$")"
  # (#1396) A gate that named a defect (exit 1) is the branch's, and an approved tip nobody can fix is stranded: the landing
  # pass opens the round the fix pass reads, the way its rebase-conflict path does. An inconclusive gate (exit 4) and a gate
  # the machine's full budget kept from running (exit 73, #1684) are the machine's or the loop tooling's, never the branch's:
  # neither opens a round, and the next base head re-gates — which is the recovery when the base itself was at fault.
  if [ "$rc" = 1 ]; then
    # (#1398) The fix round can act only on what the gate printed. A bullet that names the id alone sends it to a green branch
    # head it cannot reproduce (the gate read the tip REBASED onto the base), so the round answers needs-human and the tip is
    # stranded until the marker expires and the same round is paid again. Each bullet now carries what python said about that id.
    local rbk ids t; rbk=$(printf '%s' "$br" | tr / _)
    ids=$(printf '%s\n' "$out" | sed -n 's/^FAILED \([^ ]*\).*/\1/p' | sort -u)
    # ev <id>: the message the gate printed for one id: pytest's short summary (the text after " - "), else its first E line.
    ev() { printf '%s\n' "$out" | awk -v id="$1" '
      index($0, "FAILED " id " - ") == 1 { sub("^FAILED [^ ]+ - ", ""); gsub(/[[:space:]]+$/, ""); print; found = 1; exit }
      first == "" && /^E   / { first = $0; sub(/^E   /, "", first) }
      END { if (!found && first != "") print first }'; }
    # one red with no id names a shell suite or the gate itself: several of its error lines, never only the last one.
    tail3() { printf '%s\n' "$out" | awk 'NF' | tail -5 | tr '\n' '|' | sed 's/|/ | /g; s/ *| *$//' | cut -c1-220; }
    { printf 'VERDICT: CHANGES\n\n## Blocking\n'
      if [ -n "$ids" ]; then
        printf '%s\n' "$ids" | while IFS= read -r t; do
          [ -n "$t" ] || continue
          local e; e=$(ev "$t"); [ -n "$e" ] || e="the gate named this id and printed no message for it"
          printf -- '- %s — the landing gate, this tip rebased onto %s, fails: %s — make it pass the gate.\n' "$t" "$land" "$e"
        done
      else
        printf -- '- whole change — the landing gate on %s rebased onto %s failed: %s — make it pass the gate.\n' \
          "${sha:0:9}" "$land" "$(tail3)"
      fi
    } > "$SG_STATE/review-$rbk.md"
    printf '%s\n' "$sha" > "$SG_STATE/review-$rbk.sha"
    date +%s > "$fpm"
  fi
  # A capacity answer is transient, so it writes no wait marker (#1684): the marker is one line per head and base, and a marker
  # written here would keep the branch out of the gate until the base moved. The branch is gated again on the next pass, where a
  # red or an inconclusive answer waits for the base to move, as before.
  if [ "$rc" = 73 ]; then
    printf 'BRANCHLAND WAIT %s: the local gate did not run: the machine gate budget was full on %s rebased onto %s %s; it gates again on the next pass\n' "$br" "${sha:0:9}" "$land" "${base:-?}"; return 1; fi
  say wait "$br" "$sha" "gate$base" "the local gate (CI off) $res on ${sha:0:9} rebased onto $land ${base:-?}: $(printf '%s' "$out" | tail -1)"; return 1; }

# redfix <branch> <tip> <red line>: 0 when the tip is on top of the red base head (#809): the head the red line names is an ancestor
# of <tip>, and <tip> is the branch's head as read. Its own check or the local gate at that tip is what proves the fix, and the caller
# reads that first. Any other answer, or an unreadable fetch, is 1, and the branch waits as before.
redfix() { local br=$1 sha=$2 line=$3 b ref full tip
  b=$(printf '%s' "$line" | sed -n 's/^base [^ ]* is red at \([0-9a-f]*\):.*/\1/p'); [ -n "$b" ] || return 1
  ref=$(printf '%s' "$br" | tr '/' '_')
  /usr/bin/git -C "$SG_CHECKOUT" fetch -q "$remote" "+refs/heads/$land:refs/sg-red/$land" "+refs/heads/$br:refs/sg-red/$ref" >/dev/null 2>&1 || return 1
  tip=$(/usr/bin/git -C "$SG_CHECKOUT" rev-parse -q --verify "refs/sg-red/$ref^{commit}") && [ "$tip" = "$sha" ] || return 1
  full=$(/usr/bin/git -C "$SG_CHECKOUT" rev-parse -q --verify "refs/sg-red/$land^{commit}") || return 1
  case "$full" in "$b"*) ;; *) return 1;; esac
  /usr/bin/git -C "$SG_CHECKOUT" merge-base --is-ancestor "$full" "$tip" 2>/dev/null; }

# The work branches (workbranches.sh). Only a branch whose issue is open lands (#367): the remote keeps many old work branches whose
# issue closed under a pull request or was dropped, and one of those would land its old commits in dev again.
# A `loop-bug:landing` issue names a defect in the landing path: no branch lands while it is open (#641, ADR-0146 decision 3).
gh_land_stop && exit 0
sg_work_branches "$remote" || { echo "BRANCHLAND: $wb_err"; exit 0; }

# base_refs: the issues a commit the base carries names with a `Refs #N` line (#2184), each named once, as " 7 9 ". A close
# proves a delivery the way wave.sh's landing reads one: the base must hold a commit whose message names the issue. The base is
# fetched once per landing pass to refs/sg-close/$land; a fetch that fails proves nothing, so that landing closes nothing.
base_refs() { local ref="refs/sg-close/$land"
  /usr/bin/git -C "$SG_CHECKOUT" fetch -q "$remote" "+refs/heads/$land:$ref" >/dev/null 2>&1 || { printf ' '; return 0; }
  printf ' %s ' "$(/usr/bin/git -C "$SG_CHECKOUT" log --format=%B "$ref" 2>/dev/null | /usr/bin/grep -oE 'Refs #[0-9]+' \
    | /usr/bin/grep -oE '[0-9]+' | sort -un | tr '\n' ' ')"; }

# bookkeep <branch> <landed sha> <green sentence> <issue> [delete-branch]: the books a landing writes (#695, #1500). The push that
# lands a branch deletes it, so only a branch the base already carried is deleted here (#1826).
bookkeep() { local br=$1 ms=$2 green=$3 i=$4 del=${5:-}
  rm -f "$A"/branchland-*-"$(printf '%s' "$br" | tr '/' '_')"-*
  # The landing is the issue's `landed` fact (ADR-0149, #695): rule R reads it instead of a run's land heads. Written before
  # the close, because the work is in $land whether or not the API accepts the close; a refused write is named, never dropped.
  # (#1500) A kept landing branch fix/<n>-land-<run> closes every `issue` line of its marker; a marker with none (the #1344 form) closes
  # the branch's own issue. The marker is read here, before it is removed below.
  kmark="$SG_STATE/auto/kept-$(printf '%s' "$br" | tr / _)"; closeset=$(sed -n 's/^issue //p' "$kmark" 2>/dev/null | sort -un | tr '\n' ' '); closeset=${closeset:-$i}
  # (#2184) A close proves that a commit the base carries names the issue. A marker line with no such commit closes nothing,
  # queues one note, and leaves the issue open with its card in Todo; the base is read once, after the landing put the work in it.
  brefs=$(base_refs); closed=""
  for ci in $closeset; do
    case "$brefs" in
    *" $ci "*)
    out=$(python3 "$here/issue_ledger.py" landed "$SG_REPO#$ci" "$br" branch "$ms" 2>&1) || echo "BRANCHLAND: the landed fact of #$ci was refused: $out"
    gh_send -X PATCH "repos/$SG_REPO/issues/$ci" -f state=closed -f state_reason=completed >/dev/null || echo "BRANCHLAND: #$ci did not close after the landing of $br"
    mkdir -p "$SG_STATE/cq"   # the delivery comment goes through the queue: cqflush.sh keeps one per issue, edited in place (#328)
    cat > "$(sg_cq_delivery "$ci" "branchland$$")" <<EOT
Closed #$ci after landing $br in $land as $ms (fast-forward push, $br deleted). $green Refs #$ci
EOT
    col=Done; [ "${SG_BOARD_VOCAB:-}" = shipped ] && col=Closed   # the shipped vocabulary has no Done (#468)
    $BOARD "$SG_OWNER" "$SG_PROJECT" "$col" "$ci" >/dev/null
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$br" "$ms" "$ci" >> "$SG_STATE/landed.tsv"   # the ledger the hub's last-landing fact reads: the landed branch is deleted by the push, so the remote no longer names it. The board draws no lane for it (#384): a landed branch is read from its issue card
    closed="${closed:+$closed }$ci";;
    *)
    mkdir -p "$SG_STATE/cq"
    printf 'Status update — branch %s: the landing as %s carries no commit that names #%s with a `Refs #%s` line, so #%s stays open and its card stays in Todo.\n' "$br" "$ms" "$ci" "$ci" "$ci" > "$(sg_cq_status "$ci" "branchland$$")";;
    esac
  done
  case "$br" in fix/*-land-*) remote_run=${br#fix/*-land-}; /usr/bin/git -C "$SG_CHECKOUT" push -q "$remote" --delete "wave/$remote_run" >/dev/null 2>&1; rm -f "$SG_STATE/landround-$(printf '%s' "$br" | tr / _).head";; esac   # the retired wave branch goes with its landing (#1500)
  [ -n "$del" ] && /usr/bin/git -C "$SG_CHECKOUT" push -q "$remote" --delete "$br" >/dev/null 2>&1   # a branch the base already carried is not deleted by any push of ours (#1826)
  rm -f "$SG_STATE/auto/kept-$(printf '%s' "$br" | tr / _)"   # a landed rebase round's branch is gone, so its kept marker is too (#1344)
  sg_base_red "$land" >/dev/null   # the base right after the landing (#786): a red head it lands on is reported now, not at the next landing
  if [ -n "$closed" ]; then echo "BRANCHLAND LANDED $br as $ms into $land, closed #${closed// /, #}"
  else echo "BRANCHLAND LANDED $br as $ms into $land, closed no issue: no commit the base carries names one"; fi; }

# A kept landing branch (fix/<n>-land-<run> with its marker) whose tip the base already carries has landed: the work reached the base
# in an earlier round, and the tip it names now is the base's own commit, so no review, gate or push can add anything (#1826). Its
# books are written here; a review of that tip answers BLOCKED with no diff, and each round moves the tip to a fresh try count.
# Only a kept branch: an ordinary fix/<n> pushed at the base tip with no commit yet reads the same in git (the #676 trap in
# wtclean.sh), and the marker is the proof that the branch carried work. An unreadable fetch falls through to the pass below.
kept_landed() { local br=$1 sha=$2 ref full
  ref=$(printf '%s' "$br" | tr '/' '_'); [ -s "$SG_STATE/auto/kept-$ref" ] || return 1
  /usr/bin/git -C "$SG_CHECKOUT" fetch -q "$remote" "+refs/heads/$land:refs/sg-kept/$land" "+refs/heads/$br:refs/sg-kept/$ref" >/dev/null 2>&1 || return 1
  full=$(/usr/bin/git -C "$SG_CHECKOUT" rev-parse -q --verify "refs/sg-kept/$land^{commit}") || return 1
  /usr/bin/git -C "$SG_CHECKOUT" merge-base --is-ancestor "$sha" "$full" 2>/dev/null; }

# round_empty <branch> <tip> (#1960): 0 when a kept landing round ended with no pushed head. The tip is the head the round started
# from, a fix run was dispatched for it (auto/branchfix-<key>-<sha9>), and its push marker is gone, so the push back is done. The
# remote tip is read after the marker: a push that landed before the marker went is seen, and the round is not read as empty.
round_empty() { local k run
  case "$1" in fix/*-land-*) ;; *) return 1;; esac
  k=$(printf '%s' "$1" | tr / _)
  [ -s "$SG_STATE/auto/kept-$k" ] && /usr/bin/grep -q '^round ' "$SG_STATE/auto/kept-$k" || return 1
  [ "$(cat "$SG_STATE/landround-$k.head" 2>/dev/null)" = "$2" ] || return 1
  run=$(cat "$A/branchfix-$k-${2:0:9}" 2>/dev/null); [ -n "$run" ] && [ ! -e "$A/branchfix-push-$run" ] || return 1
  [ "$(/usr/bin/git ls-remote "$remote" "refs/heads/$1" 2>/dev/null | awk '{print $1}')" = "$2" ] && printf '%s\n' "$run"; }

while read -r sha br i; do
  [ -n "$br" ] || continue
  case "$br" in fix/*-land-*)
    kept_landed "$br" "$sha" && { bookkeep "$br" "$sha" "The tip ${sha:0:9} was already in $land, so it carries no work of its own." "$i" delete; continue; };; esac
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
  # One parser classifies it (#1376): branchland reads no first-line substring and no --summary count, so a review outside
  # the accepted shape can never release a branch. A saved file is the full-review shape or a fix round's ('## Resolved'),
  # and it carries no mode, so the fix-round shape is asked when the full one is malformed.
  rk="$SG_STATE/review-$(printf '%s' "$br" | tr '/' '_')"
  cls=$(python3 "$here/revformat.py" --classify "$rk.md" "$rk.sha" "$sha" 2>/dev/null)
  [ "$cls" = malformed ] && cls=$(python3 "$here/revformat.py" --classify "$rk.md" "$rk.sha" "$sha" --fix-round 2>/dev/null)
  case "$cls" in
    approves) ;;
    changes)
      # (#1960) A kept round that pushed nothing has no other bound: wave.sh requeues its issues and removes the marker.
      if [ -x "$here/wave.sh" ] && rrun=$(round_empty "$br" "$sha"); then
        bash "$here/wave.sh" landempty "$br" "fix run $rrun ended and $br is still at ${sha:0:9}" 2>&1; continue; fi
      say wait "$br" "$sha" changes "the review of ${sha:0:9} asked for changes: $(head -1 "$rk.md")"; continue;;
    stale) say wait "$br" "$sha" noreview "no review of ${sha:0:9} yet"; continue;;
    *) # malformed holds its own branch and names the shape. The marker carries the file's content, and the pass classifies
       # afresh every time, so a corrected file at the same tip lands on the next pass -- the hold is never permanent.
       say wait "$br" "$sha" "malformed$(cksum "$rk.md" 2>/dev/null | awk '{print $1}')" "the review of ${sha:0:9} is malformed: it is not the accepted shape (line 1 'VERDICT: APPROVE' or 'VERDICT: CHANGES', then '## Blocking' and/or '## Debt', one bullet per finding); a corrected file lands on the next pass"; continue;;
  esac
  # The base's own check (#786): a red completed check on $land holds every landing until its head is green, whatever this branch
  # says. It is read before the local gate, so a red base costs no gate run.
  red=$(sg_base_red "$land"); past=
  if [ -n "$red" ]; then
    redfix "$br" "$sha" "$red" || { say wait "$br" "$sha" basered "$red"; continue; }
    past=$(printf '%s' "$red" | sed -n 's/^base [^ ]* is red at \([0-9a-f]*\):.*/\1/p')
  fi
  if [ -n "$gate" ]; then localgate "$br" "$sha" "$i" || continue; green="The local gate (CI off) passed on ${sha:0:9}."
  else green="The check on ${sha:0:9} was green."; fi
  [ -n "$past" ] && echo "BRANCHLAND PAST RED BASE $br: $land is red at $past; the tip ${sha:0:9} contains that head and its own evidence is green"
  fl=$(SG_BASE=$land bash "$here/ffland.sh" "$br" "$i" 2>/dev/null | tail -1)
  case "$fl" in
    "FFLAND "*) ms=${fl#FFLAND };;
    *) say refuse "$br" "$sha" push "not landed in $land: ${fl#FFLAND-FALLBACK }"; continue;;
  esac
  bookkeep "$br" "$ms" "$green" "$i"
done <<EOT
$wb_list
EOT
exit 0
