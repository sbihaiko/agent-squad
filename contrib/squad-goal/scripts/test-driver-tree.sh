#!/bin/bash
# seconds: 84.36
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

# The trap reaches the kill through the functions it names: a suite that stops its drivers in `stopall` and calls `stopall`
# from its EXIT trap kills the tree just the same, so the guard resolves every name the trap runs to the functions the suite
# defines, transitively, and asks whether driver_tree_kill is among them (ADR-0018: the guard proves the reach, not the
# spelling of one line). A suite with no EXIT trap at all still fails.
trap_kills_tree() {
  python3 - "$1" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
fn, cur = {}, None   # a function's body runs from its `name() {` to the closing `}` at column 0, or to its one-line end
for line in src.splitlines():
    if cur is None:
        m = re.match(r"^([A-Za-z_]\w*)\(\)\s*\{", line)
        if m:
            cur, fn[cur] = m.group(1), set()
            rest = line[m.end():]
            fn[cur].update(re.findall(r"[A-Za-z_]\w*", rest))
            if rest.rstrip().endswith("}"): cur = None
    elif line.startswith("}"):
        cur = None
    else:
        fn[cur].update(re.findall(r"[A-Za-z_]\w*", line))
traps = [b for _, b in re.findall(r'\btrap\s+(["\'])(.*?)\1\s+EXIT', src, re.S)]
def reaches(name, seen=()):
    if name == "driver_tree_kill": return True
    if name in seen or name not in fn: return False
    return any(reaches(n, seen + (name,)) for n in fn[name])
sys.exit(0 if any(reaches(w) for t in traps for w in set(re.findall(r"[A-Za-z_]\w*", t))) else 1)
PY
}
for s in $DRIVERS; do
  trap_kills_tree "$here/$s.sh" && ok "$s kills its driver tree in its EXIT trap" || ko "$s has no EXIT trap that kills its driver tree"
done
exit $fail
