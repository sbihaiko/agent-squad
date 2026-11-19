#!/bin/bash
# seconds: 1.86
# Regression test for hand-gate.sh (#1169): a hand gate declares its worker count and its reason where it starts, and a
# delta of at most two files takes at most two workers. A throwaway repository and a stub python that logs its argv: no
# battery runs and no machine budget is taken for real.
# usage: bash test-hand-gate.sh [scripts dir]
unset SG_SERIAL_HELD SG_GATE_WORKERS SG_GATE_REASON SG_HAND_SMALL_WORKERS SG_HAND_SMALL_FILES
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G="git -c user.email=a@b -c user.name=t"; export SG_STATE=$T/st SQUAD_CAPS_DIR=$T/caps SG_FAST_LOG_DIR=$T/logs SG_FAST_MEMO=0
mkdir -p "$SG_STATE" "$SG_FAST_LOG_DIR"
R=$T/r; git init -q -b dev "$R"; echo base > "$R/a.txt"; $G -C "$R" add -A; $G -C "$R" commit -q -m base
$G -C "$R" checkout -q -b gui/fix; echo 1 >> "$R/a.txt"; echo 1 > "$R/b.txt"; $G -C "$R" add -A; $G -C "$R" commit -q -m fix
printf '#!/bin/bash\necho "$*" >> "%s/py.log"\ncase "$*" in *squad.testselect*) echo "all: stub";; esac\nexit 0\n' "$T" > "$T/py"; chmod +x "$T/py"
hg() { rm -f "$T/py.log" "$SG_STATE/gate-status"; (cd "$R" && SG_FAST_BASE=dev SG_GATE_PY="$T/py" SG_FAST_RETRY_CMD=false bash "$d/hand-gate.sh" 2>&1); }

out=$(hg); rc=$?
[ $rc = 2 ] && /usr/bin/grep -q 'no worker count declared' <<<"$out" && [ ! -e "$T/py.log" ] && ok "a hand gate with no count is refused and runs no battery" || ko "no count: rc $rc: $out"

out=$(SG_GATE_WORKERS=auto hg); rc=$?
[ $rc = 2 ] && /usr/bin/grep -q 'no worker count declared' <<<"$out" && [ ! -e "$T/py.log" ] && ok "a count that is not a number is refused, never passed to pytest" || ko "count auto: rc $rc: $out"

out=$(SG_GATE_WORKERS=2 hg); rc=$?
[ $rc = 2 ] && /usr/bin/grep -q 'no reason declared' <<<"$out" && [ ! -e "$T/py.log" ] && ok "a hand gate with a count and no reason is refused" || ko "no reason: rc $rc: $out"

out=$(SG_GATE_WORKERS=6 SG_GATE_REASON="two-file fix" hg); rc=$?
[ $rc = 2 ] && /usr/bin/grep -q 'takes at most 2 workers' <<<"$out" && [ ! -e "$T/py.log" ] && ok "a two-file delta above the small ceiling is refused" || ko "two files at 6: rc $rc: $out"

out=$(SG_GATE_WORKERS=2 SG_GATE_REASON="two-file fix in a.txt and b.txt" hg); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx -- "-m pytest -p no:deepeval -n 2 -m not heavy" "$T/py.log" && ok "a two-file hand fix runs its pytest with the low declared count" || ko "two files at 2: rc $rc: '$(cat "$T/py.log" 2>&1)' $out"
/usr/bin/grep -q 'hand-gate: 2 workers, 2 files changed, reason: two-file fix in a.txt and b.txt' <<<"$out" && ok "the start line records the count, the file count and the reason" || ko "start line: $out"

$G -C "$R" checkout -q gui/fix; echo 1 > "$R/c.txt"; $G -C "$R" add -A; $G -C "$R" commit -q -m third
out=$(SG_GATE_WORKERS=6 SG_GATE_REASON="three files" hg); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx -- "-m pytest -p no:deepeval -n 6 -m not heavy" "$T/py.log" && ok "a delta above the small ceiling takes the count the hand gate declared" || ko "three files at 6: rc $rc: '$(cat "$T/py.log" 2>&1)' $out"

rm -rf "$T"
[ $fail = 0 ] && echo "test-hand-gate: all ok" || { echo "test-hand-gate: FAILED"; exit 1; }
