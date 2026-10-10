# Shared settings for squad-goal scripts. Override any of them in the environment.
# A profile (scripts/profiles/<name>.sh) supplies the per-repo defaults: repo, board, base branch,
# merge method, e2e command, review and coding rules. Pick one with SG_PROFILE, else it is matched
# from the origin remote of the current checkout. Add a repo by copying a profile.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "${SG_PROFILE:-}" ]; then
  url=$(git remote get-url origin 2>/dev/null | tr A-Z a-z || true)
  for f in "$here"/profiles/*.sh; do n=$(basename "$f" .sh); case "$url" in *"$n"*|*"$(echo "$n" | tr A-Z a-z)"*) SG_PROFILE=$n;; esac; done
fi
: "${SG_PROFILE:?no squad-goal profile matched; set SG_PROFILE or add scripts/profiles/<name>.sh}"
. "$here/profiles/$SG_PROFILE.sh"
if [ -z "${SG_SQUAD:-}" ]; then
  SG_SQUAD=$(python3 -I -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['agent-squad@agent-squad'][0]['installPath'])")
fi
: "${SG_STATE:=$HOME/.cache/squad-goal}"
: "${SG_REVIEW_MODEL:=gpt-5.6-terra}"
export SG_PROFILE SG_REPO SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE SG_MERGE_METHOD SG_AUTO_MERGE SG_E2E_DIR SG_E2E_CMD SG_E2E_PASS_REGEX SG_WORKFLOW SG_ROOT_NAME SG_REVIEW_RULES SG_CODING_RULES SG_SQUAD SG_STATE SG_AUTO_BUGFIX SG_BUG_LABEL SG_BUG_SKIP_LABELS SG_BUG_PROJECT SG_BUG_DOING SG_FRONTIER_LABEL SG_FRONTIER_PREAMBLE SG_FORK_DIR SG_FORK_BASE SG_FORK_BRANCH SG_FORK_TEST_CMD
BOARD="$here/board.sh"   # board-status.sh + a hub refresh flag
mkdir -p "$SG_STATE/cq" "$SG_STATE/req"
touch "$SG_STATE/active-runs.txt"
