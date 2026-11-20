#!/bin/bash
# seconds: 55.24
# The wave tip's check (#1290, #1409, #1687, #1700, #1434): its gate rounds are marked, a Python-only red names the
# child, a red on a tree a gate passed gets a second look, and the sibling filter reads the bodies.
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"

# W1290 (#1290): a landing that runs more than one gate round (a red tip, the child that broke it leaves, the tip again) marks each
# round on its held lock (locks/single-wland/round), so the held alert reads the last round's start as progress. The tip gate
# records whether the mark is there when it runs, then clears it: a mark the second round did not set reads as absent.
W93=wave/run-93; start2 run-93 >/dev/null; child 930 g930 "origin/$W93"; en2 run-93 930 >/dev/null; child 931 g931 "origin/$W93"; en2 run-93 931 >/dev/null
mkdir -p "$T/st/locks/single-wland"; rm -f "$T/st/rounds.log"; printf 'run-93\n' > "$T/st/auto/disp-931"
r=$(SG_WAVE_TIP_GATES='[ -e "$SG_STATE/locks/single-wland/round" ] && echo marked >> "$SG_STATE/rounds.log"; rm -f "$SG_STATE/locks/single-wland/round"; ! /usr/bin/git log --format=%s origin/dev..HEAD | /usr/bin/grep -q "work 931"' SG_WAVE_CHILD_GATES='[ "$SG_WAVE_CHILD" = 931 ] && exit 1; exit 0' land2 run-93); rc=$?
[ "$(/usr/bin/grep -c '^marked$' "$T/st/rounds.log" 2>/dev/null)" = 2 ] && ok "W1290: each gate round of the landing marks its held lock" || ko "W1290: marks at the tip gate: '$(cat "$T/st/rounds.log" 2>/dev/null)' (exit $rc: '$r')"
[ $rc = 0 ] && ok "W1290: the two-round landing lands the wave without the child that broke it" || ko "W1290: exit $rc: '$r'"
rm -rf "$T/st/locks/single-wland"

# W1409 (#1409): a Python-only red at the wave tip names the child whose own pytest delta fails. The child's pytest runs
# through gate.sh: under the gate lock (SG_SERIAL_HELD names gate) and holding gate slots (SQUAD_GATE_WORKERS), never beside them.
# SG_WAVE_CHILD_PYTEST replaces the delta command, as SG_WAVE_CHILD_GATES replaces the shell suites; the default child shell suites pass here.
W96=wave/run-96; start2 run-96 >/dev/null; child 1940 g1940 "origin/$W96"; en2 run-96 1940 >/dev/null; child 1941 g1941 "origin/$W96"; en2 run-96 1941 >/dev/null
printf 'run-96\n' > "$T/st/auto/disp-1941"; rm -f "$T/st/py.log"; : > "$T/gh.log"; : > "$T/board.log"
r=$(SG_WAVE_TIP_GATES='! /usr/bin/git log --format=%s origin/dev..HEAD | /usr/bin/grep -q "work 1941"' SG_WAVE_CHILD_PYTEST='echo "$SG_WAVE_CHILD held=${SG_SERIAL_HELD:-none} workers=${SQUAD_GATE_WORKERS:-none}" >> "$SG_STATE/py.log"; [ "$SG_WAVE_CHILD" = 1941 ] && exit 1; exit 0' land2 run-96 2>&1); rc=$?
[ $rc = 0 ] && ok "W1409: the wave without the child whose pytest delta fails lands" || ko "W1409: exit $rc: '$r'"
case "$r" in *"WAVE CHECK RED"*"#1941"*) ok "W1409: the red names the child whose pytest delta fails";; *) ko "W1409: no child named: '$r'";; esac
[ "$(head -1 "$T/st/py.log" 2>/dev/null | cut -d' ' -f1)" = 1941 ] && ok "W1409: the newest child's delta runs first" || ko "W1409: pytest order: '$(cat "$T/st/py.log" 2>/dev/null)'"
/usr/bin/grep -q '^1941 held=gate workers=1$' "$T/st/py.log" 2>/dev/null && ok "W2594: the child's pytest takes one gate slot by default" || ko "W2594: the child gate's count: '$(cat "$T/st/py.log" 2>/dev/null)'"
$O cat-file -e dev:g1941 2>/dev/null && ko "W1409: the failing child landed" || ok "W1409: the failing child did not land"
$O cat-file -e dev:g1940 2>/dev/null && ok "W1409: the other child landed" || ko "W1409: the other child did not land"
[ "$(/usr/bin/grep -c '^o 3 Todo 1941$' "$T/board.log")" = 1 ] && ok "W1409: the failing child is requeued once" || ko "W1409: requeue: '$(cat "$T/board.log")'"
# W2594 (#2594): SG_WAVE_GATE_WORKERS names the count a wave child gate takes, so one wave no longer holds half a budget of four.
W94=wave/run-94; start2 run-94 >/dev/null; child 1947 g1947 "origin/$W94"; en2 run-94 1947 >/dev/null
printf 'run-94\n' > "$T/st/auto/disp-1947"; rm -f "$T/st/py.log"; : > "$T/gh.log"; : > "$T/board.log"
r=$(SG_WAVE_GATE_WORKERS=2 SG_WAVE_TIP_GATES='! /usr/bin/git log --format=%s origin/dev..HEAD | /usr/bin/grep -q "work 1947"' SG_WAVE_CHILD_PYTEST='echo "workers=${SQUAD_GATE_WORKERS:-none}" >> "$SG_STATE/py.log"; exit 0' land2 run-94 2>&1); rc=$?
[ "$(/usr/bin/tail -1 "$T/st/py.log" 2>/dev/null)" = "workers=2" ] && ok "W2594: SG_WAVE_GATE_WORKERS names the child gate's count" || ko "W2594: named count: '$(cat "$T/st/py.log" 2>/dev/null)' (exit $rc)"

# A green tip runs no child pytest.
W95=wave/run-95; start2 run-95 >/dev/null; child 950 g950 "origin/$W95"; en2 run-95 950 >/dev/null
rm -f "$T/st/py.log"
r=$(SG_WAVE_TIP_GATES=true SG_WAVE_CHILD_PYTEST='echo ran >> "$SG_STATE/py.log"; exit 1' land2 run-95 2>&1); rc=$?
[ $rc = 0 ] && [ ! -e "$T/st/py.log" ] && ok "W1409: a green tip runs no child pytest" || ko "W1409: exit $rc, py.log: '$(cat "$T/st/py.log" 2>/dev/null)': '$r'"

# A default-style tip red (a measured fail naming pytest at this HEAD, recorded by gate.sh as fast-gate.sh does) never blames a child whose own pytest passes, and the child
# gate writes no wave-level journal: the child runs with SG_GATE_STATUS=self and no SG_GATE_WAVE.
W97=wave/run-97; start2 run-97 >/dev/null; child 1944 g1944 "origin/$W97"; en2 run-97 1944 >/dev/null; child 1945 g1945 "origin/$W97"; en2 run-97 1945 >/dev/null
printf 'run-97\n' > "$T/st/auto/disp-1945"; : > "$T/gh.log"; : > "$T/board.log"
printf '%s\n' 'printf "fail\tred\tpytest\n" > "$GATE_OUTCOME_FILE"; exit 1' > "$T/tipred.sh"
r=$(SG_WAVE_TIP_GATES="SERIAL_TRY=1 SG_GATE_KIND=fast bash $T/s/gate.sh bash $T/tipred.sh" SG_WAVE_CHILD_PYTEST='exit 0' land2 run-97 2>&1); rc=$?
case "$r" in *"broke the wave tip"*) ko "W1409: a passing child was named after a tip red: '$r'";; *) ok "W1409: a passing child is not named after a measured tip red";; esac
[ "$(/usr/bin/grep -c '^o 3 Todo 194[45]$' "$T/board.log")" = 0 ] && ok "W1409: no passing child is requeued after a tip red" || ko "W1409: requeue: '$(cat "$T/board.log")'"

# The tip check of the wave second look (#1687, #1700): the fast gate through gate.sh, which writes its own history row for the
# tree it checked -- kind fast, the selection and the scope its battery named (#1700 finding 5). It fails, as a flaky red does.
printf '%s\n' 'printf "selected 2 files\n" > "$GATE_SELECT_FILE"; printf "delta\n" > "$GATE_SCOPE_FILE"; printf "fail\tred\tpytest\n" > "$GATE_OUTCOME_FILE"; exit 1' > "$T/tipred1700.sh"
TIP1700="SERIAL_TRY=1 SG_GATE_KIND=fast bash $T/s/gate.sh bash $T/tipred1700.sh"

# W1687 (#1687, #1700): a tip red on a tree a gate passed before may be the machine's (a timeout under load), so no child is
# blamed on it: the wave waits and the check runs again on a later pass. A second red on that same tip takes the red path.
# The covering pass is the row the child's entry gate wrote before the wave landed, with caller entry; the tip check runs
# through gate.sh and writes its own row, the red one. Both name the same tree and the same suite (#1700 finding 5).
# The wave carries two children (#2209): the one the check names leaves, and the other keeps a tip ahead of the base, so the
# check runs again on it. A wave whose only child left runs no check at all and closes empty (W2209).
W98=wave/run-98; start2 run-98 >/dev/null; child 1959 g1959 "origin/$W98"; en2 run-98 1959 >/dev/null; child 1960 g1960 "origin/$W98"; en2 run-98 1960 >/dev/null
printf 'run-98\n' > "$T/st/auto/disp-1960"; : > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/auto/tipred-wave_run-98"
# The covering pass is the wave entry's row: the child's own gate ran on this tree before the wave landed, with caller entry,
# over the same kind, selection and scope the tip check names (#1700 finding 5).
T98=$($G -C "$T/origin.git" rev-parse "$W98")
printf '1\tfast\tpass\t%s\t0\tselected 2 files\tdelta\tpass\t-\truff mypy pytest\t-\t2\t-\tentry\n' "$T98" >> "$T/st/gate-history"
# The tip without #1960 has a pass of its own too: the entry gate of #1959 wrote it (#1699).
printf '1\tfast\tpass\t%s\t0\tselected 2 files\tdelta\tpass\t-\truff mypy pytest\t-\t2\t-\tentry\n' "$($G -C "$T/origin.git" rev-parse "$W98~1")" >> "$T/st/gate-history"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='exit 1' land2 run-98 2>&1); rc=$?
[ $rc != 0 ] && case "$r" in *"WAVE WAIT $W98"*"passed before"*) true;; *) false;; esac && ok "W1687: a tip red on a tree a gate passed holds the wave for a second look" || ko "W1687: first red: exit $rc: '$r'"
case "$r" in *"broke the wave tip"*) ko "W1687: a child was blamed on the first red of a passed tree: '$r'";; *) ok "W1687: no child is blamed on the first red of a passed tree";; esac
[ "$(/usr/bin/grep -c '^o 3 Todo 1960$' "$T/board.log")" = 0 ] && ok "W1687: the child is not requeued on that red" || ko "W1687: requeue: '$(cat "$T/board.log")'"
# W1700 finding 5: the fixture is production's. In production the entry gate writes the covering pass row on the wave tip
# before the check, with caller entry, and the tip check writes its own row, the red one. Neither row is written by the
# other's check.
hn=$(awk -F'\t' -v s="$($G -C "$T/origin.git" rev-parse "$W98")" '$4 == s && $14 == "entry" && $3 == "pass" && $6 == "selected 2 files"' "$T/st/gate-history" | /usr/bin/grep -c .)
[ "$hn" = 1 ] && ok "W1700: the covering pass is the entry gate's row on the wave tip" || ko "W1700: entry pass rows for the wave tip: '$hn' in '$(cat "$T/st/gate-history")'"
hn=$(awk -F'\t' -v s="$($G -C "$T/origin.git" rev-parse "$W98")" '$4 == s && $14 == "tip" && $3 == "fail"' "$T/st/gate-history" | /usr/bin/grep -c .)
[ "$hn" = 1 ] && ok "W1700: the tip check writes its own red row" || ko "W1700: tip red rows for the wave tip: '$hn' in '$(cat "$T/st/gate-history")'"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='/usr/bin/git diff --name-only "$SG_WAVE_CHILD_FROM" "$SG_WAVE_CHILD_TO" | /usr/bin/grep -qx g1960 && exit 1; exit 0' land2 run-98 2>&1); rc=$?
case "$r" in *"WAVE CHECK RED"*"#1960"*) ok "W1687: a second red on the same tip names the child, as before";; *) ko "W1687: second red: exit $rc: '$r'";; esac
# After the child left, the tip without it is red too, on a tree with a pass of its own: that red takes the old path in
# the same pass. A second look there would move the one marker to the other tip, and the wave would wait for ever (#1699).
case "$r" in *"WAVE WAIT"*) ko "W1699: a red after a child left opened another second look: '$r'";; *) ok "W1699: a red after a child left opens no second look";; esac
[ "$(cat "$T/st/auto/tipred-wave_run-98" 2>/dev/null)" = "$($G -C "$T/origin.git" rev-parse "$W98" 2>/dev/null)" ] && ok "W1699: the marker stays on the tip that waited" || ko "W1699: the marker moved to another tip: '$(cat "$T/st/auto/tipred-wave_run-98" 2>/dev/null)'"
# A child's own pytest that was bounded (gate.sh exit 4) measured no red: the child is not blamed and the wave waits.
W99=wave/run-99; start2 run-99 >/dev/null; child 1962 g1962 "origin/$W99"; en2 run-99 1962 >/dev/null
printf 'run-99\n' > "$T/st/auto/disp-1962"; : > "$T/gh.log"; : > "$T/board.log"
r=$(SG_WAVE_TIP_GATES=false SG_WAVE_CHILD_PYTEST='echo "gate: bounded: the gate passed its 1s deadline and no check reported a defect"; exit 4' land2 run-99 2>&1); rc=$?
[ $rc != 0 ] && case "$r" in *"WAVE WAIT $W99"*"no verdict"*) true;; *) false;; esac && ok "W1687: a bounded child check holds the wave with no verdict" || ko "W1687: bounded child: exit $rc: '$r'"
case "$r" in *"broke the wave tip"*) ko "W1687: a bounded child check blamed the child: '$r'";; *) ok "W1687: a bounded child check blames no child";; esac
[ "$(/usr/bin/grep -c '^o 3 Todo 1962$' "$T/board.log")" = 0 ] && ok "W1687: the bounded child is not requeued" || ko "W1687: requeue: '$(cat "$T/board.log")'"
# An exit 4 with no bounded line is the command's own (pytest exits 4 on a conftest import error): the child is red (#1699).
W97=wave/run-97; start2 run-97 >/dev/null; child 1964 g1964 "origin/$W97"; en2 run-97 1964 >/dev/null
printf 'run-97\n' > "$T/st/auto/disp-1964"; : > "$T/gh.log"; : > "$T/board.log"
r=$(SG_WAVE_TIP_GATES=false SG_WAVE_CHILD_PYTEST='exit 4' land2 run-97 2>&1); rc=$?
case "$r" in *"#1964 broke the wave tip"*) ok "W1699: a child whose check exits 4 with no bounded line is blamed";; *) ko "W1699: bare exit 4: exit $rc: '$r'";; esac

# W1700 (#1700): the debt of the #1687 review. The second-look marker goes wherever the check settles and the wave lands or
# stops; the second look needs the same tree AND the same suite; the marker's write decides; the wait for a check with no
# verdict names its one cause and keeps the check's log. The marker names one wave: auto/tipred-<wave with / as _>.
# 1: a check that passes with no word still settles the check, so it clears the marker.
W70=wave/run-70; start2 run-70 >/dev/null; child 1701 g1701 "origin/$W70"; en2 run-70 1701 >/dev/null
M70="$T/st/auto/tipred-wave_run-70"; printf '%s\n' "$($G -C "$T/origin.git" rev-parse "$W70")" > "$M70"
r=$(SG_WAVE_TIP_GATES=true land2 run-70 2>&1); rc=$?
[ $rc = 0 ] && [ ! -e "$M70" ] && ok "W1700: a check that passes with no word clears the second-look marker" || ko "W1700: no-word pass: exit $rc, marker '$(cat "$M70" 2>/dev/null)': '$r'"
# 1: an unproven check holds the wave (#1802) and clears the second-look marker: the next pass runs the check again.
W71=wave/run-71; start2 run-71 >/dev/null; child 1702 g1702 "origin/$W71"; en2 run-71 1702 >/dev/null
M71="$T/st/auto/tipred-wave_run-71"; printf '%s\n' "$($G -C "$T/origin.git" rev-parse "$W71")" > "$M71"
r=$(SG_WAVE_TIP_GATES='printf "unproven\tinconclusive\t\tpytest\n" > "$GATE_OUTCOME_FILE"; exit 4' land2 run-71 2>&1); rc=$?
[ $rc != 0 ] && [ ! -e "$M71" ] && ! $O cat-file -e dev:g1702 2>/dev/null && ok "W1700: an unproven check holds the wave and clears the marker" || ko "W1700: unproven: exit $rc, marker '$(cat "$M71" 2>/dev/null)': '$r'"
# 1: an empty wave ends here: the branch goes, and so does its marker.
W72=wave/run-72; start2 run-72 >/dev/null
M72="$T/st/auto/tipred-wave_run-72"; printf 'deadbeef\n' > "$M72"
r=$(land2 run-72 2>&1); rc=$?
[ $rc = 0 ] && [ ! -e "$M72" ] && ok "W1700: an empty landing clears the marker" || ko "W1700: empty landing: exit $rc, marker '$(cat "$M72" 2>/dev/null)': '$r'"
# 1: a wave that freezes for good ends too, and its marker goes with it. The landing cannot fetch its base (a remote that is
# not there is no network cause), so it freezes before any check could settle the marker, at one try.
W73=wave/run-73; start2 run-73 >/dev/null; child 1703 g1703 "origin/$W73"; en2 run-73 1703 >/dev/null
M73="$T/st/auto/tipred-wave_run-73"; printf 'deadbeef\n' > "$M73"
r=$(SG_LAND_REMOTE="$T/nosuch.git" SG_WAVE_LAND_TRIES=1 SG_WAVE_TIP_GATES=true land2 run-73 2>&1); rc=$?
case "$r" in *"WAVE NEEDS-HUMAN"*) true;; *) false;; esac && [ ! -e "$M73" ] \
  && ok "W1700: a frozen wave clears the marker" || ko "W1700: frozen wave: exit $rc, marker '$(cat "$M73" 2>/dev/null)': '$r'"

# 2: the second look is the same tree AND the same suite. A pass row of the tip check's kind over another selection is another
# suite, and so is a pass of another kind over a delta: neither covers the tip check.
W74=wave/run-74; start2 run-74 >/dev/null; child 1704 g1704 "origin/$W74"; en2 run-74 1704 >/dev/null
: > "$T/st/gate-history"; M74="$T/st/auto/tipred-wave_run-74"; rm -f "$M74"
printf '1\tfast\tpass\t%s\t0\tselected 1 file\tdelta\tpass\t-\truff mypy pytest\t-\t2\t-\tentry\n' "$($G -C "$T/origin.git" rev-parse "$W74")" >> "$T/st/gate-history"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='exit 0' land2 run-74 2>&1); rc=$?
case "$r" in *"passed before"*) ko "W1700: another selection covered the tip check: '$r'";; *) ok "W1700: a pass row over another selection does not cover the tip check";; esac
[ ! -e "$M74" ] && ok "W1700: no second look is spent on another suite" || ko "W1700: the second look was spent on another suite"
W75=wave/run-75; start2 run-75 >/dev/null; child 1705 g1705 "origin/$W75"; en2 run-75 1705 >/dev/null
: > "$T/st/gate-history"; M75="$T/st/auto/tipred-wave_run-75"; rm -f "$M75"
printf '1\tgate\tpass\t%s\t0\tselected 2 files\tdelta\tpass\t-\truff mypy pytest\t-\t2\t-\tentry\n' "$($G -C "$T/origin.git" rev-parse "$W75")" >> "$T/st/gate-history"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='exit 0' land2 run-75 2>&1); rc=$?
case "$r" in *"passed before"*) ko "W1700: a delta pass of another gate kind covered the tip check: '$r'";; *) ok "W1700: a delta pass of another gate kind does not cover the tip check";; esac
# 2: a full battery covers any suite of the same tree: it ran every test the tip check asks for.
W76=wave/run-76; start2 run-76 >/dev/null; child 1706 g1706 "origin/$W76"; en2 run-76 1706 >/dev/null
: > "$T/st/gate-history"; M76="$T/st/auto/tipred-wave_run-76"; rm -f "$M76"
T76=$($G -C "$T/origin.git" rev-parse "$W76")
printf '1\tfull\tpass\t%s\t0\tall: the promotion proof runs the full battery\tfull\tpass\t-\tfull\t-\t2\t-\tother\n' "$T76" >> "$T/st/gate-history"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='exit 0' land2 run-76 2>&1); rc=$?
case "$r" in *"passed before"*) ok "W1700: a full battery on the same tree covers the tip check";; *) ko "W1700: full battery: exit $rc: '$r'";; esac
[ "$(cat "$M76" 2>/dev/null)" = "$T76" ] && ok "W1700: the second look names the tip it was spent on" || ko "W1700: marker: '$(cat "$M76" 2>/dev/null)' for tip '$T76'"

# 3: the marker's write decides. A marker path that cannot hold the sha (a directory here) leaves no proof that the second
# look was spent, so the red takes its usual path and one line names the marker; an unchecked write would answer the second
# look on every pass and the wave would never land.
W77=wave/run-77; start2 run-77 >/dev/null; child 1707 g1707 "origin/$W77"; en2 run-77 1707 >/dev/null
: > "$T/st/gate-history"; : > "$T/board.log"; mkdir -p "$T/st/auto/tipred-wave_run-77"
printf '1\tfast\tpass\t%s\t0\tselected 2 files\tdelta\tpass\t-\truff mypy pytest\t-\t2\t-\tentry\n' "$($G -C "$T/origin.git" rev-parse "$W77")" >> "$T/st/gate-history"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='exit 1' land2 run-77 2>&1); rc=$?
case "$r" in *"passed before"*) ko "W1700: an unwritable marker answered a second look it never recorded: '$r'";; *) ok "W1700: an unwritable marker spends no second look";; esac
case "$r" in *"WAVE-NOTE $W77"*"cannot be written"*) ok "W1700: an unwritable marker is named";; *) ko "W1700: the unwritable marker is silent: '$r'";; esac
case "$r" in *"#1707 broke the wave tip"*) ok "W1700: with no marker written, the red names the child";; *) ko "W1700: marker write: exit $rc: '$r'";; esac
rm -rf "$T/st/auto/tipred-wave_run-77"

# 4: the three causes of a check with no verdict are named apart, and each keeps the check's log, so a bounded child and a held
# lock leave a trace for the next pass. The holder of the gate lock is the same one Q uses.
W78=wave/run-78; start2 run-78 >/dev/null; child 1708 g1708 "origin/$W78"; en2 run-78 1708 >/dev/null
sleep 30 & hp=$!; mkdir -p "$T/st/locks/gate"; echo "$hp" > "$T/st/locks/gate/pid"
r=$(SERIAL_TRY=1 SG_WAVE_TIP_GATES="bash '$T/s/gate.sh' true" land2 run-78 2>&1); rc=$?
case "$r" in *"WAVE WAIT $W78"*"gate lock is held by pid $hp"*) ok "W1700: a held gate lock is named as its own cause";; *) ko "W1700: held lock: exit $rc: '$r'";; esac
case "$r" in *"gate slots are held by another gate, or a child's own check was bounded"*) ko "W1700: the causes still share one catch-all: '$r'";; *) ok "W1700: a held lock is not read as the catch-all";; esac
[ -f "$T/st/wave-check-wave_run-78.log" ] && ok "W1700: a check the lock held keeps its log" || ko "W1700: the check log of the held landing was deleted"
rm -rf "$T/st/locks/gate"; kill $hp 2>/dev/null
W79=wave/run-79; start2 run-79 >/dev/null; child 1709 g1709 "origin/$W79"; en2 run-79 1709 >/dev/null
r=$(SG_WAVE_TIP_GATES='echo tip-check-ran; exit 1' SG_WAVE_CHILD_PYTEST='echo "gate: bounded: the gate passed its 1s deadline and no check reported a defect"; exit 4' land2 run-79 2>&1); rc=$?
case "$r" in *"WAVE WAIT $W79"*"#1709"*"bounded"*) ok "W1700: a bounded child is named as its own cause";; *) ko "W1700: bounded child: exit $rc: '$r'";; esac
L79="$T/st/wave-check-wave_run-79.log"
[ -f "$L79" ] && /usr/bin/grep -q 'tip-check-ran' "$L79" && /usr/bin/grep -q 'gate: bounded: ' "$L79" \
  && ok "W1700: the bounded child's own line stays in the check log" || ko "W1700: check log: '$(cat "$L79" 2>/dev/null)'"

# W2330 (#2330): a caps.gates skip names the gate that holds the slots, in each of the three shapes the budget writes: the
# budget is full, it has free slots but not the whole count, and it keeps its freed slots for a waiting priority check. The
# lines are the budget's own: squad.caps writes them here against a throwaway budget of three. held_by reads the first holder
# of each and answers `slots:<holder>`; a line whose `held by` is prose, never the shape's own hold, answers nothing.
mkdir -p "$T/genproj/docs/squad"; printf 'caps:\n  gates: 3\n' > "$T/genproj/docs/squad/defaults.yaml"
sleep 30 & hp=$!
capgen() { PYTHONPATH="$SG_SQUAD" SQUAD_CAPS_DIR="$T/genleases" python3 -m squad.caps "$@" --pid "$hp" --target "$T/genproj"; }
caprel() { PYTHONPATH="$SG_SQUAD" SQUAD_CAPS_DIR="$T/genleases" python3 -m squad.caps release gates "$1" >/dev/null 2>&1; }
capgen take gates child-gate-7 --count 1 >/dev/null 2>&1
FREE=$(capgen take gates gate-9 --count 3 --try --priority tip 2>&1); s1=$?
caprel gate-9
capgen take gates wave-tip-2 --count 3 --try --priority tip >/dev/null 2>&1
QUEUED=$(capgen take gates child-gate-9 --count 1 --try 2>&1); s2=$?
caprel wave-tip-2; capgen take gates child-gate-8 --count 2 >/dev/null 2>&1
FULL=$(capgen take gates gate-11 --count 3 --try --priority tip 2>&1); s3=$?
caprel gate-11; caprel child-gate-8; caprel child-gate-7; kill $hp 2>/dev/null; wait $hp 2>/dev/null
case "$s1:$FREE" in 73:*"has 2 free slot(s) of 3"*"held by child-gate-7 (lease pid"*"skipped, not queued (try)") ok "W2330: the budget writes the free-slot skip line";; *) ko "W2330: free-slot line: $s1 '$FREE'";; esac
case "$s2:$QUEUED" in 73:*"keeps its freed slots for wave-tip-2"*"held by child-gate-7 (lease pid"*"skipped, not queued (try)") ok "W2330: the budget writes the queued-waiter skip line";; *) ko "W2330: queued-waiter line: $s2 '$QUEUED'";; esac
case "$s3:$FULL" in 73:*"is full (3 slots), held by child-gate-7 (lease pid"*"skipped, not queued (try)") ok "W2330: the budget writes the full skip line";; *) ko "W2330: full line: $s3 '$FULL'";; esac
# wave.sh returns before its subcommands when it is sourced, so the probe calls the predicate the landing calls. $0 is in the
# fixture's scripts dir, so wave.sh sources the env beside it, as a landing there does.
hby() { bash -c '. "$1/wave.sh"; held_by "$2"' "$T/s/probe.sh" "$T/s" "$1"; }
for c in FREE QUEUED FULL; do
  got=$(hby "${!c}"); grc=$?
  [ "$got" = "slots:child-gate-7" ] && [ "$grc" = 0 ] && ok "W2330: held_by names the holder of the $c line" || ko "W2330: held_by($c) = '$got' (exit $grc)"
done
got=$(hby 'caps: caps.gates has 2 free slot(s) of 3: gate-9 needs its whole count of 3; held by child-gate-7 (lease pid 9), other-gate-3 (lease pid 8): gate-9 skipped, not queued (try)'); grc=$?
[ "$got" = "slots:child-gate-7" ] && [ "$grc" = 0 ] && ok "W2330: held_by reads the first of two holders" || ko "W2330: held_by(two holders) = '$got' (exit $grc)"
got=$(hby 'caps: caps.gates is full (3 slots), held by ?: gate-9 skipped, not queued (try)'); grc=$?
[ "$got" = "slots:?" ] && [ "$grc" = 0 ] && ok "W2330: held_by names an unknown holder" || ko "W2330: held_by(unknown) = '$got' (exit $grc)"
for prose in 'caps: caps.gates is full (3 slots), and it was held by the wrong gate: gate-9 skipped, not queued (try)' \
  'caps: caps.gates has 2 free slot(s) of 3: gate-9 needs its whole count of 3; it was held by the wrong gate: gate-9 skipped, not queued (try)' \
  'caps: caps.gates was held by the wrong gate earlier: gate-9 skipped, not queued (try)'; do
  got=$(hby "$prose"); grc=$?
  [ -z "$got" ] && [ "$grc" = 1 ] && ok "W2330: a prose held-by answers nothing: ${prose:0:50}" || ko "W2330: held_by(prose) = '$got' (exit $grc): $prose"
done
# The wave's own tip check reads the queued-waiter skip as a hold too, and its wait line names the holder without the lease.
W80=wave/run-80; start2 run-80 >/dev/null; child 1710 g1710 "origin/$W80"; en2 run-80 1710 >/dev/null
{ echo '#!/bin/bash'; echo "cat >&2 <<'EOX'"; printf '%s\n' "$QUEUED"; echo EOX; echo 'exit 73'; } > "$T/capskip.sh"
r=$(SG_WAVE_TIP_GATES="bash $T/capskip.sh" land2 run-80 2>&1); rc=$?
case "$r" in *"WAVE WAIT $W80"*"gate slots are held by child-gate-7;"*) ok "W2330: the tip check's wait names the holder of the queued-waiter line";; *) ko "W2330: queued-waiter wait: exit $rc: '$r'";; esac
case "$r" in *"lease pid"*) ko "W2330: the wait line quotes the lease detail: '$r'";; *) ok "W2330: the wait line carries no lease detail";; esac

# SD (#1434): the sibling filter before dispatch reads the bodies; the entry also reads each child's own diff. Two siblings
# whose bodies name nothing in common but whose diffs touch one file are a conflict before the wave: the second never
# enters and its issue goes back to the queue, so the landing never attempts the merge of the pair. A sibling whose diff
# is disjoint from the wave's enters as it always did.
# The file both siblings change exists in the base first, and #61 is cut from that same base, not from the wave tip: the pair
# is genuinely independent, so neither diff carries a sibling's change and only the derived check can stop the second.
rm -rf "$T/basef"; $G clone -q "$T/origin.git" "$T/basef" 2>/dev/null
(cd "$T/basef" && $G checkout -q dev && printf 'a\nb\nc\nd\ne\nf\n' > shared60 && $G add shared60 \
  && $G -c user.email=a@b -c user.name=t commit -q -m "the shared file" && $G push -q origin HEAD:dev)
WS=wave/run-s; start2 run-s >/dev/null
inwave() { $O merge-base --is-ancestor "$($G -C "$T/c$2" rev-parse HEAD)" "$1" 2>/dev/null && echo yes || echo no; }
# The body filter passes the pair: neither body names a repository-relative file and neither names a spec shared with the
# other, so siblings.py lets them join. The entry's diff check is what must stop the second.
cat > "$T/issues-sd.json" <<'J'
[{"number": 60, "body": "Adjust the first line of the greeting."},
 {"number": 61, "body": "Adjust the last line of the greeting."}]
J
python3 "$d/siblings.py" "$T/issues-sd.json" 61 60 >/dev/null 2>&1 && ok "SD: the body filter lets the pair join, so only the diff check can stop it" || ko "SD: the body filter already refuses the pair, so this case would prove nothing"
rm -rf "$T/c60"; $G clone -q "$T/origin.git" "$T/c60" 2>/dev/null
(cd "$T/c60" && $G checkout -q -b gui/60 "origin/$WS" && printf 'A\nb\nc\nd\ne\nf\n' > shared60 && $G add shared60 \
  && $G -c user.email=a@b -c user.name=t commit -q -m "work 60 (Refs #60)")
en2 run-s 60 >/dev/null
# #61 is cut from the wave's base and changes another line of the same file: a rebase on the tip applies cleanly, so nothing
# but the derived check stops it. Neither body names the file.
rm -rf "$T/c61"; $G clone -q "$T/origin.git" "$T/c61" 2>/dev/null
(cd "$T/c61" && $G checkout -q -b gui/61 origin/dev && printf 'a\nb\nc\nd\ne\nF\n' > shared60 && $G add shared60 \
  && $G -c user.email=a@b -c user.name=t commit -q -m "work 61 (Refs #61)")
tip=$($O rev-parse "$WS"); printf 'run-s\n' > "$T/st/auto/disp-61"; : > "$T/board.log"
REV_BASE=$WS rev 61
r=$((cd "$T/c61" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-s 61) 2>&1)
[ "$($O rev-parse "$WS")" = "$tip" ] && [ "$(inwave "$WS" 61)" = no ] && ok "SD: a sibling whose diff shares a file never enters the wave" || ko "SD: #61 entered: '$r'"
case "$r" in *"WAVE REQUEUE #61"*shared60*) ok "SD: the shared file is named before the wave";; *) ko "SD: the shared file is not named: '$r'";; esac
python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); sys.exit(0 if any(sorted(q["issues"])==[60,61] and q["paths"]==["shared60"] for q in p) else 1)' "$T/st/shared-pairs.json" 2>/dev/null && ok "SD: the refused pair is recorded with its shared path (#1941)" || ko "SD: no pair record: '$(cat "$T/st/shared-pairs.json" 2>&1)'"
/usr/bin/grep -qx 'o 3 Todo 61' "$T/board.log" && ok "SD: its issue is back in the queue, as a body conflict leaves it" || ko "SD: no Todo card for #61: '$(cat "$T/board.log")'"
[ ! -e "$T/st/auto/disp-61" ] && ok "SD: its dispatch marker is dropped, so a later run builds it alone" || ko "SD: the marker is kept"
# The merge of the pair is never attempted: the run recorded #60 alone, so the landing carries #60 alone and never names #61
# or a conflict. The base keeps the entrant's version of the file. #61's card and close never move.
mkdir -p "$T/co/runs/run-s"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #60 work"}}' > "$T/co/runs/run-s/record.jsonl"
: > "$T/gh.log"
r=$(land2 run-s 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in "WAVE $WS "*) ok "SD: the wave of the one entrant lands";; *) ko "SD: the landing is not named: '$r'";; esac || ko "SD: exit $rc: '$r'"
case "$r" in *#61*) ko "SD: the landing attempted #61, a child the wave never carried: '$r'";; *conflict*) ko "SD: the landing hit the conflict the entry prevents: '$r'";; *) ok "SD: the landing never attempts #61's merge and names no conflict";; esac
[ "$($O show dev:shared60 2>/dev/null | head -1)" = A ] && [ "$($O show dev:shared60 2>/dev/null | tail -1)" = f ] && ok "SD: the base holds the entrant's version of the file, never #61's" || ko "SD: dev:shared60 is '$($O show dev:shared60 2>/dev/null | tr '\n' ' ')'"
/usr/bin/grep -q 'issues/60 -f state=closed' "$T/gh.log" && ok "SD: the landing delivers the one entrant" || ko "SD: #60 is not closed: '$(cat "$T/gh.log")'"
/usr/bin/grep -q 'issues/61 -f state=closed' "$T/gh.log" && ko "SD: #61 was delivered by a landing that never carried it" || ok "SD: #61 is not closed: its merge was never attempted"
# The disjoint sibling passes the derived check unchanged: two children of one wave, each cut from its base on its own file,
# both enter.
WS2=wave/run-s2; start2 run-s2 >/dev/null
child 964 g964 origin/dev; en2 run-s2 964 >/dev/null
child 965 g965 origin/dev; en2 run-s2 965 >/dev/null
[ "$(inwave "$WS2" 964)" = yes ] && [ "$(inwave "$WS2" 965)" = yes ] && ok "SD: siblings with disjoint diffs enter, unchanged" || ko "SD: a disjoint sibling was held out"
[ "$($O cat-file -e "$WS2:g964" 2>/dev/null && echo yes)" = yes ] && [ "$($O cat-file -e "$WS2:g965" 2>/dev/null && echo yes)" = yes ] && ok "SD: the wave carries both disjoint siblings" || ko "SD: the wave does not carry both: '$(inwave "$WS2" 964) $(inwave "$WS2" 965)'"
# A diff the entry cannot read is a WAVE-ERROR, never a clean pass: the child's clone has no origin/dev, so the wave's side
# of the comparison cannot be computed and the child enters no wave.
child 966 g966 origin/dev
$G -C "$T/c966" update-ref -d refs/remotes/origin/dev
REV_BASE=$WS2 rev 966
r=$((cd "$T/c966" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-s2 966) 2>&1); rc=$?
[ $rc = 2 ] && ok "SD: a sibling diff the entry cannot read exits 2" || ko "SD: exit $rc, not 2: '$r'"
case "$r" in *"WAVE-ERROR"*"cannot be read"*) ok "SD: the unreadable diff is a WAVE-ERROR, never a clean pass";; *) ko "SD: the unreadable diff is silent: '$r'";; esac

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
