#!/bin/bash
# usage: mkreq.sh <name> <body-file>   writes $SG_STATE/req/<name>.txt = body + pre-push checklist
set -e
# The model this request names is the first coding_models entry that may run now -- not switched off in the hub's models
# panel, and inside its `hours` window, an absent window always matching -- the same rule launch.py enforces: naming entry
# [0] blindly would ask for a model the hours gate is about to refuse, or quietly use an Anthropic model during the day.
mjson="$(cd "$(dirname "$0")" && pwd)/models.json"
# A model the panel switched off (#1819) is skipped exactly like one outside its window: the request never names it.
# A model whose subscription is spent (#508) is skipped too: a future codex-limited marker closes a gpt- model, agy-limited a
# gemini one -- the markers review.sh honors. A refused child otherwise lands nothing until the window resets.
[ -n "${SG_SHARED:-}" ] || { [ -f "$(dirname "$0")/address.sh" ] && . "$(dirname "$0")/address.sh" && SG_SHARED=$(sg_shared_dir 2>/dev/null); }
# A model whose provider needs a key that is absent (#482, provkey.py: qwen without an OpenCode Go key) is closed the same way.
keyless() { case $1 in *qwen*) python3 "$(dirname "$0")/provkey.py" "$1";; *) return 1;; esac; }
spent() { local f; keyless "$1" && return 0; case $1 in *gpt-*) f=codex-limited;; *gemini-*) f=agy-limited;; *) return 1;; esac
  [ -s "${SG_SHARED:-/nonexistent}/$f" ] && [ "$(date +%s)" -lt "$(cat "$SG_SHARED/$f")" ]; }
# The resolver reads the window (modelreg.in_window: "HH-HH" local, or days, ranges and UTC), never this script.
rows=$(python3 "$(dirname "$0")/modelreg.py" chain coding --models "$mjson" --tsv | awk -F'\t' '{print $2, $8, $7}')   # raw spelling, inside its window now (1/0), on
mayrun() { [ "$3" = 1 ] || return 1
  spent "$1" && return 1
  [ "$2" = 1 ]; }
cm=""; fb=""
while read -r m w on; do mayrun "$m" "$w" "$on" || continue
  if [ -z "$cm" ]; then cm=$m; elif [ -z "$fb" ]; then fb=$m; fi; done <<<"$rows"
[ -n "$cm" ] || cm=$(awk 'NR==1{print $1}' <<<"$rows")   # every entry off or out of window: name [0], launch.py refuses loudly
[ -n "$fb" ] || while read -r m _; do [ "$m" = "$cm" ] && continue; keyless "$m" || { fb=$m; break; }; done <<<"$rows"   # a keyless model is never the fallback either (#482)
[ $# -eq 2 ] || { echo "usage: mkreq.sh <name> <body-file>" >&2; exit 2; }
here="$(cd "$(dirname "$0")" && pwd)"
# The request lands in the state directory of the repository it is for (#25); a request builder sources env.sh, which
# resolves it from the seam and exports it. With no SG_STATE the seam's own base is the default -- spelling that path here
# is what the seam exists to stop, and an unset SG_STATE spelled it as `/req/<name>.txt`, the filesystem root. A tree with
# no seam beside it gets no default: the script names the absence instead of writing outside its own state.
if [ -z "${SG_STATE:-}" ] && [ -f "$here/address.sh" ]; then . "$here/address.sh"; SG_STATE=$(sg_state_base); fi
out="${SG_STATE:?mkreq.sh: no state directory; source env.sh for this repository, or set SG_STATE}/req/$1.txt"
# mtime <path>: the file mtime in epoch seconds, whichever stat this machine has (GNU -c %Y, BSD -f %m).
mtime() { if stat --version >/dev/null 2>&1; then stat -c %Y "$1" 2>/dev/null || echo 0; else stat -f %m "$1" 2>/dev/null || echo 0; fi; }
[ "$1" = --paths ] || grep -q 'IMPORTANT: your first and only action' "$2" || { echo "refusing: body lacks the 'IMPORTANT: your first and only action' line" >&2; exit 1; }
# A path a body can claim.
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
# nothing written, a message naming the file and its holder. In flight means either of two things: a queued request of any panel
# that claims it -- the same-panel sibling case of ADR-0274 (#1400) is the narrower half of that rule, and an overlapping pair of
# that panel still says so; or an unmerged gui/* branch in the fork checkout, which is the only place a fork-only child's slice is
# visible at all, since it never opens a pull request. SG_SLICE_FORCE=1 is the documented way past
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
  # (#1529) A request counts as in flight only while its work is. Nothing ever deletes $SG_STATE/req/*.txt, so
  # reading every one of them as in flight made the set only grow: a request whose run ended days ago still refused every
  # later slice naming one of its files, citing a run long over. The
  # run folder is the evidence: launch.py copies the request into runs/<run>/request.txt, so content links the request to
  # the run it went into, and a record.jsonl holding a verdict says that run is over -- the same test autobug.sh's live-run
  # scan and heartbeat.sh use. A request no run carries is in flight only inside the launch window (SG_SLICE_REQ_TTL
  # seconds, 30 min), which is all that mkreq.sh -> launch.py ever takes: an abandoned request must not hold a file forever.
  local now ttl d deadl="" livel="" deadh=" " liveh=" " hit h q qp
  now=$(date +%s); ttl=${SG_SLICE_REQ_TTL:-1800}
  for d in $(cat "$SG_STATE/active-runs.txt" 2>/dev/null || true); do
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
    [ $(( now - $(mtime "$1") )) -lt "$ttl" ]   # no run carries it: the launch window
  }
  for q in "$SG_STATE/req"/*.txt; do
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
  [ -n "$taken" ] || return 0
  for f in $paths; do
    holder=$(printf '%s\n' "$taken" | /usr/bin/awk -F'\t' -v f="$f" '$1==f {print $2; exit}')
    [ -n "$holder" ] || continue
    # (#56) A refusal names the request line that made the claim: the operator opens the request and finds the sentence, not
    # just a path. `slice_claims` reads the line with the rule that claimed it, so the line cited is never a quoted one.
    ln=$(printf '%s\n' "$claims" | /usr/bin/awk -F'\t' -v f="$f" '$1==f {print $2; exit}')
    at=" (request line $ln: \"$(sed -n "${ln}p" "$2" | cut -c1-100)\")"
    if [ $force = 1 ]; then echo "SLICE FORCE $1: $f is already in flight ($holder); dispatching anyway on SG_SLICE_FORCE=1" >&2; continue; fi
    case "$holder" in
      "request "*)
        rid=${holder#request }; hpanel=$(grep -m1 -E '^PANEL: *[^ ]+' "$SG_STATE/req/$rid.txt" 2>/dev/null | sed -E 's/^PANEL: *//; s/ +$//') || true
        msg="refusing: request $1 and request $rid$([ -n "$id" ] && [ "$hpanel" = "$id" ] && echo " (same panel $id)") both name: $f$at. Re-split the slices or add a '## Blocked by' edge between them (ADR-0274). Set SG_SLICE_FORCE=1 to dispatch anyway." ;;
    esac
    [ -n "$refused" ] || echo "$msg" >&2   # the first contested file is the one the operator reads; the report below carries the rest
    [ -z "${SG_SLICE_REPORT:-}" ] || printf '%s\t%s\n' "$f" "$holder" >> "$SG_SLICE_REPORT"
    refused=1
  done
  [ -z "$refused" ]
}
[ "$1" = --paths ] && { slice_paths "$2"; exit 0; }   # (#10) the slice the guard would read from a body: a dispatcher attributes a contested file to one ticket with the guard's own rule
slice_guard "$1" "$2" || exit 1
# (#670) A review approval is not evidence that the entry gate passes: a child kept returning work the wave gate rejected on a
# lint or type failure it was never told to run, and every round cost a child run and one of its dispatches. The request now
# names fast-gate.sh, the same gate the wave runs, as the child's own last check.
# (#81) What the child RUNS, and what it leaves alone. The full gate -- the shell suites and the pytest gate beside them -- is
# the loop's and its land pass's, once per landing: a child that runs it too starts a second full gate on the same machine, the
# two take every core between them and the owner's editor loses the box. Both halves weigh the same, so the length of the run is
# what grows, not the answer. The request therefore names the TARGETED commands instead -- the suite beside the script the child
# touched and the cases it needs. Both inline spans open with a command word, so the slice guard reads them as what the request
# RUNS and never as a file it claims (#44); the paths they name are not slices either way.
# (#740) The commands come from the profile (sg_gate_cmds in address.sh): an unset value is agent-squad's text, so its line is
# unchanged, and an empty one names no script -- a target without the scripts has no fast gate, and that is never a failure.
sh='the targeted pytest cases (`uv run --extra dev pytest tests/<file>.py -q -p no:deepeval -k <case>`) and the shell suite beside the script you changed (`bash contrib/squad-goal/scripts/test-<script>.sh`), unless the SUITE COST line names that suite: the fast gate runs it once per head, so a suite the SUITE COST line names is never run by you'
fg='bash contrib/squad-goal/scripts/fast-gate.sh'; full='`bash contrib/squad-goal/scripts/shell-suites.sh` and the full pytest suite beside it'
sh=${SG_SUITES_HINT-$sh}; fg=${SG_FAST_GATE_CMD-$fg}; full=${SG_FULL_GATE_HINT-$full}
suites_line="SUITES: run what you touched -- ${sh:-the targeted tests of the code you changed}."
# (#1527) A child that re-ran a bounded gate until its deadline landed nothing: the request says what exit 4 means, and that it ends the gate step.
if [ -n "$fg" ]; then suites_line="$suites_line Then run \`$fg\` in your worktree before you report done: it is the gate your commit must pass to enter the wave, its pytest takes the bounded worker count it carries. Run it once per commit: exit 0 is a pass; exit 4 is unproven whatever its line names (a bounded battery, an inconclusive battery or a full gate budget), not a defect in your work, and it ends your gate step: the entry gate and the landing measure your commit again under their own clock, so never run it a second time on the same commit, never wait for the machine, the nightly or another gate, and report done with the gate's last line in your result. When that line says unproven, end your result with {\"status\": \"unproven\", \"reasons\": [that line]}: a gate that could not run is no completed proof and no failing child, and the run carries the obligation (ADR-0164). Never list the fast gate in ac_commands: the run's gate runs that command again at its final head, and the proof there is the run's, not yours. Exit 1 names a defect: fix it and run the gate once on the new commit."
else suites_line="$suites_line No fast gate is configured for this repository, so run none: a missing gate is not a failure, and your targeted checks are the evidence."; fi
suites_line="$suites_line The machine's full gate (${full:-the full test suite}) belongs to the loop and to the land pass, once per landing: never run it."
# (#1761) The measured cost of each suite of 5 s or more, from each suite's own header. A child that runs one of them again spends its
# deadline in its own proofs, so the request names each cost and says which suite the fast gate runs, never the child.
cost=$(/usr/bin/awk 'FNR == 1 { if (name != "" && secs >= 5) print name " " secs; name = FILENAME; sub(/.*\//, "", name); secs = -1 }
  /^# seconds:/ { secs = $3 + 0 }
  END { if (name != "" && secs >= 5) print name " " secs }' "$here"/../../../contrib/squad-goal/scripts/test-*.sh 2>/dev/null |
  /usr/bin/sort | /usr/bin/awk 'NF == 2 { printf "%s%s %s s", sep, $1, $2; sep = ", " }') || cost=""
costline=""; [ -n "$cost" ] && costline="SUITE COST: measured seconds of each suite of 5 s or more (the '# seconds:' header of each suite in contrib/squad-goal/scripts): $cost. A suite named here is never run by you: the fast gate runs it once per head when your change touches its script, and a second run of it spends the deadline you have. A proof that cannot finish before your deadline is not a failure: commit your code and report done, and the landing pass proves it (ADR-0164)."
mkdir -p "$(dirname "$out")"; { cat "$2"; echo; echo "DONE CHECK (the run's final gate runs these; without them the run ends failed with no_ac_commands even when the work is right): end your result with {\"status\": \"pass\", \"ac_commands\": [...]}, one string per check you ran as evidence. Each string is a plain command, run as it is: the gate runs it in the run's target at its final head, where your committed work is already landed, so name no path, no cd and no wrapper (you push nothing, so no remote branch of yours exists). Use the targeted commands (the touched test classes, the doc checks when docs changed), not a full suite, and only commands that passed for you. ac_commands are the proofs that pass at the final head, and only those: the gate runs each string verbatim there, so a command that writes to the worktree turns your own fix against you -- a RED demonstration belongs in the issue status comment, never in ac_commands, and never an ac_command that writes to the worktree: no \`git checkout <other-ref> -- <file>\` (that is the RED step, which restores the pre-fix file), no \`git stash\`, no revert. A command that mutates the tree is evidence for the issue status comment, not a gate input (#1799)."; echo "$suites_line"; [ -z "$costline" ] || echo "$costline"; [ -n "$cm" ] && echo "Coding model: $cm (no [1m] suffix); fall back to $fb. This line wins over any other model named above."; cat "$here/req-checklist.txt"; } > "$out"; echo "$out"
