#!/bin/bash
# usage: nightly.sh   The full suite in both modes, heavy included, once a day (#704), in its own tree and its own lock.
# It runs `-n auto` and then `-p no:xdist` with `-p no:deepeval` over the whole suite -- no `-m 'not heavy'`, so the calls the
# fast battery deselects run here -- in a plain clone at $SG_STATE/nightly-tree, never in the owner's checkout and never in a
# child's worktree, so no suite it runs can touch the line the loop is working on.
# Its lock is the serial name `nightly` (serial.sh), in try mode: a second start never queues behind a suite of hours, and it
# never takes the `gate` lock, which the landing gate, the wave entry and the promotion all wait on. A held landing lock is no
# obstacle here, and a nightly in flight never holds a landing up.
# It runs at most once a day: a completed run leaves $SG_STATE/auto/nightly-<date>, and a later start the same day exits at
# once. The marker is written after the run, so a suite that dies leaves no marker and the next cycle tries again.
# One result record, written atomically (ADR-0030), is what the hub reads:
#   $SG_STATE/nightly-result: `<epoch> TAB <pass|fail|bounded> TAB <sha> TAB <parallel> TAB <serial> TAB <ids>`
# The result is a pass only when both modes pass. A mode is `pass` when it exited clean, `fail` when its log names a
# failing test (and `fail:signal-<n>` when a signal also stopped that red leg, so the stop reason is never lost), and
# `bounded:signal-<n>` or `bounded:exit-<n>` when it ended non-zero and named none: a leg killed by a signal measured
# nothing, so it is a bounded attempt, never a red the suite found (ADR-0164). The result is `fail` when a mode is `fail`,
# and `bounded` when a mode is bounded and none is red, so the record keeps which mode finished apart from which mode was
# stopped. `<ids>` is ONE JSON array of the complete failing test ids, `[]` when none; a mode that failed names at least
# the tests it failed, and the hub draws them. An id is never split at a space, so a parametrized case id such as
# `test_x[case with space]` reaches the hub whole. A red record holds dispatch (ghcall.sh, gh_dispatch_hold) until one green
# run replaces it; a bounded record is unproven, so it holds nothing.
# A mode finished when pytest wrote its summary line (`<n> passed ... in 12.34s`). A mode that ended with any other last
# line was killed before the summary: it is bounded, never a red with no test named (#1201). Such a mode is run once more,
# and the first log stays as `<log>.first`. A start the loop kills itself leaves the record too, naming the signal.
# A red or a bounded result raises one alert on the path health.sh already uses: one line in $SG_STATE/alerts.log and one
# notification (HEALTH_NOTIFY overrides the notifier). The alert names the mode, the exit status, the signal and the log
# line that names the cause, never "no failing test named". The two mode logs stay in $SG_STATE/nightly-parallel.log and
# nightly-serial.log.
# It also settles the proof obligations a gate left unproven (spec 1163): it runs the checks each record in
# $SG_STATE/obligations names over the tree that record names, writes settled-green or settled-red in
# $SG_STATE/obligation-settled, clears the record it answered, reports an overdue one by name, and a settled red holds the
# tree it measured like any red (ghcall.sh). The settlement also answers the card fact an unproven landing left (#1176):
# wave.sh maps the issue to the obligation key under $SG_STATE/proof-card/<issue>/, and a green settlement clears the fact
# while a red one holds the card. The result record, the red alert and the bound marker are written first, and
# the obligations are settled after: a settlement that hangs or is stopped never holds back the record the hub and the
# dispatch hold read. The settlement stops before the run's own serial hold and leaves every other record in place, each
# reported by name. A settled red older than one nightly period, or at a tree that is no longer the tip, is pruned and
# reported, so old reds never collect.
# It starts the live check last (spec 1163, #1178): the queue's invariants read from the live state, over one hour, in its
# own lock and with its own record. It starts the check detached: this body exits at once, serial.sh frees the `nightly`
# lock, and the check samples alone. The check never changes this run's result or its record, and it holds no dispatch
# (ADR-0163, ADR-0164): an hour of `locks/nightly` would refuse every promotion tick, so the check never keeps that lock.
# It holds while a run is live or the gate lock is held (#1527, ADR-0153): a full suite beside the run cap bounded every child's
# gate at 900 s. A held start writes no day marker, so the next idle cycle runs the suite.
here="$(cd "$(dirname "$0")" && pwd)"; st=${SG_STATE:-/tmp/sg-state}
mkdir -p "$st/auto" "$st/locks"
day=$st/auto/nightly-$(date +%F)
[ -n "${SG_NIGHTLY_OFF:-}" ] && exit 0   # a profile that sets it keeps no nightly: a project may carry its own suite instead (ADR-0153)
# (#1975, ADR-0153) One verdict. The nightly has two halves: the GitHub half (.github/workflows/nightly.yml) runs every test a
# Linux runner runs, and this script runs the rest, the local half. The GitHub half names the dev sha it tested in the name of
# one artifact, `nightly-dev-sha-<sha>`. The local half runs on the sha today's scheduled run tested, and on the dev tip when
# no such run exists yet. The verdict combines the local record with the scheduled run that tested the local record's sha,
# found by that artifact's name and read through the gh door (ghcall.sh), never by a bare gh call and never as the latest
# run. A run of an earlier UTC day than the local record never answers it. Fail in either half is fail. Pass in both halves
# on the same sha is pass. Any other pair is unproven: a half that is missing, unfinished, bounded, or at another sha.
#   $SG_STATE/nightly-verdict: `<epoch> TAB <pass|fail|unproven> TAB <sha> TAB <local> TAB <github> TAB <github sha>`
# The verdict is written after each local run, and a later call reads it again while it is unproven, at most once per
# SG_NIGHTLY_VERDICT_EVERY seconds (600 by default) and for one day after the local record, so a GitHub half that ends after
# the local half still settles it. `--verdict` writes it once and exits.
alert() {  # <message>
  local msg=$1 net
  printf '%s %s\n' "$(date '+%F %T')" "$msg" >> "$st/alerts.log"
  net=${HEALTH_NOTIFY:-/usr/bin/osascript}
  "$net" -e "display notification \"${msg//\"/\'}\" with title \"squad-goal\"" >/dev/null 2>&1
}
# The GitHub half's results the nightly has read, one `<sha> TAB <pass|fail>` line each: a pytest obligation at that sha is
# answered by it, so the local half never runs the full suite for it (ADR-0153 rule 1d).
gh_seen() { awk -F'\t' -v s="$1" '$1 == s { r = $2 } END { if (r != "") print r }' "$st/nightly-github-seen" 2>/dev/null; }
# utc_day <epoch>: the UTC day of a time, the day a scheduled run's `created_at` names.
utc_day() { date -u -r "$1" +%F 2>/dev/null || date -u -d "@$1" +%F 2>/dev/null; }
# gh_run_for <sha>: `<id> TAB <status> TAB <conclusion> TAB <created_at>` of the scheduled run whose artifact names the sha it
# tested; nothing when no run tested it or GitHub gives no answer.
gh_run_for() {
  local id
  id=$(. "$here/ghcall.sh" && gh_api "repos/$SG_REPO/actions/artifacts?name=nightly-dev-sha-$1&per_page=1" \
    -q '.artifacts[0].workflow_run.id // empty' 2>/dev/null) || return 0
  case "$id" in ''|*[!0-9]*) return 0;; esac
  (. "$here/ghcall.sh" && gh_api "repos/$SG_REPO/actions/runs/$id" \
    -q 'select(.event == "schedule") | "\(.id)\t\(.status)\t\(.conclusion)\t\(.created_at)"' 2>/dev/null) || true
}
# gh_today_sha: the dev sha today's scheduled run tested, by its artifact; nothing when no run of today names one.
gh_today_sha() {
  local id
  [ -n "${SG_REPO:-}" ] || return 0
  id=$(. "$here/ghcall.sh" && gh_api "repos/$SG_REPO/actions/workflows/nightly.yml/runs?event=schedule&created=%3E%3D$(date -u +%F)&per_page=1" \
    -q '.workflow_runs[0] // empty | select(.created_at[0:10] == "'"$(date -u +%F)"'") | .id' 2>/dev/null) || return 0
  case "$id" in ''|*[!0-9]*) return 0;; esac
  (. "$here/ghcall.sh" && gh_api "repos/$SG_REPO/actions/runs/$id/artifacts" \
    -q '.artifacts[].name | select(startswith("nightly-dev-sha-")) | ltrimstr("nightly-dev-sha-")' 2>/dev/null | head -n 1) || true
}
verdict_write() {
  local lres=missing lsha= lts= gres=missing gsha=- row gst gco gday res sha
  if [ -r "$st/nightly-result" ]; then IFS=$'\t' read -r lts lres lsha _ < "$st/nightly-result"; fi
  case "$lres" in pass|fail|bounded) ;; *) lres=missing; lsha=;; esac
  if [ "$lres" != fail ] && [ -n "${SG_REPO:-}" ] && [ -n "$lsha" ]; then   # a local red is the verdict whatever GitHub says
    row=$(gh_run_for "$lsha")
    if [ -n "$row" ]; then
      IFS=$'\t' read -r _ gst gco gday <<<"$row"
      if [ "${gday:0:10}" = "$(utc_day "$lts")" ]; then
        gsha=$lsha
        case "$gst/$gco" in completed/success) gres=pass;; completed/failure) gres=fail;; completed/*) gres=${gco:-unknown};; *) gres=unfinished;; esac
      fi
    fi
  fi
  if [ "$lres" = fail ] || [ "$gres" = fail ]; then res=fail
  elif [ "$lres" = pass ] && [ "$gres" = pass ] && [ -n "$lsha" ] && [ "$lsha" = "$gsha" ]; then res=pass
  else res=unproven; fi
  sha=${lsha:-$gsha}
  if { [ "$gres" = pass ] || [ "$gres" = fail ]; } && [ "$gsha" != - ] && [ "$(gh_seen "$gsha")" != "$gres" ]; then
    { tail -n 59 "$st/nightly-github-seen" 2>/dev/null; printf '%s\t%s\n' "$gsha" "$gres"; } > "$st/nightly-github-seen.$$" \
      && mv -f "$st/nightly-github-seen.$$" "$st/nightly-github-seen"
  fi
  # A GitHub-half red alerts once per sha, through the path a local red takes (rule 6). A local red has its own alert.
  if [ "$gres" = fail ] && [ "$lres" != fail ] && [ "$(cat "$st/nightly-gh-red-alerted" 2>/dev/null)" != "$gsha" ]; then
    alert "NIGHTLY GitHub half red at ${gsha:0:7}: the scheduled Linux suite failed"
    printf '%s\n' "$gsha" > "$st/nightly-gh-red-alerted"
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date +%s)" "$res" "${sha:--}" "$lres" "$gres" "$gsha" > "$st/nightly-verdict.$$" \
    && mv -f "$st/nightly-verdict.$$" "$st/nightly-verdict"
}
[ "${1:-}" = --verdict ] && { verdict_write; exit $?; }
if [ "${1:-}" != --run ] && [ -r "$st/nightly-result" ]; then
  IFS=$'\t' read -r lts _ < "$st/nightly-result"; vts=0; vres=
  [ -r "$st/nightly-verdict" ] && IFS=$'\t' read -r vts vres _ < "$st/nightly-verdict"
  case "$lts" in ''|*[!0-9]*) lts=0;; esac; case "$vts" in ''|*[!0-9]*) vts=0;; esac
  vnow=$(date +%s)
  if [ $((vnow - lts)) -lt 86400 ] && { [ "$vts" -lt "$lts" ] \
       || { [ "$vres" != pass ] && [ "$vres" != fail ] && [ $((vnow - vts)) -ge "${SG_NIGHTLY_VERDICT_EVERY:-600}" ]; }; }; then
    verdict_write
  fi
fi
# (#1975, ADR-0153) The suite starts only in the profile's hour, America/Sao_Paulo (SG_NIGHTLY_HOUR, 04 by default). The dispatch
# stops in that hour (ghcall.sh), so the live runs end and the suite gets the machine. When the hour has closed with no run that
# day, the first cycle after it records one bounded attempt; no later cycle repeats it. A 23:00 hour closes at midnight, so
# its record is written on the next day, under the day the hour opened.
nhour=$((10#${SG_NIGHTLY_HOUR:-04})); hnow=$((10#$(TZ=America/Sao_Paulo date +%H)))
if [ "$hnow" -ne "$nhour" ]; then
  wday=
  if [ "$hnow" -gt "$nhour" ]; then wday=$(date +%F)
  elif [ "$nhour" -eq 23 ]; then wday=$(date -v-1d +%F 2>/dev/null || date -d yesterday +%F 2>/dev/null); fi
  if [ -n "$wday" ] && [ ! -e "$st/auto/nightly-$wday" ] && [ ! -e "$st/auto/nightly-missed-$wday" ]; then
    mk=$(/usr/bin/git -C "${SG_NIGHTLY_TREE:-$st/nightly-tree}" rev-parse -q --verify "origin/${SG_BASE:-dev}" 2>/dev/null) || mk=
    printf '%s\tbounded\t%s\tbounded:window\tbounded:window\t[]\n' "$(date +%s)" "${mk:--}" > "$st/nightly-result.$$" \
      && mv -f "$st/nightly-result.$$" "$st/nightly-result"
    printf '%s NIGHTLY window closed at %s with no run: one bounded attempt\n' "$(date '+%F %T')" "${mk:0:7}" >> "$st/alerts.log"
    touch "$st/auto/nightly-missed-$wday"
  fi
  echo "nightly: held: outside the $(printf '%02d' "$nhour"):00 window (America/Sao_Paulo); the suite runs in that window only (#1975)"; exit 0
fi
# (#2527) A suite that cannot finish in the time left only ends bounded and costs the machine the dispatch got back
# (ghcall.sh releases it at the same point), so it never starts then; the hour closes with one missed record.
nmin=$(TZ=America/Sao_Paulo date +%M:%S); nmin=$((10#${nmin%%:*})); nneed=$((10#${SG_NIGHTLY_NEED_MINUTES:-45}))
[ $((60 - nmin)) -lt "$nneed" ] && [ ! -e "$day" ] && {
  echo "nightly: held: $((60 - nmin)) min left in the window, under the $nneed min the suite needs (#2527)"; exit 0; }
[ -e "$st/promote-reserve" ] && exit 0   # a proof is measuring in its own tree: two full suites saturate the box before either shows in the load average, and no day marker is written, so the nightly runs once the proof is over
[ -e "$day" ] && exit 0   # it already ran today: no second suite, whatever started this one
# (#1527, ADR-0153) A full suite beside live runs and their gates bounds every child's gate at 900 s, so the nightly yields to them.
# A live run is the one reader's rule (squad/run_liveness.py, the drain's and the hub's reader): a run that ended, or died with no
# verdict, never holds it. The reader runs from the first root that holds it: this script's repository, SG_SQUAD, then
# SG_CHECKOUT; a bare run name resolves under SG_CHECKOUT/runs. Only `idle` on exit 0 lets the suite start: any other answer holds
# it, since a held nightly costs one night and a suite beside a live run costs the run. A gate lock whose pid lives holds it too.
# A hold writes no day marker, so the next idle cycle runs the suite.
if [ -s "$st/active-runs.txt" ]; then
  lroot=; for r in "$(cd "$here/../../.." 2>/dev/null && pwd)" "${SG_SQUAD:-}" "${SG_CHECKOUT:-}"; do
    [ -n "$r" ] && [ -f "$r/squad/run_liveness.py" ] && { lroot=$r; break; }; done
  lv=; [ -z "$lroot" ] || lv=$(cd "$lroot" && perl -e 'alarm shift; exec @ARGV' "${SG_DRAIN_READ_S:-30}" python3 -m squad.run_liveness drain "$st/active-runs.txt" "${SG_CHECKOUT:-}" 2>/dev/null)
  [ "$lv" = idle ] || { echo "nightly: held: the run reader answers '${lv:-no answer}' for active-runs.txt; the suite runs once the loop is idle (#1527)"; exit 0; }
fi
gp=$(cat "$st/locks/gate/pid" 2>/dev/null)
[ -n "$gp" ] && kill -0 "$gp" 2>/dev/null && { echo "nightly: held: the gate lock is held by pid $gp; the suite runs once the gate is free (#1527)"; exit 0; }
if [ "${1:-}" != --run ]; then   # the lock is taken once, around the suite, and never around the day check
  SERIAL_TRY=1 SERIAL_HOLD_SECONDS=${SERIAL_HOLD_SECONDS:-10800} \
    exec bash "$here/serial.sh" nightly bash "$0" --run
fi
nt0=$(date +%s)   # the run's own start: the settlement below stops before the hold serial.sh keeps for this process
tree=${SG_NIGHTLY_TREE:-$st/nightly-tree}; remote=${SG_NIGHTLY_REMOTE:-https://github.com/$SG_REPO.git}; base=${SG_BASE:-dev}
export GIT_TERMINAL_PROMPT=0
g() { /usr/bin/git -C "$tree" "$@"; }
[ -d "$tree/.git" ] || { mkdir -p "$tree" && g init -q . && g remote add origin "$remote"; } || exit 1
g remote set-url origin "$remote"
o=$(g fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>&1) || { echo "nightly: fetch of $base failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
tip=$(g rev-parse "origin/$base") || exit 1; dtip=$tip   # the dev tip the settlements are pruned against
# (#1975, ADR-0153 rule 1d) Both halves name one sha: the local half runs on the sha today's GitHub run tested, when that run
# exists and the sha can be had, and on the dev tip otherwise.
ght=$(gh_today_sha)
case "$ght" in *[!0-9a-f]*|'') ght=;; esac
if [ -n "$ght" ] && { g cat-file -e "$ght^{commit}" 2>/dev/null || g fetch -q origin "$ght" 2>/dev/null; }; then tip=$ght; fi
g checkout -q -f --detach "$tip" && g clean -q -ffdx || { echo "nightly: the tree could not check out $tip" >&2; exit 1; }
py=${SG_GATE_PY:-${SG_CHECKOUT:+$SG_CHECKOUT/.venv/bin/python}}; [ -x "$py" ] || py=python3
# The two modes are one command each, so a caller may point them elsewhere: the whole suite, heavy included, in both.
# The default commands run the local half only (#1975, ADR-0153): SQUAD_NIGHTLY_LOCAL_HALF=1 makes the conftest keep the tests a
# Linux runner deselects by marker (the macOS-bound shell suites among them), by the one deselection rule (ADR-0139). The GitHub
# half runs the rest. A configured command is the caller's whole suite.
half=; [ -z "${SG_NIGHTLY_PARALLEL:-}${SG_NIGHTLY_SERIAL:-}" ] && half=1
par=${SG_NIGHTLY_PARALLEL:-"SQUAD_NIGHTLY_LOCAL_HALF=1 $py -m pytest -p no:deepeval -n auto"}
ser=${SG_NIGHTLY_SERIAL:-"SQUAD_NIGHTLY_LOCAL_HALF=1 $py -m pytest -p no:deepeval -p no:xdist"}
# A failing id is whatever pytest's short summary names after `FAILED ` or `ERROR `: a node id, or the file a
# collection error names (`ERROR tests/test_module.py`, no `::`). Both are what the hub shows, so neither is dropped.
# pytest writes ` - ` and the failure's message after the id, so the id is the line up to the FIRST ` - `; the rest is
# the message and is never part of the id. A node id may itself carry a space (a parametrized case id such as
# `test_x[case with space]`), so the ids are never split at whitespace: the record carries them as ONE JSON array on its
# one line, and both readers (hub.py, ghcall.sh) parse that array and never a word of it.
ids_of() { sed -n -e 's/^FAILED //p' -e 's/^ERROR //p' "$1" | sed -e 's/ - .*//'; }
# rc_reason <exit code>: the signal a leg died of, or the exit code it ended on. A shell answers 128+signal for a command
# that a signal killed, so 143 names signal 15 and is never read as a plain exit 143. This is an assumption, not a proof:
# a configured command that chooses 129..192 itself is indistinguishable here from one a signal killed, so the record names
# signal-(code-128) for it. The nightly cannot see the wrapper's own choice; the reader reads the tag as the stop reason.
rc_reason() { if [ "$1" -ge 129 ] && [ "$1" -le 192 ]; then echo "signal-$(( $1 - 128 ))"; else echo "exit-$1"; fi; }
is_signal_rc() { [ "$1" -ge 129 ] && [ "$1" -le 192 ]; }
# A mode finished when pytest wrote its last line, the summary `<n> passed ... in 12.34s`. A mode that ends with any other
# last line was killed, or its interpreter died, before the summary: its output names no failing test, and it is not a red
# the suite measured (#1201: a dead controller leaves every worker in `PluggyTeardownRaisedWarning ... OSError: cannot send
# (already closed?)` and the run prints no summary). `lastline` is the last line with content, so trailing blank lines never
# hide it.
lastline() { awk 'NF { l = $0 } END { print l }' "$1" 2>/dev/null; }
finished() { lastline "$1" | /usr/bin/grep -qE '(passed|failed|error|errors|no tests ran|skipped|deselected|xfailed|xpassed).* in [0-9]+([.][0-9]+)?s( =+)?$'; }
# A code above 128 is a signal: the shell reports 143 for a process a TERM ended, and the alert names it as a signal.
signame() {
  case "$1" in
    129) echo SIGHUP;; 130) echo SIGINT;; 131) echo SIGQUIT;; 134) echo SIGABRT;; 137) echo SIGKILL;;
    139) echo SIGSEGV;; 143) echo SIGTERM;; *) echo "exit $1";;
  esac
}
pout=$st/nightly-parallel.log; sout=$st/nightly-serial.log
# verdict <log> <exit status> <configured command>: the mode field. A leg whose log names a failing test is red whatever it
# exited with (and names the signal that also stopped it). A leg that ended non-zero and named no failing test measured
# nothing: it is bounded, carrying the signal or the exit it died of, so a killed suite is never recorded as a red the suite
# found (ADR-0164).
# pytest exits 5 when it collects no test. Under the default command that is a repository with no Python suite, and no
# suite is not red (#758); under a configured command, 5 names no test either, so it is bounded like any other non-zero
# exit that named none.
verdict() {
  if [ "$2" = 0 ] || { [ "$2" = 5 ] && [ -z "$3" ]; }; then echo pass; return; fi
  if [ -n "$(ids_of "$1")" ]; then
    if is_signal_rc "$2"; then echo "fail:signal-$(( $2 - 128 ))"; else echo fail; fi
    return
  fi
  echo "bounded:$(rc_reason "$2")"
}
# One mode, and one retry when it ended before its summary: the end may be transient, and the retry's own summary is the
# reading. The first attempt's log stays as `<log>.first`, so the end, and the OSError that names its cause, are never lost.
# The mode runs in the background so the signal trap below can stop it at once, which a foreground command would delay.
ms=; mr=; mpid=
run_mode() {  # <command> <log> <configured command>; sets ms and mr
  local r
  ( cd "$tree" && bash -c "$1" ) > "$2" 2>&1 & mpid=$!
  wait "$mpid"; r=$?; mpid=
  ms=$(verdict "$2" "$r" "$3"); mr=$r
  case "$ms" in bounded:*)
    finished "$2" || {
      mv -f "$2" "$2.first"
      ( cd "$tree" && bash -c "$1" ) > "$2" 2>&1 & mpid=$!
      wait "$mpid"; r=$?; mpid=
      ms=$(verdict "$2" "$r" "$3"); mr=$r
    };; esac
}
# One record, by temp file and rename (ADR-0030). The trap below writes the same line, so a killed start leaves a record.
write_record() {  # <result> <parallel> <serial> <ids>
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "${now:-$(date +%s)}" "$1" "$tip" "$2" "$3" "$4" > "$st/nightly-result.$$" \
    && mv -f "$st/nightly-result.$$" "$st/nightly-result"
}
# mode_why <mode field> <log>: one phrase naming the stop and the line that names its cause, for the alert. A bounded leg
# names no failing test, so the alert never says "no failing test named".
mode_why() {
  local m=$1 log=$2 n=${1##*-} why d
  case "$m" in
    bounded:signal-*) if finished "$log"; then why="named no failing test and was stopped by signal $n ($(signame $(( n + 128 ))))"
                      else why="ended before its summary, stopped by signal $n ($(signame $(( n + 128 ))))"; fi;;
    bounded:exit-*) if finished "$log"; then why="ended without naming a failing test (exit $n)"
                    else why="ended before its summary (exit $n)"; fi;;
    *) why=$m;;
  esac
  d=$(/usr/bin/grep -E 'OSError|PluggyTeardownRaisedWarning|cannot send|Terminated|Killed' "$log" 2>/dev/null | tail -n 1)
  [ -n "$d" ] || d=$(lastline "$log")
  [ -n "$d" ] && why="$why: $(printf '%s' "$d" | tr '\t\n' '  ' | cut -c1-180)"
  printf '%s' "$why"
}
# A start that is killed before a mode result leaves the record too, and it names the kill: no reader takes a killed start
# for a red suite. It spends no day marker, so the next cycle tries again.
pm=; sm=; pmf=; smf=; cur=parallel
wdog=
wdog_stop() { [ -n "$wdog" ] || return 0; kill -TERM "$wdog" 2>/dev/null; wait "$wdog" 2>/dev/null; wdog=; }
killed() {  # <signal name> <exit code>
  trap - TERM INT HUP
  wdog_stop
  [ -n "$mpid" ] && { pkill -TERM -P "$mpid" 2>/dev/null; kill -TERM "$mpid" 2>/dev/null; }
  if [ -z "$tip" ]; then alert "NIGHTLY start killed by $1 before a tip was read"; exit "$2"; fi
  local n=$(( $2 - 128 ))
  [ -n "$pmf" ] || pm="bounded:signal-$n"
  [ -n "$smf" ] || sm="bounded:signal-$n"
  write_record bounded "$pm" "$sm" '[]'
  if [ -e "$st/nightly-bound-hit" ]; then
    # The hour's end stopped the run: one bounded attempt for the hour, so the hour's close writes no second record.
    rm -f "$st/nightly-bound-hit"; touch "$st/auto/nightly-missed-${day##*/nightly-}"
    alert "NIGHTLY passed its hour's bound at ${tip:0:7} before the ${cur:-first} mode finished: one bounded attempt; the dispatch is released"
  else
    alert "NIGHTLY start killed at ${tip:0:7} by $1 before the ${cur:-first} mode finished"
  fi
  exit "$2"
}
trap 'killed SIGTERM 143' TERM
trap 'killed SIGINT 130' INT
trap 'killed SIGHUP 129' HUP
set +m   # serial.sh runs this command in its own group; job notices here would only write "Terminated: 15" over the log
# (#1975, ADR-0153 rule 1b) The run is bounded by the end of its hour, when the dispatch hold ends: a watch stops it there, and
# the trap above records the bounded attempt. SG_NIGHTLY_BOUND_SECONDS sets the bound in seconds instead.
bleft=${SG_NIGHTLY_BOUND_SECONDS:-}
if [ -z "$bleft" ]; then
  bms=$(TZ=America/Sao_Paulo date +%M:%S); bleft=$(( 3600 - 10#${bms%%:*} * 60 - 10#${bms##*:} ))
fi
rm -f "$st/nightly-bound-hit"
# The watch fires only when its own sleep ends on time: a stopped sleep or a stopped watch never stops the run.
( sp=; trap '[ -n "$sp" ] && kill "$sp" 2>/dev/null; exit 0' TERM
  sleep "$bleft" & sp=$!; wait "$sp" || exit 0
  : > "$st/nightly-bound-hit"; kill -TERM $$ 2>/dev/null ) & wdog=$!
run_mode "$par" "$pout" "${SG_NIGHTLY_PARALLEL:-}"; pm=$ms; pmf=1
cur=serial
run_mode "$ser" "$sout" "${SG_NIGHTLY_SERIAL:-}"; sm=$ms; smf=1   # both modes always run: the record says which one broke
cur=
wdog_stop
trap - TERM INT HUP
failed=$st/nightly-failing.$$   # one complete id per line, so no id is ever split
{ ids_of "$pout"; ids_of "$sout"; } | sort -u > "$failed"
nfail=$(awk 'END{print NR}' "$failed")
ids=$("$py" -c 'import json,sys; print(json.dumps([l for l in sys.stdin.read().split("\n") if l], separators=(",", ":")))' < "$failed") \
  || { rm -f "$failed"; echo "nightly: the failing ids could not be encoded" >&2; exit 1; }
rm -f "$failed"
# The result follows its modes: pass only when both finished green, fail when a leg named tests, and bounded when a leg was
# stopped and none was red. The record carries the two facts on its own (ADR-0164).
res=pass
case "$pm $sm" in *fail*) res=fail;; esac
if [ "$res" != fail ] && { [ "$pm" != pass ] || [ "$sm" != pass ]; }; then res=bounded; fi
# The proof obligations a gate left (spec 1163, gate.sh). An unproven entry writes one record:
#   $st/obligations/<key>: `<repository> TAB <tree sha> TAB <base> TAB <checks that did not run> TAB <scope> TAB <deadline>`
# The nightly settles it: it reads the record, runs the checks the record names over the tree the record names, and writes
# one settlement line at $st/obligation-settled/<key>, the gate's own key:
#   `<epoch> TAB <repository> TAB <tree sha> TAB <base> TAB <scope measured> TAB <checks measured> TAB <green|red> TAB <run>`
# The scope measured is always `full`: the nightly never runs a cheaper scope (ADR-0018), so no delta selector call
# appears here (tests/test_gate_scope.py). The run is `<nightly epoch>@<nightly tree sha>`, so a settlement names the run it was
# measured in. A settlement clears the obligation it answers. A red settlement holds dispatch at the tree it measured, like
# any red (ghcall.sh, gh_dispatch_hold); it is never credited to another tree. A record the nightly cannot read, whose
# checks it cannot run, or whose tree it cannot fetch is reported by name and left in place: an obligation is never
# dropped. A record past its deadline is reported by name before it is measured; a nightly is always past the gate's
# fifteen-minute bound (ADR-0163), so the report marks it late and the measurement still answers it. The full suite this
# run already made answers `pytest` at the tip it measured, whichever tree a record before it checked out (ADR-0168): the
# nightly never runs that suite twice. A settlement never turns a red into a green: a tree this run measured red is settled
# red whatever its named checks say, and a settled red already at the entry stands, its own epoch kept, until the nightly
# period prune releases it by name.
now=$(date +%s); run="$now@$tip"
# The suite's own record goes first, exactly as it did before the obligations existed: the hub and the dispatch hold read
# it, and a settlement that hangs or is stopped never holds it back (ADR-0153).
write_record "$res" "$pm" "$sm" "$ids" || { echo "nightly: the result record could not be written" >&2; exit 1; }
verdict_write   # the local half joins the GitHub half in one verdict (#1975); an unproven one is read again by a later call
# Only a red the suite measured alerts as red: a red mode always names at least one failing test, so the alert names them.
# A bounded leg is unproven, not red; its own alert names the mode, the stop reason and the log line that caused it, so the
# cause is never "no failing test named" (#1201).
if [ "$res" = fail ]; then
  alert "NIGHTLY full suite red at ${tip:0:7}: $nfail failing test(s)"
elif [ "$res" = bounded ]; then
  why=
  case "$pm" in bounded:*) why="the parallel mode $(mode_why "$pm" "$pout")";; esac
  case "$sm" in bounded:*) why="${why:+$why; }the serial mode $(mode_why "$sm" "$sout")";; esac
  alert "NIGHTLY full suite bounded at ${tip:0:7}: $why"
fi
odir=$st/obligations; sdir=$st/obligation-settled; olog=$st/obligation-settle.log
# The default settlement commands are the checks the fast gate's battery runs (base-battery.sh): ruff and mypy over this
# repository's layout, then the full serial suite. A profile names its own in SG_SETTLE_RUFF, SG_SETTLE_MYPY and
# SG_SETTLE_PYTEST for another repository (ADR-0138); an unset command that the tree cannot feed measures nothing, and its
# record is reported unmeasured instead of settled red for a path that is not there.
ruffn=; mypyn=; pytestn=
[ -n "${SG_SETTLE_RUFF:-}" ] && ruffn=1
[ -n "${SG_SETTLE_MYPY:-}" ] && mypyn=1
[ -n "${SG_SETTLE_PYTEST:-}" ] && pytestn=1
ruffcmd=${SG_SETTLE_RUFF:-"$py -m ruff check squad tests"}
mypycmd=${SG_SETTLE_MYPY:-"$py -m mypy squad"}
pycmd=${SG_SETTLE_PYTEST:-"$py -m pytest -p no:deepeval -p no:xdist"}
ob_report() { printf '%s %s\n' "$(date '+%F %T')" "$1" >> "$st/alerts.log"; }
# proof_card <obligation key> <green|red>: the card fact of an unproven landing (spec 1163, #1176). wave.sh maps the issue
# to the obligation key under $st/proof-card/<issue>; this pass is the map's consumer. A green settlement clears the fact
# when no other obligation of the issue is open; a red one holds the card by its `proof:red` marker. A refused card write
# is reported by name, never silently dropped.
proof_card() {
  local k=$1 res=$2 d n out rc board=${BOARD:-$here/board.sh} word
  for d in "$st"/proof-card/*; do
    [ -d "$d" ] && [ -e "$d/$k" ] || continue
    n=$(basename "$d"); rm -f "$d/$k"
    if [ "$res" != red ] && [ -n "$(/bin/ls -A "$d" 2>/dev/null)" ]; then continue; fi   # another obligation is open
    rmdir "$d" 2>/dev/null
    if [ -z "${SG_OWNER:-}" ] || [ -z "${SG_PROJECT:-}" ]; then
      ob_report "PROOF-CARD ${n}@${k}: no board is named (SG_OWNER, SG_PROJECT); the $res fact is not written"; continue
    fi
    word=$([ "$res" = red ] && printf red || printf green)
    out=$("$board" --proof "$SG_OWNER" "$SG_PROJECT" "$n" "$word" 2>&1); rc=$?
    [ "$rc" -eq 0 ] || ob_report "PROOF-CARD ${n}@${k}: the $word fact was not written: $(printf '%s\n' "$out" | tail -1)"
  done
}
period=${SG_NIGHTLY_PERIOD_SECONDS:-86400}   # one nightly period: a settled red older than this is stale
hold=${SERIAL_HOLD_SECONDS:-10800}; case $hold in ''|*[!0-9]*) hold=10800;; esac
settle_by=$((nt0 + hold - hold / 10))   # stop settling before serial.sh stops this run at its hold (exit 75)
# A settled record at a tree that is no longer the dev tip holds nothing (ghcall.sh), and a settled red older than one
# nightly period is stale: the nightly prunes both, names each in the alert, and keeps the directory bounded.
for f in "$sdir"/*; do
  [ -f "$f" ] || continue
  line=$(cat -- "$f" 2>/dev/null) || continue
  nf=$(printf '%s' "$line" | awk -F'\t' '{print NF}')
  [ "$nf" = 8 ] || continue
  IFS=$'\t' read -r pts prp psh pba psc pch pres prun <<<"$line"
  case $pts in ''|*[!0-9]*) continue;; esac
  why=
  [ "${psh:0:12}" = "${dtip:0:12}" ] || why="its tree is not the dev tip ${dtip:0:12}"
  if [ -z "$why" ] && [ "$pres" = red ] && [ $(( now - pts )) -gt "$period" ]; then
    why="it is older than one nightly period and no green answered it"
  fi
  [ -n "$why" ] || continue
  rm -f "$f" && ob_report "OBLIGATION pruned: ${prp:-?}@${psh:0:12} (${pres:-?}, run ${prun:--}): $why; removed"
done
cur=$tip; obred=
for f in "$odir"/*; do
  [ -f "$f" ] || continue
  line=$(cat -- "$f" 2>/dev/null) || line=
  nf=$(printf '%s' "$line" | awk -F'\t' '{print NF}')
  if [ "$nf" != 6 ]; then
    ob_report "OBLIGATION unreadable at $(basename "$f"): expected six fields, found ${nf:-0}; left in place"; continue
  fi
  IFS=$'\t' read -r orp osh oba och osc odl <<<"$line"
  name="${orp:-?}@${osh:0:12}"
  case $odl in
    ''|*[!0-9]*) ob_report "OBLIGATION unreadable at $name: deadline '$odl' is not an epoch; left in place"; continue;;
  esac
  if [ "$odl" -lt "$now" ]; then
    ob_report "OBLIGATION overdue: $name base $oba checks [$och] missed its deadline $(date -r "$odl" '+%F %T' 2>/dev/null || printf '%s' "$odl"); settling it now"
  fi
  case ${orp:--} in
    -|"${SG_REPO:-}") ;;
    *) ob_report "OBLIGATION unmeasured: $name belongs to $orp, not ${SG_REPO:--}; left in place"; continue;;
  esac
  if [ "$(date +%s)" -ge "$settle_by" ]; then
    ob_report "OBLIGATION unsettled: $name: the nightly's hold deadline is near; left in place"
    continue
  fi
  want= bad=
  case " $och " in *" all "*|*" unknown "*) want="ruff mypy pytest";; esac
  if [ -z "$want" ]; then
    for c in $och; do
      case $c in ruff|mypy|pytest) want="$want $c";; -) ;; *) bad=$c;; esac
    done
    want=${want# }
    [ -n "$want" ] || want="ruff mypy pytest"
  fi
  if [ -n "$bad" ]; then
    ob_report "OBLIGATION unmeasured: $name names check '$bad', which the nightly cannot run; left in place"; continue
  fi
  tgreen=
  if [ "$osh" = "$tip" ]; then
    # The suite this run made answers pytest at the tip, whichever tree an earlier record checked out (ADR-0168): the
    # nightly never runs that suite twice at the tip.
    if [ "$cur" != "$tip" ]; then
      if ! g checkout -q -f --detach "$tip" || ! g clean -q -ffdx; then
        ob_report "OBLIGATION unmeasured: $name could not be checked out in the nightly tree; left in place"; continue
      fi
      cur=$tip
    fi
    # A local half that passed ran only part of the suite: the GitHub half's result at this sha answers pytest, and with
    # none yet the obligation stays in place, so no full suite runs here for it (ADR-0153 rule 1d). A red answers it red.
    t=$tree; case $res in pass) [ -n "$half" ] || tgreen=1;; fail) tgreen=0;; *) tgreen=;; esac
    if [ -n "$half" ] && [ "$res" = pass ]; then
      case $(gh_seen "$tip") in pass) tgreen=1;; fail) tgreen=0;; *)
        case " $want " in *" pytest "*)
          ob_report "OBLIGATION unmeasured: $name: pytest waits for the nightly GitHub half at ${tip:0:7}; left in place"; continue;; esac;;
      esac
    fi
  elif [ "$osh" = "$cur" ]; then
    t=$tree
  else
    if ! g cat-file -e "$osh^{commit}" 2>/dev/null; then
      g fetch -q origin "$osh" 2>/dev/null \
        || { ob_report "OBLIGATION unmeasured: $name cannot be fetched from origin; left in place"; continue; }
    fi
    if ! g checkout -q -f --detach "$osh" || ! g clean -q -ffdx; then
      ob_report "OBLIGATION unmeasured: $name could not be checked out in the nightly tree; left in place"; continue
    fi
    cur=$osh; t=$tree
  fi
  # An older tree the GitHub half measured is answered by that result too, with no local suite (ADR-0153 rule 1d).
  if [ -n "$half" ] && [ -z "$tgreen" ] && [ "$osh" != "$tip" ]; then
    case $(gh_seen "$osh") in pass) tgreen=1;; fail) tgreen=0;; esac
  fi
  # A bounded suite at the tip measured no red: an obligation that needs pytest cannot be answered by it, so the record
  # stays open, never settled red (ADR-0164). A red still answers it as red.
  if [ "$osh" = "$tip" ] && [ "$res" = bounded ]; then
    case " $want " in *" pytest "*)
      ob_report "OBLIGATION unmeasured: $name: the nightly full suite did not finish; left in place"; continue;; esac
  fi
  ov=green; odet=; late=
  for c in $want; do
    if [ "$c" = pytest ] && [ -n "$tgreen" ]; then
      [ "$tgreen" = 1 ] || { ov=red; odet="$odet pytest"; }
      continue
    fi
    unmeas=
    case $c in
      ruff) [ -n "$ruffn" ] || [ -d "$t/squad" ] || [ -d "$t/tests" ] || unmeas=1;;
      mypy) [ -n "$mypyn" ] || [ -d "$t/squad" ] || unmeas=1;;
      pytest) [ -n "$pytestn" ] || [ -d "$t/tests" ] || unmeas=1;;
    esac
    if [ -n "$unmeas" ]; then
      ob_report "OBLIGATION unmeasured: $name names check '$c', and the tree holds no path the default command reads; left in place"
      late=1; break
    fi
    if [ "$(date +%s)" -ge "$settle_by" ]; then
      ob_report "OBLIGATION unsettled: $name: the nightly's hold deadline came before '$c' could run; left in place"
      late=1; break
    fi
    cmd=$ruffcmd; [ "$c" = mypy ] && cmd=$mypycmd; [ "$c" = pytest ] && cmd=$pycmd
    ( cd "$t" && bash -c "$cmd" ) >> "$olog" 2>&1 || { ov=red; odet="$odet $c"; }
  done
  [ -n "$late" ] && continue
  [ "$osh" = "$tip" ] && [ "$res" = fail ] && { ov=red; odet="$odet the full suite"; }
  key=$(printf '%s\t%s\t%s' "$orp" "$osh" "$oba" | cksum | cut -d' ' -f1)
  # A settlement never turns a red into a green: a settled red for the same entry stands, and its own epoch is kept so the
  # age prune above still counts from the measurement that found the defect.
  s_ts=$now; s_run=$run; pts= pst= pru=
  [ -r "$sdir/$key" ] && IFS=$'\t' read -r pts prp psh pba psc pch pst pru < "$sdir/$key" 2>/dev/null
  if [ "$pst" = red ]; then
    if [ "$ov" != red ]; then ov=red; odet="$odet a prior settled-red at this tree stands"; fi
    s_ts=${pts:-$now}; s_run=${pru:-$run}
  fi
  if mkdir -p "$sdir" 2>/dev/null \
    && printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$s_ts" "$orp" "$osh" "$oba" full "$want" "$ov" "$s_run" > "$sdir/$key.$$" \
    && mv -f "$sdir/$key.$$" "$sdir/$key"; then
    rm -f "$f"
    if [ "$ov" = red ]; then
      obred=1; ob_report "OBLIGATION settled-red: $name base $oba at $s_run:${odet# } failed"
      proof_card "$key" red
    else
      proof_card "$key" green
    fi
  else
    rm -f "$sdir/$key.$$" 2>/dev/null
    ob_report "OBLIGATION unsettled: $name: the settlement record could not be written; left in place"
  fi
done
: > "$day"   # the run is spent for today, green or red
# The live check (spec 1163, #1178): the queue's invariants against the live state, over its own hour, once the night's
# record and its settlements are written. It is started detached, so this body returns and serial.sh frees `locks/nightly`
# at once: serial.sh runs this script, so a check awaited here would keep the nightly lock for its whole hour and refuse
# every promotion tick (ADR-0163, ADR-0164). It takes its own lock in try mode and writes its own record; SG_SERIAL_HELD is
# dropped because this script no longer holds `nightly` once it returns. Its exit never reaches this run's result: a
# finding raises its own alert, and nohup keeps the check alive after this shell is gone. `--start` starts it only when no
# live lease names a check (#1660): one check per state directory, however many nightly runs start one.
env -u SG_SERIAL_HELD bash "$here/live-check.sh" --start
[ "$res" = pass ] && [ -z "$obred" ] && exit 0   # a red settlement fails the run too: it measured a defect
exit 1
