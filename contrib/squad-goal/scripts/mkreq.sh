#!/bin/bash
# usage: mkreq.sh <name> <body-file>   writes ~/.cache/squad-goal/req/<name>.txt = body + pre-push checklist
set -e
# The model this request names is the first coding_models entry that may run now -- not switched off in the hub's models
# panel, and inside its `hours` window, an absent window always matching -- the same rule launch.py enforces: naming entry
# [0] blindly would ask for a model the hours gate is about to refuse, or quietly use an Anthropic model during the day.
mjson="$(cd "$(dirname "$0")" && pwd)/models.json"; hh=$((10#$(date +%H)))
# A model the panel switched off (#1819) is skipped exactly like one outside its window: the request never names it.
mayrun() { [ "$(jq -r --arg m "$1" '.off.coding_models[$m] // false' "$mjson")" != true ] || return 1
  local w; w=$(jq -r --arg m "$1" '.hours.coding_models[$m] // empty' "$mjson"); [ -n "$w" ] || return 0
  local a=$((10#${w%%-*})) b=$((10#${w##*-}))
  if [ "$a" -le "$b" ]; then [ "$hh" -ge "$a" ] && [ "$hh" -lt "$b" ]; else [ "$hh" -ge "$a" ] || [ "$hh" -lt "$b" ]; fi; }
cm=""; fb=""
for m in $(jq -r '.coding_models[]' "$mjson"); do mayrun "$m" || continue
  if [ -z "$cm" ]; then cm=$m; elif [ -z "$fb" ]; then fb=$m; fi; done
[ -n "$cm" ] || cm=$(jq -r '.coding_models[0] // empty' "$mjson")   # every entry off or out of window: name [0], launch.py refuses loudly
[ -n "$fb" ] || fb=$(jq -r --arg c "$cm" 'first(.coding_models[]|select(.!=$c)) // empty' "$mjson")
[ $# -eq 2 ] || { echo "usage: mkreq.sh <name> <body-file>" >&2; exit 2; }
here="$(cd "$(dirname "$0")" && pwd)"; out="$HOME/.cache/squad-goal/req/$1.txt"
. "$here/forkonly.sh"   # sg_fork_branch: the one source of the branch a fork-only child commits on (#1836)
# The setting behind it is read from the environment first -- every request builder sources env.sh and exports it -- and
# from env.sh itself otherwise, exactly as the slice guard below resolves SG_FORK_BASE. forkonly.sh owns the only default.
if [ -z "${SG_FORK_BRANCH:-}" ]; then
  SG_FORK_BRANCH=$( ( . "$here/env.sh" >/dev/null 2>&1; printf '%s' "${SG_FORK_BRANCH:-}" ) 2>/dev/null ) || true
  export SG_FORK_BRANCH
fi
[ "$1" = --paths ] || grep -q 'IMPORTANT: your first and only action' "$2" || { echo "refusing: body lacks the 'IMPORTANT: your first and only action' line" >&2; exit 1; }
# A path a body can claim: the trees a child edits, the fork's own tree included (contrib/squad-goal/..., dashboard/...), which
# the old UI|UI.<x>|Core|scripts|docs|tests list was blind to -- a fork-only child's slice lives there and matched nothing.
SLICE_ROOTS='UI|UI\.[A-Za-z]+|Core|scripts|docs|tests|contrib/squad-goal|dashboard'
SLICE_RE='(^|[^A-Za-z0-9_./-])('"$SLICE_ROOTS"')/[A-Za-z0-9_./-]+'
# (#1774) Only prose counts as a slice. A frontier/autobug request embeds the full text of every issue it dispatches, so a path
# named only inside quoted or code context is evidence someone pasted, not a file the child was asked to change: issue #1769's
# body indents `refusing: request auto-fix-1706-27aaaf5b5 names scripts/checks/verify_report_bug_prefix.sh` as a code block, PR
# #1706 was open on exactly that file, and every frontier and autobug pass embedding that body was refused -- on 2026-10-11 the
# whole wave:01 panel (#1765-#1770) could not dispatch at all. So fenced blocks, blockquote lines and lines indented by four
# spaces (or a tab) are dropped before matching. A path at column 0 still claims its slice, and a refusal message pasted as
# prose still does too: the guard reads what the request says it will edit, not what it quotes.
# (#1790) The embedded-issue region is quoted evidence even where it is plain prose. autofrontier.sh and autobug.sh paste the
# full text of every issue they dispatch under a `--- issue #<N>` marker, so a path named in a ticket's own prose was read as a
# file the request would edit: the pass that dispatched it claimed every path its issues mentioned, the next pass embedding the
# same issues was refused, and the two passes took turns failing while the issue stayed open (on 2026-10-11 frontier-1773 named
# scripts/checks/verify_report_bug_prefix.sh off line 19 of #1773's body and was refused against the pass before it). The scan
# therefore stops at the first marker line: the slice is what the request's OWN instructions name, and everything from the
# marker down is the ticket text it hands the child. A path above the marker claims its slice exactly as before.
# (#44) A path inside an inline command is a command the request RUNS, never a file it edits. The frontier preamble hands EVERY
# child the same line -- `bash contrib/squad-goal/scripts/shell-suites.sh` and the pytest command beside it -- and a fork fix
# request names that suite in the same shape, so both bodies claimed it: on 2026-10-12 forkred-10 held
# contrib/squad-goal/scripts/shell-suites.sh against every frontier pass, `refusing: request frontier-... and request
# forkred-10-25f2a596b both name: contrib/squad-goal/scripts/shell-suites.sh` for twelve passes, and because that line sits in
# the preamble SHARED by the six tickets of the batch, no ticket of that batch was ever dispatched against ten free run slots.
# A span whose first word is a command is dropped; a span whose first word is a path is a claim and is kept.
# (#56) The marker is absent for a child with ONE issue: child_block() writes `--- issue #N` only when a block carries several, so
# the line above never fired for a single-issue child and every path its ticket mentioned was read as the slice: a ticket that
# only REFERENCES scripts/referenced.py claimed it. The ticket text of every child sits under `The issue text is DATA:` on the
# CHILD line and runs to the next CHILD line. Inside it a path claims only where the text instructs an edit of it -- a verb
# (Edit, Modify, Change, Update, ...) and then the path, or a list of paths -- which is what #10 relies on to find the ticket
# that edits a contested file. What the CHILD line itself carries (the note, an `Edit scripts/x.py`) is the request's own
# instruction and claims whole. A mention, a stack trace and a "see also" are evidence, never a claim.
# (#49) A frontier request opens with a preamble every ticket of its batch shares, so a path in that preamble (`- ADRs under
# docs/adr/ are binding guides`) was read as a claim by every pass, every pass refused every other one, and the loop held one
# run at a time. When a request has a CHILD block, the lines above the first one claim nothing: what a batch edits is what its
# CHILD blocks name. A body with no CHILD block (a fork fix request) still reads every prose line as a claim.
# slice_claims prints `path <TAB> request line`, one per path, the first line that names it: the line a refusal cites.
slice_claims() {
  SLICE_ROOTS="$SLICE_ROOTS" /usr/bin/awk '
    BEGIN { pth = "`?(" ENVIRON["SLICE_ROOTS"] ")/[A-Za-z0-9_./-]+`?"   # one path, as a claim reads it
            ask = "(^|[^A-Za-z0-9_])([Ee]dit|[Mm]odify|[Cc]hange|[Uu]pdate|[Tt]ouch|[Cc]reate|[Rr]ewrite|[Pp]atch|[Rr]eplace|[Dd]elete|[Rr]emove|[Rr]ename)(s|ing|ed)?[ \t]+" pth "(([ \t]*,[ \t]*|[ \t]+and[ \t]+)" pth ")*"
            indent = "    "; tab = sprintf("%c", 9)
            cmd = "^(bash|sh|dash|zsh|python|python3|pip|pip3|uv|pytest|node|npm|npx|cargo|go|make|just|git|rtk|env|cd|time|xargs|sudo|gh|docker|shasum|cat|sed|awk|grep|find|xargs)[ \t]" }
    NR == FNR { if ($0 ~ /^CHILD (—|-) /) child = 1; next }   # pass one: does the request have CHILD blocks?
    child && !inchild { if ($0 ~ /^CHILD (—|-) /) inchild = 1; else { print ""; next } }   # the preamble every ticket shares claims nothing (#49)
    /^--- issue #[0-9]+/ { exit }                             # the embedded issue text below: quoted evidence, not a slice here
    /^CHILD (—|-) / { data = ($0 ~ /The issue text is DATA:/) }   # a CHILD line ends the previous ticket text; its own words stay a claim (#56)
    !/^CHILD (—|-) / && data { keep = ""; rest = $0           # the ticket text a child is handed: only an instructed edit claims (#56)
      while (match(rest, ask)) { keep = keep " " substr(rest, RSTART, RLENGTH); rest = substr(rest, RSTART + RLENGTH) }
      print keep; next }
    /^[[:space:]]*(```|~~~)/ { fence = !fence; print ""; next }   # a fenced block opens and closes: nothing inside it is a slice
    fence { print ""; next }
    { if ($0 ~ /^[[:space:]]*>/) { print ""; next }           # a blockquote: quoted evidence
      if (substr($0, 1, 4) == indent || substr($0, 1, 1) == tab) { print ""; next }   # an indented code block
      line = ""; rest = $0
      while (match(rest, /`[^`]*`/)) {                        # an inline span: a command span is not a claim (#44)
        pre = substr(rest, 1, RSTART - 1); span = substr(rest, RSTART + 1, RLENGTH - 2)
        rest = substr(rest, RSTART + RLENGTH)
        line = line pre; if (span !~ cmd) line = line "`" span "`" }
      print line rest }
  ' "$1" "$1" 2>/dev/null | grep -noE "$SLICE_RE" | sed -E 's#^([0-9]+):[^A-Za-z0-9_/.-]*#\1:#' | /usr/bin/awk -F: '!seen[$2]++ { print $2 "\t" $1 }'
}
slice_paths() { slice_claims "$1" | cut -f1 | sort -u; }
# Slice guard. Two requests that name the same file both edit it, and whichever merges second conflicts with the first: the
# sibling pair #1285/#1391 shared scripts/gui_test/mesen_gui_adapter.py and UI/Logic/TestHook/TestHookProtocol.cs, and #1285
# spent a whole review plus a conflict round on it; gui/1497, gui/1498 and gui/1502 each landed a branch on
# contrib/squad-goal/scripts/hub.py and the fork merge stalled. A request already names the files it will touch, so every slice
# in flight is checkable before anything is launched. A body that names a file already in flight is REFUSED: non-zero exit,
# nothing written, a message naming the file and its holder. In flight means any of three things: an open PR that changes the
# file; a queued request of any panel that claims it -- the same-panel sibling case of ADR-0274 (#1400) is the narrower half of
# that rule, and an overlapping pair of that panel still says so; or an unmerged gui/* branch in the fork checkout, which is the
# only place a fork-only child's slice is visible at all, since it never opens a PR. SG_SLICE_FORCE=1 is the documented way past
# it: a deliberate overlap stays possible, but it has to be asked for and it is announced on stderr.
# (#10) The refusal prints ONE line, for the first contested file, because that is what an operator reads. A dispatcher that
# bundles several tickets into one request needs all of them: with SG_SLICE_REPORT=<file> a refusal also writes `file <TAB>
# holder` for EVERY contested file there (emptied first, so a stale report never survives), and the dispatcher drops only the
# tickets that name one instead of refusing the whole batch.
slice_guard() {
  [ -n "${SG_SLICE_REPORT:-}" ] && : > "$SG_SLICE_REPORT"
  [ -n "$(slice_paths "$2")" ] || return 0
  local paths claims id taken f holder rid hpanel msg at ln refused="" force=0
  [ "${SG_SLICE_FORCE:-}" = 1 ] && force=1
  claims=$(slice_claims "$2"); paths=$(printf '%s\n' "$claims" | cut -f1 | sort -u)
  id=$(grep -m1 -E '^PANEL: *[^ ]+' "$2" 2>/dev/null | sed -E 's/^PANEL: *//; s/ +$//') || true; id=${id:-${SG_PANEL:-}}
  # env.sh refuses to load without a matching profile, and a launch exports the SG_* values it would have set, so take them from
  # the environment first and ask env.sh only for what is missing.
  local repo=${SG_REPO:-} fdir=${SG_FORK_DIR:-} fbase=${SG_FORK_BASE:-main} co=${SG_CHECKOUT:-} ev=""
  if [ -z "$repo" ] || [ -z "$fdir" ] || [ -z "$co" ]; then
    ev=$( ( . "$(cd "$(dirname "$0")" && pwd)/env.sh" >/dev/null 2>&1; printf '%s\n%s\n%s\n%s\n' "${SG_REPO:-}" "${SG_FORK_DIR:-}" "${SG_FORK_BASE:-}" "${SG_CHECKOUT:-}" ) 2>/dev/null ) || true
    [ -n "$repo" ] || repo=$(printf '%s\n' "$ev" | sed -n 1p)
    [ -n "$fdir" ] || fdir=$(printf '%s\n' "$ev" | sed -n 2p)
    [ -n "${SG_FORK_BASE:-}" ] || fbase=$(printf '%s\n' "$ev" | sed -n 3p); fbase=${fbase:-main}
    [ -n "$co" ] || co=$(printf '%s\n' "$ev" | sed -n 4p)
  fi
  # "file <TAB> holder", one line per file in flight; the first holder found for a file is the one reported.
  taken=""
  if [ -n "$repo" ] && command -v gh >/dev/null 2>&1; then
    for p in $(gh api "repos/$repo/pulls?state=open&per_page=100" -q '.[].number' 2>/dev/null || true); do
      for f in $(gh api "repos/$repo/pulls/$p/files?per_page=100" -q '.[].filename' 2>/dev/null || true); do
        taken="$taken$f	PR #$p
"; done; done
  fi
  # (#1529) A request counts as in flight only while its work is. Nothing ever deletes ~/.cache/squad-goal/req/*.txt, so
  # reading every one of them as in flight made the set only grow: a request whose run ended days ago still refused every
  # later slice naming one of its files (auto-fix-1203-3fbfa13ca, PR #1203 closed 2026-10-10), citing a run long over. The
  # run folder is the evidence: launch.py copies the request into runs/<run>/request.txt, so content links the request to
  # the run it went into, and a record.jsonl holding a verdict says that run is over -- the same test autobug.sh's live-run
  # scan and heartbeat.sh use. A request no run carries is in flight only inside the launch window (SG_SLICE_REQ_TTL
  # seconds, 30 min), which is all that mkreq.sh -> launch.py ever takes: an abandoned request must not hold a file forever.
  local now ttl d deadl="" livel="" deadh=" " liveh=" " hit h q qp
  now=$(date +%s); ttl=${SG_SLICE_REQ_TTL:-1800}
  for d in $(cat "${SG_STATE:-$HOME/.cache/squad-goal}/active-runs.txt" 2>/dev/null || true); do
    case "$d" in /*) ;; *) d="${co:+$co/}runs/$d";; esac
    [ -f "$d/request.txt" ] && [ -f "$d/record.jsonl" ] || continue
    if /usr/bin/grep -q '"event": *"verdict"' "$d/record.jsonl" 2>/dev/null; then deadl="$deadl$d/request.txt
"; else livel="$livel$d/request.txt
"; fi
  done
  [ -n "$deadl" ] && deadh=" $(shasum -a 256 $deadl 2>/dev/null | /usr/bin/awk '{printf "%s ", $1}')"
  [ -n "$livel" ] && liveh=" $(shasum -a 256 $livel 2>/dev/null | /usr/bin/awk '{printf "%s ", $1}')"
  req_holds() {  # $1 = another request file: may it still hold the files it names?
    local h
    h=$(shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1)
    case "$liveh" in *" $h "*) return 0;; esac          # a run without a verdict carries it: still in flight
    case "$deadh" in *" $h "*) return 1;; esac          # its run recorded a verdict: history, however young the file is
    [ $(( now - $(/usr/bin/stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0) )) -lt "$ttl" ]   # no run carries it: the launch window
  }
  for q in "$HOME/.cache/squad-goal/req"/*.txt; do
    [ -e "$q" ] || continue; [ "$q" = "$out" ] && continue
    # Only a request naming a path this body also names can refuse it, and there are usually none: the liveness test below
    # is paid per candidate. A loose match is safe here -- the `taken` lookup that decides is exact.
    qp=$(slice_paths "$q"); hit=
    for f in $paths; do case "$qp" in *"$f"*) hit=1;; esac; done
    [ -n "$hit" ] || continue
    req_holds "$q" || continue
    rid=$(basename "$q" .txt)
    for f in $qp; do taken="$taken$f	request $rid
"; done
  done
  # A fork-only child commits on gui/<N> off the fork base and opens no PR, so its slice is only visible in the fork checkout.
  # (#1794) A branch whose issue is closed claims no slice: it can never land. gui/1510-mine (#1510, closed 2026-10-11) sat one
  # commit ahead of the fork base (then local/next, now main) touching contrib/squad-goal/scripts/hub.py, so the guard refused every request naming that file
  # -- five startable issues, zero live runs, and the refusal on stderr once per pass was all anyone was told. The digits of the
  # branch name are its issue (gui/1510-mine -> 1510), and `gh issue view` is asked once per number, cached for the pass. A
  # branch GitHub cannot answer for (no `gh`, no repo, an API error, an empty answer) stays in flight exactly as before: an
  # unreachable API must never silently drop a live slice, and it says nothing. A branch that is skipped is NAMED, because what
  # can no longer land is what the operator has to retire and nothing else on the loop says so.
  local bglob br bref bnum bstate ghcache=" "; bglob=$(sg_fork_branch_pattern)   # the same setting the request names and the land resolves (#1836)
  bref=${bglob%%/%n*}; bglob="refs/heads/${bglob%%/%n*}"
  if [ -n "$fdir" ] && [ -d "$fdir" ]; then
    for br in $(/usr/bin/git -C "$fdir" for-each-ref --format='%(refname:short)' "$bglob/*" 2>/dev/null || true); do
      [ -n "$(/usr/bin/git -C "$fdir" rev-list -1 "$fbase..$br" 2>/dev/null)" ] || continue
      # The digits of the branch name are the issue it belongs to; a branch that names none (a hand-made one) is left in flight.
      if [[ $br =~ ([0-9]+) ]]; then
        bnum=${BASH_REMATCH[1]}
        # The cache is read and written here, in the loop's own scope: a function returning the state would run in the
        # command substitution's subshell and lose every write, asking GitHub once per branch instead of once per issue.
        case "$ghcache" in
          *" $bnum:"*) bstate=${ghcache#* "$bnum:"}; bstate=${bstate%% *};;
          *) bstate=""
             if [ -n "$repo" ] && command -v gh >/dev/null 2>&1; then bstate=$(gh issue view "$bnum" --repo "$repo" --json state -q .state 2>/dev/null) || bstate=""; fi
             ghcache="$ghcache$bnum:$bstate ";;
        esac
        if [ "$bstate" = CLOSED ]; then
          echo "slice guard: $br skipped, its issue #$bnum is closed" >&2
          continue
        fi
      fi
      for f in $(/usr/bin/git -C "$fdir" diff --name-only "$fbase...$br" 2>/dev/null || true); do
        taken="$taken$f	branch $br
"; done
    done
  fi
  [ -n "$taken" ] || return 0
  for f in $paths; do
    holder=$(printf '%s\n' "$taken" | /usr/bin/awk -F'\t' -v f="$f" '$1==f {print $2; exit}')
    [ -n "$holder" ] || continue
    # (#56) A refusal names the request line that made the claim: the operator opens the request and finds the sentence, not
    # just a path. `slice_claims` reads the line with the rule that claimed it, so the line cited is never a quoted one.
    ln=$(printf '%s\n' "$claims" | /usr/bin/awk -F'\t' -v f="$f" '$1==f {print $2; exit}')
    at=" (request line $ln: \"$(sed -n "${ln}p" "$2" | cut -c1-100)\")"
    if [ $force = 1 ]; then echo "SLICE FORCE $1: $f is already in flight ($holder); dispatching anyway on SG_SLICE_FORCE=1" >&2; continue; fi
    # (#1769) A fix run is not a second slice. autofix.sh names its request `auto-fix-<pr>-<sha9>`, and the PR it repairs is
    # in the claim set above by definition -- its own branch changes the very files the review asked to fix. So the guard
    # refused the only run that could repair the PR it was guarding: PR #1706 (2026-10-11) got a BLOCKING verdict on 27aaaf5b5
    # and `refusing: request auto-fix-1706-27aaaf5b5 names scripts/checks/verify_report_bug_prefix.sh, which open PR #1706
    # already changes` left the verdict sitting on a mergeable, CI-green PR with nothing working it (autofix.sh then exits 1).
    # ADR-0274 separates two slices of work; a slice and its own repair are one unit of work, and the claim record already
    # carries the number the request name carries. Only that pair is exempt: the file of another open PR, of another request
    # and of an unmerged fork branch refuses exactly as before, and so does any request whose name is not a fix run's.
    if [[ $1 =~ ^auto-fix-([0-9]+)-[0-9a-f]{9}$ ]] && [ "$holder" = "PR #${BASH_REMATCH[1]}" ]; then
      echo "SLICE SELF-FIX $1: $f is $holder's own file; a fix run on the PR it repairs is not a second slice (#1769)" >&2; continue
    fi
    # (#1786) A rebase of an already-written branch is not a second writer either. forkclose.sh names its request
    # `forkrebase-<issue>-<sha9>` and its body names the files the branch no longer merges over -- which that same branch holds
    # by definition, since it is the branch being merged. With gui/1767 and gui/1768 both editing
    # contrib/squad-goal/scripts/autopilot.sh, the guard refused each rebase run citing the other branch's claim, and the only
    # request that could break the deadlock was the one refused. So the file of the branch this request repairs is exempt,
    # exactly as #1769 exempts a fix run's own PR -- and the claim record already carries the number the request name carries.
    # The closes are serialized instead: the first branch's rebase lands, and the second then collides with the base, not with a
    # branch. Another request's file, an open PR's file, and the file of a branch this request's own branch does not claim
    # refuse exactly as before, and so does any request whose name is not a rebase run's.
    # The exemption asks whether the request's OWN branch claims the file, not whether the branch the lookup happened to return
    # is that branch. `holder` above is the FIRST claimer in for-each-ref order, and that order is alphabetical: of gui/1767 and
    # gui/1768 both editing contrib/squad-goal/scripts/autopilot.sh, gui/1767 always answers first, so comparing `holder` against
    # the request's own branch exempted gui/1767's rebase and refused gui/1768's -- the one that repairs the branch the refusal
    # named. The pair stayed deadlocked, one-sided, and the refusal read as a mirror-check failure. Asking the claim list instead
    # exempts a file the request's own branch claims even when another branch claims it too, which is what serializes the closes.
    if [[ $1 =~ ^forkrebase-([0-9]+)-[0-9a-f]{9}$ ]] && printf '%s\n' "$taken" | /usr/bin/awk -F'\t' -v f="$f" -v b="branch ${bref}/${BASH_REMATCH[1]}" '$1==f && $2==b {mine=1} END{exit !mine}'; then
      echo "SLICE SELF-REBASE $1: $f is ${bref}/${BASH_REMATCH[1]}'s own file; a rebase of the branch it repairs is not a second slice (#1786)" >&2; continue
    fi
    case "$holder" in
      "request "*)
        rid=${holder#request }; hpanel=$(grep -m1 -E '^PANEL: *[^ ]+' "$HOME/.cache/squad-goal/req/$rid.txt" 2>/dev/null | sed -E 's/^PANEL: *//; s/ +$//') || true
        msg="refusing: request $1 and request $rid$([ -n "$id" ] && [ "$hpanel" = "$id" ] && echo " (same panel $id)") both name: $f$at. Re-split the slices or add a '## Blocked by' edge between them (ADR-0274). Set SG_SLICE_FORCE=1 to dispatch anyway." ;;
      "PR "*)
        msg="refusing: request $1 names $f$at, which open $holder already changes. Set SG_SLICE_FORCE=1 to dispatch anyway." ;;
      *)
        msg="refusing: request $1 names $f$at, which the unmerged fork branch ${holder#branch } touches (that is where a fork-only child's slice lives). Set SG_SLICE_FORCE=1 to dispatch anyway." ;;
    esac
    [ -n "$refused" ] || echo "$msg" >&2   # the first contested file is the one the operator reads; the report below carries the rest
    [ -z "${SG_SLICE_REPORT:-}" ] || printf '%s\t%s\n' "$f" "$holder" >> "$SG_SLICE_REPORT"
    refused=1
  done
  [ -z "$refused" ]
}
[ "$1" = --paths ] && { slice_paths "$2"; exit 0; }   # (#10) the slice the guard would read from a body: a dispatcher attributes a contested file to one ticket with the guard's own rule
slice_guard "$1" "$2" || exit 1
# (#1836) The branch a fork-only child commits on has ONE source: $SG_FORK_BRANCH (%n = the issue), the setting
# forkclose.sh substitutes into the ref it merges (sg_fork_branch, forkonly.sh). Two builders used to carry their own
# literal beside it -- autofrontier and autobug each wrote `gui/<N>` while the land resolved whatever the profile said --
# and a hand-written body could name anything at all. The MesenAI habit `fix/<N>-<short-slug>` reached fork-only children
# that way, the commit landed on a branch no close pass reads, and the card sat at Code Complete with nobody waiting for
# it (#1831). The check runs HERE, before the request file exists and so before launch.py makes a run folder: a fork-only
# dispatch that would name anything else is refused, naming both branches, and so is one whose setting cannot name a
# landing branch at all.
fork_landing_refusal() {   # <body> : the reason this fork-only request could never land; empty when it can
  local pat pref ref own cl scan t exp num
  pat=$(sg_fork_branch_pattern)
  case "$pat" in *%n*) ;; *)
    echo "is fork-only and cannot name a landing branch: SG_FORK_BRANCH=$pat carries no %n, so it names no branch per issue and forkclose.sh would resolve one ref for every issue."; return;; esac
  ref=$(sg_fork_branch 1)
  /usr/bin/git check-ref-format --branch "$ref" >/dev/null 2>&1 ||
    { echo "is fork-only and cannot name a landing branch: SG_FORK_BRANCH=$pat yields '$ref' for issue #1, which is not a valid branch name (\`git worktree add -b '$ref'\` refuses it), so no fork-only commit could land."; return; }
  pref=${pat%%/%n*}
  # What the request TELLS the child: its own lines above the first CHILD line, plus each CHILD line's own note. The ticket
  # text pasted below those is DATA the request hands over, and a ticket that happens to name a branch must never refuse
  # the dispatch carrying it (#1790).
  own=$(/usr/bin/awk '/^CHILD/ { exit } { print }' "$1" 2>/dev/null)
  cl=$(/usr/bin/grep -E '^CHILD' "$1" 2>/dev/null) || true
  scan=$(printf '%s\n%s\n' "$own" "$cl")
  for t in $(printf '%s\n' "$scan" | /usr/bin/grep -oE '(^|[^A-Za-z0-9_./-])[A-Za-z][A-Za-z0-9._-]*/(<N>|[0-9]+)(-[A-Za-z0-9._-]+)?' | sed -E 's#^[^A-Za-z]+##' | sort -u); do
    case "$t" in *'/<N>'*) exp=$(sg_fork_branch '<N>');; *)
      num=${t##*/}; num=${num%%-*}; exp=$(sg_fork_branch "$num")
      # A branch-shaped token whose number this request does not dispatch, and whose family is not the land's, is not a
      # name for its issue: a path, a run folder, another issue's ref. A `gui/1` under a gui/%n land is checked either way.
      [ "${t%%/*}" = "$pref" ] || printf '%s\n' "$cl" | /usr/bin/grep -qE "(^|[^0-9])$num([^0-9]|\$)" || continue;; esac
    [ "$t" = "$exp" ] || { echo "is fork-only and names $t, but the branch a fork-only child commits on has ONE source: SG_FORK_BRANCH=$pat, so forkclose.sh resolves $exp. A commit on $t reaches no close pass (#1831)."; return; }
  done
}
# Fork-only (#1411, the rule of forkonly.sh): the builders write a `FORK-ONLY: all` line when every child is fork-only; a hand-written body
# with no CHILD blocks is fork-only when it carries the "Fork-only work" line. A fork-only request gets the fork DONE CHECK and checklist:
# nothing in it names a PR, a push or prepush.sh.
fork=0; if grep -qE '^FORK-ONLY: all' "$2"; then fork=1; elif ! grep -qE '^CHILD' "$2" && grep -q 'Fork-only work' "$2"; then fork=1; fi
# (#81) What the child RUNS, and what it leaves alone. The full gate -- the shell suites and the pytest gate beside them -- is
# the loop's and its land pass's, once per landing: a child that runs it too starts a second full gate on the same machine, the
# two take every core between them and the owner's editor loses the box. Both halves weigh the same, so the length of the run is
# what grows, not the answer. The request therefore names the TARGETED commands instead -- the suite beside the script the child
# touched and the cases it needs. Both inline spans open with a command word, so the slice guard reads them as what the request
# RUNS and never as a file it claims (#44); the paths they name are not slices either way.
suites_line="SUITES: run what you touched -- the shell suite beside the script you changed (\`bash contrib/squad-goal/scripts/test-<script>.sh\`) and the targeted pytest cases (\`uv run --extra dev pytest tests/<file>.py -q -p no:deepeval -k <case>\`). The machine's full gate (\`bash contrib/squad-goal/scripts/shell-suites.sh\` and the pytest gate beside it) belongs to the loop and to the land pass, once per landing: never run it."
if [ $fork = 1 ]; then
  rea=$(fork_landing_refusal "$2"); [ -z "$rea" ] || { echo "refusing: request $1 $rea" >&2; exit 1; }
  mkdir -p "$(dirname "$out")"; { cat "$2"; echo; echo "DONE CHECK (the run's final gate runs these; without them the run ends failed with no_ac_commands even when the work is right): end your result with {\"status\": \"pass\", \"ac_commands\": [...]}, one string per check you ran as evidence, each as \"cd <fork worktree> && <command>\" (the gate runs in the fork worktree you committed in). Use the targeted commands (the touched test files, the doc checks when docs changed), not a full suite, and only commands that passed for you. ac_commands are the proofs that pass at the final head, and only those: the gate runs each string verbatim there, so a command that writes to the worktree turns your own fix against you -- a RED demonstration belongs in your result notes, never in ac_commands, and never an ac_command that writes to the worktree: no \`git checkout <other-ref> -- <file>\` (that is the RED step, which restores the pre-fix file), no \`git stash\`, no revert. A command that mutates the tree is evidence for your result notes, not a gate input (#1799)."; echo "$suites_line"; [ -n "$cm" ] && echo "Coding model: $cm (no [1m] suffix); fall back to $fb. This line wins over any other model named above."; ck=$(cat "$here/req-checklist-fork.txt"); ck=${ck//gui\/<N>/$(sg_fork_branch '<N>')}; echo "$ck"; } > "$out"; echo "$out"; exit 0
fi
mkdir -p "$(dirname "$out")"; { cat "$2"; echo; echo "DONE CHECK (the run's final gate runs these; without them the run ends failed with no_ac_commands even when the work is right): end your result with {\"status\": \"pass\", \"ac_commands\": [...]}, one string per check you ran as evidence. Work pushed to a PR branch: wrap each as \"$here/acpr.sh <branch> -- <command>\" (the gate runs in the main checkout; acpr.sh checks out origin/<branch> in a throwaway worktree first). Work committed only in a local fork: \"cd <fork worktree> && <command>\". Use the targeted commands (the touched test classes, the doc checks when docs changed), not a full suite, and only commands that passed for you. ac_commands are the proofs that pass at the final head, and only those: the gate runs each string verbatim there, so a command that writes to the worktree turns your own fix against you -- a RED demonstration belongs in the PR body, never in ac_commands, and never an ac_command that writes to the worktree: no \`git checkout <other-ref> -- <file>\` (that is the RED step, which restores the pre-fix file), no \`git stash\`, no revert. A command that mutates the tree is evidence for the PR body, not a gate input (#1799)."; echo "$suites_line"; [ -n "$cm" ] && echo "Coding model: $cm (no [1m] suffix); fall back to $fb. This line wins over any other model named above."; cat "$here/req-checklist.txt"; } > "$out"; echo "$out"
