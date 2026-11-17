#!/bin/bash
# The cycle lane exits by itself once its supervisor is gone (#959): an orphaned watch.sh runs no further step and leaves its
# lock, so an orphaned loop never keeps dispatching beside a supervisor that started another one.
# Offline: a copy of the real watch.sh in a temp scripts dir, every step stubbed to log its name, and a `gh` that answers nothing.
# usage: bash test-watch-orphan.sh [path/to/watch.sh]
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/watch.sh}; T=$(mktemp -d); S=$T/scripts; ST=$T/st; B=$T/bin
# A source not named watch.sh is refused (#2025): a suite given as the source copied itself in and ran itself at every level.
case "$(basename "$src")" in watch.sh) ;; *) echo "$(basename "$0"): $src is not a watch.sh; refused" >&2; exit 2;; esac
mkdir -p "$S" "$ST" "$B" "$T/co" "$T/fork"; fail=0; lp=
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export STEPS=$T/steps.log
trap '[ -n "$lp" ] && kill -9 "$lp" 2>/dev/null; rm -rf "$T"' EXIT
cp "$src" "$S/watch.sh"; cp "$d/single.sh" "$S/" 2>/dev/null; cp "$d/models.json" "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$d/drainpolicy.py" "$S/"
cat > "$S/env.sh" <<EOF
export SG_STATE=$ST SG_STATE_BASE=$T/cache SG_REPO=o/r SG_OWNER=o SG_PROJECT=1 SG_CHECKOUT=$T/co SG_FORK_DIR=$T/fork
export SG_WATCH_WAIT=3 SG_WATCH_POLL=1 SG_GH_AGGREGATE_WINDOW=0 SG_GH_COOLDOWN=0.001 SG_GH_COOLDOWN_JITTER=0.001
EOF
printf '#!/bin/bash\nshift; "$@"\n' > "$S/single.sh"
for s in token-guard.sh wave.sh consult.sh basecheckpass.sh branchland.sh branchreviewpass.sh branchfixpass.sh autobug.sh autofrontier.sh autohunt.sh heartbeat.sh wtclean.sh janitor.sh live-guard.sh vendor-guard.sh nightly.sh; do
  printf '#!/bin/bash\necho %s >> "$STEPS"\n' "$s" > "$S/$s"; done
printf '#!/bin/bash\nexit 0\n' > "$B/gh"; chmod +x "$S"/*.sh "$B/gh"; export PATH="$B:$PATH"
: > "$ST/active-runs.txt"
waitfor() { for _ in $(seq 1 60); do eval "$1" && return 0; sleep 0.2; done; return 1; }
ppid_of() { ps -o ppid= -p "$1" 2>/dev/null | tr -d ' '; }

bash "$S/watch.sh" >"$T/sup.out" 2>&1 & sup=$!
waitfor '[ -s "$STEPS" ]' && ok "a lane under a live supervisor runs its steps" || ko "a lane under a live supervisor ran no step"
kill -TERM "$sup" 2>/dev/null; wait "$sup" 2>/dev/null

lp=$(bash -c 'bash "$1" >/dev/null 2>&1 & echo $!; sleep 0.3' _ "$S/watch.sh")
waitfor '[ "$(ppid_of "$lp")" = 1 ]' && ok "the cycle lane is orphaned" || ko "the cycle lane was not orphaned"
: > "$STEPS"
for _ in $(seq 1 60); do kill -0 "$lp" 2>/dev/null || break; sleep 0.2; done
kill -0 "$lp" 2>/dev/null && ko "the orphaned cycle lane still runs" || ok "an orphaned cycle lane exits on its own"
[ ! -s "$STEPS" ] && ok "no step runs once the lane is orphaned" || ko "an orphaned lane ran: $(tr '\n' ' ' < "$STEPS")"
[ ! -e "$ST/locks/watch" ] && ok "the orphaned lane leaves no watch lock" || ko "the orphaned lane left its lock"

[ $fail = 0 ] && echo "test-watch-orphan: all checks green" || { echo "test-watch-orphan: failed"; exit 1; }
