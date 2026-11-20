#!/bin/bash
# usage: gate.sh <command...>   Runs a gate under the machine-wide `gate` lock (#392), so two gates never run together.
# The branchland local gate, the wave entry gate, the promotion gate and a hand-started gate all come through here. A second gate
# waits for the first and then runs; it is never dropped, so the wait is long (SERIAL_WAIT_SECONDS, default 86400) and says so on one
# line naming the holder. A gate holder that died, or that outlived its run, loses the lock to the next gate (serial.sh).
# SERIAL_HOLD_SECONDS (by default the full bound, the slot wait and 300 s, #2379) is the longest a gate may hold the lock.
# The exit code of the command is passed through.
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
# The deadline is the gate bound (ADR-0163, SG_GATE_BOUND_SECONDS, 900 s by default). A second
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
# TAB <selection> TAB <scope> TAB <outcome> TAB <reason> TAB <ran> TAB <not run> TAB <slots> TAB <red> TAB <stage> TAB <load start>
# TAB <load finish> TAB <tree> TAB <wait> TAB <wall> TAB <cpu> TAB <reused>`. The selection and the scope are empty
# when the gate ran no battery or names none. The outcome is pass, fail or unproven (spec 1163): the gate's own verdict, written by
# the command to GATE_OUTCOME_FILE as `<outcome> TAB <reason> TAB <ran> TAB <not run>`; with none, pass or fail by the exit code,
# and a gate stopped before any check answered is unproven. The result stays pass or fail, as the hub reads it. The reason is one
# line, `-` when none; ran and not run list the checks, `-` when none; slots is the count of gate slots the gate took. The red field
# is `-`, `red` for a measured red this gate recorded, `red-stands <sha>` for a red of the same tree that this attempt did not
# clear, `cleared-green <sha>` or `cleared-fix <red sha>..<sha>` for the two ways a red is cleared (#1170). The stage field (#1410)
# is SG_GATE_CALLER: entry (the wave entry), child (the child's own last check), landing (branchland's gate on a work branch),
# tip (the wave-tip gate) or other; a row from before the field has 13 fields, and the loop report reads it as an unknown stage.
# Two load fields follow it (#1691, gate_load below); a row from before them has 14. The entry mode is the seventeenth field
# (#1798): `gated` when the gate ran the battery, `reused` when a kept green pass answered for the tree (ADR-0168), `skipped`
# when it ran no battery; a row from before the field has 16, and every reader answers it as a gate with no mode.
# SG_GATE_ENTRY_MODE names the word a caller knows: the wave entry sets `gated` for its gate, and a gate that names no word takes
# `gated` when it wrote a selection or a scope and `skipped` when it wrote neither. A gate whose verdict was reused is `reused`,
# whatever word its caller named.
# The stage's time is told from the work: `start` is read inside the lock, after the wait, so the duration (finish minus start)
# is the gate's run time; `<tree>`, `<wait>`, `<wall>`, `<cpu>` and `<reused>` follow the mode and say the rest (#1856). `wait`
# is the whole seconds from the gate's own start to that clock, the lock wait and the slot wait together, and it is gate wait,
# never run time (ADR-0166); `wall` is finish minus start; `cpu` is the user and sys time the gate's children spent; `tree` is
# the tree sha the gate read (HEAD^{tree}, so a rebase that keeps every file is one tree); and `reused` is `yes` when a green
# verdict for the same tree and suite answered instead of a battery (ADR-0168), `no` otherwise. A gate cancelled that way is
# still one row (gate_reuse below), mode `reused`, with wait, wall and cpu of 0 and its start at its finish, so a saved gate is
# counted, a reader of gate time counts it in neither, and the share of a gate stage that is lock wait is read from the window
# (hub_profiler.gate_queue), never inferred. A row from before these fields has 17 and reads as an unknown wait and no tree.
# The runner field (#1927) is the proof that produced the verdict: `ci` when the gate's command names the CI half of a
# hybrid proof (GATE_RUNNER), else `local`; the run id field names that CI run's id, `-` when none; a batch landing gate
# (SG_GATE_BATCH, #2058) writes `batch:<size>:<runs>` there instead of `-`. A row from before these
# two fields has 22 and reads as a local gate with no run. The two slot clocks follow them (#2317): `<slot ask>` is the
# instant the gate asked for its slots inside the lock and `<slot got>` the instant it held them, so a reader splits the
# gate stint into its wait and its run (ADR-0166); a gate that took no slots of its own, a reused verdict or a skipped
# entry, writes `-` in both, because it holds no slot record. A row from before these two fields has 24 and reads as a
# gate with no slot record. A gate that sets SG_GATE_STATUS=self writes no history line, as it
# writes no status line and no obligation. The file only grows: the throughput report
# reads it, and nothing rewrites it.
# A caller that names GATE_OUTCOME_READ_FILE gets the same outcome line copied there inside the lock, so it decides on the word
# the gate wrote, never on the exit code alone (spec 1163, #1174). The variable goes before the command runs, so a gate inside a
# gate never overwrites the caller's copy.
# One clock starts when the gate takes the lock (ADR-0163, #1171): SG_GATE_BOUND_SECONDS, else 900.
# The command reads GATE_START_EPOCH, GATE_DEADLINE_EPOCH and GATE_BOUND_SECONDS, so its checks and its retry spend one budget.
# A command that runs the full battery gets a second clock (#2379, ADR-0169 rule 9): GATE_FULL_BOUND_SECONDS, measured from this
# machine's full-battery gates (sg_full_bound), and GATE_FULL_DEADLINE_EPOCH. The gate moves its stop there only once the
# command wrote `full` to GATE_SCOPE_FILE, and a delta command keeps the delta bound.
# A command that does not answer at the deadline is stopped with every
# descendant, five seconds past it so a command that is writing its record is not cut in half, and the outcome is unproven
# with a reason that says bounded. A defect the command already wrote is fail, and so is any red that stands for the tree.
# A gate watches the process that asked for it (#1576). The outermost gate exports that pid as SG_GATE_PARENT before the lock is
# taken, so --inner and --run, which serial.sh forks below the lock holder, watch the caller and never their own parent. When
# that process is gone (or this one is reparented to pid 1) nobody waits for the answer any more: the gate stops its command,
# names the orphan on one line of its log, and gives the lock and its slots back, instead of holding them for its whole bound
# while every later gate exits 73 on a budget it cannot use. A suite that is SIGKILLed runs no trap, so the watch is the gate's
# own. A gate whose caller lives never takes that path.
here="$(cd "$(dirname "$0")" && pwd)"; st=${SG_STATE:-/tmp/sg-state}
. "$here/caps.sh" 2>/dev/null || : "${SG_PS:=ps}"   # SG_PS in every branch, the --run child too (#1508)
# (#2306) The gate command never writes a GIT_DIR its caller exported. A caps.sh with no scrub refuses the gate, never skips it.
sg_git_env_scrub || { echo "gate.sh: $here/caps.sh gave no sg_git_env_scrub, so a hook's GIT_DIR would reach the gate; refused" >&2; exit 2; }
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
# The one attempt bound (ADR-0163, #1171): SG_GATE_BOUND_SECONDS, else 900, and the gate command reads it, so the attempt it
# makes -- its checks and its retry -- spends one clock.
gate_bound() {
  local b=${SG_GATE_BOUND_SECONDS:-900}; case $b in ''|*[!0-9]*|0) b=900;; esac; printf '%s\n' "$b"
}
# (#2379, ADR-0169 rule 9) The full-battery bound: a gate whose command names the full scope (GATE_SCOPE_FILE) runs to it
# instead of the delta bound. The outermost gate reads it once (sg_full_bound, measured from this machine's gate history) and
# exports it as GATE_FULL_BOUND_SECONDS, so the lock hold, the clock and the command read one value. Never under the delta bound.
gate_full_bound() {
  local b f; b=$(gate_bound); f=${GATE_FULL_BOUND_SECONDS:-}
  case $f in ''|*[!0-9]*|0) f=$(sg_full_bound "$b" 2>/dev/null);; esac
  case $f in ''|*[!0-9]*|0) f=$b;; esac
  [ "$f" -ge "$b" ] || f=$b; printf '%s\n' "$f"
}
# The longest a priority caller (tip, landing) waits for its slots: the gates that hold them end inside the longer of the gate
# ceiling (SG_GATE_MAX_SECONDS) and the full bound, so the wait is that plus a margin (#2009, #2379).
gate_slot_wait() {
  local m=${SG_GATE_MAX_SECONDS:-900} f; case $m in ''|*[!0-9]*|0) m=900;; esac
  f=$(gate_full_bound); [ "$f" -gt "$m" ] && m=$f; printf '%s\n' "$(( m + 60 ))"
}
# The orphan watch (#1576). gate_parent_gone <pid> is true when the system no longer answers for the process, or answers Z: a
# killed process its own parent has not reaped yet is gone, as serial.sh reads it. A pid of 1 or of nothing names no caller, and
# a read that cannot tell (ps refused the pid) is never taken for a death: an alive caller must never take this path.
gate_parent_gone() {
  local p=$1 s
  case $p in ''|*[!0-9]*|1) return 1;; esac
  kill -0 "$p" 2>/dev/null || return 0
  s=$($SG_PS -o stat= -p "$p" 2>/dev/null | cut -c1)
  [ "$s" = Z ]
}
# gate_orphaned: true when the process that asked for this gate is gone, or when this process was reparented to pid 1, which is
# the same loss when the caller is the gate's own parent. It runs in the --run child, so `$$` is the gate it belongs to.
gate_orphaned() {
  if [ -n "${SG_GATE_PARENT:-}" ]; then gate_parent_gone "$SG_GATE_PARENT" && return 0; fi
  [ "$($SG_PS -o ppid= -p "$$" 2>/dev/null | tr -d ' ')" = 1 ]
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
  # (#2379) A full-scope obligation is settled by a full battery, so its deadline is the full bound; a delta one keeps the delta.
  if [ "$scope" = full ]; then bound=$(gate_full_bound); else bound=$(gate_bound); fi
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
# The load (#1691): fields fifteen and sixteen of a history line are the one-minute load average when the command started and
# when the line is written, so a red can be read beside the load it was measured under (gate_load_set.py). They label a set
# and decide nothing: no load bound is in this gate (ADR-0012, ADR-0018). GATE_LOAD_READ names a script that prints the
# number, for a suite that cannot set the machine's load; a reading that is no number is a dash.
gate_load() {
  local l
  if [ -n "${GATE_LOAD_READ:-}" ]; then l=$(bash "$GATE_LOAD_READ" 2>/dev/null)
  else l=$(LC_ALL=C uptime 2>/dev/null | sed -n 's/.*load average[s]*: *\([0-9.]*\).*/\1/p'); fi
  case "$l" in ''|*[!0-9.]*) l=-;; esac
  printf '%s' "$l"
}
gate_mode() {  # <selection> <scope> <reused>: the entry mode this row carries (#1798). A reused verdict is `reused` (#1856);
  # else a caller that knows the word names it, and a gate that names none ran the battery when it wrote a selection or a
  # scope, and ran none when it wrote neither.
  [ "${3:-}" = yes ] && { printf reused; return; }
  case "${SG_GATE_ENTRY_MODE:-}" in gated|reused|skipped) printf '%s' "$SG_GATE_ENTRY_MODE"; return;; esac
  if [ -n "$1" ] || [ -n "$2" ]; then printf gated; else printf skipped; fi
}
# The CPU the gate's command spent (#1856): the user and sys time of this shell's children, in whole seconds. `times` is a
# builtin, so it reads this shell's own children; it is written through a file because a command substitution would fork
# and the forked shell would report no children of its own.
gate_cpu_read() {  # sets gate_cpu_s: `-` when no reading is possible
  local f c
  gate_cpu_s=-
  f=$(mktemp "${TMPDIR:-/tmp}/gate-cpu.XXXXXX" 2>/dev/null) || return 0
  times > "$f" 2>/dev/null
  c=$(awk 'NR == 2 { for (i = 1; i <= 2; i++) { t = $i; sub(/s$/, "", t); split(t, a, "m"); c += a[1] * 60 + a[2] } } END { printf "%d\n", int(c + 0.5) }' "$f" 2>/dev/null)
  rm -f "$f"
  case "$c" in ''|*[!0-9]*) ;; *) gate_cpu_s=$c;; esac
}
gate_history() {  # <kind> <result> <sha> <start epoch> <selection> <scope> <outcome> <reason> <ran> <not run> <slots> <red> [<reused>] [<runner>] [<run id>]
  local tree wall runner=${14:-${GATE_RUNNER:-local}} runid=${15:-${GATE_RUNNER_ID:--}}
  case "$runner" in ci|local) ;; *) runner=local;; esac   # the two words the proof has; anything else is a local gate
  # A batch landing gate (#2058) names its batch in the run id field: `batch:<size>:<run>,<run>...`. The field count stays.
  [ -n "${SG_GATE_BATCH:-}" ] && [ "$runid" = - ] && runid="batch:$SG_GATE_BATCH"
  fin=${gate_fin:-$(date +%s)}   # the finish epoch the line carries, and the epoch the obligation's deadline counts from (note)
  tree=$(git rev-parse -q --verify 'HEAD^{tree}' 2>/dev/null); [ -n "$tree" ] || tree=-
  wall=$(( fin - ${4:-$fin} )); [ "$wall" -ge 0 ] || wall=0
  [ -z "${gate_wall:-}" ] || wall=$gate_wall   # a cancelled gate spends no wall, whatever second its two clocks fell in
  mkdir -p "$st" 2>/dev/null || return 0
  printf -v line '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' "$fin" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}" "${11}" "${12:--}" "${SG_GATE_CALLER:-other}" "${gate_load0:--}" "$(gate_load)" "$(gate_mode "$5" "$6" "${13:-no}")" "${tree:--}" "${gate_wait:--}" "$wall" "${gate_cpu_s:--}" "${13:-no}" "$runner" "${runid:--}" "${gate_slot_ask:--}" "${gate_slot_got:--}"
  append_line "$st/gate-history" "$line"
}
# A gate a green verdict already answers writes one line and runs nothing (#1856, #1798, ADR-0168): the same shape as a
# measured gate, with no selection and no scope (it ran no battery), the pass the memo gave, mode `reused`, reused `yes`, and
# wait, wall and cpu of 0. Its start is its finish, so a reader that folds gate time counts it in neither (hub.py,
# throughput.py, tune_report.py), and the reuse is never a silent skip (ADR-0168 rule 5). gate.sh stays the file's one writer.
gate_reuse() {  # <kind> <sha>
  gate_fin=$(date +%s) gate_wait=0 gate_wall=0 gate_cpu_s=0 gate_load0=$(gate_load)
  gate_history "$1" pass "$2" "$gate_fin" "" "" pass - - - 0 - yes
}
# An entry that ran no battery at all, because the wave entry gate is off (#1800), writes one line too, mode `skipped`: no
# lock, no slots, no battery. Its result is a pass because nothing measured a red; the wave's landing gate is what proves the
# child. gate.sh stays the file's one writer.
gate_skip() {  # <kind> <sha>
  SG_GATE_ENTRY_MODE=skipped
  gate_fin=$(date +%s) gate_wait=0 gate_wall=0 gate_cpu_s=0 gate_load0=$(gate_load)
  gate_history "$1" pass "$2" "$gate_fin" "" "" pass - - - 0 - no
}
case "${1:-}" in
  --status) [ $# = 4 ] || { echo "usage: gate.sh --status <kind> <pass|fail> <sha>" >&2; exit 2; }; status "$2" "$3" "$4"; exit 0;;
  --reuse)  # a cancelled gate whose verdict a green memo answers: one history line, no lock, no slots, no battery (ADR-0168)
    [ $# = 3 ] || { echo "usage: gate.sh --reuse <kind> <sha>" >&2; exit 2; }; gate_reuse "$2" "$3"; exit 0;;
  --history)  # <kind> <result> <sha> <start> <selection> <scope> <outcome> <runner> <run id>: one history line, with no lock and
              # no slots. A proof that must never take the gate lock records its gate with it, through the one writer (#1927).
    [ $# = 10 ] || { echo "usage: gate.sh --history <kind> <result> <sha> <start> <selection> <scope> <outcome> <runner> <run id>" >&2; exit 2; }
    gate_history "$2" "$3" "$4" "$5" "$6" "$7" "$8" - - - - - no "$9" "${10}"; exit 0;;
  --skip)   # an entry that ran no battery at all (#1800): one history line, mode skipped, no lock, no slots, no battery
    [ $# = 3 ] || { echo "usage: gate.sh --skip <kind> <sha>" >&2; exit 2; }; gate_skip "$2" "$3"; exit 0;;
  --inner)  # inside the lock: take the slots, then run the gate. The take is one try, never a wait: a gate that cannot take its
            # slots exits 73 at once with none, and serial.sh releases the lock on that exit, so it holds neither resource while
            # it waits for the other. Every later exit path runs the EXIT trap and gives the slots back.
            # (#1872) A full-battery gate is the exception: it waits for its slots while it holds the lock, bounded by
            # SG_GATE_SLOT_WAIT_SECONDS, so a run of one-slot gates cannot starve it. The wait is gate wait, never run time
            # (ADR-0166), because the clock starts in --run once the slots are held.
    shift
    # caps.sh is sourced once, at the top, and this branch and its `--run` child read that one source (#1510): a second
    # source here ran the interpreter lookup again for a reader this shell already has.
    # The lease names the lock holder, this process's parent: a waiter that reaps the lock holder past its deadline then also
    # frees the lease, so a stopped run never leaves a child's gate holding slots (ADR-0051).
    # (#2009, ADR-0174) A tip check or a landing gate waits for its slots too. The gates that hold them end inside one gate
    # bound, and no new gate takes the lock this gate holds, so the wait is one bound plus a margin, never a single try.
    # (#2049) While it waits, its take names the caller as a priority: a waiting marker names this gate as the next holder,
    # and no child gate takes a freed slot until this gate takes its slots or gives up.
    # (#2330) The wait is one bounded take, `caps take --wait`, never a loop of tries. A take whose bound runs out exits 75 and
    # writes the waited-out line, so the landing reads a wait, never a skip (73) that measured nothing; caps.py clears the
    # marker of a priority waiter that gives up. Every other gate takes one try.
    prio=() take=(--try)
    case ${SG_GATE_CALLER:-} in tip|landing)
      prio=(--priority "$SG_GATE_CALLER")
      : "${SG_GATE_SLOT_WAIT:=1}"; : "${SG_GATE_SLOT_WAIT_SECONDS:=$(gate_slot_wait)}";; esac
    [ "${SG_GATE_SLOT_WAIT:-0}" = 1 ] && take=(--wait "${SG_GATE_SLOT_WAIT_SECONDS:-120}")
    # (#2317) The two clocks the record needs to split the gate stint: when it asked for its slots inside the lock, and
    # when it held them. The run clock starts after this, so its seconds are the run, never the wait (ADR-0166).
    slot_ask=$(date +%s)
    # (#2604) The slot count is the admission and the worker count is the pool: SG_GATE_SLOT_COUNT names the slots this gate
    # takes, and SG_GATE_WORKERS stays the count its command reads. A wave tip check takes one slot by default (wave.sh) and
    # keeps its pytest pool; at the whole count, a long gate left the wave no slot inside its wait, and the check measured nothing.
    slot_count=${SG_GATE_SLOT_COUNT:-}; named=1
    case $slot_count in ''|*[!0-9]*|0) slot_count=${SG_GATE_WORKERS:-6}; named=0;; esac
    SQUAD_GATE_WORKERS=$(sg_caps take gates "gate-$$" --count "$slot_count" --pid "${PPID:-$$}" --target "$PWD" "${take[@]}" "${prio[@]}"); slot_rc=$?
    [ "$slot_rc" = 0 ] || { [ ${#prio[@]} = 0 ] || sg_caps release gates "gate-$$" >/dev/null 2>&1; exit "$slot_rc"; }
    [ "$named" = 0 ] || SQUAD_GATE_WORKERS=${SG_GATE_WORKERS:-$SQUAD_GATE_WORKERS}
    export SQUAD_GATE_WORKERS SG_GATE_SLOT_ASK="$slot_ask" SG_GATE_SLOT_GOT="$(date +%s)"
    trap 'exit 143' TERM; trap 'exit 130' INT   # a stop past the hold deadline still runs the EXIT trap below
    trap 'sg_caps release gates "gate-$$"' EXIT
    bash "$here/gate.sh" --run "$@"; exit $?;;
  --run)    # inside the lock: run the command, then write the line. One clock starts when the lock is taken (ADR-0163, #1171).
    shift
    outread=${GATE_OUTCOME_READ_FILE:-}; unset GATE_OUTCOME_READ_FILE   # the caller's copy: the command never sees it
    export GATE_SELECT_FILE GATE_SCOPE_FILE GATE_OUTCOME_FILE GATE_BASE_FILE GATE_REUSE_FILE
    GATE_SELECT_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-select.XXXXXX" 2>/dev/null) || GATE_SELECT_FILE=
    GATE_SCOPE_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-scope.XXXXXX" 2>/dev/null) || GATE_SCOPE_FILE=
    GATE_OUTCOME_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-outcome.XXXXXX" 2>/dev/null) || GATE_OUTCOME_FILE=
    GATE_BASE_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-base.XXXXXX" 2>/dev/null) || GATE_BASE_FILE=
    GATE_REUSE_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-reuse.XXXXXX" 2>/dev/null) || GATE_REUSE_FILE=
    start=$(date +%s); gate_load0=$(gate_load)
    # (#1856) The wait is everything before this clock: the lock wait and the slot wait, measured from the gate's own start
    # (SG_GATE_T0, set by this invocation before it queued), never a constant and never folded into the run time (ADR-0166).
    gate_wait=$(( start - ${SG_GATE_T0:-$start} )); [ "$gate_wait" -ge 0 ] || gate_wait=0
    # (#2317) The instant it asked for its slots and the instant it held them, from the holder above; a gate that took no
    # slots of its own, a reused verdict or a skipped entry, carries a dash in both fields, because it holds no record.
    gate_slot_ask=${SG_GATE_SLOT_ASK:--} gate_slot_got=${SG_GATE_SLOT_GOT:--}
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
    # (#2379) The full clock beside the delta one: the command runs to it only once it named the full scope.
    fbound=$(gate_full_bound); fdeadline=$(( start + fbound ))
    export GATE_START_EPOCH=$start GATE_DEADLINE_EPOCH=$deadline GATE_BOUND_SECONDS=$bound
    export GATE_FULL_BOUND_SECONDS=$fbound GATE_FULL_DEADLINE_EPOCH=$fdeadline
    # The watchdog's latch: it exists once the command named the full scope, and the record then names that scope.
    latchf=$(mktemp "${TMPDIR:-/tmp}/gate-latch.XXXXXX" 2>/dev/null) || latchf=; [ -z "$latchf" ] || rm -f "$latchf"
    note() {  # <result> <outcome if the command wrote none> <reason if none>: one record, the red's precedence folded in
      note_rc=
      if [ "${SG_GATE_STATUS:-}" != self ]; then
        sha=$(git rev-parse HEAD 2>/dev/null); sel=$(cat "$GATE_SELECT_FILE" 2>/dev/null); scope=$(cat "$GATE_SCOPE_FILE" 2>/dev/null)
        [ -z "$latchf" ] || [ ! -e "$latchf" ] || scope=full   # (#2379) the scope the watchdog latched, whatever the file says now
        oc= rs= ran= notran=
        [ -z "$GATE_OUTCOME_FILE" ] || IFS=$'\t' read -r oc rs ran notran < "$GATE_OUTCOME_FILE" 2>/dev/null
        [ -n "$oc" ] || { oc=$2; rs=$3; ran=-; notran=-; }
        fold_red "$(repo_name)" "$sha" "$scope" "$1" "$oc" "$rs" "${ran:--}" "${notran:--}"
        # The caller's copy of the word this gate wrote, written inside the lock so no other gate can answer for it (#1174).
        # It carries the folded outcome: a red that stands answers for the tree whatever the attempt reported.
        [ -z "$outread" ] || { printf '%s\t%s\t%s\t%s\n' "$FOC" "$FRS" "$FRAN" "$FNOT" > "$outread.$$" 2>/dev/null \
          && mv -f "$outread.$$" "$outread" 2>/dev/null; }
        status "${SG_GATE_KIND:-gate}" "$FRES" "$sha" "$sel" "$scope"
        # (#1856, ADR-0168) A command that answers from a stored green verdict marks the line: the gate ran no battery,
        # so its row says the verdict was reused and the reader counts the saved gate. The marker is its content, never its
        # existence: the temp file exists from the moment it is made.
        gate_reused=no; [ "$(cat "${GATE_REUSE_FILE:-/nonexistent}" 2>/dev/null)" = yes ] && gate_reused=yes
        gate_history "${SG_GATE_KIND:-gate}" "$FRES" "$sha" "$start" "$sel" "$scope" "$FOC" "$FRS" "${FRAN:--}" "${FNOT:--}" "${SQUAD_GATE_WORKERS:-0}" "$FRED" "$gate_reused"
        # The gate-attempt journal (#1255): one attempt record per verdict this invocation drew, for the card
        # the gate names (SG_GATE_CARD) and the tip it ran at. A deadline stop reaches here too: its verdict is
        # unproven, and an unproven attempt counts like any other. The record's identity is (card, tip, aid), and
        # the aid is this invocation's start marker, so a crash between the append and the field write, then a
        # replay under a new process, answers as the record itself and never appends twice. SG_GATE_AID names a
        # chain of gates that is one attempt (a landing's retry chain): the first record answers for the chain.
        gaok=1
        if [ -n "${SG_GATE_CARD:-}" ] || [ -n "${SG_GATE_WAVE:-}" ]; then
          gaj=${SG_GATE_JOURNAL:-}
          if [ -z "$gaj" ] && [ -n "${SG_BOARD_CACHE:-}" ]; then
            gaj=$(python3 "$here/gate_attempts.py" journal "$SG_BOARD_CACHE" "${SG_REPO:-$(repo_name)}" 2>/dev/null) || gaj=
          fi
          if [ -n "$gaj" ]; then
            # A failed append is named on stderr, never swallowed: the gate's verdict stands, and the journal
            # step is loud so a gate that journaled nothing is visible on the record it printed.
            if ! garec=$(python3 "$here/gate_attempts.py" record "$gaj" "${SG_GATE_CARD:-0}" "$sha" "$FOC" "${SG_GATE_AID:-}" "$fin" "${SG_GATE_WAVE:-}" 2>&1); then
              echo "gate: WARNING: the attempt of card ${SG_GATE_CARD:-0} at ${sha:0:9} was not journaled: $garec" >&2; gaok=
            fi
            # The journal is the source and the card's Gate Tries is its projection (#1255, ADR-0170): the count
            # is replayed from the whole journal, so a crash between the append and the field write answers one
            # count on the next projection. A projection that fails is named, never dropped silently.
            if [ -z "${SG_GATE_CARD:-}" ]; then :
            elif ! gat=$(python3 "$here/gate_attempts.py" count "$gaj" "$SG_GATE_CARD" 2>&1); then
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
      [ -z "$GATE_REUSE_FILE" ] || rm -f "$GATE_REUSE_FILE"
      [ -z "$latchf" ] || rm -f "$latchf"
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
    orphfile=$(mktemp "${TMPDIR:-/tmp}/gate-orphan.XXXXXX" 2>/dev/null) || orphfile=; [ -z "$orphfile" ] || rm -f "$orphfile"
    wd=
    # reap_wd <pid>: the watchdog's own group when it leads one, its children and its pid when it does not, so the sleep it
    # forked is never reparented and left to run out its bound (#1441).
    reap_wd() { local p; [ "$($SG_PS -o pgid= -p "$1" 2>/dev/null | tr -d ' ')" = "$1" ] && kill -- -"$1" 2>/dev/null
      for p in $(pgrep -P "$1" 2>/dev/null); do kill "$p" 2>/dev/null; done; kill "$1" 2>/dev/null; wait "$1" 2>/dev/null; }
    trap '[ -z "$wd" ] || reap_wd "$wd"; note fail unproven "bounded: the gate was stopped at its deadline and no check reported a defect"; [ "$FOC" = fail ] && exit 1; exit "${note_rc:-143}"' TERM
    "$@" & cpid=$!
    # The command is the one that knows how to stop its own tree and write what it found; the wrapper's own stop waits a
    # short grace past the deadline, so a command that answers at the deadline is never killed mid-record. A command that
    # does not answer is stopped here, with every descendant (ADR-0163, #1171). One watchdog answers both, because both are
    # a reason to stop the command's tree: a look every two seconds finds the deadline five seconds past its clock, and it
    # finds a caller that died (#1576), which is a gate nobody waits for. A sleep cut short is no deadline: the bound is read
    # from the clock at every look (#392).
    set -m   # the watchdog leads its own process group, so reap_wd reaches the sleep it forks
    # (#2379) The full scope moves the stop to the full clock: the watchdog reads GATE_SCOPE_FILE at every look and latches on
    # `full`, so a later rewrite of the file never shortens the clock; until it reads `full`, the delta clock holds.
    stopat=$(( start + bound + 5 )); fstopat=$(( start + fbound + 5 ))
    ( full=0
      while :; do
        sleep 2
        kill -0 $$ 2>/dev/null || exit 0
        if gate_orphaned; then [ -z "$orphfile" ] || : > "$orphfile"; break; fi
        if [ "$full" = 0 ] && [ -n "$GATE_SCOPE_FILE" ] && [ "$(cat "$GATE_SCOPE_FILE" 2>/dev/null)" = full ]; then
          full=1; [ -z "$latchf" ] || : > "$latchf"; fi
        now=$(date +%s)
        [ "$now" -lt "$stopat" ] && continue
        [ "$full" = 1 ] && [ "$now" -lt "$fstopat" ] && continue
        [ -z "$timedout" ] || : > "$timedout"; break
      done
      stop_tree "$cpid" ) >/dev/null 2>&1 & wd=$!
    set +m
    wait "$cpid" 2>/dev/null; rc=$?
    reap_wd "$wd"
    gate_cpu_read   # (#1856) read before any other child runs, so the line names this command's own CPU
    # An orphan answers for no caller: it writes no status, no history line and no obligation, because nobody asked this tree
    # for a word, and a stale status would read as this gate's. It gives back what it holds -- the slots here, the lock when
    # serial.sh's holder exits -- and names the orphan on one line of its log.
    if [ -n "$orphfile" ] && [ -e "$orphfile" ]; then
      rm -f "$orphfile"; [ -z "$timedout" ] || rm -f "$timedout"
      [ -z "$GATE_SELECT_FILE" ] || rm -f "$GATE_SELECT_FILE"
      [ -z "$GATE_SCOPE_FILE" ] || rm -f "$GATE_SCOPE_FILE"
      [ -z "$GATE_OUTCOME_FILE" ] || rm -f "$GATE_OUTCOME_FILE"
      [ -z "$GATE_BASE_FILE" ] || rm -f "$GATE_BASE_FILE"
      [ -z "$GATE_REUSE_FILE" ] || rm -f "$GATE_REUSE_FILE"
      [ -z "$latchf" ] || rm -f "$latchf"
      echo "gate: orphan: the process that asked for this gate (pid ${SG_GATE_PARENT:-?}) is gone: the gate stops and releases its slots" >&2
      exit 143
    fi
    if [ -n "$timedout" ] && [ -e "$timedout" ]; then
      rm -f "$timedout"
      [ -z "$latchf" ] || [ ! -e "$latchf" ] || bound=$fbound   # the line names the clock that ended the command
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
# The process that asked for this gate (#1576) is read here, before the exec below, because serial.sh forks --inner and --run
# and their own parent is the lock holder, which lives as long as the gate does. An outermost gate sets it; a gate inside a gate
# inherits the one caller of the chain.
[ -n "${SG_GATE_PARENT:-}" ] || export SG_GATE_PARENT=$PPID
# (#1856) The instant this gate began, before it queues for the lock: the wait of its history line is measured from here, so a
# gate set afresh never inherits the clock of a gate that called it. --inner and --run sit below the lock, so the value they
# read is this one.
SG_GATE_T0=$(date +%s); export SG_GATE_T0
# (#2317) A gate inside a gate inherits the outer gate's environment, so the slot clocks are cleared here at every gate's
# own start: only the --inner that takes this gate's slots sets them, and a gate that takes none writes dashes.
unset SG_GATE_SLOT_ASK SG_GATE_SLOT_GOT
wave_wait=${SERIAL_WAIT_SECONDS:-1800}
# (#2379) The hold outlives the longer gate: the full bound, read once here for the whole gate (an outermost gate never takes
# an inherited value; a gate inside a gate keeps its holder's), plus the slot wait of a caller that waits for its slots inside
# the lock (tip, landing, SG_GATE_SLOT_WAIT=1), plus the backstop.
case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;; *) unset GATE_FULL_BOUND_SECONDS;; esac
GATE_FULL_BOUND_SECONDS=$(gate_full_bound); export GATE_FULL_BOUND_SECONDS
hold_wait=0
case ${SG_GATE_CALLER:-} in tip|landing) hold_wait=${SG_GATE_SLOT_WAIT_SECONDS:-$(gate_slot_wait)};;
  *) [ "${SG_GATE_SLOT_WAIT:-0}" = 1 ] && hold_wait=${SG_GATE_SLOT_WAIT_SECONDS:-120};; esac
case $hold_wait in ''|*[!0-9]*) hold_wait=0;; esac
export SERIAL_WAIT_SECONDS=${SERIAL_WAIT_SECONDS:-86400} SERIAL_HOLD_SECONDS=${SERIAL_HOLD_SECONDS:-$(( GATE_FULL_BOUND_SECONDS + hold_wait + 300 ))}
case ":${SG_SERIAL_HELD:-}:" in
  *":gate:"*) exec bash "$here/gate.sh" --run "$@" ;;   # inside a gate holder: no slots of its own, it keeps the holder's count
esac
# A wave's gate is live from the request, queued or running (#1257): the marker names this process, which exec keeps as the
# lock waiter and then the holder, so a dead pid is a gate that is gone. Its limit is the attempt bound plus the lock wait.
if [ -n "${SG_GATE_WAVE:-}" ]; then
  mkdir -p "$st" 2>/dev/null && printf '%s %s %s\n' "$$" "$(date +%s)" "$(( GATE_FULL_BOUND_SECONDS + wave_wait ))" > "$st/gate-wave-$SG_GATE_WAVE.$$.live.$$" 2>/dev/null \
    && mv -f "$st/gate-wave-$SG_GATE_WAVE.$$.live.$$" "$st/gate-wave-$SG_GATE_WAVE.$$.live" 2>/dev/null
fi
exec bash "$here/serial.sh" gate bash "$here/gate.sh" --inner "$@"   # exec: the lock holder is this process, and --inner takes the slots inside it
