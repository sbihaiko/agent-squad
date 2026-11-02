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
#   $SG_STATE/nightly-result: `<epoch> TAB <pass|fail> TAB <sha> TAB <parallel> TAB <serial> TAB <ids>`
# The result is a pass only when both modes pass. `<ids>` is ONE JSON array of the complete failing test ids, `[]` when
# none; a mode that failed names at least the tests it failed, and the hub draws them. An id is never split at a space,
# so a parametrized case id such as `test_x[case with space]` reaches the hub whole. A red record holds dispatch
# (ghcall.sh, gh_dispatch_hold) until one green run replaces it.
# A red result raises one alert on the path health.sh already uses: one line in $SG_STATE/alerts.log and one notification
# (HEALTH_NOTIFY overrides the notifier). The two mode logs stay in $SG_STATE/nightly-parallel.log and nightly-serial.log.
# An open bound marker (promote-pass.sh writes $SG_STATE/promote-bound, ADR-0164) moves the run to the oldest marked tip that
# $SG_STATE/promote-bound-settled does not name; the result then settles that tip there, pass or fail, and dev waits a day.
# It also settles the proof obligations a gate left unproven (spec 1163): it runs the checks each record in
# $SG_STATE/obligations names over the tree that record names, writes settled-green or settled-red in
# $SG_STATE/obligation-settled, clears the record it answered, reports an overdue one by name, and a settled red holds the
# tree it measured like any red (ghcall.sh).
here="$(cd "$(dirname "$0")" && pwd)"; st=${SG_STATE:-/tmp/sg-state}
mkdir -p "$st/auto" "$st/locks"
day=$st/auto/nightly-$(date +%F)
[ -n "${SG_NIGHTLY_OFF:-}" ] && exit 0   # a profile that sets it keeps no nightly: a project may carry its own suite instead (ADR-0153)
[ -e "$st/promote-reserve" ] && exit 0   # a proof is measuring in its own tree: two full suites saturate the box before either shows in the load average, and no day marker is written, so the nightly runs once the proof is over
[ -e "$day" ] && exit 0   # it already ran today: no second suite, whatever started this one
if [ "${1:-}" != --run ]; then   # the lock is taken once, around the suite, and never around the day check
  SERIAL_TRY=1 SERIAL_HOLD_SECONDS=${SERIAL_HOLD_SECONDS:-10800} \
    exec bash "$here/serial.sh" nightly bash "$0" --run
fi
tree=${SG_NIGHTLY_TREE:-$st/nightly-tree}; remote=${SG_NIGHTLY_REMOTE:-https://github.com/$SG_REPO.git}; base=${SG_BASE:-dev}
export GIT_TERMINAL_PROMPT=0
g() { /usr/bin/git -C "$tree" "$@"; }
bound_open() {  # the oldest bound-ended tip that no settlement line names
  [ -f "$st/promote-bound" ] || return 0
  local t
  while IFS=$'\t' read -r _ t _; do
    [ -n "$t" ] || continue
    awk -F'\t' -v t="$t" '$2 == t { f = 1 } END { exit !f }' "$st/promote-bound-settled" 2>/dev/null || { printf '%s\n' "$t"; return 0; }
  done < "$st/promote-bound"
}
[ -d "$tree/.git" ] || { mkdir -p "$tree" && g init -q . && g remote add origin "$remote"; } || exit 1
g remote set-url origin "$remote"
o=$(g fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>&1) || { echo "nightly: fetch of $base failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
tip=$(g rev-parse "origin/$base") || exit 1
# ADR-0164 (#1046): an open bound marker names a tip the full suite never finished. The nightly proves that tip, not dev, and
# settles the marker with its result; a red settles as red and fails the record.
bt=$(bound_open)
if [ -n "$bt" ] && ! g cat-file -e "$bt^{commit}" 2>/dev/null && ! g fetch -q origin "$bt" 2>/dev/null; then bt=; fi
[ -n "$bt" ] && tip=$bt
g checkout -q -f --detach "$tip" && g clean -q -ffdx || { echo "nightly: the tree could not check out $tip" >&2; exit 1; }
py=${SG_GATE_PY:-${SG_CHECKOUT:+$SG_CHECKOUT/.venv/bin/python}}; [ -x "$py" ] || py=python3
# The two modes are one command each, so a caller may point them elsewhere: the whole suite, heavy included, in both.
par=${SG_NIGHTLY_PARALLEL:-"$py -m pytest -p no:deepeval -n auto"}
ser=${SG_NIGHTLY_SERIAL:-"$py -m pytest -p no:deepeval -p no:xdist"}
# A failing id is whatever pytest's short summary names after `FAILED ` or `ERROR `: a node id, or the file a
# collection error names (`ERROR tests/test_module.py`, no `::`). Both are what the hub shows, so neither is dropped.
# pytest writes ` - ` and the failure's message after the id, so the id is the line up to the FIRST ` - `; the rest is
# the message and is never part of the id. A node id may itself carry a space (a parametrized case id such as
# `test_x[case with space]`), so the ids are never split at whitespace: the record carries them as ONE JSON array on its
# one line, and both readers (hub.py, ghcall.sh) parse that array and never a word of it.
ids_of() { sed -n -e 's/^FAILED //p' -e 's/^ERROR //p' "$1" | sed -e 's/ - .*//'; }
pout=$st/nightly-parallel.log; sout=$st/nightly-serial.log
# pytest exits 5 when it collects no test. Under the default command that is a repository with no Python suite, and no
# suite is not red (#758); a profile names its own command in SG_NIGHTLY_PARALLEL/SG_NIGHTLY_SERIAL, and there 5 is red.
mode() { ( cd "$tree" && bash -c "$1" ) > "$2" 2>&1; local r=$?; [ $r = 0 ] || { [ $r = 5 ] && [ -z "$3" ]; }; }
pm=pass; mode "$par" "$pout" "${SG_NIGHTLY_PARALLEL:-}" || pm=fail
sm=pass; mode "$ser" "$sout" "${SG_NIGHTLY_SERIAL:-}" || sm=fail   # both modes always run: the record says which one broke
failed=$st/nightly-failing.$$   # one complete id per line, so no id is ever split
{ ids_of "$pout"; ids_of "$sout"; } | sort -u > "$failed"
nfail=$(awk 'END{print NR}' "$failed")
ids=$("$py" -c 'import json,sys; print(json.dumps([l for l in sys.stdin.read().split("\n") if l], separators=(",", ":")))' < "$failed") \
  || { rm -f "$failed"; echo "nightly: the failing ids could not be encoded" >&2; exit 1; }
rm -f "$failed"
res=pass; { [ "$pm" = pass ] && [ "$sm" = pass ]; } || res=fail
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
# run already made answers `pytest` at the tip it measured (ADR-0168): the nightly never runs that suite twice. A
# settlement never turns a red into a green: a tree this run measured red is settled red whatever its named checks say.
now=$(date +%s); run="$now@$tip"
odir=$st/obligations; sdir=$st/obligation-settled; olog=$st/obligation-settle.log
ruffcmd=${SG_SETTLE_RUFF:-"$py -m ruff check squad tests"}
mypycmd=${SG_SETTLE_MYPY:-"$py -m mypy squad"}
pycmd=${SG_SETTLE_PYTEST:-"$py -m pytest -p no:deepeval -p no:xdist"}
ob_report() { printf '%s %s\n' "$(date '+%F %T')" "$1" >> "$st/alerts.log"; }
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
  if [ "$osh" = "$cur" ]; then
    t=$tree
    [ "$osh" = "$tip" ] && tgreen=$([ "$res" = pass ] && echo 1 || echo 0)
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
  ov=green; odet=
  for c in $want; do
    if [ "$c" = pytest ] && [ -n "$tgreen" ]; then
      [ "$tgreen" = 1 ] || { ov=red; odet="$odet pytest"; }
      continue
    fi
    cmd=$ruffcmd; [ "$c" = mypy ] && cmd=$mypycmd; [ "$c" = pytest ] && cmd=$pycmd
    ( cd "$t" && bash -c "$cmd" ) >> "$olog" 2>&1 || { ov=red; odet="$odet $c"; }
  done
  [ "$osh" = "$tip" ] && [ "$res" != pass ] && { ov=red; odet="$odet the full suite"; }
  key=$(printf '%s\t%s\t%s' "$orp" "$osh" "$oba" | cksum | cut -d' ' -f1)
  if mkdir -p "$sdir" 2>/dev/null \
    && printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$now" "$orp" "$osh" "$oba" full "$want" "$ov" "$run" > "$sdir/$key.$$" \
    && mv -f "$sdir/$key.$$" "$sdir/$key"; then
    rm -f "$f"
    [ "$ov" = red ] && { obred=1; ob_report "OBLIGATION settled-red: $name base $oba at $run:${odet# } failed"; }
  else
    rm -f "$sdir/$key.$$" 2>/dev/null
    ob_report "OBLIGATION unsettled: $name: the settlement record could not be written; left in place"
  fi
done
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$now" "$res" "$tip" "$pm" "$sm" "$ids" > "$st/nightly-result.$$" \
  && mv -f "$st/nightly-result.$$" "$st/nightly-result" || { echo "nightly: the result record could not be written" >&2; exit 1; }
if [ "$res" = fail ]; then
  if [ "$nfail" -gt 0 ]; then msg="NIGHTLY full suite red at ${tip:0:7}: $nfail failing test(s)"
  else msg="NIGHTLY full suite red at ${tip:0:7}: no failing test named"; fi
  printf '%s %s\n' "$(date '+%F %T')" "$msg" >> "$st/alerts.log"
  notify=${HEALTH_NOTIFY:-/usr/bin/osascript}
  "$notify" -e "display notification \"${msg//\"/\'}\" with title \"squad-goal\"" >/dev/null 2>&1
fi
[ -n "$bt" ] && printf '%s\t%s\t%s\n' "$(date +%s)" "$bt" "$res" >> "$st/promote-bound-settled"
: > "$day"   # the run is spent for today, green or red
[ "$res" = pass ] && [ -z "$obred" ] && exit 0   # a red settlement fails the run too: it measured a defect
exit 1
