# Shared settings for squad-goal scripts. Override any of them in the environment.
# A profile (scripts/profiles/<name>.sh) supplies the per-repo defaults: repo, board, base branch,
# merge method, e2e command, review and coding rules. Pick one with SG_PROFILE, else it is matched
# from the origin remote of the current checkout. Add a repo by copying a profile.
# One profile is one repository. SG_TOOLING_REPO and SG_FORK_DIR, the second repository a profile could name, are retired (#20):
# they map into SG_REPOS, draw one named warning per state directory, and never stop the run.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$here/repoctx.sh"   # the one resolution of a repository: profiles dir, origin match, state directory (#20)
[ -n "${SG_PROFILE:-}" ] || SG_PROFILE=$(sg_profile_from_origin .) || SG_PROFILE=""
: "${SG_PROFILE:?no squad-goal profile matched; set SG_PROFILE or add scripts/profiles/<name>.sh}"
. "$(sg_profiles_dir)/$SG_PROFILE.sh"
if [ -z "${SG_SQUAD:-}" ]; then
  SG_SQUAD=$(python3 -I -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['agent-squad@agent-squad'][0]['installPath'])")
fi
# One state directory per repository (#20): markers, locks, active-runs.txt, auto/, cq/, tests, alerts, watch.log and
# health.json of one profile never share a namespace with another's. SG_STATE set in the environment still wins.
: "${SG_STATE_BASE:=$HOME/.cache/squad-goal}"
: "${SG_STATE:=$SG_STATE_BASE/$SG_PROFILE}"
# What stays machine-wide (provider quota markers, the board cache) lives in one shared directory, never in a repository's state.
: "${SG_SHARED:=$SG_STATE_BASE/_shared}"
SG_RETIRED=$(sg_retired_set); SG_REPOS=$(sg_repo_list)
[ -z "$SG_RETIRED" ] || sg_warn_retired "$SG_PROFILE" "$SG_STATE" $SG_RETIRED
: "${SG_REVIEW_MODEL:=gpt-5.6-terra}"
export SG_SHARED SG_REPOS SG_STATE_BASE SG_PROFILE SG_REPO SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE SG_MERGE_METHOD SG_AUTO_MERGE SG_CI_WORKFLOW SG_E2E_DIR SG_E2E_CMD SG_E2E_PASS_REGEX SG_WORKFLOW SG_ROOT_NAME SG_REVIEW_RULES SG_CODING_RULES SG_SQUAD SG_STATE SG_AUTO_BUGFIX SG_BUG_LABEL SG_BUG_SKIP_LABELS SG_BUG_PROJECT SG_BUG_DOING SG_FRONTIER_LABEL SG_FRONTIER_PREAMBLE SG_FORK_DIR SG_FORK_BASE SG_FORK_BRANCH SG_FORK_TEST_CMD SG_E2E_BUILD_DIR SG_E2E_BUILD_CMD
BOARD="$here/board.sh"   # board-status.sh + a hub refresh flag
mkdir -p "$SG_STATE/cq" "$SG_STATE/req" "$SG_SHARED"
touch "$SG_STATE/active-runs.txt"
