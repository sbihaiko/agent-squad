# Shared settings for squad-goal scripts. Override any of them in the environment.
# A profile (scripts/profiles/<name>.sh) supplies the per-repo defaults: repo, board, base branch,
# merge method, e2e command, review and coding rules. Pick one with SG_PROFILE, else the seam (address.sh) resolves the
# checkout the script was started in, as it resolves any checkout. Add a repo by copying a profile.
# A profile the seam does not know stops here, naming it, so no script reaches GitHub with no repository resolved.
# A profile works a list of repositories (SG_REPOS, the primary first). SG_TOOLING_REPO, the second repository a profile
# could name, is retired (#20): a profile that still sets it keeps working. It is mapped into SG_REPOS and one warning per
# state directory names the profile, the setting and its replacement. SG_REQUIRED_SETTINGS is the contract in one place
# (`<setting>:<the setting that engages it>`; the retired form is `<setting>:<engaging setting>:retired:<replacement>`).
# env.sh resolves itself with BASH_SOURCE and the seam uses bash builtins, so another shell is refused here, by name (#761).
[ -n "${BASH_VERSION:-}" ] || { echo "env.sh: source it from bash (bash -c '. env.sh; ...'), not from ${ZSH_NAME:-this shell}" >&2; return 2 2>/dev/null || exit 2; }
SG_REQUIRED_SETTINGS="SG_TOOLING_REPO:SG_FORK_DIR:retired:SG_REPOS"
# (#2798, ADR-0051) A git network call has a deadline: a transfer under GIT_HTTP_LOW_SPEED_LIMIT bytes/s for
# SG_GIT_STALL_SECONDS fails, so a fetch or push from a GitHub that stopped answering ends and its pass releases its lock.
# The bound reads the transfer itself (a stall), never a total time, so a slow but moving fetch still finishes (wtclean.sh).
export GIT_HTTP_LOW_SPEED_LIMIT=${GIT_HTTP_LOW_SPEED_LIMIT:-1000} GIT_HTTP_LOW_SPEED_TIME=${GIT_HTTP_LOW_SPEED_TIME:-${SG_GIT_STALL_SECONDS:-60}}
export SG_MOVE_BY=loop   # a card move a loop script makes carries its dispatch context in the board record (#861)
# A tool's absolute path differs by platform: /usr/sbin/lsof holds on macOS and is absent on the CI runner, where a
# hardcoded path turns a missing tool into a failed pass. One resolution here, and every script reads it from SG_LSOF.
SG_LSOF="${SG_LSOF:-}"; [ -n "$SG_LSOF" ] || SG_LSOF="$(command -v lsof 2>/dev/null)"
for _l in /usr/sbin/lsof /usr/bin/lsof; do [ -n "$SG_LSOF" ] || { [ -x "$_l" ] && SG_LSOF="$_l"; }; done
unset _l
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$here/address.sh"   # the one resolution of a repository: profiles dir, checkout and origin match, state directory
SG_PROFILES_DIR="$(sg_profiles_dir)"; export SG_PROFILES_DIR   # a session a run starts carries it, so the worktree's own prepush resolves the loop's profile (#664)
[ -n "${SG_PROFILE:-}" ] || SG_PROFILE=$(repoctx "$PWD" 2>/dev/null | sed -n 's/^SG_PROFILE=//p')
: "${SG_PROFILE:?no squad-goal profile matched; set SG_PROFILE or add scripts/profiles/<name>.sh}"
_sg_ctx=$(repoctx "$SG_PROFILE") || exit 2
. "$(sg_profiles_dir)/$SG_PROFILE.sh"
# The defaults below are read after the profile, so a profile's := default wins (#757).
# The labels no dispatch pass takes as work are one set (skiplabels.sh, #1593), sourced here so the frontier, the bug and the
# health passes read the same file; its two lane defaults live in skiplabels.sh, not here.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skiplabels.sh"
# The local gates a child runs in its own worktree before it enters the wave branch (wave.sh): lint, types, the fast
# battery and the shell suites of the scripts the child changes (fast-gate.sh). A profile that works another repository overrides it, and an empty value refuses the entry, so no child ever
# enters a wave ungated (ADR-0145 rule 9).
: "${SG_WAVE_GATES:=bash '$here/fast-gate.sh'}"
# The wave entry's own battery (wave.sh, #1800): `off`, the default (#1802), admits the child after its clean rebase with no
# battery, and the entry records `skipped`; the wave's landing gate is the one batch gate that proves every child (#1797).
# `on` runs the battery when the entry finds no reusable green verdict. A reuse hit enters as before.
: "${SG_WAVE_ENTRY_GATE:=off}"
# The gate slots a wave child gate takes (wave.sh, #2594). A child gate is a delta over one child's change, so one slot fits
# it; at two, two waves held the whole budget of `caps.gates` and a third entry gate ended unproven for "budget is full".
: "${SG_WAVE_GATE_WORKERS:=1}"
# The landing passes that run at once (watch.sh, #2607, ADR-0176): two waves rebase, check and push together, and git refuses
# the push that is not a fast-forward, so the refused pass repeats from its fetch. One is the behavior before the setting.
: "${SG_LAND_PARALLEL:=2}"
# The gate slots a wave tip check takes (wave.sh, #2604). One is the default: a tip check is one gate whose suite pool carries
# its parallelism, and at the profile's whole count a long branchland gate left the wave no slot inside its 900 s wait.
: "${SG_TIP_GATE_SLOTS:=1}"
# The review a loop run gives each passed child before it merges it (#601, ADR-0145 rule 9): the loop workflow's review gate
# runs the script this names, and with none set (a run outside the loop) the gate answers "no review here".
: "${SG_RUN_REVIEW:=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/runreview.sh}"
if [ -z "${SG_SQUAD:-}" ]; then
  SG_SQUAD=$(python3 -I -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['agent-squad@agent-squad'][0]['installPath'])")
fi
# One state directory per repository (#20, #25): markers, locks, active-runs.txt, auto/, req/, cq/, tests, alerts,
# watch.log and health.json of one profile never share a namespace with another's. What must not split comes from the
# same seam: SG_SHARED (provider limit markers, quota cache) and SG_BOARD_CACHE (one per owner and project).
# SG_STATE, SG_SHARED or SG_BOARD_CACHE set in the environment still wins.
eval "$(printf '%s\n' "$_sg_ctx" | /usr/bin/grep -E '^(SG_STATE|SG_SHARED|SG_BOARD_CACHE)=' | sed 's/^/_ctx_/')"; unset _sg_ctx
: "${SG_STATE:=$_ctx_SG_STATE}" "${SG_SHARED:=$_ctx_SG_SHARED}" "${SG_BOARD_CACHE:=$_ctx_SG_BOARD_CACHE}"
: "${SG_REVIEW_MODEL:=gpt-5.6-terra}"
# Seconds between two beats of the fast lane (fastloop.sh), 120 unless the profile sets SG_FAST_INTERVAL. The slow cycle keeps its own beat.
: "${SG_FAST_INTERVAL:=120}"
# The watch cycle closes with one wait of SG_WATCH_WAIT seconds at most, polled every SG_WATCH_POLL seconds; a run verdict ends it early (watch.sh). A fraction is fine.
: "${SG_WATCH_WAIT:=90}" "${SG_WATCH_POLL:=3}"
# A wave has an age cap (#378, ADR-0145 rule 13): a wave past it lands the children it holds, and the children of the run
# that never entered go back to the queue. Two hours by default. It is read here, after the profile, so a profile that sets
# SG_WAVE_MAX_AGE_SECONDS wins. The wave tip's own check (#377) is SG_WAVE_TIP_GATES, built in wave.sh (a profile may set
# it, and an empty value leaves the wave's CI as its only check).
: "${SG_WAVE_MAX_AGE_SECONDS:=7200}"
# A wave also closes early when its oldest entry waited past a limit (#2316, ADR-0145 rule 16): it lands with the children
# that entered it, and the rest of the run enters a later wave cut from the moved base. Thirty minutes by default; a guess,
# not a measured value. Read here, after the profile, so a profile that sets SG_WAVE_ENTRY_WAIT_SECONDS wins.
: "${SG_WAVE_ENTRY_WAIT_SECONDS:=1800}"
# The promotion proof promote-stg.sh reads (ADR-0145): `ci` (the default) takes the dev tip's CI, and `local` takes the promotion
# gate's own status line, the full suite in both modes (owner decision on #439). Set `local` in the profile while the account's
# Actions billing keeps CI from starting; a tip with no proof line is not promoted. It is read here, after the profile, so a
# profile that names it wins (#439).
: "${SG_PROMOTE_PROOF:=ci}"
# The branch a wave lands in (`SG_BASE`) and the branch the promotion merges into (`SG_STG_BRANCH`, `stg` unless a profile
# names another): both scripts read these, never a literal (#1646). `SG_PROMOTE=off` turns the promotion pass off for a
# profile that delivers by another path: the pass exits 0 before any fetch, so no log line grows on every cycle.
: "${SG_BASE:=dev}" "${SG_STG_BRANCH:=stg}" "${SG_MAIN_BRANCH:=main}" "${SG_PROMOTE:=on}"
# The worker count the base battery's pytest runs with (base-battery.sh): six fits a 16 GB machine, where `auto` takes every
# core and a gate beside a second one saturates it. Read here, after the profile, so a profile that sets it wins.
: "${SG_GATE_WORKERS:=6}"
# A proved board-door move is drawn at once, marked pending, until a list read agrees (#650). It may stand no longer than
# SG_PENDING_WINDOW_SECONDS (120 s): an older entry is dropped, the card returns to the read's column, and the loop's log
# names the disagreement once (#651). hub.py validates the window and warns on stderr when the value is not a
# non-negative number. SG_PENDING_OVERLAY=off serves the list read alone, so a suspected overlay defect is ruled out
# without a code change.
: "${SG_PENDING_WINDOW_SECONDS:=120}"
: "${SG_PENDING_OVERLAY:=on}"
# The board vocabulary the loop writes (#760): board_define.py makes a board with the shipped vocabulary, so `shipped` is the
# default. Read here, after the profile, so a profile on an old board sets another value (`legacy`) and keeps it.
: "${SG_BOARD_VOCAB:=shipped}"
sg_gate_cmds   # (#740) the gate commands a request names: the profile's, or none where the checkout lacks agent-squad's scripts
# The retired SG_TOOLING_REPO is one more entry of SG_REPOS, and the key is not read again (the seam lists it once).
if [ -n "${SG_TOOLING_REPO:-}" ]; then
  SG_REPOS=$(sg_repos | tr '\n' ' '); SG_REPOS=${SG_REPOS% }
  mkdir -p "$SG_STATE"
  [ -e "$SG_STATE/warned-SG_TOOLING_REPO" ] || { touch "$SG_STATE/warned-SG_TOOLING_REPO"
    echo "env.sh: profile $SG_PROFILE sets SG_TOOLING_REPO, which is retired: it is mapped into SG_REPOS for now; name $SG_TOOLING_REPO in SG_REPOS and remove SG_TOOLING_REPO from the profile" >&2; }
  unset SG_TOOLING_REPO
fi
export SG_STG_BRANCH SG_MAIN_BRANCH SG_PROMOTE
export SG_LSOF SG_FRONTIER_SKIP_LABELS SG_WAVE_GATES SG_WAVE_ENTRY_GATE SG_WAVE_GATE_WORKERS SG_LAND_PARALLEL SG_TIP_GATE_SLOTS SG_TIP_GATE_SLOTS SG_RUN_REVIEW SG_WAVE_MAX_AGE_SECONDS SG_WAVE_ENTRY_WAIT_SECONDS SG_PROMOTE_PROOF SG_GATE_WORKERS SG_PROFILE SG_SHARED SG_BOARD_CACHE SG_REPO SG_REPOS SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE SG_MERGE_METHOD SG_AUTO_MERGE SG_CI_WORKFLOW SG_E2E_DIR SG_E2E_CMD SG_E2E_PASS_REGEX SG_WORKFLOW SG_ROOT_NAME SG_REVIEW_RULES SG_CODING_RULES SG_SQUAD SG_STATE SG_AUTO_BUGFIX SG_BUG_LABEL SG_BUG_SKIP_LABELS SG_BUG_PROJECT SG_BUG_DOING SG_FRONTIER_LABEL SG_FRONTIER_PREAMBLE SG_FORK_DIR SG_FORK_BASE SG_FORK_BRANCH SG_FORK_TEST_CMD SG_E2E_BUILD_DIR SG_E2E_BUILD_CMD SG_FAST_INTERVAL SG_PENDING_WINDOW_SECONDS SG_PENDING_OVERLAY SG_BOARD_VOCAB
BOARD="$here/board.sh"   # board-status.sh + a hub refresh flag
mkdir -p "$SG_STATE/cq" "$SG_STATE/req" "$SG_SHARED" "$(dirname "$SG_BOARD_CACHE")"
touch "$SG_STATE/active-runs.txt"
