#!/bin/bash
# Regression test for wave.sh land (#1416): a wave never closes the child its tip check dropped. The tip-check bisection removes
# a child that broke the tip; the no-Refs fallback and the per-commit close skip that child's issue. A refusal alone never
# excludes an issue, and work already in the base still closes. A bare local remote, a stub gh, a stub board: no GitHub call.
# usage: bash test-wave-dropped.sh [scripts dir]
unset REV REV_BASE REV_SHA SG_SERIAL_HELD SG_GATE_STATUS SG_GATE_KIND SERIAL_WAIT_SECONDS SERIAL_HOLD_SECONDS SERIAL_POLL_SECONDS
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
cp "$d/wave.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/wavepause.py" "$d/child_issues.py" "$d/address.sh" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/base-battery.sh" "$d/basecheck.sh" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SG_RETIRE_CMD=/usr/bin/true   # a capped run here is an ended run (#1970); test-wave-retire.sh holds the retirement
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf 'export SG_LAND_REMOTE=%s/origin.git SG_BOARD_VOCAB=shipped\n: "${SG_WAVE_TIP_GATES:=true}"; export SG_WAVE_TIP_GATES\n' "$T"
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *) echo "{}";; esac\n' "$T" > "$T/bin/gh"
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"
# closes: the issue numbers the stub gh was asked to close, one per line, in order.
closes() { sed -n 's#^api -X PATCH repos/x/y/issues/\([0-9]*\) -f state=closed -f state_reason=completed$#\1#p' "$T/gh.log" 2>/dev/null | tr '\n' ' '; }
# mkrun <run> <issues|-> <message|''>: the run's folder with its dispatch request, its wave branch, and one child commit on the wave.
# <issues> is one issue or a comma list (a multi-issue request). An issue of `-` writes a request with no CHILD line and no
# record, so the run's issues cannot be read.
mkrun() { local run=$1 n=$2 msg=$3 rd="$T/co/runs/$1" i
  mkdir -p "$rd"; : > "$rd/record.jsonl"
  if [ "$n" = - ]; then rm -f "$rd/record.jsonl"; printf 'work with no child line\n' > "$rd/request.txt"
  else printf 'CHILD — work GitHub issue(s) %s: do it\n' "$n" > "$rd/request.txt"
    for i in ${n//,/ }; do echo "$run" > "$T/st/auto/disp-$i"; done; fi
  (cd "$T/co" && bash "$T/s/wave.sh" start "$run") >/dev/null
  [ -n "$msg" ] || return 0   # no message: the wave carries no commit
  rm -rf "$T/c-$run"; $G clone -q "$T/origin.git" "$T/c-$run" 2>/dev/null
  (cd "$T/c-$run" && $G checkout -q -b w "origin/wave/$run" && echo "$run" > "g-$run" && $G add "g-$run" \
    && $G -c user.email=a@b -c user.name=t commit -q -m "$msg" && $G push -q origin "HEAD:refs/heads/wave/$run") 2>/dev/null; }
# entered <run> <issue>: the issue ledger records that the issue's child entered the run's wave, as wave.sh enter writes it.
entered() { (. "$T/s/env.sh"; python3 "$T/s/issue_ledger.py" wave "x/y#$2" "$1" entered "child entered") >/dev/null; }
# cap <run>: the run's wave is past its age cap.
cap() { printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-$1"; }
land() { (cd "$T/co" && bash "$T/s/wave.sh" land "$1" 2>&1); }

# Tip red while a commit of #N is in the tree; the child's own suite fails for the issues in DROP.
TIPRED='! /usr/bin/git log --format=%s origin/dev..HEAD | /usr/bin/grep -q "bad"'
CHILDRED='case " $DROP " in *" $SG_WAVE_CHILD "*) exit 1;; esac; exit 0'
# addc <run> <message> <file>: one more child commit on the run's wave.
addc() { (cd "$T/c-$1" && echo "$3" > "$3" && $G add "$3" && $G -c user.email=a@b -c user.name=t commit -q -m "$2" \
  && $G push -q origin "HEAD:refs/heads/wave/$1") 2>/dev/null; }
ledger() { (. "$T/s/env.sh"; python3 "$T/s/issue_ledger.py" "$@") >/dev/null; }
queued() { /usr/bin/grep -c "WAVE REQUEUE #$1" <<< "$2"; }

# a: one child, dropped by the tip check: the wave lands with no commit, the issue stays open, no landed.tsv row, the requeue stands.
mkrun run-a 91 "bad work 91 (Refs #91)"; entered run-a 91; : > "$T/gh.log"; rm -f "$T/st/landed.tsv"
r=$(DROP=91 SG_WAVE_TIP_GATES=$TIPRED SG_WAVE_CHILD_GATES=$CHILDRED land run-a); rc=$?
case "$r" in *"WAVE REQUEUE #91"*) ok "a: the requeue stands";; *) ko "a: no requeue: '$r'";; esac
[ -z "$(closes)" ] && ok "a: the dropped child's issue #91 stays open" || ko "a: closes '$(closes)': '$r'"
[ -s "$T/st/landed.tsv" ] && ko "a: landed.tsv has a row: $(cat "$T/st/landed.tsv")" || ok "a: no landed.tsv row"
ls "$T/st/cq" 2>/dev/null | /usr/bin/grep -q 'delivery' && ko "a: a delivery comment is queued: $(ls "$T/st/cq")" || ok "a: no delivery comment is queued"

# b: two children, one dropped and one survives (its commit names no issue): only the survivor closes.
mkrun run-b 92,93 "bad work 92 (Refs #92)"; addc run-b "work 93 with no reference line" g93; entered run-b 92; entered run-b 93; : > "$T/gh.log"
r=$(DROP=92 SG_WAVE_TIP_GATES=$TIPRED SG_WAVE_CHILD_GATES=$CHILDRED land run-b); rc=$?
[ "$(closes)" = "93 " ] && ok "b: only the survivor #93 closes" || ko "b: closes '$(closes)': '$r'"
case "$r" in *"landed in dev (#93)"*) ok "b: the landing names #93 only";; *) ko "b: landing line: '$r'";; esac

# c: an interrupted bisection (the child leaves, then the tip's check holds), then a retry: the child that left stays out,
# so the retry lands only the survivor's work and closes only its issue (#2301). The wave carries a second child, so a tip stays ahead of the base and the check runs
# again: a wave whose only child left runs no check and closes empty (#2209, test-wave.sh W2209).
mkrun run-c 94,98 "bad work 94 (Refs #94)"; addc run-c "work 98 (Refs #98)" g98; entered run-c 94; entered run-c 98; : > "$T/gh.log"; rm -f "$T/n-c"
TIPC='n=$(cat "$SG_STATE/n-c" 2>/dev/null || echo 0); echo $((n + 1)) > "$SG_STATE/n-c"; if [ $n = 0 ]; then ! /usr/bin/git log --format=%s origin/dev..HEAD | /usr/bin/grep -q bad; elif [ $n = 1 ]; then echo "serial: waited 1s for lock gate (bounded)"; exit 75; else true; fi'
r=$(DROP=94 SG_WAVE_TIP_GATES=$TIPC SG_WAVE_CHILD_GATES=$CHILDRED land run-c); rc=$?
[ $rc != 0 ] && [ -z "$(closes)" ] && ok "c: the interrupted landing closes nothing" || ko "c: exit $rc, closes '$(closes)': '$r'"
r=$(SG_WAVE_TIP_GATES=true land run-c); rc=$?
[ $rc = 0 ] && [ "$(closes)" = "98 " ] && ok "c: the retry closes #98 only" || ko "c: exit $rc, closes '$(closes)': '$r'"
/usr/bin/git -C "$T/origin.git" log --format=%s dev | /usr/bin/grep -q "bad work 94" && ko "c: the work of #94, which left the wave, reached dev" || ok "c: the work of #94, which left the wave, stays off dev"
/usr/bin/git -C "$T/origin.git" log --format=%s dev | /usr/bin/grep -q "work 98" && ok "c: the work of #98 reached dev" || ko "c: the work of #98 is not on dev"

# d: a refused child that re-enters and lands closes.
mkrun run-d 95 "work 95 (Refs #95)"; ledger wave x/y#95 run-d refused "an earlier refusal" keep; entered run-d 95; : > "$T/gh.log"
r=$(SG_WAVE_TIP_GATES=true land run-d); rc=$?
[ $rc = 0 ] && [ "$(closes)" = "95 " ] && ok "d: a refused child that re-entered and landed closes" || ko "d: exit $rc, closes '$(closes)': '$r'"

# e: work already in the base (a squash without Refs, test-wave.sh case U): the empty wave still closes the entered child.
mkrun run-e 96 ""; entered run-e 96; : > "$T/gh.log"
r=$(SG_WAVE_TIP_GATES=true land run-e); rc=$?
[ $rc = 0 ] && [ "$(closes)" = "96 " ] && ok "e: an entered child of an empty wave still closes" || ko "e: exit $rc, closes '$(closes)': '$r'"

# f: the tip check drops the only child, a long bisection lets the base move, the push is refused, and the retry finds nothing
# ahead: the empty path must not count the dropped child as delivered.
mkrun run-f 97 "bad work 97 (Refs #97)"; entered run-f 97; : > "$T/gh.log"; rm -f "$T/st/landed.tsv" "$T/moved"
rm -rf "$T/mv"; $G clone -q "$T/origin.git" "$T/mv" 2>/dev/null
CHILDMOVE='if [ ! -e "$T_MOVED" ]; then : > "$T_MOVED"; (cd "$T_MV" && echo m > mv-f && /usr/bin/git add mv-f && /usr/bin/git -c user.email=a@b -c user.name=t commit -q -m "base moves" && /usr/bin/git push -q origin HEAD:refs/heads/dev) >/dev/null 2>&1; fi; case " $DROP " in *" $SG_WAVE_CHILD "*) exit 1;; esac; exit 0'
r=$(T_MOVED=$T/moved T_MV=$T/mv DROP=97 SG_WAVE_TIP_GATES=$TIPRED SG_WAVE_CHILD_GATES=$CHILDMOVE land run-f); rc=$?
[ -e "$T/moved" ] && ok "f: the base moved during the bisection" || ko "f: the base never moved: '$r'"
[ -z "$(closes)" ] && ok "f: the dropped child's issue #97 stays open on the empty retry" || ko "f: closes '$(closes)': '$r'"
[ -s "$T/st/landed.tsv" ] && ko "f: landed.tsv has a row: $(cat "$T/st/landed.tsv")" || ok "f: no landed.tsv row on the empty retry"

[ $fail = 0 ] && echo "PASS test-wave-dropped" || echo "FAIL test-wave-dropped"
exit $fail
