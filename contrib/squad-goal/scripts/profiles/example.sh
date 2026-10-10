# squad-goal profile: copy to profiles/<repo-name>.sh (matched against the origin remote) and fill in.
: "${SG_REPO:=owner/repo}"
: "${SG_OWNER:=owner}"                 # GitHub user that owns the Project board
: "${SG_PROJECT:=1}"                   # Project (v2) number of the board
: "${SG_CHECKOUT:=$HOME/src/repo}"     # main checkout the squad runs from
: "${SG_BASE:=main}"
: "${SG_MERGE_METHOD:=squash}"
: "${SG_AUTO_MERGE:=yes}"
: "${SG_E2E_DIR:=$HOME/wt-e2e}"         # detached worktree on the base branch for post-merge e2e
: "${SG_E2E_CMD:=make test}"
: "${SG_E2E_PASS_REGEX:=passed}"
: "${SG_REVIEW_RULES:=the repo's AGENTS.md/CLAUDE.md rules}"
: "${SG_CODING_RULES:=follow AGENTS.md; bug fixes in TDD with the RED output in the PR body}"
: "${SG_ROOT_NAME:=repo}"
# : "${SG_SQUAD:=$HOME/src/agent-squad}"   # unset: the installed agent-squad plugin
