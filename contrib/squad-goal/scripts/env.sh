# Shared settings for squad-goal scripts. Override any of them in the environment.
# A profile (scripts/profiles/<name>.sh) supplies the per-repo defaults: repo, board, base branch,
# merge method, e2e command, review and coding rules. Pick one with SG_PROFILE, else the seam (address.sh) resolves the
# checkout the script was started in, as it resolves any checkout. Add a repo by copying a profile.
# A profile the seam does not know stops here, naming it, so no script reaches GitHub with no repository resolved.
# A profile works a list of repositories (SG_REPOS, the primary first). SG_TOOLING_REPO, the second repository a profile
# could name, is retired (#20): a profile that still sets it keeps working. It is mapped into SG_REPOS and one warning per
# state directory names the profile, the setting and its replacement. SG_REQUIRED_SETTINGS is the contract in one place
# (`<setting>:<the setting that engages it>`; the retired form is `<setting>:<engaging setting>:retired:<replacement>`).
SG_REQUIRED_SETTINGS="SG_TOOLING_REPO:SG_FORK_DIR:retired:SG_REPOS"
# A spec is a document until the owner starts it, and a freeze marker holds the card without changing its column:
# the dispatch pass never takes one of these labels as work (SG_FRONTIER_SKIP_LABELS, beside the bug label it already skips).
: "${SG_FRONTIER_SKIP_LABELS:=spec,needs-owner,needs-ratification,needs-info,frozen:owner,frozen:dependency,wontfix}"
# The local gates a child runs in its own worktree before it enters the wave branch (wave.sh): lint, types, the fast
# battery and the shell suites of the scripts the child changes (fast-gate.sh). A profile that works another repository overrides it, and an empty value refuses the entry, so no child ever
# enters a wave ungated (ADR-0145 rule 9).
: "${SG_WAVE_GATES:=bash '$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/fast-gate.sh'}"
# The promotion proof promote-stg.sh reads (ADR-0145): `ci` (the default) takes the dev tip's CI, and `local` takes the promotion
# gate's own status line, the full suite in both modes (owner decision on #439). Set `local` in the profile while the account's
# Actions billing keeps CI from starting; a tip with no proof line is not promoted.
: "${SG_PROMOTE_PROOF:=ci}"
# A tool's absolute path differs by platform: /usr/sbin/lsof holds on macOS and is absent on the CI runner, where a
# hardcoded path turns a missing tool into a failed pass. One resolution here, and every script reads it from SG_LSOF.
SG_LSOF="${SG_LSOF:-}"; [ -n "$SG_LSOF" ] || SG_LSOF="$(command -v lsof 2>/dev/null)"
for _l in /usr/sbin/lsof /usr/bin/lsof; do [ -n "$SG_LSOF" ] || { [ -x "$_l" ] && SG_LSOF="$_l"; }; done
unset _l
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$here/address.sh"   # the one resolution of a repository: profiles dir, checkout and origin match, state directory
[ -n "${SG_PROFILE:-}" ] || SG_PROFILE=$(repoctx "$PWD" 2>/dev/null | sed -n 's/^SG_PROFILE=//p')
: "${SG_PROFILE:?no squad-goal profile matched; set SG_PROFILE or add scripts/profiles/<name>.sh}"
_sg_ctx=$(repoctx "$SG_PROFILE") || exit 2
. "$(sg_profiles_dir)/$SG_PROFILE.sh"
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
# A wave has an age cap (#378, ADR-0145 rule 13): a wave past it lands the children it holds, and the children of the run
# that never entered go back to the queue. Two hours by default. It is read here, after the profile, so a profile that sets
# SG_WAVE_MAX_AGE_SECONDS wins. The wave tip's own check (#377) is SG_WAVE_TIP_GATES, built in wave.sh (a profile may set
# it, and an empty value leaves the wave's CI as its only check).
: "${SG_WAVE_MAX_AGE_SECONDS:=7200}"
# The retired SG_TOOLING_REPO is one more entry of SG_REPOS, and the key is not read again (the seam lists it once).
if [ -n "${SG_TOOLING_REPO:-}" ]; then
  SG_REPOS=$(sg_repos | tr '\n' ' '); SG_REPOS=${SG_REPOS% }
  mkdir -p "$SG_STATE"
  [ -e "$SG_STATE/warned-SG_TOOLING_REPO" ] || { touch "$SG_STATE/warned-SG_TOOLING_REPO"
    echo "env.sh: profile $SG_PROFILE sets SG_TOOLING_REPO, which is retired: it is mapped into SG_REPOS for now; name $SG_TOOLING_REPO in SG_REPOS and remove SG_TOOLING_REPO from the profile" >&2; }
  unset SG_TOOLING_REPO
fi
export SG_LSOF SG_FRONTIER_SKIP_LABELS SG_WAVE_GATES SG_WAVE_MAX_AGE_SECONDS SG_PROMOTE_PROOF SG_PROFILE SG_SHARED SG_BOARD_CACHE SG_REPO SG_REPOS SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE SG_MERGE_METHOD SG_AUTO_MERGE SG_CI_WORKFLOW SG_E2E_DIR SG_E2E_CMD SG_E2E_PASS_REGEX SG_WORKFLOW SG_ROOT_NAME SG_REVIEW_RULES SG_CODING_RULES SG_SQUAD SG_STATE SG_AUTO_BUGFIX SG_BUG_LABEL SG_BUG_SKIP_LABELS SG_BUG_PROJECT SG_BUG_DOING SG_FRONTIER_LABEL SG_FRONTIER_PREAMBLE SG_FORK_DIR SG_FORK_BASE SG_FORK_BRANCH SG_FORK_TEST_CMD SG_E2E_BUILD_DIR SG_E2E_BUILD_CMD SG_FAST_INTERVAL
BOARD="$here/board.sh"   # board-status.sh + a hub refresh flag
mkdir -p "$SG_STATE/cq" "$SG_STATE/req" "$SG_SHARED" "$(dirname "$SG_BOARD_CACHE")"
touch "$SG_STATE/active-runs.txt"
