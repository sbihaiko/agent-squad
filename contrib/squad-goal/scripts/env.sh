# Shared settings for squad-goal scripts. Override any of them in the environment.
# A profile (scripts/profiles/<name>.sh) supplies the per-repo defaults: repo, board, base branch,
# merge method, e2e command, review and coding rules. Pick one with SG_PROFILE, else the seam (address.sh) resolves the
# checkout the script was started in, as it resolves any checkout. Add a repo by copying a profile.
# A profile the seam does not know stops here, naming it, so no script reaches GitHub with no repository resolved.
# One profile is one repository. SG_TOOLING_REPO, the second repository a profile could name, is retired (#20): a profile
# that sets it stops here, naming it; give that repository its own profile instead.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$here/address.sh"   # the one resolution of a repository: profiles dir, checkout and origin match, state directory
[ -n "${SG_PROFILE:-}" ] || SG_PROFILE=$(repoctx "$PWD" 2>/dev/null | sed -n 's/^SG_PROFILE=//p')
: "${SG_PROFILE:?no squad-goal profile matched; set SG_PROFILE or add scripts/profiles/<name>.sh}"
repoctx "$SG_PROFILE" >/dev/null || exit 2
. "$(sg_profiles_dir)/$SG_PROFILE.sh"
[ -z "${SG_TOOLING_REPO:-}" ] || { echo "env.sh: SG_TOOLING_REPO is set (by profile $SG_PROFILE or the environment) and is retired: unset it and give $SG_TOOLING_REPO its own profile" >&2; exit 2; }
if [ -z "${SG_SQUAD:-}" ]; then
  SG_SQUAD=$(python3 -I -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['agent-squad@agent-squad'][0]['installPath'])")
fi
# One state directory per repository (#20): markers, locks, active-runs.txt, auto/, cq/, tests, alerts, watch.log and
# health.json of one profile never share a namespace with another's. SG_STATE set in the environment still wins.
: "${SG_STATE_BASE:=$HOME/.cache/squad-goal}"
: "${SG_STATE:=$SG_STATE_BASE/$SG_PROFILE}"
: "${SG_REVIEW_MODEL:=gpt-5.6-terra}"
export SG_STATE_BASE SG_PROFILE SG_REPO SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE SG_MERGE_METHOD SG_AUTO_MERGE SG_CI_WORKFLOW SG_E2E_DIR SG_E2E_CMD SG_E2E_PASS_REGEX SG_WORKFLOW SG_ROOT_NAME SG_REVIEW_RULES SG_CODING_RULES SG_SQUAD SG_STATE SG_AUTO_BUGFIX SG_BUG_LABEL SG_BUG_SKIP_LABELS SG_BUG_PROJECT SG_BUG_DOING SG_FRONTIER_LABEL SG_FRONTIER_PREAMBLE SG_FORK_DIR SG_FORK_BASE SG_FORK_BRANCH SG_FORK_TEST_CMD SG_E2E_BUILD_DIR SG_E2E_BUILD_CMD
BOARD="$here/board.sh"   # board-status.sh + a hub refresh flag
mkdir -p "$SG_STATE/cq" "$SG_STATE/req"
touch "$SG_STATE/active-runs.txt"
