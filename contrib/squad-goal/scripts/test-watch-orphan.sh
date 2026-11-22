#!/bin/bash
# seconds: 1.02
# The cycle lane exits by itself once its supervisor is gone (#959): an orphaned watch.sh runs no further step and leaves its
# lock, so an orphaned loop never keeps dispatching beside a supervisor that started another one.
# Offline: a copy of the real watch.sh in a temp scripts dir, every step stubbed to log its name, and a `gh` that answers nothing.
# No process table is read (#2781): a child's sandbox refuses `ps`, so the suite proves the orphaning by the supervisor pid it
# recorded, and each stub decides at its own start that it ran with no supervisor. The fixture carries no caps.sh, so the lane
# runs the fallback orphan proof, the one a reader-less copy needs.
# usage: bash test-watch-orphan.sh [path/to/watch.sh]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/watch.sh}; T=$(mktemp -d); S=$T/scripts; ST=$T/st; B=$T/bin
# A source not named watch.sh is refused (#2025): a suite given as the source copied itself in and ran itself at every level.
case "$(basename "$src")" in watch.sh) ;; *) echo "$(basename "$0"): $src is not a watch.sh; refused" >&2; exit 2;; esac
mkdir -p "$S" "$ST" "$B" "$T/co" "$T/fork"; fail=0; lp=
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export STEPS=$T/steps.log LATE=$T/late.log SUP=$T/sup.pid STARTED=$T/started
trap '[ -n "$lp" ] && kill -9 "$lp" 2>/dev/null; rm -rf "$T"' EXIT
cp "$src" "$S/watch.sh"; cp "$d/single.sh" "$S/" 2>/dev/null; cp "$d/models.json" "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$d/drainpolicy.py" "$S/"
cat > "$S/env.sh" <<EOF
export SG_STATE=$ST SG_STATE_BASE=$T/cache SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_CHECKOUT=$T/co SG_FORK_DIR=$T/fork
export SG_WATCH_WAIT=3 SG_WATCH_POLL=1 SG_GH_AGGREGATE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001
export LANE_SUP=\$PPID   # the lane's own supervisor, read where the lane starts, so a step names the supervisor it ran under
EOF
printf '#!/bin/bash\nshift; "$@"\n' > "$S/single.sh"
# A step logs its name, and logs it to $LATE as well when its own start finds its lane's supervisor gone: that is a step that
# ran once its lane was orphaned, measured at the step, never at a clock read of the suite. A step a killed lane left behind
# names that lane's supervisor, which still runs, so it is never read as a late step of another lane.
for s in token-guard.sh wave.sh consult.sh basecheckpass.sh branchland.sh branchreviewpass.sh branchfixpass.sh autobug.sh autofrontier.sh autohunt.sh heartbeat.sh wtclean.sh janitor.sh live-guard.sh vendor-guard.sh nightly.sh; do
  printf '#!/bin/bash\necho %s >> "$STEPS"\n[ -n "$LANE_SUP" ] && ! kill -0 "$LANE_SUP" 2>/dev/null && echo %s >> "$LATE"\n' "$s" "$s" > "$S/$s"; done
# The first step of a cycle waits for the suite's release, under the second lane only: the supervisor dies while the lane is
# inside a step, so no step launch is in flight at the orphan instant. Every later step is then one the lane decides to start
# with its supervisor already gone.
export RELEASE=$T/release
printf '#!/bin/bash\necho token-guard.sh >> "$STEPS"\n[ -n "$LANE_SUP" ] && ! kill -0 "$LANE_SUP" 2>/dev/null && echo token-guard.sh >> "$LATE"\n[ -n "$LANE_SUP" ] && [ "$LANE_SUP" = "$(cat "$SUP" 2>/dev/null)" ] && { : > "$STARTED"; while [ ! -e "$RELEASE" ]; do sleep 0.02; done; }\n' > "$S/token-guard.sh"
printf '#!/bin/bash\nexit 0\n' > "$B/gh"; chmod +x "$S"/*.sh "$B/gh"; export PATH="$B:$PATH"
: > "$ST/active-runs.txt"
waitfor() { for _ in $(seq 1 60); do eval "$1" && return 0; sleep 0.2; done; return 1; }

bash "$S/watch.sh" >"$T/sup.out" 2>&1 & sup=$!
waitfor '[ -s "$STEPS" ]' && ok "a lane under a live supervisor runs its steps" || ko "a lane under a live supervisor ran no step"
kill -TERM "$sup" 2>/dev/null; wait "$sup" 2>/dev/null
: > "$STEPS"; : > "$LATE"

# The lane's supervisor records both pids and stays alive until the lane's first step starts under it: the orphan instant is
# measured from that step, never a fixed delay. When it returns, the supervisor is gone and the lane is orphaned.
bash -c 'printf "%s\n" $$ > "$2"
  bash "$1" >/dev/null 2>&1 & printf "%s\n" $! > "$3"
  for _ in $(seq 1 750); do [ -e "$4" ] && break; sleep 0.02; done' _ "$S/watch.sh" "$SUP" "$T/lane.pid" "$STARTED"
lp=$(cat "$T/lane.pid" 2>/dev/null)
waitfor '! kill -0 "$(cat "$SUP")" 2>/dev/null' && ok "the cycle lane is orphaned" || ko "the cycle lane was not orphaned"
[ -e "$STARTED" ] && ok "the orphaned lane ran a step under its supervisor before the supervisor died" || ko "the orphaned lane ran no step under a supervisor"
: > "$RELEASE"   # the step that held the lane returns: every later step is one the lane starts while orphaned
for _ in $(seq 1 60); do kill -0 "$lp" 2>/dev/null || break; sleep 0.2; done
kill -0 "$lp" 2>/dev/null && ko "the orphaned cycle lane still runs" || ok "an orphaned cycle lane exits on its own"
[ ! -s "$LATE" ] && ok "no step starts once the lane is orphaned" || ko "an orphaned lane ran: $(tr '\n' ' ' < "$LATE")"
[ ! -e "$ST/locks/watch" ] && ok "the orphaned lane leaves no watch lock" || ko "the orphaned lane left its lock"

[ $fail = 0 ] && echo "test-watch-orphan: all checks green" || { echo "test-watch-orphan: failed"; exit 1; }
