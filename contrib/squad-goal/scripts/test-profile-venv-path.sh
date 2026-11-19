#!/bin/bash
# seconds: 0.07
# A profile value a child or a gate echoes names the interpreter by its ABSOLUTE path.
#
# The `ac-verify` gate runs each declared command in the run's workspace, a git worktree cut from the base: it carries
# no `.venv` (untracked), and `squad/workcmd.clean_env` strips `VIRTUAL_ENV` and the orchestrator's venv from PATH on
# purpose. So a command spelled `.venv/bin/python` cannot resolve there and the gate fails a run whose code is green
# (#679: two wave runs failed ac-verify that way, both children approved).
#
# The values checked here are the ones a child can copy into `ac_commands` or that a gate can run. `SG_E2E_CMD` is
# excluded on purpose: its contract runs it from `$SG_E2E_DIR`, the checkout, where a relative path resolves.
# Offline: no gh, no network, no loop state.
# usage: bash test-profile-venv-path.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
trap 'rm -rf "$T"' EXIT

GUARDED="SG_CODING_RULES SG_SUITES_HINT SG_FULL_GATE_HINT SG_FAST_GATE_CMD SG_PREPUSH_CMD SG_FAST_CMD"
# a bare `.venv/bin/`: not preceded by a slash, so `/abs/path/.venv/bin/python` passes and `.venv/bin/python` does not
BARE='(^|[^/])\.venv/bin/'

# the values a profile exposes, one per line, taken in a subshell so sourcing changes nothing here
values() {
  bash -c 'set -a; SG_CHECKOUT=/checkout; . "$1" >/dev/null 2>&1 || exit 0
           for v in '"$GUARDED"'; do eval "printf \"%s\\t%s\\n\" \"\$v\" \"\${$v-}\""; done' _ "$1"
}

check() {   # check <label> <profile>: every guarded value of one profile is free of a bare `.venv/bin/`
  local bad=0 name val
  while IFS=$'\t' read -r name val; do
    [ -n "$val" ] || continue
    if printf '%s' "$val" | $G -Eq "$BARE"; then
      echo "  $name: $val" >&2
      bad=1
    fi
  done < <(values "$2")
  if [ "$bad" = 0 ]; then ok "$1"; else ko "$1 (a bare .venv/bin/ cannot resolve in the gate's worktree)"; fi
}

# every shipped profile is clean
n=0
for p in "$d"/profiles/*.sh; do
  [ -f "$p" ] || continue
  n=$((n + 1))
  check "profile $(basename "$p") names the absolute interpreter" "$p"
done
[ "$n" -gt 0 ] && ok "profiles found ($n)" || ko "no profile under $d/profiles"

# and the check can fail: a profile that tells a child the relative path is caught
mkdir -p "$T/profiles"
printf '%s\n' ': "${SG_CODING_RULES:=run tests with .venv/bin/python -m pytest -q}"' > "$T/profiles/relative.sh"
printf '%s\n' ': "${SG_CODING_RULES:=run tests with /checkout/.venv/bin/python -m pytest -q}"' > "$T/profiles/absolute.sh"
if values "$T/profiles/relative.sh" | $G -Eq "$BARE"; then ok "a relative interpreter is caught"
else ko "a relative interpreter was not caught"; fi
if values "$T/profiles/absolute.sh" | $G -Eq "$BARE"; then ko "an absolute interpreter was wrongly caught"
else ok "an absolute interpreter passes"; fi

# The profile this suite owns names the local gate of its own repository. `branchland.sh` falls back to `sg_base_gate`, the
# squad's own battery (`ruff check squad tests`, `mypy squad`), which cannot pass in a repository that has no `squad/`
# package: with CI off, every landing is refused for a command that never ran (#679, "Found 1 error." on two approved
# branches). Other profiles declare their own gates and are not this suite's to judge; `example.sh` is a template.
gate_of() { bash -c 'set -a; SG_CHECKOUT=/checkout; . "$1" >/dev/null 2>&1 || exit 0; printf "%s" "${SG_LOCAL_GATE_CMD-}"' _ "$1"; }
# A profile is machine config, not a repository file: the owner keeps `wz-zesty-agent.sh` beside the installed scripts, and this
# tree holds `example.sh` alone. A tree that carries no such profile is not this suite's to judge (ADR-0139), so the check runs
# only where the profile it reads is present.
pw="$d/profiles/wz-zesty-agent.sh"
if [ ! -f "$pw" ]; then ok "no wz-zesty-agent profile in this tree: its local gate is the profile's, not this suite's (ADR-0139)"
elif [ -n "$(gate_of "$pw")" ]; then ok "the profile names its local gate"
else ko "SG_LOCAL_GATE_CMD is unset (branchland would run the squad's own battery in this repository)"; fi

exit "$fail"
