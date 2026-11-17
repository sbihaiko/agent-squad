#!/bin/bash
# usage: . startability.sh   (then sg_not_startable <issue>, sg_fork_branch <issue>, sg_issue_worktree_note <issue>)
# The shared read and helpers of every dispatcher: an issue the loop marked not startable is not dispatched again, the
# branch an issue's work lives on, and the note a request builder hands a child whose worktree an earlier run left behind.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ghcall.sh"
export SG_GH_PASS=landing   # the pass that spends the GitHub calls below, named in the call log (ADR-0277); a dispatcher that sources this file exports its own pass name after it
# The branch an issue's work lives on: ONE setting, SG_FORK_BRANCH (%n = the issue). The dispatchers, stack.sh and the
# launch that makes the issue worktree all name it through this function, so the branch a child is given and the branch
# the worktree is made on cannot come from two sources.
sg_fork_branch() { local br=${SG_FORK_BRANCH:-gui/%n}; echo "${br//%n/$1}"; }
sg_fork_branch_pattern() { echo "${SG_FORK_BRANCH:-gui/%n}"; }
# The labels a dispatch lane takes no work from: ONE file, read by both dispatchers and by the health pass (#1593), so a card
# the frontier will never start is never reported startable (skiplabels.sh carries each lane's set and its default).
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skiplabels.sh"
# An issue claimed by either dispatcher is unavailable to both passes. The marker is the dispatch lock,
# so its namespace is intentionally shared even though each pass writes its own name.
sg_dispatch_claimed() { [ -e "$SG_STATE/auto/bug-$1" ] || [ -e "$SG_STATE/auto/disp-$1" ]; }
# The column Todo is the owner's go signal (#550, ADR-0142): a dispatcher takes an issue only when its card sits in Todo
# and it carries the go label. One board read a pass, through the seam's door, never one per issue. It prints one issue a
# line whose card is in Todo; a card with no column is not in Todo. A read that does not answer returns 1, and the pass
# then dispatches nothing.
# With a go list (comma-separated issue numbers, #800) the read also names each go-labelled issue whose card no read carries:
# it is rescued by its known node id when that answers Todo, else named once on stderr and in alerts.log. No list is the plain read.
# The go list was read at another time, so the read is told the go label (#1031): an issue the list still names go-labelled is
# named that only when the issue itself agrees, and one it answers closed, or without the label, is a NOTE instead of an alert.
sg_board_rows() {   # [go-csv]: the one board read of a pass, every card the carrier answers, "<issue>\t<state>" a row
  local here; here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ $# -gt 0 ]; then
    if [ -n "${SG_FRONTIER_LABEL:-}" ]; then python3 "$here/issue_state.py" --go "$1" --label "$SG_FRONTIER_LABEL" 2>/dev/null || return 1
    else python3 "$here/issue_state.py" --go "$1" 2>/dev/null || return 1; fi
  else python3 "$here/issue_state.py" 2>/dev/null || return 1; fi
}
sg_todo_of() {   # the Todo issues of the rows on stdin; the go list's UNREAD and NOTE rows are said on stderr
  awk -F '\t' -v t="${SG_TODO:-Todo}" '$1 == "UNREAD" { print "FRONTIER: go-labelled #" $2 " has no readable card on the board (named in alerts.log)" > "/dev/stderr"; next } $1 == "NOTE" { print "FRONTIER: #" $2 " is " $3 "; it is not go-labelled, and nothing alerts" > "/dev/stderr"; next } $2 == t { print $1 }' | sg_recorded_todo
}
# A recorded write outranks a board read (#1995, ADR-0144): the Project Status field is eventually consistent, so a read
# can answer Todo for a card a writer has already moved. board.py proves every move it writes and records each proof, in
# write order, in board-transitions.<repo>.jsonl, so the last recorded write of a card is the latest column a writer
# proved. A card whose last recorded write sets another column is no Todo signal until a later write sets Todo, whatever
# the read answers. A record that cannot be read filters nothing, as for any filter that cannot run.
# The record masks the read only while the read catches up, and a write lands seconds after it is made. A record older
# than that window is a memory of a past write, never the truth now: the owner moves a card in GitHub's own view, and
# that write is recorded by nobody, so a record that never expired hid every later hand move for good (#2151).
sg_recorded_todo() {   # <issue> a line on stdin: print the ones the transition record does not contradict
  local f
  if [ -z "${SG_STATE:-}" ] || [ -z "${SG_REPO:-}" ]; then cat; return 0; fi
  f="$SG_STATE/board-transitions.${SG_REPO//\//_}.jsonl"
  python3 -c '
import json, sys, time
from datetime import datetime
path, todo, trust = sys.argv[1], sys.argv[2], float(sys.argv[3])
def stamp(r):
    at = r.get("at")
    if not isinstance(at, str): return None
    try: return datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
    except ValueError: return None
last = {}
try:
    with open(path, encoding="utf-8") as f:
        for line in f:
            try: r = json.loads(line)
            except ValueError: continue
            n, to = r.get("issue"), r.get("to")
            if n is not None and to is not None: last[str(n)] = (to, stamp(r))
except OSError:
    last = {}
now = time.time()
for n in sys.stdin.read().split():
    rec = last.get(n)
    # A write named in the record with no instant is trusted: it is a writer that predates this bound, and the sure fix
    # is the writer, never a reader that guesses an age.
    if rec and (rec[1] is None or now - rec[1] <= trust) and rec[0] != todo: continue
    print(n)
' "$f" "${SG_TODO:-Todo}" "${SG_RECORD_TRUST_SECONDS:-300}"
}
sg_todo_cards() {
  local out; out=$(sg_board_rows "$@") || return 1
  printf '%s\n' "$out" | sg_todo_of
}
# The go-labelled open issues of the pass's issue list, comma-separated (#800): the list sg_todo_cards checks for a card.
sg_go_csv() {   # <issues.json> <label>
  jq -r --arg g "$2" '[.[]|select(.pull_request|not)|select([.labels[]?.name]|index($g))|(.number|tostring)]|join(",")' "$1" 2>/dev/null
}
# Read live child requests once for both dispatchers. Issue text is not structured in spawn_accepted yet,
# so accept the two request forms that the dispatchers write and return exact decimal issue numbers only.
# An issue is held from its dispatch until its delivery ends (#539): a run with a verdict whose wave marker
# auto/wave-<run> stands (wave.sh clears it when the wave lands) still holds its issues, and so does a child that entered
# such a wave (the entered fact of its ledger, ADR-0149), whether or not its run is still listed. A run whose record cannot be read holds
# the issues its request names: an unknown delivery state keeps the hold.
sg_live_issues() {   # <active-runs.txt>: print comma-separated issue numbers from live spawn_accepted events
  python3 - "$SG_CHECKOUT" "$1" "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" "${SG_REPO:-}" <<'PY'
import json, os, pathlib, re, sys
root, active, here, repo = sys.argv[1:]
auto = pathlib.Path(active).parent / "auto"
busy = set()
os.environ.setdefault("SG_STATE", str(auto.parent)); sys.path.insert(0, here)
try:
    import issue_ledger
    for run, n in (issue_ledger.entered(repo) if repo else []):
        if (auto / f"wave-{run}").exists():
            busy.add(str(n))
except (OSError, ImportError, ValueError) as e:
    print(f"startability: the entered facts could not be read ({e})", file=sys.stderr)
try:
    import liveholds as lh   # the start's own rule (#1829): one answer for both readers (ADR-0149)
    busy |= {str(n) for n in lh.live_holders(active, base=root)}
except (OSError, ImportError, ValueError) as e:
    lh = None
    print(f"startability: the live-holder rule could not be read ({e}); the record's own words are read instead", file=sys.stderr)
try:
    runs = pathlib.Path(active).read_text().split()
except OSError:
    runs = []
for run in runs:
    folder = pathlib.Path(run if run.startswith("/") else pathlib.Path(root) / "runs" / run)
    record = folder / "record.jsonl"
    try:
        text = record.read_text()
    except OSError:
        continue
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(event)
    if any(isinstance(event, dict) and event.get("event") == "verdict" for event in events) and not (auto / f"wave-{folder.name}").exists():
        continue
    # The router writes the issue it works in its own words, with and without the hash, one or several at a time, and names
    # its repository ("issue o/r #12") or its reference ("Refs #12"); liveholds reads every form the record carries (#428, #1829).
    if lh is not None:
        busy |= {str(x) for x in lh.busy_from_record(text)}
    else:
        for listed in re.findall(r"\bgithub\s+issues?\s*#?\s*([0-9][0-9,\s]*)", text, re.IGNORECASE):
            busy.update(re.findall(r"\d+", listed))
        busy.update(re.findall(r"\bissues?\s*#\s*(\d+)\b", text, re.IGNORECASE))
        busy.update(re.findall(r"\bgui/(\d+)\b", text, re.IGNORECASE))
print(",".join(sorted(busy, key=int)) or "-")
PY
}
# A failed run's child can leave commits on a local branch no remote holds (#771). The loop never pushes them (#361):
# janitor rule O keeps each such tree as auto/salvage-<n> ("<branch> <sha9> in <tree>"), and the next dispatch names the
# branch and sha, so the new run can reuse the work. A tree that is gone, or whose HEAD moved off that sha, is not named.
sg_salvage_note() {   # <issue>
  local f="${SG_STATE:-}/auto/salvage-$1" b sha _in w head
  [ -n "${SG_STATE:-}" ] && [ -f "$f" ] || return 0
  while read -r b sha _in w; do
    [ -n "$w" ] && [ -d "$w" ] || continue
    head=$(git -C "$w" rev-parse HEAD 2>/dev/null) || continue
    case "$head" in "$sha"*) ;; *) continue;; esac
    printf 'SALVAGE: an earlier run of this issue failed and left commit %s on local branch %s in %s; no remote holds it. Read that work first (git -C %s log -p %s) and reuse what is sound (git cherry-pick %s) instead of starting again.\n' "$sha" "$b" "$w" "$w" "$sha" "$sha"
  done < "$f"
}
# An old run can leave the issue worktree behind. The dispatcher owns that path, so it tells the next child when the
# exact expected checkout is safe to reuse. Every other state stays intact and is reported as a blocker (#65).
sg_issue_worktree_note() {   # <issue>: print child instructions only when worktrees/child/gui-<issue> already exists
  local n=$1 path branch root current state
  sg_salvage_note "$n"
  path="${SG_CHECKOUT%/}/worktrees/child/gui-$n"
  branch=$(sg_fork_branch "$n")
  [ -e "$path" ] || return 0
  path=$(cd "$path" 2>/dev/null && pwd -P) || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is unreadable. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "${SG_CHECKOUT%/}/worktrees/child/gui-$n"; return
  }
  root=$(git -C "$path" rev-parse --show-toplevel 2>/dev/null) || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is unreadable. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path"; return
  }
  root=$(cd "$root" 2>/dev/null && pwd -P) || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is unreadable. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path"; return
  }
  [ "$root" = "$path" ] || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is not its own checkout. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path"; return
  }
  current=$(git -C "$path" symbolic-ref --quiet --short HEAD 2>/dev/null) || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is detached; expected branch %s. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path" "$branch"; return
  }
  state=$(git -C "$path" status --porcelain --untracked-files=all 2>/dev/null) || {
    printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s cannot be checked; expected branch %s. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path" "$branch"; return
  }
  [ "$current" = "$branch" ] && [ -z "$state" ] && {
    printf 'REUSE ISSUE WORKTREE: Use the clean existing worktree at %s on branch %s as read-only evidence of earlier work. Do not create a fresh worktree, do not delete or reset this one, and create no branch of your own. Your own commits go in the worktree the run gives you (ADR-0145 rule 9).\n' "$path" "$branch"; return
  }
  printf 'PRESERVE ISSUE WORKTREE: The existing worktree at %s is on branch %s; expected branch %s%s. Do not reuse, reset, delete, or replace it. Preserve it and report this blocker in your result.\n' "$path" "$current" "$branch" "${state:+ and it is dirty}"
}
# An issue whose ledger is corrupt, or missing beside its old counters, is held (ADR-0149 rule 4): sg_ledger_held prints the
# cause (the issue's file and why) and exits 0, so no dispatch pass takes it, and it never falls back to the old counters. A
# ledger module that is not there, a ledger that reads, or a new issue with no file and no old counter holds nothing. The
# owner's `issue_ledger.py rearm` (or `migrate`, for the old counters) writes a fresh file and frees it.
sg_ledger_held() {
  local l; l="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/issue_ledger.py"
  [ "$(python3 "$l" read "$SG_REPO#$1" state 2>/dev/null)" = held ] || return 1
  python3 "$l" read "$SG_REPO#$1" held 2>/dev/null; return 0
}
# An issue the loop itself has marked not startable (#1858): a run that ended failed on it SG_HEALTH_REPEAT_COUNT times running
# leaves a `SQUAD-NOT-STARTABLE:` line among its comments, and every dispatch path reads that line before it starts -- so the loop
# cannot feed itself the same input again. health.sh writes it; autobug.sh and autofrontier.sh read it, once per candidate that got
# past their cheap filters. Absent the marker, a comments read costs one request and changes nothing.
# sg_not_startable <issue> [label]: 0 when the issue holds. The marker holds when it names this build or names none; a marker
# another build wrote holds nothing (ADR-0159, rule 3). The frozen:not-startable label (label=1, read by the caller) holds too, but
# only while no marker re-arms it: beside another build's marker it holds nothing, and with no marker at all it keeps its stop (rule 4).
sg_not_startable() {
  local out rc cur lab=${2:-}
  out=$(gh_api "repos/$SG_REPO/issues/$1/comments?per_page=100" -q '.[].body'); rc=$?
  # A refused read is not "no marker": it is no answer at all, and the caller's own guards still apply (#1855). Reading the
  # refusal as a negative here would be the same defect the wrapper in autobug.sh exists to stop.
  [ "$rc" -eq 0 ] || return 1
  cur=$(sg_loop_build 2>/dev/null) || cur=""
  printf '%s\n' "$out" | awk -v cur="$cur" -v lab="$lab" '
    /^SQUAD-NOT-STARTABLE:/ {
      seen = 1
      if (match($0, /build=[0-9a-f]+/)) {
        b = substr($0, RSTART + 6, RLENGTH - 6)
        if (cur != "" && b != cur) next      # written by another build: re-armed
      }
      held = 1; exit
    }
    END { if (held || (lab != "" && !seen)) exit 0; exit 1 }'
}
# A ticket whose acceptance commands name a repository path the repository does not hold is not dispatched (#729): the run
# would spend its budget and end failed on a command that cannot pass (run-20261021-043520-76754d named three scripts that did
# not exist). acpaths.py finds the paths; a path the ticket creates is not missing. sg_ac_refused <issue> <issue.json> <PASS>
# exits 0 when the ticket is refused: it queues one comment that names the paths, labels the issue needs-info (a skip label
# of both dispatchers), and prints the refusal. A check that cannot run refuses nothing. Under AF_DRY or AB_DRY it only prints.
sg_ac_refused() {
  local m p o rc
  m=$(python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/acpaths.py" "$2" "${SG_CHECKOUT:-.}" "${SG_BASE:-main}" "origin/${SG_BASE:-main}" 2>/dev/null); rc=$?
  [ "$rc" = 1 ] && [ -n "$m" ] || return 1
  p=$(printf '%s\n' "$m" | paste -sd, - | sed 's/,/, /g')
  [ -n "${AF_DRY:-}${AB_DRY:-}" ] && { echo "$3 would refuse #$1: its acceptance commands name missing paths: $p; needs-info"; return 0; }
  if [ "$(cat "$SG_STATE/auto/acmiss-$1" 2>/dev/null)" != "$p" ]; then   # one comment per set of paths, even when the label write fails
    printf 'SQUAD-AC-MISSING: the loop did not dispatch this ticket. Its acceptance criteria name commands on paths the repository does not hold: %s. A run cannot pass a command on a missing script. Fix the command, or name the path under "What to build" when the run must create it, then remove the needs-info label.\n' "$p" > "$(sg_cq_file "$1-acmissing.md")"
    mkdir -p "$SG_STATE/auto"; printf '%s\n' "$p" > "$SG_STATE/auto/acmiss-$1"
  fi
  o=$(gh_send --method POST "repos/$SG_REPO/issues/$1/labels" -f "labels[]=needs-info") || echo "$3 NOTE #$1: the needs-info label write failed, the next pass retries: ${o%%$'\n'*}" >&2
  echo "$3 refuse #$1: its acceptance commands name missing paths: $p; commented and labelled needs-info"
}
