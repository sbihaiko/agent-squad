# Sourced by a suite that starts a watchd driver under its temp root (#989).
# driver_tree_kill ROOT: TERM every process whose command names ROOT and every descendant of one, rescanning each pass
# until none is left (a supervisor may restart a lane while it stops), then KILL what a last scan still finds. A lane outlives
# a supervisor killed first (it is reparented to pid 1), so the tree is found by its root, not by its parent. It never signals
# a process group: a driver that shares the suite's group would take the suite down with it.
. "${BASH_SOURCE[0]%/*}/caps.sh" 2>/dev/null || : "${SG_PS:=ps}"   # SG_PS (#1508)
# A suite that sources this file and then clears every SG_ name (`unset "${!SG_@}"`) would leave the scan with no reader, find
# nothing and leak its drivers, so the reader is kept under a name of its own.
DT_PS=$SG_PS
driver_tree_pids() {
  ${SG_PS:-$DT_PS} -axwwo pid=,ppid=,command= 2>/dev/null | DT_ROOT="$1" DT_SELF="$$" awk '
    { ppid[$1] = $2; line[$1] = $0 }
    END {
      for (p in line) if (index(line[p], ENVIRON["DT_ROOT"])) want[p] = 1
      do { grew = 0; for (p in ppid) if (!(p in want) && (ppid[p] in want)) { want[p] = 1; grew = 1 } } while (grew)
      delete want[ENVIRON["DT_SELF"]]
      for (p in want) print p
    }'
}

driver_tree_kill() {
  local i pids
  [ -n "$1" ] || return 0
  for i in $(seq 1 20); do
    pids=$(driver_tree_pids "$1"); [ -n "$pids" ] || return 0
    kill -TERM $pids 2>/dev/null; sleep 0.1
  done
  pids=$(driver_tree_pids "$1"); [ -n "$pids" ] && kill -KILL $pids 2>/dev/null
  return 0
}
