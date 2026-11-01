#!/bin/bash
# A suite that starts a driver stops the driver's whole tree before it exits (#989), on a failed assertion, a timeout or a signal.
# Each suspect runs under a private TMPDIR, once to its end and once with a TERM sent while a driver of its copy is alive; a process
# whose command line still names that TMPDIR afterwards is a leaked driver. A suite that starts a driver kills the tree in its EXIT trap.
here="$(cd "$(dirname "$0")" && pwd)"; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
named() { GD="$1" /bin/ps -axwwo pid=,command= 2>/dev/null | GD="$1" awk 'index($0, ENVIRON["GD"]) { print $1 }'; }
settle() { for _ in $(seq 1 20); do [ -z "$(named "$1")" ] && return 0; sleep 0.1; done; }
G=$(mktemp -d); trap 'for p in $(named "$G"); do kill -KILL "$p" 2>/dev/null; done; settle "$G"; rm -rf "$G" 2>/dev/null' EXIT
mkdir -p "$G/bin"
printf '#!/bin/sh\n[ "$1" = -d ] && [ $# -eq 1 ] && exec /usr/bin/mktemp -d "$GUARD_ROOT/tmp.XXXXXX"\nexec /usr/bin/mktemp "$@"\n' > "$G/bin/mktemp"; chmod +x "$G/bin/mktemp"
DRIVERS="test-watchd-supervisor test-watchd-reload test-watchd-lock test-watchd-driver-sha test-watchd-token-stop test-janitor-watchd-orphan test-health test-live-guard test-token-guard"
SUSPECTS="$DRIVERS test-watchtail"
run() { local -a e; e=(env $(compgen -e | /usr/bin/grep -E '^(SG_|SERIAL_)' | sed 's/^/-u /') HOME="$2/home" TMPDIR="$2" GUARD_ROOT="$2" PATH="$G/bin:$PATH" bash "$here/$1.sh"); [ "$3" = exec ] && exec "${e[@]}" >/dev/null 2>&1; "${e[@]}" >/dev/null 2>&1; }

for s in $SUSPECTS; do
  r=$(mktemp -d "$G/r.XXXX"); mkdir -p "$r/home"
  run "$s" "$r"; settle "$r"
  left=$(named "$r")
  [ -z "$left" ] && ok "$s leaves no process alive when it exits" || { ko "$s leaves process(es) alive after it exits: $(echo $left)"; for p in $left; do kill -KILL "$p" 2>/dev/null; done; }
done

for s in $SUSPECTS; do
  r=$(mktemp -d "$G/k.XXXX"); mkdir -p "$r/home"
  run "$s" "$r" exec & pid=$!
  for _ in $(seq 1 100); do [ -n "$(named "$r")" ] && break; sleep 0.1; done
  kill -TERM "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; settle "$r"
  left=$(named "$r")
  [ -z "$left" ] && ok "$s leaves no process alive when it is killed mid-run" || { ko "$s leaves process(es) alive after a kill mid-run: $(echo $left)"; for p in $left; do kill -KILL "$p" 2>/dev/null; done; }
done

for s in $DRIVERS; do
  /usr/bin/grep -h 'trap .*EXIT' "$here/$s.sh" | /usr/bin/grep -q 'driver_tree_kill' && ok "$s kills its driver tree in its EXIT trap" || ko "$s has no EXIT trap that kills its driver tree"
done
exit $fail
