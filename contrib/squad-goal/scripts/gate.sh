#!/bin/bash
# usage: gate.sh <command...>   Runs a gate under the machine-wide `gate` lock (#392), so two gates never run together.
# The branchland local gate, the wave entry gate, the promotion gate and a hand-started gate all come through here. A second gate
# waits for the first and then runs; it is never dropped, so the wait is long (SERIAL_WAIT_SECONDS, default 86400) and says so on one
# line naming the holder. A gate holder that died, or that outlived its run, loses the lock to the next gate (serial.sh).
# SERIAL_HOLD_SECONDS (default 7200) is the longest a gate may hold the lock. The exit code of the command is passed through.
# The hub reads the latest result from $SG_STATE/gate-status: `<epoch> TAB <kind> TAB <pass|fail> TAB <sha>`, and, when the
# gate ran the test battery, a fifth field, its selection (`selected N files` or `all: <reason>`; GATE_SELECT_FILE carries it
# out), and a sixth, the scope that selection names, `delta` or `full` (GATE_SCOPE_FILE carries it out). The reason in an
# `all:` line is why a delta refused.
# SG_GATE_KIND is fast for a tip or wave entry, full for a promotion to stg, and gate by default. A later gate replaces this
# hub line. A full result also writes $SG_STATE/gate-status-full, so a later fast result cannot replace promotion proof.
# An unproven outcome writes the entry's proof obligation (spec 1163): one record per entry in $SG_STATE/obligations,
# `<repository> TAB <tree sha> TAB <base> TAB <checks that did not run> TAB <scope> TAB <deadline>`. The entry is the
# repository, the tree sha and the base; the base is what GATE_BASE_FILE carries (as GATE_SCOPE_FILE carries the scope),
# else SG_BASE, else `-`. The checks are the outcome's not-run list, `all` when it named none; the scope is the gate's,
# `full` when it named none, because a check that did not run cannot be said to cover less than the whole tree (ADR-0018).
# The deadline is the proof bound past the gate (ADR-0163, SG_PROMOTE_BOUND_SECONDS, 900 s), never a new setting. A second
# obligation for the same entry merges into its record: the checks join, the wider scope wins and the deadline never moves
# later. A pass or a fail writes none, because each already answers the job.
# The sha is the HEAD the gate ran at, read from the working directory. The lock lives in $SG_STATE/locks/gate. With SG_STATE
# unset it is the one machine-wide /tmp/sg-state, never a TMPDIR path: TMPDIR is per user on macOS, so two users or a launchd
# job would resolve two lock directories and run two gates together.
# The lock and the budget are taken in one order: the lock first, the slots second (#1166). A gate queued behind another gate holds
# no slot while it waits. Inside the lock it takes SG_GATE_WORKERS gate slots (#566) of the machine-wide budget (caps.gates,
# squad/caps.py) in one locked step: all it asks for when they are free, else the free slots when they number two or more (#734),
# so a third gate runs with 2 to 4 workers instead of giving up; fewer than two free is no take, so a gate never holds part of its
# slots while it waits for the rest. The take is one try, never a wait: a gate that cannot take its slots gives the lock back at
# once and exits 73, so no gate queues behind a budget it cannot use and no resource is held while the other is awaited.
# SQUAD_GATE_WORKERS is the count the gate actually took, and the battery's pytest runs with it. Slots are re-entrant as the lock
# is: a gate inside a gate holder (SG_SERIAL_HELD names gate) takes none and keeps the holder's SQUAD_GATE_WORKERS. A project file
# that lowers caps.gates below SG_GATE_WORKERS gives the gate min(workers, ceiling) slots, never a wait for slots that cannot exist.
# Every exit path gives both back: serial.sh releases the lock, and the lock holder's EXIT trap releases its slots. A process that
# dies holding either loses it to the next caller: a lock whose holder is gone is reaped by serial.sh, and a lease whose pid is
# gone is reclaimed by the next count (squad/caps.py). Try mode (SERIAL_TRY) skips a held lock at once (73); a lock wait is bound
# by SERIAL_WAIT_SECONDS and by the call deadline of a run (SG_CALL_DEADLINE_SECONDS), says so on one line, and ends in 75.
# `gate.sh --status <kind> <pass|fail> <sha>` writes status for a gate whose sha is not the working directory's HEAD: a command
# that calls it sets SG_GATE_STATUS=self, so the gate does not overwrite it with the HEAD it was started in. Each line is written
# by a temp file and a mv, so no reader sees half of it. SG_STATE unset: /tmp/sg-state, never TMPDIR.
# Each finished gate also appends one line to $SG_STATE/gate-history: `<finish epoch> TAB <kind> TAB <result> TAB <sha> TAB <start epoch>
# TAB <selection> TAB <scope> TAB <outcome> TAB <reason> TAB <ran> TAB <not run> TAB <slots> TAB <red>`. The selection and the scope are empty
# when the gate ran no battery or names none. The outcome is pass, fail or unproven (spec 1163): the gate's own verdict, written by
# the command to GATE_OUTCOME_FILE as `<outcome> TAB <reason> TAB <ran> TAB <not run>`; with none, pass or fail by the exit code,
# and a gate stopped before any check answered is unproven. The result stays pass or fail, as the hub reads it. The reason is one
# line, `-` when none; ran and not run list the checks, `-` when none; slots is the count of gate slots the gate took. The red field
# is `-`, `red` for a measured red this gate recorded, `red-stands <sha>` for a red of the same tree that this attempt did not
# clear, `cleared-green <sha>` or `cleared-fix <red sha>..<sha>` for the two ways a red is cleared (#1170). The duration
# is finish minus start, and start is read inside the lock, after the wait: it is the gate's run time, never its queue time. A gate
# that sets SG_GATE_STATUS=self writes no history line, as it writes no status line and no obligation. The file only grows: the throughput report
# reads it, and nothing rewrites it.
# A caller that names GATE_OUTCOME_READ_FILE gets the same outcome line copied there inside the lock, so it decides on the word
# the gate wrote, never on the exit code alone (spec 1163, #1174). The variable goes before the command runs, so a gate inside a
# gate never overwrites the caller's copy.
# One clock starts when the gate takes the lock (ADR-0163, #1171): SG_GATE_BOUND_SECONDS, else SG_PROMOTE_BOUND_SECONDS, else 900.
# The command reads GATE_START_EPOCH, GATE_DEADLINE_EPOCH, GATE_BOUND_SECONDS and GATE_DELTA_RESERVE_SECONDS, so its checks, its
# retry and the delta that follows spend one budget. A command that does not answer at the deadline is stopped with every
# descendant, five seconds past it so a command that is writing its record is not cut in half, and the outcome is unproven
# with a reason that says bounded. A defect the command already wrote is fail, and so is any red that stands for the tree.
here="$(cd "$(dirname "$0")" && pwd)"; st=${SG_STATE:-/tmp/sg-state}
write_status() {  # <path> <line>
  printf '%s\n' "$2" > "$1.$$" && mv -f "$1.$$" "$1"
}
# --- a measured red stands for its tree (spec 1163, #1170) ---
# One standing record per repository tree in $SG_STATE/gate-reds, `<red epoch> TAB <repo> TAB <tree sha> TAB <scope> TAB <the
# checks that reported it> TAB <reason> TAB - TAB -`. Only a fail that names the checks it ran is a measured red: a bare exit
# code reports no check and never stands as one. A pass clears the record only when it covers the red's scope and ran every
# check the red names; the gate's history line then names which of the two cleared it, `cleared-green <sha>` for a pass on the
# same tree or `cleared-fix <red sha>..<sha>` for a pass on a descendant, so the clearing is on the record forever and the
# standing set holds only reds that still answer for a tree.
# The one attempt bound (ADR-0163, #1171): SG_GATE_BOUND_SECONDS, else the promotion bound, else 900. The delta's share of
# the same budget is SG_GATE_DELTA_RESERVE_SECONDS, else the promotion fallback share, else 240, and the gate command reads
# both, so the attempt it makes -- its checks, its retry and the delta that follows -- spends one clock.
gate_bound() {
  local b=${SG_GATE_BOUND_SECONDS:-${SG_PROMOTE_BOUND_SECONDS:-900}}; case $b in ''|*[!0-9]*|0) b=900;; esac; printf '%s\n' "$b"
}
gate_reserve() {
  local r=${SG_GATE_DELTA_RESERVE_SECONDS:-${SG_PROMOTE_FALLBACK_SECONDS:-240}}; case $r in ''|*[!0-9]*) r=240;; esac; printf '%s\n' "$r"
}
red_dir() { printf '%s\n' "$st/gate-reds"; }
red_file() {  # <repo> <sha>: the record of that tree, standing or cleared
  printf '%s\n' "$(red_dir)/$(printf '%s\t%s' "$1" "$2" | cksum | cut -d' ' -f1)"
}
red_standing() {  # <repo> <sha>: the standing red of that tree, empty when none
  local f fc1 fc2 fc3 fc4 fc5 fc6 fcl; f=$(red_file "$1" "$2"); [ -r "$f" ] || return 0
  IFS=$'\t' read -r fc1 fc2 fc3 fc4 fc5 fc6 fcl _ < "$f"
  [ "$fcl" = - ] && printf '%s\n' "$f"
}
red_record() {  # <repo> <sha> <scope> <checks> <reason>: the red this attempt measured, standing for its tree
  local f sc=${3:--} ck=${4:--} rs=${5:--}; f=$(red_file "$1" "$2"); mkdir -p "$(red_dir)" 2>/dev/null || return 0
  printf '%s\t%s\t%s\t%s\t%s\t%s\t-\t-\n' "$(date +%s)" "$1" "$2" "$sc" "$ck" "$rs" > "$f.$$" && mv -f "$f.$$" "$f"
}
red_clear() {  # <file>: the pass cleared it; the history line names the clearing, so the standing record goes
  local c1 c2 c3 c4 c5 c6 ccl cce; IFS=$'\t' read -r c1 c2 c3 c4 c5 c6 ccl cce < "$1" 2>/dev/null
  [ "$ccl" = - ] || return 0
  rm -f "$1"
}
red_covers() {  # <red scope> <red checks> <pass scope> <pass ran>: the pass covered the ground the red measured
  local rs=$1 rch=$2 ps=$3 pr=$4 c
  case "$rs" in ''|-) ;; *) case "$rs:$ps" in full:full|delta:full|delta:delta) ;; *) return 1;; esac;; esac
  case "$rch" in ''|-|unknown) return 0;; esac
  for c in $rch; do case " $pr " in *" $c "*) ;; *) return 1;; esac; done
}
red_ancestor() {  # <repo> <sha>: a standing red on an ancestor tree, empty when none
  local f c1 c2 rsha c4 c5 c6 c7 c8
  for f in "$(red_dir)"/*; do [ -f "$f" ] || continue
    IFS=$'\t' read -r c1 c2 rsha c4 c5 c6 c7 c8 < "$f"
    [ "$c2" = "$1" ] && [ "$c7" = - ] && [ "$rsha" != "$2" ] || continue
    git merge-base --is-ancestor "$rsha" "$2" 2>/dev/null && { printf '%s\n' "$f"; return; }
  done
}
# fold_red <repo> <sha> <scope> <result> <outcome> <reason> <ran> <notran>: the red's precedence over this attempt, in the
# globals FRES, FOC, FRS, FRAN, FNOT and FRED. A measured red wins over a later unproven attempt or a pass of another scope;
# a covered pass on the same tree clears it as green, a covered pass on a descendant as a fix. FCHANGED says the fold turned
# the attempt away from what its checks reported, so the exit code is the fail's.
fold_red() {
  FRES=$4; FOC=$5; FRS=$6; FRAN=$7; FNOT=$8; FRED=-; FCHANGED=
  [ -n "$2" ] || return 0
  local rf r1 r2 ra rsc rch rrs r7 r8
  if [ "$5" = fail ] && [ -n "$7" ] && [ "$7" != - ]; then
    FRES=fail; red_record "$1" "$2" "$3" "$7" "$6"; FRED=red; return 0
  fi
  rf=$(red_standing "$1" "$2")
  if [ -n "$rf" ]; then
    IFS=$'\t' read -r r1 r2 ra rsc rch rrs r7 r8 < "$rf"
    if [ "$5" = pass ] && red_covers "$rsc" "$rch" "$3" "$7"; then
      red_clear "$rf" green "${SG_GATE_KIND:-gate} $2" "$(date +%s)"
      FRED="cleared-green $2"
    else
      FRES=fail; FOC=fail; FCHANGED=1; FRAN=$rch; FNOT=-
      FRS="red stands on $ra and this attempt does not clear it: $rrs"
      FRED="red-stands $ra"
    fi
    return 0
  fi
  [ "$5" = pass ] || return 0
  rf=$(red_ancestor "$1" "$2")
  [ -n "$rf" ] || return 0
  IFS=$'\t' read -r r1 r2 ra rsc rch rrs r7 r8 < "$rf"
  red_covers "$rsc" "$rch" "$3" "$7" || return 0
  red_clear "$rf" fix "$ra..$2" "$(date +%s)"
  FRED="cleared-fix $ra..$2"
}
repo_name() {  # the repository an obligation names: SG_REPO (owner/name), else origin's owner/name, else `-`
  local u
  [ -n "${SG_REPO:-}" ] && { printf '%s\n' "$SG_REPO"; return 0; }
  u=$(git remote get-url origin 2>/dev/null) || u=
  case $u in *github.com[:/]*) u=${u#*github.com[:/]};; esac
  u=${u%.git}; u=${u%/}
  case $u in ?*/?*) printf '%s\n' "$u";; *) printf '%s\n' -;; esac
}
merge_checks() {  # <a> <b>: the named checks of two records for one entry; `all` absorbs, and `unknown` yields to a name
  local a=$1 b=$2 c
  case " $a $b " in *" all "*) printf '%s\n' all; return 0;; esac
  c=$(printf '%s\n%s\n' "$a" "$b" | tr ' ' '\n' | /usr/bin/grep -vxE -- '-|unknown' | /usr/bin/grep -v '^$' | sort -u | tr '\n' ' ')
  if [ -n "$c" ]; then printf '%s\n' "${c% }"; return 0; fi
  case " $a $b " in *" unknown "*) printf '%s\n' unknown;; *) printf '%s\n' all;; esac
}
obligation() {  # <repository> <tree sha> <base> <checks> <scope> <finish epoch>: one record per entry, merged never duplicated
  local repo=$1 sha=$2 base=$3 checks=$4 scope=$5 fin=${6:-} dir=$st/obligations key f orp osh oba och osc odl bound dl
  [ -n "$sha" ] || return 0
  [ -n "$repo" ] || repo=-
  [ -n "$base" ] || base=-
  [ "$checks" = - ] && checks=all
  [ -n "$scope" ] || scope=full
  case $fin in ''|*[!0-9]*) fin=$(date +%s);; esac
  bound=${SG_PROMOTE_BOUND_SECONDS:-900}; case $bound in ''|*[!0-9]*) bound=900;; esac
  dl=$((fin + bound))   # the deadline counts from the gate's finish, the epoch the history line carries
  mkdir -p "$dir" 2>/dev/null || return 0
  key=$(printf '%s\t%s\t%s' "$repo" "$sha" "$base" | cksum | cut -d' ' -f1)
  f=$dir/$key
  if [ -r "$f" ]; then
    IFS=$'\t' read -r orp osh oba och osc odl < "$f"
    checks=$(merge_checks "$och" "$checks")
    case " $osc $scope " in *" full "*) scope=full;; esac
    case $odl in ''|*[!0-9]*) ;; *) [ "$odl" -lt "$dl" ] && dl=$odl;; esac
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$repo" "$sha" "$base" "$checks" "$scope" "$dl" > "$f.$$" && mv -f "$f.$$" "$f"
}
status() {  # <kind> <result> <sha> [<test selection>] [<test scope>]
  mkdir -p "$st" 2>/dev/null || return 0
  printf -v line '%s\t%s\t%s\t%s' "$(date +%s)" "$1" "$2" "$3"
  [ -z "${4:-}" ] || line="$line"$'\t'"$4"
  [ -z "${5:-}" ] || line="$line"$'\t'"$5"
  write_status "$st/gate-status" "$line" || return
  [ "$1" != full ] || write_status "$st/gate-status-full" "$line"
}
append_line() {  # <path> <line>: one short write at the end of the file, never a rewrite
  printf '%s\n' "$2" >> "$1"
}
gate_history() {  # <kind> <result> <sha> <start epoch> <selection> <scope> <outcome> <reason> <ran> <not run> <slots> <red>
  fin=$(date +%s)   # the finish epoch the line carries, and the epoch the obligation's deadline counts from (note)
  mkdir -p "$st" 2>/dev/null || return 0
  printf -v line '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' "$fin" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}" "${11}" "${12:--}"
  append_line "$st/gate-history" "$line"
}
case "${1:-}" in
  --status) [ $# = 4 ] || { echo "usage: gate.sh --status <kind> <pass|fail> <sha>" >&2; exit 2; }; status "$2" "$3" "$4"; exit 0;;
  --inner)  # inside the lock: take the slots, then run the gate. The take is one try, never a wait: a gate that cannot take its
            # slots exits 73 at once with none, and serial.sh releases the lock on that exit, so it holds neither resource while
            # it waits for the other. Every later exit path runs the EXIT trap and gives the slots back.
    shift
    . "$here/caps.sh"
    # The lease names the lock holder, this process's parent: a waiter that reaps the lock holder past its deadline then also
    # frees the lease, so a stopped run never leaves a child's gate holding slots (ADR-0051).
    SQUAD_GATE_WORKERS=$(sg_caps take gates "gate-$$" --count "${SG_GATE_WORKERS:-6}" --pid "${PPID:-$$}" --target "$PWD" --try) || exit $?
    export SQUAD_GATE_WORKERS
    trap 'exit 143' TERM; trap 'exit 130' INT   # a stop past the hold deadline still runs the EXIT trap below
    trap 'sg_caps release gates "gate-$$"' EXIT
    bash "$here/gate.sh" --run "$@"; exit $?;;
  --run)    # inside the lock: run the command, then write the line. One clock starts when the lock is taken (ADR-0163, #1171).
    shift
    outread=${GATE_OUTCOME_READ_FILE:-}; unset GATE_OUTCOME_READ_FILE   # the caller's copy: the command never sees it
    export GATE_SELECT_FILE GATE_SCOPE_FILE GATE_OUTCOME_FILE GATE_BASE_FILE
    GATE_SELECT_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-select.XXXXXX" 2>/dev/null) || GATE_SELECT_FILE=
    GATE_SCOPE_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-scope.XXXXXX" 2>/dev/null) || GATE_SCOPE_FILE=
    GATE_OUTCOME_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-outcome.XXXXXX" 2>/dev/null) || GATE_OUTCOME_FILE=
    GATE_BASE_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-base.XXXXXX" 2>/dev/null) || GATE_BASE_FILE=
    start=$(date +%s)
    # One attempt identity per logical gate invocation (#1255): the aid is a token written to a marker before the
    # gate runs, and a replay for the same card and tip reads that marker instead of drawing a new token. Two runs
    # in one second still differ, because each draws its own token; and a crash-restart answers as the same attempt
    # whatever pid or clock it carries, because the token is the one the marker holds, never the replay's own.
    gaid_marker=
    if [ -n "${SG_GATE_CARD:-}" ] && [ -z "${SG_GATE_AID:-}" ]; then
      sha0=$(git rev-parse HEAD 2>/dev/null)
      gaid_marker="$st/gate-attempt-$SG_GATE_CARD-${sha0:0:12}.start"
      if [ -r "$gaid_marker" ]; then SG_GATE_AID=$(cat "$gaid_marker" 2>/dev/null)
      else SG_GATE_AID="gate-$start-$$-$RANDOM"; { mkdir -p "$st" 2>/dev/null && printf '%s\n' "$SG_GATE_AID" > "$gaid_marker"; } 2>/dev/null || :; fi
      export SG_GATE_AID
    fi
    bound=$(gate_bound); deadline=$(( start + bound ))
    export GATE_START_EPOCH=$start GATE_DEADLINE_EPOCH=$deadline GATE_BOUND_SECONDS=$bound
    export GATE_DELTA_RESERVE_SECONDS=$(gate_reserve)
    note() {  # <result> <outcome if the command wrote none> <reason if none>: one record, the red's precedence folded in
      note_rc=
      if [ "${SG_GATE_STATUS:-}" != self ]; then
        sha=$(git rev-parse HEAD 2>/dev/null); sel=$(cat "$GATE_SELECT_FILE" 2>/dev/null); scope=$(cat "$GATE_SCOPE_FILE" 2>/dev/null)
        oc= rs= ran= notran=
        [ -z "$GATE_OUTCOME_FILE" ] || IFS=$'\t' read -r oc rs ran notran < "$GATE_OUTCOME_FILE" 2>/dev/null
        [ -n "$oc" ] || { oc=$2; rs=$3; ran=-; notran=-; }
        fold_red "$(repo_name)" "$sha" "$scope" "$1" "$oc" "$rs" "${ran:--}" "${notran:--}"
        # The caller's copy of the word this gate wrote, written inside the lock so no other gate can answer for it (#1174).
        # It carries the folded outcome: a red that stands answers for the tree whatever the attempt reported.
        [ -z "$outread" ] || { printf '%s\t%s\t%s\t%s\n' "$FOC" "$FRS" "$FRAN" "$FNOT" > "$outread.$$" 2>/dev/null \
          && mv -f "$outread.$$" "$outread" 2>/dev/null; }
        status "${SG_GATE_KIND:-gate}" "$FRES" "$sha" "$sel" "$scope"
        gate_history "${SG_GATE_KIND:-gate}" "$FRES" "$sha" "$start" "$sel" "$scope" "$FOC" "$FRS" "${FRAN:--}" "${FNOT:--}" "${SQUAD_GATE_WORKERS:-0}" "$FRED"
        # The gate-attempt journal (#1255): one attempt record per verdict this invocation drew, for the card
        # the gate names (SG_GATE_CARD) and the tip it ran at. A deadline stop reaches here too: its verdict is
        # unproven, and an unproven attempt counts like any other. The record's identity is (card, tip, aid), and
        # the aid is this invocation's start marker, so a crash between the append and the field write, then a
        # replay under a new process, answers as the record itself and never appends twice. SG_GATE_AID names a
        # chain of gates that is one attempt (a landing's retry chain): the first record answers for the chain.
        gaok=1
        if [ -n "${SG_GATE_CARD:-}" ]; then
          gaj=${SG_GATE_JOURNAL:-}
          if [ -z "$gaj" ] && [ -n "${SG_BOARD_CACHE:-}" ]; then
            gaj=$(python3 "$here/gate_attempts.py" journal "$SG_BOARD_CACHE" "${SG_REPO:-$(repo_name)}" 2>/dev/null) || gaj=
          fi
          if [ -n "$gaj" ]; then
            # A failed append is named on stderr, never swallowed: the gate's verdict stands, and the journal
            # step is loud so a gate that journaled nothing is visible on the record it printed.
            if ! garec=$(python3 "$here/gate_attempts.py" record "$gaj" "$SG_GATE_CARD" "$sha" "$FOC" "${SG_GATE_AID:-}" "$fin" 2>&1); then
              echo "gate: WARNING: the attempt of card $SG_GATE_CARD at ${sha:0:9} was not journaled: $garec" >&2; gaok=
            fi
            # The journal is the source and the card's Gate Tries is its projection (#1255, ADR-0170): the count
            # is replayed from the whole journal, so a crash between the append and the field write answers one
            # count on the next projection. A projection that fails is named, never dropped silently.
            if ! gat=$(python3 "$here/gate_attempts.py" count "$gaj" "$SG_GATE_CARD" 2>&1); then
              echo "gate: WARNING: the Gate Tries count of card $SG_GATE_CARD did not replay: $gat" >&2; gaok=
            elif [ -n "${SG_STATE:-}" ] && [ -n "${SG_OWNER:-}" ] && [ -n "${SG_PROJECT:-}" ]; then
              if ! gaw=$(python3 "$here/board.py" --gate-tries "$SG_OWNER" "$SG_PROJECT" "$SG_GATE_CARD" "$gat" 2>&1); then
                echo "gate: WARNING: the Gate Tries projection of card $SG_GATE_CARD failed: $gaw" >&2; gaok=
              fi
            fi
          fi
        fi
        # The start marker holds the identity for a replay: it is dropped only once the append and its projection
        # answered, so a crash before that lets the next gate of the same card and tip answer as the same attempt.
        [ -z "$gaid_marker" ] || [ -z "$gaok" ] || rm -f "$gaid_marker" 2>/dev/null || :
        if [ "$FOC" = unproven ]; then
          base=$(cat "$GATE_BASE_FILE" 2>/dev/null); [ -n "$base" ] || base=${SG_BASE:-}
          obligation "$(repo_name)" "$sha" "$base" "${FNOT:--}" "$scope" "$fin"
        fi
        # The exit code follows the record: a pass the red turned to fail, and an unproven attempt the red turned to fail,
        # both exit 1, never 0 and never the unproven code.
        [ "$FRES" = pass ] && note_rc=0
        if [ "$FRES" = fail ] && { [ "$1" = pass ] || [ -n "$FCHANGED" ]; }; then note_rc=1; fi
      fi
      [ -z "$GATE_SELECT_FILE" ] || rm -f "$GATE_SELECT_FILE"
      [ -z "$GATE_SCOPE_FILE" ] || rm -f "$GATE_SCOPE_FILE"
      [ -z "$GATE_OUTCOME_FILE" ] || rm -f "$GATE_OUTCOME_FILE"
      [ -z "$GATE_BASE_FILE" ] || rm -f "$GATE_BASE_FILE"
    }
    # stop_tree <pid>: every descendant as it stands now, TERM then KILL, so a child that ignores TERM still goes and a child
    # its parent leaves behind is in the list taken before the first signal.
    tree_pids() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do tree_pids "$c"; done; printf '%s\n' "$1"; }
    stop_tree() { local list x
      list=$(mktemp "${TMPDIR:-/tmp}/gate-stop.XXXXXX" 2>/dev/null)
      if [ -z "$list" ]; then kill -TERM "$1" 2>/dev/null; sleep 2; kill -KILL "$1" 2>/dev/null; return 0; fi
      tree_pids "$1" > "$list"
      while read -r x; do [ -n "$x" ] && kill -TERM "$x" 2>/dev/null; done < "$list"; sleep 2
      while read -r x; do [ -n "$x" ] && kill -KILL "$x" 2>/dev/null; done < "$list"
      rm -f "$list"; }
    timedout=$(mktemp "${TMPDIR:-/tmp}/gate-timeout.XXXXXX" 2>/dev/null) || timedout=; [ -z "$timedout" ] || rm -f "$timedout"
    wd=
    trap 'kill "$wd" 2>/dev/null; note fail unproven "bounded: the gate was stopped at its deadline and no check reported a defect"; [ "$FOC" = fail ] && exit 1; exit "${note_rc:-143}"' TERM
    "$@" & cpid=$!
    # The command is the one that knows how to stop its own tree and write what it found; the wrapper's own stop waits a
    # short grace past the deadline, so a command that answers at the deadline is never killed mid-record. A command that
    # does not answer is stopped here, with every descendant (ADR-0163, #1171).
    ( sleep "$(( bound + 5 ))"; kill -0 $$ 2>/dev/null || exit 0; [ $(( $(date +%s) - start )) -ge "$bound" ] || exit 0
      [ -z "$timedout" ] || : > "$timedout"; stop_tree "$cpid" ) >/dev/null 2>&1 & wd=$!
    wait "$cpid" 2>/dev/null; rc=$?
    kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
    if [ -n "$timedout" ] && [ -e "$timedout" ]; then
      rm -f "$timedout"
      note fail unproven "bounded: the gate passed its ${bound}s deadline and no check reported a defect"
      echo "gate: bounded: the gate passed its ${bound}s deadline and no check reported a defect"
      [ "$FOC" = fail ] && exit 1
      exit "${note_rc:-4}"
    fi
    [ -z "$timedout" ] || rm -f "$timedout"
    if [ $rc = 0 ]; then note pass pass -; else note fail fail "exit $rc"; fi
    [ -z "$note_rc" ] || rc=$note_rc
    exit $rc;;
esac
[ $# -gt 0 ] || { echo "usage: gate.sh <command...>   (lock in \$SG_STATE/locks/gate; SG_STATE unset: /tmp/sg-state, never TMPDIR)" >&2; exit 2; }
# The lock's hold is the attempt bound plus a backstop: the gate's own clock stops its tree first and writes the outcome,
# and a holder that wedges past that loses the lock to the next gate (serial.sh).
export SERIAL_WAIT_SECONDS=${SERIAL_WAIT_SECONDS:-86400} SERIAL_HOLD_SECONDS=${SERIAL_HOLD_SECONDS:-$(( $(gate_bound) + 300 ))}
case ":${SG_SERIAL_HELD:-}:" in
  *":gate:"*) exec bash "$here/gate.sh" --run "$@" ;;   # inside a gate holder: no slots of its own, it keeps the holder's count
esac
exec bash "$here/serial.sh" gate bash "$here/gate.sh" --inner "$@"   # exec: the lock holder is this process, and --inner takes the slots inside it
